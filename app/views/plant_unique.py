"""Уникальные отчёты завода: что на заводе есть своё, чего нет у соседей по ТС.

Зеркало «Похожих отчётов». Там пары ищутся **внутри** одного завода, потому
что один и тот же отчёт на нескольких площадках — норма, а не дубль. Здесь эта
норма и есть предмет вопроса: уникален тот отчёт, у которого на других заводах
своей ТС нет ни тёзки, ни близкого по набору таблиц двойника.

Порог сходства — ползунок, а не константа: «тот же отчёт» на разных заводах
успевает разойтись по набору таблиц, и где кончается «тот же» и начинается
«другой», решает читатель, а не страница.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from _shared import (
    PALETTE,
    SURFACE,
    download,
    is_blank,
    missing_facts_notice,
    num,
    page_setup,
    plural,
    query,
    rc_scope,
    rc_selector,
    reports_word,
    row_picker,
    search_box,
    show_table,
    table_height,
)

page_setup("Уникальные отчёты завода", "🏷️")
missing_facts_notice()
st.caption(
    "Отчёты, которых нет у соседей по ТС: ни тёзки на другом заводе, ни "
    "отчёта с похожим набором таблиц. Сравниваются только заводы **одной "
    "ТС** — сети ведут хозяйство независимо, и совпадение отчёта между ними "
    "ничего не говорит о том, что завод делает сам. Считаются только "
    "настоящие таблицы: view, mat.view, временные и процедуры не в счёт."
)

data = query("SELECT * FROM v_report_plant_twin")
if data.empty:
    st.info("Отчёты не загружены. Откройте **Данные → Загрузка данных**.")
    st.stop()

f1, f2 = st.columns([2, 3])
with f1:
    network, plant = rc_selector()
with f2:
    threshold = st.slider(
        "Порог сходства с отчётом другого завода", 0.05, 1.0, 0.8, 0.05,
        format="%.2f",
        help="Какая доля таблиц должна совпасть (индекс Жаккара), чтобы отчёт "
             "на другом заводе считался тем же самым — и этот перестал быть "
             "уникальным. 1.00 — наборы таблиц совпадают полностью. Порог "
             "ниже находит больше двойников и оставляет меньше уникальных.",
    )

# Уникальность считается по ВСЕЙ базе, а не по выбранному РЦ: сравнивать надо
# именно с соседями по ТС, а выбор РЦ их из выборки как раз убирает. Сначала
# признак, потом фильтр — не наоборот.
data["is_unique"] = (data["name_twin_count"] == 0) & (
    data["best_jaccard"].isna() | (data["best_jaccard"] < threshold)
)

scoped = rc_scope(data, network, plant).copy()
if scoped.empty:
    st.info("В выбранном РЦ отчётов нет.")
    st.stop()

# Пустые ТС и завод — это незаполненная колонка исходного файла, а не сеть и не
# завод. В группировке они обязаны остаться видимыми, иначе строки просто
# исчезнут из итогов.
scoped["network"] = scoped["network"].fillna("(не указана)")
scoped["plant"] = scoped["plant"].fillna("(не указан)")

unique_only = scoped[scoped["is_unique"]]

# --- Плитки ------------------------------------------------------------------
# Считаются по показанному разрезу, а не по всей базе: иначе цифра сверху не
# сходилась бы с таблицей под ней.

k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "Уникальных отчётов", num(len(unique_only)),
    help=f"Из {num(len(scoped))} {reports_word(len(scoped))} в этом разрезе.",
)
k2.metric(
    "Доля уникальных",
    f"{100.0 * len(unique_only) / len(scoped):.0f}%" if len(scoped) else "—",
)
k3.metric(
    "Объём их таблиц, МБ", num(unique_only["tables_total_mb"].sum(), decimals=0),
    help="Сумма размеров таблиц уникальных отчётов. Таблицу, которую внутри "
         "завода читают несколько отчётов, эта сумма считает в каждом заново — "
         "это не объём хранилища, а стоимость чтения.",
)
k4.metric(
    # min_count=1: без файла статистики вся колонка пуста, и обычная сумма дала
    # бы честный на вид ноль вместо прочерка — «отчёты не запускали» вместо
    # «данных о запусках нет».
    "Запусков", num(unique_only["exec_count"].sum(min_count=1)),
    help="Сколько раз запускались уникальные отчёты за период выгрузки "
         "статистики. Прочерк — статистика не загружена.",
)

# --- Сколько уникальных на каждом заводе -------------------------------------

scoped["unique_mb"] = scoped["tables_total_mb"].where(scoped["is_unique"], 0)
scoped["unique_execs"] = scoped["exec_count"].fillna(0).where(scoped["is_unique"], 0)

summary = (
    scoped.groupby(["network", "plant"], as_index=False)
    .agg(
        reports=("report_id", "count"),
        unique_reports=("is_unique", "sum"),
        plants_compared=("plants_compared", "max"),
        unique_mb=("unique_mb", "sum"),
        unique_execs=("unique_execs", "sum"),
    )
    .sort_values("unique_reports", ascending=False)
)
summary["unique_reports"] = summary["unique_reports"].astype(int)
summary["shared_reports"] = summary["reports"] - summary["unique_reports"]
summary["unique_share"] = (100.0 * summary["unique_reports"] / summary["reports"]).round(1)

st.divider()
st.subheader("Сколько уникальных отчётов на каждом заводе")

# Один стек на завод: уникальные и те, у кого двойник есть у соседей. Две
# величины одной природы и одной шкалы — честные столбики, а не две оси.
if len(summary) > 1:
    chart = summary.sort_values("unique_reports")
    labels = (chart["network"] + " · " + chart["plant"]).tolist()
    fig = go.Figure()
    for name, column, color in (
        ("Уникальные", "unique_reports", PALETTE[0]),
        ("Есть у соседей по ТС", "shared_reports", PALETTE[1]),
    ):
        fig.add_bar(
            y=labels, x=chart[column], name=name, orientation="h",
            marker=dict(color=color, line=dict(color=SURFACE, width=2)),
            hovertemplate="%{y}<br>" + name + ": %{x:,.0f}<extra></extra>",
        )
    fig.update_layout(
        barmode="stack", height=max(240, 52 * len(chart)),
        margin=dict(l=0, r=0, t=8, b=0), bargap=0.4,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        # traceorder="normal" — иначе легенда идёт в обратном порядке к рядам
        # в столбике, и подписи читаются наоборот.
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0,
                    traceorder="normal"),
        xaxis=dict(title="отчётов", gridcolor="rgba(139,147,161,0.18)",
                   zeroline=False),
        yaxis=dict(title=""),
        separators=", ",
    )
    st.plotly_chart(fig, use_container_width=True)

show_table(
    summary[[
        "network", "plant", "reports", "unique_reports", "unique_share",
        "unique_mb", "unique_execs", "plants_compared",
    ]],
    {
        "reports": st.column_config.NumberColumn("Отчётов всего"),
        "unique_reports": st.column_config.NumberColumn("Уникальных"),
        "unique_share": st.column_config.NumberColumn("Доля, %", format="%.1f"),
        "unique_mb": st.column_config.NumberColumn(
            "Объём их таблиц, МБ", format="%.0f"),
        "unique_execs": st.column_config.NumberColumn("Запусков"),
        "plants_compared": st.column_config.NumberColumn(
            "Сравнивалось с заводами",
            help="Сколько других заводов с отчётами есть в этой ТС. Ноль — "
                 "сравнивать не с чем."),
    },
    height=table_height(len(summary), 260),
)

# Завод, которому не с кем сравниться, обязан быть назван: иначе «все отчёты
# уникальны» читается как вывод, хотя это отсутствие данных для сравнения.
alone = summary[summary["plants_compared"] == 0]
if not alone.empty:
    named = ", ".join(f"**{r.network} · {r.plant}**" for r in alone.itertuples())
    st.warning(
        f"Сравнивать не с чем: {named} — в этой ТС других заводов с отчётами "
        "нет, поэтому все их отчёты попали в уникальные. Это не признак того, "
        "что отчёты особенные.",
        icon="⚠️",
    )

blank_tables = int((unique_only["table_count"] == 0).sum())
if blank_tables:
    st.info(
        f"У {num(blank_tables)} из показанных отчётов не указано ни одной "
        f"таблицы-источника — {plural(blank_tables, 'он проверен', 'они проверены', 'они проверены')} "
        "только по наименованию. По таблицам сравнить их нельзя.",
        icon="ℹ️",
    )

# --- Список отчётов -----------------------------------------------------------

st.divider()
st.subheader("Отчёты")

show_all = st.checkbox(
    "Показать и отчёты с двойниками",
    help="По умолчанию в списке только уникальные. Включите, чтобы увидеть "
         "остальные и то, на каком заводе у каждого нашёлся двойник.",
)
listed = scoped if show_all else unique_only
listed = search_box(
    listed, ["report_name", "catalog_path", "table_names", "report_no"],
    "Поиск по отчёту или таблице", key="s_unique",
)

if listed.empty:
    st.info("Ничего не найдено. Измените условие поиска или порог сходства.")
    st.stop()

listed = listed.sort_values("tables_total_mb", ascending=False, na_position="last")


def twin_cell(row) -> str:
    """Кто двойник: тёзка, похожий по таблицам или никто."""
    if row["is_unique"]:
        return "—"
    if row["name_twin_count"]:
        return f"тёзка: {row['name_twin_plants']}"
    return f"{row['best_twin_plant'] or '—'} · {row['best_jaccard']:.0%}"


columns = [
    ("Завод", 12, lambda r: r["plant"]),
    ("Отчёт", 38, lambda r: r["report_name"]),
    ("Таблиц", 7, lambda r: num(r["table_count"]), True),
    ("Объём, МБ", 11, lambda r: num(r["tables_total_mb"], decimals=0), True),
    ("Запусков", 9, lambda r: num(r["exec_count"]), True),
]
if show_all:
    columns.append(("Двойник", 26, twin_cell))
else:
    columns.append(("Каталог", 26, lambda r: r["catalog_path"]))

picked = row_picker(listed, "report_id", "unique", columns)

if picked is None:
    st.info("👆 Щёлкните по строке отчёта, чтобы увидеть его таблицы и запуски.")
else:
    st.divider()
    st.subheader(picked["report_name"])
    st.caption(
        f"№ {picked['report_no'] or '—'} · {picked['network'] or '—'} · "
        f"{picked['plant'] or '—'} · `{picked['catalog_path']}`"
    )

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Таблиц", num(picked["table_count"]))
    m2.metric(
        "Объём таблиц, МБ", num(picked["tables_total_mb"], decimals=1),
        help="Сумма размеров настоящих таблиц отчёта.",
    )
    m3.metric(
        "Только его, МБ", num(picked["tables_exclusive_mb"], decimals=1),
        help="Таблицы, которых не касается ни один другой отчёт — столько "
             "освободится при выводе этого отчёта из эксплуатации.",
    )
    m4.metric("Запусков", num(picked["exec_count"]))
    m5.metric("Ср. длительность, с", num(picked["avg_duration_sec"], decimals=1))

    # Почему отчёт попал в уникальные — с числами, а не на слово.
    if picked["plants_compared"] == 0:
        st.warning(
            "В этой ТС нет других заводов с отчётами — сравнивать было не с чем.",
            icon="⚠️",
        )
    elif picked["name_twin_count"]:
        st.error(
            f"Тёзка есть на других заводах ТС: {picked['name_twin_plants']}. "
            "Отчёт не уникален.",
            icon="🚫",
        )
    elif is_blank(picked["best_jaccard"]):
        st.success(
            "На других заводах ТС нет отчёта, у которого было бы хотя бы две "
            "общие таблицы с этим.",
            icon="✅",
        )
    else:
        line = (
            f"Ближайший отчёт на другом заводе — **{picked['best_twin_report']}** "
            f"({picked['best_twin_plant']}): сходство наборов таблиц "
            f"{picked['best_jaccard']:.0%}, общих таблиц "
            f"{num(picked['best_shared_tables'])} при пороге {threshold:.0%}."
        )
        if picked["is_unique"]:
            st.success(line, icon="✅")
        else:
            st.error(line + " Это порог или выше — отчёт не уникален.", icon="🚫")

    if picked["uses_view"] is True:
        st.info(
            "Отчёт обращается к данным через view: за ним могут стоять таблицы, "
            "которых нет в списке ниже. Сравнение по таблицам поэтому неполное.",
            icon="👁️",
        )

    tables = query(
        """
        -- Размер через v_report_table_size, а не прямым join к fact_table_size:
        -- там строка на каждый завод, и прямой join размножил бы таблицы
        -- отчёта по числу заводов.
        SELECT t.full_name, s.total_mb, s.row_count, s.retention_days,
               (SELECT COUNT(DISTINCT x.report_id) FROM bridge_report_table x
                WHERE x.table_id = t.table_id) AS used_by_reports
        FROM bridge_report_table b
        JOIN dim_table t                ON t.table_id = b.table_id
        LEFT JOIN v_report_table_size s ON s.table_id = b.table_id
                                       AND s.report_id = b.report_id
        WHERE b.report_id = ? AND t.object_kind = 'TABLE'
        ORDER BY s.total_mb DESC NULLS LAST
        """,
        (int(picked["report_id"]),),
    )
    if tables.empty:
        st.caption("У отчёта не указано ни одной таблицы-источника.")
    else:
        shown = show_table(
            tables,
            {
                "total_mb": st.column_config.NumberColumn(
                    "Объём, МБ", format="%.1f"),
                "row_count": st.column_config.NumberColumn("Строк"),
                "retention_days": st.column_config.NumberColumn("Глубина, дней"),
                "used_by_reports": st.column_config.NumberColumn(
                    "Отчётов использует",
                    help="Сколько отчётов всего читают эту таблицу — вместе с "
                         "отчётами других заводов. 1 — только этот."),
            },
            height=table_height(len(tables), 320),
        )
        download(shown, f"report_{int(picked['report_id'])}_tables.csv",
                 "Выгрузить таблицы отчёта")

st.divider()
# Служебные колонки, добавленные для свода, в выгрузку не идут: в файле нужны
# поля витрины, а не промежуточные суммы страницы.
HELPERS = ("is_unique", "unique_mb", "unique_execs")
download(
    unique_only[[c for c in data.columns if c not in HELPERS]],
    "unique_reports.csv", "Выгрузить уникальные отчёты",
)
download(summary, "unique_reports_by_plant.csv", "Выгрузить свод по заводам")
