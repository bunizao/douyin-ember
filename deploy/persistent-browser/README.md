# Persistent browser profile

This deployment uses Docker Compose and a host systemd timer. It keeps the
browser profile, task configuration, and send history on the host. Local login
and dry-run checks should use the same profile as the timer.

Copy the deployment files into `/opt/douyin-ember` and copy `.env.example`
to `.env`. Set `DOUYIN_IMAGE` to a verified image tag or digest:

    COMPOSE_FILE=compose.yaml:compose.profile.yaml
    DOUYIN_IMAGE=ghcr.io/bunizao/douyin-ember:latest

Keep config.json, .env, and the browser profile private. Compose also expects
cookie.json to exist. An empty JSON array is enough in profile mode.

With the timer stopped, run `docker compose pull` and open the profile login helper:

    docker compose run --rm --no-deps --entrypoint xvfb-run douyin-ember \
      --auth-file=/tmp/douyin.xauth --server-num=99 \
      "--server-args=-screen 0 1600x1200x24" python /app/deploy/persistent-browser/login.py

Scan the QR code from artifacts/server-login.png and complete any prompts.
Press Enter after completing login. Use r to refresh an expired QR code.

With a valid profile, run these commands from the deployment directory:

    sudo python3 scheduled-run.py --dry-run
    sudo python3 backup-image.py

Install the service and timer units into /etc/systemd/system, reload systemd,
and enable douyin-ember.timer. The timer starts a one-shot container daily
at 08:00 Asia/Shanghai. The scheduler bounds its run time, keeps dated receipts
in artifacts, and restores a pruned image from the verified local backup.

Updates are automatic: also install `douyin-ember-update.service` and
`douyin-ember-update.timer` and enable the timer. Every day at 06:00 it pulls
`ghcr.io/bunizao/douyin-ember:latest`. When the commit changed, it pins
DOUYIN_IMAGE to that commit's `sha-` tag and runs a dry run. If at least one
friend was reached, it keeps the new image, refreshes the backup and deletes
the older images; otherwise it restores the previous DOUYIN_IMAGE. Run
`sudo python3 update-image.py` to update right away, and
`journalctl -u douyin-ember-update` to see what it decided.

To roll back by hand, set DOUYIN_IMAGE to an older `sha-` tag, pull it, run a
dry run and `backup-image.py`. Old tags stay available on GHCR.
