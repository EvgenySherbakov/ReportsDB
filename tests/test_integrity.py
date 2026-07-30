"""Проверки целостности. См. docs/TZ.md, раздел 12.

Запуск: pytest -q   (перед этим: python -m reportsdb sample)
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from reportsdb.config import (  # noqa: E402
    Mapping,
    SectionConfig,
    load_mapping,
    resolve_columns,
)
from reportsdb.etl import build  # noqa: E402
from reportsdb.normalize import (  # noqa: E402
    normalise_catalog_path,
    parse_table_list,
    parse_table_ref,
)
from reportsdb.sample_data import generate  # noqa: E402

VIEWS = [
    "v_report_footprint",
    "v_table_criticality",
    "v_report_cost_value",
    "v_decommission_candidates",
    "v_catalog_overview",
    "v_schema_overview",
    "v_report_overlap",
]


@pytest.fixture(scope="module")
def paths(tmp_path_factory):
    return generate()


@pytest.fixture(scope="module")
def full_db(paths, tmp_path_factory):
    """БД со всеми тремя источниками."""
    mapping = load_mapping()
    mapping.table_sizes.file = str(paths["sizes"])
    mapping.report_usage.file = str(paths["usage"])
    db = tmp_path_factory.mktemp("db") / "full.duckdb"
    build(paths["reports"], db, mapping)
    con = duckdb.connect(str(db), read_only=True)
    yield con
    con.close()


@pytest.fixture(scope="module")
def bare_db(paths, tmp_path_factory):
    """БД только с каталогом отчётов — факты пусты."""
    base = load_mapping()
    mapping = Mapping(
        reports=base.reports,
        table_sizes=SectionConfig(),
        report_usage=SectionConfig(),
    )
    db = tmp_path_factory.mktemp("db") / "bare.duckdb"
    build(paths["reports"], db, mapping)
    con = duckdb.connect(str(db), read_only=True)
    yield con
    con.close()


# --- Нормализация ---------------------------------------------------------

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("dbo.Orders", ("dbo", "Orders", True)),
        ("[dbo].[Orders]", ("dbo", "Orders", True)),
        ("  fin.Invoice  ", ("fin", "Invoice", True)),
        ("ReportDW.dbo.Orders", ("dbo", "Orders", True)),  # берём два последних
        ("TempStaging", ("(unknown)", "TempStaging", False)),
        ("", None),
        ("   ", None),
        (".", None),
    ],
)
def test_parse_table_ref(raw, expected):
    assert parse_table_ref(raw) == expected


def test_parse_table_list_dedupes_case_insensitively():
    result = parse_table_list("dbo.Orders; DBO.ORDERS ;dbo.Customers;;")
    assert len(result) == 2


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("/Finance/Monthly", "/Finance/Monthly"),
        ("\\Finance\\Monthly\\", "/Finance/Monthly"),
        ("Finance//Monthly", "/Finance/Monthly"),
        ("", "/"),
        (None, "/"),
        ("/", "/"),
    ],
)
def test_normalise_catalog_path(raw, expected):
    assert normalise_catalog_path(raw) == expected


def test_real_headers_from_customer_file_resolve():
    """Фактические заголовки из файла заказчика должны сопоставляться без правок.

    «Наименование отчета» пишется без «ё» — сопоставление это учитывает.
    """
    headers = ["№", "Наименование отчета", "Каталог", "Таблицы источники данных"]
    resolved = resolve_columns(headers, load_mapping().reports.columns)
    assert resolved["report_no"] == "№"
    assert resolved["report_name"] == "Наименование отчета"
    assert resolved["catalog_path"] == "Каталог"
    assert resolved["source_tables"] == "Таблицы источники данных"


def test_report_no_is_loaded(full_db):
    missing = full_db.execute(
        "SELECT COUNT(*) FROM dim_report WHERE report_no IS NULL"
    ).fetchone()[0]
    assert missing == 0


def test_raw_data_is_git_ignored():
    """Данные заказчика не должны попадать в репозиторий ни при каком раскладе."""
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "data/raw/*" in ignore
    assert "data/*.duckdb" in ignore
    assert "dist/" in ignore


# --- Целостность модели ---------------------------------------------------

def test_no_dangling_bridge_keys(full_db):
    dangling = full_db.execute(
        """
        SELECT COUNT(*) FROM bridge_report_table b
        LEFT JOIN dim_report r ON r.report_id = b.report_id
        LEFT JOIN dim_table  t ON t.table_id  = b.table_id
        WHERE r.report_id IS NULL OR t.table_id IS NULL
        """
    ).fetchone()[0]
    assert dangling == 0


def test_full_name_unique(full_db):
    dupes = full_db.execute(
        "SELECT COUNT(*) FROM (SELECT full_name FROM dim_table "
        "GROUP BY full_name HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert dupes == 0


def test_every_source_row_accounted_for(full_db):
    run = full_db.execute(
        "SELECT rows_read, rows_loaded, rows_rejected FROM etl_run"
    ).fetchone()
    assert run[0] == run[1] + run[2]


def test_exclusive_mb_never_exceeds_real_storage(full_db):
    """Ключевая защита от двойного учёта общих таблиц."""
    total = full_db.execute("SELECT COALESCE(SUM(total_mb), 0) FROM fact_table_size").fetchone()[0]
    exclusive = full_db.execute(
        "SELECT COALESCE(SUM(exclusive_mb), 0) FROM v_report_footprint"
    ).fetchone()[0]
    assert exclusive <= total + 1e-6, (
        f"exclusive_mb={exclusive} превышает реальный объём {total} — "
        "значит, общие таблицы посчитаны дважды"
    )


def test_gross_mb_is_at_least_exclusive(full_db):
    bad = full_db.execute(
        "SELECT COUNT(*) FROM v_report_footprint WHERE gross_mb < exclusive_mb - 1e-6"
    ).fetchone()[0]
    assert bad == 0


def test_exclusive_tables_belong_to_exactly_one_report(full_db):
    """exclusive_mb должен строиться ровно на таблицах с report_count = 1."""
    mismatch = full_db.execute(
        """
        WITH per_report AS (
            SELECT b.report_id, COALESCE(SUM(s.total_mb), 0) AS mb
            FROM bridge_report_table b
            JOIN (SELECT table_id FROM bridge_report_table
                  GROUP BY table_id HAVING COUNT(DISTINCT report_id) = 1) e
              ON e.table_id = b.table_id
            LEFT JOIN fact_table_size s ON s.table_id = b.table_id
            GROUP BY b.report_id
        )
        SELECT COUNT(*) FROM v_report_footprint f
        JOIN per_report p ON p.report_id = f.report_id
        WHERE ABS(p.mb - f.exclusive_mb) > 0.02
        """
    ).fetchone()[0]
    assert mismatch == 0


def test_size_coverage_within_bounds(full_db):
    bad = full_db.execute(
        "SELECT COUNT(*) FROM v_report_footprint "
        "WHERE size_coverage_pct IS NOT NULL "
        "AND (size_coverage_pct < 0 OR size_coverage_pct > 100)"
    ).fetchone()[0]
    assert bad == 0


def test_rejected_rows_are_logged(full_db):
    rejected = full_db.execute("SELECT rows_rejected FROM etl_run").fetchone()[0]
    logged = full_db.execute(
        "SELECT COUNT(*) FROM etl_reject WHERE reason = 'Пустое имя отчёта'"
    ).fetchone()[0]
    assert rejected == logged > 0, "строка с пустым именем должна попасть в etl_reject"


def test_report_without_sources_is_loaded(full_db):
    found = full_db.execute(
        "SELECT table_count FROM v_report_footprint WHERE report_name = 'Report Without Sources'"
    ).fetchone()
    assert found is not None and found[0] == 0


# --- Витрины на пустых фактах --------------------------------------------

@pytest.mark.parametrize("view", VIEWS)
def test_views_work_without_facts(bare_db, view):
    """Главное требование ТЗ: аналитика не падает до появления размеров и статистики."""
    bare_db.execute(f"SELECT * FROM {view}").fetchall()


def test_footprint_is_zero_without_sizes(bare_db):
    total = bare_db.execute("SELECT SUM(exclusive_mb) FROM v_report_footprint").fetchone()[0]
    assert total == 0


def test_quadrant_says_no_usage_data(bare_db):
    quadrants = {
        row[0] for row in bare_db.execute("SELECT DISTINCT quadrant FROM v_report_cost_value").fetchall()
    }
    assert quadrants == {"Нет данных об использовании"}


# --- Экспорт HTML ---------------------------------------------------------

def test_html_export_is_self_contained(full_db, tmp_path, paths):
    import re

    from reportsdb.export_html import export

    mapping = load_mapping()
    mapping.table_sizes.file = str(paths["sizes"])
    mapping.report_usage.file = str(paths["usage"])
    db = tmp_path / "export.duckdb"
    build(paths["reports"], db, mapping)

    out = export(db, tmp_path / "out.html")
    html = out.read_text(encoding="utf-8")

    assert "/*__DATA__*/null" not in html, "данные не подставились в шаблон"
    external = re.findall(r"""(?:src|href)\s*=\s*["']https?://""", html)
    assert not external, f"HTML тянет внешние ресурсы: {external}"
    assert "Отчётность SSRS" in html
