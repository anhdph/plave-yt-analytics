"""Re-run the classification rules (SPEC 7) on dim_video. No API calls, only category_id changes.

    python src/reclassify.py --dry-run    # show what would change
    python src/reclassify.py              # apply

Run sql/04_category_v2.sql first: every category_id in config.py must exist in dim_category.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

import config
from load import Database
from transform import classify

log = logging.getLogger(__name__)


def find_changes(videos: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Videos whose stored category_id differs from what the current rules give."""
    changes = []
    for v in videos:
        new_id = classify(v["title"], v["duration_sec"], v["actual_start_time"], v["live_broadcast_content"])
        if new_id != v["category_id"]:
            changes.append({"video_id": v["video_id"], "old_id": v["category_id"], "new_id": new_id})
    return changes


def missing_categories(existing_ids: Iterable[int]) -> list[int]:
    """Category IDs used by the rules that dim_category does not have."""
    return sorted(set(config.CATEGORY_NAMES) - set(existing_ids))


def reclassify(db: Database, dry_run: bool) -> int:
    """Return the process exit code."""
    def read(conn: Connection) -> tuple[list[int], list[dict[str, Any]]]:
        ids = list(conn.execute(text("SELECT category_id FROM plave.dim_category")).scalars())
        videos = conn.execute(text("""
            SELECT video_id, title, duration_sec, actual_start_time, live_broadcast_content, category_id
            FROM plave.dim_video
        """)).mappings()
        return ids, [dict(v) for v in videos]

    category_ids, videos = db.run(read)
    missing = missing_categories(category_ids)
    changes = find_changes(videos)

    moves = Counter((c["old_id"], c["new_id"]) for c in changes)
    print(f"{len(videos)} videos, {len(changes)} to reclassify")
    for (old_id, new_id), n in moves.most_common():
        print(f"  {n:>5}  {config.CATEGORY_NAMES.get(old_id, old_id)} -> {config.CATEGORY_NAMES[new_id]}")

    if missing:
        log.error("dim_category has no category_id %s: run sql/04_category_v2.sql first", missing)
        return 1
    if dry_run:
        print("Dry run: nothing written")
        return 0
    if changes:
        def write(conn: Connection) -> None:
            # Skips rows the pipeline changed meanwhile; updated_at = metadata last changed (as in load.py)
            conn.execute(text("""
                UPDATE plave.dim_video SET category_id = :new_id, updated_at = now()
                WHERE video_id = :video_id AND category_id = :old_id
            """), changes)
        db.run(write)
    print(f"Updated {len(changes)} videos")
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Re-run the classification rules on dim_video")
    parser.add_argument("--dry-run", action="store_true", help="show the changes without writing")
    args = parser.parse_args()
    return reclassify(Database(), args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
