#!/usr/bin/env python3
"""Command-line entry point for the Copenhagen Fishing Forecast.

Typical use:

    python run.py                 # live data, send the email (the workflow path)
    python run.py --mock --dry-run  # synthetic data, write the email to ./output
    python run.py --dry-run       # live data, but write the email instead of sending
    python run.py --verbose       # add debug logging

Credentials (ICLOUD_EMAIL, ICLOUD_APP_PASSWORD) are read from the environment.
For local runs you can put them in a .env file next to this script — it is
git-ignored and loaded automatically.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from fishing_forecast.config import Config, ConfigError
from fishing_forecast.emailer import EmailError
from fishing_forecast.pipeline import run
from fishing_forecast.weather import WeatherError

REPO_ROOT = Path(__file__).resolve().parent


def _load_env_file(path: Path) -> None:
    """Load simple KEY=VALUE lines from a .env file into the environment.

    Existing environment variables always win, so GitHub Actions secrets are
    never overridden by a stray local file.
    """
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Email a shore-fishing recommendation for Copenhagen when "
        "conditions look promising — and stay quiet when they do not."
    )
    parser.add_argument(
        "--config",
        default=str(REPO_ROOT / "config.yaml"),
        help="Path to the config file (default: ./config.yaml).",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Use synthetic forecast data instead of the live APIs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Write the email to ./output instead of sending it.",
    )
    parser.add_argument(
        "--state",
        default=str(REPO_ROOT / "data" / "sent_log.json"),
        help="Path to the sent-windows log (default: ./data/sent_log.json).",
    )
    parser.add_argument(
        "--output",
        default=str(REPO_ROOT / "output"),
        help="Where --dry-run writes the email (default: ./output).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    _load_env_file(REPO_ROOT / ".env")

    try:
        config = Config.load(args.config)
        result = run(
            config,
            mock=args.mock,
            dry_run=args.dry_run,
            state_path=args.state,
            output_dir=args.output,
        )
    except ConfigError as exc:
        logging.error("Configuration problem: %s", exc)
        return 2
    except WeatherError as exc:
        logging.error("Weather data problem: %s", exc)
        return 1
    except EmailError as exc:
        logging.error("Email problem: %s", exc)
        return 1

    if result.emailed:
        logging.info("Done — %s", result.message)
    elif result.dry_run_path is not None:
        logging.info("Done — %s", result.message)
    else:
        logging.info("Done — %s", result.message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
