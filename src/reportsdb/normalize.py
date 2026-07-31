"""Нормализация значений из Excel. См. docs/TZ.md, раздел 7."""

from __future__ import annotations

import fnmatch
import re
from typing import Any

from .config import UNKNOWN_SCHEMA

_MULTI_SLASH = re.compile(r"/{2,}")
_BRACKETS = re.compile(r"[\[\]\"`]")


def clean_text(value: Any) -> str | None:
    """Приводит ячейку к строке или None. Пустые и NaN → None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "null", "#n/a"}:
        return None
    return text


def normalise_catalog_path(value: Any) -> str:
    """`\\Finance\\Monthly\\` → `/Finance/Monthly`. Пустое → `/`."""
    text = clean_text(value)
    if text is None:
        return "/"
    text = text.replace("\\", "/")
    text = _MULTI_SLASH.sub("/", text)
    if not text.startswith("/"):
        text = "/" + text
    if len(text) > 1:
        text = text.rstrip("/")
    return text or "/"


def split_folders(catalog_path: str) -> tuple[str | None, str | None, str | None, int]:
    """Первые три уровня папок и глубина вложенности."""
    parts = [p for p in catalog_path.split("/") if p]
    levels: list[str | None] = [parts[i] if i < len(parts) else None for i in range(3)]
    return levels[0], levels[1], levels[2], len(parts)


def join_folders(*levels: Any) -> str:
    """Собирает путь из отдельных колонок уровней каталога.

    Пустые уровни пропускаются: («Продажи», None, «Месяц») → «/Продажи/Месяц».
    """
    parts = [clean_text(level) for level in levels]
    filled = [p.replace("/", "-").strip() for p in parts if p]
    return "/" + "/".join(filled) if filled else "/"


_TRUE = {"да", "yes", "y", "true", "1", "+", "истина", "есть", "v", "х", "x"}
_FALSE = {"нет", "no", "n", "false", "0", "-", "ложь", "отсутствует", "—"}


def parse_bool(value: Any) -> bool | None:
    """Признак «да/нет» из ячейки. Непонятное значение → None, а не False.

    Разница принципиальна: «неизвестно» и «нет» ведут к разным выводам.
    """
    text = clean_text(value)
    if text is None:
        return None
    key = text.strip().lower().replace("ё", "е")
    if key in _TRUE:
        return True
    if key in _FALSE:
        return False
    return None


def parse_table_ref(raw: Any) -> tuple[str, str, bool] | None:
    """`[dbo].[Orders]` → (`dbo`, `Orders`, True).

    Возвращает (схема, таблица, разобрано_успешно) либо None, если элемент пуст.
    Без точки — схема неизвестна. Больше двух сегментов (`db.schema.table`) —
    берутся два последних.
    """
    text = clean_text(raw)
    if text is None:
        return None
    text = _BRACKETS.sub("", text).strip().strip(".")
    if not text:
        return None

    parts = [p.strip() for p in text.split(".") if p.strip()]
    if not parts:
        return None
    if len(parts) == 1:
        return UNKNOWN_SCHEMA, parts[0], False
    return parts[-2], parts[-1], True


def parse_table_list(raw: Any, separator: str = ";") -> list[tuple[str, str, bool]]:
    """Разбирает ячейку со списком таблиц. Дубликаты схлопываются."""
    text = clean_text(raw)
    if text is None:
        return []

    seen: dict[str, tuple[str, str, bool]] = {}
    for chunk in text.split(separator):
        parsed = parse_table_ref(chunk)
        if parsed is None:
            continue
        key = f"{parsed[0]}.{parsed[1]}".lower()
        seen.setdefault(key, parsed)
    return list(seen.values())


def match_object_kind(
    table_name: str, patterns: dict[str, list[str]]
) -> str | None:
    """Тип объекта по маске имени: {"VIEW": ["V_*"]} → "VIEW".

    Маски задаются в config/mapping.yml и по умолчанию пусты — тип берётся из
    явных колонок файла. Возвращает None, если ни одна маска не подошла.
    """
    name = table_name.strip().upper()
    for kind, masks in patterns.items():
        for mask in masks:
            if fnmatch.fnmatch(name, mask.strip().upper()):
                return kind
    return None


def full_name_key(schema_name: str, table_name: str) -> str:
    """Ключ сопоставления таблиц — всегда в нижнем регистре."""
    return f"{schema_name}.{table_name}".lower()


def to_number(value: Any) -> float | None:
    """Число из ячейки; терпит пробелы-разделители разрядов и запятую."""
    text = clean_text(value)
    if text is None:
        return None
    text = text.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None
