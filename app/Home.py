"""Обзорная страница."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import ACCENT, download, missing_facts_notice, page_setup, query

page_setup("Обзор отчётности SSRS", "🗂️")
missing_facts_notice()

kpi = query(
    """
    SELECT
        (SELECT COUNT(*) FROM dim_report)                         AS reports,
        (SELECT COUNT(*) FROM dim_table)                          AS tables,
        (SELECT COUNT(*) FROM bridge_report_table)                AS links,
        (SELECT COUNT(*) FROM v_table_criticality WHERE is_orphan) AS orphans,
        (SELECT ROUND(SUM(total_mb), 1) FROM fact_table_size)     AS total_mb,
        (SELECT ROUND(AVG(size_coverage_pct), 0) FROM v_report_footprint) AS coverage
    """
).iloc[0]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Отчётов", f"{kpi.reports:,}".replace(",", " "))
c2.metric("Таблиц-источников", f"{kpi.tables:,}".replace(",", " "))
c3.metric("Связей", f"{kpi.links:,}".replace(",", " "))
c4.metric(
    "Объём таблиц, МБ",
    "—" if kpi.total_mb is None else f"{kpi.total_mb:,.0f}".replace(",", " "),
    help="Суммарный размер уникальных таблиц — без двойного учёта общих.",
)
c5.metric(
    "Покрытие размерами",
    "—" if kpi.coverage is None else f"{kpi.coverage:.0f}%",
    help="Средняя доля таблиц отчёта, для которых известен размер.",
)

st.divider()

left, right = st.columns([3, 2])

with left:
    st.subheader("Отчёты по папкам каталога")
    catalog = query("SELECT * FROM v_catalog_overview")
    if not catalog.empty:
        fig = px.bar(
            catalog.sort_values("report_count"),
            x="report_count",
            y="folder",
            orientation="h",
            labels={"report_count": "Отчётов", "folder": ""},
            color_discrete_sequence=[ACCENT],
        )
        fig.update_layout(height=max(260, 34 * len(catalog)), margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)
    st.dataframe(catalog, use_container_width=True, hide_index=True)
    download(catalog, "catalog_overview.csv")

with right:
    st.subheader("Схемы БД")
    schemas = query("SELECT * FROM v_schema_overview")
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
    st.dataframe(schemas, use_container_width=True, hide_index=True)

st.divider()
st.subheader("Качество исходных данных")

q1, q2 = st.columns(2)
with q1:
    rejects = query("SELECT source_row, reason, payload FROM etl_reject")
    st.caption(f"Отброшено строк при загрузке: **{len(rejects)}**")
    if not rejects.empty:
        st.dataframe(rejects, use_container_width=True, hide_index=True, height=200)
with q2:
    unparsed = query(
        "SELECT full_name, table_name FROM dim_table WHERE NOT is_parsed_ok ORDER BY 1"
    )
    st.caption(f"Ссылок на таблицы без схемы: **{len(unparsed)}**")
    if not unparsed.empty:
        st.dataframe(unparsed, use_container_width=True, hide_index=True, height=200)

run = query("SELECT * FROM etl_run ORDER BY run_id DESC LIMIT 1")
if not run.empty:
    r = run.iloc[0]
    st.caption(
        f"Загружено из `{r.source_file}` — {r.started_at:%Y-%m-%d %H:%M}, "
        f"версия {r.tool_version}, sha256 `{r.source_sha256[:12]}…`"
    )
