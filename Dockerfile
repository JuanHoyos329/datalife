# DataLife · imagen de producción (app web + ETL)
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY core ./core
COPY db ./db
COPY etl ./etl
COPY app ./app
COPY wsgi.py .

# Usuario sin privilegios
RUN useradd --create-home --uid 10001 datalife
USER datalife

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/salud' % os.environ.get('PORT','8000'))" || exit 1

# Render/Railway inyectan $PORT; en local es 8000
# Si WEB_CONCURRENCY llega vacía, gunicorn falla al arrancar: se normaliza aquí
CMD export WEB_CONCURRENCY="${WEB_CONCURRENCY:-2}" && exec gunicorn wsgi:app --bind 0.0.0.0:${PORT} --workers ${WEB_CONCURRENCY} --threads 4 --timeout 120 --access-logfile -
