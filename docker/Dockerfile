FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    KURO_SKIP_CREATE_ALL=1

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt gunicorn==23.0.0

RUN useradd -m -u 10001 kuro \
    && mkdir -p /home/kuro/.kurowear/certs \
    && chown -R kuro:kuro /home/kuro/.kurowear

COPY --chown=kuro:kuro . .

RUN mkdir -p /app/app/static/uploads /app/logs \
    && chown -R kuro:kuro /app/app/static/uploads /app/logs

USER kuro

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "2", "--timeout", "120", "wsgi:app"]