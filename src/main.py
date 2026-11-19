"""Pipeline entry point: extract -> transform -> load, one run_log row per run (SPEC 5, 9).

    python src/main.py

Every run: channel stats, full uploads-playlist scan, stats for the recent tier + new videos.
Full run (also the old tier): no successful full run yet, or the latest is >= 12h older than snapshot_ts.

Status / exit code:
    success  everything fetched and loaded                       exit 0
    partial  a video batch failed or rows were skipped, rest kept exit 0
    failed   fatal error (quota, bad key, DB down, ...)           exit 1 (GitHub emails on failure)
"""

from __future__ import annotations

import logging
import os
import sys
import time
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit

from sqlalchemy.exc import SQLAlchemyError

import config
import extract
import transform
from load import Database
from logger import RunLogger, RunStats, trigger_source

log = logging.getLogger("main")


# --- Tier logic (pure, unit tested) ------------------------------------------------------

def is_full_run(snapshot_ts: datetime, last_full_snapshot: datetime | None) -> bool:
    if last_full_snapshot is None:
        return True  # first ever run: backfill
    return snapshot_ts - last_full_snapshot >= timedelta(hours=config.FULL_RUN_INTERVAL_HOURS)


def select_video_ids(
    playlist_ids: Iterable[str],
    known: Mapping[str, datetime],
    snapshot_ts: datetime,
    full: bool,
) -> list[str]:
    """Videos to fetch, in playlist order. Only videos still in the playlist (deleted ones are dropped).

    Hourly: new videos (not in dim_video) + known videos in the recent tier. Full: everything.
    """
    if full:
        return list(playlist_ids)
    return [
        vid for vid in playlist_ids
        if vid not in known or transform.is_recent(known[vid], snapshot_ts)
    ]


def decide_status(failed_batch_ids: list[str], rows_failed: int) -> str:
    return "partial" if failed_batch_ids or rows_failed else "success"


# --- Error messages ----------------------------------------------------------------------

def _secrets() -> list[str]:
    values = [os.getenv("YOUTUBE_API_KEY") or ""]
    db_url = os.getenv("DATABASE_URL") or ""
    if db_url:
        values.append(db_url)
        try:
            values.append(urlsplit(db_url).password or "")
        except ValueError:
            pass
    return [v for v in values if len(v) >= 4]


def safe_error(exc: BaseException) -> str:
    """One-line error for run_log and logs, with any secret value masked."""
    if isinstance(exc, SQLAlchemyError) and getattr(exc, "orig", None) is not None:
        # Driver message only: str(exc) of SQLAlchemy errors appends the SQL and its parameters.
        orig = exc.orig
        message = f"Database {type(orig).__name__}: {str(orig).strip()}"
    else:
        message = f"{type(exc).__name__}: {exc}"
    for secret in _secrets():
        message = message.replace(secret, "***")
    return message


# --- Pipeline ----------------------------------------------------------------------------

class Client(Protocol):
    quota_units_used: int
    def get_channel(self, channel_id: str) -> dict: ...
    def get_playlist_video_ids(self, playlist_id: str) -> list[str]: ...
    def get_videos(self, video_ids: list[str]) -> extract.VideoFetchResult: ...


def run_pipeline(
    db: Database,
    run_logger: RunLogger,
    client_factory: Callable[[], Client],
    started_at: datetime,
    source: str,
) -> int:
    """Run once and return the process exit code. run_log is written whatever happens after start()."""
    snap = transform.SnapshotTime.from_start(started_at)

    run_logger.close_stale_runs(started_at)
    full = is_full_run(snap.snapshot_ts, run_logger.last_successful_full_snapshot())
    run_type = "full" if full else "hourly"
    run_id = run_logger.start(snap.snapshot_ts, started_at, run_type, source)

    stats = RunStats()
    client: Client | None = None
    status, error = "failed", None
    try:
        client = client_factory()

        raw_channel = client.get_channel(config.CHANNEL_ID)
        playlist_ids = client.get_playlist_video_ids(raw_channel["uploads_playlist_id"])
        stats.videos_in_playlist = len(playlist_ids)

        known = db.get_known_videos()
        stats.new_videos = sum(vid not in known for vid in playlist_ids)
        to_fetch = select_video_ids(playlist_ids, known, snap.snapshot_ts, full)
        log.info("%s run: %d videos in playlist, %d new, fetching %d",
                 run_type, stats.videos_in_playlist, stats.new_videos, len(to_fetch))

        fetched = client.get_videos(to_fetch)
        stats.videos_fetched = len(fetched.videos)

        previous = db.get_previous_views([v["video_id"] for v in fetched.videos], snap.snapshot_ts)
        channel_rows = transform.transform_channel(raw_channel, snap)
        video_rows = transform.transform_videos(fetched.videos, snap, previous)
        stats.rows_failed = video_rows.rows_failed + (channel_rows is None)

        loaded = db.load_snapshot(channel_rows, video_rows, run_id=run_id)
        stats.rows_inserted = loaded.fact_rows

        status = decide_status(fetched.failed_ids, stats.rows_failed)
        if status == "partial":
            problems = list(fetched.errors)
            if fetched.failed_ids:
                problems.append(f"{len(fetched.failed_ids)} videos in failed batches not loaded")
            if stats.rows_failed:
                problems.append(f"{stats.rows_failed} rows skipped by validation")
            error = "; ".join(problems)
    except Exception as exc:  # noqa: BLE001 - every error must end up in run_log
        status, error = "failed", safe_error(exc)
        log.error("Run %d failed: %s", run_id, error)
    finally:
        if client is not None:
            stats.quota_units_used = client.quota_units_used

    try:
        run_logger.finish(run_id, status, stats, error)
    except Exception as exc:  # noqa: BLE001
        log.error("Could not write the result of run %d to run_log: %s", run_id, safe_error(exc))
        return 1
    return 1 if status == "failed" else 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    started_at = datetime.now(timezone.utc)
    t0 = time.monotonic()

    try:
        db = Database()
        run_logger = RunLogger(db)
        code = run_pipeline(db, run_logger, extract.make_client, started_at, trigger_source())
    except Exception as exc:  # noqa: BLE001 - before run_log has a row (e.g. DB unreachable)
        log.error("Pipeline could not start: %s", safe_error(exc))
        return 1
    log.info("Done in %.1fs, exit code %d", time.monotonic() - t0, code)
    return code


if __name__ == "__main__":
    sys.exit(main())
