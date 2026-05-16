"""Tests for parsing Open-Meteo responses into a SpotForecast."""
from __future__ import annotations

import json
from datetime import date, datetime

import pytest

from fishing_forecast.config import Spot
from fishing_forecast.weather import WeatherError, parse_spot_forecast

SPOT = Spot(name="Test", latitude=55.714, longitude=12.599)


def _load(fixtures_dir, name):
    return json.loads((fixtures_dir / name).read_text(encoding="utf-8"))


def test_parses_hourly_and_daily(fixtures_dir):
    forecast = parse_spot_forecast(
        _load(fixtures_dir, "sample_forecast.json"),
        _load(fixtures_dir, "sample_marine.json"),
        SPOT,
    )

    assert len(forecast.hours) == 4
    first = forecast.hours[0]
    assert first.time == datetime(2026, 5, 16, 4, 0)
    assert first.wind_speed_ms == 3.2
    assert first.wind_gust_ms == 5.0
    assert first.cloud_cover_pct == 40

    # Daily sun times become a {date: datetime} map.
    assert forecast.sunrise[date(2026, 5, 16)] == datetime(2026, 5, 16, 5, 14)
    assert forecast.sunset[date(2026, 5, 16)] == datetime(2026, 5, 16, 21, 20)


def test_marine_data_is_merged_by_timestamp(fixtures_dir):
    forecast = parse_spot_forecast(
        _load(fixtures_dir, "sample_forecast.json"),
        _load(fixtures_dir, "sample_marine.json"),
        SPOT,
    )
    assert forecast.hours[0].wave_height_m == 0.12
    assert forecast.hours[0].water_temp_c == 10.8


def test_missing_marine_data_is_tolerated(fixtures_dir):
    forecast = parse_spot_forecast(
        _load(fixtures_dir, "sample_forecast.json"), None, SPOT
    )
    assert all(h.wave_height_m is None for h in forecast.hours)
    assert all(h.water_temp_c is None for h in forecast.hours)


def test_malformed_forecast_raises(fixtures_dir):
    with pytest.raises(WeatherError):
        parse_spot_forecast({"not": "valid"}, None, SPOT)


def test_hours_between_and_hour_at(fixtures_dir):
    forecast = parse_spot_forecast(
        _load(fixtures_dir, "sample_forecast.json"), None, SPOT
    )
    window = forecast.hours_between(
        datetime(2026, 5, 16, 5, 0), datetime(2026, 5, 16, 6, 0)
    )
    assert [h.time.hour for h in window] == [5, 6]

    # hour_at snaps to the start of the hour.
    assert forecast.hour_at(datetime(2026, 5, 16, 5, 40)).time.hour == 5
    assert forecast.hour_at(datetime(2026, 5, 16, 12, 0)) is None
