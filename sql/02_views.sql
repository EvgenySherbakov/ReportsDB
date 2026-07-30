-- Аналитические витрины. См. docs/TZ.md, раздел 5.
-- Все витрины обязаны работать при пустых fact_table_size / fact_report_usage.

-- 5.1. Объём данных на отчёт ---------------------------------------------
-- ВНИМАНИЕ: gross_mb суммирует общие таблицы повторно в разных отчётах.
-- Суммировать gross_mb по всем отчётам НЕЛЬЗЯ — это не объём хранилища.
-- Реальная выгода от вывода отчёта из эксплуатации — exclusive_mb.
CREATE VIEW v_report_footprint AS
WITH tbl_usage AS (
    SELECT table_id, COUNT(DISTINCT report_id) AS report_count
    FROM bridge_report_table
    GROUP BY table_id
),
joined AS (
    SELECT
        b.report_id,
        b.table_id,
        u.report_count,
        s.total_mb,
        s.row_count,
        s.percent_of_total,
        (s.table_id IS NOT NULL) AS has_size
    FROM bridge_report_table b
    JOIN tbl_usage u ON u.table_id = b.table_id
    LEFT JOIN fact_table_size s ON s.table_id = b.table_id
)
SELECT
    r.report_id,
    r.report_no,
    r.report_name,
    r.network,
    r.plant,
    r.catalog_path,
    r.folder_l1,
    r.folder_l2,
    r.folder_l3,
    r.uses_view,
    COUNT(j.table_id)                                                  AS table_count,
    COUNT(*) FILTER (WHERE j.has_size)                                 AS sized_table_count,
    COUNT(*) FILTER (WHERE j.report_count = 1)                         AS exclusive_table_count,
    ROUND(COALESCE(SUM(j.total_mb), 0), 2)                             AS gross_mb,
    ROUND(COALESCE(SUM(j.total_mb) FILTER (WHERE j.report_count = 1), 0), 2) AS exclusive_mb,
    ROUND(COALESCE(SUM(j.total_mb), 0)
        - COALESCE(SUM(j.total_mb) FILTER (WHERE j.report_count = 1), 0), 2) AS shared_mb,
    COALESCE(SUM(j.row_count), 0)                                      AS gross_rows,
    COALESCE(SUM(j.row_count) FILTER (WHERE j.report_count = 1), 0)    AS exclusive_rows,
    ROUND(COALESCE(SUM(j.percent_of_total) FILTER (WHERE j.report_count = 1), 0), 3)
                                                                       AS exclusive_pct_of_db,
    CASE
        WHEN COUNT(j.table_id) = 0 THEN NULL
        ELSE ROUND(100.0 * COUNT(*) FILTER (WHERE j.has_size) / COUNT(j.table_id), 1)
    END                                                                AS size_coverage_pct
FROM dim_report r
LEFT JOIN joined j ON j.report_id = r.report_id
GROUP BY r.report_id, r.report_no, r.report_name, r.network, r.plant,
         r.catalog_path, r.folder_l1, r.folder_l2, r.folder_l3, r.uses_view;

-- 5.2. Критичность таблиц -------------------------------------------------
CREATE VIEW v_table_criticality AS
SELECT
    t.table_id,
    t.full_name,
    t.schema_name,
    t.table_name,
    t.is_parsed_ok,
    COUNT(DISTINCT b.report_id)                    AS report_count,
    (COUNT(DISTINCT b.report_id) = 0)              AS is_orphan,
    s.total_mb,
    s.percent_of_total,
    s.segment_count,
    s.row_count,
    string_agg(DISTINCT r.report_name, '; ')       AS reports
FROM dim_table t
LEFT JOIN bridge_report_table b ON b.table_id = t.table_id
LEFT JOIN dim_report r          ON r.report_id = b.report_id
LEFT JOIN fact_table_size s     ON s.table_id = t.table_id
GROUP BY t.table_id, t.full_name, t.schema_name, t.table_name, t.is_parsed_ok,
         s.total_mb, s.percent_of_total, s.segment_count, s.row_count;

-- 5.3. Стоимость против ценности -----------------------------------------
CREATE VIEW v_report_cost_value AS
WITH j AS (
    SELECT
        f.*,
        u.exec_count,
        u.distinct_users,
        u.avg_duration_sec,
        u.last_executed_at
    FROM v_report_footprint f
    LEFT JOIN fact_report_usage u ON u.report_id = f.report_id
),
th AS (
    SELECT
        (SELECT median(exclusive_mb) FROM j WHERE COALESCE(size_coverage_pct, 0) > 0) AS mb_med,
        (SELECT median(exec_count)   FROM j WHERE exec_count IS NOT NULL)             AS exec_med
)
SELECT
    j.report_id,
    j.report_no,
    j.report_name,
    j.catalog_path,
    j.folder_l1,
    j.folder_l2,
    j.folder_l3,
    j.network,
    j.plant,
    j.uses_view,
    j.table_count,
    j.exclusive_mb,
    j.gross_mb,
    j.exclusive_pct_of_db,
    j.size_coverage_pct,
    j.exec_count,
    j.distinct_users,
    j.avg_duration_sec,
    -- Суммарное время, потраченное на отчёт за период: вторая метрика
    -- стоимости, независимая от объёма данных.
    ROUND(j.exec_count * j.avg_duration_sec, 1) AS total_duration_sec,
    j.last_executed_at,
    CASE
        WHEN j.exec_count IS NULL OR j.exec_count = 0 THEN NULL
        ELSE ROUND(j.exclusive_mb / j.exec_count, 3)
    END AS mb_per_execution,
    CASE
        WHEN j.exec_count IS NULL                                    THEN 'Нет данных об использовании'
        WHEN j.exclusive_mb >= COALESCE(th.mb_med, 0)
         AND j.exec_count   <  COALESCE(th.exec_med, 0)              THEN 'Дорогой и невостребованный'
        WHEN j.exclusive_mb >= COALESCE(th.mb_med, 0)                THEN 'Дорогой и востребованный'
        WHEN j.exec_count   <  COALESCE(th.exec_med, 0)              THEN 'Дешёвый и невостребованный'
        ELSE                                                              'Дешёвый и востребованный'
    END AS quadrant
FROM j CROSS JOIN th;

-- 5.4. Кандидаты на вывод из эксплуатации ---------------------------------
CREATE VIEW v_decommission_candidates AS
SELECT
    report_id,
    report_no,
    report_name,
    network,
    plant,
    catalog_path,
    uses_view,
    table_count,
    exclusive_mb,
    gross_mb,
    exclusive_pct_of_db,
    size_coverage_pct,
    exec_count,
    avg_duration_sec,
    last_executed_at,
    CASE
        WHEN exec_count IS NULL                     THEN 'Низкая: нет данных об использовании'
        -- За view могут стоять таблицы, которых нет в списке источников,
        -- поэтому объём такого отчёта посчитан не полностью.
        WHEN uses_view                              THEN 'Низкая: отчёт использует view'
        WHEN COALESCE(size_coverage_pct, 0) < 100   THEN 'Средняя: размеры известны не по всем таблицам'
        ELSE                                             'Высокая'
    END AS confidence
FROM v_report_cost_value
WHERE exec_count IS NULL OR exec_count = 0
ORDER BY exclusive_mb DESC, table_count DESC;

-- 5.5. Обзор по организационным разрезам ---------------------------------
CREATE VIEW v_network_overview AS
SELECT
    COALESCE(c.network, '(не указана)') AS network,
    COALESCE(c.plant, '(не указан)')    AS plant,
    COUNT(*)                            AS report_count,
    COUNT(*) FILTER (WHERE c.uses_view) AS reports_with_view,
    ROUND(SUM(c.exclusive_mb), 2)       AS exclusive_mb,
    SUM(c.exec_count)                   AS exec_count,
    ROUND(SUM(c.total_duration_sec), 1) AS total_duration_sec
FROM v_report_cost_value c
GROUP BY 1, 2
ORDER BY report_count DESC;

-- 5.5.1. Обзор каталога и схем --------------------------------------------
CREATE VIEW v_catalog_overview AS
SELECT
    COALESCE(c.folder_l1, '(не указан)') AS folder_l1,
    COALESCE(c.folder_l2, '')            AS folder_l2,
    COALESCE(c.folder_l3, '')            AS folder_l3,
    COUNT(*)                             AS report_count,
    COUNT(*) FILTER (WHERE c.uses_view)  AS reports_with_view,
    SUM(c.table_count)                   AS table_links,
    ROUND(SUM(c.exclusive_mb), 2)        AS exclusive_mb,
    ROUND(SUM(c.exclusive_pct_of_db), 3) AS exclusive_pct_of_db,
    SUM(c.exec_count)                    AS exec_count,
    ROUND(SUM(c.total_duration_sec), 1)  AS total_duration_sec,
    ROUND(AVG(c.size_coverage_pct), 1)   AS avg_size_coverage_pct
FROM v_report_cost_value c
GROUP BY 1, 2, 3
ORDER BY report_count DESC;

CREATE VIEW v_schema_overview AS
SELECT
    t.schema_name,
    COUNT(*)                                          AS table_count,
    COUNT(*) FILTER (WHERE t.report_count = 0)        AS orphan_table_count,
    SUM(t.report_count)                               AS report_links,
    ROUND(SUM(t.total_mb), 2)                         AS total_mb,
    ROUND(SUM(t.percent_of_total), 3)                 AS percent_of_db,
    SUM(t.row_count)                                  AS total_rows
FROM v_table_criticality t
GROUP BY t.schema_name
ORDER BY total_mb DESC NULLS LAST;

-- 5.5.2. Время выполнения ------------------------------------------------
-- Вторая, независимая от объёма метрика стоимости: отчёт бывает лёгким по
-- данным и дорогим по суммарному времени, и наоборот.
CREATE VIEW v_report_duration AS
SELECT
    report_id,
    report_no,
    report_name,
    network,
    plant,
    catalog_path,
    uses_view,
    table_count,
    exec_count,
    avg_duration_sec,
    total_duration_sec,
    exclusive_mb,
    CASE
        WHEN avg_duration_sec IS NULL           THEN 'Нет данных'
        WHEN avg_duration_sec >= 60             THEN 'Минута и дольше'
        WHEN avg_duration_sec >= 10             THEN 'От 10 секунд'
        WHEN avg_duration_sec >= 1              THEN 'От 1 секунды'
        ELSE                                         'Меньше секунды'
    END AS duration_band
FROM v_report_cost_value
ORDER BY total_duration_sec DESC NULLS LAST;

-- 5.6. Пересечение отчётов по набору источников ---------------------------
CREATE VIEW v_report_overlap AS
WITH cnt AS (
    SELECT report_id, COUNT(*) AS n
    FROM bridge_report_table
    GROUP BY report_id
    HAVING COUNT(*) >= 2
),
pairs AS (
    SELECT a.report_id AS report_id_1, b.report_id AS report_id_2, COUNT(*) AS shared_tables
    FROM bridge_report_table a
    JOIN bridge_report_table b
      ON a.table_id = b.table_id
     AND a.report_id < b.report_id
    GROUP BY 1, 2
)
SELECT
    r1.report_name AS report_1,
    r2.report_name AS report_2,
    p.shared_tables,
    c1.n           AS tables_1,
    c2.n           AS tables_2,
    ROUND(p.shared_tables::DOUBLE / (c1.n + c2.n - p.shared_tables), 3) AS jaccard
FROM pairs p
JOIN cnt c1        ON c1.report_id = p.report_id_1
JOIN cnt c2        ON c2.report_id = p.report_id_2
JOIN dim_report r1 ON r1.report_id = p.report_id_1
JOIN dim_report r2 ON r2.report_id = p.report_id_2
WHERE p.shared_tables::DOUBLE / (c1.n + c2.n - p.shared_tables) >= 0.8
ORDER BY jaccard DESC, p.shared_tables DESC;
