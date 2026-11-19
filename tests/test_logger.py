"""run_log helpers in logger.py that need no database."""

from datetime import datetime, timezone

import pytest

import config
from logger import RunLogger, RunStats, trigger_source, truncate_error


class FakeDb:
    """Records that a transaction was requested; never reached when validation fails."""
    def __init__(self):
        self.calls = 0

    def run(self, fn):
        self.calls += 1
        return None


def test_trigger_source_github(monkeypatch):
    monkeypatch.setenv("GITHUB_EVENT_NAME", "schedule")
    assert trigger_source() == "schedule"


def test_trigger_source_local(monkeypatch):
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)
    assert trigger_source() == "local"


def test_truncate_error():
    assert truncate_error(None) is None
    assert truncate_error("short") == "short"
    long = "x" * (config.ERROR_MESSAGE_MAX_LEN + 100)
    out = truncate_error(long)
    assert len(out) == config.ERROR_MESSAGE_MAX_LEN
    assert out.endswith("[truncated]")


def test_run_stats_defaults():
    s = RunStats()
    assert s.videos_in_playlist is None and s.rows_inserted == 0 and s.quota_units_used == 0


def test_invalid_run_type_rejected_before_db():
    db = FakeDb()
    with pytest.raises(ValueError):
        RunLogger(db).start(datetime.now(timezone.utc), datetime.now(timezone.utc), "daily", "local")
    assert db.calls == 0


@pytest.mark.parametrize("status", ["running", "ok", ""])
def test_invalid_final_status_rejected(status):
    db = FakeDb()
    with pytest.raises(ValueError):
        RunLogger(db).finish(1, status, RunStats())
    assert db.calls == 0
