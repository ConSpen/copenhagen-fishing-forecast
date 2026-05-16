"""Weather and marine data from the Open-Meteo APIs.

Open-Meteo is free and needs no API key. Two endpoints are used:

  * the forecast API   — wind, gusts, cloud, precipitation, pressure, air temp,
                         and the daily sunrise/sunset times
  * the marine API     — wave height and sea-surface temperature

The forecast is essential: if it fails, the run stops. The marine data is
"nice to have" — inner-harbour wave and water-temperature coverage can be
patchy — so a marine failure is logged and the run continues without it.

A note on time: Open-Meteo, when given a ``timezone`` parameter, returns local
timestamps. This package works in *naive local time* throughout (no tzinfo
attached) to avoid the usual aware/naive arithmetic bugs. Everything — "now",
the planning window, sunrise/sunset, window edges — is local wall-clock time
for the configured timezone.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

import requests

from .config import Spot

logger = logging.getLogger(__name__)

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

_FORECAST_HOURLY = [
    "temperature_2m",
    "precipitation",
    "cloud_cover",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "surface_pressure",
]
_MARINE_HOURLY = ["wave_height", "sea_surface_temperature"]

_HTTP_TIMEOUT = 12  # seconds


class WeatherError(RuntimeError):
    """Raised when essential forecast data cannot be retrieved or parsed."""


@dataclass(frozen=True)
class HourPoint:
    """Conditions at a single hour. Marine fields are optional."""

    time: datetime
    temperature_c: float
    precipitation_mm: float
    cloud_cover_pct: float
    wind_speed_ms: float
    wind_gust_ms: float
    wind_direction_deg: float
    pressure_hpa: float
    wave_height_m: float | None = None
    water_temp_c: float | None = None


@dataclass
class SpotForecast:
    """A full forecast for one spot: hourly points plus daily sun times."""

    spot: Spot
    hours: list[HourPoint]
    sunrise: dict[date, datetime]
    sunset: dict[date, datetime]

    def hour_at(self, when: datetime) -> HourPoint | None:
        """Return the hourly point on the same calendar hour as ``when``."""
        target = when.replace(minute=0, second=0, microsecond=0)
        for point in self.hours:
            if point.time == target:
                return point
        return None

    def hours_between(self, start: datetime, end: datetime) -> list[HourPoint]:
        """Return hourly points whose timestamp falls within [start, end]."""
        return [p for p in self.hours if start <= p.time <= end]


# --------------------------------------------------------------------------
# Network fetch — kept separate from parsing so the parser can be unit-tested
# against fixture JSON without touching the network.
# --------------------------------------------------------------------------

def fetch_forecast_raw(
    spot: Spot,
    days: int,
    timezone: str,
    session: requests.Session | None = None,
) -> dict:
    """Fetch the raw forecast JSON for a spot. Raises WeatherError on failure."""
    logger.info("Fetching forecast for %s ...", spot.name)
    session = session or requests.Session()
    params = {
        "latitude": spot.latitude,
        "longitude": spot.longitude,
        "hourly": ",".join(_FORECAST_HOURLY),
        "daily": "sunrise,sunset",
        "forecast_days": days,
        "timezone": timezone,
        "wind_speed_unit": "ms",
    }
    try:
        resp = session.get(FORECAST_URL, params=params, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        raise WeatherError(
            f"Could not fetch forecast for {spot.name}: {exc}"
        ) from exc


def fetch_marine_raw(
    spot: Spot,
    days: int,
    timezone: str,
    session: requests.Session | None = None,
) -> dict | None:
    """Fetch the raw marine JSON for a spot. Returns None on failure."""
    logger.info("Fetching marine data for %s ...", spot.name)
    session = session or requests.Session()
    params = {
        "latitude": spot.latitude,
        "longitude": spot.longitude,
        "hourly": ",".join(_MARINE_HOURLY),
        "forecast_days": days,
        "timezone": timezone,
    }
    try:
        resp = session.get(MARINE_URL, params=params, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        logger.warning(
            "Marine data unavailable for %s (%s) — continuing without it.",
            spot.name,
            exc,
        )
        return None


# --------------------------------------------------------------------------
# Parsing — pure functions, no I/O.
# --------------------------------------------------------------------------

def parse_spot_forecast(
    raw_forecast: dict,
    raw_marine: dict | None,
    spot: Spot,
) -> SpotForecast:
    """Combine raw forecast and (optional) marine JSON into a SpotForecast."""
    try:
        hourly = raw_forecast["hourly"]
        times = [datetime.fromisoformat(t) for t in hourly["time"]]
    except (KeyError, TypeError, ValueError) as exc:
        raise WeatherError(
            f"Forecast response for {spot.name} was not in the expected shape: {exc}"
        ) from exc

    # Marine arrays are aligned to their own time axis; index them by timestamp
    # so a missing or differently-ordered marine series can't corrupt anything.
    wave_by_time: dict[datetime, float | None] = {}
    water_by_time: dict[datetime, float | None] = {}
    if raw_marine and "hourly" in raw_marine:
        m = raw_marine["hourly"]
        m_times = [datetime.fromisoformat(t) for t in m.get("time", [])]
        wave = m.get("wave_height", [])
        water = m.get("sea_surface_temperature", [])
        for i, t in enumerate(m_times):
            if i < len(wave):
                wave_by_time[t] = wave[i]
            if i < len(water):
                water_by_time[t] = water[i]

    hours: list[HourPoint] = []
    for i, t in enumerate(times):
        hours.append(
            HourPoint(
                time=t,
                temperature_c=_num(hourly["temperature_2m"][i]),
                precipitation_mm=_num(hourly["precipitation"][i]),
                cloud_cover_pct=_num(hourly["cloud_cover"][i]),
                wind_speed_ms=_num(hourly["wind_speed_10m"][i]),
                wind_gust_ms=_num(hourly["wind_gusts_10m"][i]),
                wind_direction_deg=_num(hourly["wind_direction_10m"][i]),
                pressure_hpa=_num(hourly["surface_pressure"][i]),
                wave_height_m=wave_by_time.get(t),
                water_temp_c=water_by_time.get(t),
            )
        )

    daily = raw_forecast.get("daily", {})
    sunrise = _parse_sun_series(daily.get("time", []), daily.get("sunrise", []))
    sunset = _parse_sun_series(daily.get("time", []), daily.get("sunset", []))

    return SpotForecast(spot=spot, hours=hours, sunrise=sunrise, sunset=sunset)


def get_spot_forecast(
    spot: Spot,
    days: int,
    timezone: str,
    session: requests.Session | None = None,
) -> SpotForecast:
    """Fetch and parse a forecast for a spot (the live-mode convenience path)."""
    session = session or requests.Session()
    raw_forecast = fetch_forecast_raw(spot, days, timezone, session)
    raw_marine = fetch_marine_raw(spot, days, timezone, session)
    return parse_spot_forecast(raw_forecast, raw_marine, spot)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _num(value: object) -> float:
    """Coerce a possibly-null API value to a float, treating null as 0.0."""
    if value is None:
        return 0.0
    return float(value)


def _parse_sun_series(
    days: list[str], times: list[str]
) -> dict[date, datetime]:
    """Build a {date: datetime} map from Open-Meteo's daily sun arrays."""
    out: dict[date, datetime] = {}
    for day_str, time_str in zip(days, times):
        if not time_str:
            continue
        out[date.fromisoformat(day_str)] = datetime.fromisoformat(time_str)
    return out
