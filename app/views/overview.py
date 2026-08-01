"""Обзорная страница — главный дашборд.

Порядок сверху вниз: плитки-показатели, три круговых разреза, справочные
таблицы, качество загрузки. Всё в разрезе выбранного РЦ.
"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import (
    ACCENT,
    donut,
    download,
    missing_facts_notice,
    num,
    page_setup,
    query,
    rc_selector,
    show_table,
    table_height,
)

page_setup("Обзор отчётности SSRS", "🗂️")
missing_facts_notice()

network, plant = rc_selector()

# Все показатели страницы считаются в разрезе выбранного РЦ. Отчёты фильтруются
# по своим сети и заводу, таблицы — по своим: размер одной таблицы на разных
# заводах разный, и складывать заводы в один итог нельзя.
scope = "по всем ТС и заводам" if network is None else f"{network} · {plant}"
st.subheader(f"Показатели: {scope}")

if network is None:
    where_reports, where_tables, params = "TRUE", "TRUE", ()
else:
    where_reports = (
        "COALESCE(network, '(не указана)') = ? AND COALESCE(plant, '(не указан)') = ?"
    )
    where_tables = "network = ? AND plant = ?"
    params = (network, plant)

# --- Плитки-показатели -------------------------------------------------------

kpi = query(
    f"""
    SELECT
        (SELECT COUNT(*) FROM dim_report WHERE {where_reports})          AS reports,
        (SELECT COUNT(DISTINCT full_name) FROM v_tables_catalog
          WHERE {where_tables})                                          AS tables,
        (SELECT ROUND(SUM(total_mb), 1) FROM v_tables_catalog
          WHERE {where_tables})                                          AS total_mb,
        (SELECT COUNT(DISTINCT LOWER(schema_name)) FROM v_tables_catalog
          WHERE {where_tables})                                          AS schemas
    """,
    params * 4 if params else (),
).iloc[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Отчётов", num(kpi.reports))
c2.metric(
    "Таблиц", num(kpi.tables),
    help="Из таблицы №1 «Таблицы и размеры» — то есть из файла размеров. "
         "Считаются сами таблицы, а не строки «таблица + завод».",
)
c3.metric(
    "Объём таблиц, ГБ", num((kpi.total_mb or 0) / 1024, decimals=1),
    help="Сумма размеров таблиц из файла размеров по выбранному разрезу. "
         "В мегабайтах точное значение — в таблице схем ниже.",
)
c4.metric("Схем БД", num(kpi.schemas))

st.divider()

# --- Три круговых разреза ----------------------------------------------------
# По три доли в каждой: в круге читатель сравнивает все доли между собой, и
# четвёртый цвет палитры эту проверку на различимость не проходит. Где классов
# больше — хвост свёрнут в «Прочие».

st.subheader("Из чего это состоит")

d1, d2, d3 = st.columns(3)

with d1:
    volume = query(
        f"""
        SELECT
            CASE WHEN report_count = 0 THEN 'Не используются'
                 WHEN report_count = 1 THEN 'Один отчёт'
                 ELSE 'Несколько отчётов' END AS grp,
            ROUND(SUM(total_mb), 1) AS mb
        FROM v_tables_catalog
        WHERE {where_tables} AND total_mb IS NOT NULL
        GROUP BY 1
        """,
        params,
    )
    order = ["Несколько отчётов", "Один отчёт", "Не используются"]
    volume = volume.set_index("grp").reindex(order).fillna(0.0).reset_index()
    if volume["mb"].sum() > 0:
        donut(order, volume["mb"].tolist(), "Объём по использованию", " МБ")
        st.caption(
            "«Не используются» — объём, который освободится целиком. "
            "Использование считается по всем отчётам, а не только по выбранному РЦ."
        )
    else:
        st.info("Размеры таблиц не загружены.")

with d2:
    retention = query(
        f"""
        SELECT retention_band AS grp, COUNT(*) AS n
        FROM v_rc_report_retention
        WHERE {where_reports} AND retention_band <> 'Не задана'
        GROUP BY 1
        """,
        params,
    )
    order = ["До 30 дней", "От 31 до 45 дней", "Более 45 дней"]
    retention = retention.set_index("grp").reindex(order).fillna(0).reset_index()
    unknown = query(
        f"SELECT COUNT(*) AS n FROM v_rc_report_retention "
        f"WHERE {where_reports} AND retention_band = 'Не задана'",
        params,
    )["n"].iloc[0]
    if retention["n"].sum() > 0:
        donut(order, retention["n"].tolist(), "Глубина хранения", " отч.")
        st.caption(
            f"Глубина отчёта — максимум по его таблицам. Без глубины: {unknown}."
        )
    else:
        st.info("Глубина хранения не загружена.")

with d3:
    usage = query(
        f"""
        SELECT
            CASE WHEN exec_count IS NULL OR exec_count = 0 THEN 'Не запускались'
                 WHEN exec_count <= 100 THEN 'До 100 обращений'
                 ELSE 'Более 100' END AS grp,
            COUNT(*) AS n
        FROM v_rc_report_usage
        WHERE {where_reports}
        GROUP BY 1
        """,
        params,
    )
    order = ["Более 100", "До 100 обращений", "Не запускались"]
    usage = usage.set_index("grp").reindex(order).fillna(0).reset_index()
    if usage["n"].sum() > 0:
        donut(order, usage["n"].tolist(), "Обращения к отчётам", " отч.")
        st.caption(
            "Отчёты без обращений — первые кандидаты на вывод из эксплуатации."
        )
    else:
        st.info("Статистика обращений не загружена.")

st.divider()

# --- Справочные таблицы ------------------------------------------------------

left, right = st.columns(2)

with left:
    st.subheader("Отчёты по каталогам")
    catalog = query(
        f"""
        SELECT folder_l1, COUNT(*) AS report_count
        FROM dim_report
        WHERE {where_reports} AND folder_l1 IS NOT NULL AND folder_l1 <> ''
        GROUP BY 1
        ORDER BY report_count DESC
        """,
        params,
    )
    if not catalog.empty:
        # Столбики одним цветом: здесь важна величина, а не различение рядов,
        # поэтому цвет не несёт смысла и множить его незачем.
        fig = px.bar(
            catalog.sort_values("report_count"),
            x="report_count", y="folder_l1", orientation="h",
            labels={"report_count": "Отчётов", "folder_l1": ""},
            color_discrete_sequence=[ACCENT],
        )
        fig.update_traces(marker_cornerradius=4,
                          hovertemplate="%{y}<br>%{x} отчётов<extra></extra>")
        fig.update_layout(
            height=max(200, 38 * len(catalog)), margin=dict(l=0, r=0, t=4, b=0),
            bargap=0.4, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            xaxis=dict(gridcolor="rgba(139,147,161,0.18)", zeroline=False),
            yaxis=dict(showgrid=False),
        )
        st.plotly_chart(fig, use_container_width=True)

    with st.expander("Полная иерархия каталогов"):
        full = query(
            f"""
            SELECT catalog_path, COUNT(*) AS report_count
            FROM dim_report
            WHERE {where_reports}
            GROUP BY 1
            ORDER BY report_count DESC
            """,
            params,
        )
        shown = show_table(full, {
            "catalog_path": "Каталог", "report_count": "Отчётов",
        }, height=table_height(len(full)))
        download(shown, "catalog_overview.csv")

with right:
    st.subheader("Схемы БД")
    # Таблицей, а не диаграммой: схем много, столбики уезжали далеко вниз, а
    # у части схем размер неизвестен — на диаграмме это пустое место.
    schemas = query(
        f"""
        -- LOWER на случай базы, собранной до приведения схем к одному регистру.
        SELECT LOWER(schema_name) AS schema_name,
               COUNT(DISTINCT full_name) AS table_count,
               ROUND(SUM(total_mb), 1)   AS total_mb,
               ROUND(SUM(percent_of_total), 2) AS percent_of_db
        FROM v_tables_catalog
        WHERE {where_tables}
        GROUP BY 1
        ORDER BY total_mb DESC NULLS LAST
        """,
        params,
    )
    sized = schemas[schemas["total_mb"].notna() & (schemas["total_mb"] > 0)]
    empty_count = len(schemas) - len(sized)
    st.caption(
        f"Схем с известным размером: **{len(sized)}** из {len(schemas)}."
        + (f" Без размера: {empty_count} — их таблиц нет в файле размеров."
           if empty_count else "")
    )
    shown = show_table(sized, {
        "schema_name": "Схема",
        "table_count": st.column_config.NumberColumn("Таблиц"),
        "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
        "percent_of_db": st.column_config.ProgressColumn(
            "Доля БД, %", format="%.2f",
            min_value=0.0, max_value=float(max(sized["percent_of_db"].max(), 1))
            if not sized.empty else 1.0,
        ),
    }, height=table_height(len(sized)))
    download(shown, "schemas.csv")

st.divider()

# --- Качество загрузки -------------------------------------------------------

st.subheader("Качество исходных данных")

q1, q2 = st.columns(2)
with q1:
    rejects = query("SELECT source_row, reason, payload FROM etl_reject")
    st.caption(f"Отброшено строк при загрузке: **{len(rejects)}**")
    if not rejects.empty:
        show_table(rejects, {
            "source_row": "Строка файла", "reason": "Причина", "payload": "Данные строки",
        }, height=table_height(len(rejects), 240))
with q2:
    unparsed = query(
        "SELECT full_name, table_name FROM dim_table WHERE NOT is_parsed_ok ORDER BY 1"
    )
    recovered = query(
        "SELECT COUNT(*) AS n FROM dim_table WHERE schema_source = 'файл размеров'"
    )["n"].iloc[0]
    caption = f"Ссылок на таблицы без схемы: **{len(unparsed)}**"
    if recovered:
        caption += f" (ещё {recovered} восстановлено по файлу размеров)"
    st.caption(caption)
    if not unparsed.empty:
        show_table(unparsed, {"full_name": "Таблица", "table_name": "Имя таблицы"},
                   height=table_height(len(unparsed), 240))

run = query("SELECT * FROM etl_run ORDER BY run_id DESC LIMIT 1")
if not run.empty:
    r = run.iloc[0]
    st.caption(
        f"Загружено из `{r.source_file}` — {r.started_at:%Y-%m-%d %H:%M}, "
        f"версия {r.tool_version}, sha256 `{r.source_sha256[:12]}…`"
    )
