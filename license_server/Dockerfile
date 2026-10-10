FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY license_server/requirements.txt /app/license_server/requirements.txt
RUN pip install --no-cache-dir -r /app/license_server/requirements.txt && useradd --uid 10001 --create-home license
COPY license_protocol.py /app/license_protocol.py
COPY license_server/*.py /app/license_server/
COPY license_server/templates /app/license_server/templates
COPY license_server/static /app/license_server/static
USER 10001:10001
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "license_server.app:production_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log", "--limit-concurrency", "100", "--timeout-keep-alive", "5"]
