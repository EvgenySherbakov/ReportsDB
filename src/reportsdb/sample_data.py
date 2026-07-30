"""Синтетические данные — позволяют проверить весь конвейер до появления
реального Excel-файла. Структура файлов повторяет ожидаемую от заказчика.
"""

from __future__ import annotations

import random
from pathlib import Path

import pandas as pd

from .config import RAW_DIR

FOLDERS = [
    "/Finance/Monthly",
    "/Finance/Daily",
    "/Sales/Regional",
    "/Sales/Executive",
    "/Operations/Warehouse",
    "/Operations/Logistics",
    "/HR",
]

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

        rows.append(
            {
                "№": i + 1,
                "Наименование отчета": name,
                "Каталог": rnd.choice(FOLDERS),
                "Таблицы источники данных": ";".join(picked),
                "Owner": rnd.choice(["a.ivanov", "s.petrova", "d.kuznetsov", ""]),
            }
        )

    # Пара проблемных строк — проверка отбраковки.
    n = len(rows)
    rows.append({"№": n + 1, "Наименование отчета": "", "Каталог": "/Finance",
                 "Таблицы источники данных": "dbo.Orders", "Owner": ""})
    rows.append({"№": n + 2, "Наименование отчета": "Report Without Sources",
                 "Каталог": "/HR", "Таблицы источники данных": "", "Owner": ""})

    reports_path = RAW_DIR / "sample_reports.xlsx"
    pd.DataFrame(rows).to_excel(reports_path, index=False)

    sizes = []
    for t in all_tables:
        # Часть таблиц намеренно без размера — проверка size_coverage_pct.
        if rnd.random() < 0.12:
            continue
        data_mb = round(rnd.lognormvariate(3.0, 1.6), 2)
        sizes.append(
            {
                "Table": t,
                "Rows": int(data_mb * rnd.randint(800, 4000)),
                "Data MB": data_mb,
                "Index MB": round(data_mb * rnd.uniform(0.1, 0.6), 2),
                "Measured At": "2026-07-01",
            }
        )
    sizes_path = RAW_DIR / "sample_table_sizes.xlsx"
    pd.DataFrame(sizes).to_excel(sizes_path, index=False)

    usage = []
    for row in rows:
        if not row["Наименование отчета"]:
            continue
        if rnd.random() < 0.15:  # часть отчётов вовсе без статистики
            continue
        execs = 0 if rnd.random() < 0.25 else int(rnd.lognormvariate(3.2, 1.5))
        usage.append(
            {
                "Report Name": row["Наименование отчета"],
                "Path": row["Каталог"],
                "Executions": execs,
                "Users": max(0, min(execs, int(execs * rnd.uniform(0.1, 0.5)))),
                "Avg Duration Ms": round(rnd.lognormvariate(7.5, 1.0), 0),
                "Last Executed": "" if execs == 0 else "2026-07-20",
                "Period Start": "2026-01-01",
                "Period End": "2026-06-30",
            }
        )
    usage_path = RAW_DIR / "sample_report_usage.xlsx"
    pd.DataFrame(usage).to_excel(usage_path, index=False)

    return {"reports": reports_path, "sizes": sizes_path, "usage": usage_path}
