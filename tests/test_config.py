"""Tests for loading and validating config.yaml."""
from __future__ import annotations

from pathlib import Path

import pytest

from fishing_forecast.config import Config, ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent
REAL_CONFIG = REPO_ROOT / "config.yaml"

# A minimal valid config used as the basis for the "reject bad input" tests.
_VALID = """\
spots:
  - name: Testspot
    latitude: 55.7
    longitude: 12.6
planning_window:
  min_days: 2
  max_days: 7
alert_threshold: 3.5
daypart:
  dawn_start_offset: -0.75
  dawn_end_offset: 2.5
  dusk_start_offset: -2.5
  dusk_end_offset: 0.75
weights:
  wind: 0.35
  precipitation: 0.15
  cloud: 0.12
  pressure: 0.12
  solunar: 0.16
  water_temp: 0.06
  wave: 0.04
timezone: Europe/Copenhagen
email:
  recipient: someone@example.com
  subject_prefix: Fishing window
"""


def test_the_real_config_file_loads_and_validates():
    config = Config.load(REAL_CONFIG)
    assert config.spots
    assert abs(sum(config.weights.values()) - 1.0) < 0.001


def test_valid_config_loads(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_VALID, encoding="utf-8")
    config = Config.load(path)
    assert config.spots[0].name == "Testspot"
    assert config.alert_threshold == 3.5


def test_weights_that_do_not_sum_to_one_are_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_VALID.replace("wind: 0.35", "wind: 0.50"), encoding="utf-8")
    with pytest.raises(ConfigError, match="sum to 1.0"):
        Config.load(path)


def test_unknown_weight_key_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_VALID.replace("wave: 0.04", "wave: 0.04\n  tide: 0.0"), encoding="utf-8")
    with pytest.raises(ConfigError):
        Config.load(path)


def test_impossible_planning_window_is_rejected(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(_VALID.replace("max_days: 7", "max_days: 1"), encoding="utf-8")
    with pytest.raises(ConfigError, match="max_days"):
        Config.load(path)


def test_missing_file_is_reported_clearly(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        Config.load(tmp_path / "nope.yaml")
