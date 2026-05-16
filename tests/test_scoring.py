"""Tests for the scoring model — the heart of the project."""
from __future__ import annotations

from datetime import date, datetime

import pytest

from fishing_forecast.astro import MoonInfo, Window
from fishing_forecast.scoring import (
    ScoringError,
    build_window_conditions,
    score_cloud,
    score_conditions,
    score_precipitation,
    score_pressure,
    score_solunar,
    score_water_temp,
    score_wave,
    score_wind,
    WindowConditions,
)
from fishing_forecast.solunar import FeedingPeriod, SolunarDay
from fishing_forecast.weather import SpotForecast
from fishing_forecast.config import Spot
from tests.conftest import make_hour

NEW_MOON = MoonInfo(0.0, "New Moon", 0.0, 1.0)
QUARTER_MOON = MoonInfo(7.0, "First Quarter", 0.5, 0.0)
SPOT = Spot(name="Test", latitude=55.7, longitude=12.6)

# Weights mirroring config.yaml — used for the aggregate tests.
WEIGHTS = {
    "wind": 0.35,
    "precipitation": 0.15,
    "cloud": 0.12,
    "pressure": 0.12,
    "solunar": 0.16,
    "water_temp": 0.06,
    "wave": 0.04,
}


# --------------------------------------------------------------------------
# Individual factor curves
# --------------------------------------------------------------------------

def test_wind_score_rewards_a_light_breeze_and_punishes_a_gale():
    assert score_wind(4.0, 6.0) > 0.9
    assert score_wind(13.0, 18.0) < 0.2
    # A flat calm is fishable but slightly less productive than a ripple.
    assert score_wind(0.5, 1.0) < score_wind(4.0, 6.0)


def test_wind_score_applies_a_gust_penalty():
    steady = score_wind(5.0, 7.0)
    gusty = score_wind(5.0, 20.0)
    assert gusty < steady


def test_precipitation_score():
    assert score_precipitation(0.0) == pytest.approx(1.0)
    assert score_precipitation(0.2) == pytest.approx(1.0)
    assert score_precipitation(8.0) < 0.3


def test_cloud_score_prefers_overcast():
    assert score_cloud(70.0) == pytest.approx(1.0)
    assert score_cloud(0.0) < score_cloud(70.0)


def test_pressure_score_prefers_stability():
    assert score_pressure(0.0) == pytest.approx(1.0)
    assert score_pressure(-6.0) < 0.4          # a sharp drop is bad
    assert score_pressure(1.0) > 0.85          # a gentle rise is fine


def test_water_temp_score_handles_missing_data_and_extremes():
    assert score_water_temp(None) == 0.80      # neutral when unavailable
    assert score_water_temp(8.0) == pytest.approx(1.0)
    assert score_water_temp(-2.0) < 0.5


def test_wave_score_handles_missing_data():
    assert score_wave(None) == 0.90            # neutral when unavailable
    assert score_wave(0.2) == pytest.approx(1.0)
    assert score_wave(2.5) < 0.3


def test_solunar_score_uses_moon_and_feeding_periods():
    # A new moon should beat a quarter moon, all else equal.
    assert score_solunar(NEW_MOON, None) > score_solunar(QUARTER_MOON, None)
    # An overlapping major period should beat no overlap.
    assert score_solunar(QUARTER_MOON, "major") > score_solunar(
        QUARTER_MOON, None
    )
    # A major overlap should beat a minor one.
    assert score_solunar(QUARTER_MOON, "major") > score_solunar(
        QUARTER_MOON, "minor"
    )


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def _conditions(**overrides) -> WindowConditions:
    base = dict(
        mean_wind_ms=4.0,
        max_gust_ms=6.0,
        max_precip_mm=0.0,
        mean_cloud_pct=65.0,
        pressure_trend_hpa=0.2,
        water_temp_c=11.0,
        max_wave_m=0.2,
        moon=NEW_MOON,
        feeding_overlap="major",
        mean_temp_c=10.0,
    )
    base.update(overrides)
    return WindowConditions(**base)


def test_score_conditions_stays_in_range_and_is_half_star_rounded():
    result = score_conditions(_conditions(), WEIGHTS)
    assert 0.0 <= result.total <= 1.0
    assert 0.0 <= result.stars <= 5.0
    # Stars are rounded to the nearest half.
    assert (result.stars * 2) == int(result.stars * 2)


def test_score_conditions_breakdown_sums_to_total():
    result = score_conditions(_conditions(), WEIGHTS)
    assert set(result.components) == set(WEIGHTS)
    total_of_parts = sum(c.contribution for c in result.components.values())
    assert total_of_parts == pytest.approx(result.total)


def test_good_conditions_outscore_bad_conditions():
    good = score_conditions(_conditions(), WEIGHTS)
    bad = score_conditions(
        _conditions(
            mean_wind_ms=14.0,
            max_gust_ms=22.0,
            max_precip_mm=9.0,
            pressure_trend_hpa=-6.0,
            moon=QUARTER_MOON,
            feeding_overlap=None,
        ),
        WEIGHTS,
    )
    assert good.stars > bad.stars
    assert good.is_promising(3.5)
    assert not bad.is_promising(3.5)


# --------------------------------------------------------------------------
# Digesting hourly data into a window's conditions
# --------------------------------------------------------------------------

def test_build_window_conditions_aggregates_and_finds_pressure_trend():
    # A dawn window 05:00–07:00, with an hour at 02:00 for the pressure lookback.
    hours = [
        make_hour(datetime(2026, 5, 16, 2, 0), pressure=1010.0),
        make_hour(datetime(2026, 5, 16, 5, 0), wind=3.0, gust=5.0, pressure=1013.0),
        make_hour(datetime(2026, 5, 16, 6, 0), wind=5.0, gust=8.0, pressure=1013.5),
        make_hour(datetime(2026, 5, 16, 7, 0), wind=4.0, gust=6.0, pressure=1014.0),
    ]
    forecast = SpotForecast(spot=SPOT, hours=hours, sunrise={}, sunset={})
    window = Window(
        daypart="dawn",
        start=datetime(2026, 5, 16, 5, 0),
        end=datetime(2026, 5, 16, 7, 0),
    )
    solunar = SolunarDay(
        day=date(2026, 5, 16),
        moon=NEW_MOON,
        periods=[
            FeedingPeriod(
                "major",
                datetime(2026, 5, 16, 5, 30),
                datetime(2026, 5, 16, 7, 30),
            )
        ],
    )

    conditions = build_window_conditions(forecast, window, solunar)

    assert conditions.mean_wind_ms == pytest.approx(4.0)   # (3 + 5 + 4) / 3
    assert conditions.max_gust_ms == 8.0
    # Pressure trend: midpoint (06:00 -> 1013.5) minus 3h before start (02:00 -> 1010.0).
    assert conditions.pressure_trend_hpa == pytest.approx(3.5)
    assert conditions.feeding_overlap == "major"


def test_build_window_conditions_raises_without_coverage():
    forecast = SpotForecast(spot=SPOT, hours=[], sunrise={}, sunset={})
    window = Window(
        daypart="dawn",
        start=datetime(2026, 5, 16, 5, 0),
        end=datetime(2026, 5, 16, 7, 0),
    )
    solunar = SolunarDay(day=date(2026, 5, 16), moon=NEW_MOON)
    with pytest.raises(ScoringError):
        build_window_conditions(forecast, window, solunar)
