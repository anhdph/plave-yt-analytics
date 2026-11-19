"""Configuration: env vars, constants and classification rules (SPEC.md).

Secrets come from environment variables (GitHub Secrets in CI, .env locally).
Never print or log their values.
"""

import os

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
