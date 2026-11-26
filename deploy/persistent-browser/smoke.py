"""Check the deployment runtime without credentials or sending messages."""

import asyncio
import json
import platform
from importlib.metadata import version
from urllib.parse import urlsplit

from playwright.async_api import async_playwright

from app.config import load_settings
from app.selectors import DOUYIN_CHAT_URL


async def main() -> None:
    settings = load_settings()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content("<title>Runtime check</title><p>Chromium works</p>")
            assert await page.title() == "Runtime check"
            response = await page.goto(
                DOUYIN_CHAT_URL, wait_until="domcontentloaded", timeout=60_000
            )
            assert response is not None and response.status < 400
            assert urlsplit(page.url).hostname in {"www.douyin.com", "douyin.com"}
            print(json.dumps({
                "python": platform.python_version(),
                "playwright": version("playwright"),
                "chromium": browser.version,
                "douyin_http_status": response.status,
                "douyin_host": urlsplit(page.url).hostname,
                "probe_uses_credentials": False,
                "messages_sent": 0,
            }))
        finally:
            await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
