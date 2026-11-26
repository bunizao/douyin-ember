#!/bin/sh
set -eu

CRON_SCHEDULE="${CRON_SCHEDULE:-30 0 * * *}"
RUN_ON_START="${RUN_ON_START:-false}"
# Cron starts jobs with an empty environment, so the container's variables are
# snapshotted here and sourced by the job. The timeout releases run.lock if a
# browser hangs, so the next day's run is not blocked.
ENV_FILE=/run/douyin-ember.env
RUN="cd /app && timeout -k 30 60m python run.py"

mkdir -p /app/artifacts /data
(umask 077 && export -p > "$ENV_FILE")

if [ "$RUN_ON_START" = "true" ]; then
  echo "[docker] running task once on container start"
  sh -c "$RUN" || true
fi

cat > /etc/cron.d/douyin-ember <<CRON
SHELL=/bin/sh
${CRON_SCHEDULE} root . ${ENV_FILE} && ${RUN} >> /proc/1/fd/1 2>> /proc/1/fd/2
CRON
chmod 0644 /etc/cron.d/douyin-ember

printf '[docker] cron schedule: %s\n' "$CRON_SCHEDULE"
printf '[docker] timezone: %s\n' "${TZ:-Asia/Shanghai}"

exec cron -f
