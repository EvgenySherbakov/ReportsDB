"""Сборка самодостаточного HTML-файла. См. docs/TZ.md, разделы 2.1 и 9.

Данные встраиваются как JSON, агрегация — на чистом JavaScript. Ноль внешних
запросов: файл открывается двойным кликом по протоколу file://.

Состав файла повторяет разделы приложения «Аналитика РЦ» и «Отчёты»: коллега
без Python и Docker должен видеть то же, что и владелец базы.

Два правила, которым подчинён набор запросов:

- **Никаких `LIMIT`.** Обрезка молча показывала бы меньше, чем приложение.
  Единственный отбор — содержательный порог сходства в «Похожих отчётах».
- **Не дублировать строки.** Имена отчётов не повторяются в каждой строке
  связи: связи несут `report_id`, а имя подставляет JS по справочнику. На
  тысячах связей это разница в разы по размеру файла.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import duckdb

from .config import DB_PATH, DIST_DIR

TEMPLATE = Path(__file__).parent / "templates" / "standalone.html"

QUERIES = {
    # Справочник отчётов. Служит списком, карточкой и основой ABC-анализа.
    #
    # Счётчик таблиц и суммы объёма берутся из v_report_tables_summary — как на
    # соответствующих страницах приложения. У v_report_footprint поле
    # table_count считает ВСЕ объекты отчёта, включая view и процедуры, и в
    # карточке рядом с разделом «Таблицы» давало бы другое число.
    "reports": """
        SELECT f.report_id, f.report_no, f.report_name,
               COALESCE(f.network, '—') AS network, COALESCE(f.plant, '—') AS plant,
               f.catalog_path, COALESCE(f.folder_l1, '(корень)') AS folder,
               f.uses_view,
               COALESCE(s.table_count, 0)  AS table_count,
               COALESCE(f.table_count, 0)  AS object_count,
               s.tables_total_mb, s.tables_exclusive_mb, s.tables_pct_of_db,
               f.exclusive_mb, f.gross_mb, f.shared_mb,
               f.exclusive_pct_of_db, f.size_coverage_pct,
               c.exec_count, c.avg_duration_sec, c.total_duration_sec, c.quadrant,
               s.view_count, s.matview_count, s.temp_count, s.routine_count,
               s.retention_days, s.table_names,
               -- Текст запроса едет в файл целиком: страница «Запросы к БД из SSRS» без
               -- него пуста, а обрезать значило бы показать неполный SQL, по
               -- которому нельзя судить о запросе. На размер влияет заметно,
               -- поэтому страница сборки честно показывает вес файла.
               r.sql_text
        FROM v_report_footprint f
        LEFT JOIN v_report_cost_value c     ON c.report_id = f.report_id
        LEFT JOIN v_report_tables_summary s ON s.report_id = f.report_id
        LEFT JOIN dim_report r              ON r.report_id = f.report_id
        ORDER BY f.exclusive_mb DESC NULLS LAST
    """,
    # Все связи «отчёт → объект» одним набором: из него собираются таблица №2
    # (только TABLE), таблица №3 (только ROUTINE) и карточка отчёта.
    # Имя отчёта не дублируется — JS подставит его по report_id.
    "objects": """
        SELECT b.report_id, t.full_name, t.schema_name, t.object_kind,
               s.total_mb, s.percent_of_total, s.retention_days,
               (SELECT COUNT(DISTINCT x.report_id) FROM bridge_report_table x
                WHERE x.table_id = b.table_id) AS used_by
        FROM bridge_report_table b
        JOIN dim_table t                ON t.table_id = b.table_id
        -- Размер берётся через резолвер: прямой join к fact_table_size
        -- размножил бы строки по числу заводов.
        LEFT JOIN v_report_table_size s ON s.report_id = b.report_id
                                       AND s.table_id  = b.table_id
        ORDER BY t.object_kind, s.total_mb DESC NULLS LAST
    """,
    # Таблица №1 — каталог таблиц и размеров, строка на «таблица + завод».
    "sizes": """
        SELECT network, plant, full_name, schema_name, object_kind,
               total_mb, percent_of_total, row_count, retention_days,
               segment_count, report_count, rc_report_count
        FROM v_tables_catalog
        ORDER BY total_mb DESC NULLS LAST
    """,
    # Таблица №4 — обращения к отчётам.
    "usage": """
        SELECT report_no, report_name, COALESCE(network, '—') AS network,
               COALESCE(plant, '—') AS plant, catalog_path,
               exec_count, distinct_users, avg_duration_sec, total_duration_sec,
               usage_band
        FROM v_rc_report_usage
    """,
    # Таблица №5 — глубина хранения.
    "retention": """
        SELECT report_no, report_name, COALESCE(network, '—') AS network,
               COALESCE(plant, '—') AS plant, catalog_path, table_count,
               tables_with_retention, retention_days, retention_days_min,
               retention_band
        FROM v_rc_report_retention
    """,
    "candidates": """
        SELECT report_no, report_name, COALESCE(network, '—') AS network,
               COALESCE(plant, '—') AS plant, catalog_path, uses_view,
               exclusive_mb, exclusive_pct_of_db, table_count, exec_count, confidence
        FROM v_decommission_candidates
    """,
    "durations": """
        SELECT report_no, report_name, COALESCE(network, '—') AS network,
               COALESCE(plant, '—') AS plant, catalog_path, uses_view,
               exec_count, avg_duration_sec, total_duration_sec, duration_band
        FROM v_report_duration
        WHERE avg_duration_sec IS NOT NULL
    """,
    # Похожие отчёты. Порог сходства содержательный, а не обрезка: пары с
    # одной общей таблицей — это шум, которого на реальных данных десятки
    # тысяч. Сравниваются только отчёты одного завода: один и тот же отчёт
    # живёт на нескольких заводах, и это норма, а не дубль.
    "overlap": """
        WITH cnt AS (
            SELECT b.report_id, COUNT(*) AS n
            FROM bridge_report_table b
            JOIN dim_table t ON t.table_id = b.table_id
            WHERE t.object_kind = 'TABLE'
            GROUP BY b.report_id
            HAVING COUNT(*) >= 2
        ),
        pairs AS (
            SELECT a.report_id AS id1, b.report_id AS id2, COUNT(*) AS shared
            FROM bridge_report_table a
            JOIN bridge_report_table b ON b.table_id = a.table_id
                                      AND b.report_id > a.report_id
            JOIN dim_table t           ON t.table_id = a.table_id
            WHERE t.object_kind = 'TABLE'
            GROUP BY 1, 2
            HAVING COUNT(*) >= 2
        )
        SELECT p.id1, p.id2, p.shared, c1.n AS tables_1, c2.n AS tables_2,
               ROUND(p.shared::DOUBLE / (c1.n + c2.n - p.shared), 3) AS jaccard
        FROM pairs p
        JOIN cnt c1        ON c1.report_id = p.id1
        JOIN cnt c2        ON c2.report_id = p.id2
        JOIN dim_report r1 ON r1.report_id = p.id1
        JOIN dim_report r2 ON r2.report_id = p.id2
        WHERE COALESCE(r1.network, '') = COALESCE(r2.network, '')
          AND COALESCE(r1.plant, '')   = COALESCE(r2.plant, '')
          AND p.shared::DOUBLE / (c1.n + c2.n - p.shared) >= 0.3
        ORDER BY jaccard DESC, p.shared DESC
    """,
    # Справочник объектов-источников: таблицы, view, процедуры — и кто из них
    # не используется ни одним отчётом.
    "tables": """
        SELECT full_name, schema_name, object_kind, kind_source, report_count,
               is_orphan, total_mb, percent_of_total, retention_days,
               row_count, plant_count
        FROM v_table_criticality
        ORDER BY report_count DESC, total_mb DESC NULLS LAST
    """,
    # Сравнение РЦ между собой: отчёты и объём базы завода рядом.
    "rc": "SELECT * FROM v_network_overview",
    # Двойники отчёта в разрезе заводов своей ТС. Порог сходства на странице —
    # ползунок, поэтому в файл едут ФАКТЫ (сходство, тёзки), а не готовый
    # признак «уникален»: иначе в файле была бы одна правда, а в приложении
    # другая. Имя отчёта не дублируется — JS подставит его по report_id.
    "twin": """
        SELECT report_id, name_twin_count, name_twin_where,
               best_jaccard, best_shared_tables, best_twin_report, best_twin_where,
               counterparts_compared
        FROM v_report_twin
        WHERE scope = 'PLANT'
    """,
    # Отчёт сети целиком: строка на «ТС + наименование», заводы схлопнуты. Это
    # единица счёта для вопроса «чем различаются сети» — тот же отчёт на трёх
    # заводах одной ТС считается один раз.
    "nettwin": """
        SELECT network, name_key, report_name, plant_count, plants,
               table_count, tables_total_mb, exec_count, networks_compared,
               name_twin_count, name_twin_networks, best_jaccard,
               best_shared_tables, best_twin_report, best_twin_network
        FROM v_network_report_twin
    """,
}

# Показатели считаются в браузере из тех же наборов, что видит пользователь, —
# иначе при выборе РЦ плитки остались бы общими по базе. Отсюда нужен только
# источник данных для подписи в шапке.
KPI_SQL = """
SELECT (SELECT source_file FROM etl_run ORDER BY run_id DESC LIMIT 1) AS source_file
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
