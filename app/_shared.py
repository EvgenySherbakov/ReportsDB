"""Общее для страниц Streamlit: подключение к БД, палитра, выгрузка CSV."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

DB_PATH = Path(os.environ.get("REPORTSDB_PATH", ROOT / "data" / "reports.duckdb"))

# Палитра проверена скриптом validate_palette.js: цветность, различимость при
# дальтонизме и контраст к фону. Пары использовать только соседние.
PALETTE = ["#2a78d6", "#eb6834", "#1baf7a"]
ACCENT = PALETTE[0]      # основной ряд
SECONDARY = PALETTE[1]   # второй ряд в стопке
MUTED = "#8a8985"        # рецессивные элементы: сетка, опорные линии

FOOTPRINT_HINT = (
    "**gross_mb** суммирует общие таблицы в каждом отчёте заново — складывать этот "
    "столбец по отчётам нельзя, это не объём хранилища. Освобождаемый при выводе "
    "отчёта объём — это **exclusive_mb**: таблицы, которых не касается больше никто."
)


def page_setup(title: str, icon: str = "📊") -> None:
    st.set_page_config(page_title=f"{title} · ReportsDB", page_icon=icon, layout="wide")
    st.title(title)


def surface_color() -> str:
    """Цвет фона диаграмм — для зазоров между сегментами стопки."""
    return "#0e1117" if st.get_option("theme.base") == "dark" else "#ffffff"


# Открытые соединения. Нужны, чтобы закрыть их перед пересборкой БД: под
# Windows занятый файл нельзя ни переименовать, ни перезаписать.
_OPEN_CONNECTIONS: list[duckdb.DuckDBPyConnection] = []


@st.cache_resource
def connect() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        st.error(
            f"База не найдена: `{DB_PATH}`\n\n"
            "Загрузите данные на странице **Загрузка данных** или соберите базу "
            "командой `python -m reportsdb build data/raw/<файл>.xlsx`."
        )
        st.stop()
    con = duckdb.connect(str(DB_PATH), read_only=True)
    _OPEN_CONNECTIONS.append(con)
    return con


def release_db() -> None:
    """Отпускает файл БД и сбрасывает кэши — вызывать перед пересборкой."""
    for con in _OPEN_CONNECTIONS:
        try:
            con.close()
        except Exception:  # noqa: BLE001 — соединение уже закрыто, это не ошибка
            pass
    _OPEN_CONNECTIONS.clear()
    st.cache_resource.clear()
    st.cache_data.clear()


@st.cache_data(show_spinner=False)
def query(sql: str, params: tuple = ()) -> pd.DataFrame:
    return connect().execute(sql, list(params)).df()


# Русские подписи колонок — одни и те же на всех страницах.
LABELS = {
    "report_no": "№",
    "report_name": "Отчёт",
    "catalog_path": "Каталог",
    "folder_l1": "Папка",
    "table_count": "Таблиц",
    "sized_table_count": "Из них с размером",
    "exclusive_table_count": "Эксклюзивных таблиц",
    "gross_rows": "Строк, всего",
    "exclusive_rows": "Строк, эксклюзивно",
    "exec_count": "Запусков",
    "distinct_users": "Пользователей",
    "avg_duration_ms": "Длительность, мс",
    "last_executed_at": "Последний запуск",
    "mb_per_execution": "МБ на запуск",
    "quadrant": "Квадрант",
    "confidence": "Уверенность",
    "full_name": "Таблица",
    "schema_name": "Схема",
    "table_name": "Имя таблицы",
    "report_count": "Отчётов",
    "row_count": "Строк",
    "total_mb": "Объём, МБ",
    "is_orphan": "Сирота",
    "is_parsed_ok": "Схема распознана",
    "reports": "Зависимые отчёты",
}

# Технические ключи: в таблицах не показываем, в CSV они не нужны тоже.
TECHNICAL = ["report_id", "table_id"]


def show_table(df: pd.DataFrame, extra: dict | None = None, **kwargs) -> pd.DataFrame:
    """Таблица с русскими подписями и без суррогатных ключей."""
    view = df.drop(columns=[c for c in TECHNICAL if c in df.columns]).copy()
    # Пустые текстовые ячейки Streamlit рисует как «None» — заменяем на прочерк.
    # Числовые колонки не трогаем: строка сломала бы сортировку по значению.
    for col in view.columns:
        if view[col].dtype == "object":
            view[col] = view[col].fillna("—")
    config = {k: v for k, v in (extra or {}).items() if k in view.columns}
    for col in view.columns:
        if col not in config and col in LABELS:
            config[col] = LABELS[col]
    st.dataframe(view, use_container_width=True, hide_index=True,
                 column_config=config, **kwargs)
    return view


def download(df: pd.DataFrame, filename: str, label: str = "Выгрузить CSV") -> None:
    st.download_button(
        label,
        df.to_csv(index=False).encode("utf-8-sig"),
        file_name=filename,
        mime="text/csv",
    )


def has_data(table: str) -> bool:
    return query(f"SELECT COUNT(*) AS n FROM {table}")["n"].iloc[0] > 0


def missing_facts_notice() -> None:
    """Честно предупреждает, каких данных ещё нет."""
    missing = []
    if not has_data("fact_table_size"):
        missing.append("размеры таблиц")
    if not has_data("fact_report_usage"):
        missing.append("частота использования отчётов")
    if missing:
        st.warning(
            "Пока не загружены: " + ", ".join(missing)
            + ". Столбцы, зависящие от этих данных, будут пустыми."
        )
