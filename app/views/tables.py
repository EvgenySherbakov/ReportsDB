"""Справочник таблиц: где используется и сколько весит."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import ACCENT, download, page_setup, query, search_box, show_table

page_setup("Таблицы", "🗃️")
st.caption(
    "Какая таблица в скольких отчётах используется и что сломается при её изменении."
)

df = query("SELECT * FROM v_table_criticality")

c1, c2 = st.columns([3, 2])
kind = c1.selectbox("Тип объекта", ["(все)"] + sorted(df["object_kind"].unique().tolist()))
only_orphans = c2.checkbox(
    "Только «сироты»", help="Объекты, на которые не ссылается ни один отчёт.")

if kind != "(все)":
    df = df[df["object_kind"] == kind]
if only_orphans:
    df = df[df["is_orphan"]]

view = search_box(df, ["full_name", "schema_name", "table_name", "reports"], key="s_tables")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Объектов", len(view))
k2.metric("Из них «сирот»", int(view["is_orphan"].sum()))
k3.metric(
    "Суммарный объём, МБ",
    f"{view['total_mb'].sum():,.0f}".replace(",", " ")
    if view["total_mb"].notna().any() else "—",
)
k4.metric(
    "Доля БД, %",
    f"{view['percent_of_total'].sum():.2f}"
    if view["percent_of_total"].notna().any() else "—",
)

top = view.nlargest(20, "report_count")
if not top.empty:
    fig = px.bar(
        top, x="report_count", y="full_name", orientation="h",
        labels={"report_count": "Зависимых отчётов", "full_name": ""},
        hover_data=["object_kind", "total_mb", "retention_days"],
        color_discrete_sequence=[ACCENT],
    )
    fig.update_layout(
        height=max(280, 30 * len(top)), margin=dict(l=0, r=0, t=10, b=0), bargap=0.35,
        yaxis=dict(categoryorder="array", categoryarray=top["full_name"].tolist()[::-1]),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Изменение схемы этих таблиц затронет наибольшее число отчётов.")

shown = show_table(
    view.sort_values(["report_count", "total_mb"], ascending=False)[
        ["full_name", "schema_name", "object_kind", "report_count", "total_mb",
         "percent_of_total", "retention_days", "row_count", "segment_count",
         "is_orphan", "reports"]
    ],
    {
        "reports": st.column_config.TextColumn("Зависимые отчёты", width="large"),
        "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
        "percent_of_total": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
        "retention_days": st.column_config.NumberColumn("Глубина, дней"),
        "is_orphan": st.column_config.CheckboxColumn("Сирота"),
    },
)
download(shown, "tables.csv")
