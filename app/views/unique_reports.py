"""Уникальные отчёты: чего нет в других ТС и чего нет у соседей по ТС.

Зеркало «Похожих отчётов». Там пары ищутся **внутри** одного завода, потому
что один и тот же отчёт на нескольких площадках — норма, а не дубль. Здесь эта
норма и есть предмет вопроса: что у сети (или у завода) есть своё.

**Областей сравнения две, и обе нужны.** На данных заказчика сети пересекаются
по отчётам примерно на 70%, и главный вопрос — чем они различаются; внутри
одной сети уникальных отчётов по заводам заметно меньше. Поэтому область
выбирается переключателем, а не зашита в страницу.

Единица счёта меняется вместе с областью. В разрезе ТС считается **отчёт
сети**: отчёт, стоящий на трёх заводах одной сети, — это один отчёт этой сети,
и считать его трижды значило бы завысить «своё» втрое. В разрезе заводов
единица — запись отчёта на конкретном заводе.
"""

from __future__ import annotations

import plotly.graph_objects as go
import streamlit as st

from _shared import (
    ALL,
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

page_setup("Уникальные отчёты", "🏷️")
missing_facts_notice()

BETWEEN_NETWORKS = "Между ТС — чего нет в других сетях"
BETWEEN_PLANTS = "Между заводами одной ТС — чего нет у соседей"

scope = st.radio(
    "Что с чем сравнивать",
    [BETWEEN_NETWORKS, BETWEEN_PLANTS],
    horizontal=True,
    key="unique_scope",
    help="В разрезе ТС отчёт сравнивается с отчётами всех остальных сетей, а "
         "единица счёта — отчёт сети: стоящий на нескольких её заводах "
         "считается один раз. В разрезе заводов сравниваются заводы внутри "
         "одной ТС, и единица — запись отчёта на заводе.",
)
by_networks = scope == BETWEEN_NETWORKS

st.caption(
    (
        "Отчёты, которых нет ни в одной другой ТС: ни тёзки, ни отчёта с "
        "похожим набором таблиц. Отчёт, стоящий на нескольких заводах одной "
        "сети, — это один отчёт этой сети."
        if by_networks else
        "Отчёты, которых нет у соседних заводов **своей** ТС: ни тёзки, ни "
        "отчёта с похожим набором таблиц. Другие сети в этом разрезе не "
        "участвуют."
    )
    + " Считаются только настоящие таблицы: view, mat.view, временные и "
      "процедуры не в счёт."
)

# --- Данные и порог -----------------------------------------------------------

f1, f2 = st.columns([2, 3])

if by_networks:
    data = query("SELECT * FROM v_network_report_twin")
    if data.empty:
        st.info("Отчёты не загружены. Откройте **Данные → Загрузка данных**.")
        st.stop()
    data["row_key"] = data["network"] + " · " + data["name_key"]
    with f1:
        networks = [ALL] + sorted(data["network"].unique())
        chosen = st.selectbox("Торговая сеть", networks, key="unique_network")
    GROUP = "network"
    COMPARED = "networks_compared"
else:
    data = query("SELECT * FROM v_report_twin WHERE scope = 'PLANT'")
    if data.empty:
        st.info("Отчёты не загружены. Откройте **Данные → Загрузка данных**.")
        st.stop()
    data["row_key"] = data["report_id"].astype(str)
    with f1:
        network, plant = rc_selector()
    GROUP = "plant"
    COMPARED = "counterparts_compared"

with f2:
    threshold = st.slider(
        "Порог сходства наборов таблиц", 0.05, 1.0, 0.8, 0.05, format="%.2f",
        help="Какая доля таблиц должна совпасть (индекс Жаккара), чтобы отчёт "
             "на другой стороне считался тем же самым — и этот перестал быть "
             "уникальным. 1.00 — наборы совпадают полностью. Порог ниже "
             "находит больше двойников и оставляет меньше уникальных.",
    )

# Признак считается по ВСЕЙ базе, а потом уже применяется фильтр: сравнивать
# надо именно с теми, кого фильтр из выборки убирает.
data["is_unique"] = (data["name_twin_count"] == 0) & (
    data["best_jaccard"].isna() | (data["best_jaccard"] < threshold)
)

if by_networks:
    scoped = data if chosen == ALL else data[data["network"] == chosen]
    scoped = scoped.copy()
else:
    scoped = rc_scope(data, network, plant).copy()
    scoped["network"] = scoped["network"].fillna("(не указана)")
    scoped["plant"] = scoped["plant"].fillna("(не указан)")

if scoped.empty:
    st.info("В выбранном разрезе отчётов нет.")
    st.stop()

unique_only = scoped[scoped["is_unique"]]
unit = "отчётов сети" if by_networks else "записей отчётов"

# --- Плитки ------------------------------------------------------------------
# Считаются по показанному разрезу, а не по всей базе: иначе цифра сверху не
# сходилась бы с таблицей под ней.

k1, k2, k3, k4 = st.columns(4)
k1.metric(
    "Уникальных отчётов", num(len(unique_only)),
    help=f"Из {num(len(scoped))} {reports_word(len(scoped))} в этом разрезе "
         f"({unit}).",
)
k2.metric(
    "Доля уникальных",
    f"{100.0 * len(unique_only) / len(scoped):.0f}%" if len(scoped) else "—",
)
k3.metric(
    "Объём их таблиц, МБ", num(unique_only["tables_total_mb"].sum(), decimals=0),
    help="Сумма размеров таблиц уникальных отчётов. Таблицу, которую читают "
         "несколько отчётов, эта сумма считает в каждом заново — это не объём "
         "хранилища, а стоимость чтения.",
)
k4.metric(
    # min_count=1: без файла статистики вся колонка пуста, и обычная сумма дала
    # бы честный на вид ноль вместо прочерка — «отчёты не запускали» вместо
    # «данных о запусках нет».
    "Запусков", num(unique_only["exec_count"].sum(min_count=1)),
    help="Сколько раз запускались уникальные отчёты за период выгрузки "
         "статистики. Прочерк — статистика не загружена.",
)

# --- Свод по сетям или заводам ------------------------------------------------

scoped["unique_mb"] = scoped["tables_total_mb"].where(scoped["is_unique"], 0)
scoped["unique_execs"] = (
    scoped["exec_count"].fillna(0).where(scoped["is_unique"], 0)
)

keys = ["network"] if by_networks else ["network", "plant"]
summary = (
    scoped.groupby(keys, as_index=False)
    .agg(
        reports=("row_key", "count"),
        unique_reports=("is_unique", "sum"),
        compared=(COMPARED, "max"),
        unique_mb=("unique_mb", "sum"),
        unique_execs=("unique_execs", "sum"),
    )
    .sort_values("unique_reports", ascending=False)
)
summary["unique_reports"] = summary["unique_reports"].astype(int)
summary["shared_reports"] = summary["reports"] - summary["unique_reports"]
summary["unique_share"] = (
    100.0 * summary["unique_reports"] / summary["reports"]
).round(1)

st.divider()
st.subheader(
    "Сколько своих отчётов у каждой ТС" if by_networks
    else "Сколько уникальных отчётов на каждом заводе"
)

# Один стек на сеть или завод: уникальные и те, что есть на другой стороне. Две
# величины одной природы и одной шкалы — честные столбики, а не две оси.
if len(summary) > 1:
    chart = summary.sort_values("unique_reports")
    labels = (
        chart["network"] if by_networks
        else chart["network"] + " · " + chart["plant"]
    ).tolist()
    other = "Есть в других ТС" if by_networks else "Есть у соседей по ТС"
    fig = go.Figure()
    for name, column, color in (
        ("Уникальные", "unique_reports", PALETTE[0]),
        (other, "shared_reports", PALETTE[1]),
    ):
        fig.add_bar(
            y=labels, x=chart[column], name=name, orientation="h",
            marker=dict(color=color, line=dict(color=SURFACE, width=2)),
            hovertemplate="%{y}<br>" + name + ": %{x:,.0f}<extra></extra>",
        )
    fig.update_layout(
        barmode="stack", height=max(240, 62 * len(chart)),
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
    summary[keys + [
        "reports", "unique_reports", "unique_share", "unique_mb",
        "unique_execs", "compared",
    ]],
    {
        "reports": st.column_config.NumberColumn("Отчётов всего"),
        "unique_reports": st.column_config.NumberColumn("Уникальных"),
        "unique_share": st.column_config.NumberColumn("Доля, %", format="%.1f"),
        "unique_mb": st.column_config.NumberColumn(
            "Объём их таблиц, МБ", format="%.0f"),
        "unique_execs": st.column_config.NumberColumn("Запусков"),
        "compared": st.column_config.NumberColumn(
            "Сравнивалось с ТС" if by_networks else "Сравнивалось с заводами",
            help="Со скольким числом других сетей (заводов) было что "
                 "сравнивать. Ноль — сравнивать не с чем."),
    },
    height=table_height(len(summary), 260),
)

# Тот, кому не с кем сравниться, обязан быть назван: иначе «все отчёты
# уникальны» читается как вывод, хотя это отсутствие данных для сравнения.
alone = summary[summary["compared"] == 0]
if not alone.empty:
    named = ", ".join(
        f"**{r.network}**" if by_networks else f"**{r.network} · {r.plant}**"
        for r in alone.itertuples()
    )
    st.warning(
        (f"Сравнивать не с чем: {named} — других сетей с отчётами в базе нет, "
         if by_networks else
         f"Сравнивать не с чем: {named} — в этой ТС других заводов с отчётами "
         "нет, ")
        + "поэтому все их отчёты попали в уникальные. Это не признак того, что "
          "отчёты особенные.",
        icon="⚠️",
    )

blank_tables = int((unique_only["table_count"] == 0).sum())
if blank_tables:
    st.info(
        f"У {num(blank_tables)} из показанных отчётов не указано ни одной "
        f"таблицы-источника — "
        f"{plural(blank_tables, 'он проверен', 'они проверены', 'они проверены')} "
        "только по наименованию. По таблицам сравнить их нельзя.",
        icon="ℹ️",
    )

# --- Список отчётов -----------------------------------------------------------

st.divider()
st.subheader("Отчёты")

show_all = st.checkbox(
    "Показать и отчёты с двойниками",
    help="По умолчанию в списке только уникальные. Включите, чтобы увидеть "
         "остальные и то, где у каждого нашёлся двойник.",
)
listed = scoped if show_all else unique_only
search_columns = (
    ["report_name", "plants"] if by_networks
    else ["report_name", "catalog_path", "table_names", "report_no"]
)
listed = search_box(listed, search_columns, "Поиск по отчёту или таблице",
                    key="s_unique")

if listed.empty:
    st.info("Ничего не найдено. Измените условие поиска или порог сходства.")
    st.stop()

listed = listed.sort_values("tables_total_mb", ascending=False, na_position="last")


def twin_cell(row) -> str:
    """Где двойник: тёзка, похожий по таблицам или никого."""
    if row["is_unique"]:
        return "—"
    if row["name_twin_count"]:
        where = row["name_twin_networks"] if by_networks else row["name_twin_where"]
        return f"тёзка: {where}"
    twin = row["best_twin_network"] if by_networks else row["best_twin_where"]
    return f"{twin or '—'} · {row['best_jaccard']:.0%}"


if by_networks:
    columns = [
        ("ТС", 14, lambda r: r["network"]),
        ("Отчёт", 34, lambda r: r["report_name"]),
        ("Заводов", 8, lambda r: num(r["plant_count"]), True),
        ("Таблиц", 7, lambda r: num(r["table_count"]), True),
        ("Объём, МБ", 11, lambda r: num(r["tables_total_mb"], decimals=0), True),
        ("Запусков", 9, lambda r: num(r["exec_count"]), True),
    ]
else:
    columns = [
        ("Завод", 12, lambda r: r["plant"]),
        ("Отчёт", 34, lambda r: r["report_name"]),
        ("Таблиц", 7, lambda r: num(r["table_count"]), True),
        ("Объём, МБ", 11, lambda r: num(r["tables_total_mb"], decimals=0), True),
        ("Запусков", 9, lambda r: num(r["exec_count"]), True),
    ]
columns.append(
    ("Двойник", 24, twin_cell) if show_all
    else (("Заводы", 24, lambda r: r["plants"]) if by_networks
          else ("Каталог", 24, lambda r: r["catalog_path"]))
)

picked = row_picker(listed, "row_key", "unique", columns)

if picked is None:
    st.info("👆 Щёлкните по строке отчёта, чтобы увидеть его таблицы и запуски.")
else:
    st.divider()
    st.subheader(picked["report_name"])
    if by_networks:
        count = int(picked["plant_count"])
        st.caption(
            f"{picked['network']} · {count} "
            f"{plural(count, 'завод', 'завода', 'заводов')}: {picked['plants']}"
        )
    else:
        st.caption(
            f"№ {picked['report_no'] or '—'} · {picked['network'] or '—'} · "
            f"{picked['plant'] or '—'} · `{picked['catalog_path']}`"
        )

    if by_networks:
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Заводов в ТС с этим отчётом", num(picked["plant_count"]))
        m2.metric(
            "Таблиц", num(picked["table_count"]),
            help="Разных таблиц по всем заводам сети: одна и та же таблица на "
                 "трёх заводах остаётся одной таблицей.",
        )
        m3.metric(
            "Объём таблиц, МБ", num(picked["tables_total_mb"], decimals=1),
            help="Сумма по заводам сети: на каждом заводе таблицы отчёта "
                 "занимают своё место.",
        )
        m4.metric("Запусков", num(picked["exec_count"]))
    else:
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
        m5.metric("Ср. длительность, с",
                  num(picked["avg_duration_sec"], decimals=1))

    # Почему отчёт попал в уникальные — с числами, а не на слово.
    side = "сетей" if by_networks else "заводов"
    if picked[COMPARED] == 0:
        st.warning(
            f"Других {side} с отчётами для сравнения нет — сравнивать было "
            "не с чем.",
            icon="⚠️",
        )
    elif picked["name_twin_count"]:
        where = (picked["name_twin_networks"] if by_networks
                 else picked["name_twin_where"])
        st.error(
            f"Тёзка есть: {where}. Отчёт не уникален.",
            icon="🚫",
        )
    elif is_blank(picked["best_jaccard"]):
        st.success(
            f"Среди других {side} нет отчёта, у которого было бы хотя бы две "
            "общие таблицы с этим.",
            icon="✅",
        )
    else:
        twin_where = (picked["best_twin_network"] if by_networks
                      else picked["best_twin_where"])
        line = (
            f"Ближайший отчёт на другой стороне — **{picked['best_twin_report']}** "
            f"({twin_where}): сходство наборов таблиц "
            f"{picked['best_jaccard']:.0%}, общих таблиц "
            f"{num(picked['best_shared_tables'])} при пороге {threshold:.0%}."
        )
        if picked["is_unique"]:
            st.success(line, icon="✅")
        else:
            st.error(line + " Это порог или выше — отчёт не уникален.", icon="🚫")

    if not by_networks and picked["uses_view"] is True:
        st.info(
            "Отчёт обращается к данным через view: за ним могут стоять таблицы, "
            "которых нет в списке ниже. Сравнение по таблицам поэтому неполное.",
            icon="👁️",
        )

    # --- Таблицы отчёта -------------------------------------------------------
    # Размер берётся через v_report_table_size, а не прямым join к
    # fact_table_size: там строка на каждый завод, и прямой join размножил бы
    # таблицы отчёта по числу заводов.
    if by_networks:
        tables = query(
            """
            SELECT t.full_name,
                   ROUND(SUM(s.total_mb), 2)              AS total_mb,
                   MAX(s.retention_days)                  AS retention_days,
                   COUNT(DISTINCT COALESCE(r.plant, '(не указан)')) AS plants_using,
                   (SELECT COUNT(DISTINCT x.report_id) FROM bridge_report_table x
                    WHERE x.table_id = t.table_id)        AS used_by_reports
            FROM dim_report r
            JOIN bridge_report_table b      ON b.report_id = r.report_id
            JOIN dim_table t                ON t.table_id = b.table_id
            LEFT JOIN v_report_table_size s ON s.table_id = b.table_id
                                           AND s.report_id = b.report_id
            WHERE COALESCE(r.network, '(не указана)') = ?
              AND LOWER(TRIM(r.report_name)) = ?
              AND t.object_kind = 'TABLE'
            GROUP BY t.table_id, t.full_name
            ORDER BY total_mb DESC NULLS LAST
            """,
            (picked["network"], picked["name_key"]),
        )
        extra = {
            "total_mb": st.column_config.NumberColumn(
                "Объём, МБ", format="%.1f",
                help="Сумма по заводам сети, на которых отчёт эту таблицу читает."),
            "retention_days": st.column_config.NumberColumn("Глубина, дней"),
            "plants_using": st.column_config.NumberColumn(
                "Заводов ТС", help="На скольких заводах этой сети отчёт "
                                   "обращается к таблице."),
            "used_by_reports": st.column_config.NumberColumn(
                "Отчётов использует",
                help="Сколько отчётов всего читают эту таблицу — вместе с "
                     "отчётами других сетей и заводов."),
        }
        file_name = "network_report_tables.csv"
    else:
        tables = query(
            """
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
        extra = {
            "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
            "row_count": st.column_config.NumberColumn("Строк"),
            "retention_days": st.column_config.NumberColumn("Глубина, дней"),
            "used_by_reports": st.column_config.NumberColumn(
                "Отчётов использует",
                help="Сколько отчётов всего читают эту таблицу — вместе с "
                     "отчётами других заводов. 1 — только этот."),
        }
        file_name = f"report_{int(picked['report_id'])}_tables.csv"

    if tables.empty:
        st.caption("У отчёта не указано ни одной таблицы-источника.")
    else:
        shown = show_table(tables, extra, height=table_height(len(tables), 320))
        download(shown, file_name, "Выгрузить таблицы отчёта")

st.divider()
# Служебные колонки, добавленные для свода, в выгрузку не идут: в файле нужны
# поля витрины, а не промежуточные суммы страницы.
HELPERS = ("is_unique", "unique_mb", "unique_execs", "row_key")
download(
    unique_only[[c for c in data.columns if c not in HELPERS]],
    "unique_reports.csv", "Выгрузить уникальные отчёты",
)
download(summary, "unique_reports_summary.csv", "Выгрузить свод")
