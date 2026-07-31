#!/usr/bin/env bash
# Запуск команд reportsdb: scripts/rdb.sh <команда> [аргументы]
#
# Нужен потому, что run.sh ставит пакет в виртуальное окружение проекта,
# а `python -m reportsdb` из обычной консоли его не видит и падает с
# «No module named reportsdb». Этот скрипт запускает команду тем же
# Python, что и приложение.
#
# Примеры:
#   scripts/rdb.sh diagnose
#   scripts/rdb.sh build data/raw/reports.xlsx
#   scripts/rdb.sh export-html
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -eq 0 ]; then
  echo "Укажите команду. Например:"
  echo "  scripts/rdb.sh diagnose"
  echo "  scripts/rdb.sh build data/raw/reports.xlsx"
  echo "  scripts/rdb.sh export-html"
  exit 2
fi

if ! command -v uv >/dev/null 2>&1; then
  python3 -m pip install --quiet -e .
  exec python3 -m reportsdb "$@"
fi

uv venv --allow-existing >/dev/null 2>&1
uv pip install --quiet -e .
exec uv run python -m reportsdb "$@"
