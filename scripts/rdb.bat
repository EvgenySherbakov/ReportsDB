@echo off
REM Запуск команд reportsdb под Windows: scripts\rdb.bat <команда> [аргументы]
REM
REM Нужен потому, что run.bat ставит пакет в виртуальное окружение проекта,
REM а `python -m reportsdb` из обычной консоли его не видит и падает с
REM «No module named reportsdb». Этот скрипт запускает команду тем же
REM Python, что и приложение.
REM
REM Примеры:
REM   scripts\rdb.bat diagnose
REM   scripts\rdb.bat build data\raw\reports.xlsx
REM   scripts\rdb.bat export-html
cd /d "%~dp0.."

if "%~1"=="" (
  echo Укажите команду. Например:
  echo   scripts\rdb.bat diagnose
  echo   scripts\rdb.bat build data\raw\reports.xlsx
  echo   scripts\rdb.bat export-html
  exit /b 2
)

where uv >nul 2>nul
if errorlevel 1 (
  python -m pip install -q -e .
  python -m reportsdb %*
  exit /b %errorlevel%
)

uv venv --allow-existing >nul 2>nul
uv pip install -q -e .
uv run python -m reportsdb %*
exit /b %errorlevel%
