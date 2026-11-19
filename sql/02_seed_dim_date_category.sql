-- 02_seed_dim_date_category.sql: reference data (SPEC.md section 10, "Seed").
-- Idempotent: ON CONFLICT DO NOTHING. Run after 01_ddl.sql.
-- category_id values are mirrored in src/config.py; keep them in sync.

SET search_path TO plave;

INSERT INTO dim_category (category_id, category_name, priority) VALUES
    (1, 'Music Video', 1),
    (2, 'Live',        2),
    (3, 'Shorts',      3),
    (4, 'Performance', 4),
    (5, 'Other',       5)
ON CONFLICT (category_id) DO NOTHING;

INSERT INTO dim_date (
    date_key, full_date, day_of_month, day_of_week, day_name,
    iso_week, iso_year, month, month_name, quarter, year, is_weekend
)
SELECT
    to_char(d, 'YYYYMMDD')::INT,
    d::DATE,
    EXTRACT(DAY     FROM d)::SMALLINT,
    EXTRACT(ISODOW  FROM d)::SMALLINT,
    trim(to_char(d, 'Day')),
    EXTRACT(WEEK    FROM d)::SMALLINT,      -- ISO week
    EXTRACT(ISOYEAR FROM d)::SMALLINT,
    EXTRACT(MONTH   FROM d)::SMALLINT,
    trim(to_char(d, 'Month')),
    EXTRACT(QUARTER FROM d)::SMALLINT,
    EXTRACT(YEAR    FROM d)::SMALLINT,
    EXTRACT(ISODOW  FROM d) IN (6, 7)
FROM generate_series('2020-01-01'::DATE, '2030-12-31'::DATE, INTERVAL '1 day') AS g(d)
ON CONFLICT (date_key) DO NOTHING;
