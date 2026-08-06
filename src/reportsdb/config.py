"""Пути проекта и загрузка config/mapping.yml."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# Версия программы. Пишется в etl_run.tool_version, поэтому по любой базе
# видно, каким кодом она собрана. Должна совпадать с version в pyproject.toml —
# за этим следит test_version_is_the_same_everywhere.
VERSION = "1.5.0"

# Версия структуры БД. Поднимать при КАЖДОМ изменении sql/01_schema.sql или
# sql/02_views.sql. Приложение сравнивает её с версией в файле базы и, если та
# старее, просит перезагрузить данные вместо падения с ошибкой SQL.
SCHEMA_VERSION = 10

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
    """Настройки чтения одного листа Excel.

    Файлов может быть несколько: данные приходят по файлу на завод, и все они
    грузятся за один проход. Завод берётся из колонок ТС и Завод внутри файла,
    а не из того, какой это файл, — иначе размеры разных заводов молча
    сложились бы в один.
    """

    files: list[str] = field(default_factory=list)
    sheet: str | int | None = None
    header_row: int = 0
    columns: dict[str, list[str]] = field(default_factory=dict)
    table_separator: str = ";"
    # Маски имён для распознавания типа объекта: {"VIEW": ["V_*"], ...}.
    # По умолчанию пусто — тип берётся только из явных колонок файла.
    object_patterns: dict[str, list[str]] = field(default_factory=dict)
    # Типы сегментов, которые считаются объёмом таблицы (см. mapping.yml).
    segment_types: list[str] = field(default_factory=list)

    @property
    def file(self) -> str | None:
        """Первый файл секции — для кода, которому нужен ровно один."""
        return self.files[0] if self.files else None

    @file.setter
    def file(self, value: str | None) -> None:
        self.files = [value] if value else []

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> "SectionConfig":
        raw = raw or {}
        columns = {
            key: [str(v) for v in (value or [])]
            for key, value in (raw.get("columns") or {}).items()
        }
        # В конфиге допустимы оба вида: `file: одинфайл.xlsx` и
        # `files: [первый.xlsx, второй.xlsx]`. Первый оставлен, чтобы старые
        # конфиги продолжали работать без правок.
        one = raw.get("file")
        many = raw.get("files") or ([one] if one else [])
        return cls(
            files=[str(f) for f in many if f],
            sheet=raw.get("sheet"),
            header_row=int(raw.get("header_row") or 0),
            columns=columns,
            table_separator=raw.get("table_separator") or ";",
            object_patterns={
                str(kind): [str(v) for v in (masks or [])]
                for kind, masks in (raw.get("object_patterns") or {}).items()
            },
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
