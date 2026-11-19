"""Tier logic and run status in main.py, with fake DB / API (no network)."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import OperationalError

import config
import extract
import main
from load import LoadStats

UTC = timezone.utc
START = datetime(2026, 11, 19, 14, 17, 5, tzinfo=UTC)
SNAP_TS = START.replace(minute=0, second=0, microsecond=0)
CHANNEL_ID = "UCPZIPuQPrfrUG9Xe_okEmQA"
OLD = SNAP_TS - timedelta(days=200)
RECENT = SNAP_TS - timedelta(days=3)


# --- is_full_run ----------------------------------------------------------------------

def test_first_run_is_full():
    assert main.is_full_run(SNAP_TS, None)


@pytest.mark.parametrize("hours_ago, expected", [(0, False), (11, False), (12, True), (30, True)])
def test_full_run_every_12h(hours_ago, expected):
    assert main.is_full_run(SNAP_TS, SNAP_TS - timedelta(hours=hours_ago)) is expected


# --- select_video_ids ---------------------------------------------------------------

def test_hourly_selects_recent_and_new_in_playlist_order():
    playlist = ["new00000001", "recent00001", "old00000001"]
    known = {"recent00001": RECENT, "old00000001": OLD, "deleted0001": RECENT}
    assert main.select_video_ids(playlist, known, SNAP_TS, full=False) == ["new00000001", "recent00001"]


def test_full_selects_whole_playlist_only():
    playlist = ["recent00001", "old00000001"]
    known = {"recent00001": RECENT, "old00000001": OLD, "deleted0001": OLD}
    assert main.select_video_ids(playlist, known, SNAP_TS, full=True) == playlist


def test_recent_window_boundary():
    edge = SNAP_TS - timedelta(days=config.RECENT_WINDOW_DAYS)
    known = {"edge0000001": edge, "older000001": edge - timedelta(seconds=1)}
    assert main.select_video_ids(list(known), known, SNAP_TS, full=False) == ["edge0000001"]


# --- safe_error -----------------------------------------------------------------------

def test_safe_error_masks_secrets(monkeypatch):
    monkeypatch.setenv("YOUTUBE_API_KEY", "AIzaSECRETKEY")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p4ssw0rd@host:5432/postgres")
    msg = main.safe_error(RuntimeError("key=AIzaSECRETKEY pw=p4ssw0rd"))
    assert "AIzaSECRETKEY" not in msg and "p4ssw0rd" not in msg


def test_safe_error_sqlalchemy_drops_sql_params():
    exc = OperationalError("SELECT secret_param", {"x": "param_value"}, Exception("server closed"))
    msg = main.safe_error(exc)
    assert "server closed" in msg and "param_value" not in msg


# --- run_pipeline with fakes ----------------------------------------------------------

def _raw_video(video_id, published_at, views=100):
    return {
        "video_id": video_id, "channel_id": CHANNEL_ID, "title": "PLAVE clip", "published_at": published_at,
        "duration_sec": 60, "tags": None, "youtube_category_id": "10", "live_broadcast_content": "none",
        "actual_start_time": None, "actual_end_time": None, "view_count": views, "like_count": 1,
        "comment_count": 1, "concurrent_viewers": None, "fetched_at": START,
    }


class FakeClient:
    def __init__(self, playlist, published, failed_ids=(), fatal=False):
        self.playlist, self.published, self.failed_ids, self.fatal = playlist, published, list(failed_ids), fatal
        self.quota_units_used = 0
        self.requested = None

    def get_channel(self, channel_id):
        self.quota_units_used += 1
        if self.fatal:
            raise extract.YouTubeFatalError("YouTube channels.list: HTTP 403 (quotaExceeded)")
        return {"channel_id": channel_id, "title": "PLAVE", "custom_url": "@plave",
                "uploads_playlist_id": "UU" + channel_id[2:], "published_at": OLD,
                "subscriber_count": 1, "view_count": 2, "video_count": 3, "fetched_at": START}

    def get_playlist_video_ids(self, playlist_id):
        self.quota_units_used += 1
        return list(self.playlist)

    def get_videos(self, ids):
        self.quota_units_used += 1
        self.requested = list(ids)
        res = extract.VideoFetchResult()
        for vid in ids:
            if vid in self.failed_ids:
                res.failed_ids.append(vid)
            else:
                res.videos.append(_raw_video(vid, self.published[vid]))
        if res.failed_ids:
            res.errors.append("YouTube videos.list: HTTP 503 (backendError)")
        return res


class FakeDb:
    def __init__(self, known):
        self.known = known
        self.loaded = None

    def get_known_videos(self):
        return dict(self.known)

    def get_previous_views(self, ids, before):
        return {}

    def load_snapshot(self, channel, videos, run_id=None):
        self.loaded = (channel, videos, run_id)
        return LoadStats(dim_channel=1, dim_video=len(videos.dims), fact_channel=1, fact_video=len(videos.facts))


class FakeRunLogger:
    def __init__(self, last_full=None):
        self.last_full = last_full
        self.started = None
        self.finished = None

    def close_stale_runs(self, now=None):
        return 0

    def last_successful_full_snapshot(self):
        return self.last_full

    def start(self, snapshot_ts, start_time, run_type, source):
        self.started = (snapshot_ts, run_type, source)
        return 42

    def finish(self, run_id, status, stats, error_message=None):
        self.finished = (run_id, status, stats, error_message)


PUBLISHED = {"new00000001": RECENT, "recent00001": RECENT, "old00000001": OLD}
PLAYLIST = list(PUBLISHED)
KNOWN = {"recent00001": RECENT, "old00000001": OLD}


def test_hourly_run_success():
    db, rl, client = FakeDb(KNOWN), FakeRunLogger(last_full=SNAP_TS - timedelta(hours=2)), FakeClient(PLAYLIST, PUBLISHED)
    code = main.run_pipeline(db, rl, lambda: client, START, "local")

    assert code == 0
    assert rl.started == (SNAP_TS, "hourly", "local")
    assert client.requested == ["new00000001", "recent00001"]
    run_id, status, stats, error = rl.finished
    assert (run_id, status, error) == (42, "success", None)
    assert (stats.videos_in_playlist, stats.new_videos, stats.videos_fetched) == (3, 1, 2)
    assert stats.rows_inserted == 3 and stats.rows_failed == 0 and stats.quota_units_used == 3
    assert db.loaded[2] == 42  # facts carry the run_id


def test_first_run_is_full_backfill():
    db, rl, client = FakeDb({}), FakeRunLogger(last_full=None), FakeClient(PLAYLIST, PUBLISHED)
    assert main.run_pipeline(db, rl, lambda: client, START, "schedule") == 0
    assert rl.started[1] == "full"
    assert client.requested == PLAYLIST
    assert rl.finished[2].new_videos == 3


def test_failed_batch_is_partial_and_keeps_the_rest():
    db, rl = FakeDb(KNOWN), FakeRunLogger(last_full=None)
    client = FakeClient(PLAYLIST, PUBLISHED, failed_ids=["old00000001"])
    assert main.run_pipeline(db, rl, lambda: client, START, "local") == 0
    _, status, stats, error = rl.finished
    assert status == "partial"
    assert stats.videos_fetched == 2 and len(db.loaded[1].facts) == 2
    assert "503" in error and "1 videos in failed batches" in error


def test_invalid_row_is_partial():
    published = {**PUBLISHED, "bad id!": RECENT}
    db, rl, client = FakeDb(KNOWN), FakeRunLogger(), FakeClient(PLAYLIST + ["bad id!"], published)
    main.run_pipeline(db, rl, lambda: client, START, "local")
    _, status, stats, _ = rl.finished
    assert status == "partial" and stats.rows_failed == 1


def test_fatal_api_error_fails_run_and_exits_nonzero():
    db, rl, client = FakeDb(KNOWN), FakeRunLogger(), FakeClient(PLAYLIST, PUBLISHED, fatal=True)
    assert main.run_pipeline(db, rl, lambda: client, START, "local") == 1
    _, status, stats, error = rl.finished
    assert status == "failed" and "quotaExceeded" in error
    assert stats.quota_units_used == 1
    assert db.loaded is None


def test_missing_api_key_fails_run():
    def no_key():
        raise RuntimeError("Environment variable YOUTUBE_API_KEY is not set")
    rl = FakeRunLogger()
    assert main.run_pipeline(FakeDb(KNOWN), rl, no_key, START, "local") == 1
    assert rl.finished[1] == "failed" and "YOUTUBE_API_KEY" in rl.finished[3]


def test_finish_failure_exits_nonzero():
    class BrokenFinish(FakeRunLogger):
        def finish(self, *args, **kwargs):
            raise OperationalError("UPDATE", {}, Exception("connection lost"))
    client = FakeClient(PLAYLIST, PUBLISHED)
    assert main.run_pipeline(FakeDb(KNOWN), BrokenFinish(), lambda: client, START, "local") == 1
