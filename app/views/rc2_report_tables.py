"""№2. Отчёт → таблицы, один ко многим.

Два взгляда на одну связь. «По отчётам» отвечает на вопрос «из каких таблиц
состоит этот отчёт», «По таблицам» — на обратный «в какие отчёты попадает эта
таблица». Щелчок по строке в любом из них разворачивает разбор этой строки.
"""

from __future__ import annotations

import streamlit as st

from _shared import (
    download,
    num,
    page_setup,
    query,
    rc_scope,
    rc_selector,
    row_picker,
    search_box,
    show_table,
)

page_setup("№2. Отчёт → таблицы", "2️⃣")
st.caption(
    "Только настоящие таблицы. View, материализованные view, временные и "
    "generated-объекты исключены — они не отражают физического хранения."
)

network, plant = rc_selector()
df = rc_scope(query("SELECT * FROM v_rc_report_tables"), network, plant)

if df.empty:
    st.info("Для выбранного РЦ нет ни одной связи отчёта с таблицей.")
    st.stop()

c1, c2, c3 = st.columns(3)
c1.metric("Отчётов", num(df["report_name"].nunique()))
c2.metric("Уникальных таблиц", num(df["table_full_name"].nunique()))
c3.metric(
    "Таблиц на отчёт, среднее",
    num(len(df) / df["report_name"].nunique(), decimals=1),
)

by_reports, by_tables = st.tabs(["По отчётам", "По таблицам"])

# --- Вид 1: строка — отчёт ---------------------------------------------------

with by_reports:
    st.caption("Строка — отчёт. Видно, из каких таблиц он состоит.")
    reports = (
        df.groupby(["report_name", "network", "plant", "catalog_path"], as_index=False)
        .agg(
            table_count=("table_full_name", "nunique"),
            total_mb=("total_mb", "sum"),
            retention_days=("retention_days", "max"),
        )
        .sort_values("total_mb", ascending=False)
        .reset_index(drop=True)
    )
    # Ключ строки собран из полей, а не из её номера: при смене фильтра или
    # сортировки номер уезжает, а выбранная строка должна остаться той же.
    reports["row_key"] = (
        reports["network"].astype(str) + "|" + reports["plant"].astype(str) + "|"
        + reports["catalog_path"].astype(str) + "|" + reports["report_name"]
    )
    found = search_box(reports, ["report_name", "catalog_path"],
                       "Поиск по наименованию отчёта", key="s_rc2_rep")

    picked = row_picker(
        found, "row_key", "rc2rep",
        lambda r: (
            f"**{r['report_name']}**  ·  {r['table_count']} табл.  ·  "
            f"{num(r['total_mb'], ' МБ', 1)}  ·  `{r['catalog_path']}`"
        ),
    )
    download(found.drop(columns=["row_key"]), "rc_2_by_reports.csv",
             "Выгрузить: отчёты и их таблицы")

    if picked is None:
        st.info("👆 Щёлкните по строке отчёта — ниже появится разбор.")
    else:
        st.divider()
        rows = df[
            (df["report_name"] == picked["report_name"])
            & (df["catalog_path"] == picked["catalog_path"])
            & (df["plant"].astype(str) == str(picked["plant"]))
        ]
        st.subheader(picked["report_name"])
        st.caption(f"{picked['network']} · {picked['plant']} · `{picked['catalog_path']}`")

        a1, a2, a3, a4 = st.columns(4)
        a1.metric("Таблиц-источников", num(picked["table_count"]))
        a2.metric("Суммарный объём, МБ", num(picked["total_mb"], decimals=1))
        a3.metric(
            "Крупнейшая таблица, МБ", num(rows["total_mb"].max(), decimals=1),
            help="Самая тяжёлая таблица отчёта — с неё начинается оптимизация.",
        )
        a4.metric(
            "Глубина, дней", num(picked["retention_days"]),
            help="Максимум по таблицам отчёта.",
        )

        no_size = int(rows["total_mb"].isna().sum())
        if no_size:
            st.info(
                f"У {no_size} из {len(rows)} таблиц отчёта нет размера в файле "
                "размеров — суммарный объём это нижняя оценка."
            )
        show_table(
            rows[["table_full_name", "schema_name", "total_mb",
                  "percent_of_total", "retention_days"]]
            .sort_values("total_mb", ascending=False),
            {
                "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
                "percent_of_total": st.column_config.NumberColumn(
                    "Доля БД, %", format="%.3f"),
                "retention_days": st.column_config.NumberColumn("Глубина, дней"),
            },
        )

# --- Вид 2: строка — таблица -------------------------------------------------

with by_tables:
    st.caption("Строка — таблица. Видно, в какие отчёты она попадает.")
    tables = (
        df.groupby(["table_full_name", "schema_name"], as_index=False)
        .agg(
            report_count=("report_name", "nunique"),
            total_mb=("total_mb", "max"),
            retention_days=("retention_days", "max"),
        )
        .sort_values("report_count", ascending=False)
        .reset_index(drop=True)
    )
    st.caption(
        "Объём взят как максимум по строкам таблицы, а не как сумма: одна и та "
        "же таблица приходит здесь в нескольких отчётах, и складывать её "
        "размер заново на каждый отчёт нельзя."
    )
    found_t = search_box(tables, ["table_full_name", "schema_name"],
                         "Поиск по наименованию таблицы", key="s_rc2_tab")

    picked_t = row_picker(
        found_t, "table_full_name", "rc2tab",
        lambda r: (
            f"**{r['table_full_name']}**  ·  в {r['report_count']} отчётах  ·  "
            f"{num(r['total_mb'], ' МБ', 1)}"
        ),
    )
    download(found_t, "rc_2_by_tables.csv", "Выгрузить: таблицы и их отчёты")

    if picked_t is None:
        st.info("👆 Щёлкните по строке таблицы — ниже появится разбор.")
    else:
        st.divider()
        rows = df[df["table_full_name"] == picked_t["table_full_name"]]
        st.subheader(picked_t["table_full_name"])
        st.caption(f"Схема: {picked_t['schema_name']}")

        b1, b2, b3, b4 = st.columns(4)
        b1.metric("Отчётов", num(picked_t["report_count"]))
        b2.metric("Объём, МБ", num(picked_t["total_mb"], decimals=1))
        b3.metric(
            "Заводов", num(rows["plant"].nunique()),
            help="На скольких заводах эта таблица участвует в отчётах.",
        )
        b4.metric("Глубина, дней", num(picked_t["retention_days"]))

        if picked_t["report_count"] == 1:
            st.success(
                "Таблицу использует один отчёт: при его выводе из эксплуатации "
                "её объём освободится целиком."
            )
        else:
            st.warning(
                f"Таблицу использует {picked_t['report_count']} отчётов — "
                "изменение затронет их все, а вывод одного отчёта места не "
                "освободит."
            )
        show_table(
            rows[["report_name", "network", "plant", "catalog_path"]]
            .drop_duplicates()
            .sort_values("report_name"),
        )
