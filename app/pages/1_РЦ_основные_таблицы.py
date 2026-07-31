"""Пять основных представлений в разрезе РЦ (завода).

Аналитика ведётся по каждому РЦ отдельно. РЦ определяется парой «сеть + завод»:
одно и то же имя завода встречается в разных торговых сетях, и объединять их
нельзя — это разные площадки.
"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import (
    ACCENT,
    MUTED,
    download,
    page_setup,
    query,
    show_table,
)

page_setup("РЦ: основные таблицы", "🏢")

ALL = "(все РЦ)"

summary = query("SELECT * FROM v_rc_summary")
if summary.empty:
    st.info("Нет загруженных данных. Откройте страницу «Загрузка данных».")
    st.stop()

# --- Выбор РЦ ---------------------------------------------------------------

pairs = [ALL] + [
    f"{row.network} · {row.plant}"
    for row in summary.sort_values(["network", "plant"]).itertuples()
]
chosen = st.selectbox(
    "Распределительный центр", pairs,
    help="РЦ определяется парой «сеть + завод»: одно имя завода встречается "
         "в разных сетях и означает разные площадки.",
)


def scope(df):
    """Оставляет строки выбранного РЦ."""
    if chosen == ALL:
        return df
    network, plant = chosen.split(" · ", 1)
    return df[(df["network"] == network) & (df["plant"] == plant)]


head = scope(summary) if chosen != ALL else summary
k1, k2, k3, k4, k5, k6 = st.columns(6)
k1.metric("Отчётов", int(head["report_count"].sum()))
k2.metric("Таблиц", int(head["table_count"].sum()))
k3.metric("View", int(head["view_count"].sum()))
k4.metric("Mat.view", int(head["matview_count"].sum()))
k5.metric("Временных", int(head["temp_count"].sum()))
k6.metric("Функций/процедур", int(head["routine_count"].sum()))

if chosen == ALL:
    st.caption(
        "Показаны все РЦ сразу. Выберите конкретный, чтобы таблицы ниже "
        "относились к одной площадке."
    )

st.divider()

t1, t2, t3, t4, t5 = st.tabs([
    "№1 Таблицы и размеры",
    "№2 Отчёт → таблицы",
    "№3 Отчёт → функции",
    "№4 Отчёт → обращения",
    "№5 Отчёт → глубина хранения",
])

# --- №1. Таблицы и их размеры, один к одному --------------------------------

with t1:
    st.subheader("Таблица №1. Объект и его размер")
    st.caption(
        "Одна строка на объект внутри РЦ. Включены таблицы и материализованные "
        "view — то, что реально занимает место. Обычные view, временные объекты "
        "и процедуры сюда не входят."
    )
    df1 = scope(query("SELECT * FROM v_rc_tables"))

    # Одна и та же таблица обслуживает отчёты нескольких РЦ. Её размер —
    # свойство таблицы, а не РЦ, поэтому в сумме и на диаграмме объекты
    # берутся по одному разу. Иначе объём складывался бы повторно.
    unique1 = df1.drop_duplicates(subset=["full_name"])

    c1, c2, c3 = st.columns(3)
    c1.metric(
        "Объектов", len(unique1),
        help="Уникальные объекты. Строк в таблице ниже больше, если объект "
             "используют отчёты нескольких РЦ.",
    )
    c2.metric(
        "Суммарный объём, МБ",
        f"{unique1['total_mb'].sum():,.0f}".replace(",", " ")
        if unique1["total_mb"].notna().any() else "—",
        help="Каждый объект посчитан один раз, без двойного учёта общих таблиц.",
    )
    unknown = int(unique1["size_unknown"].sum()) if len(unique1) else 0
    c3.metric(
        "Без размера", unknown,
        help="Объекты, которых нет в файле размеров. Их объём неизвестен, "
             "а не равен нулю.",
    )

    if chosen == ALL and len(df1) > len(unique1):
        st.caption(
            f"Строк с учётом принадлежности к РЦ — {len(df1)}, уникальных "
            f"объектов — {len(unique1)}. Показатели выше считают каждый объект "
            "один раз."
        )

    if unique1["total_mb"].notna().any():
        top = unique1.nlargest(20, "total_mb")
        fig = px.bar(
            top, x="total_mb", y="full_name", orientation="h",
            labels={"total_mb": "Объём, МБ", "full_name": ""},
            hover_data=["object_kind", "retention_days", "report_count"],
            color_discrete_sequence=[ACCENT],
        )
        fig.update_layout(
            height=max(300, 30 * len(top)), margin=dict(l=0, r=0, t=10, b=0),
            bargap=0.35,
            yaxis=dict(categoryorder="array",
                       categoryarray=top["full_name"].tolist()[::-1]),
        )
        st.plotly_chart(fig, use_container_width=True)

    shown = show_table(
        df1.sort_values("total_mb", ascending=False),
        {
            "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
            "percent_of_total": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
            "retention_days": st.column_config.NumberColumn("Глубина, дней"),
            "object_kind": "Тип объекта",
            "kind_source": "Тип определён",
            "size_unknown": st.column_config.CheckboxColumn("Размер неизвестен"),
        },
    )
    download(shown, "rc_1_tables.csv")

# --- №2. Отчёт и его таблицы, один ко многим --------------------------------

with t2:
    st.subheader("Таблица №2. Отчёт и его таблицы")
    st.caption(
        "Только настоящие таблицы. View, материализованные view, временные и "
        "generated-объекты исключены — они в таблицах №1 и №3 либо не относятся "
        "к физическому хранению."
    )
    df2 = scope(query("SELECT * FROM v_rc_report_tables"))

    c1, c2, c3 = st.columns(3)
    c1.metric("Связей отчёт → таблица", len(df2))
    c2.metric("Отчётов", df2["report_name"].nunique())
    c3.metric("Уникальных таблиц", df2["table_full_name"].nunique())

    search = st.text_input("Поиск по отчёту или таблице", "", key="q2")
    if search:
        mask = df2["report_name"].str.contains(search, case=False, na=False) | \
            df2["table_full_name"].str.contains(search, case=False, na=False)
        df2 = df2[mask]

    shown = show_table(
        df2,
        {
            "table_full_name": "Таблица",
            "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
            "percent_of_total": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
            "retention_days": st.column_config.NumberColumn("Глубина, дней"),
            "object_kind": "Тип объекта",
            "kind_source": "Тип определён",
        },
    )
    download(shown, "rc_2_report_tables.csv")

# --- №3. Отчёт и его функции и процедуры ------------------------------------

with t3:
    st.subheader("Таблица №3. Отчёт и его функции и процедуры")
    df3 = scope(query("SELECT * FROM v_rc_report_routines"))

    if df3.empty:
        st.info(
            "Функции и процедуры не загружены. Добавьте в файл отчётов колонку "
            "**«Функции/процедуры»** со списком через `;` — витрина заполнится "
            "автоматически, править конфиг не нужно."
        )
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Связей отчёт → процедура", len(df3))
        c2.metric("Отчётов", df3["report_name"].nunique())
        c3.metric("Уникальных процедур", df3["routine_full_name"].nunique())

        st.subheader("Самые востребованные процедуры")
        top = (
            df3.groupby("routine_full_name", as_index=False)
            .agg(report_count=("report_name", "nunique"))
            .nlargest(15, "report_count")
        )
        fig = px.bar(
            top, x="report_count", y="routine_full_name", orientation="h",
            labels={"report_count": "Отчётов", "routine_full_name": ""},
            color_discrete_sequence=[ACCENT],
        )
        fig.update_layout(
            height=max(280, 30 * len(top)), margin=dict(l=0, r=0, t=10, b=0),
            bargap=0.35,
            yaxis=dict(categoryorder="array",
                       categoryarray=top["routine_full_name"].tolist()[::-1]),
        )
        st.plotly_chart(fig, use_container_width=True)

        shown = show_table(
            df3,
            {"routine_full_name": "Функция / процедура", "routine_name": "Имя"},
        )
        download(shown, "rc_3_report_routines.csv")

# --- №4. Отчёт и обращения пользователей ------------------------------------

with t4:
    st.subheader("Таблица №4. Отчёт и обращения пользователей")
    st.caption(
        "Мера пользовательской активности — «Кол-во обращений». Это число "
        "запусков, а не уникальных пользователей: если появится отдельная "
        "колонка с пользователями, она встанет рядом в столбце «Пользователей»."
    )
    df4 = scope(query("SELECT * FROM v_rc_report_usage"))

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Отчётов", len(df4))
    c2.metric(
        "Всего обращений",
        f"{df4['exec_count'].sum():,.0f}".replace(",", " ")
        if df4["exec_count"].notna().any() else "—",
    )
    c3.metric("Не запускались", int((df4["exec_count"] == 0).sum()))
    c4.metric("Без данных", int(df4["exec_count"].isna().sum()))

    if df4["exec_count"].notna().any():
        bands = ["Не запускался", "До 10 обращений", "От 10 до 100",
                 "Более 100", "Нет данных"]
        counts = (
            df4.groupby("usage_band", as_index=False)
            .agg(report_count=("report_name", "count"))
        )
        counts["order"] = counts["usage_band"].apply(
            lambda b: bands.index(b) if b in bands else len(bands)
        )
        counts = counts.sort_values("order")
        fig = px.bar(
            counts, x="usage_band", y="report_count",
            labels={"usage_band": "", "report_count": "Отчётов"},
            color_discrete_sequence=[ACCENT],
        )
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), bargap=0.4)
        st.plotly_chart(fig, use_container_width=True)

    shown = show_table(
        df4.sort_values("exec_count", ascending=False),
        {
            "usage_band": "Группа по обращениям",
            "uses_view": st.column_config.CheckboxColumn("Через view"),
            "avg_duration_sec": st.column_config.NumberColumn(
                "Ср. длительность, с", format="%.1f"),
            "total_duration_sec": st.column_config.NumberColumn(
                "Суммарное время, с", format="%.0f"),
        },
    )
    download(shown, "rc_4_report_usage.csv")

# --- №5. Отчёт и глубина хранения -------------------------------------------

with t5:
    st.subheader("Таблица №5. Отчёт и глубина хранения данных")
    st.caption(
        "Глубина задана на таблицу. Глубина отчёта — **максимум** по его "
        "таблицам: отчёт показывает столько дней, сколько хранит самая "
        "«долгая» его таблица."
    )
    df5 = scope(query("SELECT * FROM v_rc_report_retention"))

    if not df5["retention_days"].notna().any():
        st.info(
            "Глубина хранения не загружена. Добавьте в файл размеров таблиц "
            "колонку **«Глубина хранения»** (в днях) — витрина заполнится "
            "автоматически, править конфиг не нужно."
        )
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Более 45 дней", int((df5["retention_band"] == "Более 45 дней").sum()))
        c2.metric("Медиана, дней", f"{df5['retention_days'].median():.0f}")
        c3.metric("Без глубины", int(df5["retention_days"].isna().sum()))

        bands = ["До 30 дней", "От 31 до 45 дней", "Более 45 дней", "Не задана"]
        counts = (
            df5.groupby("retention_band", as_index=False)
            .agg(report_count=("report_name", "count"))
        )
        counts["order"] = counts["retention_band"].apply(
            lambda b: bands.index(b) if b in bands else len(bands)
        )
        counts = counts.sort_values("order")
        fig = px.bar(
            counts, x="retention_band", y="report_count",
            labels={"retention_band": "", "report_count": "Отчётов"},
            color_discrete_sequence=[ACCENT],
        )
        fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), bargap=0.4)
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            "Отчёты с глубиной более 45 дней — первые кандидаты на сокращение "
            "срока хранения, если такая глубина не нужна бизнесу."
        )

    shown = show_table(
        df5.sort_values("retention_days", ascending=False),
        {
            "retention_band": "Группа по глубине",
            "retention_days": st.column_config.NumberColumn("Глубина, дней"),
            "retention_days_min": st.column_config.NumberColumn("Минимум, дней"),
            "tables_with_retention": "Таблиц с глубиной",
        },
    )
    download(shown, "rc_5_report_retention.csv")
