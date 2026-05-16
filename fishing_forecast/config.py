"""Loads ``config.yaml`` into typed dataclasses and validates it.

Failing loudly here — at startup — is deliberate. A misconfigured weight set or
an impossible planning window should stop the run immediately with a clear
message, not produce a quietly wrong forecast.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

# How close the scoring weights must sum to 1.0 before we accept them.
_WEIGHT_SUM_TOLERANCE = 0.001

# The factors the scoring model expects. config.yaml must define exactly these.
EXPECTED_WEIGHTS = {
    "wind",
    "precipitation",
    "cloud",
    "pressure",
    "solunar",
    "water_temp",
    "wave",
}


class ConfigError(ValueError):
    """Raised when config.yaml is missing fields or internally inconsistent."""


@dataclass(frozen=True)
class Spot:
    name: str
    latitude: float
    longitude: float
    notes: str = ""


@dataclass(frozen=True)
class PlanningWindow:
    min_days: int
    max_days: int


@dataclass(frozen=True)
class Daypart:
    dawn_start_offset: float
    dawn_end_offset: float
    dusk_start_offset: float
    dusk_end_offset: float


@dataclass(frozen=True)
class EmailConfig:
    recipient: str
    subject_prefix: str


@dataclass(frozen=True)
class Config:
    spots: list[Spot]
    planning_window: PlanningWindow
    alert_threshold: float
    daypart: Daypart
    weights: dict[str, float]
    timezone: str
    email: EmailConfig

    @classmethod
    def load(cls, path: str | Path) -> "Config":
        """Read and validate a config file, returning a populated Config."""
        path = Path(path)
        if not path.exists():
            raise ConfigError(f"Config file not found: {path}")

        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

        try:
            spots = [
                Spot(
                    name=s["name"],
                    latitude=float(s["latitude"]),
                    longitude=float(s["longitude"]),
                    notes=s.get("notes", ""),
                )
                for s in raw["spots"]
            ]
            planning_window = PlanningWindow(
                min_days=int(raw["planning_window"]["min_days"]),
                max_days=int(raw["planning_window"]["max_days"]),
            )
            daypart = Daypart(
                dawn_start_offset=float(raw["daypart"]["dawn_start_offset"]),
                dawn_end_offset=float(raw["daypart"]["dawn_end_offset"]),
                dusk_start_offset=float(raw["daypart"]["dusk_start_offset"]),
                dusk_end_offset=float(raw["daypart"]["dusk_end_offset"]),
            )
            weights = {k: float(v) for k, v in raw["weights"].items()}
            email = EmailConfig(
                recipient=raw["email"]["recipient"],
                subject_prefix=raw["email"].get("subject_prefix", "Fishing window"),
            )
            config = cls(
                spots=spots,
                planning_window=planning_window,
                alert_threshold=float(raw["alert_threshold"]),
                daypart=daypart,
                weights=weights,
                timezone=raw.get("timezone", "Europe/Copenhagen"),
                email=email,
            )
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"config.yaml is missing or malformed: {exc}") from exc

        config._validate()
        return config

    def _validate(self) -> None:
        if not self.spots:
            raise ConfigError("At least one spot must be defined.")

        if self.planning_window.min_days < 0:
            raise ConfigError("planning_window.min_days cannot be negative.")
        if self.planning_window.max_days < self.planning_window.min_days:
            raise ConfigError(
                "planning_window.max_days must be >= min_days."
            )

        if not 0.0 <= self.alert_threshold <= 5.0:
            raise ConfigError("alert_threshold must be between 0 and 5.")

        missing = EXPECTED_WEIGHTS - self.weights.keys()
        unexpected = self.weights.keys() - EXPECTED_WEIGHTS
        if missing:
            raise ConfigError(f"weights is missing: {sorted(missing)}")
        if unexpected:
            raise ConfigError(f"weights has unknown keys: {sorted(unexpected)}")

        total = sum(self.weights.values())
        if abs(total - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ConfigError(
                f"weights must sum to 1.0 (currently {total:.3f})."
            )
