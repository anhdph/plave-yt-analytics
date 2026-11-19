"""Extract: YouTube Data API v3 calls (SPEC sections 4 and 9).

Uses only channels.list, playlistItems.list and videos.list (1 quota unit each).
Returns plain dicts keyed by DB column names. Validation and classification
happen later in transform.py.

Run directly for a local smoke test (calls the real API, writes nothing):
    python src/extract.py
"""

from __future__ import annotations

import logging
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential, wait_random

import config

log = logging.getLogger(__name__)
# googleapiclient logs full request URLs, including the API key, at DEBUG level.
logging.getLogger("googleapiclient").setLevel(logging.WARNING)


class YouTubeFatalError(Exception):
    """Not retryable (quotaExceeded, invalid key, other 4xx): the run must stop and be marked failed."""


class YouTubeTransientError(Exception):
    """Network error, timeout or HTTP 5xx that still failed after every retry."""


@dataclass
class VideoFetchResult:
    videos: list[dict[str, Any]] = field(default_factory=list)
    failed_ids: list[str] = field(default_factory=list)   # in batches that failed -> run is partial
    missing_ids: list[str] = field(default_factory=list)  # requested but not returned (deleted/private)
    errors: list[str] = field(default_factory=list)


# --- Parsing helpers (pure functions, unit tested) ------------------------------

_DURATION_RE = re.compile(
    r"^P(?:(?P<w>\d+)W)?(?:(?P<d>\d+)D)?(?:T(?:(?P<h>\d+)H)?(?:(?P<m>\d+)M)?(?:(?P<s>\d+)S)?)?$"
)


def parse_duration(value: str | None) -> int | None:
    """ISO 8601 duration (PT3M4S, P1DT2H, P0D) -> seconds. None if missing or malformed."""
    if not value:
        return None
    match = _DURATION_RE.match(value)
    if not match or value in ("P", "PT"):
        return None
    parts = {k: int(v) if v else 0 for k, v in match.groupdict().items()}
    return parts["w"] * 604800 + parts["d"] * 86400 + parts["h"] * 3600 + parts["m"] * 60 + parts["s"]


def parse_ts(value: str | None) -> datetime | None:
    """RFC 3339 timestamp from the API -> timezone-aware datetime (UTC)."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    """API counts are strings. Missing (hidden likes, disabled comments) -> None, never 0."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def uploads_playlist_id(channel_id: str) -> str:
    """Uploads playlist ID derived from the channel ID (UC... -> UU...)."""
    return "UU" + channel_id[2:]


def parse_channel(item: dict[str, Any], fetched_at: datetime) -> dict[str, Any]:
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    details = item.get("contentDetails", {})
    channel_id = item.get("id")
    hidden_subs = stats.get("hiddenSubscriberCount", False)
    return {
        "channel_id": channel_id,
        "title": snippet.get("title"),
        "custom_url": snippet.get("customUrl"),
        "uploads_playlist_id": details.get("relatedPlaylists", {}).get("uploads")
        or (uploads_playlist_id(channel_id) if channel_id else None),
        "published_at": parse_ts(snippet.get("publishedAt")),
        "subscriber_count": None if hidden_subs else to_int(stats.get("subscriberCount")),
        "view_count": to_int(stats.get("viewCount")),
        "video_count": to_int(stats.get("videoCount")),
        "fetched_at": fetched_at,
    }


def parse_video(item: dict[str, Any], fetched_at: datetime) -> dict[str, Any]:
    snippet = item.get("snippet", {})
    stats = item.get("statistics", {})
    details = item.get("contentDetails", {})
    live = item.get("liveStreamingDetails") or {}
    return {
        "video_id": item.get("id"),
        "channel_id": snippet.get("channelId"),
        "title": snippet.get("title"),
        "published_at": parse_ts(snippet.get("publishedAt")),
        "duration_sec": parse_duration(details.get("duration")),
        "tags": snippet.get("tags"),  # list[str] or None
        "youtube_category_id": snippet.get("categoryId"),
        "live_broadcast_content": snippet.get("liveBroadcastContent"),
        "actual_start_time": parse_ts(live.get("actualStartTime")),
        "actual_end_time": parse_ts(live.get("actualEndTime")),
        "view_count": to_int(stats.get("viewCount")),
        "like_count": to_int(stats.get("likeCount")),
        "comment_count": to_int(stats.get("commentCount")),
        "concurrent_viewers": to_int(live.get("concurrentViewers")),
        "fetched_at": fetched_at,
    }


# --- Error handling -------------------------------------------------------------

def _http_status(exc: HttpError) -> int:
    return int(exc.resp.status)


def _http_reason(exc: HttpError) -> str:
    """Short reason such as 'quotaExceeded' or 'keyInvalid'. Never uses str(exc): it contains the URL and key."""
    details = getattr(exc, "error_details", None)
    if isinstance(details, list) and details and isinstance(details[0], dict):
        reason = details[0].get("reason")
        if reason:
            return str(reason)
    return str(getattr(exc, "reason", "") or "unknown")


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, HttpError):
        return _http_status(exc) >= 500
    # OSError covers ConnectionError, TimeoutError and ssl.SSLError.
    return isinstance(exc, (OSError, httplib2.HttpLib2Error))


def _describe(exc: BaseException) -> str:
    if isinstance(exc, HttpError):
        return f"HTTP {_http_status(exc)} ({_http_reason(exc)})"
    return type(exc).__name__


def _log_retry(retry_state) -> None:
    log.warning(
        "YouTube call failed: %s. Retry %d/%d in %.1fs",
        _describe(retry_state.outcome.exception()),
        retry_state.attempt_number,
        config.API_MAX_ATTEMPTS - 1,
        retry_state.next_action.sleep,
    )


# --- Client ---------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


class YouTubeClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        http = httplib2.Http(timeout=config.API_TIMEOUT_SEC)
        # Static discovery document bundled with the library: building needs no network call.
        self._yt = build("youtube", "v3", developerKey=api_key, http=http, cache_discovery=False)
        self.quota_units_used = 0

    def _redact(self, text: str) -> str:
        return text.replace(self._api_key, "***") if self._api_key else text

    @retry(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(config.API_MAX_ATTEMPTS),
        # 2s, 4s, 8s, ... capped, plus up to 1s of jitter
        wait=wait_exponential(multiplier=config.API_RETRY_INITIAL_WAIT_SEC, max=config.API_RETRY_MAX_WAIT_SEC)
        + wait_random(0, 1),
        before_sleep=_log_retry,
        reraise=True,
    )
    def _execute_with_retry(self, request) -> dict[str, Any]:
        self.quota_units_used += 1  # every request sent counts, retries included
        return request.execute(num_retries=0)

    def _call(self, endpoint: str, request) -> dict[str, Any]:
        """Execute with retry and turn library errors into our two error types.

        `from None` drops the original traceback, whose message contains the request URL and API key.
        """
        try:
            return self._execute_with_retry(request)
        except HttpError as exc:
            msg = f"YouTube {endpoint}: {_describe(exc)}"
            if _http_status(exc) >= 500:
                raise YouTubeTransientError(msg) from None
            raise YouTubeFatalError(msg) from None
        except (OSError, httplib2.HttpLib2Error) as exc:
            raise YouTubeTransientError(self._redact(f"YouTube {endpoint}: {type(exc).__name__}: {exc}")) from None

    def get_channel(self, channel_id: str) -> dict[str, Any]:
        response = self._call(
            "channels.list",
            self._yt.channels().list(part=config.CHANNEL_PARTS, id=channel_id),
        )
        fetched_at = _now()
        items = response.get("items", [])
        if not items:
            raise YouTubeFatalError(f"YouTube channels.list: channel {channel_id} not found")
        return parse_channel(items[0], fetched_at)

    def get_playlist_video_ids(self, playlist_id: str) -> list[str]:
        """Every video ID in the playlist (all pages), newest first, deduplicated."""
        video_ids: list[str] = []
        seen: set[str] = set()
        page_token = None
        while True:
            response = self._call(
                "playlistItems.list",
                self._yt.playlistItems().list(
                    part="contentDetails",
                    playlistId=playlist_id,
                    maxResults=config.API_BATCH_SIZE,
                    pageToken=page_token,
                    fields="nextPageToken,items/contentDetails/videoId",
                ),
            )
            for item in response.get("items", []):
                video_id = item.get("contentDetails", {}).get("videoId")
                if video_id and video_id not in seen:
                    seen.add(video_id)
                    video_ids.append(video_id)
            page_token = response.get("nextPageToken")
            if not page_token:
                return video_ids

    def get_videos(self, video_ids: list[str]) -> VideoFetchResult:
        """Stats + metadata in batches of 50.

        A batch that still fails after retries is recorded and skipped, so the other
        batches are kept (run becomes partial). Fatal errors (4xx) stop everything.
        """
        result = VideoFetchResult()
        size = config.API_BATCH_SIZE
        for start in range(0, len(video_ids), size):
            batch = video_ids[start : start + size]
            try:
                response = self._call(
                    "videos.list",
                    self._yt.videos().list(part=config.VIDEO_PARTS, id=",".join(batch)),
                )
            except YouTubeTransientError as exc:
                log.error("%s. Skipping batch of %d videos", exc, len(batch))
                result.failed_ids.extend(batch)
                result.errors.append(str(exc))
                continue
            fetched_at = _now()
            returned = set()
            for item in response.get("items", []):
                result.videos.append(parse_video(item, fetched_at))
                returned.add(item.get("id"))
            result.missing_ids.extend(v for v in batch if v not in returned)
        if result.missing_ids:
            log.info("%d videos not returned by the API (deleted or private)", len(result.missing_ids))
        return result


def make_client() -> YouTubeClient:
    return YouTubeClient(config.require_env("YOUTUBE_API_KEY"))


# --- Local smoke test ------------------------------------------------------------

def _smoke_test() -> None:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # Korean titles on a cp1252 Windows console
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    client = make_client()

    channel = client.get_channel(config.CHANNEL_ID)
    print("\n=== Channel")
    for key, value in channel.items():
        print(f"  {key:20}: {value}")

    ids = client.get_playlist_video_ids(channel["uploads_playlist_id"])
    print(f"\n=== Uploads playlist: {len(ids)} video IDs (channel reports video_count={channel['video_count']})")

    result = client.get_videos(ids)
    videos = result.videos
    print(f"\n=== videos.list: {len(videos)} returned, {len(result.failed_ids)} failed, "
          f"{len(result.missing_ids)} missing")
    print(f"  like_count NULL       : {sum(v['like_count'] is None for v in videos)}")
    print(f"  comment_count NULL    : {sum(v['comment_count'] is None for v in videos)}")
    print(f"  duration_sec NULL     : {sum(v['duration_sec'] is None for v in videos)}")
    print(f"  has actual_start_time : {sum(v['actual_start_time'] is not None for v in videos)}")
    print(f"  live_broadcast_content: {dict(Counter(v['live_broadcast_content'] for v in videos))}")

    print("\n=== 5 newest videos")
    for v in sorted(videos, key=lambda v: v["published_at"] or datetime.min.replace(tzinfo=timezone.utc),
                    reverse=True)[:5]:
        print(f"  {v['video_id']}  {v['published_at']}  {str(v['duration_sec']):>6}s  "
              f"views={v['view_count']}  {(v['title'] or '')[:60]}")

    print(f"\nQuota units used: {client.quota_units_used}")


if __name__ == "__main__":
    _smoke_test()
