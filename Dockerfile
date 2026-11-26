FROM python:3.11-slim-bookworm

LABEL org.opencontainers.image.source="https://github.com/bunizao/douyin-auto-fire"
LABEL org.opencontainers.image.description="Douyin streak message sender"

ENV PATH="/app/.venv/bin:$PATH" \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HEADLESS=true \
    TZ=Asia/Shanghai

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:0.11.15 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./

RUN apt-get update \
    && apt-get install -y --no-install-recommends cron libatomic1 tzdata xauth \
    && uv sync --locked --no-dev \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY app ./app
COPY run.py ./
COPY scripts ./scripts
COPY deploy/persistent-browser/login.py deploy/persistent-browser/smoke.py ./deploy/persistent-browser/
COPY --chmod=755 docker/entrypoint.sh ./docker/entrypoint.sh

RUN mkdir -p /app/artifacts /data

ENTRYPOINT ["/app/docker/entrypoint.sh"]
