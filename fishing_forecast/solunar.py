"""Solunar feeding-period data, with a moon-phase fallback.

Solunar theory is a long-standing angling heuristic: fish are believed to feed
more actively during the "major" and "minor" periods tied to the moon's
position, and around the new and full moon. It is a *soft* signal — useful as a
tie-breaker, not a guarantee — and the scoring model weights it accordingly.

Primary source is the free solunar.org API, which returns major/minor feeding
periods for a date and location. If that API is unreachable or returns an
unexpected shape, the locally-computed moon-phase proximity from astro.py is
used instead, so a solunar signal is always available. The moon-phase proximity
is, in fact, always the baseline — the API only ever *adds* the period-overlap
detail on top of it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

import requests

from .astro import MoonInfo
from .config import Spot

logger = logging.getLogger(__name__)

SOLUNAR_URL = "https://api.solunar.org/solunar"
_HTTP_TIMEOUT = 8  # seconds
_MAX_FAILURES = 2  # after this many timeouts, skip the API for the rest of the run
_failure_count = 0


def reset_failures() -> None:
    """Reset the short-circuit counter (called at the start of each pipeline run)."""
    global _failure_count
    _failure_count = 0


@dataclass(frozen=True)
class FeedingPeriod:
    """A solunar major or minor feeding period."""

    kind: str  # "major" or "minor"
    start: datetime
    end: datetime


@dataclass
class SolunarDay:
    """Solunar state for one day at one spot."""

    day: date
    moon: MoonInfo
    periods: list[FeedingPeriod] = field(default_factory=list)
    api_day_rating: float | None = None  # raw API value, kept for display only
    source: str = "moon-phase fallback"

    def overlaps_window(
        self, start: datetime, end: datetime
    ) -> FeedingPeriod | None:
        """Return the first feeding period overlapping [start, end], if any."""
        for period in self.periods:
            if period.start <= end and period.end >= start:
                return period
        return None


def fetch_solunar_raw(
    spot: Spot,
    day: date,
    tz_offset_hours: float,
    session: requests.Session | None = None,
) -> dict | None:
    """Fetch raw solunar JSON for a spot and day. Returns None on any failure.

    After ``_MAX_FAILURES`` failures the rest of the run skips the API entirely,
    so a slow or unreachable solunar.org cannot hold the whole pipeline hostage
    — the moon-phase fallback in ``scoring`` is the same signal the API
    contributes most of anyway.
    """
    global _failure_count
    if _failure_count >= _MAX_FAILURES:
        return None

    session = session or requests.Session()
    # API path form: /solunar/{lat},{lng},{YYYYMMDD},{tz_offset}
    url = (
        f"{SOLUNAR_URL}/{spot.latitude},{spot.longitude},"
        f"{day.strftime('%Y%m%d')},{tz_offset_hours}"
    )
    try:
        resp = session.get(url, timeout=_HTTP_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except (requests.RequestException, ValueError) as exc:
        _failure_count += 1
        if _failure_count >= _MAX_FAILURES:
            logger.warning(
                "Solunar API has failed %d times — skipping it for the rest of this "
                "run (moon-phase fallback continues).",
                _failure_count,
            )
        else:
            logger.warning(
                "Solunar API unavailable for %s on %s (%s) — using moon-phase fallback.",
                spot.name,
                day,
                exc,
            )
        return None


def get_solunar_day(
    spot: Spot,
    day: date,
    tz_offset_hours: float,
    moon: MoonInfo,
    session: requests.Session | None = None,
) -> SolunarDay:
    """Build a SolunarDay, preferring the API and falling back to moon phase."""
    raw = fetch_solunar_raw(spot, day, tz_offset_hours, session)
    if not raw:
        return SolunarDay(day=day, moon=moon)

    periods: list[FeedingPeriod] = []
    for kind in ("major", "minor"):
        for idx in (1, 2):
            period = _parse_period(raw, day, kind, idx)
            if period is not None:
                periods.append(period)

    rating = raw.get("dayRating")
    try:
        rating = float(rating) if rating is not None else None
    except (TypeError, ValueError):
        rating = None

    return SolunarDay(
        day=day,
        moon=moon,
        periods=periods,
        api_day_rating=rating,
        source="solunar.org",
    )


def _parse_period(
    raw: dict, day: date, kind: str, idx: int
) -> FeedingPeriod | None:
    """Parse one major/minor period (e.g. major1) from a solunar response."""
    start_str = raw.get(f"{kind}{idx}Start")
    stop_str = raw.get(f"{kind}{idx}Stop")
    if not start_str or not stop_str:
        return None
    try:
        start = datetime.combine(day, _parse_clock(start_str))
        end = datetime.combine(day, _parse_clock(stop_str))
    except ValueError:
        return None
    if end < start:  # period runs across midnight
        end += timedelta(days=1)
    return FeedingPeriod(kind=kind, start=start, end=end)


def _parse_clock(value: str) -> time:
    """Parse an 'HH:MM' clock string as returned by solunar.org."""
    return datetime.strptime(value.strip(), "%H:%M").time()
