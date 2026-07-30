"""Произвольный SQL-запрос к базе."""

from __future__ import annotations

import streamlit as st

from _shared import connect, download, page_setup

page_setup("SQL-запрос", "⌨️")

ROW_LIMIT = 5000

con = connect()
objects = con.execute(
    """
    SELECT table_name AS name, table_type AS kind
    FROM information_schema.tables
    WHERE table_schema = 'main'
    ORDER BY table_type DESC, table_name
    """
).df()

with st.sidebar:
    st.subheader("Объекты базы")
    for kind, group in objects.groupby("kind"):
        st.caption("Представления" if kind == "VIEW" else "Таблицы")
        for name in group["name"]:
            st.code(name, language=None)

PRESETS = {
    "Топ отчётов по освобождаемому объёму":
        "SELECT report_name, exclusive_mb, table_count, size_coverage_pct\n"
        "FROM v_report_footprint\nORDER BY exclusive_mb DESC\nLIMIT 50;",
    "Самые критичные таблицы":
        "SELECT full_name, report_count, total_mb\n"
        "FROM v_table_criticality\nORDER BY report_count DESC\nLIMIT 50;",
    "Отчёты конкретной таблицы":
        "SELECT r.report_name, r.catalog_path\n"
        "FROM dim_report r\n"
        "JOIN bridge_report_table b ON b.report_id = r.report_id\n"
        "JOIN dim_table t ON t.table_id = b.table_id\n"
        "WHERE t.full_name = 'dbo.orders'\nORDER BY 1;",
    "Отчёты без источников":
        "SELECT report_name, catalog_path\nFROM v_report_footprint\n"
        "WHERE table_count = 0\nORDER BY 1;",
}

preset = st.selectbox("Готовый запрос", ["(свой запрос)"] + list(PRESETS))
default = PRESETS.get(preset, "SELECT * FROM v_report_footprint LIMIT 20;")

sql = st.text_area("SQL", value=default, height=200)

if st.button("Выполнить", type="primary"):
    try:
        df = con.execute(sql).df()
    except Exception as exc:  # noqa: BLE001 — текст ошибки нужен пользователю
        st.error(f"Ошибка запроса:\n\n```\n{exc}\n```")
    else:
        if len(df) > ROW_LIMIT:
            st.info(f"Показаны первые {ROW_LIMIT} строк из {len(df)}.")
            df = df.head(ROW_LIMIT)
        st.success(f"Строк: {len(df)}")
        st.dataframe(df, use_container_width=True, hide_index=True)
        download(df, "query_result.csv")

st.caption("База открыта только на чтение — изменить данные запросом нельзя.")
