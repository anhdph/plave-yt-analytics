# PLAVE YouTube Analytics - End-to-End Data Pipeline & Dashboard

## 1. Objective
Build an automated pipeline that collects public data of the PLAVE YouTube channel via the YouTube Data API v3, stores it in PostgreSQL (dimensional model) as periodic snapshots, and visualizes it with Power BI Desktop.
This is a portfolio project: the pipeline must run automatically 24/7 (even when my computer is off), be free to operate, and accumulate historical data over time.

## 2. Tech stack
- Language: Python 3.11+
- Libraries: google-api-python-client, SQLAlchemy 2.x + psycopg 3 (`psycopg[binary]`), pandas, python-dotenv, tenacity (retry), pytest
- DB management tool: DBeaver
- Database: Supabase (PostgreSQL, free tier), SSL required
  - Free tier (checked Nov 2026): 500 MB database size, project paused after 1 week of inactivity, max 2 active projects.
  - Connect through the **Session pooler (port 5432)**, NOT the direct host. The direct host `db.<ref>.supabase.co` is IPv6-only on the free tier;
    GitHub Actions runners and many home networks are IPv4-only. Do not use port 6543 (transaction pooler).
  - Copy the exact Session pooler string from Supabase dashboard -> Connect.
- Orchestration: GitHub Actions (hourly cron). Do NOT use Airflow or Docker.
- BI: Power BI Desktop (Import mode, manual refresh), built-in PostgreSQL connector, via the same Session pooler host.
- Secrets: GitHub Secrets `YOUTUBE_API_KEY`, `DATABASE_URL`. Local development uses `.env` (in `.gitignore`, never committed).
  - `DATABASE_URL` format: `postgresql+psycopg://postgres.<project_ref>:<password>@<pooler-host>:5432/postgres?sslmode=require`

## 3. Repo structure
```
plave-yt-analytics/
├── .github/workflows/
│   ├── pipeline.yml       # hourly pipeline
│   └── keepalive.yml      # prevents GitHub from disabling the cron after 60 days
├── src/
│   ├── extract.py         # YouTube API calls
│   ├── transform.py       # validation + classification
│   ├── load.py            # upsert into PostgreSQL
│   ├── logger.py          # writes run_log
│   ├── config.py          # env vars, constants, classification rules
│   ├── reclassify.py      # re-run classification from DB, no API calls
│   └── main.py            # orchestrates the steps
├── sql/
│   ├── 01_ddl.sql
│   ├── 02_seed_dim_date_category.sql
│   ├── 03_roles.sql       # read-only role for Power BI
│   └── 04_category_v2.sql # migrate dim_category from 5 to 15 categories
├── tests/
│   └── test_classification.py
├── requirements.txt
├── .env.example
├── .gitignore
└── README.md
```

## 4. Data extraction (public data only)
- The YouTube Analytics API is not available (I do not own the channel): no watch time, CTR, or retention.
- Use: `channels.list`, `playlistItems.list` (uploads playlist: `UC...` -> `UU...`), `videos.list` (batch up to 50 IDs per call).
- AVOID `search.list` (100 quota units per call).
- Parts: `snippet`, `statistics`, `contentDetails`, `liveStreamingDetails`.
- Channel: subscriberCount (rounded by YouTube; NULL if hidden), viewCount, videoCount.
- Video: title, publishedAt, duration (ISO 8601 -> seconds), tags, categoryId, liveBroadcastContent, viewCount, likeCount, commentCount,
  liveStreamingDetails.actualStartTime / actualEndTime / concurrentViewers.
- NULL handling: if comments are disabled or likes are hidden, store NULL, not 0.
- `videos.list` is called with all parts, so every stats fetch also refreshes the video's metadata
  (title edits, and `publishedAt` changes after a Premiere goes live).

### Quota estimate (10,000 units/day free)
Each list call = 1 unit. With N videos on the channel and R recent videos:
- Hourly run: 1 (channel) + ceil(N/50) (playlist) + ceil((R+M)/50) (stats; M = Official M/V older than 30 days)
- Full run fetches all N videos: ceil(N/50) for stats instead.
- Example (Nov 2026) N = 1,837, R = 35, M = 17: 40 units/hour, 75 for a full run -> ~1,030 units/day. Quota is not a concern.

## 5. Collection frequency (logic lives in main.py)
Timezone of reference: **Asia/Ho_Chi_Minh (UTC+7)**. GitHub cron runs in UTC.

- **Every run (hourly):**
  1. Fetch channel stats.
  2. Scan the **entire** uploads playlist (all pages) to get the full list of video IDs; IDs not yet in `dim_video` are new videos.
  3. Fetch stats + metadata for the **recent tier**: videos with `published_at` within 30 days of `snapshot_ts`, plus all new videos.
  4. Also fetch every **Official M/V** whatever its age (`ALWAYS_FETCH_CATEGORIES` in config.py, using `dim_video.category_id`).
     `fact_video.tier` still depends on age only, so an old M/V has hourly snapshots with `tier = 'old'`.
- **Full run:** also fetch the rest of the **old tier** (all videos with `published_at` older than 30 days).
  - Rule: run the old tier if there is no successful full run in `run_log`, or if
    `snapshot_ts - (latest snapshot_ts of a successful full run) >= 12 hours`.
  - This self-heals when GitHub delays or skips a run (no dependency on hitting exactly 00:00/12:00).
  - The first ever run is automatically a full run (backfill).
- Videos move from the recent tier to the old tier automatically.
- Deleted/private videos simply disappear from the playlist/API response; no flag is stored.
  Their rows stay in the database and keep their last snapshot.

## 6. Snapshot time
- `snapshot_ts` (TIMESTAMPTZ): the run's start time **floored to the hour**. One value shared by every row of that run. Part of the fact PK -> re-running within the same hour overwrites instead of duplicating (idempotent).
- `snapshot_ts_local` (TIMESTAMP): `snapshot_ts` converted to Asia/Ho_Chi_Minh, computed in Python.
- `date_key` (INT, YYYYMMDD): derived from `snapshot_ts_local`; joins to `dim_date`.
- `fetched_at` (TIMESTAMPTZ): the actual time the API response was received.

## 7. Video classification (Transform step, after API calls)
Each video belongs to exactly ONE of 15 categories. Definitions follow the channel's own public playlists
(reviewed on all 1,836 videos, Nov 2026); the rules themselves only use stored fields, never playlists,
because playlists cover less than half of the videos and are updated late.

| Category | Definition |
|---|---|
| Official M/V | As the PLAVE OFFICIAL M/V playlist: M/V, Special Video, Official Lyric Video, duration > 120s; not Performance Ver. |
| Cover | `[COVER]`, `[From. PLAVE]`, songs of `[PLAVE BIRTHDAY GIFT]` (not the member playlists, not short cuts) |
| Official Audio | Title contains "Official Audio" |
| Performance | Own-channel videos of PLAVE STAGE CLIP, M/V (Performance Ver.), Dance Practice / Vertical Dance |
| Live | Streamed (actualStartTime, or live / upcoming), not a Premiere and not PL:RADIO |
| PL:RADIO | "PL:RADIO with DJ" |
| LASSGO PLAVE | `[LASSGO PLAVE]` episodes and full cams |
| PL:SENT | `[플레젠트]` / "PL:SENT" |
| Highlight | `[하이라이트]` / Highlight, including songs from comeback lives |
| Behind | As the PLAVE Behind playlist: `[BEHIND]`, `[SPECIAL]`, "Recording Behind" |
| Promo | Teaser, highlight medley, concept / mood film, scheduler, ID clip, prologue, epilogue, etc. |
| Other | Greetings, reactions, fanchant guides, playlists, interviews, A to T : JAPAN, PUBG, everything else |
| Shorts - Clip | Shorts cut from shows: `#PLAVE_LIVE`, `#PL_RADIO`, `#LASSGO_PLAVE`, `#AtoT_JAPAN` |
| Shorts - Promo | Shorts about releases, albums and tours |
| Shorts - General | All other Shorts |

- Evaluation order (first match wins): Shorts - Clip, Shorts - Promo, Shorts - General, PL:RADIO, Live,
  Highlight (`[하이라이트]`), Promo, Behind, LASSGO PLAVE, PL:SENT, Other (reaction / guide / playlist), Performance,
  Official M/V, Official Audio, Highlight (any "highlight"), Cover, then Other. `dim_category.priority` mirrors this order
  (first rule of each category, Other last).
- Shorts: not streamed, 0 < duration <= 180s, title has a real hashtag. `#1`, `#2` (episode numbers) and the
  `｜#플레이브 PLAVE` signature are not hashtags; `｜#플레이브 #PLAVE` and `[COVER]` titles are Shorts only up to 60s.
- Premieres also have actualStartTime: a streamed video with a release or series keyword is not Live.
- Rules, keywords and thresholds live in `config.py` only.
- Raw fields (title, duration_sec, actual_start_time, live_broadcast_content) are stored alongside `category_id`, so
  `reclassify.py` can re-run the rules from the DB without calling the API (`--dry-run` shows the changes first).
- Unit tests cover: normal cases of each category, Premieres, upcoming / running streams, duration thresholds, NULL duration,
  NULL title, hashtag signatures, overlapping keywords, and that the SQL seed matches `config.py`.
- Known limits: Shorts without any hashtag fall into Promo / Other (about a dozen, mostly from 2022-2024); show hashtags exist only
  from 2024 to 2025, so Shorts - Clip and Shorts - General are not comparable outside that period.

## 8. Validation
- IDs: video `^[A-Za-z0-9_-]{11}$`, channel `^UC[A-Za-z0-9_-]{22}$`. Invalid -> skip row, count in `rows_failed`, log WARNING.
- Negative counts -> skip row, count in `rows_failed`.
- Unexpected NULLs (title, publishedAt, viewCount) -> log WARNING. like/comment NULL is expected and allowed.
- View count lower than the previous snapshot of the same video: log WARNING, keep the row, set `is_view_drop = true`.
  No previous snapshot -> `is_view_drop = false`.

## 9. Error handling & retry
- Network errors, timeouts, HTTP 5xx from YouTube: retry up to 5 times with exponential backoff (tenacity, e.g. 2s, 4s, 8s... with jitter).
- `quotaExceeded`, invalid API key, other 4xx: do NOT retry. Stop, mark the run `failed`, write the error to `run_log`, exit non-zero (GitHub emails on failure).
- Database connection errors: retry up to 3 times (covers a database waking up).
- A failure on one video batch should not lose the others: load what succeeded, mark the run `partial`.

## 10. Database schema (PostgreSQL, schema `plave`)
All tables live in schema `plave`, not `public` (Supabase auto-exposes `public` over its REST API).

```sql
CREATE SCHEMA IF NOT EXISTS plave;
SET search_path TO plave;

CREATE TABLE run_log (
    run_id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    snapshot_ts         TIMESTAMPTZ NOT NULL,          -- floored hour of this run
    start_time          TIMESTAMPTZ NOT NULL,          -- actual start
    end_time            TIMESTAMPTZ,
    status              TEXT NOT NULL CHECK (status IN ('running','success','partial','failed')),
    run_type            TEXT NOT NULL CHECK (run_type IN ('hourly','full')),
    trigger_source      TEXT,                          -- schedule / workflow_dispatch / local
    videos_in_playlist  INT,
    new_videos          INT,
    videos_fetched      INT,
    rows_inserted       INT DEFAULT 0,
    rows_failed         INT DEFAULT 0,
    quota_units_used    INT DEFAULT 0,
    error_message       TEXT
);

CREATE TABLE dim_date (
    date_key      INT PRIMARY KEY,                     -- YYYYMMDD
    full_date     DATE NOT NULL UNIQUE,
    day_of_month  SMALLINT NOT NULL,
    day_of_week   SMALLINT NOT NULL,                   -- ISO: 1 = Monday
    day_name      TEXT NOT NULL,
    iso_week      SMALLINT NOT NULL,
    iso_year      SMALLINT NOT NULL,
    month         SMALLINT NOT NULL,
    month_name    TEXT NOT NULL,
    quarter       SMALLINT NOT NULL,
    year          SMALLINT NOT NULL,
    is_weekend    BOOLEAN NOT NULL
);

CREATE TABLE dim_category (
    category_id    SMALLINT PRIMARY KEY,
    category_name  TEXT NOT NULL UNIQUE,
    priority       SMALLINT NOT NULL UNIQUE           -- evaluation order
);

CREATE TABLE dim_channel (
    channel_id           TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    custom_url           TEXT,
    uploads_playlist_id  TEXT NOT NULL,
    published_at         TIMESTAMPTZ,
    first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE dim_video (
    video_id                TEXT PRIMARY KEY,
    channel_id              TEXT NOT NULL REFERENCES dim_channel(channel_id),
    title                   TEXT NOT NULL,
    published_at            TIMESTAMPTZ NOT NULL,
    published_date_key      INT REFERENCES dim_date(date_key),  -- VN date
    duration_sec            INT,
    tags                    JSONB,                               -- array of strings; Power BI reads it as text
    youtube_category_id     TEXT,
    live_broadcast_content  TEXT,                                -- none / upcoming / live
    actual_start_time       TIMESTAMPTZ,
    actual_end_time         TIMESTAMPTZ,
    category_id             SMALLINT NOT NULL REFERENCES dim_category(category_id),
    first_seen_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE fact_video (
    video_id            TEXT NOT NULL REFERENCES dim_video(video_id),
    snapshot_ts         TIMESTAMPTZ NOT NULL,
    snapshot_ts_local   TIMESTAMP NOT NULL,
    date_key            INT NOT NULL REFERENCES dim_date(date_key),
    fetched_at          TIMESTAMPTZ NOT NULL,
    tier                TEXT NOT NULL CHECK (tier IN ('recent','old')),
    view_count          BIGINT CHECK (view_count >= 0),
    like_count          BIGINT CHECK (like_count >= 0),      -- NULL = hidden
    comment_count       BIGINT CHECK (comment_count >= 0),   -- NULL = disabled
    concurrent_viewers  BIGINT,                              -- only while live
    is_view_drop        BOOLEAN NOT NULL DEFAULT false,
    run_id              BIGINT REFERENCES run_log(run_id),
    PRIMARY KEY (video_id, snapshot_ts)
);

CREATE TABLE fact_channel (
    channel_id          TEXT NOT NULL REFERENCES dim_channel(channel_id),
    snapshot_ts         TIMESTAMPTZ NOT NULL,
    snapshot_ts_local   TIMESTAMP NOT NULL,
    date_key            INT NOT NULL REFERENCES dim_date(date_key),
    fetched_at          TIMESTAMPTZ NOT NULL,
    subscriber_count    BIGINT,                              -- NULL if hidden
    view_count          BIGINT,
    video_count         INT,
    run_id              BIGINT REFERENCES run_log(run_id),
    PRIMARY KEY (channel_id, snapshot_ts)
);
```

- Fact metrics are CUMULATIVE. Deltas are computed in DAX (Power BI), never stored.
- Upserts use `INSERT ... ON CONFLICT (...) DO UPDATE` (idempotent):
  - `dim_video` / `dim_channel`: update all metadata columns + `updated_at`, keep `first_seen_at`.
  - `fact_*`: on the same `(id, snapshot_ts)`, overwrite metrics, `fetched_at`, `run_id`.
- `is_view_drop` is computed before insert by comparing with the latest snapshot of the same video where `snapshot_ts < current snapshot_ts`.

### Seed (02_seed_dim_date_category.sql)
- `dim_category`: the 15 categories of section 7; IDs and names mirrored in `config.py`. IDs 1-5 are kept from the first
  5-category scheme. Existing databases are migrated with `04_category_v2.sql`, then `python src/reclassify.py`.
- `dim_date`: `generate_series('2020-01-01', '2030-12-31', '1 day')`.

### Roles (03_roles.sql)
- `powerbi_reader`: LOGIN, `USAGE` on schema `plave`, `SELECT` on all tables (+ default privileges for future tables). Power BI uses this account, never the admin `postgres` account.
- Through the pooler the username becomes `powerbi_reader.<project_ref>` (verify when testing).

## 11. GitHub Actions
### pipeline.yml
- Triggers: `schedule: cron '17 * * * *'` (minute 17, avoids the busy top of the hour) + `workflow_dispatch`.
- `concurrency: { group: pipeline, cancel-in-progress: false }` so two runs never overlap.
- `timeout-minutes: 15`.
- Steps: checkout -> setup-python 3.11 with pip cache -> `pip install -r requirements.txt` -> `python src/main.py`.
- Secrets passed as env vars. Never print them.
### keepalive.yml
- GitHub disables scheduled workflows after 60 days without repo activity. A monthly scheduled job keeps the repo active
  (e.g. an empty commit pushed with `GITHUB_TOKEN` and `permissions: contents: write`). Verify it works at the first month.
- Chain of risk to document: cron disabled -> no DB activity -> Supabase pauses after 7 days.

## 12. Power BI Desktop (Import mode)
- Connect with the PostgreSQL connector to the Session pooler host, port 5432, database `postgres`, SSL on, user `powerbi_reader.<ref>`. Test the connection early (step 2).
- Load tables directly from schema `plave`.
- Model (star schema):
  - `dim_date[date_key]` -> `fact_video[date_key]`, `fact_channel[date_key]` (active)
  - `dim_date[date_key]` -> `dim_video[published_date_key]` (inactive, use with USERELATIONSHIP)
  - `dim_video` -> `fact_video`, `dim_category` -> `dim_video`, `dim_channel` -> `fact_channel`
- No SQL views: all derived metrics are computed in DAX:
  - Latest value per video: value at the max `snapshot_ts` in the current filter context.
  - Daily growth: latest value of the day minus latest value of the previous day with data
    (old videos have only ~2 snapshots/day; gaps in data inflate a single day's delta).
  - Velocity (views at 24h / 72h / 7d after publish): first snapshot with `snapshot_ts >= published_at + N hours`;
    only for videos published after the pipeline started.
  - Engagement rate: `DIVIDE(likes + comments, views)`; treat NULL likes/comments as unknown, not 0.
- Dashboard pages:
  1. Overview: KPIs (subscribers, total views, video count) + trend over time
  2. Growth: daily/weekly/monthly growth of views, likes, comments, subscribers
  3. Category comparison: the 15 categories of section 7
  4. Video detail: ranking table + drill-through per video
  5. Pipeline health: from `run_log` (status, durations, quota, gaps between snapshots)

## 13. Implementation order (finish and test each step before moving on)
1. Initialize repo, folder structure, `.env.example`, `.gitignore`, `requirements.txt`
2. Create the Supabase project; run `01_ddl.sql`, `02_seed`, `03_roles.sql` in DBeaver via the Session pooler (SSL);
   test the connection from: local Python, an empty `workflow_dispatch` GitHub Actions job, and Power BI (`powerbi_reader`)
3. `extract.py`: fetch channel + full uploads playlist + videos, test locally
4. `transform.py`: validation + classification + unit tests
5. `load.py`: upserts; verify idempotency (run twice in the same hour, row counts unchanged)
6. `logger.py` + `run_log`
7. `main.py`: assemble the pipeline + tier logic (recent / full every 12h)
8. GitHub Actions: `pipeline.yml` + secrets, test with `workflow_dispatch`, then enable cron; add `keepalive.yml`
   - Right before enabling cron, wipe all test data in DBeaver (as `postgres`) so the first real run is a full backfill:
     `TRUNCATE plave.fact_video, plave.fact_channel, plave.run_log, plave.dim_video, plave.dim_channel RESTART IDENTITY;`
     Keep `dim_date` and `dim_category` (seed). `run_log` must be truncated too, otherwise test full runs make the first real run `hourly`.
     Verify the first real run: `run_type = 'full'`, `fact_video` rows = videos in playlist, no fact row with `run_id IS NULL`.
9. Let the pipeline run for at least a few days to collect real data
10. Power BI dashboard
11. README: architecture, data limitations, how to run, dashboard screenshots

## 14. Limitations & operations (document in README)
- Public data only: no watch time, CTR, or retention.
- Subscriber count is rounded by YouTube, so growth charts look step-like.
- No historical data before the pipeline's first run; velocity metrics only for videos published afterwards.
- GitHub cron can be delayed or skipped; snapshots are not guaranteed every hour.
- Power BI Desktop Import mode needs a manual Refresh; data in Supabase still accumulates automatically.
- Supabase free tier: 500 MB. Expected growth well under 100 MB/year. Check size occasionally:
  `SELECT pg_size_pretty(pg_database_size(current_database()));`
- Free projects pause after 1 week of inactivity (only a risk if the workflow stops running).

## 15. Key design decisions
| Topic | Decision | Reason |
|---|---|---|
| Database | PostgreSQL on Supabase | Runs while my computer is off, free tier is enough |
| Connection | Session pooler, port 5432, SSL | Direct host is IPv6-only; GitHub Actions is IPv4 |
| Snapshot time | Floored to the hour + actual `fetched_at`; UTC `TIMESTAMPTZ` + VN local column | Idempotent PK while keeping the real fetch time |
| Old-tier schedule | Full run when >= 12h since last successful full run; cron at minute 17 | Self-heals when GitHub delays or skips runs |
| Official M/V | Fetched every hour whatever their age | Most important content; only ~17 videos (+1 quota unit/hour, Nov 2026) |
| Playlist scan | Entire uploads playlist every hour; stats by tier | Catch new videos quickly, keep storage small |
| Deleted/private videos | No flag; video stops getting snapshots | Keeps the model simple |
| `is_view_drop` | false when no previous snapshot | Nothing to compare against |
| Derived metrics | Computed in DAX, no SQL views | Single place for business logic |
| Classification | Rules in `config.py` only | Change rules and reclassify without API calls |
| Retry | Retry transient errors only; never retry quota/auth errors | Retrying a non-transient error cannot succeed |
| Security | Schema `plave` (not `public`), read-only role for Power BI, `.env` git-ignored | Not exposed via Supabase REST API, least privilege |