"""Tests for the sent-windows log (de-duplication)."""
from __future__ import annotations

from datetime import date, datetime

from fishing_forecast.state import SentLog


def test_records_and_recognises_a_sent_window():
    log = SentLog()
    window_date = date(2026, 5, 17)
    log.record(window_date, "Nordhavn", "dawn", 4.0)

    # Same window, same rating — already sent.
    assert log.already_sent(window_date, "Nordhavn", "dawn", 4.0)
    # A different window has not been sent.
    assert not log.already_sent(window_date, "Sydhavn", "dawn", 4.0)
    assert not log.already_sent(window_date, "Nordhavn", "dusk", 4.0)


def test_a_meaningfully_improved_window_is_resent():
    log = SentLog()
    window_date = date(2026, 5, 17)
    log.record(window_date, "Nordhavn", "dawn", 3.5)

    # A small uptick is not worth a second email.
    assert log.already_sent(window_date, "Nordhavn", "dawn", 3.5)
    # Half a star better — worth telling him about.
    assert not log.already_sent(window_date, "Nordhavn", "dawn", 4.0)


def test_recording_the_same_window_twice_replaces_the_entry():
    log = SentLog()
    window_date = date(2026, 5, 17)
    log.record(window_date, "Nordhavn", "dawn", 3.5)
    log.record(window_date, "Nordhavn", "dawn", 4.5)
    assert len(log) == 1


def test_prune_drops_old_windows():
    log = SentLog()
    log.record(date(2026, 5, 1), "Nordhavn", "dawn", 4.0)   # old
    log.record(date(2026, 5, 20), "Nordhavn", "dawn", 4.0)  # recent
    log.prune(today=date(2026, 5, 21))
    assert len(log) == 1


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "sent_log.json"
    log = SentLog()
    log.record(date(2026, 5, 17), "Nordhavn", "dawn", 4.0, sent_at=datetime(2026, 5, 15, 7, 0))
    log.save(path)

    reloaded = SentLog.load(path)
    assert reloaded.already_sent(date(2026, 5, 17), "Nordhavn", "dawn", 4.0)


def test_loading_a_missing_file_starts_empty(tmp_path):
    assert len(SentLog.load(tmp_path / "does_not_exist.json")) == 0
