"""Log in interactively on the deployment's persistent browser profile."""

import asyncio
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.browser import AuthenticationError, open_douyin, open_private_messages
from app.config import ConfigError, load_settings, read_env
from app.history import run_lock
from app.selectors import DOUYIN_CHAT_URL


async def login() -> None:
    settings = replace(load_settings(read_env()), trace=False)
    if not settings.browser_user_data_dir:
        raise ConfigError("Set BROWSER_USER_DATA_DIR before logging in")
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    screenshot = settings.artifacts_dir / "server-login.png"
    with run_lock(settings.artifacts_dir / "run.lock"):
        async with open_douyin(settings) as session:
            page = session.page
            cookies = await session.context.cookies(DOUYIN_CHAT_URL)
            if any(cookie["name"] == "sessionid" and cookie["value"] for cookie in cookies):
                await open_private_messages(page)
                print("Existing server login verified. No messages sent.", flush=True)
                return
            while True:
                await page.goto(DOUYIN_CHAT_URL, wait_until="domcontentloaded", timeout=60_000)
                await page.get_by_text("扫码登录", exact=True).first.wait_for(state="visible", timeout=30_000)
                await page.wait_for_timeout(2_000)
                await page.screenshot(path=str(screenshot))
                screenshot.chmod(0o600)
                print(f"Login screenshot ready: {screenshot}", flush=True)
                answer = await asyncio.to_thread(input, "Press Enter after login, or r to refresh the QR code: ")
                if answer.strip().lower() != "r":
                    break
            for _ in range(60):
                cookies = await session.context.cookies(DOUYIN_CHAT_URL)
                if any(cookie["name"] == "sessionid" and cookie["value"] for cookie in cookies):
                    break
                await page.wait_for_timeout(500)
            else:
                raise AuthenticationError("The browser has not completed login; no session cookie was received")
            await page.get_by_text("扫码登录", exact=True).first.wait_for(state="hidden", timeout=15_000)
            await open_private_messages(page)
            print("Login verified; browser profile saved. No messages sent.", flush=True)


if __name__ == "__main__":
    asyncio.run(login())
