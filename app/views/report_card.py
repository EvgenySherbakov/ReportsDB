"""Карточка отчёта: щёлкнуть по строке и увидеть всё про отчёт.

Одна таблица отчётов сверху, полная картина по выбранному — снизу: какие
таблицы он использует, сколько они весят, как часто он запускается.
"""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import (
    ACCENT,
    SECONDARY,
    download,
    is_blank,
    num,
    page_setup,
    query,
    rc_scope,
    rc_selector,
    row_picker,
    search_box,
    show_table,
    surface_color,
)

page_setup("Карточка отчёта", "🔍")
st.caption(
    "Щёлкните по строке отчёта — ниже появятся его таблицы, размеры и "
    "статистика. Повторный щелчок по выбранной строке снимает выбор."
)

# Три фильтра в один ряд: иначе они съедают полэкрана, и разбор выбранной
# строки уезжает под сгиб — а смотреть на него надо вместе с таблицей.
SORTS = {
    "По размеру таблиц": ("tables_total_mb", False),
    "По числу запусков": ("exec_count", False),
    "По числу таблиц": ("table_count", False),
    "По наименованию": ("report_name", True),
}

f1, f2, f3 = st.columns([2, 3, 2])
with f1:
    network, plant = rc_selector()
reports = rc_scope(query("SELECT * FROM v_report_tables_summary"), network, plant)
with f2:
    view = search_box(
        reports, ["report_name", "catalog_path", "table_names", "report_no"],
        "Поиск по отчёту или таблице",
        key="s_card",
    )
with f3:
    sort_by = st.selectbox("Сортировка", list(SORTS), key="card_sort")

if view.empty:
    st.info("Ничего не найдено. Измените условие поиска.")
    st.stop()

# --- Список отчётов с выбором строки ----------------------------------------
# Строки-кнопки, а не st.dataframe: тот выбирает строку только флажком в левой
# колонке, щелчок по самой строке он игнорирует.

column, ascending = SORTS[sort_by]
ordered = view.sort_values(column, ascending=ascending, na_position="last")


def full_list() -> None:
    """Полный список таблицей — с сортировкой по колонкам и выгрузкой."""
    with st.expander("Показать таблицей — сортировка по колонкам и выгрузка"):
        grid = ordered[[
            "report_no", "report_name", "network", "plant", "catalog_path",
            "table_count", "tables_total_mb", "tables_exclusive_mb", "exec_count",
            "avg_duration_sec", "uses_view",
        ]].reset_index(drop=True)
        show_table(grid, {
            "report_no": st.column_config.TextColumn("№", width="small"),
            "report_name": st.column_config.TextColumn("Отчёт", width="large"),
            "table_count": st.column_config.NumberColumn("Таблиц"),
            "tables_total_mb": st.column_config.NumberColumn(
                "Размер таблиц, МБ", format="%.1f"),
            "tables_exclusive_mb": st.column_config.NumberColumn(
                "Из них только его, МБ", format="%.1f"),
            "exec_count": st.column_config.NumberColumn("Запусков"),
            "avg_duration_sec": st.column_config.NumberColumn(
                "Ср. длит., с", format="%.1f"),
            "uses_view": st.column_config.CheckboxColumn("Через view"),
        }, height=360)
        download(grid, "reports.csv", "Выгрузить список отчётов")

full = row_picker(
    ordered, "report_id", "card",
    [
        ("Отчёт", 44, lambda r: r["report_name"]),
        ("Завод", 12, lambda r: r["plant"] or "—"),
        ("Таблиц", 7, lambda r: num(r["table_count"]), True),
        ("Размер, МБ", 12, lambda r: num(r["tables_total_mb"], decimals=1), True),
        ("Запусков", 9, lambda r: num(r["exec_count"]), True),
        ("Каталог", 30, lambda r: r["catalog_path"]),
    ],
)

if full is None:
    # Свёрнутая таблица и выгрузка — в самом низу страницы: между списком и
    # карточкой они отодвинули бы карточку за нижний край экрана.
    st.info("👆 Щёлкните по строке отчёта, чтобы увидеть его карточку.")
    full_list()
    st.stop()

# --- Карточка выбранного отчёта ---------------------------------------------

st.divider()
st.header(full["report_name"])
st.caption(
    f"№ {full['report_no'] or '—'} · {full['network'] or '—'} · "
    f"{full['plant'] or '—'} · `{full['catalog_path']}`"
)

m1, m2, m3, m4, m5 = st.columns(5)
m1.metric("Таблиц", num(full["table_count"]))
m2.metric(
    "Суммарный размер, МБ", num(full["tables_total_mb"], decimals=1),
    help="Сумма размеров всех таблиц отчёта.",
)
m3.metric(
    "Только его, МБ", num(full["tables_exclusive_mb"], decimals=1),
    help="Таблицы, которых не касается ни один другой отчёт. Столько "
         "освободится при выводе отчёта из эксплуатации.",
)
m4.metric("Запусков", num(full["exec_count"]))
m5.metric("Ср. длительность, с", num(full["avg_duration_sec"], decimals=1))

n1, n2, n3, n4, n5 = st.columns(5)
n1.metric("View", num(full["view_count"]))
n2.metric("Mat.view", num(full["matview_count"]))
n3.metric("Временных", num(full["temp_count"]))
n4.metric("Функций/процедур", num(full["routine_count"]))
n5.metric(
    "Глубина, дней", num(full["retention_days"]),
    help="Максимум по таблицам отчёта.",
)

if full["uses_view"] is True:
    st.warning(
        "Отчёт обращается к данным через view: за ним могут стоять таблицы, "
        "которых нет в списке ниже. Размер посчитан не полностью."
    )
if not is_blank(full["size_coverage_pct"]) and full["size_coverage_pct"] < 100:
    st.info(
        f"Размер известен по {full['size_coverage_pct']:.0f}% таблиц отчёта "
        f"({num(full['sized_table_count'])} из {num(full['table_count'])}). "
        "Суммарный объём — нижняя оценка."
    )

# --- Таблицы отчёта ----------------------------------------------------------

report_id = int(full["report_id"])
tabs = st.tabs(["Таблицы", "Функции и процедуры", "Прочие объекты", "SQL-запрос"])

with tabs[0]:
    tables = query(
        """
        -- Размер берётся через v_report_table_size, а не прямым join к
        -- fact_table_size: там строка на каждый завод, и прямой join размножил
        -- бы таблицы отчёта по числу заводов.
        SELECT t.full_name, t.schema_name, s.total_mb, s.percent_of_total,
               s.retention_days, s.row_count, s.segment_count,
               (SELECT COUNT(DISTINCT x.report_id) FROM bridge_report_table x
                WHERE x.table_id = t.table_id) AS used_by_reports
        FROM bridge_report_table b
        JOIN dim_table t                ON t.table_id = b.table_id
        LEFT JOIN v_report_table_size s ON s.table_id = b.table_id
                                       AND s.report_id = b.report_id
        WHERE b.report_id = ? AND t.object_kind = 'TABLE'
        ORDER BY s.total_mb DESC NULLS LAST
        """,
        (report_id,),
    )
    if tables.empty:
        st.info("У отчёта не указано ни одной таблицы-источника.")
    else:
        tables["Принадлежность"] = tables["used_by_reports"].apply(
            lambda n: "Только этот отчёт" if n == 1 else f"Общая ({n} отчётов)"
        )
        chart = tables.dropna(subset=["total_mb"])
        if not chart.empty:
            fig = px.bar(
                chart, x="total_mb", y="full_name", orientation="h",
                color="Принадлежность",
                color_discrete_map={"Только этот отчёт": ACCENT},
                color_discrete_sequence=[ACCENT, SECONDARY],
                labels={"total_mb": "Объём, МБ", "full_name": ""},
            )
            fig.update_traces(marker_line_width=2, marker_line_color=surface_color())
            fig.update_layout(
                height=max(260, 32 * len(chart)), margin=dict(l=0, r=0, t=10, b=0),
                bargap=0.35, legend=dict(orientation="h", y=1.08, x=0, title=""),
                yaxis=dict(categoryorder="array",
                           categoryarray=chart["full_name"].tolist()[::-1]),
            )
            st.plotly_chart(fig, use_container_width=True)

        shown = show_table(
            tables.drop(columns=["used_by_reports"]),
            {
                "total_mb": st.column_config.NumberColumn("Объём, МБ", format="%.1f"),
                "percent_of_total": st.column_config.NumberColumn("Доля БД, %", format="%.3f"),
                "retention_days": st.column_config.NumberColumn("Глубина, дней"),
            },
        )
        download(shown, f"report_{report_id}_tables.csv", "Выгрузить таблицы отчёта")

with tabs[1]:
    routines = query(
        "SELECT t.full_name, t.schema_name, t.table_name FROM bridge_report_table b "
        "JOIN dim_table t ON t.table_id = b.table_id "
        "WHERE b.report_id = ? AND t.object_kind = 'ROUTINE' ORDER BY 1",
        (report_id,),
    )
    if routines.empty:
        st.caption("Функции и процедуры у отчёта не указаны.")
    else:
        show_table(routines, {"full_name": "Функция / процедура", "table_name": "Имя"})

with tabs[2]:
    others = query(
        "SELECT t.object_kind, t.full_name, t.schema_name, t.kind_source "
        "FROM bridge_report_table b JOIN dim_table t ON t.table_id = b.table_id "
        "WHERE b.report_id = ? AND t.object_kind IN "
        "('VIEW', 'MATERIALIZED VIEW', 'TEMP') ORDER BY t.object_kind, t.full_name",
        (report_id,),
    )
    if others.empty:
        st.caption("View, материализованных view и временных объектов не указано.")
    else:
        show_table(others, {"object_kind": "Тип объекта", "kind_source": "Тип определён"})

with tabs[3]:
    # Не через витрину: sql_text — свойство определения отчёта, не разреза по
    # РЦ, и отдельный прямой запрос по report_id проще, чем тащить текст через
    # v_report_tables_summary ради одной страницы.
    sql_row = query(
        "SELECT sql_text FROM dim_report WHERE report_id = ?", (report_id,)
    )
    sql_text = sql_row["sql_text"].iloc[0] if not sql_row.empty else None
    if is_blank(sql_text):
        st.caption(
            "Текст SQL-запроса не загружен. Добавляется отдельным файлом на "
            "странице **Загрузка данных** (роль «SQL-запросы»)."
        )
    else:
        st.code(sql_text, language="sql")

# --- Соседи по таблицам ------------------------------------------------------

st.divider()
st.subheader("Отчёты, использующие те же таблицы")
st.caption("Кого затронет изменение таблиц этого отчёта.")

neighbours = query(
    """
    SELECT r.report_name, r.network, r.plant, r.catalog_path,
           COUNT(*) AS shared_tables
    FROM bridge_report_table a
    JOIN bridge_report_table b ON b.table_id = a.table_id AND b.report_id <> a.report_id
    JOIN dim_report r          ON r.report_id = b.report_id
    JOIN dim_table  t          ON t.table_id  = a.table_id
    WHERE a.report_id = ? AND t.object_kind = 'TABLE'
    GROUP BY 1, 2, 3, 4
    ORDER BY shared_tables DESC
    LIMIT 50
    """,
    (report_id,),
)
if neighbours.empty:
    st.caption("Ни одна таблица этого отчёта не используется другими отчётами.")
else:
    show_table(neighbours, {"shared_tables": "Общих таблиц"})

st.divider()
full_list()
