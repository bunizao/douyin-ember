# Douyin Ember

A self-hosted bot that keeps Douyin chat streaks alive.
Forked from [`unmev/douyin-auto-fire`](https://github.com/unmev/douyin-auto-fire) and cleaned up to actually run reliably.

## Features

- **Text, stickers and images:** Send plain text, Douyin's built-in stickers by name, or PNG/JPG/GIF/WEBP files.
- **Random picks:** A `random` message chooses one of several options each run, so friends don't get the same line every day.
- **Per-friend messages:** Give everyone the same `messages` list, or give each target its own.
- **Human-ish pacing:** Waits a random 3–8 s between messages (configurable with `send_interval_seconds`).
- **Multi-account:** List accounts in `config/accounts.json`; each gets its own login, task file and artifacts.
- **Notifications:** DingTalk bot or any webhook, with custom headers and a JSON body template. `scripts/check_webhook.py` sends a test.
- **Private logs:** Real names are replaced with aliases (`好友01`, `好友02`, …) in logs, results and notifications.
- **Dry run:** `--dry-run` checks login and finds every friend without sending anything.
- **Duplicate protection:** With `"prevent_duplicates": true`, a rerun on the same day skips messages that were already sent.
- **QR login helper:** `scripts/login.py` opens a browser, waits for the scan and saves `storage-state.json`.
- **Failure screenshots:** Every failed target leaves a screenshot in `artifacts/`.

## What's Fixed

### Reliability & Behavior
- **Isolated failures:** A missing chat no longer kills the queue. The error is logged and the run continues. Login and security verification prompts still abort immediately.
- **Stable chat matching:** Bind recipients by Douyin `conversation_id` so nickname changes don't break delivery, and verify the active chat before sending.
- **Safer retries:** Send history tracks both confirmed and uncertain deliveries. Pre-send failures (missing sticker, image, or input box) release the duplicate-send lock so reruns can retry, while uncertain sends still block duplicates.
- **Persistent browser login:** Reuses an authenticated browser profile on the host across runs.
- **Fail-fast config:** Invalid `WEBHOOK_TEMPLATE` values are rejected at startup instead of failing mid-run.

### Bugs Squashed
- **Broken Docker cron:** Debian cron starts jobs with an empty environment, hiding cookies, tasks and webhook settings (only `RUN_ON_START` worked). The environment is now passed through.
- **Missing Playwright libraries:** The Docker image installs the system libraries the Playwright driver needs.
- **Chromium crashes & hangs:** `init` reaps zombie child processes, `/dev/shm` is raised from Docker's 64 MB default to 256 MB, and each run is capped at 60 minutes.
- **Cross-account config leaks:** Multi-account mode reloaded the root `.env` per account and left stale keys behind, so accounts could inherit another's credentials, tasks or webhooks.
- **Stray `storage-state.json` overrides:** It is only picked up when no cookie, storage state or browser profile is configured.
- **False-positive risk aborts:** Login and verification checks matched keywords anywhere on the page, so a friend's message mentioning login or verification could abort the run. Chat content, conversation lists and search results are now ignored.
- **Ignored retries on slow pages:** "Element not found" was classified as permanent, bypassing `target_open_retries` on slow renders.
- **False "friend not found":** The fixed 1.5 s search wait is replaced with polling up to the open timeout.
- **Stale drafts sent by mistake:** The input box is cleared before typing.

### Performance & Cleanup
- **No per-message delay:** Login and risk checks run immediately instead of waiting ~4 s before every message.
- **Tracing off by default:** Playwright tracing (full screenshots and DOM snapshots) is disabled unless `TRACE=true` is set.
- **Bounded disk usage:** Screenshots and traces are deleted after 14 days and send history after 7 days, so neither grows forever.
- **Hardened host scheduler:** Prevents overlapping runs, enforces time limits, and can restore a pruned image from a verified backup.
- **Clear exit codes:** `0` = all sent, `1` = partial failure, `2` = stopped on login, risk control or config error.

## Quick Start

### With `uv`
Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --locked
uv run --locked playwright install chromium
cp .env.example .env
cp config.example.json config.json
```

Fill in `config.json` and set `DOUYIN_COOKIE` or `DOUYIN_STORAGE_STATE` in `.env` (keep credentials and recipient configs private). A bound entry in `friends` looks like:

```json
{"name": "Local label", "conversation_id": "0:1:123:456"}
```

Dry run, send, or test:

```bash
uv run --locked run.py --dry-run
uv run --locked run.py
uv run --locked python -m pytest -q
```

To run it daily on a server without Docker, see [uv + systemd](deploy/systemd/README.md).

### With Docker
Place `config.json` and `cookie.json` next to `docker-compose.yml`:

```bash
docker compose pull
docker compose run --rm --no-deps --entrypoint python douyin-ember run.py --dry-run
docker compose up -d
```

The container runs the task daily at 00:30 Asia/Shanghai; set `CRON_SCHEDULE` and `TZ` in `.env` to change that. The "Publish Docker image" workflow publishes `amd64`/`arm64` images to `ghcr.io/bunizao/douyin-ember` (`latest` and commit tags). Run `docker compose up -d --pull always` to update.

For a persistent browser profile and a host timer, see the [persistent browser deployment](deploy/persistent-browser/README.md).

### Multi-account
Copy `config/accounts.example.json` to `config/accounts.json` and `config/account.env.example` to one `.env.accountN` per account. Each account needs its own `DOUYIN_COOKIE` and `TASK_CONFIG`; everything else is inherited from `.env`.
