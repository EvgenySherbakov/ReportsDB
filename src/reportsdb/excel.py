"""Чтение Excel/CSV. Всё читается как текст — см. docs/TZ.md, раздел 7.6."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .config import SectionConfig


def read_sheet(path: Path, section: SectionConfig) -> pd.DataFrame:
    """Читает лист как строки, без приведения типов и без интерпретации NA."""
    suffix = path.suffix.lower()
    if suffix in {".csv", ".tsv", ".txt"}:
        sep = "\t" if suffix == ".tsv" else None
        return pd.read_csv(
            path,
            sep=sep,
            engine="python",
            dtype=str,
            keep_default_na=False,
            skiprows=section.header_row or 0,
        )

    sheet = section.sheet if section.sheet is not None else 0
    return pd.read_excel(
        path,
        sheet_name=sheet,
        dtype=str,
        keep_default_na=False,
        header=section.header_row or 0,
    )


def read_all(paths: list[Path], section: SectionConfig) -> pd.DataFrame:
    """Читает все файлы секции в одну таблицу.

    Файлы приходят по одному на завод, а завод указан колонками ТС и Завод
    внутри файла, поэтому склейка безопасна: строки разных заводов остаются
    разными строками. Заголовки у файлов могут слегка различаться (у кого-то
    нет необязательной колонки) — недостающие колонки становятся пустыми,
    а не роняют загрузку.
    """
    frames = [read_sheet(path, section) for path in paths]
    if not frames:
        return pd.DataFrame()
    if len(frames) == 1:
        return frames[0]
    return pd.concat(frames, ignore_index=True, sort=False)


def list_sheets(path: Path) -> list[str]:
    if path.suffix.lower() in {".csv", ".tsv", ".txt"}:
        return ["(csv)"]
    return pd.ExcelFile(path).sheet_names
