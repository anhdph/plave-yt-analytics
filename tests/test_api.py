"""Export every video on a YouTube channel to data/channel_videos.csv.

Configure YOUTUBE_API_KEY and YOUTUBE_CHANNEL_ID in a .env file, then run:
    python youtube_api_test.py
"""

import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


load_dotenv()

API_KEY = os.getenv("YOUTUBE_API_KEY")
CHANNEL_ID = os.getenv("YOUTUBE_CHANNEL_ID")
BASE_URL = "https://www.googleapis.com/youtube/v3"
OUTPUT_PATH = Path("data/channel_videos.csv")
PAGE_SIZE = 50
FIELDS = [
    "id",
    "snippet.publishedAt",
    "snippet.channelId",
    "snippet.title",
    "snippet.thumbnails",
    "contentDetails.duration",
    "statistics.viewCount",
    "statistics.likeCount",
    "statistics.commentCount",
    "liveStreamingDetails.actualStartTime",
    "liveStreamingDetails.actualEndTime",
    "liveStreamingDetails.concurrentViewers",
]


def youtube_get(endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
    """Call the YouTube Data API and include useful details for API errors."""
    response = requests.get(
        f"{BASE_URL}/{endpoint}",
        params={**params, "key": API_KEY},
        timeout=30,
    )
    if not response.ok:
        try:
            message = response.json()["error"]["message"]
        except (ValueError, KeyError):
            message = response.text[:500]
        raise RuntimeError(f"YouTube API error ({response.status_code}): {message}")
    return response.json()


def get_uploads_playlist(channel_id: str) -> tuple[str, str]:
    result = youtube_get(
        "channels",
        {"part": "snippet,contentDetails", "id": channel_id},
    )
    items = result.get("items", [])
    if not items:
        raise ValueError(f"No channel found for YOUTUBE_CHANNEL_ID={channel_id!r}.")
    channel = items[0]
    return channel["contentDetails"]["relatedPlaylists"]["uploads"], channel["snippet"]["title"]


def get_all_video_ids(uploads_playlist_id: str) -> list[str]:
    """Follow every uploads-playlist page; YouTube returns at most 50 per page."""
    video_ids: list[str] = []
    page_token = None
    while True:
        params: dict[str, Any] = {
            "part": "contentDetails",
            "playlistId": uploads_playlist_id,
            "maxResults": PAGE_SIZE,
        }
        if page_token:
            params["pageToken"] = page_token
        result = youtube_get("playlistItems", params)
        video_ids.extend(
            item["contentDetails"]["videoId"]
            for item in result.get("items", [])
            if item.get("contentDetails", {}).get("videoId")
        )
        page_token = result.get("nextPageToken")
        print(f"Collected {len(video_ids)} video IDs...")
        if not page_token:
            return video_ids


def get_video_rows(video_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch requested fields in API batches of up to 50 video IDs."""
    rows = []
    parts = "snippet,contentDetails,statistics,liveStreamingDetails"
    for start in range(0, len(video_ids), PAGE_SIZE):
        batch = video_ids[start : start + PAGE_SIZE]
        result = youtube_get("videos", {"part": parts, "id": ",".join(batch)})
        for video in result.get("items", []):
            snippet = video.get("snippet", {})
            content = video.get("contentDetails", {})
            stats = video.get("statistics", {})
            live = video.get("liveStreamingDetails", {})
            rows.append(
                {
                    "id": video.get("id"),
                    "publishedAt": snippet.get("publishedAt"),
                    "channelId": snippet.get("channelId"),
                    "title": snippet.get("title"),
                    "thumbnails": snippet.get("thumbnails", {}).get("maxres", {}).get("url"),
                    "duration": content.get("duration"),
                    "viewCount": stats.get("viewCount"),
                    "likeCount": stats.get("likeCount"),
                    "commentCount": stats.get("commentCount"),
                    "actualStartTime": live.get("actualStartTime"),
                    "actualEndTime": live.get("actualEndTime"),
                    "concurrentViewers": live.get("concurrentViewers"),
                }
            )
        print(f"Fetched details for {min(start + PAGE_SIZE, len(video_ids))}/{len(video_ids)} videos...")
    return rows


def main() -> None:
    if not API_KEY or not CHANNEL_ID:
        raise ValueError("Set YOUTUBE_API_KEY and YOUTUBE_CHANNEL_ID in your .env file.")

    playlist_id, channel_title = get_uploads_playlist(CHANNEL_ID)
    video_ids = get_all_video_ids(playlist_id)
    rows = get_video_rows(video_ids)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Write CSV using the standard library so no dataframe dependency is needed.
    import csv

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Channel: {channel_title}")
    print(f"Videos found: {len(video_ids)}; detail records returned: {len(rows)}")
    print(f"CSV saved to: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
