"""Snapshot time (SPEC 6) and validation (SPEC 8) in transform.py."""

import logging
from datetime import datetime, timedelta, timezone

import config
from transform import SnapshotTime, is_recent, transform_channel, transform_videos

UTC = timezone.utc
CHANNEL_ID = "UCPZIPuQPrfrUG9Xe_okEmQA"
# 2026-11-19 17:42 UTC = 2026-11-20 00:42 in Vietnam: date_key must use the local date.
START = datetime(2026, 11, 19, 17, 42, 13, 500, tzinfo=UTC)
SNAP = SnapshotTime.from_start(START)
FETCHED = START + timedelta(seconds=5)


def _video(**overrides):
    v = {
        "video_id": "YXCYFujG2iU",
        "channel_id": CHANNEL_ID,
        "title": "PLAVE 'Star Trace' M/V",
        "published_at": datetime(2026, 11, 8, 10, 0, 2, tzinfo=UTC),
        "duration_sec": 184,
        "tags": ["PLAVE"],
        "youtube_category_id": "10",
        "live_broadcast_content": "none",
        "actual_start_time": None,
        "actual_end_time": None,
        "view_count": 1000,
        "like_count": 100,
        "comment_count": 10,
        "concurrent_viewers": None,
        "fetched_at": FETCHED,
    }
    v.update(overrides)
    return v


def _channel(**overrides):
    c = {
        "channel_id": CHANNEL_ID,
        "title": "PLAVE 플레이브",
        "custom_url": "@plave_official",
        "uploads_playlist_id": "UUPZIPuQPrfrUG9Xe_okEmQA",
        "published_at": datetime(2022, 6, 16, 5, 44, 54, tzinfo=UTC),
        "subscriber_count": 1220000,
        "view_count": 958879621,
        "video_count": 1835,
        "fetched_at": FETCHED,
    }
    c.update(overrides)
    return c


# --- Snapshot time ----------------------------------------------------------------

def test_snapshot_time_floored_and_local():
    assert SNAP.snapshot_ts == datetime(2026, 11, 19, 17, 0, tzinfo=UTC)
    assert SNAP.snapshot_ts_local == datetime(2026, 11, 20, 0, 0)  # naive, UTC+7
    assert SNAP.snapshot_ts_local.tzinfo is None
    assert SNAP.date_key == 20261120


def test_snapshot_time_same_hour_is_identical():
    assert SnapshotTime.from_start(START.replace(minute=0, second=0)) == SNAP
    assert SnapshotTime.from_start(START.replace(minute=59, second=59)) == SNAP


def test_is_recent_boundary():
    ts = SNAP.snapshot_ts
    assert is_recent(ts - timedelta(days=config.RECENT_WINDOW_DAYS), ts)
    assert not is_recent(ts - timedelta(days=config.RECENT_WINDOW_DAYS, seconds=1), ts)
    assert is_recent(ts + timedelta(days=2), ts)  # scheduled Premiere


# --- Videos -----------------------------------------------------------------------

def test_valid_video_builds_dim_and_fact():
    out = transform_videos([_video()], SNAP, previous_views={})
    assert out.rows_failed == 0
    dim, fact = out.dims[0], out.facts[0]
    assert dim["category_id"] == config.CATEGORY_MUSIC_VIDEO
    assert dim["published_date_key"] == 20261108  # 17:00 local
    assert fact["snapshot_ts"] == SNAP.snapshot_ts
    assert fact["date_key"] == 20261120
    assert fact["fetched_at"] == FETCHED
    assert fact["tier"] == "recent"
    assert fact["is_view_drop"] is False


def test_published_date_key_uses_vietnam_date():
    v = _video(published_at=datetime(2026, 11, 8, 18, 30, tzinfo=UTC))  # 01:30 on Nov 9 in VN
    assert transform_videos([v], SNAP, {}).dims[0]["published_date_key"] == 20261109


def test_old_tier():
    v = _video(published_at=datetime(2025, 1, 1, tzinfo=UTC))
    assert transform_videos([v], SNAP, {}).facts[0]["tier"] == "old"


def test_null_like_and_comment_kept_as_null():
    out = transform_videos([_video(like_count=None, comment_count=None)], SNAP, {})
    assert out.rows_failed == 0
    assert out.facts[0]["like_count"] is None
    assert out.facts[0]["comment_count"] is None


def test_invalid_ids_are_skipped_and_counted(caplog):
    rows = [
        _video(video_id="short"),
        _video(video_id="YXCYFujG2i!"),
        _video(video_id=None),
        _video(channel_id="UUPZIPuQPrfrUG9Xe_okEmQA"),
        _video(),
    ]
    with caplog.at_level(logging.WARNING):
        out = transform_videos(rows, SNAP, {})
    assert out.rows_failed == 4
    assert len(out.dims) == len(out.facts) == 1
    assert caplog.text.count("Skipping video row") == 4


def test_negative_count_is_skipped():
    out = transform_videos([_video(like_count=-1), _video(view_count=-5)], SNAP, {})
    assert out.rows_failed == 2
    assert out.facts == []


def test_null_title_or_published_at_is_skipped():
    out = transform_videos([_video(title=None), _video(published_at=None)], SNAP, {})
    assert out.rows_failed == 2


def test_null_view_count_warns_but_keeps(caplog):
    with caplog.at_level(logging.WARNING):
        out = transform_videos([_video(view_count=None)], SNAP, {"YXCYFujG2iU": 500})
    assert out.rows_failed == 0
    assert out.facts[0]["view_count"] is None
    assert out.facts[0]["is_view_drop"] is False
    assert "view_count is NULL" in caplog.text


def test_view_drop_flagged_and_kept(caplog):
    with caplog.at_level(logging.WARNING):
        out = transform_videos([_video(view_count=900)], SNAP, {"YXCYFujG2iU": 1000})
    assert out.facts[0]["is_view_drop"] is True
    assert out.view_drops == 1
    assert "dropped 1000 -> 900" in caplog.text


def test_no_drop_when_equal_higher_or_no_previous():
    rows = [_video(video_id="aaaaaaaaaaa", view_count=1000),
            _video(video_id="bbbbbbbbbbb", view_count=1001),
            _video(video_id="ccccccccccc", view_count=5)]
    out = transform_videos(rows, SNAP, {"aaaaaaaaaaa": 1000, "bbbbbbbbbbb": 1000})
    assert [f["is_view_drop"] for f in out.facts] == [False, False, False]


# --- Channel ----------------------------------------------------------------------

def test_channel_rows():
    rows = transform_channel(_channel(), SNAP)
    assert rows.dim["uploads_playlist_id"] == "UUPZIPuQPrfrUG9Xe_okEmQA"
    assert rows.fact["date_key"] == 20261120
    assert rows.fact["subscriber_count"] == 1220000


def test_channel_hidden_subscribers_ok():
    assert transform_channel(_channel(subscriber_count=None), SNAP).fact["subscriber_count"] is None


def test_channel_invalid_is_rejected():
    assert transform_channel(_channel(channel_id="PLAVE"), SNAP) is None
    assert transform_channel(_channel(view_count=-1), SNAP) is None
