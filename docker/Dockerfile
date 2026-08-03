FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONPATH=/app/src

COPY pyproject.toml ./
RUN pip install --no-cache-dir \
        "duckdb>=1.0" "pandas>=2.0" "openpyxl>=3.1" "xlrd>=2.0" \
        "pyyaml>=6.0" "streamlit>=1.36" "plotly>=5.20"

COPY src/ ./src/
COPY app/ ./app/
COPY sql/ ./sql/
COPY config/ ./config/
# Streamlit читает .streamlit/config.toml из рабочего каталога. Без него в
# контейнере теряются три вещи сразу: тёмная тема, совпадение backgroundColor
# с SURFACE (иначе вокруг сегментов диаграмм видна кайма) и отключённая
# отправка статистики использования.
COPY .streamlit/ ./.streamlit/
# Собранная БД кладётся в образ, чтобы коллеге хватило одной команды.
# Если её нет — приложение подскажет, как собрать.
COPY data/ ./data/

EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "app/Home.py", \
     "--server.address=0.0.0.0", "--server.port=8501", "--server.headless=true"]
