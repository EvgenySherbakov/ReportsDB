"""Критичность таблиц-источников: кто от кого зависит."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import ACCENT, download, page_setup, query, show_table

page_setup("Таблицы-источники", "🗃️")

df = query("SELECT * FROM v_table_criticality")

c1, c2, c3 = st.columns([2, 2, 3])
schema = c1.selectbox("Схема", ["(все)"] + sorted(df["schema_name"].unique().tolist()))
only_orphans = c2.checkbox(
    "Только «сироты»", help="Таблицы, на которые не ссылается ни один отчёт."
)
search = c3.text_input("Поиск по имени таблицы", "")

view = df.copy()
if schema != "(все)":
    view = view[view["schema_name"] == schema]
if only_orphans:
    view = view[view["is_orphan"]]
if search:
    view = view[view["full_name"].str.contains(search, case=False, na=False)]

k1, k2, k3 = st.columns(3)
k1.metric("Таблиц в выборке", len(view))
k2.metric("Из них «сирот»", int(view["is_orphan"].sum()))
k3.metric(
    "Суммарный объём, МБ",
    f"{view['total_mb'].sum():,.0f}".replace(",", " ") if view["total_mb"].notna().any() else "—",
)
if view["segment_count"].fillna(0).gt(1).any():
    st.caption(
        f"У {int(view['segment_count'].fillna(0).gt(1).sum())} таблиц размер "
        "сложен из нескольких сегментов (секции). Индексные и LOB-сегменты в "
        "объём таблицы не входят."
    )

st.subheader("Топ-20 самых востребованных таблиц")
top = view.nlargest(20, "report_count")
if not top.empty:
    fig = px.bar(
        top,
        x="report_count",
        y="full_name",
        orientation="h",
        labels={"report_count": "Зависимых отчётов", "full_name": ""},
        color_discrete_sequence=[ACCENT],
    )
    fig.update_layout(height=600, margin=dict(l=0, r=0, t=10, b=0),
                      yaxis=dict(categoryorder="total ascending"))
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Изменение схемы этих таблиц затронет наибольшее число отчётов.")

st.subheader("Таблицы")
shown = show_table(
    view.sort_values(["report_count", "total_mb"], ascending=False)[
        ["full_name", "schema_name", "report_count", "total_mb", "percent_of_total",
         "segment_count", "row_count", "is_orphan", "is_parsed_ok", "reports"]
    ],
    {
        "reports": st.column_config.TextColumn("Зависимые отчёты", width="large"),
        "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
        "percent_of_total": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
        "segment_count": st.column_config.NumberColumn(
            "Сегментов", help="Сколько строк выгрузки сложилось в размер таблицы: "
                              "у секционированных таблиц больше одной."),
        "is_orphan": st.column_config.CheckboxColumn("Сирота"),
        "is_parsed_ok": st.column_config.CheckboxColumn("Схема распознана"),
    },
)
download(shown, "table_criticality.csv")

st.divider()
st.subheader("Отчёты с почти одинаковым набором источников")
overlap = query("SELECT * FROM v_report_overlap")
if overlap.empty:
    st.caption("Пар с коэффициентом Жаккара ≥ 0.8 не найдено.")
else:
    st.caption(
        "Кандидаты на объединение: наборы таблиц совпадают на 80% и более. "
        "Совпадение источников не гарантирует совпадения логики — проверяйте вручную."
    )
    st.dataframe(overlap, use_container_width=True, hide_index=True)
    download(overlap, "report_overlap.csv")
