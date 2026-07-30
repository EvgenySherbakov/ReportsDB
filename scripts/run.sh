#!/usr/bin/env bash
# Запуск приложения локально. Требуется Python 3.11+.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v uv >/dev/null 2>&1; then
  echo "uv не найден. Ставлю зависимости через pip..."
  python3 -m pip install --quiet -e .
  exec python3 -m streamlit run app/Home.py
fi

uv venv --allow-existing
uv pip install --quiet -e .

if [ ! -f data/reports.duckdb ]; then
  echo "База не найдена. Генерирую демо-данные..."
  uv run python -m reportsdb sample
  uv run python -m reportsdb build --config config/mapping.sample.yml data/raw/sample_reports.xlsx
fi

uv run streamlit run app/Home.py
