"""Сборка самодостаточного HTML-файла. См. docs/TZ.md, разделы 2.1 и 9.

Данные встраиваются как JSON, агрегация — на чистом JavaScript. Ноль внешних
запросов: файл открывается двойным кликом по протоколу file://.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb

from .config import DB_PATH, DIST_DIR

TEMPLATE = Path(__file__).parent / "templates" / "standalone.html"

QUERIES = {
    "reports": """
        SELECT f.report_id, f.report_no, f.report_name,
               COALESCE(f.network, '—') AS network, COALESCE(f.plant, '—') AS plant,
               f.catalog_path, COALESCE(f.folder_l1, '(корень)') AS folder,
               f.uses_view, f.table_count, f.exclusive_mb, f.gross_mb, f.shared_mb,
               f.exclusive_pct_of_db, f.size_coverage_pct,
               c.exec_count, c.avg_duration_sec, c.total_duration_sec, c.quadrant
        FROM v_report_footprint f
        LEFT JOIN v_report_cost_value c ON c.report_id = f.report_id
        ORDER BY f.exclusive_mb DESC
    """,
    "tables": """
        SELECT full_name, schema_name, report_count, is_orphan, total_mb, row_count
        FROM v_table_criticality
        ORDER BY report_count DESC, total_mb DESC NULLS LAST
    """,
    "candidates": """
        SELECT report_no, report_name, COALESCE(network, '—') AS network,
               COALESCE(plant, '—') AS plant, catalog_path, uses_view,
               exclusive_mb, exclusive_pct_of_db, table_count, exec_count, confidence
        FROM v_decommission_candidates
        LIMIT 200
    """,
    "durations": """
        SELECT report_no, report_name, COALESCE(network, '—') AS network,
               COALESCE(plant, '—') AS plant, catalog_path, uses_view,
               exec_count, avg_duration_sec, total_duration_sec, duration_band
        FROM v_report_duration
        WHERE avg_duration_sec IS NOT NULL
        LIMIT 500
    """,
    "catalog": "SELECT * FROM v_catalog_overview",
    "networks": "SELECT * FROM v_network_overview",
}

KPI_SQL = """
SELECT
    (SELECT COUNT(*) FROM dim_report)                          AS reports,
    (SELECT COUNT(*) FROM dim_table)                           AS tables,
    (SELECT COUNT(*) FROM bridge_report_table)                 AS links,
    (SELECT COUNT(*) FROM v_table_criticality WHERE is_orphan) AS orphans,
    (SELECT ROUND(SUM(total_mb), 1) FROM fact_table_size)      AS total_mb,
    (SELECT ROUND(AVG(size_coverage_pct), 0) FROM v_report_footprint) AS coverage,
    (SELECT ROUND(SUM(exclusive_pct_of_db), 2) FROM v_report_footprint) AS pct_of_db,
    (SELECT COUNT(*) FROM dim_report WHERE uses_view)                 AS with_view,
    (SELECT ROUND(SUM(total_duration_sec) / 3600.0, 1) FROM v_report_cost_value) AS hours,
    (SELECT source_file FROM etl_run ORDER BY run_id DESC LIMIT 1)    AS source_file
"""


def export(db_path: Path = DB_PATH, out_path: Path | None = None) -> Path:
    if not db_path.exists():
        raise SystemExit(f"База не найдена: {db_path}. Сначала выполните `build`.")

    out_path = out_path or DIST_DIR / "reportsdb.html"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        payload = {
            key: json.loads(con.execute(sql).df().to_json(orient="records"))
            for key, sql in QUERIES.items()
        }
        payload["kpi"] = json.loads(con.execute(KPI_SQL).df().to_json(orient="records"))[0]
    finally:
        con.close()

    payload["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M")

    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null",
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
    )
    out_path.write_text(html, encoding="utf-8")
    return out_path
