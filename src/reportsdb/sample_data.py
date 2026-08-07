"""Синтетические данные — позволяют проверить весь конвейер до появления
реального Excel-файла. Структура файлов повторяет ожидаемую от заказчика.
"""

from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

from .config import RAW_DIR

# Каталог приходит тремя отдельными колонками — уровнями пути пользователя.
FOLDER_LEVELS = [
    ("Финансы", "Ежемесячные", "Продажи"),
    ("Финансы", "Ежедневные", ""),
    ("Продажи", "Региональные", "Детализация"),
    ("Продажи", "Руководству", ""),
    ("Логистика", "Склад", "Комплектация"),
    ("Логистика", "Перевозки", ""),
    ("Персонал", "", ""),
]

# Завод принадлежит ровно одной торговой сети — как в жизни. Случайная пара
# «сеть + завод» ломала бы сопоставление размеров с отчётами.
PLANT_NETWORK = {
    "Завод 1": "СЕТЬ-А",
    "Завод 2": "СЕТЬ-А",
    "Завод 3": "СЕТЬ-Б",
    "Завод 4": "СЕТЬ-В",
}
PLANTS = list(PLANT_NETWORK)
NETWORKS = sorted(set(PLANT_NETWORK.values()))

SCHEMAS = ["dbo", "sales", "fin", "ops", "stg"]
ENTITIES = [
    "Orders", "Customers", "Invoice", "InvoiceLine", "Product", "Category",
    "Employee", "Department", "Shipment", "Warehouse", "Stock", "Payment",
    "Currency", "Region", "Calendar", "GLAccount", "CostCenter", "Budget",
    "Forecast", "PriceList", "Supplier", "PurchaseOrder", "Return", "Audit",
]

REPORT_PREFIX = [
    "Monthly", "Daily", "Weekly", "Executive", "Detailed", "Consolidated",
    "Regional", "Legacy", "Ad-hoc",
]
REPORT_SUBJECT = [
    "Sales Summary", "Revenue Breakdown", "Stock Levels", "Headcount",
    "Shipment Status", "Margin Analysis", "Payment Register", "Budget vs Actual",
    "Supplier Performance", "Returns Overview",
]


def generate(seed: int = 42, report_count: int = 120) -> dict[str, Path]:
    """Создаёт три файла в data/raw/ и возвращает пути к ним."""
    rnd = random.Random(seed)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # Универсум таблиц заметно шире числа отчётов — иначе эксклюзивных таблиц
    # не остаётся и exclusive_mb вырождается в ноль.
    all_tables = sorted(
        {
            f"{schema}.{entity}{suffix}"
            for schema in SCHEMAS
            for entity in ENTITIES
            for suffix in ("", "_Hist", "_Stg", "_Archive")
        }
    )

    # View, материализованные view, временные объекты и процедуры — отдельными
    # списками, как в файле заказчика.
    views = [f"{s}.V_{e.upper()}" for s in SCHEMAS[:3] for e in ENTITIES[:6]]
    matviews = [f"{s}.MV_{e.upper()}" for s in SCHEMAS[:2] for e in ENTITIES[:4]]
    temps = [f"{s}.TMP_{e.upper()}" for s in SCHEMAS[:2] for e in ENTITIES[:5]]
    routines = [f"{s}.PRC_{e.upper()}" for s in SCHEMAS[:2] for e in ENTITIES[:6]]
    routines += [f"{s}.FN_{e.upper()}" for s in SCHEMAS[:2] for e in ENTITIES[6:10]]

    # Ядро часто переиспользуемых таблиц — создаёт реалистичное перекрытие.
    core = rnd.sample(all_tables, 8)
    # «Длинный хвост»: таблицы, к которым тянется ровно один отчёт.
    tail = [t for t in all_tables if t not in core]

    rows = []
    used_names: set[str] = set()
    for i in range(report_count):
        name = f"{rnd.choice(REPORT_PREFIX)} {rnd.choice(REPORT_SUBJECT)}"
        while name in used_names:
            name = f"{rnd.choice(REPORT_PREFIX)} {rnd.choice(REPORT_SUBJECT)} {rnd.randint(2, 99)}"
        used_names.add(name)

        picked = rnd.sample(core, rnd.randint(0, 3)) + rnd.sample(
            tail, rnd.randint(1, 6)
        )
        picked = list(dict.fromkeys(picked))

        # Мусор, который встречается в ручных выгрузках: скобки, лишние пробелы,
        # таблица без схемы, трёхсегментное имя.
        if i % 17 == 0 and picked:
            picked[0] = f"[{picked[0].split('.')[0]}].[{picked[0].split('.')[1]}]"
        if i % 23 == 0:
            picked.append("TempStaging")
        if i % 29 == 0 and picked:
            picked[-1] = f"ReportDW.{picked[-1]}"
        # Без схемы, но имя однозначно определяет схему по файлу размеров:
        # «STANDALONE_1» существует только в dbo — проверка восстановления.
        if i % 31 == 0:
            picked.append("STANDALONE_1")
        # Без схемы и неоднозначно: «Orders» есть во всех схемах сразу —
        # проверка, что при нескольких кандидатах схема не угадывается.
        if i % 37 == 0:
            picked.append("Orders")

        l1, l2, l3 = rnd.choice(FOLDER_LEVELS)
        execs = 0 if rnd.random() < 0.22 else int(rnd.lognormvariate(3.2, 1.5))
        plant_name = rnd.choice(PLANTS)
        rows.append(
            {
                "№": i + 1,
                "ТС": PLANT_NETWORK[plant_name],
                "Завод": plant_name,
                "Каталог 1-го уровня": l1,
                "Каталог 2-го уровня": l2,
                "Каталог 3-го уровня": l3,
                "Наименование отчета": name,
                "Используется view": rnd.choice(["да", "нет", "нет", "нет"]),
                "Таблицы источники данных": ";".join(picked),
                "View": ";".join(rnd.sample(views, rnd.randint(0, 2))),
                "Mat.view": ";".join(rnd.sample(matviews, rnd.randint(0, 1))),
                "Временные таблицы": ";".join(rnd.sample(temps, rnd.randint(0, 2))),
                "Функции/процедуры": ";".join(rnd.sample(routines, rnd.randint(0, 3))),
                "Ср. дл. (сек)": round(rnd.lognormvariate(1.6, 1.2), 1),
                "Кол-во обращений": execs,
            }
        )

    # «Сетевые» отчёты: одно и то же наименование заведено в разных ТС и на
    # нескольких заводах внутри одной сети. Без такого пересечения демо-данные
    # не показывают ни межсетевой разницы, ни внутрисетевых дублей — то есть
    # ровно того, для чего сделана страница уникальных отчётов: у заказчика
    # сети пересекаются по отчётам примерно на 70%.
    #
    # Часть копий чуть расходится по набору таблиц (одна таблица заменена) —
    # так проверяется порог сходства: полное совпадение и «почти то же самое»
    # должны вести себя одинаково, а расхождение вдвое — уже нет.
    shared_core = rnd.sample(all_tables, 20)
    for index, subject in enumerate(REPORT_SUBJECT):
        name = f"Сетевой {subject}"
        base_tables = rnd.sample(shared_core, 5)
        l1, l2, l3 = FOLDER_LEVELS[index % len(FOLDER_LEVELS)]
        # Последние два наименования есть не во всех сетях — иначе в каждой ТС
        # «своего» не остаётся вовсе и разница между сетями всегда нулевая.
        networks = NETWORKS if index < len(REPORT_SUBJECT) - 2 else NETWORKS[:1]
        for network_name in networks:
            plants = [p for p, net in PLANT_NETWORK.items() if net == network_name]
            for plant_index, plant_name in enumerate(plants):
                picked = list(base_tables)
                if (index + plant_index) % 3 == 0:
                    picked[-1] = rnd.choice([t for t in tail if t not in picked])
                rows.append(
                    {
                        "№": len(rows) + 1,
                        "ТС": network_name,
                        "Завод": plant_name,
                        "Каталог 1-го уровня": l1,
                        "Каталог 2-го уровня": l2,
                        "Каталог 3-го уровня": l3,
                        "Наименование отчета": name,
                        "Используется view": "нет",
                        "Таблицы источники данных": ";".join(picked),
                        "View": "",
                        "Mat.view": "",
                        "Временные таблицы": "",
                        "Функции/процедуры": "",
                        "Ср. дл. (сек)": round(rnd.lognormvariate(1.6, 1.2), 1),
                        "Кол-во обращений": int(rnd.lognormvariate(3.2, 1.5)),
                    }
                )

    # Пара проблемных строк — проверка отбраковки.
    n = len(rows)
    blank = {k: "" for k in rows[0]}
    rows.append({**blank, "№": n + 1, "Каталог 1-го уровня": "Финансы",
                 "Таблицы источники данных": "dbo.Orders"})
    rows.append({**blank, "№": n + 2, "Наименование отчета": "Report Without Sources",
                 "Каталог 1-го уровня": "Персонал", "Кол-во обращений": 0})

    reports_path = RAW_DIR / "sample_reports.xlsx"
    pd.DataFrame(rows).to_excel(reports_path, index=False)

    # Файл размеров повторяет выгрузку сегментов БД: строка на сегмент,
    # у части таблиц несколько секций, есть индексы и LOB-сегменты.
    # Размер ведётся по заводам: на каждом заводе таблица занимает своё место.
    # Часть таблиц намеренно не встречается ни в одном отчёте — список таблиц
    # самостоятелен и не зависит от отчётов.
    orphan_tables = [
        f"{s}.STANDALONE_{i}" for i, s in enumerate(SCHEMAS[:4], start=1)
    ]
    segments = []
    for t in all_tables + orphan_tables:
        # Часть таблиц намеренно без размера — проверка size_coverage_pct.
        if rnd.random() < 0.12:
            continue
        owner, table_name = t.split(".", 1)
        # Глубина хранения: чаще 30 и 45 дней, у части таблиц — заметно больше.
        retention = rnd.choice([30, 30, 30, 45, 45, 60, 90, 180, 365])
        # Таблица живёт не на всех заводах и весит на них по-разному.
        for plant_name in rnd.sample(PLANTS, rnd.randint(1, len(PLANTS))):
            network_name = PLANT_NETWORK[plant_name]
            scale = rnd.uniform(0.4, 2.5)
            parts = 1 if rnd.random() < 0.85 else rnd.randint(2, 4)
            for part in range(parts):
                segments.append(
                    {
                        "ТС": network_name,
                        "Завод": plant_name,
                        "OWNER": owner.upper(),
                        "SEGMENT_NAME": table_name.upper(),
                        "SEGMENT_TYPE": "TABLE" if parts == 1 else "TABLE PARTITION",
                        "SIZE_MB": round(rnd.lognormvariate(3.0, 1.6) * scale, 2),
                        "PERCENT_OF_TOTAL": None,
                        "PERCENT_OF_SCHEMA": None,
                        "Глубина хранения": retention,
                    }
                )
            # Индексный сегмент: имя своё, к таблице по выгрузке не привязывается.
            if rnd.random() < 0.5:
                segments.append(
                    {
                        "ТС": network_name,
                        "Завод": plant_name,
                        "OWNER": owner.upper(),
                        "SEGMENT_NAME": f"IDX_{table_name.upper()}",
                        "SEGMENT_TYPE": "INDEX",
                        "SIZE_MB": round(rnd.lognormvariate(2.0, 1.2), 2),
                        "PERCENT_OF_TOTAL": None,
                        "PERCENT_OF_SCHEMA": None,
                        "Глубина хранения": retention,
                    }
                )

    total = sum(s["SIZE_MB"] for s in segments)
    for i, seg in enumerate(segments, start=1):
        seg["№"] = i
        seg["PERCENT_OF_TOTAL"] = round(100.0 * seg["SIZE_MB"] / total, 4)
        seg["PERCENT_OF_SCHEMA"] = None
    order = ["№", "ТС", "Завод", "OWNER", "SEGMENT_NAME", "SEGMENT_TYPE", "SIZE_MB",
             "PERCENT_OF_TOTAL", "PERCENT_OF_SCHEMA", "Глубина хранения"]
    sizes_path = RAW_DIR / "sample_table_sizes.xlsx"
    pd.DataFrame(segments)[order].to_excel(sizes_path, index=False)

    # Отдельный файл статистики — единственный источник exec_count и
    # avg_duration_sec: основной файл эти колонки физически несёт (строки ниже
    # используют их как источник значений для этого файла), но загрузчик их не
    # читает. Здесь он покрывает часть отчётов — непокрытые остаются без
    # статистики, и это тоже часть проверки.
    usage = []
    for row in rows:
        if not row["Наименование отчета"]:
            continue
        if rnd.random() < 0.4:
            continue
        execs = row["Кол-во обращений"] or 0
        usage.append(
            {
                "Наименование отчета": row["Наименование отчета"],
                "ТС": row["ТС"],
                "Завод": row["Завод"],
                "Кол-во обращений": execs,
                "Users": max(0, int(execs * rnd.uniform(0.1, 0.5))),
                "Ср. дл. (сек)": row["Ср. дл. (сек)"],
                "Last Executed": "" if execs == 0 else "2026-07-20",
                "Period Start": "2026-01-01",
                "Period End": "2026-06-30",
            }
        )
    usage_path = RAW_DIR / "sample_report_usage.xlsx"
    pd.DataFrame(usage).to_excel(usage_path, index=False)

    # Текст SQL-запроса — тоже отдельный, необязательный файл. Структура
    # повторяет файл отчётов: №, ТС, Завод, три уровня каталога, наименование —
    # плюс запрос. Так его выгружает заказчик, и так проверяется точное
    # сопоставление: у отчёта-тёзки на соседнем заводе свой запрос, и строки не
    # должны перепутаться. Покрывает часть отчётов плюс одно заведомо
    # несуществующее имя — проверка sql_unmatched.
    sql_rows = []
    for index, row in enumerate(rows):
        name = row["Наименование отчета"]
        if not name or not row["Таблицы источники данных"]:
            continue
        if rnd.random() < 0.5:
            continue
        tables = row["Таблицы источники данных"].split(";")
        sql_rows.append(
            {
                "№": len(sql_rows) + 1,
                "ТС": row["ТС"],
                "Завод": row["Завод"],
                "Каталог 1-го уровня": row["Каталог 1-го уровня"],
                "Каталог 2-го уровня": row["Каталог 2-го уровня"],
                "Каталог 3-го уровня": row["Каталог 3-го уровня"],
                "Наименование отчета": name,
                # Запрос упоминает первую таблицу отчёта: на такой строке видно,
                # что поиск по тексту запроса на странице «Запросы к БД из SSRS»
                # действительно находит отчёты по имени таблицы.
                "Запрос к базе данных": (
                    f"SELECT *\nFROM {tables[0]}\nWHERE 1 = 1"
                ),
            }
        )
    sql_rows.append(
        {
            "№": len(sql_rows) + 1,
            "ТС": NETWORKS[0],
            "Завод": PLANTS[0],
            "Каталог 1-го уровня": "Финансы",
            "Каталог 2-го уровня": "",
            "Каталог 3-го уровня": "",
            "Наименование отчета": "Несуществующий отчёт",
            "Запрос к базе данных": "SELECT 1",
        }
    )
    sql_path = RAW_DIR / "sample_report_sql.xlsx"
    pd.DataFrame(sql_rows).to_excel(sql_path, index=False)

    return {
        "reports": reports_path, "sizes": sizes_path, "usage": usage_path,
        "sql": sql_path,
    }
