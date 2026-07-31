"""№4. Отчёт → обращения пользователей."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import ACCENT, download, page_setup, query, rc_scope, rc_selector, search_box, show_table

page_setup("№4. Отчёт → обращения", "4️⃣")
st.caption(
    "Мера пользовательской активности — «Кол-во обращений». Это число запусков, "
    "а не уникальных пользователей: если появится отдельная колонка с "
    "пользователями, она встанет рядом в столбце «Пользователей»."
)

network, plant = rc_selector()
df = rc_scope(query("SELECT * FROM v_rc_report_usage"), network, plant)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Отчётов", len(df))
c2.metric(
    "Всего обращений",
    f"{df['exec_count'].sum():,.0f}".replace(",", " ")
    if df["exec_count"].notna().any() else "—",
)
c3.metric("Не запускались", int((df["exec_count"] == 0).sum()))
c4.metric("Без данных", int(df["exec_count"].isna().sum()))

if df["exec_count"].notna().any():
    bands = ["Не запускался", "До 10 обращений", "От 10 до 100", "Более 100", "Нет данных"]
    counts = df.groupby("usage_band", as_index=False).agg(report_count=("report_name", "count"))
    counts["order"] = counts["usage_band"].apply(
        lambda b: bands.index(b) if b in bands else len(bands))
    counts = counts.sort_values("order")
    fig = px.bar(
        counts, x="usage_band", y="report_count",
        labels={"usage_band": "", "report_count": "Отчётов"},
        color_discrete_sequence=[ACCENT],
    )
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), bargap=0.4)
    st.plotly_chart(fig, use_container_width=True)

view = search_box(df, ["report_name", "catalog_path"],
                  "Поиск по наименованию отчёта", key="s_rc4")
shown = show_table(
    view.sort_values("exec_count", ascending=False),
    {
        "usage_band": "Группа по обращениям",
        "uses_view": st.column_config.CheckboxColumn("Через view"),
        "avg_duration_sec": st.column_config.NumberColumn("Ср. длительность, с", format="%.1f"),
        "total_duration_sec": st.column_config.NumberColumn("Суммарное время, с", format="%.0f"),
    },
)
download(shown, "rc_4_report_usage.csv")
