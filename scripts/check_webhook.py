#!/usr/bin/env python3
"""Send a test notification with the configured WEBHOOK_* settings."""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import ConfigError, load_settings, read_env
from app.models import TargetResult
from app.notifier import send_webhook_notification


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="read this file instead of ./.env")
    args = parser.parse_args()
    try:
        settings = load_settings(read_env(args.env_file))
    except ConfigError as exc:
        print(f"Config error: {exc}")
        return 1
    if not settings.webhook_url:
        print("WEBHOOK_URL is not set")
        return 1
    results = [TargetResult(target="好友01", status="success", sent=2)]
    try:
        asyncio.run(send_webhook_notification(
            webhook_url=settings.webhook_url,
            task_id="webhook-test",
            dry_run=True,
            results=results,
            screenshots=[],
            headers=settings.webhook_headers,
            template=settings.webhook_template,
        ))
    except Exception as exc:
        print(f"Webhook failed: {exc}")
        return 1
    print(f"Sent a test notification to {settings.webhook_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
