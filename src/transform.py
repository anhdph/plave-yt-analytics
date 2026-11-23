"""Transform: snapshot time (SPEC 6), classification (SPEC 7) and validation (SPEC 8).

Pure functions: no API or database access. Input is the dicts produced by extract.py,
output is rows keyed by DB column names for load.py (run_id is added at load time).
Classification rules themselves live in config.py.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import config

log = logging.getLogger(__name__)

COUNT_FIELDS_VIDEO = ("view_count", "like_count", "comment_count", "concurrent_viewers")
COUNT_FIELDS_CHANNEL = ("subscriber_count", "view_count", "video_count")


# --- Time ------------------------------------------------------------------------

def floor_to_hour(ts: datetime) -> datetime:
    """Run start time -> snapshot_ts (UTC, floored to the hour)."""
    return ts.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def to_local(ts: datetime) -> datetime:
    """Aware timestamp -> naive Asia/Ho_Chi_Minh time (for TIMESTAMP columns)."""
    return ts.astimezone(config.LOCAL_TZ).replace(tzinfo=None)


def to_date_key(local_dt: datetime) -> int:
    return local_dt.year * 10000 + local_dt.month * 100 + local_dt.day


def is_recent(published_at: datetime, snapshot_ts: datetime) -> bool:
    """Recent tier: published within RECENT_WINDOW_DAYS of snapshot_ts (future Premieres count as recent)."""
    return published_at >= snapshot_ts - timedelta(days=config.RECENT_WINDOW_DAYS)


@dataclass(frozen=True)
class SnapshotTime:
    snapshot_ts: datetime
    snapshot_ts_local: datetime
    date_key: int

    @classmethod
    def from_start(cls, start_time: datetime) -> SnapshotTime:
        ts = floor_to_hour(start_time)
        local = to_local(ts)
        return cls(ts, local, to_date_key(local))


# --- Classification ----------------------------------------------------------------

def classify(
    title: str | None,
    duration_sec: int | None,
    actual_start_time: datetime | None,
    live_broadcast_content: str | None = None,
) -> int:
    """Return the category_id of the first rule in config.CATEGORY_RULES that matches."""
    video = config.VideoInfo.build(title, duration_sec, actual_start_time, live_broadcast_content)
    for category_id, rule in config.CATEGORY_RULES:
        if rule(video):
            return category_id
    return config.CATEGORY_FALLBACK


# --- Validation --------------------------------------------------------------------

def _negative_fields(row: Mapping[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [f for f in fields if row.get(f) is not None and row[f] < 0]


def validate_video(v: Mapping[str, Any]) -> str | None:
    """Return why the row must be skipped, or None if it can be loaded. Logs WARNINGs."""
    video_id = v.get("video_id")
    if not isinstance(video_id, str) or not config.VIDEO_ID_RE.match(video_id):
        return f"invalid video_id {video_id!r}"
    channel_id = v.get("channel_id")
    if not isinstance(channel_id, str) or not config.CHANNEL_ID_RE.match(channel_id):
        return f"video {video_id}: invalid channel_id {channel_id!r}"
    negative = _negative_fields(v, COUNT_FIELDS_VIDEO)
    if negative:
        return f"video {video_id}: negative {', '.join(negative)}"
    # title and published_at are NOT NULL in dim_video, so the row cannot be stored.
    for name in ("title", "published_at"):
        if v.get(name) is None:
            return f"video {video_id}: {name} is NULL"
    if v.get("view_count") is None:
        log.warning("video %s: view_count is NULL (kept)", video_id)
    return None


def validate_channel(c: Mapping[str, Any]) -> str | None:
    channel_id = c.get("channel_id")
    if not isinstance(channel_id, str) or not config.CHANNEL_ID_RE.match(channel_id):
        return f"invalid channel_id {channel_id!r}"
    negative = _negative_fields(c, COUNT_FIELDS_CHANNEL)
    if negative:
        return f"channel {channel_id}: negative {', '.join(negative)}"
    for name in ("title", "uploads_playlist_id"):  # NOT NULL in dim_channel
        if c.get(name) is None:
            return f"channel {channel_id}: {name} is NULL"
    if c.get("view_count") is None:
        log.warning("channel %s: view_count is NULL (kept)", channel_id)
    # subscriber_count NULL is expected when the channel hides it.
    return None


# --- Row builders ------------------------------------------------------------------

@dataclass
class ChannelRows:
    dim: dict[str, Any]
    fact: dict[str, Any]


@dataclass
class VideoRows:
    dims: list[dict[str, Any]] = field(default_factory=list)
    facts: list[dict[str, Any]] = field(default_factory=list)
    rows_failed: int = 0
    view_drops: int = 0


def transform_channel(raw: Mapping[str, Any], snap: SnapshotTime) -> ChannelRows | None:
    """None if the channel row is invalid (caller counts it in rows_failed)."""
    reason = validate_channel(raw)
    if reason:
        log.warning("Skipping channel row: %s", reason)
        return None
    dim = {k: raw[k] for k in ("channel_id", "title", "custom_url", "uploads_playlist_id", "published_at")}
    fact = {
        "channel_id": raw["channel_id"],
        "snapshot_ts": snap.snapshot_ts,
        "snapshot_ts_local": snap.snapshot_ts_local,
        "date_key": snap.date_key,
        "fetched_at": raw["fetched_at"],
        "subscriber_count": raw["subscriber_count"],
        "view_count": raw["view_count"],
        "video_count": raw["video_count"],
    }
    return ChannelRows(dim, fact)


def transform_videos(
    raw_videos: list[Mapping[str, Any]],
    snap: SnapshotTime,
    previous_views: Mapping[str, int],
) -> VideoRows:
    """Validate, classify and split into dim_video / fact_video rows.

    previous_views: latest stored view_count per video_id (from fact_video), used for is_view_drop.
    """
    out = VideoRows()
    for v in raw_videos:
        reason = validate_video(v)
        if reason:
            log.warning("Skipping video row: %s", reason)
            out.rows_failed += 1
            continue

        video_id = v["video_id"]
        out.dims.append({
            "video_id": video_id,
            "channel_id": v["channel_id"],
            "title": v["title"],
            "published_at": v["published_at"],
            "published_date_key": to_date_key(to_local(v["published_at"])),
            "duration_sec": v["duration_sec"],
            "tags": v["tags"],
            "youtube_category_id": v["youtube_category_id"],
            "live_broadcast_content": v["live_broadcast_content"],
            "actual_start_time": v["actual_start_time"],
            "actual_end_time": v["actual_end_time"],
            "category_id": classify(
                v["title"], v["duration_sec"], v["actual_start_time"], v["live_broadcast_content"]
            ),
        })

        previous = previous_views.get(video_id)
        views = v["view_count"]
        is_view_drop = previous is not None and views is not None and views < previous
        if is_view_drop:
            log.warning("video %s: view_count dropped %d -> %d", video_id, previous, views)
            out.view_drops += 1

        out.facts.append({
            "video_id": video_id,
            "snapshot_ts": snap.snapshot_ts,
            "snapshot_ts_local": snap.snapshot_ts_local,
            "date_key": snap.date_key,
            "fetched_at": v["fetched_at"],
            "tier": "recent" if is_recent(v["published_at"], snap.snapshot_ts) else "old",
            "view_count": views,
            "like_count": v["like_count"],
            "comment_count": v["comment_count"],
            "concurrent_viewers": v["concurrent_viewers"],
            "is_view_drop": is_view_drop,
        })
    return out
