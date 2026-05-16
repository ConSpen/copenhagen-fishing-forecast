"""Orchestrates a single forecast run: fetch, score, select, email.

The flow is:

  1. For each spot, get a 7-day forecast (live from Open-Meteo, or synthesised
     by mockdata in --mock mode).
  2. For each day inside the planning window, build the dawn and dusk windows,
     score each one, and keep the windows that clear the alert threshold.
  3. Drop windows already emailed (the sent log), unless one has improved.
  4. If anything is left, pick the best, build the email, and either send it
     or — in --dry-run — write it to disk.
  5. Record what was emailed so tomorrow's run does not repeat it.

No promising window means no email. That is the whole point.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from . import mockdata
from .astro import dawn_window, dusk_window, moon_info
from .config import Config, Spot
from .emailer import (
    EmailError,
    Recommendation,
    WindowCandidate,
    build_html,
    build_plaintext,
    build_subject,
    save_dry_run,
    send_email,
)
from .scoring import ScoringError, build_window_conditions, score_conditions
from . import solunar
from .solunar import SolunarDay, get_solunar_day
from .species import recommend
from .state import SentLog
from .weather import SpotForecast, get_spot_forecast, parse_spot_forecast

logger = logging.getLogger(__name__)

# How many windows to list as alternatives beneath the headline pick.
_MAX_ALTERNATIVES = 3


@dataclass
class RunResult:
    """The outcome of a run, for the CLI to report."""

    emailed: bool
    candidates_found: int
    recommendation: Recommendation | None
    dry_run_path: Path | None = None
    message: str = ""


def run(
    config: Config,
    *,
    mock: bool = False,
    dry_run: bool = False,
    today: date | None = None,
    state_path: str | Path = "data/sent_log.json",
    output_dir: str | Path = "output",
) -> RunResult:
    """Run one full forecast cycle and return what happened."""
    tz = ZoneInfo(config.timezone)
    now = datetime.now(tz)
    today = today or now.date()
    generated_at = now.replace(tzinfo=None)
    tz_offset_hours = (now.utcoffset().total_seconds() / 3600.0) if now.utcoffset() else 0.0

    # Reset the solunar short-circuit at the start of every run.
    solunar.reset_failures()

    # The forecast must reach today + max_days; today is day 0, so add one.
    forecast_days = config.planning_window.max_days + 1
    session = None if mock else requests.Session()

    candidates: list[WindowCandidate] = []
    for spot in config.spots:
        forecast = _load_forecast(
            spot, forecast_days, config, mock, today, session
        )
        candidates.extend(
            _evaluate_spot(
                spot, forecast, config, mock, today, tz_offset_hours, session
            )
        )

    logger.info(
        "Evaluated %d spot(s); %d window(s) cleared the %.1f-star threshold.",
        len(config.spots),
        len(candidates),
        config.alert_threshold,
    )

    sent_log = SentLog.load(state_path)
    fresh = [
        c
        for c in candidates
        if not sent_log.already_sent(
            c.window.date, c.spot.name, c.window.daypart, c.score.stars
        )
    ]

    if not fresh:
        if candidates:
            message = (
                f"{len(candidates)} window(s) cleared the threshold, but all "
                "have already been emailed."
            )
        else:
            message = "No windows cleared the alert threshold."
        logger.info("%s No email sent.", message)
        return RunResult(
            emailed=False,
            candidates_found=len(candidates),
            recommendation=None,
            message=message,
        )

    # Best window: highest stars, then earliest start (more planning notice),
    # then highest underlying score as a final tie-break.
    fresh.sort(key=lambda c: (-c.score.stars, c.window.start, -c.score.total))
    best = fresh[0]
    alternatives = fresh[1 : 1 + _MAX_ALTERNATIVES]

    recommendation = Recommendation(
        best=best, alternatives=alternatives, generated_at=generated_at
    )
    subject = build_subject(recommendation, config)
    html = build_html(recommendation)
    plaintext = build_plaintext(recommendation)

    dry_run_path: Path | None = None
    if dry_run:
        dry_run_path = save_dry_run(subject, html, plaintext, output_dir)
        logger.info("Dry run — no email sent, no state written.")
        return RunResult(
            emailed=False,
            candidates_found=len(candidates),
            recommendation=recommendation,
            dry_run_path=dry_run_path,
            message=f"Dry run — email written to {dry_run_path}",
        )

    user, password = _email_credentials()
    send_email(subject, html, plaintext, config.email.recipient, user, password)

    # Record every window shown in the email so it is not repeated tomorrow.
    for candidate in [best, *alternatives]:
        sent_log.record(
            candidate.window.date,
            candidate.spot.name,
            candidate.window.daypart,
            candidate.score.stars,
            sent_at=generated_at,
        )
    sent_log.prune(today)
    sent_log.save(state_path)

    return RunResult(
        emailed=True,
        candidates_found=len(candidates),
        recommendation=recommendation,
        message=f"Emailed: {subject}",
    )


# --------------------------------------------------------------------------
# Per-spot evaluation
# --------------------------------------------------------------------------

def _evaluate_spot(
    spot: Spot,
    forecast: SpotForecast,
    config: Config,
    mock: bool,
    today: date,
    tz_offset_hours: float,
    session: requests.Session | None,
) -> list[WindowCandidate]:
    """Score every dawn/dusk window in the planning window for one spot."""
    candidates: list[WindowCandidate] = []
    window_cfg = config.planning_window

    for offset in range(window_cfg.min_days, window_cfg.max_days + 1):
        day = today + timedelta(days=offset)
        sunrise = forecast.sunrise.get(day)
        sunset = forecast.sunset.get(day)
        if sunrise is None or sunset is None:
            logger.warning(
                "No sunrise/sunset for %s on %s — skipping that day.",
                spot.name,
                day,
            )
            continue

        moon = moon_info(day)
        solunar = _load_solunar(
            spot, day, moon, mock, tz_offset_hours, session
        )

        for window in (
            dawn_window(sunrise, config.daypart),
            dusk_window(sunset, config.daypart),
        ):
            try:
                conditions = build_window_conditions(forecast, window, solunar)
            except ScoringError as exc:
                logger.warning("%s Skipping.", exc)
                continue

            score = score_conditions(conditions, config.weights)
            if score.is_promising(config.alert_threshold):
                species = recommend(window.date.month, conditions.water_temp_c)
                candidates.append(
                    WindowCandidate(
                        spot=spot,
                        window=window,
                        score=score,
                        species=species,
                    )
                )

    return candidates


# --------------------------------------------------------------------------
# Data loading — the only place live and mock modes differ
# --------------------------------------------------------------------------

def _load_forecast(
    spot: Spot,
    days: int,
    config: Config,
    mock: bool,
    today: date,
    session: requests.Session | None,
) -> SpotForecast:
    if mock:
        raw_forecast = mockdata.generate_forecast_raw(
            spot, days, today, config.timezone
        )
        raw_marine = mockdata.generate_marine_raw(spot, days, today)
        return parse_spot_forecast(raw_forecast, raw_marine, spot)
    return get_spot_forecast(spot, days, config.timezone, session)


def _load_solunar(
    spot: Spot,
    day: date,
    moon,
    mock: bool,
    tz_offset_hours: float,
    session: requests.Session | None,
) -> SolunarDay:
    if mock:
        return mockdata.generate_solunar_day(spot, day, moon)
    return get_solunar_day(spot, day, tz_offset_hours, moon, session)


def _email_credentials() -> tuple[str, str]:
    """Read SMTP credentials from the environment. Raises EmailError if absent."""
    user = os.environ.get("ICLOUD_EMAIL")
    password = os.environ.get("ICLOUD_APP_PASSWORD")
    if not user or not password:
        raise EmailError(
            "ICLOUD_EMAIL and ICLOUD_APP_PASSWORD must be set — as environment "
            "variables locally, or as GitHub Actions secrets in the workflow."
        )
    return user, password
