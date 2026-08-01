"""Похожие отчёты: пары, которые ссылаются на один и тот же набор таблиц.

Сигнал для объединения дублирующихся отчётов — в том числе такого, который
внутри одного РЦ не увидеть: один и тот же отчёт мог быть независимо собран
для разных заводов и с тех пор разъехался по мелочам.
"""

from __future__ import annotations

import streamlit as st

from _shared import download, num, page_setup, query, row_picker, search_box

page_setup("Похожие отчёты", "🧩")
st.caption(
    "Пара отчётов, ссылающихся на одни и те же таблицы. Считаются только "
    "настоящие таблицы — view, mat.view, временные и процедуры не в счёт. "
    "Совпадение по названию отчёта на разных заводах — это один и тот же "
    "отчёт, а не похожие; такие пары не показаны."
)

pairs = query(
    """
    WITH cnt AS (
        SELECT b.report_id, COUNT(*) AS n
        FROM bridge_report_table b
        JOIN dim_table t ON t.table_id = b.table_id
        WHERE t.object_kind = 'TABLE'
        GROUP BY b.report_id
        HAVING COUNT(*) >= 2
    ),
    pairs AS (
        -- Порог «не меньше 2 общих таблиц» — иначе любые два отчёта,
        -- ссылающиеся хоть раз на общий справочник вроде календаря, попадут
        -- в кандидаты, а таких пар на реальных данных могут быть десятки тысяч.
        SELECT a.report_id AS id1, b.report_id AS id2, COUNT(*) AS shared
        FROM bridge_report_table a
        JOIN bridge_report_table b ON b.table_id = a.table_id AND b.report_id > a.report_id
        JOIN dim_table t            ON t.table_id = a.table_id
        WHERE t.object_kind = 'TABLE'
        GROUP BY 1, 2
        HAVING COUNT(*) >= 2
    )
    SELECT
        r1.report_id AS report_id_1, r1.report_name AS report_1,
        r1.network AS network_1, r1.plant AS plant_1, r1.catalog_path AS catalog_1,
        r2.report_id AS report_id_2, r2.report_name AS report_2,
        r2.network AS network_2, r2.plant AS plant_2, r2.catalog_path AS catalog_2,
        p.shared AS shared_tables, c1.n AS tables_1, c2.n AS tables_2,
        ROUND(p.shared::DOUBLE / (c1.n + c2.n - p.shared), 3) AS jaccard,
        (p.shared = c1.n AND p.shared = c2.n) AS exact_match
    FROM pairs p
    JOIN cnt c1        ON c1.report_id = p.id1
    JOIN cnt c2        ON c2.report_id = p.id2
    JOIN dim_report r1 ON r1.report_id = p.id1
    JOIN dim_report r2 ON r2.report_id = p.id2
    -- Один отчёт, заведённый для разных заводов, ссылается на одни и те же
    -- таблицы всегда — это не дубль по смыслу, а один и тот же отчёт.
    WHERE NOT (r1.report_name = r2.report_name AND r1.catalog_path = r2.catalog_path)
    ORDER BY jaccard DESC, shared_tables DESC
    """
)

if pairs.empty:
    st.info(
        "Совпадений не найдено. Это ожидаемо на разнородных данных — "
        "загляните сюда снова после накопления реальных отчётов."
    )
    st.stop()

pairs["row_key"] = (
    pairs["report_id_1"].astype(str) + "-" + pairs["report_id_2"].astype(str)
)

min_jaccard = st.slider(
    "Минимальное сходство наборов таблиц", 0.0, 1.0, 0.3, 0.05,
    help="Доля общих таблиц от объединения обоих наборов (индекс Жаккара). "
         "1.0 — наборы совпадают полностью.",
)
found = pairs[pairs["jaccard"] >= min_jaccard]
found = search_box(found, ["report_1", "report_2", "catalog_1", "catalog_2"],
                   "Поиск по наименованию отчёта", key="s_overlap")

# KPI считаются по тому, что реально показано ниже (после порога и поиска) —
# иначе цифра сверху не совпадала бы с длиной таблицы под ней.
c1, c2, c3 = st.columns(3)
c1.metric("Показано пар", num(len(found)),
          help=f"Всего кандидатов при пороге ≥ 0: {num(len(pairs))}.")
c2.metric(
    "Точных совпадений", num(int(found["exact_match"].sum())),
    help="Оба отчёта ссылаются ровно на один и тот же набор таблиц.",
)
c3.metric(
    "Разных заводов", num(int((found["plant_1"] != found["plant_2"]).sum())),
    help="Пара, где отчёты числятся за разными заводами — кандидат, который "
         "не видно ни на одной странице «Аналитика РЦ» сразу.",
)

picked = row_picker(
    found, "row_key", "overlap",
    [
        ("Отчёт 1", 30, lambda r: r["report_1"]),
        ("Завод 1", 10, lambda r: r["plant_1"] or "—"),
        ("Отчёт 2", 30, lambda r: r["report_2"]),
        ("Завод 2", 10, lambda r: r["plant_2"] or "—"),
        ("Общих", 7, lambda r: num(r["shared_tables"]), True),
        ("Сходство", 9, lambda r: f"{r['jaccard']:.0%}", True),
    ],
)

if picked is None:
    st.info("👆 Щёлкните по паре, чтобы увидеть общие и различающиеся таблицы.")
else:
    st.divider()
    st.subheader(f"{picked['report_1']}  ↔  {picked['report_2']}")
    st.caption(
        f"{picked['network_1'] or '—'} · {picked['plant_1'] or '—'} · "
        f"`{picked['catalog_1']}`  —  "
        f"{picked['network_2'] or '—'} · {picked['plant_2'] or '—'} · "
        f"`{picked['catalog_2']}`"
    )
    if picked["exact_match"]:
        st.success("Отчёты ссылаются ровно на один и тот же набор таблиц.")

    detail = query(
        """
        SELECT t.full_name,
               COUNT(*) FILTER (WHERE b.report_id = ?) AS in_1,
               COUNT(*) FILTER (WHERE b.report_id = ?) AS in_2
        FROM bridge_report_table b
        JOIN dim_table t ON t.table_id = b.table_id
        WHERE b.report_id IN (?, ?) AND t.object_kind = 'TABLE'
        GROUP BY t.full_name
        ORDER BY in_1 DESC, in_2 DESC, t.full_name
        """,
        (int(picked["report_id_1"]), int(picked["report_id_2"]),
         int(picked["report_id_1"]), int(picked["report_id_2"])),
    )
    detail["Есть у"] = detail.apply(
        lambda r: "у обоих" if r["in_1"] and r["in_2"]
        else ("только у 1-го" if r["in_1"] else "только у 2-го"),
        axis=1,
    )
    st.dataframe(
        detail[["full_name", "Есть у"]].rename(columns={"full_name": "Таблица"}),
        hide_index=True, use_container_width=True,
    )

download(pairs.drop(columns=["row_key"]), "similar_reports.csv", "Выгрузить все пары")
