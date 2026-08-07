-- Аналитические витрины. См. docs/TZ.md, раздел 5.
-- Все витрины обязаны работать при пустых fact_table_size / fact_report_usage.

-- 0. Размер таблицы, применимый к конкретному отчёту -----------------------
-- Размер хранится на пару «таблица + завод», а отчёт тоже привязан к заводу.
-- Эта витрина выбирает правильную строку размера: сначала точное совпадение
-- по заводу отчёта, иначе — общая строка «(не указан)» для файлов без ТС и
-- Завода. Все витрины со стороны отчётов обязаны ходить сюда, а НЕ напрямую
-- в fact_table_size: прямое соединение по table_id размножило бы строки по
-- числу заводов и завысило суммы в разы.
CREATE VIEW v_report_table_size AS
SELECT
    b.report_id,
    b.table_id,
    COALESCE(exact.total_mb,         common.total_mb)         AS total_mb,
    COALESCE(exact.percent_of_total, common.percent_of_total) AS percent_of_total,
    COALESCE(exact.row_count,        common.row_count)        AS row_count,
    COALESCE(exact.retention_days,   common.retention_days)   AS retention_days,
    COALESCE(exact.segment_count,    common.segment_count)    AS segment_count,
    (exact.table_id IS NOT NULL OR common.table_id IS NOT NULL) AS has_size,
    (exact.table_id IS NOT NULL)                                AS size_is_plant_specific
FROM bridge_report_table b
JOIN dim_report r ON r.report_id = b.report_id
LEFT JOIN fact_table_size exact
       ON exact.table_id = b.table_id
      AND exact.network  = COALESCE(r.network, '(не указана)')
      AND exact.plant    = COALESCE(r.plant, '(не указан)')
LEFT JOIN fact_table_size common
       ON common.table_id = b.table_id
      AND common.network  = '(не указана)'
      AND common.plant    = '(не указан)';

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
    LEFT JOIN v_report_table_size s ON s.table_id = b.table_id AND s.report_id = b.report_id
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
    t.object_kind,
    t.kind_source,
    t.schema_source,
    t.is_parsed_ok,
    COUNT(DISTINCT b.report_id)                    AS report_count,
    (COUNT(DISTINCT b.report_id) = 0)              AS is_orphan,
    sz.total_mb,
    sz.percent_of_total,
    sz.segment_count,
    sz.retention_days,
    sz.row_count,
    sz.plant_count,
    string_agg(DISTINCT r.report_name, '; ')       AS reports
FROM dim_table t
LEFT JOIN bridge_report_table b ON b.table_id = t.table_id
LEFT JOIN dim_report r          ON r.report_id = b.report_id
-- Размер таблицы складывается по всем заводам: на каждом она занимает своё
-- место. Соединение уже свёрнуто, поэтому строки не размножаются.
LEFT JOIN (
    SELECT table_id,
           ROUND(SUM(total_mb), 2)         AS total_mb,
           ROUND(SUM(percent_of_total), 3) AS percent_of_total,
           SUM(segment_count)              AS segment_count,
           MAX(retention_days)             AS retention_days,
           SUM(row_count)                  AS row_count,
           COUNT(*)                        AS plant_count
    FROM fact_table_size GROUP BY table_id
) sz ON sz.table_id = t.table_id
GROUP BY t.table_id, t.full_name, t.schema_name, t.table_name, t.object_kind,
         t.kind_source, t.schema_source, t.is_parsed_ok, sz.total_mb,
         sz.percent_of_total, sz.segment_count, sz.retention_days, sz.row_count,
         sz.plant_count;

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
-- Две независимые стороны РЦ, сведённые по ключу «сеть + завод»:
--   отчёты  — из файла отчётов, размеры — из файла размеров.
-- FULL JOIN, а не обычный: завод бывает в одном файле и отсутствует в другом
-- (размеры выгрузили, отчёты ещё нет — и наоборот), и такой РЦ обязан остаться
-- в сравнении со своими цифрами, а не исчезнуть.
CREATE VIEW v_network_overview AS
WITH by_reports AS (
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
),
by_tables AS (
    -- То же, что даёт v_tables_catalog, но собранное здесь: эта витрина
    -- определена ниже по файлу, а порядок создания в DuckDB имеет значение.
    -- «Под отчётами» считается по отчётам ЭТОГО завода, а не по всей базе.
    -- Каждая строка витрины — это РЦ, и таблица, к которой тянется отчёт
    -- соседнего завода, здесь лежит без дела. Общий счёт по базе при 70%
    -- совпадения отчётов между сетями показывал бы почти всё используемым.
    SELECT
        s.network, s.plant,
        COUNT(DISTINCT t.full_name)                    AS db_table_count,
        ROUND(SUM(s.total_mb), 1)                      AS db_total_mb,
        COUNT(DISTINCT t.full_name) FILTER (WHERE u.report_count > 0)
                                                       AS used_table_count,
        ROUND(SUM(s.total_mb) FILTER (WHERE u.report_count > 0), 1) AS used_mb,
        COUNT(DISTINCT LOWER(t.schema_name))           AS schema_count,
        ROUND(MEDIAN(s.retention_days), 0)             AS median_retention_days
    FROM fact_table_size s
    JOIN dim_table t ON t.table_id = s.table_id
    LEFT JOIN (
        SELECT b.table_id, r.network, r.plant,
               COUNT(DISTINCT b.report_id) AS report_count
        FROM bridge_report_table b
        JOIN dim_report r ON r.report_id = b.report_id
        GROUP BY 1, 2, 3
    ) u ON u.table_id = s.table_id
       AND u.network IS NOT DISTINCT FROM s.network
       AND u.plant   IS NOT DISTINCT FROM s.plant
    GROUP BY 1, 2
)
SELECT
    COALESCE(r.network, t.network) AS network,
    COALESCE(r.plant, t.plant)     AS plant,
    COALESCE(r.report_count, 0)      AS report_count,
    COALESCE(r.reports_with_view, 0) AS reports_with_view,
    t.db_table_count,
    t.db_total_mb,
    t.used_table_count,
    -- Таблицы, которых не касается ни один отчёт: место, которое база занимает
    -- без видимой причины. Разница, а не отдельный счёт, — чтобы две колонки
    -- гарантированно складывались в общее число таблиц завода.
    t.db_table_count - t.used_table_count AS unused_table_count,
    t.used_mb,
    ROUND(t.db_total_mb - t.used_mb, 1)   AS unused_mb,
    CASE WHEN t.db_total_mb > 0
         THEN ROUND(100.0 * t.used_mb / t.db_total_mb, 1) END AS used_pct_of_plant,
    t.schema_count,
    t.median_retention_days,
    r.exclusive_mb,
    r.exec_count,
    r.total_duration_sec,
    CASE WHEN COALESCE(r.report_count, 0) > 0
         THEN ROUND(t.db_total_mb / r.report_count, 1) END AS mb_per_report
FROM by_reports r
FULL JOIN by_tables t ON t.network = r.network AND t.plant = r.plant
ORDER BY db_total_mb DESC NULLS LAST, report_count DESC;

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
-- Только настоящие таблицы — как и везде, где речь о наборе таблиц отчёта.
--
-- Сравниваются только отчёты одного завода. Один и тот же отчёт живёт на
-- нескольких заводах, и это нормальное устройство, а не дубль: объединять
-- там нечего, а пар «отчёт сам с собой на соседнем заводе» набегает столько,
-- что за ними не видно настоящих кандидатов. Ограничение по РЦ заодно делает
-- ненужным прежний отбор по совпадению имени и каталога: внутри одного завода
-- пара «имя + каталог» уникальна по ключу отчёта.
CREATE VIEW v_report_overlap AS
WITH cnt AS (
    SELECT b.report_id, COUNT(*) AS n
    FROM bridge_report_table b
    JOIN dim_table t ON t.table_id = b.table_id
    WHERE t.object_kind = 'TABLE'
    GROUP BY b.report_id
    HAVING COUNT(*) >= 2
),
pairs AS (
    SELECT a.report_id AS report_id_1, b.report_id AS report_id_2, COUNT(*) AS shared_tables
    FROM bridge_report_table a
    JOIN bridge_report_table b
      ON a.table_id = b.table_id
     AND a.report_id < b.report_id
    JOIN dim_table t ON t.table_id = a.table_id
    WHERE t.object_kind = 'TABLE'
    GROUP BY 1, 2
)
SELECT
    COALESCE(r1.network, '(не указана)') AS network,
    COALESCE(r1.plant,   '(не указан)')  AS plant,
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
-- Только внутри одного РЦ. COALESCE, а не сравнение напрямую: NULL = NULL
-- в SQL неверно, и отчёты без указанного завода выпали бы из сравнения совсем.
WHERE COALESCE(r1.network, '') = COALESCE(r2.network, '')
  AND COALESCE(r1.plant, '')   = COALESCE(r2.plant, '')
  AND p.shared_tables::DOUBLE / (c1.n + c2.n - p.shared_tables) >= 0.8
ORDER BY jaccard DESC, p.shared_tables DESC;


-- =========================================================================
-- 6. Пять основных представлений в разрезе РЦ (завода)
-- Все витрины несут колонки network и plant: аналитика ведётся по каждому РЦ.
-- См. docs/TZ.md, раздел 5.6.
-- =========================================================================

-- 6.1. Таблица №1 — все таблицы и их размеры ------------------------------
-- Источник списка таблиц БД — ТОЛЬКО файл размеров. Строка на пару
-- «таблица + завод», ровно как в файле; ничего к нему не добавляется.
-- Витрина НЕ зависит от отчётов и в обратную сторону тоже: объекты, которые
-- упомянуты в отчётах, но в файле размеров отсутствуют, сюда НЕ подмешиваются
-- — мост «отчёт ↔ таблица» только ссылается на этот список. Сколько таких
-- ссылок повисло без размера, видно в size_coverage_pct отчёта.
-- Колонки report_count и rc_report_count добавлены справочно и на состав строк
-- не влияют. Их РАЗНИЦА принципиальна: report_count считает отчёты по всей
-- базе, rc_report_count — только отчёты того же завода, что и строка. На
-- странице «в разрезе РЦ» верен второй: таблица, к которой тянется отчёт
-- соседнего завода, на этом заводе всё равно лежит без дела, и по общему счёту
-- она выглядела бы используемой. При 70% совпадения отчётов между сетями
-- общий счёт занижает «не используются» примерно вдвое.
CREATE VIEW v_tables_catalog AS
SELECT
    s.network,
    s.plant,
    t.full_name,
    t.schema_name,
    t.table_name,
    t.object_kind,
    t.kind_source,
    s.total_mb,
    s.percent_of_total,
    s.row_count,
    s.retention_days,
    s.segment_count,
    s.measured_at,
    (SELECT COUNT(DISTINCT b.report_id) FROM bridge_report_table b
     WHERE b.table_id = t.table_id) AS report_count,
    (SELECT COUNT(DISTINCT b.report_id)
     FROM bridge_report_table b
     JOIN dim_report r ON r.report_id = b.report_id
     WHERE b.table_id = t.table_id
       AND r.network IS NOT DISTINCT FROM s.network
       AND r.plant   IS NOT DISTINCT FROM s.plant) AS rc_report_count
FROM fact_table_size s
JOIN dim_table t ON t.table_id = s.table_id;

-- 6.2. Таблица №2 — отчёт и его таблицы, один ко многим --------------------
-- Только настоящие таблицы: view, материализованные view, временные и
-- generated-объекты исключены — они не отражают физического хранения.
CREATE VIEW v_rc_report_tables AS
SELECT
    r.network,
    r.plant,
    r.report_no,
    r.report_name,
    r.catalog_path,
    t.full_name       AS table_full_name,
    t.schema_name,
    t.object_kind,
    t.kind_source,
    s.total_mb,
    s.percent_of_total,
    s.retention_days
FROM bridge_report_table b
JOIN dim_report r           ON r.report_id = b.report_id
JOIN dim_table  t           ON t.table_id  = b.table_id
LEFT JOIN v_report_table_size s ON s.table_id = b.table_id AND s.report_id = b.report_id
WHERE t.object_kind = 'TABLE'
ORDER BY r.plant, r.report_name, t.full_name;

-- 6.3. Таблица №3 — отчёт и его функции и процедуры ------------------------
CREATE VIEW v_rc_report_routines AS
SELECT
    r.network,
    r.plant,
    r.report_no,
    r.report_name,
    r.catalog_path,
    t.full_name    AS routine_full_name,
    t.schema_name,
    t.table_name   AS routine_name
FROM bridge_report_table b
JOIN dim_report r ON r.report_id = b.report_id
JOIN dim_table  t ON t.table_id  = b.table_id
WHERE t.object_kind = 'ROUTINE'
ORDER BY r.plant, r.report_name, t.full_name;

-- 6.4. Таблица №4 — отчёт и обращения пользователей ------------------------
-- Заказчик считает мерой пользовательской активности «Кол-во обращений».
-- Если появится отдельная колонка с числом уникальных пользователей, она
-- подхватится в distinct_users и встанет рядом.
CREATE VIEW v_rc_report_usage AS
SELECT
    r.network,
    r.plant,
    r.report_no,
    r.report_name,
    r.catalog_path,
    r.uses_view,
    u.exec_count,
    u.distinct_users,
    u.avg_duration_sec,
    ROUND(u.exec_count * u.avg_duration_sec, 1) AS total_duration_sec,
    u.last_executed_at,
    CASE
        WHEN u.exec_count IS NULL THEN 'Нет данных'
        WHEN u.exec_count = 0     THEN 'Не запускался'
        WHEN u.exec_count < 10    THEN 'До 10 обращений'
        WHEN u.exec_count < 100   THEN 'От 10 до 100'
        ELSE                           'Более 100'
    END AS usage_band
FROM dim_report r
LEFT JOIN fact_report_usage u ON u.report_id = r.report_id
ORDER BY r.plant, u.exec_count DESC NULLS LAST;

-- 6.5. Таблица №5 — отчёт и глубина хранения данных ------------------------
-- Глубина задана на таблицу. Глубина отчёта — максимум по его таблицам:
-- отчёт показывает столько дней, сколько хранит самая «долгая» его таблица.
CREATE VIEW v_rc_report_retention AS
WITH per_report AS (
    SELECT
        b.report_id,
        MAX(s.retention_days)                                   AS retention_days,
        MIN(s.retention_days)                                   AS retention_days_min,
        COUNT(*) FILTER (WHERE s.retention_days IS NOT NULL)    AS tables_with_retention,
        COUNT(*)                                                AS table_count
    FROM bridge_report_table b
    JOIN dim_table t            ON t.table_id = b.table_id
    LEFT JOIN v_report_table_size s ON s.table_id = b.table_id AND s.report_id = b.report_id
    WHERE t.object_kind = 'TABLE'
    GROUP BY b.report_id
)
SELECT
    r.network,
    r.plant,
    r.report_no,
    r.report_name,
    r.catalog_path,
    p.table_count,
    p.tables_with_retention,
    p.retention_days,
    p.retention_days_min,
    CASE
        WHEN p.retention_days IS NULL THEN 'Не задана'
        WHEN p.retention_days <= 30   THEN 'До 30 дней'
        WHEN p.retention_days <= 45   THEN 'От 31 до 45 дней'
        ELSE                               'Более 45 дней'
    END AS retention_band
FROM dim_report r
LEFT JOIN per_report p ON p.report_id = r.report_id
ORDER BY r.plant, p.retention_days DESC NULLS LAST;

-- 6.6. Сводка по РЦ — шапка страницы --------------------------------------
CREATE VIEW v_rc_summary AS
SELECT
    COALESCE(r.network, '(не указана)') AS network,
    COALESCE(r.plant, '(не указан)')    AS plant,
    COUNT(DISTINCT r.report_id)         AS report_count,
    COUNT(DISTINCT b.table_id) FILTER (WHERE t.object_kind = 'TABLE')   AS table_count,
    COUNT(DISTINCT b.table_id) FILTER (WHERE t.object_kind = 'VIEW')    AS view_count,
    COUNT(DISTINCT b.table_id) FILTER (WHERE t.object_kind = 'MATERIALIZED VIEW')
                                                                        AS matview_count,
    COUNT(DISTINCT b.table_id) FILTER (WHERE t.object_kind = 'TEMP')    AS temp_count,
    COUNT(DISTINCT b.table_id) FILTER (WHERE t.object_kind = 'ROUTINE') AS routine_count
FROM dim_report r
LEFT JOIN bridge_report_table b ON b.report_id = r.report_id
LEFT JOIN dim_table t           ON t.table_id  = b.table_id
GROUP BY 1, 2
ORDER BY report_count DESC;


-- 6.7. Отчёт и его таблицы одной строкой ----------------------------------
-- Прямо отвечает на вопрос «на какие таблицы ссылается отчёт и сколько они
-- весят суммарно». Питает карточку отчёта и страницу объёма.
CREATE VIEW v_report_tables_summary AS
WITH tbl AS (
    SELECT
        b.report_id,
        t.table_id,
        t.full_name,
        s.total_mb,
        s.percent_of_total,
        s.retention_days,
        (SELECT COUNT(DISTINCT x.report_id)
         FROM bridge_report_table x WHERE x.table_id = t.table_id) AS used_by_reports
    FROM bridge_report_table b
    JOIN dim_table t            ON t.table_id = b.table_id
    LEFT JOIN v_report_table_size s ON s.table_id = b.table_id AND s.report_id = b.report_id
    WHERE t.object_kind = 'TABLE'
),
agg AS (
    SELECT
        report_id,
        COUNT(*)                                                   AS table_count,
        COUNT(*) FILTER (WHERE total_mb IS NOT NULL)               AS sized_table_count,
        COUNT(*) FILTER (WHERE used_by_reports = 1)                AS exclusive_table_count,
        ROUND(COALESCE(SUM(total_mb), 0), 2)                       AS tables_total_mb,
        ROUND(COALESCE(SUM(total_mb) FILTER (WHERE used_by_reports = 1), 0), 2)
                                                                   AS tables_exclusive_mb,
        ROUND(COALESCE(SUM(percent_of_total), 0), 3)               AS tables_pct_of_db,
        MAX(retention_days)                                        AS retention_days,
        string_agg(full_name, ', ' ORDER BY total_mb DESC NULLS LAST) AS table_names
    FROM tbl
    GROUP BY report_id
),
other AS (
    SELECT
        b.report_id,
        COUNT(*) FILTER (WHERE t.object_kind = 'VIEW')              AS view_count,
        COUNT(*) FILTER (WHERE t.object_kind = 'MATERIALIZED VIEW') AS matview_count,
        COUNT(*) FILTER (WHERE t.object_kind = 'TEMP')              AS temp_count,
        COUNT(*) FILTER (WHERE t.object_kind = 'ROUTINE')           AS routine_count
    FROM bridge_report_table b
    JOIN dim_table t ON t.table_id = b.table_id
    GROUP BY b.report_id
)
SELECT
    r.report_id,
    r.report_no,
    r.report_name,
    r.network,
    r.plant,
    r.catalog_path,
    r.uses_view,
    COALESCE(a.table_count, 0)           AS table_count,
    COALESCE(a.sized_table_count, 0)     AS sized_table_count,
    COALESCE(a.exclusive_table_count, 0) AS exclusive_table_count,
    COALESCE(a.tables_total_mb, 0)       AS tables_total_mb,
    COALESCE(a.tables_exclusive_mb, 0)   AS tables_exclusive_mb,
    COALESCE(a.tables_pct_of_db, 0)      AS tables_pct_of_db,
    a.retention_days,
    a.table_names,
    COALESCE(o.view_count, 0)            AS view_count,
    COALESCE(o.matview_count, 0)         AS matview_count,
    COALESCE(o.temp_count, 0)            AS temp_count,
    COALESCE(o.routine_count, 0)         AS routine_count,
    u.exec_count,
    u.avg_duration_sec,
    ROUND(u.exec_count * u.avg_duration_sec, 1) AS total_duration_sec,
    CASE
        WHEN a.table_count IS NULL OR a.table_count = 0 THEN NULL
        ELSE ROUND(100.0 * a.sized_table_count / a.table_count, 1)
    END AS size_coverage_pct
FROM dim_report r
LEFT JOIN agg   a ON a.report_id = r.report_id
LEFT JOIN other o ON o.report_id = r.report_id
LEFT JOIN fact_report_usage u ON u.report_id = r.report_id
ORDER BY tables_total_mb DESC;


-- =========================================================================
-- 7. Двойники отчёта: в других ТС и на других заводах своей ТС
-- См. docs/TZ.md, раздел 5.8.
-- =========================================================================
-- Зеркало v_report_overlap. Там пары ищутся ВНУТРИ одного завода, потому что
-- один и тот же отчёт на нескольких площадках — норма, а не дубль. Здесь эта
-- норма и есть предмет вопроса: что у сети (или у завода) есть своё, чего нет
-- у остальных.
--
-- Областей сравнения две, и обе нужны — какая интереснее, зависит от данных.
-- На данных заказчика сети пересекаются по отчётам примерно на 70%, и главный
-- вопрос — чем они РАЗЛИЧАЮТСЯ; внутри одной сети уникальных отчётов по
-- заводам заметно меньше. Поэтому область не зашита, а стоит в колонке
-- `scope`, и страница выбирает нужную:
--
--   'NETWORK' — сравнение с отчётами ДРУГИХ ТС (любой их завод);
--   'PLANT'   — сравнение с другими заводами СВОЕЙ ТС.
--
-- Строка на «отчёт × область»: витрина отдаёт ФАКТЫ, а не приговор
-- «уникален». Тёзки и ближайший по набору таблиц отчёт с величиной сходства
-- есть, а порог, за которым сходство означает «тот же отчёт», задаёт читатель —
-- на странице это ползунок. Зашить порог в витрину значило бы иметь две разные
-- правды об уникальности: одну в SQL, другую на странице.
--
-- COALESCE, а не сравнение напрямую: NULL = NULL в SQL неверно, и отчёты без
-- заполненной сети или завода выпали бы из сравнения совсем.
CREATE VIEW v_report_twin AS
WITH rc AS (
    SELECT report_id,
           COALESCE(network, '(не указана)') AS net,
           COALESCE(plant,   '(не указан)')  AS plt,
           LOWER(TRIM(report_name))          AS name_key
    FROM dim_report
),
tbl AS (
    -- Только настоящие таблицы — как везде, где речь о наборе таблиц отчёта.
    SELECT b.report_id, b.table_id
    FROM bridge_report_table b
    JOIN dim_table t ON t.table_id = b.table_id
    WHERE t.object_kind = 'TABLE'
),
cnt AS (
    SELECT report_id, COUNT(*) AS n FROM tbl GROUP BY report_id
),
scopes AS (
    SELECT 'NETWORK' AS scope UNION ALL SELECT 'PLANT'
),
pairs AS (
    -- Порог «не меньше 2 общих таблиц» — тот же, что в v_report_overlap: одна
    -- общая таблица означает общий справочник вроде календаря, а не тот же
    -- отчёт, и таких пар на реальных данных десятки тысяч.
    SELECT a.report_id AS id1, b.report_id AS id2, COUNT(*) AS shared
    FROM tbl a
    JOIN tbl b ON b.table_id = a.table_id AND b.report_id <> a.report_id
    GROUP BY 1, 2
    HAVING COUNT(*) >= 2
),
scored AS (
    SELECT
        p.id1 AS report_id,
        p.id2 AS twin_id,
        p.shared,
        ROUND(p.shared::DOUBLE / (c1.n + c2.n - p.shared), 3) AS jaccard,
        CASE WHEN ra.net = rb.net THEN 'PLANT' ELSE 'NETWORK' END AS scope
    FROM pairs p
    JOIN cnt c1 ON c1.report_id = p.id1
    JOIN cnt c2 ON c2.report_id = p.id2
    JOIN rc ra  ON ra.report_id = p.id1
    JOIN rc rb  ON rb.report_id = p.id2
    -- Пара обязана быть между разными РЦ: внутри одного завода похожие отчёты
    -- ищет v_report_overlap, и смешивать эти два вопроса нельзя.
    WHERE NOT (ra.net = rb.net AND ra.plt = rb.plt)
),
best AS (
    -- Ближайший двойник в своей области: наибольшее сходство, при равенстве —
    -- больше общих таблиц. Ровно один на «отчёт × область», иначе строки
    -- размножились бы по числу похожих отчётов и суммы на странице поехали бы.
    SELECT report_id, scope, twin_id, shared, jaccard
    FROM (
        SELECT *, ROW_NUMBER() OVER (
            PARTITION BY report_id, scope
            ORDER BY jaccard DESC, shared DESC, twin_id
        ) AS rn
        FROM scored
    )
    WHERE rn = 1
),
name_pairs AS (
    -- Тёзка: сравнение по LOWER(TRIM(...)) — регистр и краевые пробелы в
    -- выгрузке разъезжаются, а отчёт при этом один и тот же.
    SELECT ra.report_id,
           CASE WHEN ra.net = rb.net THEN 'PLANT' ELSE 'NETWORK' END AS scope,
           rb.net, rb.plt
    FROM rc ra
    JOIN rc rb ON rb.name_key = ra.name_key
    WHERE NOT (ra.net = rb.net AND ra.plt = rb.plt)
),
name_twin AS (
    SELECT
        report_id,
        scope,
        -- Считается то же, что перечисляется: в области ТС — сети, в области
        -- заводов — заводы. Иначе число и список рядом противоречили бы друг
        -- другу.
        COUNT(DISTINCT CASE WHEN scope = 'PLANT' THEN plt ELSE net END) AS name_twin_count,
        string_agg(DISTINCT CASE WHEN scope = 'PLANT' THEN plt ELSE net END, '; '
                   ORDER BY CASE WHEN scope = 'PLANT' THEN plt ELSE net END)
            AS name_twin_where
    FROM name_pairs
    GROUP BY report_id, scope
),
plants_in_net AS (
    SELECT net, COUNT(DISTINCT plt) AS n FROM rc GROUP BY net
),
networks_total AS (
    SELECT COUNT(DISTINCT net) AS n FROM rc
)
SELECT
    s.report_id,
    s.report_no,
    s.report_name,
    s.network,
    s.plant,
    s.catalog_path,
    s.uses_view,
    s.table_count,
    s.sized_table_count,
    s.tables_total_mb,
    s.tables_exclusive_mb,
    s.table_names,
    s.exec_count,
    s.avg_duration_sec,
    s.total_duration_sec,
    sc.scope,
    -- С чем вообще было что сравнивать. Ноль означает, что сравнивать не с чем,
    -- и «отчёт уникален» тогда не вывод, а отсутствие данных — страница обязана
    -- сказать это прямо.
    CASE WHEN sc.scope = 'PLANT' THEN pin.n - 1 ELSE nt_all.n - 1 END
        AS counterparts_compared,
    COALESCE(nt.name_twin_count, 0) AS name_twin_count,
    nt.name_twin_where,
    b.jaccard AS best_jaccard,
    b.shared  AS best_shared_tables,
    rb.report_name AS best_twin_report,
    rbc.net        AS best_twin_network,
    rbc.plt        AS best_twin_plant,
    CASE WHEN sc.scope = 'PLANT' THEN rbc.plt ELSE rbc.net END AS best_twin_where,
    b.twin_id AS best_twin_report_id
FROM v_report_tables_summary s
CROSS JOIN scopes sc
JOIN rc r0             ON r0.report_id = s.report_id
JOIN plants_in_net pin ON pin.net = r0.net
CROSS JOIN networks_total nt_all
LEFT JOIN name_twin nt ON nt.report_id = s.report_id AND nt.scope = sc.scope
LEFT JOIN best b       ON b.report_id  = s.report_id AND b.scope  = sc.scope
LEFT JOIN dim_report rb ON rb.report_id = b.twin_id
LEFT JOIN rc rbc        ON rbc.report_id = b.twin_id;


-- 7.1. Отчёт сети целиком: строка на «ТС + наименование» -------------------
-- Для вопроса «чем различаются сети» единица счёта — отчёт сети, а не запись
-- по заводу: отчёт, стоящий на трёх заводах одной ТС, — это один отчёт этой
-- сети, и считать его трижды значило бы завысить «своё» втрое.
--
-- Двойник в других ТС берётся по худшему для уникальности случаю: если хотя бы
-- одна площадка отчёта нашла себе двойника в другой сети, значит отчёт в других
-- сетях есть. Порога уникальности здесь тоже нет — он остаётся за читателем.
CREATE VIEW v_network_report_twin AS
WITH rows AS (
    SELECT * FROM v_report_twin WHERE scope = 'NETWORK'
),
keys AS (
    SELECT DISTINCT
           COALESCE(network, '(не указана)') AS network,
           LOWER(TRIM(report_name))          AS name_key
    FROM dim_report
),
name_twin AS (
    SELECT a.network, a.name_key,
           COUNT(DISTINCT b.network)              AS name_twin_count,
           string_agg(DISTINCT b.network, '; ' ORDER BY b.network) AS name_twin_networks
    FROM keys a
    JOIN keys b ON b.name_key = a.name_key AND b.network <> a.network
    GROUP BY a.network, a.name_key
),
tables AS (
    -- Таблиц у отчёта сети — объединение по её заводам, а не сумма по записям:
    -- одна и та же таблица на трёх заводах остаётся одной таблицей.
    SELECT COALESCE(r.network, '(не указана)') AS network,
           LOWER(TRIM(r.report_name))         AS name_key,
           COUNT(DISTINCT b.table_id)         AS table_count
    FROM dim_report r
    JOIN bridge_report_table b ON b.report_id = r.report_id
    JOIN dim_table t           ON t.table_id = b.table_id AND t.object_kind = 'TABLE'
    GROUP BY 1, 2
),
agg AS (
    SELECT
        COALESCE(network, '(не указана)')          AS network,
        LOWER(TRIM(report_name))                   AS name_key,
        MIN(report_name)                           AS report_name,
        COUNT(*)                                   AS plant_count,
        string_agg(DISTINCT COALESCE(plant, '(не указан)'), '; '
                   ORDER BY COALESCE(plant, '(не указан)'))       AS plants,
        -- Объём и запуски складываются по заводам сети: на каждом заводе отчёт
        -- читает свои таблицы и запускается своё число раз.
        ROUND(SUM(tables_total_mb), 2)             AS tables_total_mb,
        SUM(exec_count)                            AS exec_count,
        MAX(counterparts_compared)                 AS networks_compared,
        MAX(best_jaccard)                          AS best_jaccard
    FROM rows
    GROUP BY 1, 2
),
best AS (
    SELECT network, name_key, best_twin_report, best_twin_network,
           best_shared_tables, best_jaccard
    FROM (
        SELECT COALESCE(network, '(не указана)') AS network,
               LOWER(TRIM(report_name))         AS name_key,
               best_twin_report, best_twin_network, best_shared_tables, best_jaccard,
               ROW_NUMBER() OVER (
                   PARTITION BY COALESCE(network, '(не указана)'),
                                LOWER(TRIM(report_name))
                   ORDER BY best_jaccard DESC NULLS LAST, best_shared_tables DESC
               ) AS rn
        FROM rows
    )
    WHERE rn = 1
)
SELECT
    a.network,
    a.name_key,
    a.report_name,
    a.plant_count,
    a.plants,
    COALESCE(t.table_count, 0) AS table_count,
    a.tables_total_mb,
    a.exec_count,
    a.networks_compared,
    COALESCE(nt.name_twin_count, 0) AS name_twin_count,
    nt.name_twin_networks,
    a.best_jaccard,
    b.best_shared_tables,
    b.best_twin_report,
    b.best_twin_network
FROM agg a
LEFT JOIN tables t    ON t.network = a.network AND t.name_key = a.name_key
LEFT JOIN name_twin nt ON nt.network = a.network AND nt.name_key = a.name_key
LEFT JOIN best b       ON b.network = a.network AND b.name_key = a.name_key;
