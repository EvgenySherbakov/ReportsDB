-- Схема БД отчётности SSRS. См. docs/TZ.md, раздел 4.
-- Скрипт выполняется на чистой БД (ETL пересоздаёт файл), но написан идемпотентно.

DROP VIEW IF EXISTS v_report_overlap;
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
    report_name  VARCHAR NOT NULL,
    catalog_path VARCHAR,
    folder_l1    VARCHAR,
    folder_l2    VARCHAR,
    folder_l3    VARCHAR,
    folder_depth INTEGER,
    description  VARCHAR,
    owner        VARCHAR,
    source_row   INTEGER,
    UNIQUE (catalog_path, report_name)
);

-- Таблицы-источники -------------------------------------------------------
CREATE TABLE dim_table (
    table_id     INTEGER PRIMARY KEY,
    schema_name  VARCHAR NOT NULL,
    table_name   VARCHAR NOT NULL,
    full_name    VARCHAR NOT NULL UNIQUE,
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
    measured_at DATE
);

-- Частота использования отчётов (заполняется, когда появятся данные) -------
CREATE TABLE fact_report_usage (
    report_id       INTEGER PRIMARY KEY,
    exec_count      BIGINT,
    distinct_users  INTEGER,
    avg_duration_ms DOUBLE,
    last_executed_at DATE,
    period_start    DATE,
    period_end      DATE
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
    tool_version VARCHAR
);

-- Отбракованные строки ----------------------------------------------------
CREATE TABLE etl_reject (
    run_id     INTEGER,
    source_row INTEGER,
    reason     VARCHAR,
    payload    VARCHAR
);
