"""Организационный разрез: сравнение заводов между собой.

Страница отвечает на вопрос «чем заводы отличаются друг от друга»: сколько
данных лежит на каждом, какая доля этих данных под отчётами, сколько отчётов
и во что они обходятся по времени.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from _shared import (
    PALETTE,
    SURFACE,
    download,
    missing_facts_notice,
    num,
    page_setup,
    query,
    show_table,
    table_height,
)

page_setup("Сети и заводы", "🏭")
missing_facts_notice()
st.caption(
    "Сравнение РЦ между собой. «Объём базы» — сумма размеров таблиц этого "
    "завода из файла размеров; складывать заводы в один итог можно, замеры у "
    "них независимые."
)

df = query("SELECT * FROM v_network_overview")

if df.empty:
    st.info("В загруженных данных нет колонок «ТС» и «Завод».")
    st.stop()

# Числовые колонки приходят из FULL JOIN с пропусками у РЦ, который есть в
# одном файле и отсутствует в другом. Без приведения к числу Streamlit
# печатает в таких ячейках «None» вместо пустоты.
NUMERIC = [c for c in df.columns if c not in ("network", "plant")]
for column in NUMERIC:
    df[column] = pd.to_numeric(df[column], errors="coerce")

# Строки-заглушки «(не указана)/(не указан)» в счётчики не идут: это не сеть и
# не завод, а признак того, что колонку не заполнили в исходном файле.
real = df[(df["network"] != "(не указана)") & (df["plant"] != "(не указан)")]
unknown_reports = int(df.loc[~df.index.isin(real.index), "report_count"].sum())

# --- Плитки ------------------------------------------------------------------

k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "Торговых сетей", num(real["network"].nunique()),
    help="Строки с незаполненными ТС и заводом не считаются."
         + (f" Таких отчётов: {num(unknown_reports)}." if unknown_reports else ""),
)
k2.metric("Заводов", num(real["plant"].nunique()))
k3.metric(
    "Объём базы, ГБ", num(df["db_total_mb"].sum() / 1024, decimals=1),
    help="Сумма по всем заводам. У каждого завода свой замер, поэтому "
         "сложение здесь корректно.",
)
k4.metric(
    "Под отчётами", f"{100.0 * df['used_mb'].sum() / df['db_total_mb'].sum():.0f}%"
    if df["db_total_mb"].sum() else "—",
    help="Доля объёма, приходящаяся на таблицы, к которым тянется хотя бы "
         "один отчёт. Остальное база хранит без видимой причины.",
)

st.divider()

# --- Сравнение заводов по объёму ---------------------------------------------
# Один стек на завод: сколько объёма под отчётами и сколько без них. Две
# величины одной природы и одной шкалы, поэтому это честные столбики, а не
# две оси.

st.subheader("Объём базы по заводам")

chart = df[df["db_total_mb"].notna()].sort_values("db_total_mb")
if chart.empty:
    st.info("Файл размеров не загружен — сравнивать объёмы нечем.")
else:
    labels = (chart["network"] + " · " + chart["plant"]).tolist()
    fig = go.Figure()
    for name, column, color in (
        ("Под отчётами", "used_mb", PALETTE[0]),
        ("Без отчётов", "unused_mb", PALETTE[1]),
    ):
        fig.add_bar(
            y=labels, x=chart[column], name=name, orientation="h",
            marker=dict(color=color, line=dict(color=SURFACE, width=2)),
            hovertemplate="%{y}<br>" + name + ": %{x:,.0f} МБ<extra></extra>",
        )
    fig.update_layout(
        barmode="stack", height=max(240, 52 * len(chart)),
        margin=dict(l=0, r=0, t=8, b=0), bargap=0.4,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        # traceorder="normal" — иначе легенда идёт в обратном порядке к рядам
        # в столбике, и подписи читаются наоборот.
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    traceorder="normal"),
        xaxis=dict(title="МБ", gridcolor="rgba(139,147,161,0.18)", zeroline=False),
        yaxis=dict(title=""),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "«Без отчётов» — таблицы, к которым не тянется ни один отчёт. Это не "
        "обязательно лишние данные: источник связей — Excel-выгрузка, и "
        "обращения через процедуры и view в ней не видны."
    )

st.divider()

# --- Таблица сравнения --------------------------------------------------------

st.subheader("Сравнение РЦ")

columns = [
    "network", "plant", "report_count", "reports_with_view",
    "db_table_count", "db_total_mb", "used_table_count", "used_mb",
    "used_pct_of_plant", "unused_table_count", "unused_mb",
    "mb_per_report", "exclusive_mb", "schema_count",
    "median_retention_days", "exec_count", "total_duration_sec",
]
shown = show_table(
    df[columns],
    {
        "report_count": st.column_config.NumberColumn("Отчётов"),
        "reports_with_view": st.column_config.NumberColumn("Из них через view"),
        "db_table_count": st.column_config.NumberColumn("Таблиц в базе"),
        "db_total_mb": st.column_config.NumberColumn("Объём базы, МБ", format="%.0f"),
        "used_table_count": st.column_config.NumberColumn("Таблиц под отчётами"),
        "used_mb": st.column_config.NumberColumn("Под отчётами, МБ", format="%.0f"),
        "used_pct_of_plant": st.column_config.NumberColumn(
            "Под отчётами, %", format="%.1f"),
        "unused_table_count": st.column_config.NumberColumn("Таблиц без отчётов"),
        "unused_mb": st.column_config.NumberColumn("Без отчётов, МБ", format="%.0f"),
        "mb_per_report": st.column_config.NumberColumn(
            "МБ на отчёт", format="%.0f"),
        "exclusive_mb": st.column_config.NumberColumn(
            "Освободится, МБ", format="%.0f"),
        "schema_count": st.column_config.NumberColumn("Схем"),
        "median_retention_days": st.column_config.NumberColumn(
            "Глубина, медиана"),
        "exec_count": st.column_config.NumberColumn("Обращений"),
        "total_duration_sec": st.column_config.NumberColumn(
            "Суммарное время, с", format="%.0f"),
    },
    height=table_height(len(df)),
)
download(shown, "network_overview.csv")

st.caption(
    "«МБ на отчёт» — объём базы завода, делённый на число его отчётов: грубая, "
    "но сравнимая между заводами мера того, сколько данных приходится на один "
    "отчёт. «Освободится, МБ» — сумма эксклюзивных таблиц отчётов этого РЦ; "
    "общие таблицы, нужные другим отчётам, сюда не входят."
)
