"""Точка входа приложения: меню с разделами.

Страницы лежат в app/views/. Меню собирается через st.navigation, потому что
только так получаются вложенные разделы — папка pages/ их не поддерживает.
"""

from __future__ import annotations

import streamlit as st

from _shared import inject_theme

st.set_page_config(
    page_title="ReportsDB — аналитика отчётности",
    page_icon="🗂️",
    layout="wide",
)
inject_theme()

NAV = {
    "Данные": [
        st.Page("views/overview.py", title="Обзор", icon="🗂️", default=True),
        st.Page("views/load.py", title="Загрузка данных", icon="📥"),
    ],
    "Аналитика РЦ": [
        st.Page("views/rc1_tables.py", title="№1 Таблицы и размеры", icon="1️⃣"),
        st.Page("views/rc2_report_tables.py", title="№2 Отчёт → таблицы", icon="2️⃣"),
        st.Page("views/rc3_routines.py", title="№3 Отчёт → функции", icon="3️⃣"),
        st.Page("views/rc4_usage.py", title="№4 Отчёт → обращения", icon="4️⃣"),
        st.Page("views/rc5_retention.py", title="№5 Отчёт → глубина хранения", icon="5️⃣"),
    ],
    "Отчёты": [
        st.Page("views/report_card.py", title="Карточка отчёта", icon="🔍"),
        st.Page("views/cost.py", title="Объём и стоимость", icon="💾"),
        st.Page("views/candidates.py", title="Кандидаты на вывод", icon="🧹"),
        st.Page("views/report_overlap.py", title="Похожие отчёты", icon="🧩"),
        st.Page("views/unique_reports.py", title="Уникальные отчёты", icon="🏷️"),
        st.Page("views/abc.py", title="ABC-анализ", icon="📈"),
    ],
    "Справочники": [
        st.Page("views/tables.py", title="Таблицы", icon="🗃️"),
        st.Page("views/networks.py", title="Сети и заводы", icon="🏭"),
    ],
    "Инструменты": [
        st.Page("views/export.py", title="Файл для коллег", icon="📤"),
        st.Page("views/sql.py", title="SQL-запрос", icon="⌨️"),
    ],
}

st.navigation(NAV).run()
