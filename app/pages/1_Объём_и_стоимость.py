"""Приоритетная страница: объём данных и стоимость отчётов."""

from __future__ import annotations

import pandas as pd
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

ALL = "(все)"


def pick(container, label: str, column: str, source):
    """Выпадающий список по значениям колонки. Возвращает отфильтрованный набор."""
    values = [ALL] + sorted(source[column].dropna().unique().tolist())
    chosen = container.selectbox(label, values, key=f"f_{column}")
    return source if chosen == ALL else source[source[column] == chosen]


f1, f2, f3 = st.columns(3)
view = pick(f1, "Торговая сеть", "network", df)
view = pick(f2, "Завод", "plant", view)
view = pick(f3, "Каталог, уровень 1", "folder_l1", view)

# Уровни 2 и 3 — каскадом: показываются только значения, оставшиеся после
# фильтра верхнего уровня, иначе список забит пустыми вариантами.
f4, f5, f6 = st.columns(3)
view = pick(f4, "Каталог, уровень 2", "folder_l2", view)
view = pick(f5, "Каталог, уровень 3", "folder_l3", view)
view_mode = f6.selectbox(
    "Работа через view", [ALL, "Только через view", "Только напрямую"],
    help="За view стоят таблицы вне списка источников — объём таких отчётов "
         "посчитан не полностью.",
)
if view_mode == "Только через view":
    view = view[view["uses_view"] == True]  # noqa: E712 — сравнение с NULL недопустимо
elif view_mode == "Только напрямую":
    view = view[view["uses_view"] == False]  # noqa: E712

f7, f8 = st.columns([2, 3])
min_coverage = f7.slider(
    "Мин. покрытие размерами, %", 0, 100, 0,
    help="Отсекает отчёты, по которым размеры известны слишком частично.",
)
search = f8.text_input("Поиск по имени отчёта", "")

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
    "Доля базы данных",
    f"{view['exclusive_pct_of_db'].sum():.2f}%" if len(view) else "—",
    help="Какую часть всей БД занимают эксклюзивные таблицы этих отчётов.",
)

tab_size, tab_time = st.tabs(["Объём данных", "Время выполнения"])

cv = query("SELECT * FROM v_report_cost_value")
cv = cv[cv["report_id"].isin(view["report_id"])]

# --- Вкладка «Объём данных» ------------------------------------------------

with tab_size:
    st.subheader("Топ-20 отчётов по освобождаемому объёму")
    top_src = view.nlargest(20, "exclusive_mb")
    top = (
        top_src[["report_name", "exclusive_mb", "shared_mb"]]
        .rename(columns={"exclusive_mb": "Освободится (эксклюзивные)",
                         "shared_mb": "Останется (общие таблицы)"})
        .melt(id_vars="report_name", var_name="Тип объёма", value_name="МБ")
    )
    if top.empty or top["МБ"].fillna(0).sum() == 0:
        st.caption("Диаграмма появится после загрузки файла размеров таблиц.")
    else:
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
    if cv["exec_count"].notna().any():
        pts = cv.dropna(subset=["exec_count"])
        # Один ряд + медианные линии: квадранты читаются по положению, а не по цвету.
        fig = px.scatter(
            pts,
            x="exec_count",
            y="exclusive_mb",
            hover_name="report_name",
            hover_data=["network", "plant", "catalog_path", "quadrant",
                        "uses_view", "table_count", "size_coverage_pct"],
            labels={"exec_count": "Запусков за период",
                    "exclusive_mb": "Освобождаемый объём, МБ"},
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

# --- Вкладка «Время выполнения» --------------------------------------------

with tab_time:
    dur = query("SELECT * FROM v_report_duration")
    dur = dur[dur["report_id"].isin(view["report_id"])]

    if not dur["avg_duration_sec"].notna().any():
        st.info(
            "Нет данных о длительности. Загрузите файл с колонкой «Ср. дл. (сек)» "
            "на странице «Загрузка данных»."
        )
    else:
        st.caption(
            "Время — вторая, независимая от объёма метрика стоимости. Отчёт "
            "бывает лёгким по данным и дорогим по суммарному времени."
        )
        t1, t2, t3 = st.columns(3)
        total_sec = dur["total_duration_sec"].sum()
        t1.metric(
            "Суммарное время, ч",
            f"{total_sec / 3600:,.1f}".replace(",", " "),
            help="Обращения × средняя длительность, сложенные по выборке.",
        )
        t2.metric(
            "Медианная длительность, с",
            f"{dur['avg_duration_sec'].median():,.1f}".replace(",", " "),
        )
        t3.metric(
            "Дольше минуты",
            int((dur["avg_duration_sec"] >= 60).sum()),
            help="Отчёты, у которых одна выборка в среднем занимает минуту и больше.",
        )

        st.subheader("Топ-20 по суммарному времени")
        top_t = dur.nlargest(20, "total_duration_sec")
        if not top_t.empty:
            fig = px.bar(
                top_t,
                x="total_duration_sec",
                y="report_name",
                orientation="h",
                labels={"total_duration_sec": "Суммарное время, с", "report_name": ""},
                hover_data=["network", "plant", "exec_count", "avg_duration_sec"],
                color_discrete_sequence=[ACCENT],
            )
            fig.update_layout(
                height=620, margin=dict(l=0, r=0, t=10, b=0), bargap=0.35,
                yaxis=dict(categoryorder="array",
                           categoryarray=top_t["report_name"].tolist()[::-1]),
            )
            st.plotly_chart(fig, use_container_width=True)

        st.subheader("Распределение по длительности одной выборки")
        bands = ["Меньше секунды", "От 1 секунды", "От 10 секунд", "Минута и дольше"]
        counts = (
            dur[dur["duration_band"] != "Нет данных"]
            .groupby("duration_band", as_index=False)
            .agg(report_count=("report_id", "count"),
                 total_duration_sec=("total_duration_sec", "sum"))
        )
        counts["duration_band"] = pd.Categorical(
            counts["duration_band"], categories=bands, ordered=True
        )
        counts = counts.sort_values("duration_band")
        fig = px.bar(
            counts,
            x="duration_band",
            y="report_count",
            labels={"duration_band": "", "report_count": "Отчётов"},
            color_discrete_sequence=[ACCENT],
        )
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), bargap=0.4)
        st.plotly_chart(fig, use_container_width=True)

        st.subheader("Отчёты по времени")
        shown_t = show_table(
            dur.sort_values("total_duration_sec", ascending=False),
            {
                "avg_duration_sec": st.column_config.NumberColumn(
                    "Ср. длительность, с", format="%.1f"),
                "total_duration_sec": st.column_config.NumberColumn(
                    "Суммарное время, с", format="%.0f"),
                "exclusive_mb": st.column_config.NumberColumn("Освободится, МБ", format="%.1f"),
                "uses_view": st.column_config.CheckboxColumn("Через view"),
                "duration_band": "Длительность выборки",
            },
        )
        download(shown_t, "report_duration.csv")

# --- Общая таблица выборки -------------------------------------------------

st.subheader("Все отчёты выборки")
shown = show_table(
    view.sort_values("exclusive_mb", ascending=False),
    {
        "exclusive_mb": st.column_config.NumberColumn("Освободится, МБ", format="%.1f"),
        "shared_mb": st.column_config.NumberColumn("Останется общим, МБ", format="%.1f"),
        "gross_mb": st.column_config.NumberColumn("Всего, МБ ⚠", format="%.1f"),
        "exclusive_pct_of_db": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
        "uses_view": st.column_config.CheckboxColumn("Через view"),
        "size_coverage_pct": st.column_config.ProgressColumn(
            "Покрытие размерами", min_value=0, max_value=100, format="%.0f%%"
        ),
    },
)
download(shown, "report_footprint.csv")
