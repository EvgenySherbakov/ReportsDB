"""Сборка самодостаточного HTML-файла для коллег.

Отдельная страница, а не кусок загрузчика: файл собирают не в тот же момент,
когда грузят данные, и искать кнопку под всей формой загрузки неудобно.
"""

from __future__ import annotations

from datetime import datetime

import streamlit as st

from _shared import DB_PATH, num, page_setup, query

from reportsdb.config import DIST_DIR

page_setup("Файл для коллег", "📤")
st.caption(
    "Один HTML-файл со всеми данными внутри: отправляется по почте, "
    "открывается двойным кликом. Ни Python, ни Docker, ни доступ к базе "
    "получателю не нужны."
)

out_path = DIST_DIR / "reportsdb.html"

if not DB_PATH.exists():
    st.warning(
        "База ещё не собрана — собирать нечего. Загрузите данные на странице "
        "**Загрузка данных**, затем вернитесь сюда."
    )
    st.stop()

# --- Что попадёт в файл ------------------------------------------------------

st.subheader("Что будет внутри")

kpi = query(
    """
    SELECT
        (SELECT COUNT(*) FROM dim_report)                        AS reports,
        (SELECT COUNT(DISTINCT full_name) FROM v_tables_catalog) AS tables,
        (SELECT COUNT(*) FROM v_decommission_candidates)         AS candidates,
        (SELECT COUNT(*) FROM (SELECT DISTINCT network, plant FROM dim_report)) AS rc
    """
).iloc[0]

c1, c2, c3, c4 = st.columns(4)
c1.metric("Отчётов", num(kpi.reports))
c2.metric("Таблиц", num(kpi.tables))
c3.metric("Кандидатов на вывод", num(kpi.candidates))
c4.metric("РЦ", num(kpi.rc))

st.caption(
    "**Внутри — то же, что видите вы.** Вкладки собраны по тем же разделам "
    "меню: Обзор, вся «Аналитика РЦ» (№1–№5), весь раздел «Отчёты» (карточка, "
    "объём и стоимость, кандидаты на вывод, похожие отчёты, ABC-анализ) и "
    "справочник таблиц. Те же показатели, те же колонки, тот же разбор строки "
    "по щелчку, общий выбор РЦ на весь файл. Списки идут целиком, ничего не "
    "обрезается. Поиск, сортировка и выгрузка в CSV работают без интернета.\n\n"
    "Не переносятся загрузка данных и произвольный SQL — это инструменты "
    "владельца базы."
)

st.info(
    "**Файл содержит рабочие данные.** Отправляйте его так же, как саму "
    "выгрузку: внутри реальные наименования отчётов, таблиц и схем.",
    icon="🔒",
)

st.divider()

# --- Сборка ------------------------------------------------------------------

if out_path.exists():
    built = datetime.fromtimestamp(out_path.stat().st_mtime)
    st.caption(
        f"Файл на диске: `{out_path.name}`, "
        f"{out_path.stat().st_size / 1024:.0f} КБ, собран {built:%d.%m.%Y %H:%M}. "
        "Внутри данные, которые были в базе на тот момент — после новой "
        "загрузки соберите заново."
    )

if st.button("Собрать HTML-файл", type="primary", use_container_width=True):
    from reportsdb.export_html import export

    try:
        with st.spinner("Собираю файл…"):
            out_path = export(DB_PATH)
    except Exception as exc:  # noqa: BLE001 — причина нужна пользователю
        st.error(f"Не удалось собрать файл:\n\n```\n{type(exc).__name__}: {exc}\n```")
        st.stop()

    st.success(
        f"Готово: `{out_path}` — {out_path.stat().st_size / 1024:.0f} КБ."
    )

# Кнопка скачивания живёт вне обработчика нажатия: иначе она исчезала бы при
# первом же перезапуске страницы, который сама и вызывает.
if out_path.exists():
    st.download_button(
        "Скачать HTML-файл",
        out_path.read_bytes(),
        file_name=f"reportsdb_{datetime.now():%Y%m%d}.html",
        mime="text/html",
        use_container_width=True,
    )
    st.caption(
        f"Файл лежит и на диске: `{out_path}` — можно взять оттуда напрямую."
    )
