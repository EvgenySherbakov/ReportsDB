"""Сверка файла размеров с тем, что попало в базу.

Отвечает на вопрос «почему в №1 таблиц больше, чем в моём файле». Считает по
файлу ровно то же, что считает загрузчик, и показывает, на каком шаге числа
расходятся.

Имена таблиц и схем по умолчанию НЕ печатаются — данные не должны утекать в
переписку. Показать их локально: `--show-names`.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

import duckdb

from .config import DB_PATH, RAW_DIR, Mapping, SectionConfig, resolve_columns
from .excel import read_sheet
from .normalize import clean_text, full_name_key, parse_table_ref, to_number

# Поля файла размеров и то, насколько их отсутствие опасно.
# critical — без колонки цифры будут неверными; остальные необязательны.
KEY_FIELDS = [
    ("network", "ТС", False),
    ("plant", "Завод", False),
    ("schema_name", "схема", False),
    ("table_name", "имя таблицы", True),
    ("full_name", "схема.таблица одной колонкой", False),
    ("segment_type", "тип сегмента", True),
    ("total_mb", "размер, МБ", True),
    ("percent_of_total", "доля БД, %", False),
    ("retention_days", "глубина хранения", False),
]

BAR = "─" * 62


def _fmt(value: float | int) -> str:
    """Число с пробелами между разрядами — так его читают глазами."""
    if isinstance(value, float):
        return f"{value:,.1f}".replace(",", " ")
    return f"{value:,}".replace(",", " ")


def _resolve_path(section: SectionConfig) -> Path | None:
    if not section.file:
        return None
    path = Path(section.file)
    if not path.is_absolute():
        path = RAW_DIR / path
    return path


def diagnose(
    mapping: Mapping,
    db_path: Path = DB_PATH,
    sizes_file: Path | None = None,
    show_names: bool = False,
) -> int:
    """Печатает сверку. Возвращает код возврата для CLI."""
    section = mapping.table_sizes
    path = sizes_file or _resolve_path(section)
    if path is None:
        print(
            "Файл размеров не задан. Укажите его аргументом:\n"
            "  python -m reportsdb diagnose data/raw/<файл размеров>.xlsx"
        )
        return 2
    if not path.exists():
        print(f"Файл не найден: {path}")
        return 2

    print(BAR)
    print(f"Файл размеров: {path.name}")
    df = read_sheet(path, section)
    cols = resolve_columns(list(df.columns), section.columns)
    print(f"Строк в файле: {_fmt(len(df))}")

    # --- 1. Колонки ------------------------------------------------------
    print(f"\n{BAR}\n1. Сопоставление колонок\n{BAR}")
    for field_name, human, critical in KEY_FIELDS:
        found = cols.get(field_name)
        if found:
            mark, shown = "  ", found
        elif critical:
            mark, shown = "!!", "НЕ НАЙДЕНА"
        else:
            mark, shown = "  ", "нет (необязательная)"
        print(f" {mark} {human:<28} ← {shown}")
    if not cols.get("segment_type"):
        print(
            "\n !! Колонка типа сегмента не найдена. Отфильтровать индексы и\n"
            "    LOB-сегменты невозможно — они попадут в список таблиц и\n"
            "    завысят и число таблиц, и суммарный объём.\n"
            "    Это самая частая причина расхождения. Допишите фактическое\n"
            "    имя колонки в config/mapping.yml → table_sizes.columns.segment_type"
        )
    if not cols.get("plant"):
        print(
            "\n  i Колонки завода нет: все строки считаются общими для всех\n"
            "    заводов и складываются в одну строку на таблицу."
        )

    # --- 2. Типы сегментов ----------------------------------------------
    allowed = {t.strip().upper() for t in section.segment_types}
    by_type_rows: Counter = Counter()
    by_type_mb: defaultdict = defaultdict(float)
    for _, row in df.iterrows():
        raw_type = clean_text(row[cols["segment_type"]]) if cols.get("segment_type") else None
        label = raw_type if raw_type else "(пусто)"
        by_type_rows[label] += 1
        size = to_number(row[cols["total_mb"]]) if cols.get("total_mb") else None
        by_type_mb[label] += size or 0.0

    def counted(label: str) -> bool:
        """Повторяет решение загрузчика: пустой тип строку не отбрасывает."""
        if not allowed or label == "(пусто)":
            return True
        return label.strip().upper() in allowed

    print(f"\n{BAR}\n2. Типы сегментов\n{BAR}")
    print(f" {'тип':<26}{'строк':>10}{'МБ':>14}  ")
    for label, count in by_type_rows.most_common():
        verdict = "учитывается" if counted(label) else "пропускается"
        if label == "(пусто)" and counted(label):
            verdict = "УЧИТЫВАЕТСЯ — тип не указан"
        print(f" {label:<26}{_fmt(count):>10}{_fmt(by_type_mb[label]):>14}  {verdict}")
    print(f" {'учитывается всего':<26}"
          f"{_fmt(sum(c for l, c in by_type_rows.items() if counted(l))):>10}"
          f"{_fmt(sum(m for l, m in by_type_mb.items() if counted(l))):>14}")

    # --- 3. Уникальные имена ---------------------------------------------
    names_all: set[str] = set()
    names_counted: set[str] = set()
    short_counted: set[str] = set()
    schemas_per_name: defaultdict = defaultdict(set)
    mb_counted = 0.0
    plants: set[str] = set()

    for _, row in df.iterrows():
        raw_type = clean_text(row[cols["segment_type"]]) if cols.get("segment_type") else None
        label = raw_type if raw_type else "(пусто)"

        full = clean_text(row[cols["full_name"]]) if cols.get("full_name") else None
        if full is None:
            schema_name = clean_text(row[cols["schema_name"]]) if cols.get("schema_name") else None
            table_name = clean_text(row[cols["table_name"]]) if cols.get("table_name") else None
            if not table_name:
                continue
            full = f"{schema_name}.{table_name}" if schema_name else table_name
        parsed = parse_table_ref(full)
        if parsed is None:
            continue
        obj_schema, obj_name, _ = parsed
        key = full_name_key(obj_schema, obj_name)
        names_all.add(key)
        if counted(label):
            names_counted.add(key)
            short_counted.add(obj_name.lower())
            schemas_per_name[obj_name.lower()].add(obj_schema.lower())
            size = to_number(row[cols["total_mb"]]) if cols.get("total_mb") else None
            mb_counted += size or 0.0
            if cols.get("plant"):
                plants.add(clean_text(row[cols["plant"]]) or "(не указан)")

    print(f"\n{BAR}\n3. Сколько получается уникальных таблиц\n{BAR}")
    print(f"  по всем строкам файла, схема.таблица  : {_fmt(len(names_all))}")
    print(f"  только учитываемые, схема.таблица     : {_fmt(len(names_counted))}"
          "   ← именно это попадает в №1")
    print(f"  только учитываемые, имя без схемы     : {_fmt(len(short_counted))}")
    multi = {n: s for n, s in schemas_per_name.items() if len(s) > 1}
    if multi:
        print(
            f"\n  i Одно имя таблицы встречается сразу в нескольких схемах:\n"
            f"    таких имён — {_fmt(len(multi))}. Отсюда и вся разница между\n"
            f"    двумя способами счёта: {_fmt(len(names_counted))} против "
            f"{_fmt(len(short_counted))}.\n"
            f"    Если вы считали таблицы по SEGMENT_NAME без схемы, ваша цифра\n"
            f"    будет меньше: разные схемы — разные таблицы."
        )
    if show_names and multi:
        print("    Имена:", ", ".join(sorted(multi)[:20]))
    if plants:
        print(f"\n  заводов в файле: {len(plants)}")
    print(f"  сумма МБ по учитываемым строкам: {_fmt(mb_counted)}")

    # --- 4. Что в базе ----------------------------------------------------
    print(f"\n{BAR}\n4. Что лежит в базе\n{BAR}")
    if not db_path.exists():
        print(f"  База не найдена: {db_path}. Сверка с базой пропущена.")
        return 0

    con = duckdb.connect(str(db_path), read_only=True)
    try:
        one = lambda sql: con.execute(sql).fetchone()[0]  # noqa: E731
        try:
            version = one("SELECT schema_version FROM etl_run ORDER BY run_id DESC LIMIT 1")
        except duckdb.Error:
            version = None
        db_rows = one("SELECT COUNT(*) FROM fact_table_size")
        db_tables = one("SELECT COUNT(DISTINCT table_id) FROM fact_table_size")
        db_mb = one("SELECT COALESCE(SUM(total_mb), 0) FROM fact_table_size")
        print(f"  версия структуры базы            : {version}")
        print(f"  строк fact_table_size            : {_fmt(db_rows)}")
        print(f"  уникальных таблиц с размером     : {_fmt(db_tables)}")
        print(f"  сумма объёма                     : {_fmt(db_mb)} МБ")

        try:
            cat_rows = one("SELECT COUNT(*) FROM v_tables_catalog")
            cat_uniq = one("SELECT COUNT(DISTINCT full_name) FROM v_tables_catalog")
            cat_mb = one("SELECT COALESCE(SUM(total_mb), 0) FROM v_tables_catalog")
            print(f"  строк в таблице №1               : {_fmt(cat_rows)}")
            print(f"  уникальных таблиц в №1           : {_fmt(cat_uniq)}")
            print(f"  суммарный объём в №1             : {_fmt(cat_mb)} МБ")
        except duckdb.Error as exc:
            print(f"  !! Витрина №1 недоступна: {exc}")
            cat_rows = cat_uniq = cat_mb = None

        # --- 5. Вердикт ---------------------------------------------------
        print(f"\n{BAR}\n5. Сверка\n{BAR}")
        problems = 0

        # Сначала — то, что искажает сами исходные цифры. Иначе галочки ниже
        # («база повторяет файл») успокоят, хотя считалось не то.
        if allowed and not cols.get("segment_type"):
            problems += 1
            print(
                " !! ГЛАВНОЕ: колонка типа сегмента не найдена, поэтому таблицами\n"
                "    посчитаны ВСЕ строки файла, включая индексы и LOB.\n"
                f"    Таблиц получилось {_fmt(len(names_counted))} и "
                f"{_fmt(mb_counted)} МБ — обе цифры завышены.\n"
                "    Что сделать: впишите имя колонки в config/mapping.yml →\n"
                "    table_sizes.columns.segment_type и загрузите файл заново."
            )
        elif by_type_rows.get("(пусто)"):
            problems += 1
            print(
                f" !! У {_fmt(by_type_rows['(пусто)'])} строк колонка типа пуста.\n"
                "    Они посчитаны как таблицы. Если это индексы — объём завышен."
            )

        if version is not None and version < 7:
            problems += 1
            print(
                f" !! База собрана прежней версией программы (структура {version}).\n"
                "    До версии 7 в №1 подмешивались объекты из отчётов, которых\n"
                "    нет в файле размеров — отсюда лишние таблицы.\n"
                "    Что сделать: git pull, затем перезагрузить данные."
            )

        if db_tables != len(names_counted):
            problems += 1
            print(
                f" !! В базе {_fmt(db_tables)} таблиц, а по файлу должно быть "
                f"{_fmt(len(names_counted))}.\n"
                "    Разница появляется, если базу собирали на другом файле\n"
                "    размеров либо другой версией конфига."
            )
        else:
            print(f" ✓  число таблиц совпадает с файлом: {_fmt(db_tables)}")

        if abs(db_mb - mb_counted) > max(1.0, mb_counted * 0.001):
            problems += 1
            print(
                f" !! Объём в базе {_fmt(db_mb)} МБ, по файлу {_fmt(mb_counted)} МБ."
            )
        else:
            print(f" ✓  объём совпадает с файлом: {_fmt(db_mb)} МБ")

        if cat_uniq is not None and cat_uniq != db_tables:
            problems += 1
            print(
                f" !! В №1 {_fmt(cat_uniq)} таблиц, а размеров загружено на "
                f"{_fmt(db_tables)}.\n"
                "    Значит, в список попадает что-то помимо файла размеров."
            )
            extra = con.execute(
                "SELECT COUNT(DISTINCT full_name) FROM v_tables_catalog c "
                "WHERE NOT EXISTS (SELECT 1 FROM fact_table_size s JOIN dim_table t "
                "ON t.table_id = s.table_id WHERE t.full_name = c.full_name)"
            ).fetchone()[0]
            print(f"    Лишних таблиц в №1: {_fmt(extra)}")
            if show_names and extra:
                names = con.execute(
                    "SELECT DISTINCT full_name FROM v_tables_catalog c "
                    "WHERE NOT EXISTS (SELECT 1 FROM fact_table_size s JOIN dim_table t "
                    "ON t.table_id = s.table_id WHERE t.full_name = c.full_name) LIMIT 20"
                ).fetchall()
                print("    Примеры:", ", ".join(n[0] for n in names))
        elif cat_uniq is not None:
            print(f" ✓  в №1 ровно те таблицы, что в файле размеров: {_fmt(cat_uniq)}")

        if not problems:
            print("\n Расхождений нет: база повторяет файл размеров.")
    finally:
        con.close()

    print(BAR)
    return 0
