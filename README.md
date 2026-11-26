# Douyin Ember

A self-hosted sender that keeps Douyin chat streaks active.

## What this fork fixes

- **A missing chat no longer stops the queue.** The sender records the failure and continues with the next recipient. Login and security verification errors still stop the run.
- **Stable recipient matching.** Set a Douyin conversation ID to keep sending to the same chat after a nickname changes. The sender checks the open chat before sending.
- **Safer retries.** Send history covers successful and uncertain sends. The host scheduler prevents overlapping jobs, bounds run time, and can restore a pruned image from a verified backup.
- **Persistent browser login.** The host can reuse its own authenticated browser profile across runs.

### Bugs fixed

- **Docker cron runs failed every day.** Debian cron starts jobs with an empty environment, so scheduled runs never saw the cookie, task or notification settings. Only `RUN_ON_START` runs worked.
- **Docker image missing browser libraries.** The image now installs the system libraries the Playwright driver needs.
- **Chromium crashes and hung runs in Docker.** Compose now sets `init` to reap Chromium's child processes and a 256 MB `/dev/shm` (Docker's 64 MB default crashes the renderer). Each run is capped at 60 minutes.
- **Accounts leaked settings into each other.** Multi-account mode re-loaded the root `.env` inside every account and left keys behind, so one account could run with another's login, task file or webhook.
- **A stray `storage-state.json` overrode the configured login.** It is now used only when no cookie, storage state or browser profile is set.
- **A friend's message could abort the run.** Login and risk-control checks matched text anywhere on the page, so a chat containing "完成验证" or "登录后" stopped everything. Chat content, conversation lists and search results are now ignored.
- **Slow pages were never retried.** The error classifier treated "element not found" as permanent, so `target_open_retries` never applied to slow renders.
- **False "friend not found".** Search results got a fixed 1.5 s; they are now polled until the open timeout.
- **Leftover drafts were sent with the message.** The input box is cleared before typing.
- **Failed sends blocked the day's retry.** Failures that happen before anything reaches the chat (missing sticker, image or input box) now release the duplicate-send record, so a rerun can send. Uncertain sends still block it.
- **Invalid `WEBHOOK_TEMPLATE` failed late.** It is now rejected at startup instead of sending a broken payload.

### Resource use

- Login and risk prompts are checked instantly instead of waiting about 4 s before every message.
- Playwright tracing is off by default (`TRACE=true` turns it on). It recorded screenshots and DOM snapshots for the whole run.
- Screenshots and traces are deleted after 14 days and send history after 7 days. Both used to grow forever, and history was rewritten in full after every message.
- Exit codes: `0` all sent, `1` some targets failed, `2` stopped on login, risk control or configuration.

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
docker compose run --rm --no-deps --entrypoint python douyin-ember run.py --dry-run
docker compose up -d
```

GitHub Actions tests pushes to `main` and publishes AMD64 and ARM64 images to `ghcr.io/bunizao/douyin-ember` with `latest` and commit tags. Authenticate to GHCR if the package is private. Run `docker compose up -d --pull always` on the host to install updates.

For a persistent browser profile and a host timer, see the [systemd deployment](deploy/persistent-browser/README.md).

Based on [unmev/douyin-auto-fire](https://github.com/unmev/douyin-auto-fire).
