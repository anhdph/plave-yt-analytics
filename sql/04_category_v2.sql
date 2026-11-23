-- 04_category_v2.sql: migrate an existing dim_category from 5 to 15 categories (SPEC.md section 7).
-- Run once as admin (postgres), BEFORE deploying the matching config.py, then run src/reclassify.py.
-- Idempotent. New installs get the same rows from 02_seed_dim_date_category.sql.
-- IDs 1-5 keep their meaning (1 and 3 are renamed), so existing dim_video rows stay valid meanwhile.

BEGIN;

-- priority is UNIQUE: move the current values out of the way before renumbering.
UPDATE plave.dim_category SET priority = -priority WHERE priority > 0;

INSERT INTO plave.dim_category (category_id, category_name, priority) VALUES
    (14, 'Shorts - Clip',     1),
    (15, 'Shorts - Promo',    2),
    (3,  'Shorts - General',  3),
    (8,  'PL:RADIO',          4),
    (2,  'Live',              5),
    (11, 'Highlight',         6),
    (13, 'Promo',             7),
    (12, 'Behind',            8),
    (9,  'LASSGO PLAVE',      9),
    (10, 'PL:SENT',          10),
    (4,  'Performance',      11),
    (1,  'Official M/V',     12),
    (7,  'Official Audio',   13),
    (6,  'Cover',            14),
    (5,  'Other',            15)
ON CONFLICT (category_id) DO UPDATE
    SET category_name = EXCLUDED.category_name,
        priority      = EXCLUDED.priority;

COMMIT;

SELECT category_id, category_name, priority FROM plave.dim_category ORDER BY priority;
