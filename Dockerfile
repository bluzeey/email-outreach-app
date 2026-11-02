FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=production APP_LOGIN_REQUIRED=true \
    DATABASE_URL=sqlite:////data/outreach.db \
    CHECKPOINT_DATABASE_URL=sqlite:////data/checkpoints.db \
    UPLOAD_DIR=/data/uploads DATA_DIR=/data \
    DRY_RUN_DEFAULT=true EMAIL_SENDING_ENABLED=false GMAIL_CONNECTION_ENABLED=false

WORKDIR /app
COPY pyproject.toml README.md requirements-runtime.txt ./
RUN pip install --no-cache-dir -r requirements-runtime.txt
COPY app ./app
RUN pip install --no-cache-dir --no-deps . \
    && groupadd --gid 10001 outreach \
    && useradd --uid 10001 --gid 10001 --no-create-home outreach \
    && install -d -o 10001 -g 10001 -m 0700 /data /data/uploads
USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=20s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/ready',timeout=3)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-server-header"]
