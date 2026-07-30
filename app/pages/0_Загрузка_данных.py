"""Загрузка данных из файлов через интерфейс.

Коллеге не нужна командная строка: положить файлы в data/raw/ (или перетащить
их сюда), выбрать, что чем является, и нажать «Загрузить».
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from _shared import DB_PATH, page_setup, release_db

from reportsdb.config import RAW_DIR, load_mapping, resolve_columns
from reportsdb.etl import build
from reportsdb.excel import list_sheets, read_sheet

page_setup("Загрузка данных", "📥")

SUFFIXES = {".xlsx", ".xls", ".xlsm", ".csv", ".tsv"}
NO_FILE = "— не загружать —"


def raw_files() -> list[str]:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(
        p.name for p in RAW_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUFFIXES
    )


# --- Текущее состояние базы ------------------------------------------------

st.subheader("Текущее состояние")

if not DB_PATH.exists():
    st.info("База ещё не собрана. Загрузите файл с отчётами — это займёт секунды.")
else:
    import duckdb

    probe = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        run = probe.execute(
            "SELECT started_at, source_file, rows_loaded, rows_rejected FROM etl_run "
            "ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
        counts = probe.execute(
            "SELECT (SELECT COUNT(*) FROM dim_report), (SELECT COUNT(*) FROM dim_table), "
            "(SELECT COUNT(*) FROM fact_table_size), (SELECT COUNT(*) FROM fact_report_usage)"
        ).fetchone()
    finally:
        probe.close()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Отчётов", counts[0])
    c2.metric("Таблиц", counts[1])
    c3.metric("Строк с размерами", counts[2])
    c4.metric("Строк со статистикой", counts[3])
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
    help="Файл со строкой на отчёт: №, наименование, каталог, таблицы-источники.",
)
sizes_file = col2.selectbox(
    "Размеры таблиц (если есть)", [NO_FILE] + files,
    help="Строк, МБ данных и индексов по каждой таблице. Можно загрузить позже.",
)
usage_file = col3.selectbox(
    "Частота использования (если есть)", [NO_FILE] + files,
    help="Число запусков отчёта за период. Можно загрузить позже.",
)

mapping = load_mapping()

# Лист книги выбирается явно, если листов несколько.
sheets = list_sheets(RAW_DIR / reports_file)
if len(sheets) > 1:
    mapping.reports.sheet = st.selectbox("Лист с отчётами", sheets)
    st.caption(f"В книге {len(sheets)} листов — выберите нужный.")

# --- Шаг 3: предпросмотр сопоставления ------------------------------------

st.subheader("Шаг 3. Проверка колонок")

try:
    preview = read_sheet(RAW_DIR / reports_file, mapping.reports)
except Exception as exc:  # noqa: BLE001 — показываем причину пользователю
    st.error(f"Не удалось прочитать файл:\n\n```\n{exc}\n```")
    st.stop()

resolved = resolve_columns(list(preview.columns), mapping.reports.columns)
rows = [
    {
        "Поле модели": field,
        "Колонка в файле": column or "— не найдена —",
        "Статус": "✅" if column else ("❌ обязательна" if field == "report_name" else "—"),
    }
    for field, column in resolved.items()
]
st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

if not resolved.get("report_name"):
    st.error(
        "Не найдена колонка с наименованием отчёта — загрузка невозможна.\n\n"
        f"Заголовки файла: `{', '.join(map(str, preview.columns))}`\n\n"
        "Добавьте фактический заголовок в `config/mapping.yml`, раздел "
        "`reports.columns.report_name`, и обновите страницу."
    )
    st.stop()

if not resolved.get("source_tables"):
    st.warning(
        "Колонка со списком таблиц-источников не найдена. Отчёты загрузятся, "
        "но анализ объёма данных работать не будет."
    )

with st.expander(f"Первые строки файла ({len(preview)} всего)"):
    st.dataframe(preview.head(10), use_container_width=True, hide_index=True)

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

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Прочитано строк", stats.rows_read)
    m2.metric("Отчётов загружено", stats.rows_loaded)
    m3.metric("Уникальных таблиц", stats.tables)
    m4.metric("Связей", stats.links)

    if stats.rows_rejected:
        st.warning(f"Отброшено строк: {stats.rows_rejected} — см. ниже.")
    if stats.unparsed_refs:
        st.warning(
            f"Ссылок на таблицы без схемы: {stats.unparsed_refs}. "
            "Схема таких таблиц записана как «(unknown)»."
        )
    if stats.sizes_unmatched:
        st.warning(
            f"Размеры для {len(stats.sizes_unmatched)} таблиц не сопоставились с "
            "отчётами — таких таблиц нет ни в одном отчёте. "
            f"Например: {', '.join(stats.sizes_unmatched[:5])}"
        )
    if stats.usage_unmatched:
        st.warning(
            f"Статистика по {len(stats.usage_unmatched)} отчётам не сопоставилась "
            f"с каталогом. Например: {', '.join(stats.usage_unmatched[:5])}"
        )

    import duckdb

    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rejects = con.execute(
            "SELECT source_row, reason, payload FROM etl_reject"
        ).df()
        rejects.columns = ["Строка файла", "Причина", "Данные строки"]
    finally:
        con.close()
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
