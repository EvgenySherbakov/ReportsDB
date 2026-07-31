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

from reportsdb.config import SCHEMA_VERSION  # noqa: E402 — после правки sys.path

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
    """Заголовок страницы. set_page_config вызывается один раз в Home.py."""
    st.title(f"{icon} {title}" if icon else title)


def surface_color() -> str:
    """Цвет фона диаграмм — для зазоров между сегментами стопки."""
    return "#0e1117" if st.get_option("theme.base") == "dark" else "#ffffff"


# Открытые соединения. Нужны, чтобы закрыть их перед пересборкой БД: под
# Windows занятый файл нельзя ни переименовать, ни перезаписать.
_OPEN_CONNECTIONS: list[duckdb.DuckDBPyConnection] = []


def db_schema_version(con: duckdb.DuckDBPyConnection) -> int:
    """Версия структуры базы. 0 — база собрана до появления версионирования."""
    try:
        row = con.execute(
            "SELECT schema_version FROM etl_run ORDER BY run_id DESC LIMIT 1"
        ).fetchone()
    except Exception:  # noqa: BLE001 — нет колонки или самой таблицы
        return 0
    return int(row[0]) if row and row[0] is not None else 0


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

    # База, собранная прежней версией кода, не содержит новых колонок витрин.
    # Без этой проверки страницы падали бы с непонятной ошибкой SQL.
    version = db_schema_version(con)
    if version < SCHEMA_VERSION:
        st.error(
            f"База собрана прежней версией программы (структура {version} "
            f"вместо {SCHEMA_VERSION}) — в ней нет новых полей.\n\n"
            "**Что сделать:** откройте страницу **Загрузка данных** в меню слева, "
            "выберите файлы и нажмите «Загрузить». Это займёт несколько секунд.\n\n"
            "Данные при этом не потеряются: исходные файлы лежат в `data/raw/`, "
            "а прежняя база сохранится рядом как `reports.duckdb.bak`."
        )
        st.stop()
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
    "network": "ТС",
    "plant": "Завод",
    "uses_view": "Через view",
    "catalog_path": "Каталог",
    "folder_l1": "Каталог 1",
    "folder_l2": "Каталог 2",
    "folder_l3": "Каталог 3",
    "duration_band": "Длительность выборки",
    "object_kind": "Тип объекта",
    "kind_source": "Тип определён",
    "retention_days": "Глубина, дней",
    "retention_days_min": "Минимум, дней",
    "retention_band": "Группа по глубине",
    "usage_band": "Группа по обращениям",
    "table_full_name": "Таблица",
    "routine_full_name": "Функция / процедура",
    "routine_name": "Имя",
    "size_unknown": "Нет в файле размеров",
    "plant_count": "Заводов",
    "measured_at": "Дата замера",
    "tables_with_retention": "Таблиц с глубиной",
    "view_count": "View",
    "matview_count": "Mat.view",
    "temp_count": "Временных",
    "routine_count": "Функций/процедур",
    "segment_count": "Сегментов",
    "percent_of_total": "Доля БД, %",
    "percent_of_db": "Доля БД, %",
    "reports_with_view": "Из них через view",
    "table_count": "Таблиц",
    "sized_table_count": "Из них с размером",
    "exclusive_table_count": "Эксклюзивных таблиц",
    "gross_rows": "Строк, всего",
    "exclusive_rows": "Строк, эксклюзивно",
    "exec_count": "Запусков",
    "distinct_users": "Пользователей",
    "avg_duration_sec": "Ср. длительность, с",
    "total_duration_sec": "Суммарное время, с",
    "exclusive_pct_of_db": "Доля БД, %",
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


ALL = "(все)"


def rc_selector(label: str = "Распределительный центр") -> tuple[str | None, str | None]:
    """Выбор РЦ, общий для всех страниц раздела.

    Выбор хранится в session_state, поэтому при переходе между страницами
    раздела он не сбрасывается. Возвращает (сеть, завод) либо (None, None).
    """
    rc = query(
        "SELECT DISTINCT COALESCE(network, '(не указана)') AS network, "
        "COALESCE(plant, '(не указан)') AS plant FROM dim_report ORDER BY 1, 2"
    )
    options = [ALL] + [f"{r.network} · {r.plant}" for r in rc.itertuples()]
    chosen = st.selectbox(
        label, options, key="rc_choice",
        help="РЦ определяется парой «сеть + завод»: одно имя завода встречается "
             "в разных сетях и означает разные площадки.",
    )
    if chosen == ALL:
        return None, None
    network, plant = chosen.split(" · ", 1)
    return network, plant


def rc_scope(df: pd.DataFrame, network: str | None, plant: str | None) -> pd.DataFrame:
    """Оставляет строки выбранного РЦ."""
    if network is None or "network" not in df.columns:
        return df
    return df[(df["network"].fillna("(не указана)") == network)
              & (df["plant"].fillna("(не указан)") == plant)]


def search_box(
    df: pd.DataFrame,
    columns: list[str],
    label: str = "Поиск по наименованию таблицы или отчёта",
    key: str | None = None,
) -> pd.DataFrame:
    """Единый фильтр поиска: одно поле ищет сразу по нескольким колонкам.

    Заказчик просил, чтобы во всех таблицах можно было найти строку и по имени
    таблицы, и по имени отчёта — поэтому поле одно, а колонок несколько.
    """
    present = [c for c in columns if c in df.columns]
    if not present:
        return df
    text = st.text_input(label, "", key=key,
                         placeholder="часть имени таблицы или отчёта…")
    if not text:
        return df
    mask = False
    for column in present:
        mask = mask | df[column].astype(str).str.contains(text, case=False, na=False)
    found = df[mask]
    st.caption(f"Найдено строк: {len(found)} из {len(df)}.")
    return found


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
