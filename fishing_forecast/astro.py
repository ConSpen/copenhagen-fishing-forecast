"""Sun and moon calculations: dawn/dusk windows, moon phase, sunrise/sunset.

This module has no third-party dependencies — the astronomy is implemented
directly from standard, well-documented algorithms:

  * Moon phase comes from the synodic month and a known new-moon epoch.
  * Sunrise and sunset use the standard "sunrise equation" (Cooper's solar
    declination, the equation of time, and the hour-angle formula).

Both are accurate to a minute or two, which is ample here. In live runs the
sunrise/sunset values actually used come straight from the Open-Meteo forecast;
``sun_times`` below is what the offline mock-data generator uses so it does not
need a forecast to invent a plausible day around.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import Daypart

# Length of the synodic (new-moon-to-new-moon) month, in days.
_SYNODIC_MONTH = 29.530588853

# A known new moon, used as the reference epoch: 2000-01-06 18:14 UTC.
_KNOWN_NEW_MOON = datetime(2000, 1, 6, 18, 14, tzinfo=timezone.utc)

# Phase-name bands, in moon-age days. Each principal phase spans one-eighth of
# the cycle, centred on its defining instant (new, quarter, full, quarter).
_PHASE_BAND_WIDTH = _SYNODIC_MONTH / 16.0
_PHASE_NAMES = [
    "New Moon",
    "Waxing Crescent",
    "First Quarter",
    "Waxing Gibbous",
    "Full Moon",
    "Waning Gibbous",
    "Last Quarter",
    "Waning Crescent",
]

# Sun sits 0.833° below the horizon at sunrise/sunset (refraction + solar disc).
_SUN_HORIZON_DEG = -0.833


@dataclass(frozen=True)
class Window:
    """A fishable time window at a spot on a given day."""

    daypart: str  # "dawn" or "dusk"
    start: datetime
    end: datetime

    @property
    def midpoint(self) -> datetime:
        return self.start + (self.end - self.start) / 2

    @property
    def date(self) -> date:
        return self.midpoint.date()

    @property
    def duration_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


@dataclass(frozen=True)
class MoonInfo:
    """Moon state for a given day."""

    phase_value: float   # moon age in days, 0–29.53 (0 = new, ~14.8 = full)
    phase_name: str
    illumination: float  # 0–1, fraction of the disc lit
    proximity: float     # 0–1, closeness to a new or full moon


def dawn_window(sunrise: datetime, cfg: Daypart) -> Window:
    """Build the dawn fishing window around a sunrise time."""
    return Window(
        daypart="dawn",
        start=sunrise + timedelta(hours=cfg.dawn_start_offset),
        end=sunrise + timedelta(hours=cfg.dawn_end_offset),
    )


def dusk_window(sunset: datetime, cfg: Daypart) -> Window:
    """Build the dusk fishing window around a sunset time."""
    return Window(
        daypart="dusk",
        start=sunset + timedelta(hours=cfg.dusk_start_offset),
        end=sunset + timedelta(hours=cfg.dusk_end_offset),
    )


# --------------------------------------------------------------------------
# Moon
# --------------------------------------------------------------------------

def moon_age_days(day: date) -> float:
    """Days elapsed since the most recent new moon (0 .. 29.53).

    Sampled at midday UTC of the given date — good to within about a day,
    which is all a feeding heuristic needs.
    """
    instant = datetime(day.year, day.month, day.day, 12, tzinfo=timezone.utc)
    elapsed = (instant - _KNOWN_NEW_MOON).total_seconds() / 86400.0
    return elapsed % _SYNODIC_MONTH


def moon_info(day: date) -> MoonInfo:
    """Compute moon phase, illumination and new/full proximity for a day.

    ``proximity`` encodes the solunar belief that fishing tends to be better
    around the new and full moon: it is 1.0 at new or full, and falls to 0.0 at
    the quarter moons.
    """
    age = moon_age_days(day)

    band = int(age / _PHASE_BAND_WIDTH)  # 0..15
    # Bands 0 and 15 both belong to "New Moon"; the rest pair up two-to-a-name.
    name = _PHASE_NAMES[((band + 1) // 2) % 8]

    # Illumination as a cosine sweep: 0 at new, 1 at full.
    illumination = (1.0 - math.cos(2.0 * math.pi * age / _SYNODIC_MONTH)) / 2.0

    # Distance to the nearest of new (0 or 29.53) or full (~14.77). The quarter
    # moons sit a quarter-cycle from both, which is the maximum.
    half = _SYNODIC_MONTH / 2.0
    quarter = _SYNODIC_MONTH / 4.0
    dist_to_new = min(age, _SYNODIC_MONTH - age)
    dist_to_full = abs(age - half)
    nearest = min(dist_to_new, dist_to_full)
    proximity = 1.0 - (nearest / quarter)

    return MoonInfo(
        phase_value=round(age, 2),
        phase_name=name,
        illumination=round(illumination, 3),
        proximity=round(max(0.0, min(1.0, proximity)), 3),
    )


# --------------------------------------------------------------------------
# Sun — the standard sunrise equation
# --------------------------------------------------------------------------

def sun_times(
    latitude: float, longitude: float, day: date, tz: ZoneInfo
) -> tuple[datetime, datetime]:
    """Return (sunrise, sunset) as naive local datetimes for a date.

    Implements the standard sunrise equation. Accurate to a minute or two —
    fine for the mock-data generator. Live runs use Open-Meteo's own values.
    """
    doy = day.timetuple().tm_yday

    # Solar declination (Cooper's equation), in radians.
    declination = math.radians(
        -23.44 * math.cos(math.radians(360.0 / 365.0 * (doy + 10)))
    )
    lat_rad = math.radians(latitude)

    # Hour angle at sunrise/sunset, accounting for atmospheric refraction.
    cos_hour_angle = (
        math.sin(math.radians(_SUN_HORIZON_DEG))
        - math.sin(lat_rad) * math.sin(declination)
    ) / (math.cos(lat_rad) * math.cos(declination))
    cos_hour_angle = max(-1.0, min(1.0, cos_hour_angle))  # guard polar extremes
    hour_angle = math.degrees(math.acos(cos_hour_angle))

    # Solar noon in UTC hours: 12:00, shifted for longitude and the equation
    # of time (the difference between apparent and mean solar time).
    solar_noon_utc = 12.0 - longitude / 15.0 - _equation_of_time(doy) / 60.0
    sunrise_utc = solar_noon_utc - hour_angle / 15.0
    sunset_utc = solar_noon_utc + hour_angle / 15.0

    return (
        _utc_hours_to_local(day, sunrise_utc, tz),
        _utc_hours_to_local(day, sunset_utc, tz),
    )


def _equation_of_time(doy: int) -> float:
    """Equation of time for a day of year, in minutes."""
    b = math.radians(360.0 / 365.0 * (doy - 81))
    return 9.87 * math.sin(2 * b) - 7.53 * math.cos(b) - 1.5 * math.sin(b)


def _utc_hours_to_local(
    day: date, hours_utc: float, tz: ZoneInfo
) -> datetime:
    """Turn a UTC hour-of-day into a naive local datetime for the timezone."""
    base = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    utc_dt = base + timedelta(hours=hours_utc)
    return utc_dt.astimezone(tz).replace(tzinfo=None)
