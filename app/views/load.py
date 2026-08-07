"""Загрузка данных из файлов через интерфейс.

Коллеге не нужна командная строка: положить файлы в data/raw/ (или перетащить
их сюда), выбрать, что чем является, и нажать «Загрузить».

Страница проверяет колонки **всех** выбранных файлов до загрузки и честно
показывает, какая аналитика будет недоступна из-за ненайденных колонок.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from _shared import (
    DB_PATH,
    db_schema_version,
    page_setup,
    release_db,
    try_read_only_connect,
)

from reportsdb.config import RAW_DIR, SCHEMA_VERSION, load_mapping, resolve_columns
from reportsdb.etl import build
from reportsdb.excel import list_sheets, read_all

page_setup("Загрузка данных", "📥")

SUFFIXES = {".xlsx", ".xls", ".xlsm", ".csv", ".tsv"}

# Что означает каждое поле модели и что сломается без него. Группы совпадают со
# смысловыми блоками исходного файла, чтобы таблицу проверки было легко читать.
#
# Кол-во обращений / Ср. дл. (сек) сюда осознанно НЕ входят: даже если такие
# колонки физически есть в файле отчётов, они не читаются. Единственный
# источник статистики — файл роли «Статистика отдельным файлом» ниже.
REPORT_FIELDS = [
    ("report_name", "Наименование отчёта", "Обязательное", "Без него загрузка невозможна"),
    ("source_tables", "Таблицы источники данных", "Обязательное по смыслу",
     "Без него не будет анализа объёма: связей отчёт↔таблица не возникнет"),
    ("report_no", "№", "Ссылка на исходник", "Не найти строку в оригинальной таблице"),
    ("network", "ТС", "Организация", "Не будет разреза по торговым сетям"),
    ("plant", "Завод", "Организация", "Не будет разреза по заводам"),
    ("folder_l1", "Каталог 1-го уровня", "Каталог", "Не будет группировки по каталогу"),
    ("folder_l2", "Каталог 2-го уровня", "Каталог", "Путь будет короче"),
    ("folder_l3", "Каталог 3-го уровня", "Каталог", "Путь будет короче"),
    ("catalog_path", "Каталог одной колонкой", "Каталог (запасной вариант)",
     "Используется, только если уровни 1–3 не найдены"),
    ("uses_view", "Используется view", "Достоверность",
     "Не отметить отчёты, у которых список таблиц заведомо неполон"),
    ("source_views", "View", "Типы объектов",
     "View попадут в таблицу №2 как обычные таблицы и завысят её"),
    ("source_matviews", "Mat.view", "Типы объектов",
     "Материализованные view не отделятся от обычных таблиц"),
    ("source_temp_tables", "Временные таблицы", "Типы объектов",
     "Временные и generated-объекты попадут в таблицу №2"),
    ("source_routines", "Функции/процедуры", "Типы объектов",
     "Таблица №3 «Отчёт → функции» останется пустой"),
    ("description", "Описание", "Дополнительно", "—"),
    ("owner", "Владелец", "Дополнительно", "—"),
]

SIZE_FIELDS = [
    ("schema_name", "OWNER — схема", "Обязательное", "Не сопоставить таблицу с отчётом"),
    ("table_name", "SEGMENT_NAME — имя сегмента", "Обязательное",
     "Не сопоставить таблицу с отчётом"),
    ("total_mb", "SIZE_MB — размер", "Обязательное", "Не будет объёмов вообще"),
    ("segment_type", "SEGMENT_TYPE — тип сегмента", "Важное",
     "Без него индексы и LOB попадут в размер таблиц и завысят его"),
    ("percent_of_total", "PERCENT_OF_TOTAL — доля в БД", "Полезное",
     "Не показать долю отчёта в общем объёме базы"),
    ("network", "ТС", "Разрез по заводам",
     "Размеры будут общими для всех заводов, а не своими на каждом"),
    ("plant", "Завод", "Разрез по заводам",
     "Размеры будут общими для всех заводов, а не своими на каждом"),
    ("retention_days", "Глубина хранения, дней", "Полезное",
     "Таблица №5 «Отчёт → глубина хранения» останется пустой"),
    ("full_name", "Схема.таблица одной колонкой", "Запасной вариант",
     "Используется, если нет пары OWNER/SEGMENT_NAME"),
    ("row_count", "Число строк", "Дополнительно", "—"),
    ("measured_at", "Дата замера", "Дополнительно", "—"),
]

USAGE_FIELDS = [
    ("report_name", "Наименование отчёта", "Обязательное", "Не сопоставить со справочником"),
    ("network", "ТС", "Точность сопоставления", "Возможны ошибки при совпадении имён"),
    ("plant", "Завод", "Точность сопоставления", "Возможны ошибки при совпадении имён"),
    ("exec_count", "Кол-во обращений", "Статистика", "—"),
    ("avg_duration_sec", "Ср. дл. (сек)", "Статистика", "—"),
    ("distinct_users", "Пользователей", "Дополнительно", "—"),
    ("last_executed_at", "Последний запуск", "Дополнительно", "—"),
    ("period_start", "Начало периода", "Дополнительно", "—"),
    ("period_end", "Конец периода", "Дополнительно", "—"),
]

SQL_FIELDS = [
    ("report_name", "Наименование отчёта", "Обязательное", "Не сопоставить со справочником"),
    ("sql_text", "Запрос к базе данных", "Обязательное", "Файл бесполезен без текста запроса"),
    ("network", "ТС", "Точность сопоставления",
     "Без ТС и Завода запрос ляжет на все отчёты с этим именем сразу"),
    ("plant", "Завод", "Точность сопоставления",
     "Без ТС и Завода запрос ляжет на все отчёты с этим именем сразу"),
    ("folder_l1", "Каталог 1-го уровня", "Точность сопоставления",
     "Сопоставление пойдёт по «ТС + Завод + имя», без учёта каталога"),
    ("folder_l2", "Каталог 2-го уровня", "Точность сопоставления", "Путь будет короче"),
    ("folder_l3", "Каталог 3-го уровня", "Точность сопоставления", "Путь будет короче"),
    ("catalog_path", "Каталог одной колонкой", "Каталог (запасной вариант)",
     "Используется, только если уровни 1–3 не найдены"),
]


def raw_files() -> list[str]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(
        p.name for p in RAW_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUFFIXES
    )


def mapping_report(resolved: dict, spec: list[tuple]) -> tuple[pd.DataFrame, list[str]]:
    """Таблица «поле → колонка файла» и список потерь от ненайденных колонок."""
    rows, losses = [], []
    for field, human, group, consequence in spec:
        column = resolved.get(field)
        rows.append(
            {
                "Что ожидается": human,
                "Колонка в файле": column or "— не найдена —",
                "": "✅" if column else "•",
                "Группа": group,
            }
        )
        if column is None and consequence != "—" and "запасной" not in group.lower():
            losses.append(f"**{human}** — {consequence}")
    return pd.DataFrame(rows), losses


def show_mapping(title: str, resolved: dict, spec: list[tuple]) -> list[str]:
    table, losses = mapping_report(resolved, spec)
    found = int((table[""] == "✅").sum())
    st.caption(f"{title}: найдено {found} из {len(spec)} колонок.")
    st.dataframe(table, use_container_width=True, hide_index=True)
    return losses


def _show_match_report(what: str, report) -> None:
    """Разбор несопоставленных строк вспомогательного файла по причинам.

    Раньше здесь стояло одно «не сопоставилось N наименований». По такой
    строке нельзя понять, чинить файл, загружать другие заводы или всё в
    порядке, — а причины требуют совершенно разных действий. Самая дорогая из
    них — расхождение в записи завода: сопоставление тогда молча съезжает на
    «имя, встречающееся в базе один раз», и при общих наименованиях заводов
    доезжает пятая часть строк без единой жалобы.
    """
    if not report.rows or not report.unmatched_rows:
        return

    share = report.unmatched_rows / report.rows
    st.warning(
        f"**{what}: не сопоставилось строк {report.unmatched_rows} из "
        f"{report.rows} ({share:.0%}).** Разбор по причинам ниже."
    )

    if report.unknown_pairs:
        pairs = ", ".join(
            f"«{label}» — строк: {count}" for label, count in
            sorted(report.unknown_pairs.items(), key=lambda kv: -kv[1])[:10]
        )
        known = ", ".join(f"«{p}»" for p in report.db_pairs[:10]) or "—"
        st.error(
            "**ТС и Завод в файле не совпадают с каталогом отчётов.** "
            f"В файле: {pairs}. В каталоге: {known}.\n\n"
            "Пока пара не совпадает, строка ищется только по наименованию и "
            "проходит лишь тогда, когда такое наименование в базе одно. "
            "Именно так и выглядит «сопоставилась пятая часть».\n\n"
            "**Что сделать:** привести запись ТС и Завода к тому же виду, что "
            "в основном файле отчётов (частая причина — ведущий ноль в коде "
            "завода), либо загрузить основной файл того завода, о котором "
            "этот файл. Если разреза в файле нет вовсе — удалите пустые "
            "колонки ТС и Завод: тогда строка ляжет на все отчёты-тёзки."
        )
    reasons = [
        ("наименования нет в каталоге ни на одном заводе", report.name_unknown),
        ("наименование есть, но не на тех ТС и Заводе", report.rc_unknown),
        ("наименование есть у нескольких отчётов завода, каталог не совпал",
         report.ambiguous),
    ]
    lines = [
        f"- {len(names)} — {text}. Например: {', '.join(names[:3])}"
        for text, names in reasons if names
    ]
    if lines:
        st.markdown("\n".join(lines))


# --- Текущее состояние базы ------------------------------------------------

st.subheader("Текущее состояние")

if not DB_PATH.exists():
    st.info("База ещё не собрана. Загрузите файл с отчётами — это займёт секунды.")
else:
    # Эта страница — путь восстановления, поэтому она обязана открываться на
    # ЛЮБОЙ базе, включая собранную прежней версией, и даже когда файл сейчас
    # занят другим процессом. Отсюда мягкие запросы: ни один сбой чтения не
    # должен помешать нажать «Загрузить» ниже.
    run = counts = None
    version = 0
    probe = try_read_only_connect(DB_PATH)
    if probe is None:
        st.info(
            "**База сейчас занята** — похоже, в другой вкладке или окне идёт "
            "загрузка или очистка данных. Показатели ниже временно не видны, "
            "но это не мешает загрузке — она подождёт своей очереди.",
            icon="⏳",
        )
    else:
        try:
            version = db_schema_version(probe)
            try:
                run = probe.execute(
                    "SELECT started_at, source_file, rows_loaded, rows_rejected FROM etl_run "
                    "ORDER BY run_id DESC LIMIT 1"
                ).fetchone()
                counts = probe.execute(
                    """
                    SELECT (SELECT COUNT(*) FROM dim_report),
                           (SELECT COUNT(*) FROM dim_table),
                           (SELECT COUNT(*) FROM fact_table_size),
                           (SELECT COUNT(*) FROM fact_report_usage WHERE exec_count IS NOT NULL),
                           (SELECT COUNT(*) FROM dim_report WHERE uses_view),
                           (SELECT COUNT(DISTINCT network) FROM dim_report WHERE network IS NOT NULL)
                    """
                ).fetchone()
            except Exception:  # noqa: BLE001 — структура старая, показать нечего
                pass
        finally:
            probe.close()

        if version < SCHEMA_VERSION:
            st.warning(
                f"База собрана прежней версией программы (структура {version} "
                f"вместо {SCHEMA_VERSION}). Разделы аналитики пока не откроются — "
                "нажмите «Загрузить» ниже, и всё заработает."
            )
        elif counts:
            c1, c2, c3, c4, c5, c6 = st.columns(6)
            c1.metric("Отчётов", counts[0])
            c2.metric("Таблиц", counts[1])
            c3.metric("С размерами", counts[2])
            c4.metric("Со статистикой", counts[3])
            c5.metric("Через view", counts[4])
            c6.metric("Торговых сетей", counts[5])

        if run:
            st.caption(
                f"Последняя загрузка: {run[0]:%Y-%m-%d %H:%M} из `{run[1]}` — "
                f"загружено {run[2]}, отброшено {run[3]}."
            )

# --- Очистка базы ------------------------------------------------------------
# Блок стоит здесь, а не внизу страницы: ниже есть st.stop() на случай пустой
# папки и невыбранного файла отчётов, и кнопка за ними просто не отрисуется —
# ровно так однажды пропала сборка HTML. Свёрнутый блок и обязательный флажок
# защищают от случайного нажатия.

if DB_PATH.exists():
    with st.expander("🗑️ Очистить базу", expanded=False):
        st.caption(
            "Стирает **все загруженные данные**: отчёты, таблицы, размеры и "
            "статистику. Останется пустая база нужной структуры — страницы "
            "аналитики будут открываться и показывать нули, а не ошибку."
        )
        size_mb = DB_PATH.stat().st_size / 1024 / 1024
        backup = DB_PATH.with_suffix(DB_PATH.suffix + ".bak")
        raw_count = len([p for p in RAW_DIR.iterdir() if p.is_file()]) if RAW_DIR.exists() else 0

        st.warning(
            f"Будет удалено: база `{DB_PATH.name}` ({size_mb:.1f} МБ)"
            + (f" и резервная копия `{backup.name}` "
               f"({backup.stat().st_size / 1024 / 1024:.1f} МБ)"
               if backup.exists() else "")
            + ".\n\n**Отменить это нельзя.** Чтобы вернуть данные, придётся "
              "загрузить файлы заново.",
            icon="⚠️",
        )
        if raw_count:
            st.info(
                f"Исходные файлы в `{RAW_DIR}` не тронутся — их там "
                f"{raw_count}. Если данные не должны оставаться на машине, "
                "удалите их отдельно, обычным способом.",
                icon="📁",
            )

        keep_backup = st.checkbox(
            "Оставить резервную копию `reports.duckdb.bak`",
            help="По умолчанию копия удаляется вместе с базой: рядом с пустой "
                 "базой она сохранила бы ровно то, что просили стереть.",
        )
        confirmed = st.checkbox(
            "Да, стереть все данные из базы", key="clear_confirm",
            help="Флажок нужен, чтобы кнопку нельзя было нажать случайно.",
        )
        if st.button(
            "Очистить базу", type="secondary", use_container_width=True,
            disabled=not confirmed,
        ):
            from reportsdb.etl import clear

            release_db()  # тот же порядок, что при пересборке: сначала отпустить файл
            try:
                result = clear(DB_PATH, keep_backup=keep_backup)
            except Exception as exc:  # noqa: BLE001 — причина нужна пользователю
                st.error(f"Не удалось очистить базу:\n\n```\n{type(exc).__name__}: {exc}\n```")
                st.stop()

            st.success(
                f"База очищена, освобождено "
                f"{result.freed_bytes / 1024 / 1024:.1f} МБ."
                + (" Резервная копия удалена." if result.backup_removed else "")
            )
            st.session_state.pop("clear_confirm", None)
            st.rerun()

st.divider()

# --- Шаг 1: файлы ----------------------------------------------------------

st.subheader("Шаг 1. Файлы")
st.caption(
    f"Положите файлы в папку `{RAW_DIR}` — они появятся в списках ниже. "
    "Либо перетащите их сюда, тогда они сохранятся в ту же папку."
)

uploaded = st.file_uploader(
    "Перетащите файлы (.xlsx, .xls, .csv)",
    type=["xlsx", "xls", "xlsm", "csv", "tsv"],
    accept_multiple_files=True,
)
if uploaded:
    saved = []
    for item in uploaded:
        # Только имя файла — путь из браузера доверять нельзя.
        target = RAW_DIR / Path(item.name).name
        target.write_bytes(item.getbuffer())
        saved.append(target.name)
    st.success("Сохранено в data/raw/: " + ", ".join(saved))

files = raw_files()
if not files:
    st.warning(f"В папке `{RAW_DIR}` пока нет подходящих файлов.")
    st.stop()

# --- Шаг 2: назначение файлов ---------------------------------------------

st.subheader("Шаг 2. Что чем является")

st.caption(
    "Файлов на каждую роль может быть несколько — например, по одному на "
    "завод. Они будут склеены и загружены за один проход, а завод у каждой "
    "строки возьмётся из колонок **ТС** и **Завод** внутри файла."
)

col1, col2, col3, col4 = st.columns(4)
reports_files = col1.multiselect(
    "Отчёты (обязательно)", files,
    default=files[:1] if len(files) == 1 else [],
    help="Строка на отчёт: №, ТС, завод, каталог тремя уровнями, наименование, "
         "признак view, таблицы-источники. Кол-во обращений и длительность из "
         "этого файла не читаются — только из роли «Статистика» справа.",
)
sizes_files = col2.multiselect(
    "Размеры таблиц", files,
    help="Выгрузка сегментов БД: ТС, Завод, OWNER, SEGMENT_NAME, SEGMENT_TYPE, "
         "SIZE_MB, PERCENT_OF_TOTAL. Строка на сегмент, а не на таблицу.",
)
usage_files = col3.multiselect(
    "Статистика отдельным файлом", files,
    help="Единственный источник обращений и длительности — в файле отчётов "
         "эти колонки не читаются, даже если физически есть. Без этой роли "
         "статистики не будет вовсе.",
)
sql_files = col4.multiselect(
    "SQL-запросы", files,
    help="Необязательно. Структура как у файла отчётов: №, ТС, Завод, каталог "
         "тремя уровнями, наименование — плюс «Запрос к базе данных». "
         "Показывается в «Инструменты → Запросы к БД» и в карточке отчёта.",
)

if not reports_files:
    st.info("Выберите хотя бы один файл отчётов.")
    st.stop()

mapping = load_mapping()

# Лист книги выбирается явно, если листов несколько. Выбор применяется ко всем
# файлам отчётов сразу: выгрузки по заводам делает один и тот же отчёт, листы
# у них называются одинаково.
sheets = list_sheets(RAW_DIR / reports_files[0])
if len(sheets) > 1:
    mapping.reports.sheet = st.selectbox("Лист с отчётами", sheets)
    st.caption(
        f"В книге {len(sheets)} листов — выберите нужный. "
        "Выбор применяется ко всем файлам отчётов."
    )

# --- Шаг 3: проверка колонок всех выбранных файлов -------------------------

st.subheader("Шаг 3. Проверка колонок")

try:
    # Проверка колонок идёт по склейке всех файлов роли — ровно по тому, что
    # получит загрузчик. Если в одном из файлов колонки нет, в склейке она
    # окажется пустой, и это видно здесь, а не после загрузки.
    preview = read_all([RAW_DIR / f for f in reports_files], mapping.reports)
except Exception as exc:  # noqa: BLE001 — показываем причину пользователю
    st.error(f"Не удалось прочитать файл:\n\n```\n{exc}\n```")
    st.stop()

resolved = resolve_columns(list(preview.columns), mapping.reports.columns)

tab_names = ["Отчёты"]
if sizes_files:
    tab_names.append("Размеры таблиц")
if usage_files:
    tab_names.append("Статистика")
if sql_files:
    tab_names.append("SQL-запросы")
tabs = st.tabs(tab_names)

all_losses: list[str] = []

with tabs[0]:
    all_losses += show_mapping("Файл отчётов", resolved, REPORT_FIELDS)
    if len(reports_files) > 1:
        st.caption(f"Склеено файлов: {len(reports_files)}.")
    with st.expander(f"Первые строки (всего {len(preview)})"):
        st.dataframe(preview.head(10), use_container_width=True, hide_index=True)

index = 1
if sizes_files:
    with tabs[index]:
        try:
            sizes_preview = read_all(
                [RAW_DIR / f for f in sizes_files], mapping.table_sizes)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Не удалось прочитать файл размеров:\n\n```\n{exc}\n```")
            st.stop()
        sizes_resolved = resolve_columns(
            list(sizes_preview.columns), mapping.table_sizes.columns
        )
        all_losses += show_mapping("Файл размеров", sizes_resolved, SIZE_FIELDS)

        if not sizes_resolved.get("total_mb"):
            st.error(
                "Не найдена колонка с размером (`SIZE_MB`) — файл бесполезен без неё. "
                "Либо уберите его из выбора, либо добавьте заголовок в "
                "`config/mapping.yml`, раздел `table_sizes.columns.total_mb`."
            )
            st.stop()
        if not sizes_resolved.get("segment_type"):
            st.warning(
                "Колонка `SEGMENT_TYPE` не найдена: будут просуммированы **все** "
                "строки файла, включая индексные и LOB-сегменты. Размеры таблиц "
                "окажутся завышены."
            )
        else:
            allowed = ", ".join(mapping.table_sizes.segment_types)
            st.caption(f"В размер таблицы войдут только сегменты типов: {allowed}.")

        # Несколько файлов без колонки завода — молча неверные цифры: строки
        # всех заводов лягут в «(не указан)» и сложатся в один размер.
        if len(sizes_files) > 1 and not sizes_resolved.get("plant"):
            st.error(
                f"Выбрано файлов размеров: {len(sizes_files)}, а колонки "
                "**«Завод»** в них нет. Размеры разных заводов сложатся в один "
                "и ошибки при этом не будет — цифры просто окажутся неверными."
                "\n\n**Что сделать:** добавьте в выгрузку колонки ТС и Завод "
                "либо загружайте по одному файлу за раз."
            )
        elif len(sizes_files) > 1:
            st.caption(f"Склеено файлов: {len(sizes_files)}.")

        with st.expander(f"Первые строки (всего {len(sizes_preview)})"):
            st.dataframe(sizes_preview.head(10), use_container_width=True, hide_index=True)
    index += 1

if usage_files:
    with tabs[index]:
        try:
            usage_preview = read_all(
                [RAW_DIR / f for f in usage_files], mapping.report_usage)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Не удалось прочитать файл статистики:\n\n```\n{exc}\n```")
            st.stop()
        usage_resolved = resolve_columns(
            list(usage_preview.columns), mapping.report_usage.columns
        )
        show_mapping("Файл статистики", usage_resolved, USAGE_FIELDS)
        if not usage_resolved.get("report_name"):
            st.error(
                "В файле статистики нет колонки с наименованием отчёта — "
                "сопоставить строки не с чем. Уберите файл из выбора."
            )
            st.stop()
        if len(usage_files) > 1:
            st.caption(f"Склеено файлов: {len(usage_files)}.")
        with st.expander(f"Первые строки (всего {len(usage_preview)})"):
            st.dataframe(usage_preview.head(10), use_container_width=True, hide_index=True)
    index += 1

if sql_files:
    with tabs[index]:
        try:
            sql_preview = read_all(
                [RAW_DIR / f for f in sql_files], mapping.report_sql)
        except Exception as exc:  # noqa: BLE001
            st.error(f"Не удалось прочитать файл SQL-запросов:\n\n```\n{exc}\n```")
            st.stop()
        sql_resolved = resolve_columns(
            list(sql_preview.columns), mapping.report_sql.columns
        )
        show_mapping("Файл SQL-запросов", sql_resolved, SQL_FIELDS)
        if not sql_resolved.get("report_name") or not sql_resolved.get("sql_text"):
            st.error(
                "В файле SQL-запросов нет колонки с именем отчёта или текстом "
                "запроса — сопоставить нечего. Уберите файл из выбора."
            )
            st.stop()
        if sql_resolved.get("network") and sql_resolved.get("plant"):
            st.caption(
                "Сопоставление точное: сначала «ТС + Завод + каталог + имя», "
                "затем «ТС + Завод + имя». У отчёта-тёзки на соседнем заводе "
                "будет свой запрос."
            )
        else:
            st.info(
                "Колонок **ТС** и **Завод** в файле нет, поэтому сопоставление "
                "идёт по одному имени: текст запроса ляжет сразу на все отчёты "
                "с этим именем, сколько бы заводов их ни держало."
            )
        if len(sql_files) > 1:
            st.caption(f"Склеено файлов: {len(sql_files)}.")
        with st.expander(f"Первые строки (всего {len(sql_preview)})"):
            st.dataframe(sql_preview.head(10), use_container_width=True, hide_index=True)

# Блокирующая проверка — только имя отчёта.
if not resolved.get("report_name"):
    st.error(
        "Не найдена колонка с наименованием отчёта — загрузка невозможна.\n\n"
        f"Заголовки файла: `{', '.join(map(str, preview.columns))}`\n\n"
        "Добавьте фактический заголовок в `config/mapping.yml`, раздел "
        "`reports.columns.report_name`, и обновите страницу."
    )
    st.stop()

if all_losses:
    with st.expander(f"Что будет недоступно из-за ненайденных колонок ({len(all_losses)})"):
        for line in all_losses:
            st.markdown(f"- {line}")
        st.caption(
            "Загрузке это не мешает. Чтобы подхватить колонку, допишите её "
            "заголовок в `config/mapping.yml` и обновите страницу."
        )
else:
    st.success("Все ожидаемые колонки найдены — аналитика будет полной.")

# --- Шаг 4: загрузка -------------------------------------------------------

st.subheader("Шаг 4. Загрузка")
st.caption(
    "База пересобирается с нуля: прежняя версия сохраняется рядом как "
    "`reports.duckdb.bak`. Ранее загруженные данные, которых нет в выбранных "
    "файлах, не сохранятся."
)

if st.button("Загрузить", type="primary", use_container_width=True):
    mapping.table_sizes.files = list(sizes_files)
    mapping.report_usage.files = list(usage_files)
    mapping.report_sql.files = list(sql_files)

    release_db()  # отпускаем файл БД до пересборки

    try:
        with st.spinner("Читаю файлы и собираю базу…"):
            stats = build([RAW_DIR / f for f in reports_files], DB_PATH, mapping)
    except SystemExit as exc:  # ETL сообщает об ошибках через SystemExit
        st.error(f"Загрузка прервана:\n\n```\n{exc}\n```")
        st.stop()
    except Exception as exc:  # noqa: BLE001 — причина нужна пользователю
        st.error(f"Ошибка при загрузке:\n\n```\n{type(exc).__name__}: {exc}\n```")
        st.stop()

    st.success(f"Готово: загружено отчётов — {stats.rows_loaded}.")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Прочитано строк", stats.rows_read)
    m2.metric("Отчётов загружено", stats.rows_loaded)
    m3.metric("Уникальных объектов", stats.tables)
    m4.metric("Связей", stats.links)
    m5.metric("Строк размеров", stats.sizes_loaded)

    if stats.objects_by_kind:
        st.caption(
            "Объекты по типам: "
            + ", ".join(f"{k} — {v}" for k, v in sorted(stats.objects_by_kind.items()))
        )

    if stats.rows_rejected:
        st.warning(f"Отброшено строк: {stats.rows_rejected} — см. ниже.")
    if stats.schema_recovered_tables:
        st.success(
            f"Восстановлено схем по файлу размеров: {stats.schema_recovered_tables}. "
            "В отчёте таблица была указана без схемы, но её имя встречается в "
            "файле размеров ровно с одной схемой — она и присвоена. "
            "Отмечено в справочнике «Таблицы» столбцом «Схема определена»."
        )
    if stats.unparsed_refs:
        st.warning(
            f"Ссылок на таблицы без схемы: {stats.unparsed_refs}. Восстановить "
            "не удалось: либо такого имени нет в файле размеров, либо оно "
            "встречается там сразу под несколькими схемами. Схема записана как "
            "«(unknown)»."
        )
    if stats.segments_skipped:
        st.info(
            f"Пропущено {stats.segments_skipped} сегментов не-табличных типов "
            "(индексы, LOB). Так и задумано: привязать их к таблице по выгрузке "
            "нельзя, поэтому в объём таблиц они не входят."
        )
    if stats.segment_type_column_missing:
        st.error(
            "**Колонка с типом сегмента не найдена.** Отделить индексы и "
            "LOB-сегменты от таблиц невозможно — они попали в список таблиц и "
            "завысили и число таблиц, и суммарный объём.\n\n"
            "**Что сделать:** допишите фактическое имя колонки в "
            "`config/mapping.yml` → `table_sizes.columns.segment_type` и "
            "загрузите файл заново."
        )
    if stats.segments_without_type:
        st.warning(
            f"У {stats.segments_without_type} строк файла размеров не заполнен "
            "тип сегмента. Они засчитаны как таблицы: отбросить их нельзя — "
            "вдруг это настоящие таблицы. Если это индексы, объём завышен."
        )
    if stats.tables_only_in_sizes:
        st.info(
            f"{stats.tables_only_in_sizes} таблиц есть только в файле размеров и "
            "не используются ни одним отчётом. Они загружены и видны в таблице "
            "№1 — список таблиц не зависит от отчётов."
        )
    if stats.size_plants > 1:
        st.caption(f"В файле размеров различается {stats.size_plants} пар «сеть + завод».")
    _show_match_report("Статистика", stats.usage_match)
    if stats.sql_loaded:
        st.caption(f"SQL-запрос получили отчётов: {stats.sql_loaded}.")
    _show_match_report("Запросы", stats.sql_match)

    con = try_read_only_connect(DB_PATH)
    if con is None:
        # Загрузка уже прошла успешно (сообщение «Готово» выше) — сбой здесь
        # означает только то, что подробности сейчас недоступны, а не то, что
        # с только что собранной базой что-то не так.
        st.caption(
            "База сейчас занята другим процессом — заполненность колонок и "
            "отброшенные строки временно не показать."
        )
    else:
        try:
            rejects = con.execute(
                "SELECT source_row, reason, payload FROM etl_reject"
            ).df()
            rejects.columns = ["Строка файла", "Причина", "Данные строки"]
            filled = con.execute(
                """
                SELECT COUNT(*) FILTER (WHERE network IS NOT NULL),
                       COUNT(*) FILTER (WHERE uses_view IS NOT NULL),
                       COUNT(*) FILTER (WHERE folder_l2 IS NOT NULL),
                       (SELECT COUNT(*) FROM fact_report_usage WHERE avg_duration_sec IS NOT NULL)
                FROM dim_report
                """
            ).fetchone()
        finally:
            con.close()

        st.caption(
            f"Заполнено: ТС — {filled[0]}, признак view — {filled[1]}, "
            f"второй уровень каталога — {filled[2]}, длительность — {filled[3]}."
        )

        if not rejects.empty:
            with st.expander(f"Отброшенные строки ({len(rejects)})"):
                st.dataframe(rejects, use_container_width=True, hide_index=True)

    st.info("Данные обновлены — переходите к разделам аналитики в меню слева.")
    st.caption(
        "Чтобы отдать данные коллеге одним файлом — "
        "**Инструменты → Файл для коллег**."
    )
