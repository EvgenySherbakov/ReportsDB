"""Объём и стоимость: на какие таблицы ссылается отчёт и сколько они весят."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import (
    ACCENT,
    MUTED,
    SECONDARY,
    download,
    missing_facts_notice,
    page_setup,
    query,
    rc_scope,
    rc_selector,
    search_box,
    show_table,
    surface_color,
)

page_setup("Объём и стоимость", "💾")
missing_facts_notice()

network, plant = rc_selector()
df = rc_scope(query("SELECT * FROM v_report_tables_summary"), network, plant)

st.info(
    "**Размер таблиц** — сумма всех таблиц, на которые ссылается отчёт. "
    "Складывать этот столбец по отчётам нельзя: общие таблицы посчитались бы "
    "заново в каждом. **Только его** — таблицы, которых не касается ни один "
    "другой отчёт; столько освободится при выводе отчёта."
)

view = search_box(df, ["report_name", "table_names", "catalog_path"], key="s_cost")

k1, k2, k3, k4 = st.columns(4)
k1.metric("Отчётов", len(view))
k2.metric(
    "Освободится, МБ",
    f"{view['tables_exclusive_mb'].sum():,.0f}".replace(",", " "),
    help="Сумма эксклюзивных таблиц — корректно суммируется.",
)
k3.metric("Доля БД", f"{view['tables_pct_of_db'].sum():.2f}%" if len(view) else "—")
k4.metric(
    "Таблиц на отчёт, среднее",
    f"{view['table_count'].mean():.1f}" if len(view) else "—",
)

tab_size, tab_time = st.tabs(["Объём данных", "Время выполнения"])

with tab_size:
    st.subheader("Топ-20 отчётов по размеру используемых таблиц")
    top = view.nlargest(20, "tables_total_mb")
    if top.empty or top["tables_total_mb"].sum() == 0:
        st.caption("Диаграмма появится после загрузки файла размеров таблиц.")
    else:
        plot = top.assign(
            shared_mb=(top["tables_total_mb"] - top["tables_exclusive_mb"]).round(2)
        ).rename(columns={"tables_exclusive_mb": "Только этот отчёт",
                          "shared_mb": "Общие с другими"})
        plot = plot.melt(
            id_vars="report_name",
            value_vars=["Только этот отчёт", "Общие с другими"],
            var_name="Тип", value_name="МБ",
        )
        fig = px.bar(
            plot, x="МБ", y="report_name", color="Тип", orientation="h",
            color_discrete_map={"Только этот отчёт": ACCENT,
                                "Общие с другими": SECONDARY},
            labels={"report_name": ""},
        )
        fig.update_traces(marker_line_width=2, marker_line_color=surface_color())
        fig.update_layout(
            height=620, margin=dict(l=0, r=0, t=10, b=0), bargap=0.35,
            yaxis=dict(categoryorder="array",
                       categoryarray=top["report_name"].tolist()[::-1]),
            legend=dict(orientation="h", y=1.06, x=0, title=""),
        )
        st.plotly_chart(fig, use_container_width=True)

    if view["exec_count"].notna().any():
        st.subheader("Размер против частоты использования")
        pts = view.dropna(subset=["exec_count"])
        fig = px.scatter(
            pts, x="exec_count", y="tables_total_mb",
            hover_name="report_name",
            hover_data=["network", "plant", "table_count", "tables_exclusive_mb"],
            labels={"exec_count": "Запусков за период",
                    "tables_total_mb": "Размер таблиц отчёта, МБ"},
            color_discrete_sequence=[ACCENT],
        )
        fig.update_traces(
            marker=dict(size=9, line=dict(width=2, color="rgba(255,255,255,0.85)")))
        x_med, y_med = pts["exec_count"].median(), pts["tables_total_mb"].median()
        fig.add_vline(x=x_med, line_width=1, line_dash="dot", line_color=MUTED)
        fig.add_hline(y=y_med, line_width=1, line_dash="dot", line_color=MUTED)
        fig.add_annotation(
            x=0, y=pts["tables_total_mb"].max(), xanchor="left", showarrow=False,
            text="Много данных, мало запусков →<br>кандидаты на вывод",
            font=dict(color=MUTED, size=11), align="left",
        )
        fig.update_layout(height=460, margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig, use_container_width=True)

with tab_time:
    if not view["avg_duration_sec"].notna().any():
        st.info("Нет данных о длительности — загрузите колонку «Ср. дл. (сек)».")
    else:
        t1, t2, t3 = st.columns(3)
        t1.metric(
            "Суммарное время, ч",
            f"{view['total_duration_sec'].sum() / 3600:,.1f}".replace(",", " "),
            help="Обращения × средняя длительность.",
        )
        t2.metric("Медианная длительность, с", f"{view['avg_duration_sec'].median():.1f}")
        t3.metric("Дольше минуты", int((view["avg_duration_sec"] >= 60).sum()))

        st.subheader("Топ-20 по суммарному времени")
        top_t = view.nlargest(20, "total_duration_sec")
        fig = px.bar(
            top_t, x="total_duration_sec", y="report_name", orientation="h",
            labels={"total_duration_sec": "Суммарное время, с", "report_name": ""},
            hover_data=["exec_count", "avg_duration_sec", "table_count"],
            color_discrete_sequence=[ACCENT],
        )
        fig.update_layout(
            height=620, margin=dict(l=0, r=0, t=10, b=0), bargap=0.35,
            yaxis=dict(categoryorder="array",
                       categoryarray=top_t["report_name"].tolist()[::-1]),
        )
        st.plotly_chart(fig, use_container_width=True)

st.subheader("Отчёты и их таблицы")
st.caption("Столбец «Таблицы отчёта» перечисляет источники по убыванию размера.")
shown = show_table(
    view[[
        "report_no", "report_name", "network", "plant", "catalog_path",
        "table_count", "tables_total_mb", "tables_exclusive_mb", "tables_pct_of_db",
        "size_coverage_pct", "exec_count", "table_names",
    ]].sort_values("tables_total_mb", ascending=False),
    {
        "tables_total_mb": st.column_config.NumberColumn("Размер таблиц, МБ", format="%.1f"),
        "tables_exclusive_mb": st.column_config.NumberColumn("Только его, МБ", format="%.1f"),
        "tables_pct_of_db": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
        "size_coverage_pct": st.column_config.ProgressColumn(
            "Покрытие размерами", min_value=0, max_value=100, format="%.0f%%"),
        "table_names": st.column_config.TextColumn("Таблицы отчёта", width="large"),
    },
)
download(shown, "report_tables_summary.csv")
