import importlib.util
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.browser import AuthenticationError
from app.models import Settings


@pytest.mark.asyncio
@pytest.mark.parametrize("authenticated,existing", [(True, False), (False, False), (True, True)])
async def test_login_waits_for_callback_before_navigating(monkeypatch, tmp_path, authenticated, existing):
    path = Path(__file__).resolve().parents[1] / "deploy/persistent-browser/login.py"
    spec = importlib.util.spec_from_file_location("server_login", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    page = MagicMock()
    page.goto = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.get_by_text.return_value.first.wait_for = AsyncMock()

    async def screenshot(*, path):
        Path(path).write_bytes(b"screenshot")

    page.screenshot = AsyncMock(side_effect=screenshot)
    context = MagicMock()
    context.cookies = AsyncMock(
        side_effect=[[{"name": "sessionid", "value": "test-session"}]] if existing
        else [[], [], [{"name": "sessionid", "value": "test-session"}]]
        if authenticated else None,
        return_value=[],
    )

    @asynccontextmanager
    async def open_browser(settings):
        assert settings.trace is False
        yield SimpleNamespace(page=page, context=context)

    monkeypatch.setattr(module, "load_settings", lambda _env: Settings(
        task_config_path=tmp_path / "config.json", storage_state=None, cookie=None,
        headless=False, browser_path=None, trace=True,
        browser_user_data_dir=tmp_path / "profile", artifacts_dir=tmp_path / "artifacts",
    ))
    monkeypatch.setattr(module, "open_douyin", open_browser)
    monkeypatch.setattr(module.asyncio, "to_thread", AsyncMock(return_value=""))
    open_chat = AsyncMock()
    monkeypatch.setattr(module, "open_private_messages", open_chat)

    if authenticated:
        await module.login()
        assert context.cookies.await_count == (1 if existing else 3)
        open_chat.assert_awaited_once_with(page)
    else:
        with pytest.raises(AuthenticationError, match="no session cookie"):
            await module.login()
        open_chat.assert_not_awaited()

    if existing:
        page.goto.assert_not_awaited()
        module.asyncio.to_thread.assert_not_awaited()
    else:
        page.goto.assert_awaited_once_with(module.DOUYIN_CHAT_URL, wait_until="domcontentloaded", timeout=60_000)
