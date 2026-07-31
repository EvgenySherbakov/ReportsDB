"""№2. Отчёт → таблицы, один ко многим."""

from __future__ import annotations

import streamlit as st

from _shared import download, page_setup, query, rc_scope, rc_selector, search_box, show_table

page_setup("№2. Отчёт → таблицы", "2️⃣")
st.caption(
    "Только настоящие таблицы. View, материализованные view, временные и "
    "generated-объекты исключены — они не отражают физического хранения."
)

network, plant = rc_selector()
df = rc_scope(query("SELECT * FROM v_rc_report_tables"), network, plant)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Связей отчёт → таблица", len(df))
c2.metric("Отчётов", df["report_name"].nunique())
c3.metric("Уникальных таблиц", df["table_full_name"].nunique())
c4.metric(
    "Таблиц на отчёт, среднее",
    f"{len(df) / df['report_name'].nunique():.1f}" if df["report_name"].nunique() else "—",
)

view = search_box(df, ["report_name", "table_full_name", "schema_name", "catalog_path"],
                  key="s_rc2")
shown = show_table(
    view,
    {
        "table_full_name": "Таблица",
        "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
        "percent_of_total": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
        "retention_days": st.column_config.NumberColumn("Глубина, дней"),
    },
)
download(shown, "rc_2_report_tables.csv")
