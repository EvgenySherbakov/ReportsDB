"""№3. Отчёт → функции и процедуры."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import ACCENT, download, page_setup, query, rc_scope, rc_selector, search_box, show_table

page_setup("№3. Отчёт → функции и процедуры", "3️⃣")

network, plant = rc_selector()
df = rc_scope(query("SELECT * FROM v_rc_report_routines"), network, plant)

if df.empty:
    st.info(
        "Функции и процедуры не загружены. Добавьте в файл отчётов колонку "
        "**«Функции/процедуры»** со списком через `;` — витрина заполнится "
        "автоматически, править конфиг не нужно."
    )
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Связей отчёт → процедура", len(df))
c2.metric("Отчётов", df["report_name"].nunique())
c3.metric("Уникальных процедур", df["routine_full_name"].nunique())

st.subheader("Самые востребованные процедуры")
top = (
    df.groupby("routine_full_name", as_index=False)
    .agg(report_count=("report_name", "nunique"))
    .nlargest(15, "report_count")
)
fig = px.bar(
    top, x="report_count", y="routine_full_name", orientation="h",
    labels={"report_count": "Отчётов", "routine_full_name": ""},
    color_discrete_sequence=[ACCENT],
)
fig.update_layout(
    height=max(280, 30 * len(top)), margin=dict(l=0, r=0, t=10, b=0), bargap=0.35,
    yaxis=dict(categoryorder="array", categoryarray=top["routine_full_name"].tolist()[::-1]),
)
st.plotly_chart(fig, use_container_width=True)
st.caption("Изменение этих процедур затронет наибольшее число отчётов.")

view = search_box(df, ["report_name", "routine_full_name", "schema_name"],
                  "Поиск по наименованию процедуры или отчёта", key="s_rc3")
shown = show_table(view, {"routine_full_name": "Функция / процедура", "routine_name": "Имя"})
download(shown, "rc_3_report_routines.csv")
