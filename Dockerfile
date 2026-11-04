# ── Vantab ────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so code changes don't invalidate the layer.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY bot.py ./
COPY vantab ./vantab
COPY prompts ./prompts
COPY docs ./docs

# The database lives on a volume so cases survive redeploys.
RUN mkdir -p /app/data && useradd --create-home --uid 10001 vantab \
    && chown -R vantab:vantab /app
USER vantab

VOLUME ["/app/data"]

# Simple liveness check: the process must still exist.
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os,sys; sys.exit(0 if os.path.exists('/proc/1') else 1)"

CMD ["python", "bot.py"]
