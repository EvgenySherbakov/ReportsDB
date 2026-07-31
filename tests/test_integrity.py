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
from reportsdb.config import SCHEMA_VERSION  # noqa: E402
from reportsdb.etl import build  # noqa: E402
from reportsdb.normalize import (  # noqa: E402
    join_folders,
    normalise_catalog_path,
    parse_bool,
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
    "v_network_overview",
    "v_report_duration",
    "v_report_overlap",
    "v_tables_catalog",
    "v_report_table_size",
    "v_rc_report_tables",
    "v_rc_report_routines",
    "v_rc_report_usage",
    "v_rc_report_retention",
    "v_rc_summary",
    "v_report_tables_summary",
]

# Каждое поле, приходящее из исходных файлов, обязано быть видно в аналитике —
# иначе колонку загрузили впустую. Проверяется тестом ниже.
FIELDS_IN_ANALYTICS = [
    "report_no", "report_name", "network", "plant",
    "folder_l1", "folder_l2", "folder_l3", "uses_view",
    "exec_count", "avg_duration_sec", "total_duration_sec",
    "percent_of_total", "exclusive_pct_of_db", "segment_count",
    "total_mb", "schema_name", "table_name",
    "object_kind", "kind_source", "retention_days", "retention_band", "usage_band",
    "tables_total_mb", "tables_exclusive_mb", "table_names",
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
    """Фактические заголовки файла отчётов сопоставляются без правок конфига.

    «Наименование отчета» пишется без «ё» — сопоставление это учитывает.
    """
    headers = [
        "№", "ТС", "Завод", "Каталог 1-го уровня", "Каталог 2-го уровня",
        "Каталог 3-го уровня", "Наименование отчета", "Используется view",
        "Таблицы источники данных", "Ср. дл. (сек)", "Кол-во обращений",
    ]
    resolved = resolve_columns(headers, load_mapping().reports.columns)
    assert resolved["report_no"] == "№"
    assert resolved["network"] == "ТС"
    assert resolved["plant"] == "Завод"
    assert resolved["folder_l1"] == "Каталог 1-го уровня"
    assert resolved["folder_l2"] == "Каталог 2-го уровня"
    assert resolved["folder_l3"] == "Каталог 3-го уровня"
    assert resolved["report_name"] == "Наименование отчета"
    assert resolved["uses_view"] == "Используется view"
    assert resolved["source_tables"] == "Таблицы источники данных"
    assert resolved["avg_duration_sec"] == "Ср. дл. (сек)"
    assert resolved["exec_count"] == "Кол-во обращений"


def test_segment_export_headers_resolve():
    """Заголовки выгрузки сегментов БД сопоставляются без правок конфига."""
    headers = ["№", "OWNER", "SEGMENT_NAME", "SEGMENT_TYPE", "SIZE_MB",
               "PERCENT_OF_TOTAL", "PERCENT_OF_SCHEMA"]
    resolved = resolve_columns(headers, load_mapping().table_sizes.columns)
    assert resolved["schema_name"] == "OWNER"
    assert resolved["table_name"] == "SEGMENT_NAME"
    assert resolved["segment_type"] == "SEGMENT_TYPE"
    assert resolved["total_mb"] == "SIZE_MB"
    assert resolved["percent_of_total"] == "PERCENT_OF_TOTAL"


@pytest.mark.parametrize(
    "levels,expected",
    [
        (("Финансы", "Месяц", "Продажи"), "/Финансы/Месяц/Продажи"),
        (("Финансы", "", ""), "/Финансы"),
        (("Финансы", None, "Продажи"), "/Финансы/Продажи"),  # пустой уровень пропускается
        ((None, None, None), "/"),
        (("A/B", "C", None), "/A-B/C"),  # слэш внутри уровня не ломает путь
    ],
)
def test_join_folders(levels, expected):
    assert join_folders(*levels) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("да", True), ("Да", True), ("ДА", True), ("yes", True), ("1", True), ("+", True),
        ("нет", False), ("Нет", False), ("no", False), ("0", False), ("-", False),
        ("", None), (None, None), ("может быть", None),  # непонятное — не False
    ],
)
def test_parse_bool(raw, expected):
    assert parse_bool(raw) is expected


def test_uses_view_and_org_dimensions_loaded(full_db):
    row = full_db.execute(
        "SELECT COUNT(*) FILTER (WHERE network IS NOT NULL), "
        "       COUNT(*) FILTER (WHERE plant IS NOT NULL), "
        "       COUNT(*) FILTER (WHERE uses_view IS NOT NULL) FROM dim_report"
    ).fetchone()
    assert all(v > 0 for v in row), "ТС, завод и признак view должны загружаться"


def test_same_report_name_kept_per_plant(full_db):
    """Одно имя отчёта у разных заводов — разные строки, а не одна."""
    collapsed = full_db.execute(
        "SELECT COUNT(*) FROM (SELECT report_name FROM dim_report "
        "GROUP BY report_name HAVING COUNT(DISTINCT plant) > 1 AND COUNT(*) = 1)"
    ).fetchone()[0]
    assert collapsed == 0


def test_segments_are_summed_not_overwritten(full_db):
    """Секции одной таблицы складываются, а не затирают друг друга."""
    multi = full_db.execute(
        "SELECT COUNT(*) FROM fact_table_size WHERE segment_count > 1"
    ).fetchone()[0]
    assert multi > 0, "в демо-данных есть секционированные таблицы"
    bad = full_db.execute(
        "SELECT COUNT(*) FROM fact_table_size WHERE segment_count > 1 AND total_mb IS NULL"
    ).fetchone()[0]
    assert bad == 0


def test_index_segments_are_not_counted_as_tables(full_db):
    """Индексные сегменты не должны попадать в размеры таблиц."""
    leaked = full_db.execute(
        "SELECT COUNT(*) FROM dim_table WHERE table_name ILIKE 'IDX!_%' ESCAPE '!'"
    ).fetchone()[0]
    assert leaked == 0


def test_usage_comes_from_main_file(full_db):
    """Частота и длительность приходят из основного файла, без отдельного."""
    filled = full_db.execute(
        "SELECT COUNT(*) FROM fact_report_usage WHERE exec_count IS NOT NULL"
    ).fetchone()[0]
    assert filled > 0


def test_duration_is_seconds_not_milliseconds(full_db):
    """Длительность хранится в секундах — как в исходном файле."""
    worst = full_db.execute(
        "SELECT MAX(avg_duration_sec) FROM fact_report_usage"
    ).fetchone()[0]
    assert worst is not None and worst < 100000, (
        "значения похожи на миллисекунды — проверьте перевод единиц"
    )


def test_view_reports_get_low_confidence(full_db):
    """Отчёт через view не может получить высокую уверенность вывода."""
    bad = full_db.execute(
        "SELECT COUNT(*) FROM v_decommission_candidates "
        "WHERE uses_view AND confidence = 'Высокая'"
    ).fetchone()[0]
    assert bad == 0


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


def test_schema_version_is_written(full_db):
    """Версия структуры пишется в журнал — по ней приложение узнаёт старую базу."""
    version = full_db.execute(
        "SELECT schema_version FROM etl_run ORDER BY run_id DESC LIMIT 1"
    ).fetchone()[0]
    assert version == SCHEMA_VERSION


def test_schema_version_matches_view_set():
    """Забыли поднять SCHEMA_VERSION после правки SQL — тест напомнит.

    Сверяем со слепком: любое изменение схемы или витрин обязано
    сопровождаться увеличением версии, иначе у пользователей останется
    несовместимая база и падение вместо понятного сообщения.
    """
    import hashlib

    digest = hashlib.sha256()
    for name in ("01_schema.sql", "02_views.sql"):
        digest.update((ROOT / "sql" / name).read_bytes())
    # Слепок SQL на момент SCHEMA_VERSION = 7.
    expected = "53d506"  # первые 6 знаков; обновлять вместе с версией
    actual = digest.hexdigest()[:6]
    assert actual == expected, (
        f"SQL изменился (слепок {actual}, ожидался {expected}). "
        f"Поднимите SCHEMA_VERSION в src/reportsdb/config.py и обновите слепок "
        f"в этом тесте."
    )


def test_every_source_row_accounted_for(full_db):
    run = full_db.execute(
        "SELECT rows_read, rows_loaded, rows_rejected FROM etl_run"
    ).fetchone()
    assert run[0] == run[1] + run[2]


def test_exclusive_mb_never_exceeds_real_storage(full_db):
    """Ключевая защита от двойного учёта общих таблиц.

    Сравнение с полным объёмом всех заводов: отчёты живут на конкретных
    площадках, поэтому их сумма заведомо не больше.
    """
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
            -- Через резолвер: размер берётся для завода отчёта. Прямое
            -- соединение с fact_table_size размножило бы строки по заводам.
            LEFT JOIN v_report_table_size s
                   ON s.table_id = b.table_id AND s.report_id = b.report_id
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


def test_every_loaded_field_is_visible_in_analytics(full_db):
    """Ни одно загруженное поле не должно остаться только в таблицах модели."""
    views = [
        row[0] for row in full_db.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' AND table_type = 'VIEW'"
        ).fetchall()
    ]
    exposed: set[str] = set()
    for view in views:
        exposed |= {row[0] for row in full_db.execute(f"DESCRIBE {view}").fetchall()}

    missing = [f for f in FIELDS_IN_ANALYTICS if f not in exposed]
    assert not missing, f"поля загружены, но не попали ни в одну витрину: {missing}"


def test_catalog_overview_keeps_all_three_levels(full_db):
    columns = {row[0] for row in full_db.execute("DESCRIBE v_catalog_overview").fetchall()}
    assert {"folder_l1", "folder_l2", "folder_l3"} <= columns
    deep = full_db.execute(
        "SELECT COUNT(*) FROM v_catalog_overview WHERE folder_l3 <> ''"
    ).fetchone()[0]
    assert deep > 0, "третий уровень каталога должен доходить до сводки"


def test_duration_bands_cover_all_reports_with_data(full_db):
    """У каждого отчёта с длительностью есть непустая категория."""
    bad = full_db.execute(
        "SELECT COUNT(*) FROM v_report_duration "
        "WHERE avg_duration_sec IS NOT NULL AND duration_band = 'Нет данных'"
    ).fetchone()[0]
    assert bad == 0


def test_total_duration_is_execs_times_average(full_db):
    mismatch = full_db.execute(
        """
        SELECT COUNT(*) FROM v_report_duration
        WHERE exec_count IS NOT NULL AND avg_duration_sec IS NOT NULL
          AND ABS(total_duration_sec - exec_count * avg_duration_sec) > 0.2
        """
    ).fetchone()[0]
    assert mismatch == 0


# --- Пять основных представлений в разрезе РЦ ----------------------------

def test_tables_catalog_is_exactly_the_size_file(full_db):
    """Таблица №1 — ровно строки файла размеров: ни потерь, ни добавок.

    Список таблиц БД берётся только из файла размеров. Объекты, упомянутые
    в отчётах, но отсутствующие в файле, в него не подмешиваются: мост
    «отчёт ↔ таблица» лишь ссылается на этот список.
    """
    sizes = full_db.execute("SELECT COUNT(*) FROM fact_table_size").fetchone()[0]
    in_catalog = full_db.execute("SELECT COUNT(*) FROM v_tables_catalog").fetchone()[0]
    assert in_catalog == sizes


def test_tables_catalog_adds_nothing_from_reports(full_db):
    """В каталоге нет ни одной таблицы, которой нет в файле размеров.

    В демо-данных отчёты заведомо ссылаются на объекты без замера (мусорные
    имена, view, временные) — они обязаны остаться за пределами №1.
    """
    dangling = full_db.execute(
        "SELECT COUNT(*) FROM dim_table t "
        "WHERE EXISTS (SELECT 1 FROM bridge_report_table b WHERE b.table_id = t.table_id) "
        "  AND NOT EXISTS (SELECT 1 FROM fact_table_size s WHERE s.table_id = t.table_id)"
    ).fetchone()[0]
    assert dangling > 0, "проверка бессмысленна, если таких ссылок нет вовсе"

    leaked = full_db.execute(
        "SELECT COUNT(*) FROM v_tables_catalog c "
        "WHERE NOT EXISTS (SELECT 1 FROM fact_table_size s JOIN dim_table t "
        "                  ON t.table_id = s.table_id WHERE t.full_name = c.full_name)"
    ).fetchone()[0]
    assert leaked == 0


def test_tables_catalog_is_independent_of_reports(full_db):
    """Таблица попадает в №1, даже если на неё не ссылается ни один отчёт."""
    orphans = full_db.execute(
        "SELECT COUNT(*) FROM v_tables_catalog WHERE report_count = 0"
    ).fetchone()[0]
    assert orphans > 0, "в демо-данных есть таблицы вне отчётов — они обязаны быть видны"


def test_tables_catalog_shows_nothing_twice(full_db):
    """Строка на пару «таблица + завод», без дублей."""
    dupes = full_db.execute(
        "SELECT COUNT(*) FROM (SELECT network, plant, full_name FROM v_tables_catalog "
        "GROUP BY 1, 2, 3 HAVING COUNT(*) > 1)"
    ).fetchone()[0]
    assert dupes == 0


def test_table_size_differs_per_plant(full_db):
    """Размер ведётся на завод: одна таблица на разных заводах весит своё."""
    varying = full_db.execute(
        "SELECT COUNT(*) FROM (SELECT table_id FROM fact_table_size "
        "GROUP BY table_id HAVING COUNT(DISTINCT plant) > 1 "
        "AND COUNT(DISTINCT total_mb) > 1)"
    ).fetchone()[0]
    assert varying > 0


def test_size_resolver_does_not_multiply_rows(full_db):
    """Резолвер обязан давать ровно одну строку размера на связь.

    Прямое соединение с fact_table_size по table_id размножило бы строки по
    числу заводов и завысило суммы в разы.
    """
    links = full_db.execute("SELECT COUNT(*) FROM bridge_report_table").fetchone()[0]
    resolved = full_db.execute("SELECT COUNT(*) FROM v_report_table_size").fetchone()[0]
    assert resolved == links


def test_size_resolver_picks_the_plant_of_the_report(full_db):
    """Отчёту достаётся размер его собственного завода, а не чужого."""
    wrong = full_db.execute(
        """
        SELECT COUNT(*) FROM v_report_table_size v
        JOIN dim_report r ON r.report_id = v.report_id
        JOIN fact_table_size s
          ON s.table_id = v.table_id
         AND s.network = COALESCE(r.network, '(не указана)')
         AND s.plant   = COALESCE(r.plant, '(не указан)')
        WHERE v.size_is_plant_specific AND v.total_mb IS DISTINCT FROM s.total_mb
        """
    ).fetchone()[0]
    assert wrong == 0


def test_rc_report_tables_contain_only_real_tables(full_db):
    """Таблица №2 — без view, mat.view, временных и процедур."""
    kinds = {
        row[0] for row in full_db.execute(
            "SELECT DISTINCT object_kind FROM v_rc_report_tables"
        ).fetchall()
    }
    assert kinds == {"TABLE"}, f"в таблицу №2 просочились: {kinds - {'TABLE'}}"


def test_rc_report_tables_is_one_to_many(full_db):
    """У отчёта несколько таблиц — связь не схлопнута в одну строку."""
    max_tables = full_db.execute(
        "SELECT MAX(n) FROM (SELECT COUNT(*) AS n FROM v_rc_report_tables "
        "GROUP BY network, plant, report_name)"
    ).fetchone()[0]
    assert max_tables > 1


def test_rc_routines_contain_only_routines(full_db):
    """Таблица №3 — только функции и процедуры."""
    kinds = {
        row[0] for row in full_db.execute(
            "SELECT DISTINCT t.object_kind FROM v_rc_report_routines v "
            "JOIN dim_table t ON t.full_name = v.routine_full_name"
        ).fetchall()
    }
    assert kinds == {"ROUTINE"}


def test_rc_usage_covers_every_report(full_db):
    """Таблица №4 — строка на каждый отчёт, даже без статистики."""
    reports = full_db.execute("SELECT COUNT(*) FROM dim_report").fetchone()[0]
    rows = full_db.execute("SELECT COUNT(*) FROM v_rc_report_usage").fetchone()[0]
    assert rows == reports


def test_rc_retention_bands_match_days(full_db):
    """Границы 30/45 расставлены ровно так, как просил заказчик."""
    bad = full_db.execute(
        """
        SELECT COUNT(*) FROM v_rc_report_retention
        WHERE (retention_days <= 30 AND retention_band <> 'До 30 дней')
           OR (retention_days > 30 AND retention_days <= 45
               AND retention_band <> 'От 31 до 45 дней')
           OR (retention_days > 45 AND retention_band <> 'Более 45 дней')
           OR (retention_days IS NULL AND retention_band <> 'Не задана')
        """
    ).fetchone()[0]
    assert bad == 0


def test_object_kinds_are_loaded_from_columns(full_db):
    """Типы объектов приходят из отдельных колонок, а не угадываются."""
    rows = dict(full_db.execute(
        "SELECT object_kind, COUNT(*) FROM dim_table GROUP BY 1"
    ).fetchall())
    for kind in ("TABLE", "VIEW", "MATERIALIZED VIEW", "TEMP", "ROUTINE"):
        assert rows.get(kind, 0) > 0, f"нет объектов типа {kind}"

    guessed = full_db.execute(
        "SELECT COUNT(*) FROM dim_table WHERE object_kind <> 'TABLE' "
        "AND kind_source <> 'колонка'"
    ).fetchone()[0]
    assert guessed == 0, "тип, отличный от таблицы, должен приходить из колонки"


def test_report_summary_totals_match_its_tables(full_db):
    """«Отчёт ссылается на такие-то таблицы, которые весят столько-то» —
    сумма в сводке обязана совпадать с суммой по её же таблицам."""
    mismatch = full_db.execute(
        """
        WITH per_report AS (
            SELECT b.report_id, ROUND(COALESCE(SUM(s.total_mb), 0), 2) AS mb,
                   COUNT(*) AS n
            FROM bridge_report_table b
            JOIN dim_table t ON t.table_id = b.table_id
            -- Только через резолвер: он даёт ровно одну строку размера на
            -- связь, для завода этого отчёта.
            LEFT JOIN v_report_table_size s
                   ON s.table_id = b.table_id AND s.report_id = b.report_id
            WHERE t.object_kind = 'TABLE'
            GROUP BY b.report_id
        )
        SELECT COUNT(*) FROM v_report_tables_summary v
        JOIN per_report p ON p.report_id = v.report_id
        WHERE ABS(p.mb - v.tables_total_mb) > 0.02 OR p.n <> v.table_count
        """
    ).fetchone()[0]
    assert mismatch == 0


def test_report_summary_lists_table_names(full_db):
    """В сводке перечислены сами таблицы, а не только их число."""
    row = full_db.execute(
        "SELECT table_count, table_names FROM v_report_tables_summary "
        "WHERE table_count > 1 LIMIT 1"
    ).fetchone()
    assert row and row[1] and row[1].count(",") == row[0] - 1


def test_report_summary_covers_every_report(full_db):
    reports = full_db.execute("SELECT COUNT(*) FROM dim_report").fetchone()[0]
    rows = full_db.execute("SELECT COUNT(*) FROM v_report_tables_summary").fetchone()[0]
    assert rows == reports


def test_object_kind_masks_are_off_by_default():
    """Маски имён по умолчанию пусты: угаданный тип молча выкинул бы таблицу."""
    assert load_mapping().reports.object_patterns == {}


# --- Витрины на пустых фактах --------------------------------------------

@pytest.mark.parametrize("view", VIEWS)
def test_views_work_without_facts(bare_db, view):
    """Главное требование ТЗ: аналитика не падает до появления размеров и статистики."""
    bare_db.execute(f"SELECT * FROM {view}").fetchall()


def test_footprint_is_zero_without_sizes(bare_db):
    total = bare_db.execute("SELECT SUM(exclusive_mb) FROM v_report_footprint").fetchone()[0]
    assert total == 0


def test_footprint_survives_without_size_file(bare_db):
    """Без файла размеров объёмы нулевые, но витрины считаются."""
    rows = bare_db.execute(
        "SELECT COUNT(*), COUNT(*) FILTER (WHERE exclusive_mb = 0) FROM v_report_footprint"
    ).fetchone()
    assert rows[0] == rows[1] > 0


def test_quadrants_use_usage_from_main_file(bare_db):
    """Статистика лежит в основном файле, поэтому квадранты считаются и без
    отдельного файла статистики."""
    quadrants = {
        row[0] for row in bare_db.execute(
            "SELECT DISTINCT quadrant FROM v_report_cost_value"
        ).fetchall()
    }
    assert quadrants - {"Нет данных об использовании"}


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
    # Организационный разрез и время должны доехать до автономного файла.
    for key in ("network", "plant", "uses_view", "durations", "duration_band"):
        assert key in html, f"в HTML не попало поле {key}"
    external = re.findall(r"""(?:src|href)\s*=\s*["']https?://""", html)
    assert not external, f"HTML тянет внешние ресурсы: {external}"
    assert "Отчётность SSRS" in html
