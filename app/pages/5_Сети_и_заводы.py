"""Организационный разрез: торговые сети и заводы."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import ACCENT, download, missing_facts_notice, page_setup, query, show_table

page_setup("Сети и заводы", "🏭")
missing_facts_notice()

df = query("SELECT * FROM v_network_overview")

if df.empty:
    st.info("В загруженных данных нет колонок «ТС» и «Завод».")
    st.stop()

k1, k2, k3, k4 = st.columns(4)
k1.metric("Торговых сетей", df["network"].nunique())
k2.metric("Заводов", df["plant"].nunique())
k3.metric("Отчётов", int(df["report_count"].sum()))
k4.metric(
    "Через view",
    int(df["reports_with_view"].sum()),
    help="У таких отчётов список таблиц заведомо неполон: за view стоят другие таблицы.",
)

st.subheader("Отчёты по торговым сетям")
by_network = (
    df.groupby("network", as_index=False)
    .agg({"report_count": "sum", "exclusive_mb": "sum", "exec_count": "sum",
          "total_duration_sec": "sum"})
    .sort_values("report_count")
)
fig = px.bar(
    by_network,
    x="report_count",
    y="network",
    orientation="h",
    labels={"report_count": "Отчётов", "network": ""},
    color_discrete_sequence=[ACCENT],
)
fig.update_layout(height=max(240, 40 * len(by_network)), margin=dict(l=0, r=0, t=10, b=0))
st.plotly_chart(fig, use_container_width=True)

st.subheader("Сеть и завод")
shown = show_table(
    df.sort_values(["network", "plant"]),
    {
        "reports_with_view": "Отчётов через view",
        "exclusive_mb": st.column_config.NumberColumn("Освободится, МБ", format="%.1f"),
        "total_duration_sec": st.column_config.NumberColumn("Суммарное время, с", format="%.0f"),
    },
)
download(shown, "network_overview.csv")

st.caption(
    "«Освободится, МБ» — сумма эксклюзивных таблиц отчётов этой пары. Общие "
    "таблицы, которые используют отчёты других сетей, сюда не входят."
)
