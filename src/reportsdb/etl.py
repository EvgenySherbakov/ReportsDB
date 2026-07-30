"""ETL: Excel → DuckDB. Полная пересборка. См. docs/TZ.md, раздел 7."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

from .config import (
    DB_PATH,
    RAW_DIR,
    SQL_DIR,
    VERSION,
    Mapping,
    SectionConfig,
    load_mapping,
    resolve_columns,
)
from .excel import read_sheet
from .normalize import (
    clean_text,
    full_name_key,
    normalise_catalog_path,
    parse_table_list,
    split_folders,
    to_number,
)


@dataclass
class LoadStats:
    rows_read: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0
    tables: int = 0
    links: int = 0
    unparsed_refs: int = 0
    sizes_loaded: int = 0
    sizes_unmatched: list[str] = field(default_factory=list)
    usage_loaded: int = 0
    usage_unmatched: list[str] = field(default_factory=list)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    con.execute(path.read_text(encoding="utf-8"))


def _cell(row: pd.Series, column: str | None):
    return row[column] if column else None


def build(
    source: Path,
    db_path: Path = DB_PATH,
    mapping: Mapping | None = None,
) -> LoadStats:
    """Пересобирает БД с нуля из исходного файла."""
    mapping = mapping or load_mapping()
    stats = LoadStats()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        shutil.move(str(db_path), str(db_path.with_suffix(db_path.suffix + ".bak")))

    con = duckdb.connect(str(db_path))
    try:
        _run_sql_file(con, SQL_DIR / "01_schema.sql")
        _load_reports(con, source, mapping.reports, stats)
        _load_table_sizes(con, mapping.table_sizes, stats)
        _load_report_usage(con, mapping.report_usage, stats)
        _run_sql_file(con, SQL_DIR / "02_views.sql")

        con.execute(
            """
            INSERT INTO etl_run (run_id, started_at, source_file, source_sha256,
                                 rows_read, rows_loaded, rows_rejected, tool_version)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                datetime.now(),
                source.name,
                _sha256(source),
                stats.rows_read,
                stats.rows_loaded,
                stats.rows_rejected,
                VERSION,
            ],
        )
    finally:
        con.close()
    return stats


def _load_reports(
    con: duckdb.DuckDBPyConnection,
    source: Path,
    section: SectionConfig,
    stats: LoadStats,
) -> None:
    df = read_sheet(source, section)
    stats.rows_read = len(df)

    cols = resolve_columns(list(df.columns), section.columns)
    if not cols.get("report_name"):
        raise SystemExit(
            "В файле не найдена колонка с именем отчёта.\n"
            f"Заголовки файла: {list(df.columns)}\n"
            "Запустите `profile` и поправьте config/mapping.yml."
        )

    reports: list[tuple] = []
    rejects: list[tuple] = []
    tables: dict[str, tuple[int, str, str, bool]] = {}  # key → (id, schema, name, ok)
    links: set[tuple[int, int]] = set()
    seen_reports: dict[tuple[str, str], int] = {}

    for position, (_, row) in enumerate(df.iterrows()):
        source_row = position + section.header_row + 2  # +заголовок, +1-based Excel

        name = clean_text(_cell(row, cols["report_name"]))
        if name is None:
            stats.rows_rejected += 1
            rejects.append((1, source_row, "Пустое имя отчёта", str(row.to_dict())[:500]))
            continue

        path = normalise_catalog_path(_cell(row, cols.get("catalog_path")))
        key = (path, name)
        if key in seen_reports:
            report_id = seen_reports[key]
            rejects.append(
                (1, source_row, "Дубликат отчёта: строки объединены", f"{path}/{name}")
            )
        else:
            report_id = len(seen_reports) + 1
            seen_reports[key] = report_id
            l1, l2, l3, depth = split_folders(path)
            reports.append(
                (
                    report_id,
                    clean_text(_cell(row, cols.get("report_no"))),
                    name,
                    path,
                    l1,
                    l2,
                    l3,
                    depth,
                    clean_text(_cell(row, cols.get("description"))),
                    clean_text(_cell(row, cols.get("owner"))),
                    source_row,
                )
            )
            stats.rows_loaded += 1

        for schema_name, table_name, ok in parse_table_list(
            _cell(row, cols.get("source_tables")), section.table_separator
        ):
            fkey = full_name_key(schema_name, table_name)
            if fkey not in tables:
                tables[fkey] = (len(tables) + 1, schema_name, table_name, ok)
                if not ok:
                    stats.unparsed_refs += 1
            links.add((report_id, tables[fkey][0]))

    _insert(
        con,
        "dim_report",
        reports,
        ["report_id", "report_no", "report_name", "catalog_path", "folder_l1",
         "folder_l2", "folder_l3", "folder_depth", "description", "owner",
         "source_row"],
    )
    _insert(
        con,
        "dim_table",
        [(tid, schema, name, key, ok) for key, (tid, schema, name, ok) in tables.items()],
        ["table_id", "schema_name", "table_name", "full_name", "is_parsed_ok"],
    )
    _insert(con, "bridge_report_table", sorted(links), ["report_id", "table_id"])
    _insert(con, "etl_reject", rejects, ["run_id", "source_row", "reason", "payload"])

    stats.tables = len(tables)
    stats.links = len(links)


def _load_table_sizes(
    con: duckdb.DuckDBPyConnection, section: SectionConfig, stats: LoadStats
) -> None:
    path = _section_file(section)
    if path is None:
        return

    df = read_sheet(path, section)
    cols = resolve_columns(list(df.columns), section.columns)
    known = {
        key: tid
        for tid, key in con.execute("SELECT table_id, full_name FROM dim_table").fetchall()
    }

    rows: list[tuple] = []
    seen: set[int] = set()
    for _, row in df.iterrows():
        full = clean_text(_cell(row, cols.get("full_name")))
        if full is None:
            schema_name = clean_text(_cell(row, cols.get("schema_name")))
            table_name = clean_text(_cell(row, cols.get("table_name")))
            if not table_name:
                continue
            full = f"{schema_name}.{table_name}" if schema_name else table_name

        key = full.replace("[", "").replace("]", "").strip().lower()
        table_id = known.get(key)
        if table_id is None:
            stats.sizes_unmatched.append(full)
            continue
        if table_id in seen:
            continue
        seen.add(table_id)

        data_mb = to_number(_cell(row, cols.get("data_mb")))
        index_mb = to_number(_cell(row, cols.get("index_mb")))
        total_mb = to_number(_cell(row, cols.get("total_mb")))
        if total_mb is None and (data_mb is not None or index_mb is not None):
            total_mb = (data_mb or 0.0) + (index_mb or 0.0)

        rows.append(
            (
                table_id,
                _as_int(to_number(_cell(row, cols.get("row_count")))),
                data_mb,
                index_mb,
                total_mb,
                clean_text(_cell(row, cols.get("measured_at"))),
            )
        )

    _insert(
        con,
        "fact_table_size",
        rows,
        ["table_id", "row_count", "data_mb", "index_mb", "total_mb", "measured_at"],
        casts={"measured_at": "TRY_CAST(? AS DATE)"},
    )
    stats.sizes_loaded = len(rows)


def _load_report_usage(
    con: duckdb.DuckDBPyConnection, section: SectionConfig, stats: LoadStats
) -> None:
    path = _section_file(section)
    if path is None:
        return

    df = read_sheet(path, section)
    cols = resolve_columns(list(df.columns), section.columns)
    if not cols.get("report_name"):
        raise SystemExit("В файле частоты использования нет колонки с именем отчёта.")

    rows_db = con.execute(
        "SELECT report_id, report_name, catalog_path FROM dim_report"
    ).fetchall()
    by_path = {(p.lower(), n.lower()): rid for rid, n, p in rows_db}
    by_name: dict[str, list[int]] = {}
    for rid, name, _ in rows_db:
        by_name.setdefault(name.lower(), []).append(rid)

    rows: list[tuple] = []
    seen: set[int] = set()
    for _, row in df.iterrows():
        name = clean_text(_cell(row, cols["report_name"]))
        if name is None:
            continue

        report_id = None
        raw_path = clean_text(_cell(row, cols.get("catalog_path")))
        if raw_path:
            report_id = by_path.get((normalise_catalog_path(raw_path).lower(), name.lower()))
        if report_id is None:
            candidates = by_name.get(name.lower(), [])
            # Неоднозначное имя без пути сопоставлять нельзя — данные ушли бы не туда.
            report_id = candidates[0] if len(candidates) == 1 else None
        if report_id is None:
            stats.usage_unmatched.append(name)
            continue
        if report_id in seen:
            continue
        seen.add(report_id)

        rows.append(
            (
                report_id,
                _as_int(to_number(_cell(row, cols.get("exec_count")))),
                _as_int(to_number(_cell(row, cols.get("distinct_users")))),
                to_number(_cell(row, cols.get("avg_duration_ms"))),
                clean_text(_cell(row, cols.get("last_executed_at"))),
                clean_text(_cell(row, cols.get("period_start"))),
                clean_text(_cell(row, cols.get("period_end"))),
            )
        )

    _insert(
        con,
        "fact_report_usage",
        rows,
        ["report_id", "exec_count", "distinct_users", "avg_duration_ms",
         "last_executed_at", "period_start", "period_end"],
        casts={
            "last_executed_at": "TRY_CAST(? AS DATE)",
            "period_start": "TRY_CAST(? AS DATE)",
            "period_end": "TRY_CAST(? AS DATE)",
        },
    )
    stats.usage_loaded = len(rows)


def _section_file(section: SectionConfig) -> Path | None:
    if not section.file:
        return None
    path = Path(section.file)
    if not path.is_absolute():
        path = RAW_DIR / path
    if not path.exists():
        raise SystemExit(f"Файл из config/mapping.yml не найден: {path}")
    return path


def _as_int(value: float | None) -> int | None:
    return None if value is None else int(value)


def _insert(
    con: duckdb.DuckDBPyConnection,
    table: str,
    rows: list[tuple],
    columns: list[str],
    casts: dict[str, str] | None = None,
) -> None:
    if not rows:
        return
    casts = casts or {}
    placeholders = ", ".join(casts.get(col, "?") for col in columns)
    con.executemany(
        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})", rows
    )


def print_summary(stats: LoadStats) -> None:
    print("\nЗагрузка завершена:")
    print(f"  строк прочитано : {stats.rows_read}")
    print(f"  отчётов загружено: {stats.rows_loaded}")
    print(f"  строк отброшено  : {stats.rows_rejected}")
    print(f"  уникальных таблиц: {stats.tables}")
    print(f"  связей отчёт↔таблица: {stats.links}")
    if stats.unparsed_refs:
        print(f"  !  ссылок без схемы: {stats.unparsed_refs} (схема = '(unknown)')")
    if stats.sizes_loaded or stats.sizes_unmatched:
        print(f"  размеров таблиц  : {stats.sizes_loaded}")
        if stats.sizes_unmatched:
            preview = ", ".join(stats.sizes_unmatched[:5])
            print(f"  !  размеры без совпадения: {len(stats.sizes_unmatched)} ({preview}…)")
    if stats.usage_loaded or stats.usage_unmatched:
        print(f"  строк использования: {stats.usage_loaded}")
        if stats.usage_unmatched:
            preview = ", ".join(stats.usage_unmatched[:5])
            print(f"  !  использование без совпадения: {len(stats.usage_unmatched)} ({preview}…)")
    if stats.rows_rejected:
        print("\n  Причины отбраковки — в таблице etl_reject.")
