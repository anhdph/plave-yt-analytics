"""Configuration: env vars, constants and classification rules (SPEC.md).

Secrets come from environment variables (GitHub Secrets in CI, .env locally).
Never print or log their values.
"""

import os
import re
from dataclasses import dataclass
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
# Full run (old tier too) when no successful full run yet, or the latest one is >= this old (SPEC section 5).
FULL_RUN_INTERVAL_HOURS = 12

# --- Validation (SPEC section 8) -------------------------------------------------
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")

# --- Classification (SPEC section 7) ---------------------------------------------
# IDs must match sql/02_seed_dim_date_category.sql. IDs 1-5 predate the 15-category scheme and
# keep their meaning; evaluation order is CATEGORY_RULES, not the ID.
CATEGORY_OFFICIAL_MV = 1
CATEGORY_LIVE = 2
CATEGORY_SHORTS_GENERAL = 3
CATEGORY_PERFORMANCE = 4
CATEGORY_OTHER = 5
CATEGORY_COVER = 6
CATEGORY_OFFICIAL_AUDIO = 7
CATEGORY_PL_RADIO = 8
CATEGORY_LASSGO = 9
CATEGORY_PL_SENT = 10
CATEGORY_HIGHLIGHT = 11
CATEGORY_BEHIND = 12
CATEGORY_PROMO = 13
CATEGORY_SHORTS_CLIP = 14
CATEGORY_SHORTS_PROMO = 15

# category_id -> dim_category.category_name
CATEGORY_NAMES = {
    CATEGORY_OFFICIAL_MV: "Official M/V",
    CATEGORY_LIVE: "Live",
    CATEGORY_SHORTS_GENERAL: "Shorts - General",
    CATEGORY_PERFORMANCE: "Performance",
    CATEGORY_OTHER: "Other",
    CATEGORY_COVER: "Cover",
    CATEGORY_OFFICIAL_AUDIO: "Official Audio",
    CATEGORY_PL_RADIO: "PL:RADIO",
    CATEGORY_LASSGO: "LASSGO PLAVE",
    CATEGORY_PL_SENT: "PL:SENT",
    CATEGORY_HIGHLIGHT: "Highlight",
    CATEGORY_BEHIND: "Behind",
    CATEGORY_PROMO: "Promo",
    CATEGORY_SHORTS_CLIP: "Shorts - Clip",
    CATEGORY_SHORTS_PROMO: "Shorts - Promo",
}

# All keywords are lowercase and matched against the lowercased title.

# Shorts: at most 3 minutes, never streamed, with a real hashtag ("#1" is an episode number).
SHORTS_MAX_DURATION_SEC = 180
HASHTAG_RE = re.compile(r"#(?!\d+\b)\S")
# "｜#플레이브 PLAVE" is the channel's signature on long videos, not a Shorts hashtag.
CHANNEL_SIGNATURE_RE = re.compile(r"[|｜]\s*#(?:플레이브|plave) (?!#)")
# "｜#플레이브 #PLAVE" is used by long covers and by a few Shorts: a Short only up to 60s.
CHANNEL_HASHTAG_SIGNATURE_RE = re.compile(r"[|｜]\s*#(?:플레이브|plave) ?#")
# Short cuts of a cover are Shorts even without a hashtag.
SHORT_CUT_MAX_DURATION_SEC = 60
SHORTS_CLIP_KEYWORDS = (
    "#plave_live", "#pl_radio", "#lassgo_plave", "#atot_japan", "[lassgo plave]", "[pl:radio]", "[a to t",
)
SHORTS_PROMO_KEYWORDS = (
    "m/v", "album", "special video", "lyric video", "season's greetings", "asia tour", "world tour",
    "are you ready", "concert loading", "sketchbook confession", "발매기원", "recording behind", "behind 🎇",
    "[behind]", "teaser", "highlight medley", "concept film", "mood film", "logo motion", "scheduler",
    "coming soon", "prologue", "epilogue", "track spoiler",
)

# Premieres also have actualStartTime: a streamed video with one of these is not a Live.
PREMIERE_KEYWORDS = (
    "m/v", "special video", "lyric video", "official audio", "highlight medley", "highlight clip", "teaser",
    "[lassgo plave]", "[플레젠트]", "[a to t", "[cover]", "[from. plave]", "birthday gift]",
)
LIVE_KEYWORDS = ("라이브 풀버전",)  # full live re-uploaded as a normal video
PL_RADIO_KEYWORDS = ("pl:radio with dj",)
HIGHLIGHT_CLIP_MARKER = "[하이라이트]"
HIGHLIGHT_KEYWORDS = ("하이라이트", "highlight")
PROMO_KEYWORDS = (
    "teaser", "예고편", "highlight medley", "concept film", "mood film", "logo motion", "scheduler",
    "coming soon", "id clip", "prologue", "epilogue", "track spoiler", "title sequence",
)
LASSGO_KEYWORDS = ("[lassgo plave]",)
PL_SENT_KEYWORDS = ("[플레젠트]", "pl:sent")
# "[SPECIAL]" side content is in the PLAVE Behind playlist.
BEHIND_KEYWORDS = ("[behind]", "recording behind", "[special]")
REACTION_KEYWORDS = ("reaction", "리액션")
GUIDE_KEYWORDS = ("guide", "가이드")
# Own-channel videos of the PLAVE STAGE CLIP playlist + M/V (Performance Ver.) + dance practice.
PERFORMANCE_KEYWORDS = (
    "performance ver", "dance practice", "vertical dance",
    "stage video", "| plave fan concert", "weverse con", "mama awards", "girlsaward", "buzz rhythm",
    "| plave jp 1st single showcase",
)
PERFORMANCE_TOUR_RE = re.compile(r"' [|｜l] 20\d\d plave (?:asia|world) tour")
# Follows the PLAVE OFFICIAL M/V playlist: M/V, Special Video, Official Lyric Video.
MV_MIN_DURATION_SEC = 120  # strictly greater than this; the shortest official video is 149s
OFFICIAL_MV_RE = re.compile(r"m/v|special video$|official lyric video$")
# Not in the playlist (the song's M/V is), so not Official M/V.
OFFICIAL_MV_EXCLUDED = ("'여섯 번째 여름 (the 6th summer)' special video",)
OFFICIAL_AUDIO_KEYWORDS = ("official audio",)
COVER_KEYWORDS = ("[cover]", "[from. plave]", "birthday gift]")
PLAYLIST_KEYWORDS = ("playlist", "플레이리스트")  # member playlists in PLAVE BIRTHDAY GIFT are not covers


@dataclass(frozen=True)
class VideoInfo:
    """Classification input. Every field is stored in dim_video, so reclassify.py needs no API call."""

    title: str  # lowercased
    duration_sec: int | None
    is_streamed: bool  # Live or Premiere: started, live now or upcoming

    @classmethod
    def build(cls, title, duration_sec, actual_start_time, live_broadcast_content=None) -> "VideoInfo":
        streamed = actual_start_time is not None or live_broadcast_content in ("live", "upcoming")
        return cls((title or "").strip().lower(), duration_sec, streamed)

    def has(self, *keywords: str) -> bool:
        return any(keyword in self.title for keyword in keywords)


def _is_shorts(v: VideoInfo) -> bool:
    if v.is_streamed or not v.duration_sec or v.duration_sec > SHORTS_MAX_DURATION_SEC:
        return False
    if v.has("[cover]") and v.duration_sec <= SHORT_CUT_MAX_DURATION_SEC:
        return True
    if not HASHTAG_RE.search(v.title) or CHANNEL_SIGNATURE_RE.search(v.title + " "):
        return False
    return not (CHANNEL_HASHTAG_SIGNATURE_RE.search(v.title) and v.duration_sec > SHORT_CUT_MAX_DURATION_SEC)


def _is_shorts_clip(v: VideoInfo) -> bool:
    return _is_shorts(v) and v.has(*SHORTS_CLIP_KEYWORDS)


def _is_shorts_promo(v: VideoInfo) -> bool:
    return _is_shorts(v) and v.has(*SHORTS_PROMO_KEYWORDS)


def _is_pl_radio(v: VideoInfo) -> bool:
    return v.has(*PL_RADIO_KEYWORDS)


def _is_live(v: VideoInfo) -> bool:
    return (v.is_streamed and not v.has(*PREMIERE_KEYWORDS)) or v.has(*LIVE_KEYWORDS)


def _is_highlight_clip(v: VideoInfo) -> bool:
    # Before every other content rule: live recaps mention reactions, behind stories, guides...
    return v.has(HIGHLIGHT_CLIP_MARKER)


def _is_promo(v: VideoInfo) -> bool:
    return v.has(*PROMO_KEYWORDS)


def _is_behind(v: VideoInfo) -> bool:
    return v.has(*BEHIND_KEYWORDS)


def _is_lassgo(v: VideoInfo) -> bool:
    return v.has(*LASSGO_KEYWORDS)


def _is_pl_sent(v: VideoInfo) -> bool:
    return v.has(*PL_SENT_KEYWORDS)


def _is_other(v: VideoInfo) -> bool:
    # Explicitly Other: would otherwise match Official M/V, Performance or Cover.
    return v.has(*REACTION_KEYWORDS, *GUIDE_KEYWORDS, *PLAYLIST_KEYWORDS)


def _is_performance(v: VideoInfo) -> bool:
    return v.has(*PERFORMANCE_KEYWORDS) or bool(PERFORMANCE_TOUR_RE.search(v.title))


def _is_official_mv(v: VideoInfo) -> bool:
    return (
        bool(OFFICIAL_MV_RE.search(v.title))
        and v.duration_sec is not None
        and v.duration_sec > MV_MIN_DURATION_SEC
        and not v.has(*OFFICIAL_MV_EXCLUDED)
    )


def _is_official_audio(v: VideoInfo) -> bool:
    return v.has(*OFFICIAL_AUDIO_KEYWORDS)


def _is_highlight(v: VideoInfo) -> bool:
    return v.has(*HIGHLIGHT_KEYWORDS)


def _is_cover(v: VideoInfo) -> bool:
    return v.has(*COVER_KEYWORDS)


# Evaluated in this order; the first rule that matches wins. No match -> CATEGORY_FALLBACK.
CATEGORY_RULES = (
    (CATEGORY_SHORTS_CLIP, _is_shorts_clip),
    (CATEGORY_SHORTS_PROMO, _is_shorts_promo),
    (CATEGORY_SHORTS_GENERAL, _is_shorts),
    (CATEGORY_PL_RADIO, _is_pl_radio),
    (CATEGORY_LIVE, _is_live),
    (CATEGORY_HIGHLIGHT, _is_highlight_clip),
    (CATEGORY_PROMO, _is_promo),
    (CATEGORY_BEHIND, _is_behind),
    (CATEGORY_LASSGO, _is_lassgo),
    (CATEGORY_PL_SENT, _is_pl_sent),
    (CATEGORY_OTHER, _is_other),
    (CATEGORY_PERFORMANCE, _is_performance),
    (CATEGORY_OFFICIAL_MV, _is_official_mv),
    (CATEGORY_OFFICIAL_AUDIO, _is_official_audio),
    (CATEGORY_HIGHLIGHT, _is_highlight),
    (CATEGORY_COVER, _is_cover),
)
CATEGORY_FALLBACK = CATEGORY_OTHER
