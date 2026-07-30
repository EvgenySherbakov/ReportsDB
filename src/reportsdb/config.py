"""Пути проекта и загрузка config/mapping.yml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

VERSION = "0.2.0"

# Версия структуры БД. Поднимать при КАЖДОМ изменении sql/01_schema.sql или
# sql/02_views.sql. Приложение сравнивает её с версией в файле базы и, если та
# старее, просит перезагрузить данные вместо падения с ошибкой SQL.
SCHEMA_VERSION = 3

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config" / "mapping.yml"
SQL_DIR = ROOT / "sql"
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
DB_PATH = DATA_DIR / "reports.duckdb"
DIST_DIR = ROOT / "dist"

UNKNOWN_SCHEMA = "(unknown)"


@dataclass
class SectionConfig:
    """Настройки чтения одного листа Excel."""

    file: str | None = None
    sheet: str | int | None = None
    header_row: int = 0
    columns: dict[str, list[str]] = field(default_factory=dict)
    table_separator: str = ";"
    # Типы сегментов, которые считаются объёмом таблицы (см. mapping.yml).
    segment_types: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "SectionConfig":
        raw = raw or {}
        columns = {
            key: [str(v) for v in (value or [])]
            for key, value in (raw.get("columns") or {}).items()
        }
        return cls(
            file=raw.get("file"),
            sheet=raw.get("sheet"),
            header_row=int(raw.get("header_row") or 0),
            columns=columns,
            table_separator=raw.get("table_separator") or ";",
            segment_types=[str(v) for v in (raw.get("segment_types") or [])],
        )


@dataclass
class Mapping:
    reports: SectionConfig
    table_sizes: SectionConfig
    report_usage: SectionConfig


def load_mapping(path: Path | None = None) -> Mapping:
    path = path or CONFIG_PATH
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}
    return Mapping(
        reports=SectionConfig.from_dict(raw.get("reports")),
        table_sizes=SectionConfig.from_dict(raw.get("table_sizes")),
        report_usage=SectionConfig.from_dict(raw.get("report_usage")),
    )


def normalise_header(value: Any) -> str:
    """Ключ для сопоставления заголовков: без регистра и краевых пробелов."""
    return str(value).strip().lower().replace("ё", "е")


def resolve_columns(
    available: list[str], wanted: dict[str, list[str]]
) -> dict[str, str | None]:
    """Сопоставляет поля модели с реальными заголовками файла.

    Возвращает {поле: фактический заголовок или None}.
    """
    index = {normalise_header(col): col for col in available}
    resolved: dict[str, str | None] = {}
    for field_name, candidates in wanted.items():
        match = None
        for candidate in candidates:
            match = index.get(normalise_header(candidate))
            if match is not None:
                break
        resolved[field_name] = match
    return resolved
