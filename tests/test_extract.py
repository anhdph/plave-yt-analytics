"""Offline tests for extract.py: parsing, NULL handling, retry rules. No real API calls."""

from datetime import datetime, timezone

import httplib2
import pytest
from googleapiclient.errors import HttpError
from tenacity import wait_none

import extract
from extract import (
    YouTubeClient,
    YouTubeFatalError,
    YouTubeTransientError,
    parse_channel,
    parse_duration,
    parse_video,
)

FETCHED = datetime(2026, 11, 19, 3, 5, tzinfo=timezone.utc)
FAKE_KEY = "FAKE-API-KEY-123"


# --- parse_duration -----------------------------------------------------------

@pytest.mark.parametrize(
    "value, expected",
    [
        ("PT3M4S", 184),
        ("PT26S", 26),
        ("PT3M", 180),
        ("PT1H2M3S", 3723),
        ("P1DT1S", 86401),
        ("P0D", 0),  # live / upcoming
        ("PT0S", 0),
        (None, None),
        ("", None),
        ("P", None),
        ("PT", None),
        ("3:04", None),
    ],
)
def test_parse_duration(value, expected):
    assert parse_duration(value) == expected


# --- parse_video / parse_channel ------------------------------------------------

def _video_item(**overrides):
    item = {
        "id": "YXCYFujG2iU",
        "snippet": {
            "channelId": "UCPZIPuQPrfrUG9Xe_okEmQA",
            "title": "PLAVE 'Star Trace' M/V",
            "publishedAt": "2026-11-08T10:00:02Z",
            "tags": ["PLAVE", "플레이브"],
            "categoryId": "10",
            "liveBroadcastContent": "none",
        },
        "contentDetails": {"duration": "PT3M4S"},
        "statistics": {"viewCount": "485052", "likeCount": "17126", "commentCount": "1023"},
    }
    item.update(overrides)
    return item


def test_parse_video_normal():
    v = parse_video(_video_item(), FETCHED)
    assert v["video_id"] == "YXCYFujG2iU"
    assert v["published_at"] == datetime(2026, 11, 8, 10, 0, 2, tzinfo=timezone.utc)
    assert v["duration_sec"] == 184
    assert v["tags"] == ["PLAVE", "플레이브"]
    assert (v["view_count"], v["like_count"], v["comment_count"]) == (485052, 17126, 1023)
    assert v["actual_start_time"] is None
    assert v["concurrent_viewers"] is None
    assert v["fetched_at"] == FETCHED


def test_parse_video_hidden_likes_and_disabled_comments_are_null_not_zero():
    v = parse_video(_video_item(statistics={"viewCount": "10"}), FETCHED)
    assert v["view_count"] == 10
    assert v["like_count"] is None
    assert v["comment_count"] is None


def test_parse_video_live_details():
    v = parse_video(
        _video_item(liveStreamingDetails={
            "actualStartTime": "2026-11-10T11:00:00Z",
            "concurrentViewers": "5321",
        }),
        FETCHED,
    )
    assert v["actual_start_time"] == datetime(2026, 11, 10, 11, tzinfo=timezone.utc)
    assert v["actual_end_time"] is None
    assert v["concurrent_viewers"] == 5321


def test_parse_video_no_tags():
    item = _video_item()
    del item["snippet"]["tags"]
    assert parse_video(item, FETCHED)["tags"] is None


def test_parse_channel_hidden_subscribers_and_uploads_fallback():
    c = parse_channel(
        {
            "id": "UCPZIPuQPrfrUG9Xe_okEmQA",
            "snippet": {"title": "PLAVE", "customUrl": "@plave_official", "publishedAt": "2023-01-01T00:00:00Z"},
            "statistics": {"hiddenSubscriberCount": True, "subscriberCount": "0",
                           "viewCount": "100", "videoCount": "5"},
            "contentDetails": {},
        },
        FETCHED,
    )
    assert c["subscriber_count"] is None
    assert c["uploads_playlist_id"] == "UUPZIPuQPrfrUG9Xe_okEmQA"
    assert (c["view_count"], c["video_count"]) == (100, 5)


# --- Retry / error handling -----------------------------------------------------

def _http_error(status, reason):
    content = ('{"error": {"code": %d, "message": "m", "errors": [{"reason": "%s"}]}}' % (status, reason)).encode()
    resp = httplib2.Response({"status": status})
    resp.reason = reason
    return HttpError(resp, content, uri=f"https://www.googleapis.com/youtube/v3/videos?key={FAKE_KEY}")


class FakeRequest:
    """Raises the scripted exceptions in order, then returns `result`."""

    def __init__(self, *errors, result=None):
        self.errors = list(errors)
        self.result = result if result is not None else {"items": []}
        self.calls = 0

    def execute(self, num_retries=0):
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return self.result


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(YouTubeClient._execute_with_retry.retry, "wait", wait_none())
    return YouTubeClient(FAKE_KEY)


def test_5xx_is_retried_then_succeeds(client):
    req = FakeRequest(_http_error(503, "backendError"), TimeoutError(), result={"items": [1]})
    assert client._call("videos.list", req) == {"items": [1]}
    assert req.calls == 3
    assert client.quota_units_used == 3


def test_5xx_gives_up_after_max_attempts(client):
    req = FakeRequest(*[_http_error(500, "backendError")] * 10)
    with pytest.raises(YouTubeTransientError):
        client._call("videos.list", req)
    assert req.calls == extract.config.API_MAX_ATTEMPTS


@pytest.mark.parametrize("status, reason", [(403, "quotaExceeded"), (400, "keyInvalid"), (404, "notFound")])
def test_4xx_is_fatal_not_retried_and_hides_key(client, status, reason):
    req = FakeRequest(_http_error(status, reason))
    with pytest.raises(YouTubeFatalError) as info:
        client._call("videos.list", req)
    assert req.calls == 1
    assert reason in str(info.value)
    assert FAKE_KEY not in str(info.value)
    assert info.value.__cause__ is None and info.value.__suppress_context__


class FakeYouTube:
    """Minimal stand-in for the googleapiclient resource: videos().list(...)."""

    def __init__(self, requests):
        self._requests = list(requests)
        self.ids_requested = []

    def videos(self):
        return self

    def list(self, part, id):
        self.ids_requested.append(id.split(","))
        return self._requests.pop(0)


def test_get_videos_skips_failed_batch_and_keeps_others(client, monkeypatch):
    monkeypatch.setattr(extract.config, "API_BATCH_SIZE", 2)
    ids = ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc", "ddddddddddd", "eeeeeeeeeee"]
    ok1 = FakeRequest(result={"items": [_video_item(id="aaaaaaaaaaa")]})  # bbb missing (deleted)
    bad = FakeRequest(*[_http_error(500, "backendError")] * 10)
    ok2 = FakeRequest(result={"items": [_video_item(id="eeeeeeeeeee")]})
    client._yt = FakeYouTube([ok1, bad, ok2])

    result = client.get_videos(ids)

    assert [v["video_id"] for v in result.videos] == ["aaaaaaaaaaa", "eeeeeeeeeee"]
    assert result.failed_ids == ["ccccccccccc", "ddddddddddd"]
    assert result.missing_ids == ["bbbbbbbbbbb"]
    assert len(result.errors) == 1


def test_get_videos_fatal_error_stops_run(client):
    client._yt = FakeYouTube([FakeRequest(_http_error(403, "quotaExceeded"))])
    with pytest.raises(YouTubeFatalError):
        client.get_videos(["aaaaaaaaaaa"])
