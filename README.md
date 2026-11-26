# Douyin Auto Fire

A self-hosted sender that keeps Douyin chat streaks active.

## What this fork fixes

- **A missing chat no longer stops the queue.** The sender records the failure and continues with the next recipient. Login and security verification errors still stop the run.
- **Stable recipient matching.** Set a Douyin conversation ID to keep sending to the same chat after a nickname changes. The sender checks the open chat before sending.
- **Safer retries.** Send history covers successful and uncertain sends. The host scheduler prevents overlapping jobs, bounds run time, and can restore a pruned image from a verified backup.
- **Persistent browser login.** The host can reuse its own authenticated browser profile across runs.

## Run with uv

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```sh
uv sync --locked
uv run --locked playwright install chromium
cp .env.example .env
cp config.example.json config.json
```

Fill in the task and set `DOUYIN_COOKIE` or `DOUYIN_STORAGE_STATE` in `.env`. Keep credentials and recipient configuration private. A bound entry in `friends` looks like this:

```json
{"name": "Local label", "conversation_id": "0:1:123:456"}
```

```sh
uv run --locked run.py --dry-run
```

Run `uv run --locked run.py` to send or `uv run --locked python -m pytest -q` to test.

## Docker

Place `config.json` and `cookie.json` next to the Compose file.

```sh
docker compose pull
docker compose run --rm --no-deps --entrypoint python douyin-auto-fire run.py --dry-run
docker compose up -d
```

GitHub Actions tests pushes to `main` and publishes AMD64 and ARM64 images to `ghcr.io/bunizao/douyin-auto-fire` with `latest` and commit tags. Authenticate to GHCR if the package is private. Run `docker compose up -d --pull always` on the host to install updates.

For a persistent browser profile and a host timer, see the [systemd deployment](deploy/persistent-browser/README.md).

Based on [unmev/douyin-auto-fire](https://github.com/unmev/douyin-auto-fire).
