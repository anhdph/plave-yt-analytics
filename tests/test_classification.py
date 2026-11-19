"""Classification rules (SPEC section 7). Cases listed in the spec are all covered."""

from datetime import datetime, timezone

import pytest

import config
from transform import classify

MV = config.CATEGORY_MUSIC_VIDEO
LIVE = config.CATEGORY_LIVE
SHORTS = config.CATEGORY_SHORTS
PERF = config.CATEGORY_PERFORMANCE
OTHER = config.CATEGORY_OTHER

STARTED = datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "title, duration, live_start, expected",
    [
        # Normal case of each category (real titles from the channel)
        ("PLAVE 'Flame (feat. milet)' M/V", 193, None, MV),
        ("우리가 돌아왔다 플레이브 금의환향 라이브", 8310, STARTED, LIVE),
        ("Neon and beyond 💫 #PLAVE #플레이브 #shorts", 26, None, SHORTS),
        ("(SUB) PLAVE(플레이브) 'WEIBO ACG WORLD' Stage Video", 1406, None, PERF),
        ("2026 PLAVE World Tour [KEEP IT MANIC] in Incheon Highlight", 173, None, OTHER),
        # Premiere MV: M/V + actualStartTime -> MV (MV checked before Live)
        ("PLAVE 'Star Trace' M/V", 184, STARTED, MV),
        # M/V with duration exactly 180s -> not MV
        ("PLAVE 'BBUU!' M/V", 180, None, OTHER),
        ("PLAVE 'BBUU!' M/V", 181, None, MV),
        # M/V teaser (short) -> not MV
        ("PLAVE 'Dash' M/V Teaser", 21, None, OTHER),
        # Short M/V teaser that is a Premiere -> Live
        ("PLAVE 'Dash' M/V Teaser", 21, STARTED, LIVE),
        # Live title containing '#' -> Live (Live checked before Shorts)
        ("플레이브 라이브 | #플레이브 PLAVE", 8310, STARTED, LIVE),
        # NULL duration -> cannot be MV
        ("PLAVE 'Dash' M/V", None, None, OTHER),
        ("PLAVE 'Dash' M/V", None, STARTED, LIVE),
        # Uppercase / lowercase keywords
        ("PLAVE 'Dash' m/v", 187, None, MV),
        ("PLAVE 'Dash' M/V (PERFORMANCE Ver.) Teaser", 25, None, PERF),
        ("PLAVE 'Dash' performance ver.", 187, None, PERF),
        ("PLAVE STAGE VIDEO", 300, None, PERF),
        ("PLAVE Live Clip", 200, None, PERF),
        ("PLAVE LIVE CLIP", 200, None, PERF),
        # Excluded side content (behind / reaction) is never MV or Performance (real titles)
        ("(SUB) [BEHIND] 'Star Trace' M/V (Performance Ver.) 비하인드", 841, None, OTHER),
        ("(SUB) PLAVE(플레이브) 'Born Savage' M/V Reaction", 475, None, OTHER),
        ("PLAVE 'Dash' M/V behind", 300, None, OTHER),
        ("PLAVE 'Dash' M/V REACTION", 300, None, OTHER),
        ("PLAVE 'Dash' M/V 비하인드", 300, None, OTHER),
        ("PLAVE 'Dash' M/V 리액션", 300, None, OTHER),
        ("PLAVE Stage Behind", 600, None, OTHER),
        ("PLAVE Performance Reaction", 600, None, OTHER),
        # Exclusion does not affect Live or Shorts
        ("PLAVE 'Dash' M/V Reaction LIVE", 3600, STARTED, LIVE),
        ("M/V behind #PLAVE", 30, None, SHORTS),
        # Shorts beats Performance
        ("Stage behind #PLAVE", 30, None, SHORTS),
        # NULL / empty title
        (None, None, None, OTHER),
        ("", 300, None, OTHER),
    ],
)
def test_classify(title, duration, live_start, expected):
    assert classify(title, duration, live_start) == expected


def test_rules_cover_every_category_once_in_priority_order():
    ids = [category_id for category_id, _ in config.CATEGORY_RULES] + [config.CATEGORY_FALLBACK]
    assert ids == [MV, LIVE, SHORTS, PERF, OTHER]
