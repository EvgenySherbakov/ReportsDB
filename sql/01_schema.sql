-- Схема БД отчётности SSRS. См. docs/TZ.md, раздел 4.
-- Скрипт выполняется на чистой БД (ETL пересоздаёт файл), но написан идемпотентно.

DROP VIEW IF EXISTS v_report_tables_summary;
DROP VIEW IF EXISTS v_rc_summary;
DROP VIEW IF EXISTS v_rc_report_retention;
DROP VIEW IF EXISTS v_rc_report_usage;
DROP VIEW IF EXISTS v_rc_report_routines;
DROP VIEW IF EXISTS v_rc_report_tables;
DROP VIEW IF EXISTS v_rc_tables;
DROP VIEW IF EXISTS v_report_overlap;
DROP VIEW IF EXISTS v_report_duration;
DROP VIEW IF EXISTS v_network_overview;
DROP VIEW IF EXISTS v_schema_overview;
DROP VIEW IF EXISTS v_catalog_overview;
DROP VIEW IF EXISTS v_decommission_candidates;
DROP VIEW IF EXISTS v_report_cost_value;
DROP VIEW IF EXISTS v_table_criticality;
DROP VIEW IF EXISTS v_report_footprint;

DROP TABLE IF EXISTS etl_reject;
DROP TABLE IF EXISTS etl_run;
DROP TABLE IF EXISTS fact_report_usage;
DROP TABLE IF EXISTS fact_table_size;
DROP TABLE IF EXISTS bridge_report_table;
DROP TABLE IF EXISTS dim_table;
DROP TABLE IF EXISTS dim_report;

-- Отчёты -----------------------------------------------------------------
CREATE TABLE dim_report (
    report_id    INTEGER PRIMARY KEY,
    -- Номер из исходной таблицы (колонка «№»): текстом, потому что встречаются
    -- значения вида «1.2». Служит для поиска отчёта в оригинальном файле.
    report_no    VARCHAR,
    report_name  VARCHAR NOT NULL,
    -- Организационный разрез: торговая сеть и завод внутри неё.
    network      VARCHAR,
    plant        VARCHAR,
    -- Путь пользователя к отчёту. Приходит тремя колонками; catalog_path
    -- собирается из них для отображения и поиска.
    catalog_path VARCHAR,
    folder_l1    VARCHAR,
    folder_l2    VARCHAR,
    folder_l3    VARCHAR,
    folder_depth INTEGER,
    -- Отчёт обращается к данным через view. Если да, список таблиц-источников
    -- заведомо неполон: за view могут стоять другие таблицы.
    uses_view    BOOLEAN,
    description  VARCHAR,
    owner        VARCHAR,
    source_row   INTEGER,
    -- Один и тот же отчёт может существовать для разных сетей и заводов,
    -- поэтому они входят в ключ уникальности.
    UNIQUE (network, plant, catalog_path, report_name)
);

-- Объекты-источники -------------------------------------------------------
-- Хранит не только таблицы: сюда же попадают view, материализованные view,
-- временные таблицы, функции и процедуры. Различает их object_kind.
CREATE TABLE dim_table (
    table_id     INTEGER PRIMARY KEY,
    schema_name  VARCHAR NOT NULL,
    table_name   VARCHAR NOT NULL,
    full_name    VARCHAR NOT NULL UNIQUE,
    -- TABLE | VIEW | MATERIALIZED VIEW | TEMP | ROUTINE
    object_kind  VARCHAR NOT NULL DEFAULT 'TABLE',
    -- Откуда известен тип: «колонка» (объект пришёл из отдельной колонки),
    -- «маска» (распознан по имени), «по умолчанию» (не определён, считаем
    -- таблицей). Нужен, чтобы не выдавать догадку за факт.
    kind_source  VARCHAR NOT NULL DEFAULT 'по умолчанию',
    is_parsed_ok BOOLEAN NOT NULL DEFAULT TRUE
);

-- Мост многие-ко-многим ---------------------------------------------------
CREATE TABLE bridge_report_table (
    report_id INTEGER NOT NULL,
    table_id  INTEGER NOT NULL,
    PRIMARY KEY (report_id, table_id)
);

-- Размеры таблиц (заполняется, когда появятся данные) ----------------------
CREATE TABLE fact_table_size (
    table_id    INTEGER PRIMARY KEY,
    row_count   BIGINT,
    data_mb     DOUBLE,
    index_mb    DOUBLE,
    total_mb    DOUBLE,
    -- Доля таблицы в общем объёме БД, %. Приходит из выгрузки сегментов и
    -- суммируется так же, как размер: по всем сегментам одной таблицы.
    percent_of_total DOUBLE,
    -- Сколько строк выгрузки сегментов сложилось в эту таблицу (секции и т.п.).
    segment_count INTEGER,
    -- Глубина хранения данных в таблице, дней.
    retention_days INTEGER,
    measured_at DATE
);

-- Использование отчётов ---------------------------------------------------
-- Заполняется либо из основного файла (колонки «Кол-во обращений» и
-- «Ср. дл. (сек)»), либо из отдельного файла статистики.
CREATE TABLE fact_report_usage (
    report_id        INTEGER PRIMARY KEY,
    exec_count       BIGINT,
    distinct_users   INTEGER,
    -- Средняя длительность выборки в СЕКУНДАХ — в таком виде приходит из
    -- исходного файла, в таком же и хранится.
    avg_duration_sec DOUBLE,
    last_executed_at DATE,
    period_start     DATE,
    period_end       DATE
);

-- Журнал загрузок ---------------------------------------------------------
CREATE TABLE etl_run (
    run_id       INTEGER PRIMARY KEY,
    started_at   TIMESTAMP NOT NULL,
    source_file  VARCHAR,
    source_sha256 VARCHAR,
    rows_read    INTEGER,
    rows_loaded  INTEGER,
    rows_rejected INTEGER,
    tool_version VARCHAR,
    -- Версия структуры БД. По ней приложение понимает, что база собрана
    -- прежней версией кода и её нужно пересобрать.
    schema_version INTEGER
);

-- Отбракованные строки ----------------------------------------------------
CREATE TABLE etl_reject (
    run_id     INTEGER,
    source_row INTEGER,
    reason     VARCHAR,
    payload    VARCHAR
);
