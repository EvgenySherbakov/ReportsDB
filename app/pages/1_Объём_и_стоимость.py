"""Приоритетная страница: объём данных и стоимость отчётов."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import (
    ACCENT,
    FOOTPRINT_HINT,
    MUTED,
    SECONDARY,
    show_table,
    surface_color,
    download,
    missing_facts_notice,
    page_setup,
    query,
)

page_setup("Объём данных и стоимость", "💾")
missing_facts_notice()
st.info(FOOTPRINT_HINT)

df = query("SELECT * FROM v_report_footprint")

folders = ["(все)"] + sorted(df["folder_l1"].dropna().unique().tolist())
f1, f2, f3 = st.columns([2, 2, 3])
folder = f1.selectbox("Папка каталога", folders)
min_coverage = f2.slider(
    "Мин. покрытие размерами, %", 0, 100, 0,
    help="Отсекает отчёты, по которым размеры известны слишком частично.",
)
search = f3.text_input("Поиск по имени отчёта", "")

view = df.copy()
if folder != "(все)":
    view = view[view["folder_l1"] == folder]
if min_coverage:
    view = view[view["size_coverage_pct"].fillna(0) >= min_coverage]
if search:
    view = view[view["report_name"].str.contains(search, case=False, na=False)]

k1, k2, k3 = st.columns(3)
k1.metric("Отчётов в выборке", len(view))
k2.metric(
    "Освобождаемый объём, МБ",
    f"{view['exclusive_mb'].sum():,.0f}".replace(",", " "),
    help="Сумма exclusive_mb — корректно суммируется, общие таблицы не дублируются.",
)
k3.metric(
    "Медианный exclusive_mb",
    f"{view['exclusive_mb'].median():,.1f}".replace(",", " ") if len(view) else "—",
)

st.subheader("Топ-20 отчётов по освобождаемому объёму")
top_src = view.nlargest(20, "exclusive_mb")
top = (
    top_src[["report_name", "exclusive_mb", "shared_mb"]]
    .rename(columns={"exclusive_mb": "Освободится (эксклюзивные)",
                     "shared_mb": "Останется (общие таблицы)"})
    .melt(id_vars="report_name", var_name="Тип объёма", value_name="МБ")
)
if not top.empty:
    fig = px.bar(
        top,
        x="МБ",
        y="report_name",
        color="Тип объёма",
        orientation="h",
        color_discrete_map={
            "Освободится (эксклюзивные)": ACCENT,
            "Останется (общие таблицы)": SECONDARY,
        },
        labels={"report_name": ""},
    )
    # 2px зазор цветом фона — сегменты стопки не сливаются друг с другом.
    fig.update_traces(marker_line_width=2, marker_line_color=surface_color())
    fig.update_layout(
        height=620, margin=dict(l=0, r=0, t=10, b=0),
        # Порядок — строго по exclusive_mb, как обещает заголовок; сортировка
        # по суммарной длине бара показала бы совсем другой список сверху.
        yaxis=dict(categoryorder="array",
                   categoryarray=top_src["report_name"].tolist()[::-1]),
        legend=dict(orientation="h", y=1.06, x=0, title=""),
        bargap=0.35,
    )
    st.plotly_chart(fig, use_container_width=True)

st.subheader("Объём против частоты использования")
cv = query("SELECT * FROM v_report_cost_value")
cv = cv[cv["report_id"].isin(view["report_id"])]
if cv["exec_count"].notna().any():
    pts = cv.dropna(subset=["exec_count"])
    # Один ряд + медианные линии: квадранты читаются по положению, а не по цвету.
    fig = px.scatter(
        pts,
        x="exec_count",
        y="exclusive_mb",
        hover_name="report_name",
        hover_data=["catalog_path", "quadrant", "table_count", "size_coverage_pct"],
        labels={"exec_count": "Запусков за период", "exclusive_mb": "Освобождаемый объём, МБ"},
        color_discrete_sequence=[ACCENT],
    )
    fig.update_traces(marker=dict(size=9, line=dict(width=2, color="rgba(255,255,255,0.85)")))
    x_med, y_med = pts["exec_count"].median(), pts["exclusive_mb"].median()
    fig.add_vline(x=x_med, line_width=1, line_dash="dot", line_color=MUTED)
    fig.add_hline(y=y_med, line_width=1, line_dash="dot", line_color=MUTED)
    fig.add_annotation(
        x=0, y=pts["exclusive_mb"].max(), xanchor="left", showarrow=False,
        text="Дорогие и невостребованные →<br>первые кандидаты на вывод",
        font=dict(color=MUTED, size=11), align="left",
    )
    fig.update_layout(height=460, margin=dict(l=0, r=0, t=10, b=0))
    st.plotly_chart(fig, use_container_width=True)
    medians = f"{x_med:,.0f}".replace(",", " ") + " запусков и " \
        + f"{y_med:,.1f}".replace(",", " ") + " МБ"
    st.caption(
        f"Пунктир — медианы: {medians}. "
        "Левый верхний квадрант — дорогие и невостребованные отчёты. "
        "Точный квадрант каждого отчёта виден во всплывающей подсказке и в таблице ниже."
    )
else:
    st.caption("Диаграмма появится после загрузки данных о частоте использования.")

st.subheader("Все отчёты выборки")
shown = show_table(
    view.sort_values("exclusive_mb", ascending=False),
    {
        "exclusive_mb": st.column_config.NumberColumn("Освободится, МБ", format="%.1f"),
        "shared_mb": st.column_config.NumberColumn("Останется общим, МБ", format="%.1f"),
        "gross_mb": st.column_config.NumberColumn("Всего, МБ ⚠", format="%.1f"),
        "size_coverage_pct": st.column_config.ProgressColumn(
            "Покрытие размерами", min_value=0, max_value=100, format="%.0f%%"
        ),
    },
)
download(shown, "report_footprint.csv")
