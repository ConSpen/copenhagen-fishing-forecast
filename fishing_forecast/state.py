"""Remembers which fishing windows have already been emailed.

The script runs every day and looks 2–7 days ahead, so the same good window
will show up on several consecutive runs. Without a memory of what has already
been sent, one good Saturday would generate five identical emails over the
week. The sent log — a small JSON file committed back to the repo by the
workflow — prevents that.

A window is re-sent only if its rating has *improved* by at least half a star
since it was last emailed, which is genuinely worth knowing.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# A window must gain at least this many stars to justify a second email.
_RESEND_IMPROVEMENT = 0.5

# Log entries for windows older than this are dropped on each run.
_RETENTION_DAYS = 14


@dataclass
class SentRecord:
    """One emailed window."""

    date: str      # ISO date of the fishing window itself
    spot: str
    daypart: str
    stars: float
    sent_at: str   # ISO datetime the email was sent

    def key(self) -> tuple[str, str, str]:
        return (self.date, self.spot, self.daypart)


class SentLog:
    """Load/query/update the record of windows already emailed."""

    def __init__(self, records: list[SentRecord] | None = None) -> None:
        self._records: list[SentRecord] = records or []

    @classmethod
    def load(cls, path: str | Path) -> "SentLog":
        """Load the log from disk. A missing or unreadable file starts empty."""
        path = Path(path)
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            records = [SentRecord(**entry) for entry in data]
        except (json.JSONDecodeError, TypeError, OSError) as exc:
            logger.warning(
                "Could not read sent log at %s (%s) — starting with an empty log.",
                path,
                exc,
            )
            return cls()
        return cls(records)

    def save(self, path: str | Path) -> None:
        """Write the log back to disk as pretty-printed JSON."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = [asdict(record) for record in self._records]
        path.write_text(
            json.dumps(payload, indent=2) + "\n", encoding="utf-8"
        )

    def already_sent(
        self, window_date: date, spot: str, daypart: str, stars: float
    ) -> bool:
        """True if this window was emailed and has not meaningfully improved."""
        key = (window_date.isoformat(), spot, daypart)
        for record in self._records:
            if record.key() == key:
                return stars < record.stars + _RESEND_IMPROVEMENT
        return False

    def record(
        self,
        window_date: date,
        spot: str,
        daypart: str,
        stars: float,
        sent_at: datetime | None = None,
    ) -> None:
        """Record (or update) an emailed window."""
        sent_at = sent_at or datetime.now()
        key = (window_date.isoformat(), spot, daypart)
        # Drop any previous record for the same window, then add the new one.
        self._records = [r for r in self._records if r.key() != key]
        self._records.append(
            SentRecord(
                date=window_date.isoformat(),
                spot=spot,
                daypart=daypart,
                stars=stars,
                sent_at=sent_at.isoformat(timespec="seconds"),
            )
        )

    def prune(self, today: date | None = None) -> None:
        """Drop entries for windows older than the retention period."""
        today = today or date.today()
        cutoff = today - timedelta(days=_RETENTION_DAYS)
        kept = []
        for record in self._records:
            try:
                if date.fromisoformat(record.date) >= cutoff:
                    kept.append(record)
            except ValueError:
                # A malformed date string — drop it rather than crash.
                continue
        self._records = kept

    def __len__(self) -> int:
        return len(self._records)
