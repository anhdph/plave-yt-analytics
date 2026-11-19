-- 01_ddl.sql: schema + tables (SPEC.md section 10). Run as admin (postgres) via the Session pooler.
-- Idempotent: safe to re-run (CREATE ... IF NOT EXISTS). No views by design.

CREATE SCHEMA IF NOT EXISTS plave;
SET search_path TO plave;

CREATE TABLE IF NOT EXISTS run_log (
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

CREATE TABLE IF NOT EXISTS dim_date (
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

CREATE TABLE IF NOT EXISTS dim_category (
    category_id    SMALLINT PRIMARY KEY,
    category_name  TEXT NOT NULL UNIQUE,
    priority       SMALLINT NOT NULL UNIQUE           -- evaluation order
);

CREATE TABLE IF NOT EXISTS dim_channel (
    channel_id           TEXT PRIMARY KEY,
    title                TEXT NOT NULL,
    custom_url           TEXT,
    uploads_playlist_id  TEXT NOT NULL,
    published_at         TIMESTAMPTZ,
    first_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dim_video (
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

CREATE TABLE IF NOT EXISTS fact_video (
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

CREATE TABLE IF NOT EXISTS fact_channel (
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
