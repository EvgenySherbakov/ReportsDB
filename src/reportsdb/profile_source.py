"""Профилирование исходного файла перед заполнением config/mapping.yml.

Печатает листы, заголовки, долю пустых и примеры значений, а также
предварительное сопоставление с полями модели. Ничего не изменяет.
"""

from __future__ import annotations

from pathlib import Path

from .config import SectionConfig, load_mapping, resolve_columns
from .excel import list_sheets, read_sheet
from .normalize import clean_text


def profile(
    path: Path,
    sheet: str | None = None,
    header_row: int = 0,
    config: Path | None = None,
) -> None:
    print(f"Файл: {path}")
    sheets = list_sheets(path)
    print(f"Листы: {', '.join(sheets)}\n")

    section = SectionConfig(sheet=sheet if sheet is not None else None, header_row=header_row)
    df = read_sheet(path, section)
    used_sheet = sheet if sheet is not None else sheets[0]
    print(f"Лист «{used_sheet}»: {len(df)} строк, {len(df.columns)} колонок")
    print(f"Строка заголовков: {header_row}\n")

    for col in df.columns:
        values = [clean_text(v) for v in df[col].tolist()]
        filled = [v for v in values if v is not None]
        pct = 100.0 * len(filled) / len(values) if values else 0.0
        samples = []
        for v in filled[:3]:
            samples.append(v if len(v) <= 70 else v[:67] + "…")
        print(f"  · {col!r}")
        print(f"      заполнено: {pct:.0f}%  уникальных: {len(set(filled))}")
        for s in samples:
            print(f"      пример: {s}")

    mapping = load_mapping(config)
    resolved = resolve_columns(list(df.columns), mapping.reports.columns)
    print("\nПредварительное сопоставление по config/mapping.yml:")
    for field_name, col in resolved.items():
        status = f"→ {col!r}" if col else "→ НЕ НАЙДЕНО"
        print(f"  {field_name:<16} {status}")

    missing = [f for f, c in resolved.items() if c is None]
    if "report_name" in missing:
        print(
            "\n!! report_name не найден — загрузка невозможна. "
            "Добавьте фактический заголовок в config/mapping.yml."
        )
    elif missing:
        print(
            "\n!  Не сопоставлены (не критично, если этих данных нет): "
            + ", ".join(missing)
        )
