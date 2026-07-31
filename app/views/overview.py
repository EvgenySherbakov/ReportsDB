"""Обзорная страница."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import (
    ACCENT,
    download,
    missing_facts_notice,
    num,
    page_setup,
    query,
    rc_selector,
    show_table,
)

page_setup("Обзор отчётности SSRS", "🗂️")
missing_facts_notice()

network, plant = rc_selector()

# Все показатели страницы считаются в разрезе выбранного РЦ. Отчёты фильтруются
# по своим сети и заводу, таблицы — по своим: размер одной таблицы на разных
# заводах разный, и складывать заводы в один итог нельзя.
scope = "по всем ТС и заводам" if network is None else f"{network} · {plant}"
st.subheader(f"Показатели: {scope}")

if network is None:
    where_reports, where_tables, params = "TRUE", "TRUE", ()
else:
    where_reports = (
        "COALESCE(network, '(не указана)') = ? AND COALESCE(plant, '(не указан)') = ?"
    )
    where_tables = "network = ? AND plant = ?"
    params = (network, plant)

kpi = query(
    f"""
    SELECT
        (SELECT COUNT(*) FROM dim_report WHERE {where_reports})          AS reports,
        (SELECT COUNT(DISTINCT full_name) FROM v_tables_catalog
          WHERE {where_tables})                                          AS tables,
        (SELECT ROUND(SUM(total_mb), 1) FROM v_tables_catalog
          WHERE {where_tables})                                          AS total_mb
    """,
    params * 3 if params else (),
).iloc[0]

c1, c2, c3 = st.columns(3)
c1.metric("Отчётов", num(kpi.reports))
c2.metric(
    "Таблиц", num(kpi.tables),
    help="Из таблицы №1 «Таблицы и размеры» — то есть из файла размеров. "
         "Считаются сами таблицы, а не строки «таблица + завод».",
)
c3.metric(
    "Объём таблиц, МБ", num(kpi.total_mb, decimals=1),
    help="Сумма размеров таблиц из файла размеров по выбранному разрезу.",
)

st.divider()

left, right = st.columns([3, 2])

with left:
    st.subheader("Отчёты по папкам каталога")
    catalog = query(
        f"""
        SELECT folder_l1, folder_l2, folder_l3, COUNT(*) AS report_count
        FROM dim_report
        WHERE {where_reports}
        GROUP BY 1, 2, 3
        ORDER BY report_count DESC
        """,
        params,
    )
    # На диаграмме — верхний уровень; полная иерархия ниже в таблице.
    by_l1 = catalog.groupby("folder_l1", as_index=False).agg(
        report_count=("report_count", "sum"))
    if not catalog.empty:
        fig = px.bar(
            by_l1.sort_values("report_count"),
            x="report_count",
            y="folder_l1",
            orientation="h",
            labels={"report_count": "Отчётов", "folder_l1": ""},
            color_discrete_sequence=[ACCENT],
        )
        fig.update_layout(height=max(260, 34 * len(by_l1)), margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)
    shown = show_table(catalog, {
        "folder_l1": "Каталог 1",
        "folder_l2": "Каталог 2",
        "folder_l3": "Каталог 3",
        "report_count": "Отчётов",
    })
    download(shown, "catalog_overview.csv")

with right:
    st.subheader("Схемы БД")
    # Схемы тоже в разрезе РЦ: таблицы берутся из каталога размеров.
    schemas = query(
        f"""
        -- LOWER на случай базы, собранной до приведения схем к одному регистру.
        SELECT LOWER(schema_name) AS schema_name,
               COUNT(DISTINCT full_name) AS table_count,
               ROUND(SUM(total_mb), 1)   AS total_mb,
               ROUND(SUM(percent_of_total), 2) AS percent_of_db
        FROM v_tables_catalog
        WHERE {where_tables}
        GROUP BY 1
        ORDER BY total_mb DESC NULLS LAST
        """,
        params,
    )
    if not schemas.empty and schemas["total_mb"].notna().any():
        sized = schemas.dropna(subset=["total_mb"]).sort_values("total_mb")
        fig = px.bar(
            sized,
            x="total_mb",
            y="schema_name",
            orientation="h",
            labels={"total_mb": "Объём, МБ", "schema_name": ""},
            color_discrete_sequence=[ACCENT],
        )
        fig.update_layout(height=max(260, 34 * len(sized)), margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)
    show_table(schemas, {
        "percent_of_db": st.column_config.NumberColumn("Доля БД, %", format="%.2f"),
        "table_count": "Таблиц",
        "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
    })

st.divider()
st.subheader("Качество исходных данных")

q1, q2 = st.columns(2)
with q1:
    rejects = query("SELECT source_row, reason, payload FROM etl_reject")
    st.caption(f"Отброшено строк при загрузке: **{len(rejects)}**")
    if not rejects.empty:
        show_table(rejects, {
            "source_row": "Строка файла", "reason": "Причина", "payload": "Данные строки",
        }, height=200)
with q2:
    unparsed = query(
        "SELECT full_name, table_name FROM dim_table WHERE NOT is_parsed_ok ORDER BY 1"
    )
    st.caption(f"Ссылок на таблицы без схемы: **{len(unparsed)}**")
    if not unparsed.empty:
        show_table(unparsed, {"full_name": "Таблица", "table_name": "Имя таблицы"}, height=200)

run = query("SELECT * FROM etl_run ORDER BY run_id DESC LIMIT 1")
if not run.empty:
    r = run.iloc[0]
    st.caption(
        f"Загружено из `{r.source_file}` — {r.started_at:%Y-%m-%d %H:%M}, "
        f"версия {r.tool_version}, sha256 `{r.source_sha256[:12]}…`"
    )
