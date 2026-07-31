"""Кандидаты на вывод из эксплуатации."""

from __future__ import annotations

import streamlit as st

from _shared import (
    download,
    missing_facts_notice,
    page_setup,
    query,
    rc_scope,
    rc_selector,
    search_box,
    show_table,
)

page_setup("Кандидаты на вывод", "🧹")
missing_facts_notice()
st.caption(
    "Отчёты без зафиксированных запусков, по убыванию объёма, который "
    "освободится при выводе."
)

network, plant = rc_selector()
df = rc_scope(query("SELECT * FROM v_decommission_candidates"), network, plant)

levels = sorted(df["confidence"].unique().tolist())
picked = st.multiselect("Уровень уверенности", levels, default=levels)
df = df[df["confidence"].isin(picked)]

view = search_box(df, ["report_name", "catalog_path"],
                  "Поиск по наименованию отчёта", key="s_cand")

k1, k2, k3 = st.columns(3)
k1.metric("Кандидатов", len(view))
k2.metric(
    "Освободится, МБ",
    f"{view['exclusive_mb'].sum():,.0f}".replace(",", " "),
    help="Только эксклюзивные таблицы — общие останутся нужны другим отчётам.",
)
k3.metric("С высокой уверенностью", int((view["confidence"] == "Высокая").sum()))

shown = show_table(
    view,
    {
        "exclusive_mb": st.column_config.NumberColumn("Освободится, МБ", format="%.1f"),
        "gross_mb": st.column_config.NumberColumn("Всего, МБ ⚠", format="%.1f"),
        "exclusive_pct_of_db": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
        "uses_view": st.column_config.CheckboxColumn("Через view"),
        "size_coverage_pct": st.column_config.ProgressColumn(
            "Покрытие размерами", min_value=0, max_value=100, format="%.0f%%"),
    },
)
download(shown, "decommission_candidates.csv")

st.warning(
    "Перед выводом отчёта проверьте его вручную. Список источников взят из "
    "Excel-выгрузки и отражает её содержимое, а не фактические зависимости: "
    "обращения через хранимые процедуры и представления в нём не видны."
)
