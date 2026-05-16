"""Synthetic but realistic forecast data for offline runs and demos.

``--mock`` mode uses this generator instead of the live APIs. It produces raw
JSON in exactly the shape Open-Meteo returns, so the whole parse → score →
email path runs unchanged — only the data source differs. Output is seeded per
spot and date, so a mock run is reproducible.

The generator is honest about being a toy: conditions are plausible for the
Øresund and vary day to day, and one mid-window day is nudged calm so a mock
run reliably has something to show. It is for testing and demos, not forecasts.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .astro import MoonInfo, sun_times
from .config import Spot
from .solunar import FeedingPeriod, SolunarDay


def _seed_for(spot: Spot, anchor: date) -> int:
    """A stable integer seed from a spot name and date (reproducible runs)."""
    digest = hashlib.md5(
        f"{spot.name}|{anchor.isoformat()}".encode("utf-8")
    ).hexdigest()
    return int(digest, 16) % (2**32)


def _seasonal_air_temp(day: date) -> float:
    """Rough Copenhagen monthly-mean air temperature for a date, °C."""
    doy = day.timetuple().tm_yday
    return 8.5 + 9.5 * math.sin((doy - 110) / 365.0 * 2 * math.pi)


def _seasonal_sst(day: date) -> float:
    """Rough Øresund sea-surface temperature for a date, °C (lags the air)."""
    doy = day.timetuple().tm_yday
    return 10.5 + 7.5 * math.sin((doy - 140) / 365.0 * 2 * math.pi)


def generate_forecast_raw(
    spot: Spot, days: int, anchor_date: date, timezone: str
) -> dict:
    """Generate an Open-Meteo-shaped forecast response for a spot.

    Each day is given an "unsettledness" from 0 to 1 that ties the weather
    factors together the way a real system does: an unsettled day is windy,
    cloudy, wet and has falling pressure; a settled day is calm, clearer, dry
    and stable. One day inside the planning window is forced settled so a mock
    run reliably has a window worth showing.
    """
    rng = random.Random(_seed_for(spot, anchor_date))
    tz = ZoneInfo(timezone)

    unsettled = [rng.random() for _ in range(days)]
    settled_day = min(days - 1, rng.randint(2, 5))
    unsettled[settled_day] = rng.uniform(0.04, 0.16)

    pressure = rng.uniform(1008, 1024)

    times: list[str] = []
    temperature: list[float] = []
    precipitation: list[float] = []
    cloud_cover: list[int] = []
    wind_speed: list[float] = []
    wind_gusts: list[float] = []
    wind_direction: list[int] = []
    surface_pressure: list[float] = []

    sun_days: list[str] = []
    sunrise: list[str] = []
    sunset: list[str] = []

    for day_index in range(days):
        day = anchor_date + timedelta(days=day_index)
        sun_days.append(day.isoformat())
        sunrise_dt, sunset_dt = sun_times(
            spot.latitude, spot.longitude, day, tz
        )
        sunrise.append(sunrise_dt.strftime("%Y-%m-%dT%H:%M"))
        sunset.append(sunset_dt.strftime("%Y-%m-%dT%H:%M"))

        u = unsettled[day_index]
        seasonal_temp = _seasonal_air_temp(day)
        day_wind_base = 2.5 + 16.0 * u             # ~2.5 m/s calm .. ~18 stormy
        day_cloud_base = 12.0 + 80.0 * u           # clear .. fully overcast
        day_pressure_drift = (0.25 - u) * 1.4      # rising when settled, falling when not
        day_wind_dir = rng.randint(0, 359)

        # Rain becomes both more likely and heavier as a day gets unsettled.
        rain_hours: set[int] = set()
        rain_intensity = 0.0
        if rng.random() < (0.12 + 0.7 * u):
            start_hour = rng.randint(0, 18)
            rain_hours = set(range(start_hour, start_hour + rng.randint(2, 7)))
            rain_intensity = rng.uniform(0.2, 1.0) + 6.0 * u

        for hour in range(24):
            moment = datetime.combine(day, datetime.min.time()) + timedelta(
                hours=hour
            )
            times.append(moment.strftime("%Y-%m-%dT%H:%M"))

            diurnal = 4.5 * math.sin((hour - 9) / 24.0 * 2 * math.pi)
            temperature.append(
                round(seasonal_temp + diurnal + rng.uniform(-1.0, 1.0), 1)
            )

            # Wind picks up a little through the afternoon, plus hourly noise.
            wind = max(
                0.3,
                day_wind_base
                + 1.2 * math.sin((hour - 14) / 24.0 * 2 * math.pi)
                + rng.uniform(-1.0, 1.0),
            )
            wind_speed.append(round(wind, 1))
            wind_gusts.append(round(wind * rng.uniform(1.3, 1.8), 1))
            wind_direction.append(
                (day_wind_dir + rng.randint(-20, 20)) % 360
            )

            cloud_cover.append(
                int(min(100, max(0, day_cloud_base + rng.uniform(-12, 12))))
            )

            if hour in rain_hours:
                precipitation.append(
                    round(max(0.0, rain_intensity + rng.uniform(-0.3, 0.3)), 1)
                )
            else:
                precipitation.append(0.0)

            pressure = min(
                1035.0,
                max(
                    990.0,
                    pressure + day_pressure_drift + rng.uniform(-0.2, 0.2),
                ),
            )
            surface_pressure.append(round(pressure, 1))

    return {
        "latitude": spot.latitude,
        "longitude": spot.longitude,
        "timezone": timezone,
        "hourly": {
            "time": times,
            "temperature_2m": temperature,
            "precipitation": precipitation,
            "cloud_cover": cloud_cover,
            "wind_speed_10m": wind_speed,
            "wind_gusts_10m": wind_gusts,
            "wind_direction_10m": wind_direction,
            "surface_pressure": surface_pressure,
        },
        "daily": {
            "time": sun_days,
            "sunrise": sunrise,
            "sunset": sunset,
        },
    }


def generate_marine_raw(spot: Spot, days: int, anchor_date: date) -> dict:
    """Generate an Open-Meteo-shaped marine response for a spot."""
    rng = random.Random(_seed_for(spot, anchor_date) + 1)

    times: list[str] = []
    wave_height: list[float] = []
    sea_surface_temperature: list[float] = []

    for day_index in range(days):
        day = anchor_date + timedelta(days=day_index)
        # Sheltered harbour water — small waves, water temp near-constant per day.
        day_sst = _seasonal_sst(day) + rng.uniform(-0.6, 0.6)
        for hour in range(24):
            moment = datetime.combine(day, datetime.min.time()) + timedelta(
                hours=hour
            )
            times.append(moment.strftime("%Y-%m-%dT%H:%M"))
            wave_height.append(round(max(0.0, rng.uniform(0.05, 0.5)), 2))
            sea_surface_temperature.append(
                round(day_sst + rng.uniform(-0.2, 0.2), 1)
            )

    return {
        "hourly": {
            "time": times,
            "wave_height": wave_height,
            "sea_surface_temperature": sea_surface_temperature,
        }
    }


def generate_solunar_day(spot: Spot, day: date, moon: MoonInfo) -> SolunarDay:
    """Generate a plausible SolunarDay with major/minor feeding periods."""
    rng = random.Random(_seed_for(spot, day) + 2)
    midnight = datetime.combine(day, datetime.min.time())

    periods: list[FeedingPeriod] = []
    for hour in sorted(rng.sample(range(0, 22), 2)):  # two ~2h major periods
        start = midnight + timedelta(
            hours=hour, minutes=rng.choice([0, 15, 30, 45])
        )
        periods.append(FeedingPeriod("major", start, start + timedelta(hours=2)))
    for hour in sorted(rng.sample(range(0, 23), 2)):  # two ~1h minor periods
        start = midnight + timedelta(
            hours=hour, minutes=rng.choice([0, 15, 30, 45])
        )
        periods.append(FeedingPeriod("minor", start, start + timedelta(hours=1)))

    rating = round(1.0 + 2.0 * moon.proximity + rng.uniform(-0.3, 0.3), 1)
    return SolunarDay(
        day=day,
        moon=moon,
        periods=periods,
        api_day_rating=rating,
        source="mock generator",
    )
