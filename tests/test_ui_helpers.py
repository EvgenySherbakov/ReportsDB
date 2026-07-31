"""Проверки помощников интерфейса.

Отдельный файл, потому что здесь импортируется код страниц (`app/_shared.py`),
а не ETL. Тянуть Streamlit в тесты целостности незачем.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "app"))
sys.path.insert(0, str(ROOT / "src"))

from _shared import is_blank, num  # noqa: E402


# Все виды «пусто», которые приходят из DuckDB через pandas. pd.NA появляется
# у nullable-типов: целочисленная колонка с NULL приезжает как Int64, а не
# как float с NaN.
EMPTY = [None, float("nan"), pd.NA, pd.NaT]


@pytest.mark.parametrize("value", EMPTY)
def test_num_shows_dash_for_every_kind_of_empty(value):
    assert num(value) == "—"


@pytest.mark.parametrize("value", EMPTY)
def test_is_blank_recognises_every_kind_of_empty(value):
    assert is_blank(value) is True


def test_is_blank_does_not_treat_zero_as_empty():
    """Ноль — это значение, а не отсутствие данных."""
    assert is_blank(0) is False
    assert is_blank(0.0) is False
    assert num(0) == "0"


def test_num_formats_numbers_readably():
    assert num(864) == "864"
    assert num(109670.0, decimals=1) == "109 670.0"
    assert num(27.04, decimals=1) == "27.0"
    assert num(12.5, suffix=" МБ", decimals=1) == "12.5 МБ"


def test_na_self_comparison_is_the_trap_this_helper_avoids():
    """Фиксируем причину сбоя, чтобы приём не вернулся в код.

    Проверка «значение не равно самому себе» ловит float('nan'), но на pd.NA
    возвращает не False, а сам pd.NA — и падает при попытке считать его
    истинность. Именно так карточка отчёта роняла приложение.
    """
    assert (float("nan") != float("nan")) is True
    with pytest.raises(TypeError, match="ambiguous"):
        bool(pd.NA != pd.NA)
