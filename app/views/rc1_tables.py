"""№1. Таблицы и размеры — один объект на РЦ."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import ACCENT, download, page_setup, query, rc_scope, rc_selector, search_box, show_table

page_setup("№1. Таблицы и размеры", "1️⃣")
st.caption(
    "Одна строка на объект внутри РЦ. Включены таблицы и материализованные "
    "view — то, что реально занимает место. Обычные view, временные объекты и "
    "процедуры сюда не входят."
)

network, plant = rc_selector()
df = rc_scope(query("SELECT * FROM v_rc_tables"), network, plant)

# Одна таблица обслуживает отчёты нескольких РЦ, её размер — свойство таблицы,
# а не РЦ. В показателях и на диаграмме объекты берутся по одному разу.
unique = df.drop_duplicates(subset=["full_name"])

c1, c2, c3, c4 = st.columns(4)
c1.metric("Объектов", len(unique))
c2.metric(
    "Суммарный объём, МБ",
    f"{unique['total_mb'].sum():,.0f}".replace(",", " ")
    if unique["total_mb"].notna().any() else "—",
    help="Каждый объект посчитан один раз, без двойного учёта общих таблиц.",
)
c3.metric(
    "Доля БД, %",
    f"{unique['percent_of_total'].sum():.2f}"
    if unique["percent_of_total"].notna().any() else "—",
)
c4.metric(
    "Без размера", int(unique["size_unknown"].sum()) if len(unique) else 0,
    help="Объектов нет в файле размеров: объём неизвестен, а не равен нулю.",
)

if network is None and len(df) > len(unique):
    st.caption(
        f"Строк с учётом принадлежности к РЦ — {len(df)}, уникальных объектов — "
        f"{len(unique)}. Показатели считают каждый объект один раз."
    )

if unique["total_mb"].notna().any():
    top = unique.nlargest(20, "total_mb")
    fig = px.bar(
        top, x="total_mb", y="full_name", orientation="h",
        labels={"total_mb": "Объём, МБ", "full_name": ""},
        hover_data=["object_kind", "retention_days", "report_count"],
        color_discrete_sequence=[ACCENT],
    )
    fig.update_layout(
        height=max(300, 30 * len(top)), margin=dict(l=0, r=0, t=10, b=0), bargap=0.35,
        yaxis=dict(categoryorder="array", categoryarray=top["full_name"].tolist()[::-1]),
    )
    st.plotly_chart(fig, use_container_width=True)

view = search_box(df, ["full_name", "schema_name", "table_name"],
                  "Поиск по наименованию таблицы", key="s_rc1")
shown = show_table(
    view.sort_values("total_mb", ascending=False),
    {
        "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
        "percent_of_total": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
        "retention_days": st.column_config.NumberColumn("Глубина, дней"),
        "size_unknown": st.column_config.CheckboxColumn("Размер неизвестен"),
    },
)
download(shown, "rc_1_tables.csv")
