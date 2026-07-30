"""Отчёты — кандидаты на вывод из эксплуатации."""

from __future__ import annotations

import streamlit as st

from _shared import download, missing_facts_notice, page_setup, query

page_setup("Кандидаты на вывод из эксплуатации", "🧹")
missing_facts_notice()

df = query("SELECT * FROM v_decommission_candidates")

st.caption(
    "В выборку попадают отчёты, у которых нет зафиксированных запусков. "
    "Сортировка — по объёму, который освободится при выводе (exclusive_mb)."
)

levels = sorted(df["confidence"].unique().tolist())
picked = st.multiselect("Уровень уверенности", levels, default=levels)
view = df[df["confidence"].isin(picked)]

k1, k2, k3 = st.columns(3)
k1.metric("Кандидатов", len(view))
k2.metric(
    "Освободится, МБ",
    f"{view['exclusive_mb'].sum():,.0f}".replace(",", " "),
    help="Только эксклюзивные таблицы — общие останутся нужны другим отчётам.",
)
k3.metric(
    "С высокой уверенностью",
    int((view["confidence"] == "Высокая").sum()),
)

st.dataframe(
    view,
    use_container_width=True,
    hide_index=True,
    column_config={
        "exclusive_mb": st.column_config.NumberColumn("Освободится, МБ", format="%.1f"),
        "gross_mb": st.column_config.NumberColumn("gross_mb, МБ ⚠", format="%.1f"),
        "size_coverage_pct": st.column_config.ProgressColumn(
            "Покрытие размерами", min_value=0, max_value=100, format="%.0f%%"
        ),
    },
)
download(view, "decommission_candidates.csv")

st.warning(
    "Перед выводом отчёта проверьте его вручную. Список источников взят из "
    "Excel-выгрузки и отражает её содержимое, а не фактические зависимости: "
    "обращения через хранимые процедуры и представления в нём не видны. "
    "Достоверный источник — разбор `.rdl` или журнал `ReportServer.ExecutionLog`."
)
