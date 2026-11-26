# Persistent browser profile

This deployment uses Docker Compose and a host systemd timer. It keeps the
browser profile, task configuration, and send history on the host. Local login
and dry-run checks should use the same profile as the timer.

Copy the deployment files into `/opt/douyin-auto-fire` and copy `.env.example`
to `.env`. Set `DOUYIN_IMAGE` to a verified image tag or digest:

    COMPOSE_FILE=compose.yaml:compose.profile.yaml
    DOUYIN_IMAGE=ghcr.io/bunizao/douyin-auto-fire:latest

Keep config.json, .env, and the browser profile private. Compose also expects
cookie.json to exist. An empty JSON array is enough in profile mode.

With the timer stopped, run `docker compose pull` and open the profile login helper:

    docker compose run --rm --no-deps --entrypoint xvfb-run douyin-auto-fire \
      --auth-file=/tmp/douyin.xauth --server-num=99 \
      "--server-args=-screen 0 1600x1200x24" python /app/deploy/persistent-browser/login.py

Scan the QR code from artifacts/server-login.png and complete any prompts.
Press Enter after completing login. Use r to refresh an expired QR code.

With a valid profile, run these commands from the deployment directory:

    sudo python3 scheduled-run.py --dry-run
    sudo python3 backup-image.py

Install the service and timer units into /etc/systemd/system, reload systemd,
and enable douyin-auto-fire.timer. The timer starts a one-shot container daily
at 08:00 Asia/Shanghai. The scheduler bounds its run time, keeps dated receipts
in artifacts, and restores a pruned image from the verified local backup.

To update, stop the timer and wait for the service to exit. Pull the new image,
set DOUYIN_IMAGE to the tested tag or digest, repeat the dry run and backup,
then restart the timer. Keep the old image, backup, profile, and send history
available for rollback.
