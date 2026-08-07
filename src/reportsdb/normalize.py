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


# Буквы, которые на экране выглядят одинаково, а в файле записаны разными
# алфавитами. Расхождение такого рода нельзя заметить глазом: «ТСХ» с
# кириллической Х и «ТСX» с латинской X — это две разные строки и одна и та же
# надпись. Ключ приводится к нижнему регистру ДО замены, поэтому в таблице
# только строчные буквы: заглавные пары («В» и «B», «Н» и «H») сводятся к ним
# сами. Цифр в таблице нет намеренно: «З» вместо «3» — тоже опечатка, но её
# лучше назвать, чем молча исправить.
_LOOKALIKE = str.maketrans(
    {
        "а": "a", "в": "b", "е": "e", "ё": "e", "к": "k", "м": "m", "н": "h",
        "о": "o", "р": "p", "с": "c", "т": "t", "у": "y", "х": "x",
        "і": "i", "ј": "j", "ѕ": "s",
    }
)
# Тире и дефисы, которые Excel подставляет автозаменой.
_DASHES = str.maketrans({"–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-"})
_INVISIBLE = str.maketrans({" ": " ", " ": " ", "​": None, "﻿": None})
_SPACES = re.compile(r"\s+")


def loose_key(value: Any) -> str:
    """Ключ сопоставления «как выглядит», а не «как записано».

    Нужен там, где строку из вспомогательного файла ищут в каталоге отчётов:
    файлы выгружают разные люди и разные системы, и одно и то же название
    приезжает то с неразрывным пробелом, то с латинской буквой в середине
    русского слова, то в другом регистре. Точное сравнение такие строки
    разводит, а человек, который смотрит в оба файла, видит одну и ту же
    надпись и не понимает, почему данные не сошлись.

    Ключ ГРУБЫЙ и применяется только после того, как точное сравнение не дало
    результата: он не заменяет точный ключ, а ловит то, что точный ключ
    пропустил.
    """
    text = clean_text(value)
    if text is None:
        return ""
    text = text.translate(_INVISIBLE).translate(_DASHES)
    text = _SPACES.sub(" ", text).strip().casefold()
    return text.translate(_LOOKALIKE)


def _alphabet(char: str) -> str:
    if "Ѐ" <= char <= "ӿ":
        return "кириллическая"
    if char.isascii() and char.isalpha():
        return "латинская"
    if char == " ":
        return "неразрывный пробел"
    return "символ"


def spell_out_difference(in_file: str, in_catalog: str) -> str:
    """Называет посимвольно, чем отличаются две одинаковые с виду строки.

    Сказать «ТСX не совпадает с ТСХ» бесполезно — на экране это одна и та же
    надпись, и заказчик честно не увидит разницы. Поэтому разница называется
    номером позиции и кодом символа: по такой подсказке опечатку можно найти
    поиском в самом файле.
    """
    parts: list[str] = []
    for pos, (a, b) in enumerate(zip(in_file, in_catalog), start=1):
        if a == b:
            continue
        parts.append(
            f"позиция {pos}: в файле {_alphabet(a)} «{a}» (U+{ord(a):04X}), "
            f"в каталоге {_alphabet(b)} «{b}» (U+{ord(b):04X})"
        )
    if len(in_file) != len(in_catalog):
        parts.append(f"длина: {len(in_file)} против {len(in_catalog)}")
    return "; ".join(parts) or "различаются регистром или пробелами"


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
