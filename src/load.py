"""Load: reads needed by the pipeline and idempotent upserts into schema plave (SPEC 5, 6, 9, 10).

Every write is an INSERT ... ON CONFLICT DO UPDATE, so re-running within the same hour
(same snapshot_ts) overwrites the facts instead of duplicating them.

Run directly for a local test (calls the API once, writes one snapshot):
    python src/load.py
Run it twice in the same hour: row counts must not change the second time.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, NamedTuple, TypeVar

from sqlalchemy import Column, Engine, MetaData, Table, create_engine, func, text, tuple_
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection
from sqlalchemy.exc import OperationalError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential, wait_random

import config
from transform import ChannelRows, VideoRows

log = logging.getLogger(__name__)
T = TypeVar("T")

# Columns overwritten on conflict. Keys and first_seen_at are never updated.
DIM_CHANNEL_UPDATE = ("title", "custom_url", "uploads_playlist_id", "published_at")
DIM_VIDEO_UPDATE = (
    "channel_id", "title", "published_at", "published_date_key", "duration_sec", "tags",
    "youtube_category_id", "live_broadcast_content", "actual_start_time", "actual_end_time", "category_id",
)
FACT_CHANNEL_UPDATE = (
    "snapshot_ts_local", "date_key", "fetched_at", "subscriber_count", "view_count", "video_count", "run_id",
)
FACT_VIDEO_UPDATE = (
    "snapshot_ts_local", "date_key", "fetched_at", "tier", "view_count", "like_count", "comment_count",
    "concurrent_viewers", "is_view_drop", "run_id",
)


@dataclass
class LoadStats:
    dim_channel: int = 0
    dim_video: int = 0
    fact_channel: int = 0
    fact_video: int = 0

    @property
    def fact_rows(self) -> int:
        return self.fact_channel + self.fact_video


def make_engine(url: str | None = None) -> Engine:
    return create_engine(
        url or config.require_env("DATABASE_URL"),
        pool_pre_ping=True,
        connect_args={"connect_timeout": config.DB_CONNECT_TIMEOUT_SEC, "application_name": "plave-yt-pipeline"},
    )


def _log_db_retry(retry_state) -> None:
    # Only the exception class: the message can contain SQL parameters.
    log.warning(
        "Database error: %s. Retry %d/%d in %.1fs",
        type(retry_state.outcome.exception().orig).__name__,
        retry_state.attempt_number,
        config.DB_MAX_ATTEMPTS - 1,
        retry_state.next_action.sleep,
    )


db_retry = retry(
    retry=retry_if_exception_type(OperationalError),
    stop=stop_after_attempt(config.DB_MAX_ATTEMPTS),
    wait=wait_exponential(multiplier=config.DB_RETRY_INITIAL_WAIT_SEC, max=config.DB_RETRY_MAX_WAIT_SEC)
    + wait_random(0, 1),
    before_sleep=_log_db_retry,
    reraise=True,
)


def _upsert(
    conn: Connection,
    table: Table,
    rows: list[dict[str, Any]],
    key: Iterable[str],
    update_cols: Iterable[str],
    touch_updated_at: bool = False,
) -> int:
    """INSERT ... ON CONFLICT (key) DO UPDATE. Sent as multi-row VALUES batches by SQLAlchemy.

    With touch_updated_at (dimensions), the row is only rewritten when a column actually changed,
    so updated_at means "metadata last changed" rather than "last seen".
    """
    if not rows:
        return 0
    update_cols = list(update_cols)
    stmt = pg_insert(table)
    set_ = {c: stmt.excluded[c] for c in update_cols}
    where = None
    if touch_updated_at:
        set_["updated_at"] = func.now()
        where = tuple_(*(table.c[c] for c in update_cols)).is_distinct_from(
            tuple_(*(stmt.excluded[c] for c in update_cols))
        )
    stmt = stmt.on_conflict_do_update(index_elements=list(key), set_=set_, where=where)
    conn.execute(stmt, rows)
    return len(rows)


class KnownVideo(NamedTuple):
    published_at: datetime
    category_id: int


class Database:
    def __init__(self, engine: Engine | None = None):
        self.engine = engine or make_engine()
        self._tables: dict[str, Table] | None = None

    @db_retry
    def run(self, fn: Callable[[Connection], T]) -> T:
        """Run fn in one transaction, retried on connection errors (safe: every write is an upsert)."""
        with self.engine.begin() as conn:
            return fn(conn)

    def _get_tables(self, conn: Connection) -> dict[str, Table]:
        if self._tables is None:
            md = MetaData(schema=config.DB_SCHEMA)
            self._tables = {
                "dim_channel": Table("dim_channel", md, autoload_with=conn),
                # none_as_null: Python None -> SQL NULL, not the JSON value 'null'
                "dim_video": Table("dim_video", md, Column("tags", JSONB(none_as_null=True)), autoload_with=conn),
                "fact_channel": Table("fact_channel", md, autoload_with=conn),
                "fact_video": Table("fact_video", md, autoload_with=conn),
            }
        return self._tables

    # --- Reads ------------------------------------------------------------------------

    def get_known_videos(self) -> dict[str, KnownVideo]:
        """video_id -> tier selection fields for every video already in dim_video."""
        def fn(conn: Connection) -> dict[str, KnownVideo]:
            rows = conn.execute(text("SELECT video_id, published_at, category_id FROM plave.dim_video"))
            return {r.video_id: KnownVideo(r.published_at, r.category_id) for r in rows}
        return self.run(fn)

    def get_previous_views(self, video_ids: list[str], before: datetime) -> dict[str, int]:
        """Latest view_count strictly before `before` (the current snapshot_ts), per video.

        Strictly before, so re-running in the same hour compares against the previous hour,
        not against the snapshot being overwritten.
        """
        if not video_ids:
            return {}

        def fn(conn: Connection) -> dict[str, int]:
            rows = conn.execute(
                text("""
                    SELECT DISTINCT ON (video_id) video_id, view_count
                    FROM plave.fact_video
                    WHERE video_id = ANY(:ids) AND snapshot_ts < :before AND view_count IS NOT NULL
                    ORDER BY video_id, snapshot_ts DESC
                """),
                {"ids": list(video_ids), "before": before},
            )
            return {r.video_id: r.view_count for r in rows}
        return self.run(fn)

    def count_rows(self, snapshot_ts: datetime | None = None) -> dict[str, int]:
        """Row counts per table (and facts of one snapshot), for idempotency checks."""
        def fn(conn: Connection) -> dict[str, int]:
            counts = {
                t: conn.execute(text(f"SELECT count(*) FROM plave.{t}")).scalar_one()
                for t in ("dim_channel", "dim_video", "fact_channel", "fact_video")
            }
            if snapshot_ts is not None:
                for t in ("fact_channel", "fact_video"):
                    counts[f"{t} @snapshot"] = conn.execute(
                        text(f"SELECT count(*) FROM plave.{t} WHERE snapshot_ts = :ts"), {"ts": snapshot_ts}
                    ).scalar_one()
            return counts
        return self.run(fn)

    # --- Writes -----------------------------------------------------------------------

    def load_snapshot(
        self,
        channel: ChannelRows | None,
        videos: VideoRows,
        run_id: int | None = None,
    ) -> LoadStats:
        """Upsert one snapshot in a single transaction (dimensions first, for the foreign keys)."""
        def fn(conn: Connection) -> LoadStats:
            t = self._get_tables(conn)
            stats = LoadStats()
            if channel is not None:
                stats.dim_channel = _upsert(conn, t["dim_channel"], [channel.dim], ["channel_id"],
                                            DIM_CHANNEL_UPDATE, touch_updated_at=True)
            stats.dim_video = _upsert(conn, t["dim_video"], videos.dims, ["video_id"],
                                      DIM_VIDEO_UPDATE, touch_updated_at=True)
            if channel is not None:
                stats.fact_channel = _upsert(conn, t["fact_channel"], [{**channel.fact, "run_id": run_id}],
                                             ["channel_id", "snapshot_ts"], FACT_CHANNEL_UPDATE)
            stats.fact_video = _upsert(conn, t["fact_video"], [{**f, "run_id": run_id} for f in videos.facts],
                                       ["video_id", "snapshot_ts"], FACT_VIDEO_UPDATE)
            return stats
        return self.run(fn)


# --- Local idempotency test -----------------------------------------------------------

def _smoke_test() -> None:
    import extract
    import transform

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    snap = transform.SnapshotTime.from_start(datetime.now(timezone.utc))
    print(f"snapshot_ts = {snap.snapshot_ts} (local {snap.snapshot_ts_local}, date_key {snap.date_key})")

    client = extract.make_client()
    raw_channel = client.get_channel(config.CHANNEL_ID)
    ids = client.get_playlist_video_ids(raw_channel["uploads_playlist_id"])
    fetched = client.get_videos(ids)

    db = Database()
    previous = db.get_previous_views(ids, snap.snapshot_ts)
    channel_rows = transform.transform_channel(raw_channel, snap)
    video_rows = transform.transform_videos(fetched.videos, snap, previous)

    before = db.count_rows(snap.snapshot_ts)
    stats = db.load_snapshot(channel_rows, video_rows)
    after = db.count_rows(snap.snapshot_ts)

    print(f"\nUpserted: {stats}  (rows_failed={video_rows.rows_failed}, view_drops={video_rows.view_drops})")
    print(f"\n{'table':28}{'before':>10}{'after':>10}{'diff':>8}")
    for name in after:
        print(f"{name:28}{before[name]:>10}{after[name]:>10}{after[name] - before[name]:>+8}")
    print(f"\nQuota units used: {client.quota_units_used}")


if __name__ == "__main__":
    _smoke_test()
