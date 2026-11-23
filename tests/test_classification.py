"""Classification rules (SPEC section 7). Titles are real ones from the channel unless noted."""

import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import config
from transform import classify

MV = config.CATEGORY_OFFICIAL_MV
LIVE = config.CATEGORY_LIVE
GENERAL = config.CATEGORY_SHORTS_GENERAL
PERF = config.CATEGORY_PERFORMANCE
OTHER = config.CATEGORY_OTHER
COVER = config.CATEGORY_COVER
AUDIO = config.CATEGORY_OFFICIAL_AUDIO
RADIO = config.CATEGORY_PL_RADIO
LASSGO = config.CATEGORY_LASSGO
SENT = config.CATEGORY_PL_SENT
HIGHLIGHT = config.CATEGORY_HIGHLIGHT
BEHIND = config.CATEGORY_BEHIND
PROMO = config.CATEGORY_PROMO
CLIP = config.CATEGORY_SHORTS_CLIP
S_PROMO = config.CATEGORY_SHORTS_PROMO

STARTED = datetime(2026, 11, 17, 13, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "title, duration, started, expected",
    [
        # --- Official M/V: M/V, Special Video, Official Lyric Video ---
        ("PLAVE 'Flame (feat. milet)' M/V", 193, None, MV),
        ("PLAVE 'BBUU!' M/V", 179, None, MV),
        ("PLAVE 'Star Trace' M/V", 219, STARTED, MV),  # Premiere -> not Live
        ("PLAVE 'Dash' m/v", 187, None, MV),  # made-up: lowercase
        ("PLAVE '흥흥흥 (feat. SOLE)' Special Video", 188, None, MV),
        ("PLAVE(플레이브) '왜요 왜요 왜? (Why?)' | Official Lyric Video", 149, None, MV),
        ("PLAVE 'Dash' M/V", 120, None, OTHER),  # made-up: threshold is strict
        ("PLAVE 'Dash' M/V", 121, None, MV),  # made-up
        ("PLAVE 'Dash' M/V", None, None, OTHER),  # made-up: NULL duration
        # Not in the PLAVE OFFICIAL M/V playlist
        ("플레이브 (PLAVE) '여섯 번째 여름 (The 6th Summer)' Special Video", 207, STARTED, OTHER),
        ("PLAVE(플레이브) 2nd Anniversary Special Video | 데뷔 2주년 기념 코멘터리 💌", 1800, None, OTHER),
        ("PLAVE '흥흥흥 (feat. SOLE)' 180 VR Ver.", 192, None, OTHER),
        # --- Cover ---
        ("[COVER] 노아 - ベテルギウス (Betelgeuse) (원곡 : 優里 (Yuuri)) (Covered by Noah)｜#플레이브 #PLAVE", 188, None, COVER),
        ("[COVER] 은호 - FANXY CHILD (원곡 : ZICO) (Covered by Eunho)｜#플레이브 PLAVE", 69, None, COVER),
        ("[From. PLAVE] 은호 - UNDERSTAND (원곡 : keshi)｜#플레이브 #PLAVE", 148, None, COVER),
        ("[2024 PLAVE BIRTHDAY GIFT] 밤비 - Pretender (원곡 : Official髭男dism)", 233, None, COVER),
        ("[PLAVE BIRTHDAY GIFT] 은호 - BUT YOUR IDOL", 70, None, COVER),
        # Short cuts of a cover are Shorts
        ("[COVER] 노아 - ベテルギウス (Betelgeuse) (원곡 : 優里 (Yuuri)) (Covered by Noah)｜#플레이브 #PLAVE", 60, None, GENERAL),
        ("[COVER] PLAVE(플레이브) 'Golden' (원곡 : HUNTR/X, EJAE, AUDREY NUNA, REI AMI)", 48, None, GENERAL),
        # --- Official Audio ---
        ("PLAVE 'RIZZ' Official Audio", 165, None, AUDIO),
        # --- Performance: stage clips, M/V (Performance Ver.), dance practice ---
        ("(SUB) PLAVE(플레이브) 'WEIBO ACG WORLD' Stage Video", 1406, None, PERF),
        ("PLAVE 'Star Trace' M/V (Performance Ver.)", 184, None, PERF),
        ("PLAVE(플레이브) 'Dash' Dance Practice", 234, None, PERF),
        ("PLAVE (플레이브) '기다릴게 (Wait for you)' Vertical Dance", 236, None, PERF),
        ("노아 'Drowning' (원곡 : WOODZ) | PLAVE FAN CONCERT 'Hello, Asterum!' ENCORE", 283, None, PERF),
        ("PLAVE(플레이브) '안녕이란 말 대신' (원곡 : RAIN) | 2026 Weverse Con Festival", 120, None, PERF),
        ("PLAVE(플레이브) 'Island + Chroma Drift' l 2025 PLAVE Asia Tour [DASH: Quantum Leap] in Seoul", 584, None, PERF),
        ("PLAVE 'かくれんぼ (Hide and Seek)' | PLAVE JP 1st Single Showcase", 217, None, PERF),
        ("PLAVE(플레이브) Highlight Clip | PLAVE FAN CONCERT 'Hello, Asterum!' ENCORE - MUSIC AWARDS JAPAN 2025", 1006, STARTED, PERF),
        # --- Live / PL:RADIO ---
        ("우리가 돌아왔다!🛸 플레이브 금의환향🎊 라이브💙💜🩷❤️🖤 | #플레이브 PLAVE | PLAVE Live Full Ver.", 8310, STARTED, LIVE),
        ("미니 2집 녹음 비하인드🎤&타이틀곡 가이드 대공개💜🖤 | #플레이브 PLAVE | PLAVE Noah & Hamin Live Full Ver.", 7086, STARTED, LIVE),
        ("문제가📖 남느냐! 아스테룸 골든벨🔔 성휘예고편🏫 | #플레이브 PLAVE | PLAVE Live Full Ver.", 7560, STARTED, LIVE),
        ("연습생 예준&노아의 홍대 버스킹 라이브 풀버전｜#플레이브 PLAVE｜Yejun & Noah Street Performance Live Full Ver.", 1900, None, LIVE),
        ("250728 PL:RADIO with DJ PLAVE 📻", 7605, STARTED, RADIO),
        # --- LASSGO / PL:SENT ---
        ("(SUB) [LASSGO PLAVE] EP.01 | 외계인 최초 자컨 👽🎥 라쓰고 플레이브 출범식 #1", 1629, None, LASSGO),
        ("(SUB) [LASSGO PLAVE] EP.35 | 음악이란 무엇인가? 👑🎶 아스테룸 뮤직 심포지엄 ⛲ #2", 2986, STARTED, LASSGO),
        ("(SUB) [플레젠트] 같이 야근 할래?｜WORK WITH ME 🖥️｜PL:SENT 🎁", 1705, None, SENT),
        ("(SUB) [플레젠트] 2026 아스테룸 동계 외계체전 👽❄️ VCR & 비하인드｜PL:SENT 🎁", 659, None, SENT),
        # --- Highlight, including songs from comeback lives ---
        ("[하이라이트] 'Star Trace' PERFORMANCE LIVE🎤｜PLAVE Highlight Clip", 180, None, HIGHLIGHT),
        ("[하이라이트] 'HYPERDRIVE' COMEBACK 전곡 LIVE🎤｜PLAVE Highlight Clip", 896, None, HIGHLIGHT),
        ("(SUB) [하이라이트] 'Dash'로 컴백 ⚡ MV 리액션 & 앨범 비하인드 👀👏｜PLAVE Highlight Clip", 1004, None, HIGHLIGHT),
        ("(SUB) [하이라이트] 고백송 작곡 방송 🎤🎶 #1｜PLAVE Highlight Clip", 170, None, HIGHLIGHT),  # "#1" is no hashtag
        ("2026 PLAVE World Tour [KEEP IT MANIC] in Incheon Highlight", 173, None, HIGHLIGHT),
        ("(SUB) PLAVE JP 1st Single Showcase Highlight", 1080, None, HIGHLIGHT),
        # --- Behind ---
        ("(SUB) [BEHIND] 'Star Trace' M/V (Performance Ver.) 비하인드", 841, None, BEHIND),
        ("(SUB) 'かくれんぼ (Hide and Seek)' Recording Behind 🎙🎧", 662, None, BEHIND),
        ("(SUB) [SPECIAL] LASSGO PLAVE 미방분 모음집 📂✨｜PLAVE Special Clip", 1051, None, BEHIND),
        # --- Promo ---
        ("PLAVE 'Star Trace' M/V Teaser", 30, None, PROMO),
        ("PLAVE 'Dash' M/V Teaser", 21, STARTED, PROMO),  # made-up: teaser Premiere
        ("PLAVE 'Dash' M/V (Performance Ver.) Teaser", 25, None, PROMO),
        ("PLAVE (플레이브) 'ASTERUM' Highlight Medley", 71, STARTED, PROMO),
        ("2026 PLAVE World Tour [KEEP IT MANIC] in Macao ID CLIP", 96, None, PROMO),
        # --- Other ---
        ("(SUB) PLAVE(플레이브) 'Born Savage' M/V Reaction", 475, None, OTHER),
        ("PLAVE(플레이브) 'Star Trace' 응원법 가이드 (Fanchants Guide)", 216, None, OTHER),
        ("[PLAVE BIRTHDAY GIFT] HAMIN's Playlist 🎶🖤", 573, None, OTHER),
        ("(SUB) PLAVE(플레이브)가 전하는 2026 추석 인사", 62, None, OTHER),
        ("PLAVE FAN CONCERT 'Hello, Asterum!' ENCORE 관람 가이드", 60, None, OTHER),
        # --- Shorts ---
        ("Neon and beyond 💫 #PLAVE #플레이브 #shorts", 26, None, GENERAL),
        ("NEON #PLAVE", 180, None, GENERAL),  # made-up: 180s included
        ("NEON #PLAVE", 181, None, OTHER),  # made-up
        ("NEON #PLAVE", None, None, OTHER),  # made-up: NULL duration
        ("[PL:RADIO] 댄라즈가 알려주는 👨‍🏫 춤 잘 추는 방법 🕺✨ #PL_RADIO #PLAVE #플레이브 #shorts", 56, None, CLIP),
        ("PLAVE 멤버별 콘서트 전날 루틴 🎤🍀 #PLAVE_LIVE #PLAVE #플레이브 #shorts", 59, None, CLIP),
        ("[LASSGO PLAVE] 파자마파티 #1 예고 🧸🎉 #LASSGO_PLAVE #PLAVE #플레이브 #shorts", 41, None, CLIP),
        ("PLAVE 'Star Trace' M/V (Performance Ver.) #PLAVE #플레이브", 51, None, S_PROMO),
        ("TAIPEI ARE YOU READY? 🔥 #PLAVE #BAMBY #EUNHO", 21, None, S_PROMO),
        # The "#플레이브 PLAVE" signature is not a Shorts hashtag
        ("(ENG SUB) 노아의 15문 15답 숏 인터뷰🎙️｜#플레이브 PLAVE｜Noah's 15 Q&A", 48, None, OTHER),
        # --- NULL / empty title ---
        (None, None, None, OTHER),
        ("", 300, None, OTHER),
    ],
)
def test_classify(title, duration, started, expected):
    assert classify(title, duration, started) == expected


@pytest.mark.parametrize("content", ["upcoming", "live"])
@pytest.mark.parametrize("duration", [0, None])
def test_stream_that_has_not_finished_is_live_not_shorts(content, duration):
    title = "플레이브 라이브💙 | #플레이브 #PLAVE #shorts"
    assert classify(title, duration, None, content) == LIVE


def test_upcoming_premiere_is_not_live():
    assert classify("PLAVE 'Star Trace' M/V", 219, None, "upcoming") == MV


def test_rules_cover_every_category():
    ids = {category_id for category_id, _ in config.CATEGORY_RULES} | {config.CATEGORY_FALLBACK}
    assert ids == set(config.CATEGORY_NAMES) == set(range(1, 16))


@pytest.mark.parametrize("filename", ["02_seed_dim_date_category.sql", "04_category_v2.sql"])
def test_sql_matches_config(filename):
    sql = (Path(__file__).parent.parent / "sql" / filename).read_text(encoding="utf-8")
    rows = [(int(i), name, int(p)) for i, name, p in re.findall(r"\((\d+),\s*'([^']+)',\s*(\d+)\)", sql)]
    assert {i: name for i, name, _ in rows} == config.CATEGORY_NAMES
    # priority = evaluation order of the first rule of each category, Other (fallback) last
    order = [i for i in dict.fromkeys(i for i, _ in config.CATEGORY_RULES) if i != OTHER] + [OTHER]
    assert [i for i, _, _ in sorted(rows, key=lambda r: r[2])] == order
