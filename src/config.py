"""Configuration: env vars, constants and classification rules (SPEC.md).

Secrets come from environment variables (GitHub Secrets in CI, .env locally).
Never print or log their values.
"""

import os
import re
from datetime import timedelta, timezone

from dotenv import load_dotenv

load_dotenv()

# --- Environment ---------------------------------------------------------------
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
# Public channel ID, not a secret: env var only overrides the default.
CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID") or "UCPZIPuQPrfrUG9Xe_okEmQA"


def require_env(name: str) -> str:
    """Return the env var's value or stop with a message that names it (never its value)."""
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Environment variable {name} is not set (check .env or GitHub Secrets).")
    return value


# --- YouTube Data API v3 (SPEC section 4) --------------------------------------
API_BATCH_SIZE = 50  # max IDs per videos.list call / items per playlistItems page
API_TIMEOUT_SEC = 30
CHANNEL_PARTS = "snippet,statistics,contentDetails"
VIDEO_PARTS = "snippet,statistics,contentDetails,liveStreamingDetails"

# --- Retry (SPEC section 9) -----------------------------------------------------
# Network errors, timeouts, HTTP 5xx: retry up to 5 times -> 6 attempts in total.
API_MAX_ATTEMPTS = 6
API_RETRY_INITIAL_WAIT_SEC = 2  # 2s, 4s, 8s, ... plus jitter
API_RETRY_MAX_WAIT_SEC = 60

# --- Database (SPEC sections 9, 10) -------------------------------------------------
DB_SCHEMA = "plave"
DB_CONNECT_TIMEOUT_SEC = 15
# Connection errors: retry up to 3 times -> 4 attempts (covers a paused database waking up).
DB_MAX_ATTEMPTS = 4
DB_RETRY_INITIAL_WAIT_SEC = 2
DB_RETRY_MAX_WAIT_SEC = 30

# --- Run log (SPEC sections 9, 10, 12) ---------------------------------------------
RUN_STATUSES = ("running", "success", "partial", "failed")
RUN_TYPES = ("hourly", "full")
ERROR_MESSAGE_MAX_LEN = 4000
# A 'running' row older than this was killed (GitHub job timeout is 15 min): close it as failed.
STALE_RUN_AFTER_MIN = 60

# --- Time (SPEC section 6) -------------------------------------------------------
# Asia/Ho_Chi_Minh is a fixed UTC+7 with no DST, so a fixed offset is exact and needs no tz database.
LOCAL_TZ = timezone(timedelta(hours=7), "Asia/Ho_Chi_Minh")
RECENT_WINDOW_DAYS = 30  # recent tier: published within 30 days of snapshot_ts (SPEC section 5)

# --- Validation (SPEC section 8) -------------------------------------------------
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

# --- Classification (SPEC section 7) ---------------------------------------------
# IDs must match sql/02_seed_dim_date_category.sql.
CATEGORY_MUSIC_VIDEO = 1
CATEGORY_LIVE = 2
CATEGORY_SHORTS = 3
CATEGORY_PERFORMANCE = 4
CATEGORY_OTHER = 5

MV_TITLE_MARKER = "m/v"        # case-insensitive
MV_MIN_DURATION_SEC = 180      # MV needs duration strictly greater than this
SHORTS_TITLE_MARKER = "#"
PERFORMANCE_KEYWORDS = ("performance", "stage", "live clip")  # case-insensitive
# Side content about an MV/stage (behind-the-scenes, reactions) is never MV or Performance,
# e.g. "[BEHIND] 'Star Trace' M/V (Performance Ver.) 비하인드" -> Other. Case-insensitive.
EXCLUDE_KEYWORDS = ("behind", "reaction", "비하인드", "리액션")


def _is_excluded(title: str) -> bool:
    lowered = title.lower()
    return any(keyword in lowered for keyword in EXCLUDE_KEYWORDS)


def _is_music_video(title: str, duration_sec: int | None, actual_start_time) -> bool:
    # Checked before Live: Premieres also have actualStartTime.
    return (
        MV_TITLE_MARKER in title.lower()
        and duration_sec is not None
        and duration_sec > MV_MIN_DURATION_SEC
        and not _is_excluded(title)
    )


def _is_live(title: str, duration_sec: int | None, actual_start_time) -> bool:
    return actual_start_time is not None


def _is_shorts(title: str, duration_sec: int | None, actual_start_time) -> bool:
    return SHORTS_TITLE_MARKER in title


def _is_performance(title: str, duration_sec: int | None, actual_start_time) -> bool:
    lowered = title.lower()
    return any(keyword in lowered for keyword in PERFORMANCE_KEYWORDS) and not _is_excluded(title)


# Evaluated in this order; the first rule that matches wins. No match -> CATEGORY_FALLBACK.
CATEGORY_RULES = (
    (CATEGORY_MUSIC_VIDEO, _is_music_video),
    (CATEGORY_LIVE, _is_live),
    (CATEGORY_SHORTS, _is_shorts),
    (CATEGORY_PERFORMANCE, _is_performance),
)
CATEGORY_FALLBACK = CATEGORY_OTHER
