FROM ghcr.io/unmev/douyin-auto-fire@sha256:c7dc37f8e1eaec82c281b699157fde80b7824793997071b03b1d20cc355332e9

ARG SOURCE_REVISION
LABEL org.opencontainers.image.revision="${SOURCE_REVISION}"

RUN apt-get update \
    && apt-get install -y --no-install-recommends xauth \
    && rm -rf /var/lib/apt/lists/*

COPY app/models.py app/config.py app/browser.py app/main.py app/history.py app/douyin.py /app/app/
COPY deploy/persistent-browser/login.py /app/deploy/persistent-browser/login.py
