"""ABC-анализ отчётов: где сосредоточена нагрузка.

Отчёты сортируются по выбранной мере и режутся по накопленной доле: группа A —
первые отчёты, дающие 80% итога, B — следующие до 95%, C — длинный хвост.
Отвечает на вопрос «с чего начинать оптимизацию» и «что можно не трогать».
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from _shared import (
    ACCENT,
    MUTED,
    donut,
    download,
    num,
    page_setup,
    query,
    rc_scope,
    rc_selector,
    search_box,
    show_table,
    table_height,
)

page_setup("ABC-анализ отчётов", "📈")
st.caption(
    "Отчёты, отсортированные по выбранной мере и разрезанные по накопленной "
    "доле: A — первые 80% итога, B — до 95%, C — остальной хвост."
)

# Мера выбирается пользователем: «дорогой» отчёт по объёму и по времени — это
# разные отчёты, и вывод из эксплуатации у них разный.
MEASURES = {
    "Объём данных (эксклюзивный), МБ": ("exclusive_mb", " МБ", 1),
    "Число обращений": ("exec_count", "", 0),
    "Суммарное время выполнения, с": ("total_duration_sec", " с", 0),
}

f1, f2 = st.columns([2, 3])
with f1:
    network, plant = rc_selector()
with f2:
    measure_label = st.selectbox("Мера", list(MEASURES), key="abc_measure")
column, unit, decimals = MEASURES[measure_label]

df = rc_scope(query("SELECT * FROM v_report_cost_value"), network, plant)
df = df[df[column].notna() & (df[column] > 0)].copy()

if df.empty:
    st.info(
        f"Нет данных по мере «{measure_label}» в выбранном разрезе. "
        "Загрузите файл размеров или статистику обращений."
    )
    st.stop()

# --- Разметка по группам -----------------------------------------------------

df = df.sort_values(column, ascending=False).reset_index(drop=True)
total = df[column].sum()
df["Доля, %"] = 100.0 * df[column] / total
df["Накопленная доля, %"] = df["Доля, %"].cumsum()
df["Группа"] = df["Накопленная доля, %"].apply(
    lambda c: "A" if c <= 80 else ("B" if c <= 95 else "C")
)
# Первый отчёт всегда в A: если он один даёт больше 80%, условие «<= 80» его
# бы не поймало и группа A осталась бы пустой.
df.loc[0, "Группа"] = "A"

summary = (
    df.groupby("Группа", as_index=False)
    .agg(reports=("report_name", "count"), value=(column, "sum"))
    .set_index("Группа").reindex(["A", "B", "C"]).fillna(0).reset_index()
)
summary["share"] = 100.0 * summary["value"] / total

st.divider()

# --- Плитки по группам -------------------------------------------------------

k1, k2, k3, k4 = st.columns(4)
k1.metric("Всего отчётов", num(len(df)))
for col, (_, row) in zip((k2, k3, k4), summary.iterrows()):
    # Доля идёт в подпись, а не в delta: delta рисует стрелку роста, а здесь
    # это не изменение, а часть от целого — стрелка сбивала бы с толку.
    col.metric(
        f"Группа {row['Группа']} · {row['share']:.0f}% меры",
        f"{num(row['reports'])} отч.",
        help={
            "A": "Дают первые 80% итога. Здесь окупается любая оптимизация.",
            "B": "Следующие 15%. Смотреть после A.",
            "C": "Длинный хвост: 5% итога. Оптимизировать поштучно смысла нет, "
                 "зато это готовый список кандидатов на вывод.",
        }[row["Группа"]],
    )

lead = summary.iloc[0]
if lead["reports"]:
    st.success(
        f"**{lead['reports']:.0f} отчётов из {len(df)}** "
        f"({100.0 * lead['reports'] / len(df):.0f}% списка) дают "
        f"{lead['share']:.0f}% меры «{measure_label.lower()}»."
    )

st.divider()

# --- Кривая накопления -------------------------------------------------------
# Одна линия и одна ось. Классическая диаграмма Парето рисует столбцы и кривую
# на двух разных осях — так делать нельзя, две шкалы в одних координатах
# читаются неверно. Величина по отчётам видна в таблице ниже.

left, right = st.columns([3, 2])

with left:
    st.subheader("Накопленная доля")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=list(range(1, len(df) + 1)),
            y=df["Накопленная доля, %"],
            mode="lines",
            line=dict(color=ACCENT, width=2),
            name="Накоплено",
            hovertemplate="Отчётов: %{x}<br>Накоплено: %{y:.1f}%<extra></extra>",
        )
    )
    # Подпись внутри области графика: при annotation_position="right" она
    # выносится за оси и обрезается нулевым правым полем.
    for level, caption in ((80, "80% — граница A"), (95, "95% — граница B")):
        fig.add_hline(
            y=level, line=dict(color=MUTED, width=1),
            annotation_text=caption, annotation_position="top right",
            annotation_font=dict(size=11, color=MUTED),
        )
    fig.update_layout(
        height=340, margin=dict(l=0, r=0, t=4, b=0), showlegend=False,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        xaxis=dict(title="Отчётов, накопительно",
                   gridcolor="rgba(139,147,161,0.18)", zeroline=False),
        yaxis=dict(title="Доля меры, %", range=[0, 101],
                   gridcolor="rgba(139,147,161,0.18)", zeroline=False),
    )
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Чем круче начало кривой, тем сильнее нагрузка сосредоточена в немногих "
        "отчётах — и тем больше даст работа с группой A."
    )

with right:
    st.subheader("Доли групп")
    donut(
        ["A", "B", "C"], summary["value"].tolist(),
        f"Мера: {measure_label.lower()}", unit, height=280,
    )

st.divider()

# --- Таблица -----------------------------------------------------------------

st.subheader("Отчёты по группам")

g1, g2 = st.columns([1, 3])
with g1:
    chosen = st.selectbox("Группа", ["(все)", "A", "B", "C"], key="abc_group")
with g2:
    view = df if chosen == "(все)" else df[df["Группа"] == chosen]
    view = search_box(view, ["report_name", "catalog_path"],
                      "Поиск по наименованию отчёта", key="s_abc")

columns = ["Группа", "report_name", "network", "plant", column,
           "Доля, %", "Накопленная доля, %", "catalog_path"]
shown = show_table(
    view[columns],
    {
        "Группа": st.column_config.TextColumn("Группа", width="small"),
        column: st.column_config.NumberColumn(
            measure_label, format=f"%.{decimals}f"),
        "Доля, %": st.column_config.NumberColumn("Доля, %", format="%.2f"),
        "Накопленная доля, %": st.column_config.NumberColumn(
            "Накоплено, %", format="%.1f"),
    },
    height=table_height(len(view), 460),
)
download(shown, "abc_reports.csv", "Выгрузить ABC-анализ")
