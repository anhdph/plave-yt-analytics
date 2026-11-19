"""Run log: one row in plave.run_log per pipeline run (SPEC 5, 9, 10, 12).

Lifecycle used by main.py:
    run_id = run_logger.start(snap.snapshot_ts, started_at, "hourly", trigger_source())
    ... extract / transform / load, filling a RunStats ...
    run_logger.finish(run_id, "success" | "partial" | "failed", stats, error_message)

The row is inserted as 'running' first, so a run killed mid-way (GitHub timeout) still leaves
a trace; close_stale_runs() later marks such rows 'failed'.

Run directly for a local test (writes one test row, reads it back, then deletes it):
    python src/logger.py
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

import config
from load import Database

log = logging.getLogger(__name__)


@dataclass
class RunStats:
    """Counters written to run_log when the run finishes. None = step not reached."""
    videos_in_playlist: int | None = None
    new_videos: int | None = None
    videos_fetched: int | None = None
    rows_inserted: int = 0
    rows_failed: int = 0
    quota_units_used: int = 0


def trigger_source() -> str:
    """schedule / workflow_dispatch on GitHub Actions (GITHUB_EVENT_NAME), else local."""
    return os.getenv("GITHUB_EVENT_NAME") or "local"


def truncate_error(message: str | None) -> str | None:
    if message is None:
        return None
    limit = config.ERROR_MESSAGE_MAX_LEN
    return message if len(message) <= limit else message[: limit - 15] + "... [truncated]"


def _check(value: str, allowed: tuple[str, ...], name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, got {value!r}")


class RunLogger:
    def __init__(self, db: Database):
        self.db = db

    def start(self, snapshot_ts: datetime, start_time: datetime, run_type: str, source: str) -> int:
        """Insert a 'running' row and return its run_id."""
        _check(run_type, config.RUN_TYPES, "run_type")

        def fn(conn: Connection) -> int:
            return conn.execute(
                text("""
                    INSERT INTO plave.run_log (snapshot_ts, start_time, status, run_type, trigger_source)
                    VALUES (:snapshot_ts, :start_time, 'running', :run_type, :source)
                    RETURNING run_id
                """),
                {"snapshot_ts": snapshot_ts, "start_time": start_time, "run_type": run_type, "source": source},
            ).scalar_one()

        run_id = self.db.run(fn)
        log.info("Run %d started (%s, %s, snapshot_ts=%s)", run_id, run_type, source, snapshot_ts.isoformat())
        return run_id

    def set_run_type(self, run_id: int, run_type: str) -> None:
        """Change the run type after start (e.g. hourly -> full once the tier rule is evaluated)."""
        _check(run_type, config.RUN_TYPES, "run_type")
        self.db.run(lambda conn: conn.execute(
            text("UPDATE plave.run_log SET run_type = :run_type WHERE run_id = :run_id"),
            {"run_type": run_type, "run_id": run_id},
        ))

    def finish(
        self,
        run_id: int,
        status: str,
        stats: RunStats,
        error_message: str | None = None,
        end_time: datetime | None = None,
    ) -> None:
        """Write the final status, counters, end_time and error (truncated)."""
        _check(status, config.RUN_STATUSES[1:], "final status")
        params: dict[str, Any] = {
            **asdict(stats),
            "run_id": run_id,
            "status": status,
            "end_time": end_time or datetime.now(timezone.utc),
            "error_message": truncate_error(error_message),
        }
        self.db.run(lambda conn: conn.execute(
            text("""
                UPDATE plave.run_log SET
                    end_time = :end_time,
                    status = :status,
                    videos_in_playlist = :videos_in_playlist,
                    new_videos = :new_videos,
                    videos_fetched = :videos_fetched,
                    rows_inserted = :rows_inserted,
                    rows_failed = :rows_failed,
                    quota_units_used = :quota_units_used,
                    error_message = :error_message
                WHERE run_id = :run_id
            """),
            params,
        ))
        level = logging.INFO if status == "success" else logging.WARNING
        log.log(level, "Run %d finished: %s %s", run_id, status, asdict(stats))

    def last_successful_full_snapshot(self) -> datetime | None:
        """snapshot_ts of the latest full run with status 'success' (SPEC 5 tier rule)."""
        return self.db.run(lambda conn: conn.execute(
            text("SELECT max(snapshot_ts) FROM plave.run_log WHERE run_type = 'full' AND status = 'success'")
        ).scalar_one())

    def close_stale_runs(self, now: datetime | None = None) -> int:
        """Mark 'running' rows older than STALE_RUN_AFTER_MIN as failed. Returns how many."""
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(minutes=config.STALE_RUN_AFTER_MIN)
        count = self.db.run(lambda conn: conn.execute(
            text("""
                UPDATE plave.run_log
                SET status = 'failed',
                    error_message = coalesce(error_message, 'Run did not finish (killed or timed out)')
                WHERE status = 'running' AND start_time < :cutoff
            """),
            {"cutoff": cutoff},
        ).rowcount)
        if count:
            log.warning("Closed %d stale 'running' run(s) as failed", count)
        return count


# --- Local test ------------------------------------------------------------------------

def _smoke_test() -> None:
    import transform

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    db = Database()
    rl = RunLogger(db)
    started = datetime.now(timezone.utc)
    snap = transform.SnapshotTime.from_start(started)

    print(f"Last successful full run snapshot_ts: {rl.last_successful_full_snapshot()}")
    print(f"Stale runs closed: {rl.close_stale_runs()}")

    run_id = rl.start(snap.snapshot_ts, started, "hourly", trigger_source())
    rl.set_run_type(run_id, "full")
    rl.finish(run_id, "partial", RunStats(600, 0, 600, 601, 2, 14), "logger smoke test: " + "x" * 5000)

    row = db.run(lambda conn: conn.execute(
        text("SELECT *, length(error_message) AS err_len FROM plave.run_log WHERE run_id = :id"), {"id": run_id}
    ).mappings().one())
    print("\nRow written:")
    for k, v in row.items():
        if k != "error_message":
            print(f"  {k:20} {v}")

    db.run(lambda conn: conn.execute(text("DELETE FROM plave.run_log WHERE run_id = :id"), {"id": run_id}))
    print(f"\nTest row {run_id} deleted.")


if __name__ == "__main__":
    _smoke_test()
