"""Shared test fixtures and helpers."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from fishing_forecast.weather import HourPoint

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES_DIR


def make_hour(
    time: datetime,
    *,
    wind: float = 4.0,
    gust: float = 6.0,
    precip: float = 0.0,
    cloud: float = 50.0,
    temp: float = 10.0,
    pressure: float = 1018.0,
    wind_dir: float = 200.0,
    wave: float | None = 0.2,
    water: float | None = 10.5,
) -> HourPoint:
    """Build an HourPoint with sensible defaults — override only what matters."""
    return HourPoint(
        time=time,
        temperature_c=temp,
        precipitation_mm=precip,
        cloud_cover_pct=cloud,
        wind_speed_ms=wind,
        wind_gust_ms=gust,
        wind_direction_deg=wind_dir,
        pressure_hpa=pressure,
        wave_height_m=wave,
        water_temp_c=water,
    )
