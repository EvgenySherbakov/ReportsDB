"""Загрузка данных из файлов через интерфейс.

Коллеге не нужна командная строка: положить файлы в data/raw/ (или перетащить
их сюда), выбрать, что чем является, и нажать «Загрузить».

Страница проверяет колонки **всех** выбранных файлов до загрузки и честно
показывает, какая аналитика будет недоступна из-за ненайденных колонок.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

from _shared import DB_PATH, db_schema_version, page_setup, release_db

from reportsdb.config import RAW_DIR, SCHEMA_VERSION, load_mapping, resolve_columns
from reportsdb.etl import build
from reportsdb.excel import list_sheets, read_sheet

page_setup("Загрузка данных", "📥")

SUFFIXES = {".xlsx", ".xls", ".xlsm", ".csv", ".tsv"}
NO_FILE = "— не загружать —"

# Что означает каждое поле модели и что сломается без него. Группы совпадают со
# смысловыми блоками исходного файла, чтобы таблицу проверки было легко читать.
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
    ("exec_count", "Кол-во обращений", "Статистика",
     "Не определить невостребованные отчёты"),
    ("avg_duration_sec", "Ср. дл. (сек)", "Статистика",
     "Не будет анализа времени выполнения"),
    ("avg_duration_ms", "Длительность в мс", "Статистика (запасной вариант)",
     "Используется, если нет колонки в секундах"),
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


# --- Текущее состояние базы ------------------------------------------------

st.subheader("Текущее состояние")

if not DB_PATH.exists():
    st.info("База ещё не собрана. Загрузите файл с отчётами — это займёт секунды.")
else:
    # Эта страница — путь восстановления, поэтому она обязана открываться на
    # ЛЮБОЙ базе, включая собранную прежней версией. Отсюда мягкие запросы:
    # ни один сбой чтения не должен помешать нажать «Загрузить».
    run = counts = None
    version = 0
    probe = duckdb.connect(str(DB_PATH), read_only=True)
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

col1, col2, col3 = st.columns(3)
reports_file = col1.selectbox(
    "Отчёты (обязательно)", files,
    help="Строка на отчёт: №, ТС, завод, каталог тремя уровнями, наименование, "
         "признак view, таблицы-источники, средняя длительность, число обращений.",
)
sizes_file = col2.selectbox(
    "Размеры таблиц", [NO_FILE] + files,
    help="Выгрузка сегментов БД: OWNER, SEGMENT_NAME, SEGMENT_TYPE, SIZE_MB, "
         "PERCENT_OF_TOTAL. Строка на сегмент, а не на таблицу.",
)
usage_file = col3.selectbox(
    "Статистика отдельным файлом", [NO_FILE] + files,
    help="Обычно не нужен: обращения и длительность уже есть в основном файле. "
         "Если выбран — его значения перекроют данные основного файла.",
)

mapping = load_mapping()

# Лист книги выбирается явно, если листов несколько.
sheets = list_sheets(RAW_DIR / reports_file)
if len(sheets) > 1:
    mapping.reports.sheet = st.selectbox("Лист с отчётами", sheets)
    st.caption(f"В книге {len(sheets)} листов — выберите нужный.")

# --- Шаг 3: проверка колонок всех выбранных файлов -------------------------

st.subheader("Шаг 3. Проверка колонок")

try:
    preview = read_sheet(RAW_DIR / reports_file, mapping.reports)
except Exception as exc:  # noqa: BLE001 — показываем причину пользователю
    st.error(f"Не удалось прочитать файл:\n\n```\n{exc}\n```")
    st.stop()

resolved = resolve_columns(list(preview.columns), mapping.reports.columns)

tab_names = ["Отчёты"]
if sizes_file != NO_FILE:
    tab_names.append("Размеры таблиц")
if usage_file != NO_FILE:
    tab_names.append("Статистика")
tabs = st.tabs(tab_names)

all_losses: list[str] = []

with tabs[0]:
    all_losses += show_mapping("Файл отчётов", resolved, REPORT_FIELDS)
    with st.expander(f"Первые строки файла (всего {len(preview)})"):
        st.dataframe(preview.head(10), use_container_width=True, hide_index=True)

index = 1
if sizes_file != NO_FILE:
    with tabs[index]:
        try:
            sizes_preview = read_sheet(RAW_DIR / sizes_file, mapping.table_sizes)
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
        with st.expander(f"Первые строки файла (всего {len(sizes_preview)})"):
            st.dataframe(sizes_preview.head(10), use_container_width=True, hide_index=True)
    index += 1

if usage_file != NO_FILE:
    with tabs[index]:
        try:
            usage_preview = read_sheet(RAW_DIR / usage_file, mapping.report_usage)
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
        st.info(
            "Значения из этого файла **перекроют** обращения и длительность из "
            "основного файла: он считается более свежим источником."
        )
        with st.expander(f"Первые строки файла (всего {len(usage_preview)})"):
            st.dataframe(usage_preview.head(10), use_container_width=True, hide_index=True)

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
    mapping.table_sizes.file = None if sizes_file == NO_FILE else sizes_file
    mapping.report_usage.file = None if usage_file == NO_FILE else usage_file

    release_db()  # отпускаем файл БД до пересборки

    try:
        with st.spinner("Читаю файлы и собираю базу…"):
            stats = build(RAW_DIR / reports_file, DB_PATH, mapping)
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
    if stats.unparsed_refs:
        st.warning(
            f"Ссылок на таблицы без схемы: {stats.unparsed_refs}. "
            "Схема таких таблиц записана как «(unknown)»."
        )
    if stats.segments_skipped:
        st.info(
            f"Пропущено {stats.segments_skipped} сегментов не-табличных типов "
            "(индексы, LOB). Так и задумано: привязать их к таблице по выгрузке "
            "нельзя, поэтому в объём таблиц они не входят."
        )
    if stats.tables_only_in_sizes:
        st.info(
            f"{stats.tables_only_in_sizes} таблиц есть только в файле размеров и "
            "не используются ни одним отчётом. Они загружены и видны в таблице "
            "№1 — список таблиц не зависит от отчётов."
        )
    if stats.size_plants > 1:
        st.caption(f"В файле размеров различается {stats.size_plants} пар «сеть + завод».")
    if stats.usage_unmatched:
        st.warning(
            f"Статистика по {len(stats.usage_unmatched)} отчётам не сопоставилась "
            f"с каталогом. Например: {', '.join(stats.usage_unmatched[:5])}"
        )

    con = duckdb.connect(str(DB_PATH), read_only=True)
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

# --- Экспорт для коллег ----------------------------------------------------

st.divider()
st.subheader("Файл для коллег")
st.caption(
    "Соберёт один HTML-файл со всеми данными внутри: его можно отправить по "
    "почте, открывается двойным кликом, ничего устанавливать не нужно."
)

if st.button("Собрать HTML-файл", disabled=not DB_PATH.exists()):
    from reportsdb.export_html import export

    out = export(DB_PATH)
    st.success(f"Готово: `{out}` ({out.stat().st_size / 1024:.0f} КБ)")
    st.download_button(
        "Скачать reportsdb.html",
        out.read_bytes(),
        file_name=f"reportsdb_{datetime.now():%Y%m%d}.html",
        mime="text/html",
    )
