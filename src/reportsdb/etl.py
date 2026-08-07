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
    SCHEMA_VERSION,
    SQL_DIR,
    VERSION,
    Mapping,
    SectionConfig,
    load_mapping,
    resolve_columns,
)
from .excel import read_all, read_sheet
from .normalize import (
    clean_text,
    full_name_key,
    match_object_kind,
    join_folders,
    normalise_catalog_path,
    parse_bool,
    parse_table_list,
    parse_table_ref,
    split_folders,
    to_number,
)


# Колонка файла → тип объекта. Порядок важен: явная колонка перекрывает
# и маску, и умолчание, поэтому список таблиц обрабатывается первым.
SOURCE_COLUMNS = [
    ("source_tables", "TABLE"),
    ("source_views", "VIEW"),
    ("source_matviews", "MATERIALIZED VIEW"),
    ("source_temp_tables", "TEMP"),
    ("source_routines", "ROUTINE"),
]

# Чем выше приоритет, тем надёжнее источник типа объекта.
KIND_PRIORITY = {"по умолчанию": 0, "маска": 1, "колонка": 2}

# Чем выше приоритет, тем надёжнее источник схемы. «Ссылка» — схема была в
# исходном тексте, «файл размеров» — восстановлена по уникальному совпадению
# имени, «не определена» — взять неоткуда.
SCHEMA_PRIORITY = {"не определена": 0, "файл размеров": 1, "ссылка": 2}


@dataclass
class MatchReport:
    """Почему строки вспомогательного файла не легли на отчёты.

    Одно число «не сопоставилось N строк» бесполезно: причины требуют разных
    действий. Имени нет в базе вовсе — не тот файл или не те заводы загружены;
    ТС и Завод из файла нет в каталоге — разошлась запись кода завода, и
    сопоставление свалилось на «имя, встречающееся в базе один раз»; имя есть,
    но у него тёзки, а каталог не совпал — нужен каталог или разрез.
    """

    #: строк файла, где есть и наименование, и значение
    rows: int = 0
    #: строк, для которых отчёт нашёлся
    matched_rows: int = 0
    #: имени нет в каталоге отчётов ни на одном заводе
    name_unknown: list[str] = field(default_factory=list)
    #: имя в каталоге есть, но ТС и Завода из файла там нет
    rc_unknown: list[str] = field(default_factory=list)
    #: имя есть, тёзки есть, каталог не совпал — выбрать не из чего
    ambiguous: list[str] = field(default_factory=list)
    #: «ТС / Завод» из файла → сколько строк
    file_pairs: dict[str, int] = field(default_factory=dict)
    #: те из них, которых нет в каталоге отчётов
    unknown_pairs: dict[str, int] = field(default_factory=dict)
    #: «ТС / Завод», которые есть в каталоге отчётов
    db_pairs: list[str] = field(default_factory=list)

    @property
    def unmatched_rows(self) -> int:
        return self.rows - self.matched_rows


@dataclass
class LoadStats:
    rows_read: int = 0
    rows_loaded: int = 0
    rows_rejected: int = 0
    # Сколько файлов отчётов склеено в эту загрузку (по файлу на завод).
    source_files: int = 1
    size_files: int = 0
    tables: int = 0
    links: int = 0
    unparsed_refs: int = 0
    # Ссылка была без схемы, но схема нашлась по уникальному совпадению имени
    # в файле размеров — там перечислены все таблицы БД независимо от отчётов.
    schema_recovered_tables: int = 0
    segments_skipped: int = 0
    objects_by_kind: dict[str, int] = field(default_factory=dict)
    sizes_loaded: int = 0
    tables_only_in_sizes: int = 0
    size_plants: int = 0
    # Колонки типа сегмента в файле нет — отфильтровать индексы невозможно.
    segment_type_column_missing: bool = False
    # Колонка есть, но ячейка пуста: строка засчитана как таблица.
    segments_without_type: int = 0
    # Файлов размеров несколько, а колонки завода нет — размеры заводов
    # сложатся в один и никакой ошибки при этом не будет.
    size_files_without_plant: bool = False
    usage_loaded: int = 0
    usage_unmatched: list[str] = field(default_factory=list)
    usage_match: MatchReport = field(default_factory=MatchReport)
    # Сколько отчётов (по report_id, не по строкам файла) получили текст
    # SQL-запроса — одна строка файла может лечь сразу на несколько report_id,
    # если то же наименование заведено на нескольких заводах.
    sql_loaded: int = 0
    sql_unmatched: list[str] = field(default_factory=list)
    sql_match: MatchReport = field(default_factory=MatchReport)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_all(paths: list[Path]) -> str:
    """Общий слепок набора файлов — по именам и содержимому, в порядке имён."""
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda p: p.name):
        digest.update(path.name.encode("utf-8"))
        digest.update(_sha256(path).encode("ascii"))
    return digest.hexdigest()


def _run_sql_file(con: duckdb.DuckDBPyConnection, path: Path) -> None:
    con.execute(path.read_text(encoding="utf-8"))


def _cell(row: pd.Series, column: str | None):
    return row[column] if column else None


def build(
    source: Path | list[Path],
    db_path: Path = DB_PATH,
    mapping: Mapping | None = None,
) -> LoadStats:
    """Пересобирает БД с нуля из исходных файлов.

    `source` — файл отчётов или список файлов: данные приходят по файлу на
    завод. Пересборка всегда полная: одна загрузка = вся база из перечисленных
    файлов, никакого состояния «половина заводов свежая, половина старая».
    """
    mapping = mapping or load_mapping()
    sources = [source] if isinstance(source, Path) else list(source)
    if not sources:
        raise SystemExit("Не указан ни один файл отчётов.")
    stats = LoadStats()

    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        shutil.move(str(db_path), str(db_path.with_suffix(db_path.suffix + ".bak")))

    # Список таблиц БД существует независимо от отчётов (файл размеров
    # содержит все таблицы), поэтому по нему можно восстановить схему для
    # ссылок отчётов, где схема не указана. Индекс строится до загрузки
    # отчётов — файл читается отдельно от основного прохода _load_table_sizes,
    # чтобы не завязывать один шаг на внутреннее устройство другого.
    sizes_index = _sizes_index(mapping.table_sizes)

    con = duckdb.connect(str(db_path))
    try:
        _run_sql_file(con, SQL_DIR / "01_schema.sql")
        _load_reports(con, sources, mapping.reports, stats, sizes_index)
        _load_table_sizes(con, mapping.table_sizes, stats)
        _load_report_usage(con, mapping.report_usage, stats)
        _load_report_sql(con, mapping.report_sql, stats)
        _run_sql_file(con, SQL_DIR / "02_views.sql")

        con.execute(
            """
            INSERT INTO etl_run (run_id, started_at, source_file, source_sha256,
                                 rows_read, rows_loaded, rows_rejected, tool_version,
                                 schema_version)
            VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                datetime.now(),
                ", ".join(p.name for p in sources),
                # Слепок всех файлов сразу: подмена любого из них меняет его.
                _sha256_all(sources),
                stats.rows_read,
                stats.rows_loaded,
                stats.rows_rejected,
                VERSION,
                SCHEMA_VERSION,
            ],
        )
    finally:
        con.close()
    return stats


@dataclass
class ClearResult:
    """Что именно удалила очистка — чтобы отчитаться перед пользователем."""

    freed_bytes: int = 0
    backup_removed: bool = False
    raw_files_left: int = 0


def clear(db_path: Path = DB_PATH, keep_backup: bool = False) -> ClearResult:
    """Стирает все данные, оставляя пустую базу нужной структуры.

    Пустая база, а не удалённый файл: страницы аналитики продолжают
    открываться и честно показывают нули, а не падают на «база не найдена».
    Версия структуры при этом остаётся текущей, поэтому предупреждения
    «база собрана прежней версией» тоже не возникает.

    Резервная копия по умолчанию **удаляется вместе с базой**. Очистку
    просят, когда рабочих данных на машине быть не должно, а `.bak` рядом с
    пустой базой сохранил бы ровно то, что просили стереть. Кому нужна
    страховка — `keep_backup=True`.

    Исходные файлы в `data/raw/` не трогаются: это отдельные файлы
    пользователя, а не содержимое базы. Их число возвращается, чтобы
    интерфейс мог сказать об этом прямо.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    backup = db_path.with_suffix(db_path.suffix + ".bak")

    result = ClearResult()
    if db_path.exists():
        result.freed_bytes += db_path.stat().st_size
        db_path.unlink()
    if backup.exists():
        if keep_backup:
            result.backup_removed = False
        else:
            result.freed_bytes += backup.stat().st_size
            backup.unlink()
            result.backup_removed = True

    con = duckdb.connect(str(db_path))
    try:
        _run_sql_file(con, SQL_DIR / "01_schema.sql")
        _run_sql_file(con, SQL_DIR / "02_views.sql")
        # Запись в журнал: иначе по базе не отличить «только что очищена» от
        # «никогда не собиралась», а это разные ситуации.
        con.execute(
            """
            INSERT INTO etl_run (run_id, started_at, source_file, source_sha256,
                                 rows_read, rows_loaded, rows_rejected, tool_version,
                                 schema_version)
            VALUES (1, ?, ?, NULL, 0, 0, 0, ?, ?)
            """,
            [datetime.now(), "(база очищена)", VERSION, SCHEMA_VERSION],
        )
    finally:
        con.close()

    if RAW_DIR.exists():
        result.raw_files_left = sum(1 for p in RAW_DIR.iterdir() if p.is_file())
    return result


@dataclass
class SizesIndex:
    """Что известно про объекты из файла размеров ещё до разбора отчётов."""

    #: имя таблицы (в нижнем регистре) → схема, если она единственная
    schema_by_name: dict[str, str] = field(default_factory=dict)
    #: все имена из файла размеров — у этих объектов есть сегменты в БД
    stored_names: set[str] = field(default_factory=set)


def _sizes_index(section: SectionConfig) -> SizesIndex:
    """Схемы и имена объектов из файла размеров.

    **Схема.** Имя таблицы → её схема, если та единственная. В файле размеров
    перечислены все таблицы БД со схемами, независимо от того, ссылается ли на
    них хоть один отчёт. Если одно и то же имя встречается под разными схемами
    (например, «Orders» есть и в dbo, и в sales), восстановить схему нельзя —
    такое имя в индекс не попадает, и ссылка на него в отчёте останется
    «(unknown)». Индекс строится сразу по всем файлам размеров: имя,
    однозначное в одном файле, но встречающееся под другой схемой в файле
    соседнего завода, — неоднозначно.

    **Имена.** Отдельно собираются все имена файла размеров, включая
    неоднозначные. У этих объектов есть сегменты, то есть они физически
    хранятся, — значит это таблицы, как бы они ни назывались. Маска имени
    (`V_*` → view) к ним не применяется: иначе таблица с именем на «v_»
    выпала бы из таблицы №2 и из расчёта объёма.
    """
    paths = []
    for name in section.files:
        path = Path(name)
        if not path.is_absolute():
            path = RAW_DIR / path
        if path.exists():
            paths.append(path)
    if not paths:
        return SizesIndex()

    df = read_all(paths, section)
    cols = resolve_columns(list(df.columns), section.columns)

    index = SizesIndex()
    candidates: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        full = clean_text(_cell(row, cols.get("full_name")))
        schema_name = clean_text(_cell(row, cols.get("schema_name")))
        table_name = clean_text(_cell(row, cols.get("table_name")))
        if full is None:
            if not table_name:
                continue
            full = f"{schema_name}.{table_name}" if schema_name else table_name

        parsed = parse_table_ref(full)
        if parsed is None:
            continue
        obj_schema, obj_name, parsed_ok = parsed
        index.stored_names.add(obj_name.lower())
        if not parsed_ok:
            continue  # сама запись в файле размеров без схемы — не источник
        candidates.setdefault(obj_name.lower(), set()).add(obj_schema.lower())

    index.schema_by_name = {name: next(iter(schemas))
                            for name, schemas in candidates.items()
                            if len(schemas) == 1}
    return index


def _load_reports(
    con: duckdb.DuckDBPyConnection,
    source: Path | list[Path],
    section: SectionConfig,
    stats: LoadStats,
    sizes_index: SizesIndex | None = None,
) -> None:
    sizes_index = sizes_index or SizesIndex()
    sources = [source] if isinstance(source, Path) else list(source)
    # Файлы склеиваются до разбора, а не грузятся по очереди: сквозная
    # нумерация report_id и table_id ведётся по всему набору, и загрузка
    # файлов по одному выдавала бы одинаковые ключи для разных строк.
    df = read_all(sources, section)
    stats.rows_read = len(df)
    stats.source_files = len(sources)

    cols = resolve_columns(list(df.columns), section.columns)
    if not cols.get("report_name"):
        raise SystemExit(
            "В файле не найдена колонка с именем отчёта.\n"
            f"Заголовки файла: {list(df.columns)}\n"
            "Запустите `profile` и поправьте config/mapping.yml."
        )

    # Путь берётся из трёх колонок уровней; одна колонка «Каталог» — запасной
    # вариант для файлов старой структуры.
    level_cols = [cols.get("folder_l1"), cols.get("folder_l2"), cols.get("folder_l3")]
    use_levels = any(level_cols)

    reports: list[tuple] = []
    rejects: list[tuple] = []
    # key → [id, схема, имя, разобрано, тип объекта, откуда известен тип]
    tables: dict[str, list] = {}
    links: set[tuple[int, int]] = set()
    seen_reports: dict[tuple, int] = {}

    for position, (_, row) in enumerate(df.iterrows()):
        source_row = position + section.header_row + 2  # +заголовок, +1-based Excel

        name = clean_text(_cell(row, cols["report_name"]))
        if name is None:
            stats.rows_rejected += 1
            rejects.append((1, source_row, "Пустое имя отчёта", str(row.to_dict())[:500]))
            continue

        if use_levels:
            path = join_folders(*(_cell(row, c) for c in level_cols))
        else:
            path = normalise_catalog_path(_cell(row, cols.get("catalog_path")))

        network = clean_text(_cell(row, cols.get("network")))
        plant = clean_text(_cell(row, cols.get("plant")))

        # Одно и то же имя отчёта встречается у разных сетей и заводов —
        # объединять такие строки нельзя, это разные отчёты.
        key = (network, plant, path, name)
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
                    network,
                    plant,
                    path,
                    l1,
                    l2,
                    l3,
                    depth,
                    parse_bool(_cell(row, cols.get("uses_view"))),
                    clean_text(_cell(row, cols.get("description"))),
                    clean_text(_cell(row, cols.get("owner"))),
                    source_row,
                )
            )
            stats.rows_loaded += 1

        # Тип объекта задаётся колонкой, из которой он пришёл. Если объект
        # встретился в нескольких колонках, побеждает более надёжный источник.
        seen_columns: set[str] = set()
        for field_name, kind in SOURCE_COLUMNS:
            column = cols.get(field_name)
            if column is None or column in seen_columns:
                continue
            seen_columns.add(column)

            for schema_name, table_name, ok in parse_table_list(
                _cell(row, column), section.table_separator
            ):
                if field_name == "source_tables":
                    # Маска имени — последняя догадка, и только для объектов,
                    # которых нет в файле размеров: наличие сегментов значит,
                    # что объект физически хранится, то есть это таблица, как
                    # бы она ни называлась.
                    guessed = (
                        None if table_name.lower() in sizes_index.stored_names
                        else match_object_kind(table_name, section.object_patterns)
                    )
                    obj_kind = guessed or "TABLE"
                    origin = "маска" if guessed else "по умолчанию"
                else:
                    obj_kind, origin = kind, "колонка"

                # Схемы в тексте не было — ищем её по имени таблицы в файле
                # размеров. Совпадение засчитывается, только если оно
                # единственное: неоднозначную догадку выдавать за факт нельзя.
                schema_source = "ссылка"
                if not ok:
                    recovered = sizes_index.schema_by_name.get(table_name.lower())
                    if recovered:
                        schema_name, schema_source = recovered, "файл размеров"
                    else:
                        schema_source = "не определена"

                fkey = full_name_key(schema_name, table_name)
                existing = tables.get(fkey)
                if existing is None:
                    tables[fkey] = [
                        len(tables) + 1, schema_name, table_name, obj_kind, origin,
                        schema_source,
                    ]
                    if schema_source == "не определена":
                        stats.unparsed_refs += 1
                else:
                    if KIND_PRIORITY[origin] > KIND_PRIORITY[existing[4]]:
                        existing[3], existing[4] = obj_kind, origin
                    if SCHEMA_PRIORITY[schema_source] > SCHEMA_PRIORITY[existing[5]]:
                        existing[5] = schema_source
                links.add((report_id, tables[fkey][0]))

    stats.schema_recovered_tables = sum(
        1 for entry in tables.values() if entry[5] == "файл размеров"
    )

    _insert(
        con,
        "dim_report",
        reports,
        ["report_id", "report_no", "report_name", "network", "plant", "catalog_path",
         "folder_l1", "folder_l2", "folder_l3", "folder_depth", "uses_view",
         "description", "owner", "source_row"],
    )
    # Статистика использования (exec_count, avg_duration_sec) в основной файл
    # больше не пишется — единственный источник fact_report_usage теперь
    # _load_report_usage, ниже по build().
    _insert(
        con,
        "dim_table",
        # Схема хранится в нижнем регистре — как и full_name. Из отчётов она
        # приходит как «dbo», из выгрузки сегментов как «DBO»; без приведения
        # одна и та же схема двоилась бы в сводках и фильтрах.
        [
            (tid, schema.lower(), name, key, kind, origin, source,
             source != "не определена")
            for key, (tid, schema, name, kind, origin, source) in tables.items()
        ],
        ["table_id", "schema_name", "table_name", "full_name", "object_kind",
         "kind_source", "schema_source", "is_parsed_ok"],
    )
    _insert(con, "bridge_report_table", sorted(links), ["report_id", "table_id"])
    _insert(con, "etl_reject", rejects, ["run_id", "source_row", "reason", "payload"])

    stats.tables = len(tables)
    stats.links = len(links)
    for entry in tables.values():
        stats.objects_by_kind[entry[4]] = stats.objects_by_kind.get(entry[4], 0) + 1


def _load_table_sizes(
    con: duckdb.DuckDBPyConnection, section: SectionConfig, stats: LoadStats
) -> None:
    """Загружает ВСЕ строки файла размеров.

    Список таблиц существует сам по себе и не зависит от отчётов: таблица,
    на которую не ссылается ни один отчёт, всё равно попадает в базу. Объекты,
    которых нет в отчётах, заводятся здесь же.

    Ключ размера — «таблица + сеть + завод»: одна и та же таблица на разных
    заводах занимает разное место.

    Файлов может быть несколько — по одному на завод. Они складываются в один
    накопитель по ключу «таблица + сеть + завод», поэтому строки разных заводов
    остаются раздельными, а повтор одной таблицы (секции) суммируется, как и
    внутри одного файла.
    """
    paths = _section_files(section)
    if not paths:
        return

    stats.size_files = len(paths)
    df = read_all(paths, section)
    cols = resolve_columns(list(df.columns), section.columns)

    known = {
        key: tid
        for tid, key in con.execute("SELECT table_id, full_name FROM dim_table").fetchall()
    }
    next_id = (con.execute("SELECT COALESCE(MAX(table_id), 0) FROM dim_table").fetchone()[0]) + 1
    new_objects: list[tuple] = []

    # У одной таблицы в выгрузке сегментов может быть несколько строк (секции),
    # поэтому размеры накапливаются, а не перезаписываются.
    allowed = {t.strip().upper() for t in section.segment_types}
    acc: dict[tuple[int, str, str], dict] = {}

    # Без колонки типа индексные и LOB-сегменты не отличить от таблиц: они
    # станут отдельными «таблицами» и завысят и их число, и объём. Молча
    # этого делать нельзя — предупреждаем в сводке загрузки.
    if allowed and not cols.get("segment_type"):
        stats.segment_type_column_missing = True

    # Несколько файлов размеров без колонки завода — это молча неверные цифры:
    # все строки лягут в «(не указан)», и размеры разных заводов сложатся в
    # один. Падения не будет, поэтому предупреждаем явно.
    if len(paths) > 1 and not cols.get("plant"):
        stats.size_files_without_plant = True

    for _, row in df.iterrows():
        segment_type = clean_text(_cell(row, cols.get("segment_type")))
        if allowed and segment_type is not None:
            if segment_type.strip().upper() not in allowed:
                stats.segments_skipped += 1
                continue
        if allowed and segment_type is None and cols.get("segment_type"):
            # Колонка есть, но значение пустое. Отбросить нельзя — вдруг это
            # настоящая таблица; засчитываем, но считаем такие строки.
            stats.segments_without_type += 1

        full = clean_text(_cell(row, cols.get("full_name")))
        schema_name = clean_text(_cell(row, cols.get("schema_name")))
        table_name = clean_text(_cell(row, cols.get("table_name")))
        if full is None:
            if not table_name:
                continue
            full = f"{schema_name}.{table_name}" if schema_name else table_name

        parsed = parse_table_ref(full)
        if parsed is None:
            continue
        obj_schema, obj_name, parsed_ok = parsed
        key = full_name_key(obj_schema, obj_name)

        table_id = known.get(key)
        if table_id is None:
            # Таблицы нет ни в одном отчёте — заводим её: список таблиц
            # самостоятелен и не должен зависеть от отчётов.
            table_id = next_id
            next_id += 1
            known[key] = table_id
            new_objects.append(
                # Схема в нижнем регистре — см. загрузку отчётов: иначе «DBO»
                # из выгрузки сегментов и «dbo» из отчётов станут двумя схемами.
                # schema_source описывает эту же строку файла размеров, а не
                # восстановление: «ссылка», если в ней была своя схема.
                (table_id, obj_schema.lower(), obj_name, key, "TABLE",
                 "файл размеров", "ссылка" if parsed_ok else "не определена",
                 parsed_ok)
            )
            stats.tables_only_in_sizes += 1

        network = clean_text(_cell(row, cols.get("network"))) or "(не указана)"
        plant = clean_text(_cell(row, cols.get("plant"))) or "(не указан)"

        data_mb = to_number(_cell(row, cols.get("data_mb")))
        index_mb = to_number(_cell(row, cols.get("index_mb")))
        total_mb = to_number(_cell(row, cols.get("total_mb")))
        if total_mb is None and (data_mb is not None or index_mb is not None):
            total_mb = (data_mb or 0.0) + (index_mb or 0.0)

        entry = acc.setdefault(
            (table_id, network, plant),
            {"rows": None, "data": None, "index": None, "total": None,
             "pct": None, "segments": 0, "retention": None, "measured": None},
        )
        entry["segments"] += 1
        _accumulate(entry, "total", total_mb)
        _accumulate(entry, "data", data_mb)
        _accumulate(entry, "index", index_mb)
        _accumulate(entry, "pct", to_number(_cell(row, cols.get("percent_of_total"))))
        # Число строк по секциям тоже складывается.
        _accumulate(entry, "rows", to_number(_cell(row, cols.get("row_count"))))
        # Глубина хранения — свойство таблицы, а не сегмента: берём первое
        # непустое значение, а не сумму по секциям.
        if entry["retention"] is None:
            entry["retention"] = _as_int(to_number(_cell(row, cols.get("retention_days"))))
        if entry["measured"] is None:
            entry["measured"] = clean_text(_cell(row, cols.get("measured_at")))

    _insert(
        con,
        "dim_table",
        new_objects,
        ["table_id", "schema_name", "table_name", "full_name", "object_kind",
         "kind_source", "schema_source", "is_parsed_ok"],
    )

    rows_out = [
        (
            table_id,
            network,
            plant,
            _as_int(e["rows"]),
            e["data"],
            e["index"],
            e["total"],
            e["pct"],
            e["segments"],
            e["retention"],
            e["measured"],
        )
        for (table_id, network, plant), e in acc.items()
    ]

    _insert(
        con,
        "fact_table_size",
        rows_out,
        ["table_id", "network", "plant", "row_count", "data_mb", "index_mb",
         "total_mb", "percent_of_total", "segment_count", "retention_days",
         "measured_at"],
        casts={"measured_at": "TRY_CAST(? AS DATE)"},
    )
    stats.sizes_loaded = len(rows_out)
    stats.size_plants = len({(n, p) for _, n, p in acc})
    stats.tables = con.execute("SELECT COUNT(*) FROM dim_table").fetchone()[0]


def _accumulate(entry: dict, key: str, value: float | None) -> None:
    """Складывает значение сегмента, сохраняя None, если данных не было вовсе."""
    if value is None:
        return
    entry[key] = value if entry[key] is None else entry[key] + value


def _load_report_usage(
    con: duckdb.DuckDBPyConnection, section: SectionConfig, stats: LoadStats
) -> None:
    paths = _section_files(section)
    if not paths:
        return

    df = read_all(paths, section)
    cols = resolve_columns(list(df.columns), section.columns)
    if not cols.get("report_name"):
        raise SystemExit("В файле частоты использования нет колонки с именем отчёта.")

    rows_db = con.execute(
        "SELECT report_id, report_name, catalog_path, network, plant FROM dim_report"
    ).fetchall()
    # Ключи сопоставления от точного к грубому: чем больше совпало, тем надёжнее.
    by_full = {
        (_low(net), _low(pl), _low(p), _low(n)): rid
        for rid, n, p, net, pl in rows_db
    }
    by_path = {(_low(p), _low(n)): rid for rid, n, p, _, _ in rows_db}
    # Файл статистики часто несёт ТС и Завод, но не единую колонку «Каталог» —
    # заказчик выгружает их отдельно, а собирать путь из уровней каталога здесь
    # незачем: ТС+Завод+имя обычно уже однозначны. Без этого уровня совпадение
    # проверялось бы либо по полному пути (которого в файле нет), либо сразу по
    # одному имени без завода — а одно и то же имя отчёта заведено на разных
    # заводах постоянно, и строки статистики тихо терялись бы как неоднозначные.
    by_net_plant: dict[tuple[str, str, str], list[int]] = {}
    for rid, n, _, net, pl in rows_db:
        by_net_plant.setdefault((_low(net), _low(pl), _low(n)), []).append(rid)
    by_name: dict[str, list[int]] = {}
    for rid, name, _, _, _ in rows_db:
        by_name.setdefault(_low(name), []).append(rid)

    report = stats.usage_match
    _fill_db_pairs(report, rows_db)
    known_pairs = {(_low(net), _low(pl)) for _, _, _, net, pl in rows_db}

    rows: list[tuple] = []
    seen: set[int] = set()
    for _, row in df.iterrows():
        name = clean_text(_cell(row, cols["report_name"]))
        if name is None:
            continue
        report.rows += 1

        raw_path = clean_text(_cell(row, cols.get("catalog_path")))
        path = normalise_catalog_path(raw_path) if raw_path else None
        network = clean_text(_cell(row, cols.get("network")))
        plant = clean_text(_cell(row, cols.get("plant")))
        # В отличие от текста запроса статистика привязана к площадке: раздать
        # её всем тёзкам нельзя, поэтому неизвестный разрез только отмечается.
        has_rc = _note_pair(report, known_pairs, network, plant)

        report_id = None
        if path is not None:
            report_id = by_full.get((_low(network), _low(plant), _low(path), _low(name)))
            if report_id is None:
                report_id = by_path.get((_low(path), _low(name)))
        if report_id is None and has_rc:
            candidates = by_net_plant.get((_low(network), _low(plant), _low(name)), [])
            # Неоднозначно и внутри одного завода — сопоставлять нельзя.
            report_id = candidates[0] if len(candidates) == 1 else None
        if report_id is None:
            candidates = by_name.get(_low(name), [])
            # Неоднозначное имя без завода сопоставлять нельзя — данные ушли бы не туда.
            report_id = candidates[0] if len(candidates) == 1 else None
        if report_id is None:
            stats.usage_unmatched.append(name)
            _explain_miss(report, name, by_name, has_rc, network, plant)
            continue
        if report_id in seen:
            continue
        seen.add(report_id)
        report.matched_rows += 1

        rows.append(
            (
                report_id,
                _as_int(to_number(_cell(row, cols.get("exec_count")))),
                _as_int(to_number(_cell(row, cols.get("distinct_users")))),
                _duration_sec(row, cols),
                clean_text(_cell(row, cols.get("last_executed_at"))),
                clean_text(_cell(row, cols.get("period_start"))),
                clean_text(_cell(row, cols.get("period_end"))),
            )
        )

    # OR REPLACE, а не INSERT: report_id уникален по PRIMARY KEY, и без REPLACE
    # повторный вызов (например, второй проход при нескольких файлах) упал бы
    # на конфликте ключа.
    _insert(
        con,
        "fact_report_usage",
        rows,
        ["report_id", "exec_count", "distinct_users", "avg_duration_sec",
         "last_executed_at", "period_start", "period_end"],
        casts={
            "last_executed_at": "TRY_CAST(? AS DATE)",
            "period_start": "TRY_CAST(? AS DATE)",
            "period_end": "TRY_CAST(? AS DATE)",
        },
        replace=True,
    )
    stats.usage_loaded = len(rows)


def _load_report_sql(
    con: duckdb.DuckDBPyConnection, section: SectionConfig, stats: LoadStats
) -> None:
    """Текст SQL-запроса отчёта — необязательный файл.

    Структура повторяет файл отчётов: ТС, Завод, три уровня каталога,
    наименование — плюс сам запрос. Сопоставление идёт по трём уровням, от
    точного к грубому, как у статистики использования:

    1. `(сеть, завод, каталог, имя)` — полный ключ `dim_report`;
    2. `(сеть, завод, имя)` — для файлов, где каталога нет;
    3. одно имя — **и здесь правило отличается от статистики**. Если
       организационного разреза у строки нет, текст применяется ко ВСЕМ
       отчётам с этим именем: запрос описывает определение отчёта, а не
       площадку, и общий текст для отчёта-тёзки на соседнем заводе — не
       ошибка, а точный смысл данных. Если разрез у строки есть, но по нему
       не нашлось, имя годится только когда оно единственное в базе — иначе
       запрос ушёл бы на чужой завод.

    **Разрез определяется по строке, а не по файлу.** Раньше достаточно было
    наличия колонок ТС и Завода в шапке, чтобы весь файл считался «с
    разрезом». В выгрузках эти колонки сплошь и рядом объединены по
    вертикали: значение стоит только в первой строке диапазона, а остальные
    ячейки пусты. Пустая ячейка не несёт никакой информации о заводе, но
    прежнее правило всё равно требовало «имя, единственное в базе»,
    и на базе из нескольких заводов с общими наименованиями текст доезжал
    едва до пятой части отчётов — молча, без единого сообщения.
    """
    paths = _section_files(section)
    if not paths:
        return

    df = read_all(paths, section)
    cols = resolve_columns(list(df.columns), section.columns)
    if not cols.get("report_name"):
        raise SystemExit("В файле SQL-запросов нет колонки с именем отчёта.")
    if not cols.get("sql_text"):
        raise SystemExit("В файле SQL-запросов нет колонки с текстом запроса.")

    level_cols = [cols.get("folder_l1"), cols.get("folder_l2"), cols.get("folder_l3")]
    use_levels = any(level_cols)

    rows_db = con.execute(
        "SELECT report_id, report_name, catalog_path, network, plant FROM dim_report"
    ).fetchall()
    by_full = {
        (_low(net), _low(pl), _low(p), _low(n)): rid
        for rid, n, p, net, pl in rows_db
    }
    by_net_plant: dict[tuple[str, str, str], list[int]] = {}
    for rid, n, _, net, pl in rows_db:
        by_net_plant.setdefault((_low(net), _low(pl), _low(n)), []).append(rid)
    by_name: dict[str, list[int]] = {}
    for rid, name, _, _, _ in rows_db:
        by_name.setdefault(_low(name), []).append(rid)

    report = stats.sql_match
    _fill_db_pairs(report, rows_db)
    known_pairs = {(_low(net), _low(pl)) for _, _, _, net, pl in rows_db}

    updates: dict[int, str] = {}
    for _, row in df.iterrows():
        name = clean_text(_cell(row, cols["report_name"]))
        text = clean_text(_cell(row, cols["sql_text"]))
        if name is None or text is None:
            continue
        report.rows += 1

        if use_levels:
            path = join_folders(*(_cell(row, c) for c in level_cols))
        else:
            raw_path = clean_text(_cell(row, cols.get("catalog_path")))
            path = normalise_catalog_path(raw_path) if raw_path else None
        network = clean_text(_cell(row, cols.get("network")))
        plant = clean_text(_cell(row, cols.get("plant")))
        # Разрез строки — только когда обе ячейки заполнены И такая пара есть
        # в каталоге отчётов. Пара, которой в каталоге нет, ничем не лучше
        # пустой ячейки: искать по ней нечего, зато сказать о ней надо.
        has_rc = _note_pair(report, known_pairs, network, plant)

        matched: list[int] = []
        if has_rc and path is not None:
            rid = by_full.get((_low(network), _low(plant), _low(path), _low(name)))
            if rid is not None:
                matched = [rid]
        if not matched and has_rc:
            candidates = by_net_plant.get(
                (_low(network), _low(plant), _low(name)), []
            )
            # Неоднозначно даже внутри одного завода — угадывать нельзя.
            matched = candidates if len(candidates) == 1 else []
        if not matched:
            candidates = by_name.get(_low(name), [])
            if not has_rc and not _named_pair(network, plant):
                matched = candidates          # раздаём всем тёзкам, см. docstring
            elif len(candidates) == 1:
                matched = candidates
        if not matched:
            stats.sql_unmatched.append(name)
            _explain_miss(report, name, by_name, has_rc, network, plant)
            continue
        report.matched_rows += 1

        # dict, а не список: одна строка файла может лечь на несколько отчётов,
        # а разные строки — претендовать на один и тот же report_id. Побеждает
        # последняя, как и при обычной перезаписи.
        for rid in matched:
            updates[rid] = text

    if updates:
        # executemany не принимает пустой список и падает — а «ни одна строка
        # не легла» это диагноз, а не повод обрывать всю загрузку.
        con.executemany(
            "UPDATE dim_report SET sql_text = ? WHERE report_id = ?",
            [(text, rid) for rid, text in updates.items()],
        )
    stats.sql_loaded = len(updates)


def _named_pair(network: str | None, plant: str | None) -> bool:
    """Разрез в строке указан — неважно, нашёлся он в каталоге или нет."""
    return network is not None and plant is not None


def _fill_db_pairs(report: MatchReport, rows_db: list[tuple]) -> None:
    report.db_pairs = sorted(
        {f"{net} / {pl}" for _, _, _, net, pl in rows_db if net and pl}
    )


def _note_pair(
    report: MatchReport,
    known_pairs: set[tuple[str, str]],
    network: str | None,
    plant: str | None,
) -> bool:
    """Записывает разрез строки в отчёт и говорит, годится ли он для поиска."""
    if not _named_pair(network, plant):
        return False
    label = f"{network} / {plant}"
    report.file_pairs[label] = report.file_pairs.get(label, 0) + 1
    if (_low(network), _low(plant)) in known_pairs:
        return True
    report.unknown_pairs[label] = report.unknown_pairs.get(label, 0) + 1
    return False


def _explain_miss(
    report: MatchReport,
    name: str,
    by_name: dict[str, list[int]],
    has_rc: bool,
    network: str | None,
    plant: str | None,
) -> None:
    """Раскладывает несопоставленную строку по причинам — см. `MatchReport`."""
    if not by_name.get(_low(name)):
        report.name_unknown.append(name)
    elif _named_pair(network, plant) and not has_rc:
        report.rc_unknown.append(name)
    else:
        report.ambiguous.append(name)


def _low(value: str | None) -> str:
    return (value or "").strip().lower()


def _duration_sec(row: pd.Series, cols: dict[str, str | None]) -> float | None:
    """Средняя длительность в секундах. Колонку в мс переводит в секунды."""
    seconds = to_number(_cell(row, cols.get("avg_duration_sec")))
    if seconds is not None:
        return seconds
    millis = to_number(_cell(row, cols.get("avg_duration_ms")))
    return None if millis is None else millis / 1000.0


def _section_file(section: SectionConfig) -> Path | None:
    files = _section_files(section)
    return files[0] if files else None


def _section_files(section: SectionConfig) -> list[Path]:
    """Все файлы секции. Пути из конфига считаются от data/raw/."""
    resolved = []
    for name in section.files:
        path = Path(name)
        if not path.is_absolute():
            path = RAW_DIR / path
        if not path.exists():
            raise SystemExit(f"Файл из config/mapping.yml не найден: {path}")
        resolved.append(path)
    return resolved




def _as_int(value: float | None) -> int | None:
    return None if value is None else int(value)


def _insert(
    con: duckdb.DuckDBPyConnection,
    table: str,
    rows: list[tuple],
    columns: list[str],
    casts: dict[str, str] | None = None,
    replace: bool = False,
) -> None:
    if not rows:
        return
    casts = casts or {}
    placeholders = ", ".join(casts.get(col, "?") for col in columns)
    verb = "INSERT OR REPLACE INTO" if replace else "INSERT INTO"
    con.executemany(
        f"{verb} {table} ({', '.join(columns)}) VALUES ({placeholders})", rows
    )


def print_summary(stats: LoadStats) -> None:
    print("\nЗагрузка завершена:")
    if stats.source_files > 1:
        print(f"  файлов отчётов  : {stats.source_files}")
    print(f"  строк прочитано : {stats.rows_read}")
    print(f"  отчётов загружено: {stats.rows_loaded}")
    print(f"  строк отброшено  : {stats.rows_rejected}")
    print(f"  уникальных объектов: {stats.tables}")
    if stats.objects_by_kind:
        parts = ", ".join(f"{k}: {v}" for k, v in sorted(stats.objects_by_kind.items()))
        print(f"     по типам        : {parts}")
    print(f"  связей отчёт↔таблица: {stats.links}")
    if stats.schema_recovered_tables:
        print(f"     восстановлено схем по файлу размеров: "
              f"{stats.schema_recovered_tables}")
    if stats.unparsed_refs:
        print(f"  !  ссылок без схемы: {stats.unparsed_refs} (схема = '(unknown)')")
    if stats.sizes_loaded:
        if stats.size_files > 1:
            print(f"  файлов размеров  : {stats.size_files}")
        print(f"  строк размеров   : {stats.sizes_loaded}")
        if stats.size_files_without_plant:
            print("  !  файлов размеров несколько, а колонки «Завод» нет —")
            print("     размеры разных заводов сложились в один. Добавьте")
            print("     колонки ТС и Завод в выгрузку и загрузите заново.")
        if stats.size_plants > 1:
            print(f"     заводов в файле размеров: {stats.size_plants}")
        if stats.tables_only_in_sizes:
            print(f"     таблиц только в файле размеров: {stats.tables_only_in_sizes}")
        if stats.segments_skipped:
            print(f"     пропущено сегментов не-табличных типов: {stats.segments_skipped}")
        if stats.segment_type_column_missing:
            print("  !  колонка типа сегмента не найдена: индексы и LOB-сегменты")
            print("     попали в список таблиц и завысили и их число, и объём.")
            print("     Проверьте: python -m reportsdb diagnose")
        if stats.segments_without_type:
            print(f"  !  строк с пустым типом сегмента: {stats.segments_without_type}"
                  " (засчитаны как таблицы)")
    if stats.usage_loaded or stats.usage_unmatched:
        print(f"  строк использования: {stats.usage_loaded}")
        if stats.usage_unmatched:
            preview = ", ".join(stats.usage_unmatched[:5])
            print(f"  !  использование без совпадения: {len(stats.usage_unmatched)} ({preview}…)")
    if stats.sql_loaded or stats.sql_unmatched:
        print(f"  отчётов с SQL-запросом: {stats.sql_loaded}")
        if stats.sql_unmatched:
            preview = ", ".join(stats.sql_unmatched[:5])
            print(f"  !  запросы без совпадения: {len(stats.sql_unmatched)} ({preview}…)")
    if stats.rows_rejected:
        print("\n  Причины отбраковки — в таблице etl_reject.")
