"""Произвольный SQL-запрос к базе плюс справочник по её структуре."""

from __future__ import annotations

import streamlit as st

from _shared import connect, download, page_setup, query

page_setup("SQL-запрос", "⌨️")

ROW_LIMIT = 5000

con = connect()

# Назначение объектов базы. Список колонок берётся из самой базы, а описание —
# отсюда: иначе справочник разъедется со схемой при первой же правке SQL.
OBJECTS = {
    "dim_report": "Отчёты. Строка — отчёт в конкретном РЦ: один и тот же отчёт "
                  "на разных заводах даёт разные строки.",
    "dim_table": "Объекты-источники: таблицы, view, mat.view, временные и "
                 "процедуры. Что именно — в object_kind.",
    "bridge_report_table": "Связь «отчёт ↔ объект», многие-ко-многим. Только "
                           "ссылки, своих данных не хранит.",
    "fact_table_size": "Размеры таблиц из файла размеров. Строка на «таблица + "
                       "ТС + завод»: на каждом заводе размер свой.",
    "fact_report_usage": "Обращения к отчёту и средняя длительность.",
    "etl_run": "Журнал загрузок: файл, время, версия структуры.",
    "etl_reject": "Строки исходника, которые не удалось загрузить, с причиной.",

    "v_tables_catalog": "★ Таблица №1: каталог таблиц и размеров. Ровно строки "
                        "файла размеров, от отчётов не зависит.",
    "v_report_table_size": "★ Размер таблицы для конкретного отчёта — берёт "
                           "замер завода этого отчёта. Соединять отчёт с "
                           "размером только через неё.",
    "v_rc_report_tables": "Таблица №2: отчёт и его таблицы (только TABLE).",
    "v_rc_report_routines": "Таблица №3: отчёт и его функции/процедуры.",
    "v_rc_report_usage": "Таблица №4: отчёт и обращения пользователей.",
    "v_rc_report_retention": "Таблица №5: отчёт и глубина хранения.",
    "v_rc_summary": "Шапка РЦ: сколько отчётов, таблиц, view, процедур.",
    "v_report_footprint": "★ Объём данных на отчёт: gross_mb и exclusive_mb.",
    "v_report_tables_summary": "Отчёт одной строкой: перечень таблиц, их число "
                               "и суммарный размер.",
    "v_table_criticality": "Таблица и сколько отчётов её используют — что "
                           "сломается при её изменении.",
    "v_report_cost_value": "Стоимость против пользы: объём и время против "
                           "частоты обращений, квадранты.",
    "v_report_duration": "Время выполнения как отдельная метрика стоимости.",
    "v_decommission_candidates": "Кандидаты на вывод и сколько это освободит.",
    "v_report_overlap": "Пары отчётов с почти совпадающим набором таблиц.",
    "v_catalog_overview": "Свод по папкам каталога.",
    "v_schema_overview": "Свод по схемам БД.",
    "v_network_overview": "Свод по торговым сетям и заводам.",
}

# Поля, значение которых не считывается из имени.
COLUMNS = {
    "gross_mb": "Сумма таблиц отчёта. По отчётам НЕ суммируется: общие таблицы "
                "учтутся многократно.",
    "exclusive_mb": "Таблицы, которых не касается ни один другой отчёт. Только "
                    "это освободится при выводе отчёта.",
    "total_mb": "Размер, МБ. В fact_table_size — на конкретном заводе.",
    "percent_of_total": "Доля таблицы в общем объёме БД, %.",
    "object_kind": "TABLE | VIEW | MATERIALIZED VIEW | TEMP | ROUTINE.",
    "kind_source": "Откуда известен тип: «колонка», «маска», «файл размеров», "
                   "«по умолчанию».",
    "uses_view": "Отчёт ходит через view — список его таблиц заведомо неполон.",
    "retention_days": "Глубина хранения данных в таблице, дней.",
    "segment_count": "Сколько строк выгрузки сегментов сложилось в эту таблицу.",
    "size_coverage_pct": "Доля таблиц отчёта, для которых известен размер.",
    "size_is_plant_specific": "Размер найден именно для завода отчёта, а не "
                              "взят общий.",
    "has_size": "Размер для этой пары «отчёт + таблица» вообще найден.",
    "exec_count": "Число обращений к отчёту за период.",
    "avg_duration_sec": "Средняя длительность выборки, СЕКУНДЫ.",
    "total_duration_sec": "exec_count × avg_duration_sec.",
    "is_orphan": "Таблица не используется ни одним отчётом.",
    "is_parsed_ok": "У ссылки на таблицу была схема; иначе схема «(unknown)».",
    "confidence": "Насколько можно доверять выводу о кандидате на вывод.",
    "report_count": "Сколько отчётов ссылается на таблицу.",
    "plant_count": "На скольких заводах таблица встречается в файле размеров.",
    "network": "Торговая сеть (ТС).",
    "plant": "Завод.",
    "full_name": "«схема.таблица», всегда в нижнем регистре — ключ сопоставления.",
    "catalog_path": "Путь к отчёту, собранный из трёх уровней каталога.",
    "report_id": "Суррогатный ключ отчёта.",
    "table_id": "Суррогатный ключ объекта-источника.",
    "schema_version": "Версия структуры БД, которой она собрана.",
}

structure = query(
    """
    SELECT t.table_name AS name, t.table_type AS kind,
           c.column_name AS field, c.data_type AS type
    FROM information_schema.tables t
    JOIN information_schema.columns c
      ON c.table_name = t.table_name AND c.table_schema = t.table_schema
    WHERE t.table_schema = 'main'
    ORDER BY t.table_type DESC, t.table_name, c.ordinal_position
    """
)

with st.expander("📖 Что есть в базе: таблицы, витрины и их поля", expanded=True):
    st.caption(
        "Слева — как объект называется в запросе, справа — что в нём лежит. "
        "★ отмечены витрины, с которых стоит начинать."
    )
    for kind, title in (("BASE TABLE", "Таблицы"), ("VIEW", "Витрины (представления)")):
        group = structure[structure["kind"] == kind]
        if group.empty:
            continue
        st.markdown(f"**{title}**")
        for name, fields in group.groupby("name", sort=False):
            purpose = OBJECTS.get(name, "")
            head = f"`{name}` — {purpose}" if purpose else f"`{name}`"
            with st.expander(head):
                described = fields[["field", "type"]].copy()
                described["Описание"] = described["field"].map(COLUMNS).fillna("")
                st.dataframe(
                    described.rename(columns={"field": "Поле", "type": "Тип"}),
                    hide_index=True, use_container_width=True,
                )

    st.info(
        "**Два правила, без которых цифры получатся неверными.**\n\n"
        "1. Соединять отчёт с размером таблицы только через "
        "`v_report_table_size`. Прямой `JOIN fact_table_size USING (table_id)` "
        "размножит строки отчёта по числу заводов и завысит объём кратно.\n"
        "2. Складывать по отчётам можно `exclusive_mb`, но не `gross_mb`: "
        "общая таблица входит в каждый свой отчёт заново."
    )

PRESETS = {
    "Топ отчётов по освобождаемому объёму":
        "SELECT report_name, exclusive_mb, table_count, size_coverage_pct\n"
        "FROM v_report_footprint\nORDER BY exclusive_mb DESC\nLIMIT 50;",
    "Самые критичные таблицы":
        "SELECT full_name, report_count, total_mb\n"
        "FROM v_table_criticality\nORDER BY report_count DESC\nLIMIT 50;",
    "Отчёты конкретной таблицы":
        "SELECT r.report_name, r.catalog_path\n"
        "FROM dim_report r\n"
        "JOIN bridge_report_table b ON b.report_id = r.report_id\n"
        "JOIN dim_table t ON t.table_id = b.table_id\n"
        "WHERE t.full_name = 'dbo.orders'\nORDER BY 1;",
    "Отчёты без источников":
        "SELECT report_name, catalog_path\nFROM v_report_footprint\n"
        "WHERE table_count = 0\nORDER BY 1;",
    "Таблицы, которых нет ни в одном отчёте":
        "SELECT DISTINCT full_name, total_mb, plant\n"
        "FROM v_tables_catalog\nWHERE report_count = 0\n"
        "ORDER BY total_mb DESC\nLIMIT 50;",
    "Размер таблиц отчёта по его заводу":
        "SELECT r.report_name, t.full_name, s.total_mb, s.size_is_plant_specific\n"
        "FROM v_report_table_size s\n"
        "JOIN dim_report r ON r.report_id = s.report_id\n"
        "JOIN dim_table  t ON t.table_id  = s.table_id\n"
        "ORDER BY s.total_mb DESC NULLS LAST\nLIMIT 50;",
    "Одна таблица на разных заводах":
        "SELECT full_name, network, plant, total_mb\n"
        "FROM v_tables_catalog\nWHERE full_name IN (\n"
        "  SELECT full_name FROM v_tables_catalog\n"
        "  GROUP BY full_name HAVING COUNT(DISTINCT plant) > 1)\n"
        "ORDER BY full_name, plant\nLIMIT 50;",
}

preset = st.selectbox("Готовый запрос", ["(свой запрос)"] + list(PRESETS))
default = PRESETS.get(preset, "SELECT * FROM v_report_footprint LIMIT 20;")

sql = st.text_area("SQL", value=default, height=200)

if st.button("Выполнить", type="primary"):
    try:
        df = con.execute(sql).df()
    except Exception as exc:  # noqa: BLE001 — текст ошибки нужен пользователю
        st.error(f"Ошибка запроса:\n\n```\n{exc}\n```")
    else:
        if len(df) > ROW_LIMIT:
            st.info(f"Показаны первые {ROW_LIMIT} строк из {len(df)}.")
            df = df.head(ROW_LIMIT)
        st.success(f"Строк: {len(df)}")
        st.dataframe(df, use_container_width=True, hide_index=True)
        download(df, "query_result.csv")

st.caption("База открыта только на чтение — изменить данные запросом нельзя.")
