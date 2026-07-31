"""№1. Таблицы и размеры — список таблиц БД, и только он.

Единственный источник списка — файл размеров. Ничего к нему не добавляется:
объекты, упомянутые в отчётах, но отсутствующие в файле, сюда не попадают —
мост «отчёт ↔ таблица» только ссылается на этот список.
Размер ведётся на пару «сеть + завод»: на каждом заводе таблица весит своё.
"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import ACCENT, download, page_setup, query, search_box, show_table

page_setup("№1. Таблицы и размеры", "1️⃣")
st.caption(
    "Таблицы БД — ровно то, что есть в файле размеров: строка на пару "
    "«таблица + завод». Список самостоятелен и не зависит от отчётов: таблица "
    "попадает сюда, даже если на неё никто не ссылается, и не попадает, если "
    "её нет в файле размеров."
)

df = query("SELECT * FROM v_tables_catalog")
if df.empty:
    st.info(
        "Файл размеров не загружен. Откройте страницу **Загрузка данных**, "
        "выберите файл в поле «Размеры таблиц» и нажмите «Загрузить»."
    )
    st.stop()

ALL = "(все)"
f1, f2, f3 = st.columns(3)
network = f1.selectbox("Торговая сеть", [ALL] + sorted(df["network"].dropna().unique().tolist()))
view = df if network == ALL else df[df["network"] == network]
plant = f2.selectbox("Завод", [ALL] + sorted(view["plant"].dropna().unique().tolist()))
if plant != ALL:
    view = view[view["plant"] == plant]
usage = f3.selectbox(
    "Связь с отчётами", [ALL, "Только используемые", "Только неиспользуемые"],
    help="Список таблиц не зависит от отчётов; фильтр — справочный.",
)
if usage == "Только используемые":
    view = view[view["report_count"] > 0]
elif usage == "Только неиспользуемые":
    view = view[view["report_count"] == 0]

view = search_box(view, ["full_name", "schema_name", "table_name"],
                  "Поиск по наименованию таблицы", key="s_rc1")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Строк «таблица + завод»", len(view))
k2.metric("Уникальных таблиц", view["full_name"].nunique())
k3.metric(
    "Суммарный объём, МБ",
    f"{view['total_mb'].sum():,.0f}".replace(",", " ")
    if view["total_mb"].notna().any() else "—",
    help="Сумма по выбранным строкам. При выборе всех заводов это объём по "
         "всем площадкам сразу, а не размер одной таблицы.",
)
k4.metric(
    # Считаем именно таблицы, а не строки: одна таблица даёт строку на каждый
    # завод, и счёт по строкам завысил бы показатель в несколько раз.
    "Не используются отчётами", view.loc[view["report_count"] == 0, "full_name"].nunique(),
    help="Таблицы есть в файле размеров, но ни один отчёт на них не ссылается. "
         "Счёт по таблицам, а не по строкам «таблица + завод».",
)

if view["total_mb"].notna().any():
    top = view.nlargest(20, "total_mb")
    label = "full_name" if plant != ALL else None
    if label is None:
        top = top.assign(bar_label=top["full_name"] + " · " + top["plant"])
        label = "bar_label"
    fig = px.bar(
        top, x="total_mb", y=label, orientation="h",
        labels={"total_mb": "Объём, МБ", label: ""},
        hover_data=["network", "plant", "object_kind", "retention_days", "report_count"],
        color_discrete_sequence=[ACCENT],
    )
    fig.update_layout(
        height=max(300, 30 * len(top)), margin=dict(l=0, r=0, t=10, b=0), bargap=0.35,
        yaxis=dict(categoryorder="array", categoryarray=top[label].tolist()[::-1]),
    )
    st.plotly_chart(fig, use_container_width=True)
    if plant == ALL:
        st.caption("Выбран не один завод, поэтому в подписи указана площадка.")

shown = show_table(
    view.sort_values("total_mb", ascending=False),
    {
        "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
        "percent_of_total": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
        "retention_days": st.column_config.NumberColumn("Глубина, дней"),
        "report_count": st.column_config.NumberColumn("Отчётов"),
        "measured_at": "Дата замера",
    },
)
download(shown, "tables_catalog.csv")

st.caption(
    "Колонка «Отчётов» — справочная: показывает, сколько отчётов ссылается на "
    "таблицу. Связь отчётов с таблицами разбирается в таблице №2."
)

# Ссылки отчётов на объекты вне файла размеров в список не добавляются, но
# промолчать о них нельзя: у таких отчётов объём заведомо занижен.
dangling = query(
    "SELECT COUNT(*) AS n FROM dim_table t "
    "WHERE t.object_kind = 'TABLE' "
    "  AND EXISTS (SELECT 1 FROM bridge_report_table b WHERE b.table_id = t.table_id) "
    "  AND NOT EXISTS (SELECT 1 FROM fact_table_size s WHERE s.table_id = t.table_id)"
)["n"].iloc[0]
if dangling:
    st.caption(
        f"Отчёты ссылаются ещё на {dangling} таблиц, которых нет в файле "
        "размеров. В этот список они не добавляются — он повторяет файл "
        "размеров. Их видно в таблице №2, а объём таких отчётов занижен: "
        "смотрите «Покрытие размерами»."
    )
