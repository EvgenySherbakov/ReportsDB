"""Справочник таблиц: где используется и сколько весит."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import (
    ACCENT,
    download,
    kind_ru,
    num,
    page_setup,
    query,
    search_box,
    show_table,
)

page_setup("Таблицы", "🗃️")
st.caption(
    "Все объекты-источники: таблицы, представления (view), материализованные "
    "view, временные таблицы, функции и процедуры. Показано, в скольких "
    "отчётах используется каждый и что сломается при его изменении."
)

df = query("SELECT * FROM v_table_criticality")
df["Тип"] = df["object_kind"].map(kind_ru)

c1, c2 = st.columns([3, 2])
# Фильтр по русским названиям: коды TABLE/VIEW в выпадающем списке заказчику
# ничего не говорят, а объект в базе всё равно хранится кодом.
kinds = sorted(df["Тип"].unique().tolist())
kind = c1.selectbox("Тип объекта", ["(все)"] + kinds)
only_unused = c2.checkbox(
    "Только те, что не используются отчётами",
    help="Объект есть в исходных данных, но на него не ссылается ни один "
         "отчёт. Раньше такие назывались «сироты».",
)

if kind != "(все)":
    df = df[df["Тип"] == kind]
if only_unused:
    df = df[df["is_orphan"]]

view = search_box(df, ["full_name", "schema_name", "table_name", "reports"], key="s_tables")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Объектов", num(len(view)))
k2.metric(
    "Не используются отчётами", num(int(view["is_orphan"].sum())),
    help="На объект не ссылается ни один отчёт из загруженной выгрузки.",
)
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

# Сколько объектов какого типа — до фильтра, чтобы было видно, что view в
# данных вообще есть, даже когда сейчас выбраны только таблицы.
by_kind = (
    query("SELECT * FROM v_table_criticality")
    .assign(Тип=lambda d: d["object_kind"].map(kind_ru))
    .groupby("Тип", as_index=False)
    .agg(objects=("full_name", "count"),
         used=("report_count", lambda c: int((c > 0).sum())),
         total_mb=("total_mb", "sum"))
    .sort_values("objects", ascending=False)
)
with st.expander(f"Каких объектов сколько ({len(by_kind)} типов)", expanded=True):
    st.dataframe(
        by_kind.rename(columns={
            "objects": "Объектов", "used": "Используются отчётами",
            "total_mb": "Объём, МБ",
        }),
        hide_index=True, use_container_width=True,
        column_config={"Объём, МБ": st.column_config.NumberColumn(format="%.0f")},
    )
    st.caption(
        "Тип берётся из колонки файла отчётов, в которой объект перечислен. "
        "Если такой колонки нет, работает маска имени из `config/mapping.yml` "
        "— по умолчанию префикс `v_` означает представление. Объект, найденный "
        "в файле размеров, остаётся таблицей в любом случае: у него есть "
        "сегменты, значит он физически хранится."
    )

top = view.nlargest(20, "report_count")
if not top.empty:
    fig = px.bar(
        top, x="report_count", y="full_name", orientation="h",
        labels={"report_count": "Зависимых отчётов", "full_name": ""},
        hover_data=["Тип", "total_mb", "retention_days"],
        color_discrete_sequence=[ACCENT],
    )
    fig.update_layout(
        height=max(280, 30 * len(top)), margin=dict(l=0, r=0, t=10, b=0), bargap=0.35,
        yaxis=dict(categoryorder="array", categoryarray=top["full_name"].tolist()[::-1]),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Изменение схемы этих таблиц затронет наибольшее число отчётов.")

recovered = int((view["schema_source"] == "файл размеров").sum())
if recovered:
    st.caption(
        f"Восстановлено схем по уникальному совпадению имени в файле "
        f"размеров: {recovered}. Отмечены в столбце «Схема определена»."
    )

shown = show_table(
    view.sort_values(["report_count", "total_mb"], ascending=False)[
        ["full_name", "schema_name", "Тип", "kind_source", "schema_source",
         "report_count", "total_mb", "percent_of_total", "retention_days",
         "row_count", "segment_count", "is_orphan", "reports"]
    ],
    {
        "Тип": st.column_config.TextColumn("Тип объекта"),
        "kind_source": st.column_config.TextColumn(
            "Тип определён", help="«колонка» — объект пришёл из своей колонки "
            "файла отчётов; «маска» — распознан по имени (например, "
            "префикс v_); «файл размеров» — есть сегменты в базе, значит "
            "физически хранится; «по умолчанию» — считаем таблицей."),
        "reports": st.column_config.TextColumn("Зависимые отчёты", width="large"),
        "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
        "percent_of_total": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
        "retention_days": st.column_config.NumberColumn("Глубина, дней"),
        "is_orphan": st.column_config.CheckboxColumn("Не используется отчётами"),
    },
)
download(shown, "tables.csv")
