"""The scoring model: turns a window's conditions into a 0–5 star rating.

The design goal is transparency. Every factor is scored independently on a
0–1 scale, combined with the weights from config.yaml, and the per-factor
breakdown is carried all the way through to the email. You should always be
able to see *why* a window got the rating it did.

The factor curves below encode fairly conventional shore-spinning wisdom for
the Øresund. They are deliberately simple piecewise-linear shapes — easy to
read, easy to tune, and honest about being heuristics rather than a model
fitted to catch data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from statistics import mean

from .astro import MoonInfo, Window
from .solunar import SolunarDay
from .weather import SpotForecast


class ScoringError(RuntimeError):
    """Raised when a window cannot be scored (e.g. no hourly coverage)."""


# --------------------------------------------------------------------------
# Data carried between steps
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class WindowConditions:
    """Conditions for one window, digested from hourly data — the scoring input."""

    mean_wind_ms: float
    max_gust_ms: float
    max_precip_mm: float
    mean_cloud_pct: float
    pressure_trend_hpa: float  # change over ~3h; positive = rising
    water_temp_c: float | None
    max_wave_m: float | None
    moon: MoonInfo
    feeding_overlap: str | None  # "major", "minor" or None
    mean_temp_c: float           # air temperature, for display only


@dataclass(frozen=True)
class ComponentScore:
    """One factor's contribution to the overall score."""

    factor: str
    score: float        # 0–1
    weight: float       # 0–1, from config
    contribution: float # weight * score
    note: str           # plain-language description


@dataclass(frozen=True)
class WindowScore:
    """The full scored result for a window."""

    total: float                          # 0–1
    stars: float                          # 0–5, rounded to nearest 0.5
    components: dict[str, ComponentScore]
    headline: str
    conditions: WindowConditions

    def is_promising(self, threshold: float) -> bool:
        return self.stars >= threshold

    @property
    def ranked_components(self) -> list[ComponentScore]:
        """Components ordered best score first."""
        return sorted(
            self.components.values(), key=lambda c: c.score, reverse=True
        )

    @property
    def weak_points(self) -> list[ComponentScore]:
        """Components scoring below 'fair', worst first — the things to watch."""
        weak = [c for c in self.components.values() if c.score < 0.5]
        return sorted(weak, key=lambda c: c.score)


# --------------------------------------------------------------------------
# Factor curves — each returns 0–1. Curves are (input, score) points, linearly
# interpolated. See the README for the reasoning behind each shape.
# --------------------------------------------------------------------------

# Wind in m/s. A light-to-moderate breeze is ideal for shore casting; a flat
# calm is slightly less productive, and anything above ~10 m/s gets hard.
_WIND_CURVE = [
    (0, 0.80), (2, 0.95), (4, 1.00), (6, 0.92), (8, 0.72),
    (10, 0.42), (12, 0.16), (15, 0.04), (25, 0.0),
]

# Peak rainfall rate in mm/h within the window. Light drizzle barely matters;
# heavy rain is unpleasant and muddies the water.
_PRECIP_CURVE = [
    (0, 1.0), (0.3, 1.0), (1.0, 0.90), (2.5, 0.65),
    (5, 0.35), (10, 0.12), (20, 0.03),
]

# Mean cloud cover %. Overcast generally helps, especially for sea trout.
_CLOUD_CURVE = [
    (0, 0.65), (20, 0.78), (50, 0.92), (70, 1.0), (90, 1.0), (100, 0.95),
]

# Surface-pressure change over ~3h, hPa. Stable is best; a sharp drop ahead of
# a front tends to shut fish down (and brings worse weather anyway).
_PRESSURE_CURVE = [
    (-8, 0.25), (-4, 0.35), (-2, 0.55), (-0.5, 0.92), (0, 1.0),
    (0.5, 1.0), (2, 0.90), (4, 0.82), (8, 0.78),
]

# Sea-surface temperature °C. Mostly drives *which* species (see species.py);
# here it only penalises the cold and warm extremes that slow fishing down.
_WATER_CURVE = [
    (-2, 0.45), (1, 0.60), (3, 0.85), (5, 1.0), (16, 1.0),
    (19, 0.85), (22, 0.70), (26, 0.55),
]

# Wave height in m. Inner-harbour water is usually flat; a little movement is
# fine, a lot is uncomfortable and unsafe from the shore.
_WAVE_CURVE = [
    (0, 1.0), (0.4, 1.0), (0.8, 0.82), (1.2, 0.55), (2.0, 0.25), (3.0, 0.08),
]

# Neutral scores used when marine data is unavailable, so a missing feed
# neither helps nor unfairly punishes a window.
_WATER_TEMP_UNKNOWN = 0.80
_WAVE_UNKNOWN = 0.90


def score_wind(mean_ms: float, gust_ms: float) -> float:
    base = _interp(mean_ms, _WIND_CURVE)
    # Gusts above ~12 m/s wreck lure presentation even if the mean looks calm.
    if gust_ms > 12:
        base *= 1.0 - min(0.5, (gust_ms - 12) * 0.06)
    return _clamp01(base)


def score_precipitation(max_mm: float) -> float:
    return _clamp01(_interp(max_mm, _PRECIP_CURVE))


def score_cloud(mean_pct: float) -> float:
    return _clamp01(_interp(mean_pct, _CLOUD_CURVE))


def score_pressure(trend_hpa: float) -> float:
    return _clamp01(_interp(trend_hpa, _PRESSURE_CURVE))


def score_water_temp(temp_c: float | None) -> float:
    if temp_c is None:
        return _WATER_TEMP_UNKNOWN
    return _clamp01(_interp(temp_c, _WATER_CURVE))


def score_wave(height_m: float | None) -> float:
    if height_m is None:
        return _WAVE_UNKNOWN
    return _clamp01(_interp(height_m, _WAVE_CURVE))


def score_solunar(moon: MoonInfo, feeding_overlap: str | None) -> float:
    """Baseline from moon proximity (always available), plus a period bonus."""
    base = 0.45 + 0.35 * moon.proximity
    bonus = {"major": 0.35, "minor": 0.20}.get(feeding_overlap, 0.0)
    return _clamp01(base + bonus)


# --------------------------------------------------------------------------
# Assembling and scoring a window
# --------------------------------------------------------------------------

def build_window_conditions(
    forecast: SpotForecast, window: Window, solunar_day: SolunarDay
) -> WindowConditions:
    """Digest hourly forecast data over a window into a WindowConditions."""
    points = forecast.hours_between(window.start, window.end)
    if not points:
        raise ScoringError(
            f"No hourly forecast coverage for the {window.daypart} window "
            f"on {window.date}."
        )

    water_vals = [p.water_temp_c for p in points if p.water_temp_c is not None]
    wave_vals = [p.wave_height_m for p in points if p.wave_height_m is not None]

    # Pressure trend: window-midpoint pressure against ~3h before the window
    # opened. If those hours are missing, fall back to the in-window change.
    mid = forecast.hour_at(window.midpoint)
    before = forecast.hour_at(window.start - timedelta(hours=3))
    if mid is not None and before is not None:
        pressure_trend = mid.pressure_hpa - before.pressure_hpa
    else:
        pressure_trend = points[-1].pressure_hpa - points[0].pressure_hpa

    overlap = solunar_day.overlaps_window(window.start, window.end)

    return WindowConditions(
        mean_wind_ms=mean(p.wind_speed_ms for p in points),
        max_gust_ms=max(p.wind_gust_ms for p in points),
        max_precip_mm=max(p.precipitation_mm for p in points),
        mean_cloud_pct=mean(p.cloud_cover_pct for p in points),
        pressure_trend_hpa=pressure_trend,
        water_temp_c=mean(water_vals) if water_vals else None,
        max_wave_m=max(wave_vals) if wave_vals else None,
        moon=solunar_day.moon,
        feeding_overlap=overlap.kind if overlap else None,
        mean_temp_c=mean(p.temperature_c for p in points),
    )


def score_conditions(
    conditions: WindowConditions, weights: dict[str, float]
) -> WindowScore:
    """Score digested conditions against the configured weights."""
    raw_scores = {
        "wind": score_wind(conditions.mean_wind_ms, conditions.max_gust_ms),
        "precipitation": score_precipitation(conditions.max_precip_mm),
        "cloud": score_cloud(conditions.mean_cloud_pct),
        "pressure": score_pressure(conditions.pressure_trend_hpa),
        "solunar": score_solunar(conditions.moon, conditions.feeding_overlap),
        "water_temp": score_water_temp(conditions.water_temp_c),
        "wave": score_wave(conditions.max_wave_m),
    }

    components: dict[str, ComponentScore] = {}
    total = 0.0
    for factor, score in raw_scores.items():
        weight = weights[factor]
        contribution = weight * score
        total += contribution
        components[factor] = ComponentScore(
            factor=factor,
            score=score,
            weight=weight,
            contribution=contribution,
            note=_note(factor, conditions, score),
        )

    # Round to the nearest half-star on the 0–5 scale.
    stars = round(total * 5 * 2) / 2

    return WindowScore(
        total=total,
        stars=stars,
        components=components,
        headline=_headline(conditions),
        conditions=conditions,
    )


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _interp(x: float, points: list[tuple[float, float]]) -> float:
    """Piecewise-linear interpolation over (x, y) points sorted ascending by x."""
    if x <= points[0][0]:
        return points[0][1]
    if x >= points[-1][0]:
        return points[-1][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if x0 <= x <= x1:
            if x1 == x0:
                return y0
            return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
    return points[-1][1]


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _quality_word(score: float) -> str:
    if score >= 0.85:
        return "excellent"
    if score >= 0.70:
        return "good"
    if score >= 0.50:
        return "fair"
    if score >= 0.30:
        return "poor"
    return "bad"


def _note(factor: str, c: WindowConditions, score: float) -> str:
    """A one-line, plain-language description of a factor's state."""
    quality = _quality_word(score)
    if factor == "wind":
        return (
            f"{c.mean_wind_ms:.0f} m/s mean wind, gusting {c.max_gust_ms:.0f} "
            f"m/s — {quality} for casting"
        )
    if factor == "precipitation":
        if c.max_precip_mm < 0.3:
            return "Dry through the window"
        return f"Up to {c.max_precip_mm:.1f} mm/h rain — {quality}"
    if factor == "cloud":
        return f"{c.mean_cloud_pct:.0f}% cloud cover — {quality} light"
    if factor == "pressure":
        direction = (
            "steady"
            if abs(c.pressure_trend_hpa) <= 0.5
            else ("rising" if c.pressure_trend_hpa > 0 else "falling")
        )
        return (
            f"Pressure {direction} ({c.pressure_trend_hpa:+.1f} hPa/3h) — {quality}"
        )
    if factor == "solunar":
        moon = c.moon.phase_name.lower()
        if c.feeding_overlap:
            return f"{c.moon.phase_name}; a {c.feeding_overlap} feeding period overlaps"
        return f"{c.moon.phase_name} — {quality} solunar outlook"
    if factor == "water_temp":
        if c.water_temp_c is None:
            return "Water temperature unavailable — scored neutral"
        return f"Water {c.water_temp_c:.1f} °C — {quality}"
    if factor == "wave":
        if c.max_wave_m is None:
            return "Wave height unavailable — scored neutral"
        return f"Up to {c.max_wave_m:.1f} m wave — {quality}"
    return quality


def _headline(c: WindowConditions) -> str:
    """A compact one-line summary of the window's conditions."""
    rain = "dry" if c.max_precip_mm < 0.3 else f"{c.max_precip_mm:.1f} mm/h rain"
    water = (
        f"water {c.water_temp_c:.0f} °C"
        if c.water_temp_c is not None
        else "water temp n/a"
    )
    return (
        f"{c.mean_wind_ms:.0f} m/s wind, {c.mean_cloud_pct:.0f}% cloud, "
        f"{rain}, {water}, air {c.mean_temp_c:.0f} °C"
    )
