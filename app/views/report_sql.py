"""Запросы к БД: текст SQL, которым формируется каждый отчёт.

Отдельная страница, а не только вкладка в карточке отчёта: карточка отвечает на
вопрос «всё про этот отчёт», а здесь обратный порядок — искать по **тексту
запроса**. Так находится то, что по списку таблиц не видно: обращения через
`JOIN` внутри подзапроса, хинты, конкретные условия фильтрации, вызовы функций.

Поиск идёт и по тексту запроса тоже, поэтому «какие отчёты трогают эту
таблицу» отвечается прямо здесь — включая случаи, когда таблица не попала в
колонку «Таблицы источники данных» исходного файла.
"""

from __future__ import annotations

import streamlit as st

from _shared import (
    is_blank,
    num,
    page_setup,
    query,
    rc_scope,
    rc_selector,
    row_picker,
    search_box,
    show_table,
    table_height,
)

page_setup("Запросы к БД", "🧾")
st.caption(
    "Текст запроса, которым формируется отчёт. Загружается отдельным файлом на "
    "странице **Загрузка данных** (роль «SQL-запросы»). Поиск работает и по "
    "тексту запроса — так видно обращения к таблице, которых нет в колонке "
    "«Таблицы источники данных»."
)

data = query(
    """
    SELECT report_id, report_no, report_name, network, plant, catalog_path,
           uses_view, sql_text,
           LENGTH(sql_text)                                   AS sql_chars,
           -- Строк в запросе: перевод строки внутри ячейки Excel сохраняется,
           -- и по числу строк сразу видно, где запрос на две строки, а где на
           -- двести. Пустой текст даёт NULL, а не 1 — считать нечего.
           CASE WHEN sql_text IS NULL THEN NULL
                ELSE LENGTH(sql_text) - LENGTH(REPLACE(sql_text, chr(10), '')) + 1
           END                                                AS sql_lines
    FROM dim_report
    """
)
if data.empty:
    st.info("Отчёты не загружены. Откройте **Данные → Загрузка данных**.")
    st.stop()

with_sql_total = int(data["sql_text"].notna().sum())
if with_sql_total == 0:
    st.warning(
        "**Ни у одного отчёта нет текста запроса.** Файл SQL-запросов не "
        "загружен.\n\n**Что сделать:** откройте **Данные → Загрузка данных**, "
        "в шаге 2 выберите файл в поле **«SQL-запросы»** и нажмите "
        "«Загрузить». Ожидаемые колонки: `№`, `ТС`, `Завод`, "
        "`Каталог 1/2/3-го уровня`, `Наименование отчета`, "
        "`Запрос к базе данных`.",
        icon="📄",
    )
    st.stop()

f1, f2 = st.columns([2, 3])
with f1:
    network, plant = rc_selector()
with f2:
    only_with_sql = st.checkbox(
        "Только отчёты с запросом", value=True,
        help="Снимите, чтобы увидеть и те отчёты, для которых текст запроса не "
             "загружен — их не было в файле либо строка не сопоставилась.",
    )

scoped = rc_scope(data, network, plant).copy()
scoped["network"] = scoped["network"].fillna("(не указана)")
scoped["plant"] = scoped["plant"].fillna("(не указан)")

# --- Плитки ------------------------------------------------------------------
# По показанному разрезу, а не по всей базе: иначе цифра сверху не сходилась бы
# с таблицей под ней.

with_sql = scoped[scoped["sql_text"].notna()]

k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "Отчётов с запросом", num(len(with_sql)),
    help=f"Из {num(len(scoped))} отчётов в этом разрезе.",
)
k2.metric(
    "Доля с запросом",
    f"{100.0 * len(with_sql) / len(scoped):.0f}%" if len(scoped) else "—",
)
k3.metric(
    "Медиана строк в запросе", num(with_sql["sql_lines"].median()),
    help="Половина запросов короче этого, половина длиннее. Медиана, а не "
         "среднее: один запрос на тысячу строк перекосил бы среднее.",
)
k4.metric(
    "Самый длинный, строк", num(with_sql["sql_lines"].max()),
)

st.divider()

listed = scoped[scoped["sql_text"].notna()] if only_with_sql else scoped
listed = search_box(
    listed, ["report_name", "catalog_path", "sql_text", "report_no"],
    "Поиск по отчёту, каталогу или тексту запроса", key="s_report_sql",
)

if listed.empty:
    st.info("Ничего не найдено. Измените условие поиска.")
    st.stop()

listed = listed.sort_values("sql_lines", ascending=False, na_position="last")

picked = row_picker(
    listed, "report_id", "report_sql",
    [
        ("Завод", 12, lambda r: r["plant"]),
        ("Отчёт", 38, lambda r: r["report_name"]),
        ("Строк", 7, lambda r: num(r["sql_lines"]), True),
        ("Знаков", 8, lambda r: num(r["sql_chars"]), True),
        ("Каталог", 30, lambda r: r["catalog_path"]),
    ],
)

if picked is None:
    st.info("👆 Щёлкните по строке отчёта, чтобы увидеть его запрос.")
    st.stop()

# --- Запрос выбранного отчёта -------------------------------------------------

st.divider()
st.subheader(picked["report_name"])
st.caption(
    f"№ {picked['report_no'] or '—'} · {picked['network']} · {picked['plant']} · "
    f"`{picked['catalog_path']}`"
)

if is_blank(picked["sql_text"]):
    st.warning(
        "Для этого отчёта текст запроса не загружен: его не было в файле "
        "SQL-запросов либо строка не сопоставилась с каталогом отчётов.",
        icon="📄",
    )
    st.stop()

m1, m2 = st.columns(2)
m1.metric("Строк в запросе", num(picked["sql_lines"]))
m2.metric("Знаков", num(picked["sql_chars"]))

if picked["uses_view"] is True:
    st.info(
        "Отчёт помечен как работающий через view: в запросе ниже за view могут "
        "стоять таблицы, которых нет ни в списке источников, ни в самом тексте.",
        icon="👁️",
    )

st.code(picked["sql_text"], language="sql")
st.download_button(
    "Выгрузить запрос (.sql)",
    picked["sql_text"].encode("utf-8"),
    file_name=f"report_{int(picked['report_id'])}.sql",
    mime="text/plain",
)

# --- Таблицы отчёта рядом с запросом ------------------------------------------
# Список источников из исходного файла — рядом с текстом запроса: расхождение
# между ними и есть то, что эта страница помогает заметить.

st.subheader("Заявленные источники отчёта")
st.caption(
    "Из колонок исходного файла. Если в запросе выше есть таблица, которой нет "
    "здесь, — список источников в файле неполон, и объём отчёта занижен."
)

sources = query(
    """
    SELECT t.full_name, t.object_kind, t.kind_source
    FROM bridge_report_table b
    JOIN dim_table t ON t.table_id = b.table_id
    WHERE b.report_id = ?
    ORDER BY t.object_kind, t.full_name
    """,
    (int(picked["report_id"]),),
)
if sources.empty:
    st.caption("У отчёта не указано ни одного объекта-источника.")
else:
    # Какие из заявленных источников действительно упомянуты в тексте: сравнение
    # по имени без схемы тоже — в запросах таблицу пишут и со схемой, и без.
    text_lower = picked["sql_text"].lower()
    sources["Есть в запросе"] = sources["full_name"].apply(
        lambda name: "да"
        if name.lower() in text_lower
        or name.lower().rsplit(".", 1)[-1] in text_lower
        else "нет"
    )
    show_table(
        sources,
        {
            "object_kind": st.column_config.TextColumn("Тип объекта"),
            "kind_source": st.column_config.TextColumn("Тип определён"),
            "Есть в запросе": st.column_config.TextColumn(
                "Есть в запросе",
                help="Упомянуто ли имя объекта в тексте запроса. «нет» бывает "
                     "законно: объект может приходить через view или "
                     "процедуру.",
            ),
        },
        height=table_height(len(sources), 320),
    )
