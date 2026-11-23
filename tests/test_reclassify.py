"""reclassify.py: change detection and guards (no real database)."""

import config
import reclassify


def _video(video_id, title, category_id, duration=200, started=None, lbc="none"):
    return {
        "video_id": video_id, "title": title, "duration_sec": duration, "actual_start_time": started,
        "live_broadcast_content": lbc, "category_id": category_id,
    }


class FakeConn:
    def __init__(self, category_ids, videos):
        self.category_ids, self.videos, self.writes = category_ids, videos, []

    def execute(self, stmt, params=None):
        sql = str(stmt)
        if "UPDATE" in sql:
            self.writes.extend(params)
            return None
        rows = self.category_ids if "dim_category" in sql else self.videos
        return type("R", (), {"scalars": lambda s: iter(rows), "mappings": lambda s: iter(rows)})()


class FakeDb:
    def __init__(self, category_ids, videos):
        self.conn = FakeConn(category_ids, videos)

    def run(self, fn):
        return fn(self.conn)


COVER = _video("a", "[From. PLAVE] 예준 - Summer (원곡 : Paul Blanco (Feat. BE'O))", config.CATEGORY_OTHER)
MV = _video("b", "PLAVE 'Dash' M/V", config.CATEGORY_OFFICIAL_MV)
ALL_IDS = list(config.CATEGORY_NAMES)


def test_find_changes_returns_only_videos_whose_category_differs():
    assert reclassify.find_changes([COVER, MV]) == [
        {"video_id": "a", "old_id": config.CATEGORY_OTHER, "new_id": config.CATEGORY_COVER}
    ]


def test_apply_writes_changes():
    db = FakeDb(ALL_IDS, [COVER, MV])
    assert reclassify.reclassify(db, dry_run=False) == 0
    assert [w["video_id"] for w in db.conn.writes] == ["a"]


def test_dry_run_writes_nothing():
    db = FakeDb(ALL_IDS, [COVER, MV])
    assert reclassify.reclassify(db, dry_run=True) == 0
    assert db.conn.writes == []


def test_stops_before_writing_when_the_migration_was_not_run():
    db = FakeDb([1, 2, 3, 4, 5], [COVER, MV])
    assert reclassify.reclassify(db, dry_run=False) == 1
    assert db.conn.writes == []
