"""Общее для страниц Streamlit: подключение к БД, палитра, выгрузка CSV."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Callable
from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from reportsdb.config import SCHEMA_VERSION  # noqa: E402 — после правки sys.path

DB_PATH = Path(os.environ.get("REPORTSDB_PATH", ROOT / "data" / "reports.duckdb"))

# Фон диаграмм. Обязан совпадать с backgroundColor в .streamlit/config.toml:
# этим цветом рисуются зазоры между сегментами, и расхождение видно как кайма.
SURFACE = "#0f1419"

# Палитра для тёмной темы — те же три оттенка эталонной палитры, но ступени,
# подобранные под тёмный фон, а не осветлённые автоматически. Проверено
# scripts/validate_palette.js (--mode dark --surface #0f1419 --pairs all):
# светлота, цветность, различимость при дальтонизме и контраст к фону — все
# проверки пройдены.
#
# Три цвета — это потолок, а не лень. Круговая диаграмма сравнивает все доли
# между собой (режим --pairs all), и четвёртый цвет из палитры этой проверки
# не проходит: жёлтый и оранжевый неразличимы даже при полном цветовосприятии
# (ΔE 10.6 при пороге 15). Поэтому доли в круговых — строго три, хвост
# сворачивается в «Прочие», а где классов больше — столбики одним цветом.
PALETTE = ["#3987e5", "#d95926", "#199e70"]
ACCENT = PALETTE[0]      # основной ряд
SECONDARY = PALETTE[1]   # второй ряд в стопке
TERTIARY = PALETTE[2]    # третий ряд; дальше цвета не добавлять
MUTED = "#8b93a1"        # рецессивные элементы: сетка, опорные линии

FOOTPRINT_HINT = (
    "**gross_mb** суммирует общие таблицы в каждом отчёте заново — складывать этот "
    "столбец по отчётам нельзя, это не объём хранилища. Освобождаемый при выводе "
    "отчёта объём — это **exclusive_mb**: таблицы, которых не касается больше никто."
)


# Единый вид интерфейса: карточки-показатели, мягкие рамки таблиц, спокойные
# заголовки меню. Все цвета — оттенком поверх текущего фона (rgba, не hex),
# поэтому выглядит одинаково в светлой и тёмной теме, не только в одной из
# них. Селекторы сверены с фактическим DOM Streamlit 1.60 через Playwright —
# data-testid у Streamlit не документированы и меняются между версиями.
_THEME_CSS = """
<style>
h1 {
    letter-spacing: -0.01em;
    padding-bottom: 0.5rem;
    border-bottom: 1px solid rgba(127, 127, 127, 0.18);
    margin-bottom: 1.4rem !important;
}

div[data-testid="stMetric"] {
    background: rgba(127, 127, 127, 0.055);
    border: 1px solid rgba(127, 127, 127, 0.13);
    border-radius: 10px;
    padding: 0.85rem 1.1rem 0.65rem;
}
label[data-testid="stMetricLabel"] p {
    font-size: 0.76rem;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    opacity: 0.68;
}
div[data-testid="stMetricValue"] { font-weight: 650; }

div[data-testid="stDataFrame"], div[data-testid="stDataFrameResizable"] {
    border-radius: 10px;
    overflow: hidden;
    border: 1px solid rgba(127, 127, 127, 0.16);
}

/* Не трогает строки-таблицы row_picker — та стилизация специфичнее и не
   перебивается этой. */
button[data-testid^="stBaseButton"] {
    border-radius: 8px;
    transition: background-color 120ms ease, border-color 120ms ease,
                box-shadow 120ms ease;
}

hr { opacity: 0.4; }

[data-testid="stNavSectionHeader"] p {
    font-size: 0.72rem;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    opacity: 0.55;
    font-weight: 600;
}
</style>
"""


def inject_theme() -> None:
    """Единый вид приложения — вызывается один раз, из Home.py."""
    st.markdown(_THEME_CSS, unsafe_allow_html=True)


def page_setup(title: str, icon: str = "📊") -> None:
    """Заголовок страницы. set_page_config вызывается один раз в Home.py."""
    st.title(f"{icon} {title}" if icon else title)


def surface_color() -> str:
    """Цвет фона диаграмм — для зазоров между сегментами стопки."""
    return SURFACE


def donut(
    labels: list[str],
    values: list[float],
    title: str,
    unit: str = "",
    height: int = 300,
) -> None:
    """Круговая диаграмма «часть от целого». Строго до трёх долей.

    Больше трёх — нельзя: в круге читатель сравнивает все доли между собой, а
    четвёртый цвет палитры эту проверку не проходит (см. комментарий к
    PALETTE). Если классов больше, сверните хвост в «Прочие» до вызова.

    Доли подписаны процентами прямо на диаграмме — идентичность не держится на
    одном цвете. Между долями зазор цветом фона: рамку вокруг сегментов
    рисовать нельзя, разделяет именно зазор.
    """
    import plotly.graph_objects as go

    if len(labels) > len(PALETTE):
        raise ValueError(
            f"{title}: долей {len(labels)}, а безопасных цветов {len(PALETTE)}. "
            "Сверните хвост в «Прочие»."
        )

    st.markdown(f"**{title}**")
    fig = go.Figure(
        go.Pie(
            labels=labels,
            values=values,
            hole=0.58,
            sort=False,                     # порядок долей задаёт вызывающий
            direction="clockwise",
            marker=dict(colors=PALETTE[: len(labels)],
                        line=dict(color=SURFACE, width=2)),
            # Единый формат: без шаблона Plotly сам подбирает точность, и в
            # одной диаграмме оказывается «38,4%» рядом с «7,21%».
            texttemplate="%{percent:.0%}",
            textposition="inside",
            insidetextorientation="horizontal",
            textfont=dict(size=13),
            hovertemplate="%{label}<br>%{value:,.0f}" + unit + " · %{percent}<extra></extra>",
        )
    )
    fig.update_layout(
        height=height,
        margin=dict(l=0, r=0, t=4, b=0),
        showlegend=True,
        legend=dict(orientation="h", y=-0.08, x=0, font=dict(size=12)),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        separators=", ",
    )
    st.plotly_chart(fig, use_container_width=True)


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


def table_height(rows: int, limit: int = 400) -> int:
    """Высота таблицы по числу строк, но не выше `limit`.

    Фиксированная высота у короткой таблицы оставляет полосу пустых строк —
    на дашборде это выглядит как незаполненные данные.
    """
    return min(limit, 38 + 35 * max(rows, 1))


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
    "is_orphan": "Не используется отчётами",
    "is_parsed_ok": "Схема распознана",
    "schema_source": "Схема определена",
    "reports": "Зависимые отчёты",
}

# Технические ключи: в таблицах не показываем, в CSV они не нужны тоже.
TECHNICAL = ["report_id", "table_id"]

# Типы объектов по-русски. В базе они хранятся английскими кодами (так их
# видно из любого SQL-клиента), а в интерфейсе показываются словами.
OBJECT_KINDS = {
    "TABLE": "Таблица",
    "VIEW": "View (представление)",
    "MATERIALIZED VIEW": "Mat.view (материализованное)",
    "TEMP": "Временная",
    "ROUTINE": "Функция / процедура",
}


def plural(count: int, one: str, few: str, many: str) -> str:
    """«1 отчёт», «2 отчёта», «5 отчётов» — русское согласование с числом.

    Без него в интерфейсе попадаются «23 отчётов» и «33 отчётов»: мелочь,
    которая читается как небрежность ровно там, где нужно доверие к цифрам.
    """
    n = abs(int(count))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def reports_word(count: int) -> str:
    return plural(count, "отчёт", "отчёта", "отчётов")


def kind_ru(kind: str | None) -> str:
    """Русское название типа объекта; незнакомый код возвращается как есть."""
    return OBJECT_KINDS.get(kind, kind or "—")


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


def search_terms(text: str) -> list[str]:
    """Строка поиска → список искомых значений.

    Разделители — запятая, точка с запятой и перенос строки. **Пробел
    разделителем не служит**: имена отчётов состоят из нескольких слов, и по
    пробелу «Продажи за месяц» распалось бы на три бесполезных обрывка.
    Имена таблиц пробелов не содержат, поэтому список таблиц удобно вставлять
    как есть — хоть через запятую, хоть колонкой из Excel.
    """
    return [part.strip() for part in re.split(r"[,;\n]+", text) if part.strip()]


def search_box(
    df: pd.DataFrame,
    columns: list[str],
    label: str = "Поиск по наименованию таблицы или отчёта",
    key: str | None = None,
) -> pd.DataFrame:
    """Единый фильтр поиска: одно поле ищет сразу по нескольким колонкам.

    Заказчик просил, чтобы во всех таблицах можно было найти строку и по имени
    таблицы, и по имени отчёта — поэтому поле одно, а колонок несколько.

    **Значений в строке может быть несколько**, через запятую или с новой
    строки: строка остаётся, если подходит хотя бы под одно из них. Это разбор
    списка таблиц «покажи вот эти двадцать», а не пересечение условий —
    пересечение по разным таблицам дало бы пустой результат всегда.

    **Два режима сравнения.** По умолчанию — часть имени: так ищут, когда
    помнят фрагмент. Для списка готовых имён этого мало: `itm.log` найдёт и
    `dbo.catalog`, и `wh1.errlog`, потому что «log» в них есть. Флажок
    «Точное совпадение» переводит поиск на равенство целиком; имя без схемы
    тоже считается совпадением, чтобы `TRIP_LOG` находил `sdd.trip_log` —
    в выгрузках половина имён приходит со схемой, половина без.

    Поиск подстрокой идёт без регулярных выражений: заказчик вставляет
    настоящие имена, и `[dbo].[Orders]` при разборе как регулярка нашла бы
    совсем не то, а одинокая `*` уронила бы страницу.
    """
    present = [c for c in columns if c in df.columns]
    if not present:
        return df
    # text_area, а не text_input: в однострочное поле список из Excel не
    # вставить — браузер по стандарту вырезает переводы строк, и колонка из
    # двадцати таблиц слипается в одно бессмысленное слово. Высота
    # минимальная, чтобы поле не съедало экран у страниц, где под таблицей
    # должен помещаться разбор выбранной строки.
    text = st.text_area(
        label, "", key=key, height=68,
        placeholder="часть имени; несколько — через запятую или списком из Excel",
        help="Можно указать несколько значений: через запятую, точку с запятой "
             "или каждое с новой строки — например, вставить столбец из Excel. "
             "Строка попадёт в результат, если подходит хотя бы под одно из "
             "них. Пробел разделителем не считается: имена отчётов состоят из "
             "нескольких слов.",
    )
    terms = search_terms(text)
    exact = st.checkbox(
        "Точное совпадение", key=f"{key}_exact" if key else None,
        help="Искать имена целиком, а не как часть. Включайте, когда вставили "
             "список готовых имён: иначе «itm.log» найдёт заодно «dbo.catalog» "
             "и «wh1.errlog». Имя без схемы тоже считается совпадением.",
    )
    if not terms:
        return df

    mask = False
    matched = set()
    for term in terms:
        hit = False
        for column in present:
            values = df[column].astype(str)
            if exact:
                lowered = values.str.lower()
                column_hit = lowered.eq(term.lower())
                # Имя без схемы: в выгрузке половина имён приходит как
                # «схема.таблица», половина — голым именем.
                if "." not in term:
                    column_hit = column_hit | lowered.str.rsplit(
                        ".", n=1).str[-1].eq(term.lower())
            else:
                column_hit = values.str.contains(
                    term, case=False, na=False, regex=False)
            mask = mask | column_hit
            hit = hit or bool(column_hit.any())
        if hit:
            matched.add(term)

    found = df[mask]
    caption = f"Найдено строк: {len(found)} из {len(df)}."
    if len(terms) > 1:
        caption += f" Значений в запросе: {len(terms)}."
        missing = [t for t in terms if t not in matched]
        if missing:
            # Какие именно имена не нашлись — самое ценное при вставке списка:
            # иначе непонятно, то ли их нет в базе, то ли опечатка в запросе.
            shown = ", ".join(f"`{t}`" for t in missing[:10])
            tail = f" и ещё {len(missing) - 10}" if len(missing) > 10 else ""
            caption += f" Ничего не найдено по: {shown}{tail}."
    st.caption(caption)

    # Подсказка вместо догадок: список готовых имён почти всегда ищут целиком,
    # а поиск по части молча приносит соседей — заказчик замечает это уже на
    # результатах и не знает, что делать.
    if len(terms) > 1 and not exact:
        st.info(
            "Ищется **часть имени**, поэтому в выборку попадают и другие "
            "строки: `itm.log` находит заодно `dbo.catalog` и `wh1.errlog`. "
            "Вставили список готовых имён — включите **Точное совпадение**.",
            icon="🔎",
        )
    return found


def num(value, suffix: str = "", decimals: int = 0) -> str:
    """Число для показателя. Пустое значение — прочерк, а не «nan» и не сбой.

    Проверять пустоту сравнением `x != x` нельзя: у nullable-типов pandas
    ячейка приходит как `pd.NA`, а `pd.NA != pd.NA` возвращает не True, а сам
    `pd.NA` — Streamlit падает с «boolean value of NA is ambiguous».
    Единственная надёжная проверка — `pd.isna`.

    Заодно решает вторую задачу: в строке есть NULL-колонки, из-за чего pandas
    приводит весь ряд к float, и счётчики нужно возвращать к целым явно.
    """
    if value is None or pd.isna(value):
        return "—"
    return f"{value:,.{decimals}f}".replace(",", " ") + suffix


def is_blank(value) -> bool:
    """Пустое ли значение. Безопасно для pd.NA, NaN, None и обычных чисел."""
    return value is None or pd.isna(value)


# Строка-кнопка должна читаться как строка таблицы, а не как кнопка: без
# рамки, без фона, с тонкой линией снизу и в один-два ряда пикселей отступа.
# Иначе двенадцать строк занимают весь экран и разбор под таблицей не виден.
#
# Тонкости, каждая из которых проверена в браузере:
# - селектор цепляется за класс st-key-<ключ> на контейнере с key: обёртка из
#   st.markdown кнопки не охватывает, они рендерятся отдельными блоками;
# - внутри кнопки ещё два слоя (div и span) со своим justify-content: center —
#   выравнивать нужно каждый;
# - вертикальный блок Streamlit ставит между элементами gap 16px, из-за него
#   строки разъезжаются на полэкрана;
# - фон гасим только у secondary: у выбранной строки (primary) он и есть
#   подсветка выбора.
_ROW_CSS = """
<style>
.st-key-%(key)s { gap: 0 !important; }
.st-key-%(key)s button {
    padding: 0.14rem 0.5rem !important;
    min-height: 0 !important;
    border-radius: 0 !important;
    border: none !important;
    border-bottom: 1px solid rgba(128, 128, 128, 0.22) !important;
    font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace !important;
    font-size: 0.78rem !important;
    line-height: 1.5 !important;
}
.st-key-%(key)s button[kind="secondary"] { background: transparent !important; }
.st-key-%(key)s button[kind="secondary"]:hover {
    background: rgba(42, 120, 214, 0.12) !important;
}
.st-key-%(key)s button,
.st-key-%(key)s button > div,
.st-key-%(key)s button span { justify-content: flex-start !important; }
/* Шрифт задаём и на p, и на контейнере разметки: правило Streamlit для
   markdown-абзаца перебивает наследование, и ячейки перестают быть
   моноширинными — колонки разъезжаются. */
.st-key-%(key)s button > div,
.st-key-%(key)s button span,
.st-key-%(key)s button p,
.st-key-%(key)s button [data-testid="stMarkdownContainer"] p {
    width: 100%%;
    text-align: left !important;
    margin: 0;
    font-family: ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace !important;
    font-size: 0.78rem !important;
    white-space: pre !important;
    overflow: hidden;
}
/* Шапка — такая же кнопка, только выключенная: только так её метрики
   гарантированно совпадают с ячейками. Отдельный div подобрать по шрифту
   не удалось. */
.st-key-%(key)s button:disabled {
    opacity: 1 !important;
    color: rgba(128, 128, 128, 0.95) !important;
    border-bottom: 2px solid rgba(128, 128, 128, 0.5) !important;
    cursor: default !important;
}
</style>
"""

NBSP = " "
# Символы, которые Streamlit истолкует как разметку. В наименованиях отчётов
# встречается и подчёркивание, и звёздочка — без экранирования подпись поедет.
_MD_SPECIAL = str.maketrans({c: "\\" + c for c in "\\*_[]`~"})


def _cell(text: str, width: int, right: bool = False) -> str:
    """Ячейка фиксированной ширины: обрезает длинное, добивает короткое.

    Добивка неразрывными пробелами: обычные Streamlit схлопнет как разметку,
    и колонки перестанут совпадать со шапкой.
    """
    text = "" if text is None else str(text)
    if len(text) > width:
        text = text[: width - 1] + "…"
    pad = NBSP * (width - len(text))
    return (pad + text if right else text + pad) + NBSP


def row_picker(
    df: pd.DataFrame,
    id_column: str,
    key: str,
    columns: list[tuple],
    page_size: int = 12,
) -> pd.Series | None:
    """Таблица, в которой нажимается вся строка целиком.

    `st.dataframe` умеет выбирать строку только флажком в левой колонке —
    щелчок по самой строке он игнорирует (проверено на Streamlit 1.60, это
    последняя версия). Заказчику нужен щелчок по строке, поэтому строка —
    кнопка, а вид таблицы держится на моноширинном шрифте и колонках
    фиксированной ширины.

    `columns` — список `(заголовок, ширина в знаках, функция от строки)`;
    четвёртым элементом можно передать True для выравнивания вправо.

    Выбор хранится по значению `id_column`, а не по номеру строки: при смене
    сортировки или фильтра номер уезжает, а выбранная строка должна остаться
    той же. Возвращает выбранную строку или None.
    """
    if df.empty:
        return None

    box_key = f"rows_{key}"
    st.markdown(_ROW_CSS % {"key": box_key}, unsafe_allow_html=True)
    chosen_key = f"pick_{key}"
    page_key = f"page_{key}"

    pages = max(1, -(-len(df) // page_size))
    page = min(st.session_state.get(page_key, 1), pages)
    start = (page - 1) * page_size
    window = df.iloc[start:start + page_size]

    header = "".join(
        _cell(spec[0], spec[1], len(spec) > 3 and spec[3]) for spec in columns
    ).translate(_MD_SPECIAL)

    with st.container(key=box_key):
        st.button(header, key=f"{key}_head", use_container_width=True, disabled=True)
        for _, row in window.iterrows():
            row_id = row[id_column]
            selected = st.session_state.get(chosen_key) == row_id
            label = "".join(
                _cell(spec[2](row), spec[1], len(spec) > 3 and spec[3])
                for spec in columns
            ).translate(_MD_SPECIAL)
            if st.button(
                label,
                key=f"{key}_row_{row_id}",
                use_container_width=True,
                type="primary" if selected else "secondary",
            ):
                # Повторный щелчок по выбранной строке снимает выбор.
                st.session_state[chosen_key] = None if selected else row_id
                st.rerun()

    if pages > 1:
        back, info, forward = st.columns([1, 2, 1])
        if back.button("‹ Назад", key=f"{key}_prev", disabled=page <= 1,
                       use_container_width=True):
            st.session_state[page_key] = page - 1
            st.rerun()
        info.markdown(
            f"<div style='text-align:center;padding-top:0.5rem'>"
            f"Страница {page} из {pages} · строк {len(df)}</div>",
            unsafe_allow_html=True,
        )
        if forward.button("Вперёд ›", key=f"{key}_next", disabled=page >= pages,
                          use_container_width=True):
            st.session_state[page_key] = page + 1
            st.rerun()

    picked = st.session_state.get(chosen_key)
    if picked is None:
        return None
    match = df[df[id_column] == picked]
    if match.empty:  # строка ушла из-под фильтра — выбор снимаем
        return None
    return match.iloc[0]


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
    """Честно предупреждает, каких данных ещё нет.

    Пустая база — отдельный случай, а не «нет размеров»: так выглядит и
    только что очищенная база, и только что созданная. Показывать на ней
    предупреждение про недостающие факты бессмысленно — нет вообще ничего.
    """
    if not has_data("dim_report") and not has_data("fact_table_size"):
        st.info(
            "**База пуста** — данные не загружены или база была очищена. "
            "Откройте **Данные → Загрузка данных**, выберите файлы и нажмите "
            "«Загрузить». Показатели ниже останутся нулевыми до этого.",
            icon="📭",
        )
        return

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
