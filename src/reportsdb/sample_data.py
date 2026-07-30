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

NETWORKS = ["СЕТЬ-А", "СЕТЬ-Б", "СЕТЬ-В"]
PLANTS = ["Завод 1", "Завод 2", "Завод 3", "Завод 4"]

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

        l1, l2, l3 = rnd.choice(FOLDER_LEVELS)
        execs = 0 if rnd.random() < 0.22 else int(rnd.lognormvariate(3.2, 1.5))
        rows.append(
            {
                "№": i + 1,
                "ТС": rnd.choice(NETWORKS),
                "Завод": rnd.choice(PLANTS),
                "Каталог 1-го уровня": l1,
                "Каталог 2-го уровня": l2,
                "Каталог 3-го уровня": l3,
                "Наименование отчета": name,
                "Используется view": rnd.choice(["да", "нет", "нет", "нет"]),
                "Таблицы источники данных": ";".join(picked),
                "Ср. дл. (сек)": round(rnd.lognormvariate(1.6, 1.2), 1),
                "Кол-во обращений": execs,
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
    segments = []
    for t in all_tables:
        # Часть таблиц намеренно без размера — проверка size_coverage_pct.
        if rnd.random() < 0.12:
            continue
        owner, table_name = t.split(".", 1)
        parts = 1 if rnd.random() < 0.85 else rnd.randint(2, 4)
        for part in range(parts):
            size_mb = round(rnd.lognormvariate(3.0, 1.6), 2)
            segments.append(
                {
                    "OWNER": owner.upper(),
                    "SEGMENT_NAME": table_name.upper(),
                    "SEGMENT_TYPE": "TABLE" if parts == 1 else "TABLE PARTITION",
                    "SIZE_MB": size_mb,
                    "PERCENT_OF_TOTAL": None,
                    "PERCENT_OF_SCHEMA": None,
                }
            )
        # Индексный сегмент: имя своё, к таблице по выгрузке не привязывается.
        if rnd.random() < 0.5:
            segments.append(
                {
                    "OWNER": owner.upper(),
                    "SEGMENT_NAME": f"IDX_{table_name.upper()}",
                    "SEGMENT_TYPE": "INDEX",
                    "SIZE_MB": round(rnd.lognormvariate(2.0, 1.2), 2),
                    "PERCENT_OF_TOTAL": None,
                    "PERCENT_OF_SCHEMA": None,
                }
            )

    total = sum(s["SIZE_MB"] for s in segments)
    for i, seg in enumerate(segments, start=1):
        seg["№"] = i
        seg["PERCENT_OF_TOTAL"] = round(100.0 * seg["SIZE_MB"] / total, 4)
        seg["PERCENT_OF_SCHEMA"] = None
    order = ["№", "OWNER", "SEGMENT_NAME", "SEGMENT_TYPE", "SIZE_MB",
             "PERCENT_OF_TOTAL", "PERCENT_OF_SCHEMA"]
    sizes_path = RAW_DIR / "sample_table_sizes.xlsx"
    pd.DataFrame(segments)[order].to_excel(sizes_path, index=False)

    # Отдельный файл статистики: нужен, только если данных нет в основном
    # файле либо есть поля, которых там нет (пользователи, границы периода).
    # Здесь он покрывает часть отчётов — так проверяется перекрытие значений.
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

    return {"reports": reports_path, "sizes": sizes_path, "usage": usage_path}
