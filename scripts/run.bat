@echo off
REM Запуск приложения локально под Windows. Требуется Python 3.11+.
cd /d "%~dp0.."

where uv >nul 2>nul
if errorlevel 1 (
  echo uv не найден, ставлю зависимости через pip...
  python -m pip install -q -e .
  python -m streamlit run app/Home.py
  goto :eof
)

uv venv --allow-existing
uv pip install -q -e .

if not exist data\reports.duckdb (
  echo База не найдена. Генерирую демо-данные...
  uv run python -m reportsdb sample
  uv run python -m reportsdb build --config config/mapping.sample.yml data/raw/sample_reports.xlsx
)

uv run streamlit run app/Home.py
