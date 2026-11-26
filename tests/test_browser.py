import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.browser import (
    AuthenticationError,
    RiskControlError,
    SearchBoxNotReadyError,
    _collect_safe_diagnostic,
    _normalize_cookies,
    _safe_url,
    open_private_messages,
)
from app.config import ConfigError
from app.models import Settings
from app.selectors import DOUYIN_CHAT_URL, LOGIN_REQUIRED_MARKERS, RISK_MARKERS


@pytest.mark.asyncio
async def test_persistent_profile_reuses_browser_state_without_cookie_import(tmp_path) -> None:
    from app.browser import open_douyin

    context = MagicMock()
    context.new_page = AsyncMock()
    context.close = AsyncMock()
    playwright = MagicMock()
    playwright.chromium.launch_persistent_context = AsyncMock(return_value=context)
    playwright.stop = AsyncMock()
    manager = MagicMock()
    manager.start = AsyncMock(return_value=playwright)
    profile = tmp_path / "profile"
    settings = Settings(
        task_config_path=tmp_path / "config.json", storage_state=None, cookie=None,
        headless=True, browser_path=None, artifacts_dir=tmp_path / "artifacts",
        trace=False, browser_user_data_dir=profile,
    )

    with patch("app.browser.async_playwright", return_value=manager):
        async with open_douyin(settings) as session:
            assert session.context is context

    playwright.chromium.launch_persistent_context.assert_awaited_once_with(
        str(profile), headless=True, viewport={"width": 1440, "height": 1000}, locale="zh-CN",
    )
    playwright.chromium.launch.assert_not_called()
    context.add_cookies.assert_not_called()
    context.close.assert_awaited_once()
    playwright.stop.assert_awaited_once()


def _chat_page() -> MagicMock:
    page = MagicMock()
    page.goto = AsyncMock()
    page.reload = AsyncMock()
    page.wait_for_timeout = AsyncMock()
    page.url = "https://www.douyin.com/chat"
    page.title = AsyncMock(return_value="抖音私信")
    page.evaluate = AsyncMock(
        return_value={"inputs": [], "textareas": [], "contenteditable_count": 0, "role_textbox_count": 0}
    )
    return page


@pytest.mark.asyncio
async def test_opens_chat_directly_before_checking_login() -> None:
    page = _chat_page()

    with patch("app.browser._marker_shown", new=AsyncMock(return_value=False)):
        with patch("app.browser.visible_selector", new=AsyncMock(return_value='input[placeholder*="搜索"]')):
            await open_private_messages(page)

    page.goto.assert_awaited_once_with(DOUYIN_CHAT_URL, wait_until="domcontentloaded", timeout=45_000)


def test_safe_url_strips_query_and_fragment() -> None:
    assert _safe_url("https://www.douyin.com/chat?token=SECRET&x=1#frag") == "https://www.douyin.com/chat"
    assert _safe_url("https://www.douyin.com/chat") == "https://www.douyin.com/chat"
    assert _safe_url("") == ""


@pytest.mark.asyncio
async def test_safe_diagnostic_excludes_content_and_nicknames() -> None:
    page = MagicMock()
    page.url = "https://www.douyin.com/chat?token=COOKIE_SECRET"
    page.title = AsyncMock(return_value="抖音私信")
    page.evaluate = AsyncMock(
        return_value={
            "inputs": [
                {
                    "tag": "input",
                    "type": "text",
                    "placeholder": "搜索联系人",
                    "role": None,
                    "aria_label": None,
                    "value": "张三 聊天内容",
                }
            ],
            "textareas": [{"tag": "textarea", "placeholder": "搜索", "role": None, "aria_label": None, "value": "body-inner-text"}],
            "contenteditable_count": 2,
            "role_textbox_count": 3,
        }
    )

    with patch("app.browser._marker_shown", new=AsyncMock(return_value=False)):
        with patch("app.browser.visible_selector", new=AsyncMock(return_value=None)):
            diagnostic = await _collect_safe_diagnostic(page, LOGIN_REQUIRED_MARKERS, RISK_MARKERS)

    # Safe attributes must show up.
    assert "url=https://www.douyin.com/chat" in diagnostic
    assert "title=抖音私信" in diagnostic
    assert '"placeholder": "搜索联系人"' in diagnostic
    assert "role_textbox_count=3" in diagnostic
    assert "contenteditable_count=2" in diagnostic
    assert "login_marker=false" in diagnostic
    assert "risk_marker=false" in diagnostic
    assert "private_marker=false" in diagnostic

    # Forbidden content must never leak.
    assert "COOKIE_SECRET" not in diagnostic
    assert "张三" not in diagnostic
    assert "聊天内容" not in diagnostic
    assert "body-inner-text" not in diagnostic
    assert "token=" not in diagnostic


@pytest.mark.asyncio
async def test_open_private_messages_logs_diagnostic_when_search_missing(caplog) -> None:
    page = _chat_page()

    with patch("app.browser._marker_shown", new=AsyncMock(return_value=False)):
        with patch("app.browser.visible_selector", new=AsyncMock(return_value=None)):
            with caplog.at_level(logging.ERROR, logger="douyin_sender"):
                with pytest.raises(SearchBoxNotReadyError, match="刷新后搜索框仍未就绪"):
                    await open_private_messages(page, timeout_ms=500)

    assert "页面安全诊断" in caplog.text
    assert "role_textbox_count=0" in caplog.text
    page.reload.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_hit_emits_no_diagnostic(caplog) -> None:
    page = _chat_page()

    with patch("app.browser._marker_shown", new=AsyncMock(return_value=False)):
        with patch("app.browser.visible_selector", new=AsyncMock(return_value='input[placeholder*="搜索"]')):
            with caplog.at_level(logging.ERROR, logger="douyin_sender"):
                await open_private_messages(page)

    assert "页面安全诊断" not in caplog.text
    page.wait_for_timeout.assert_awaited_once_with(3_000)


@pytest.mark.asyncio
async def test_search_box_found_without_waiting_on_absent_markers() -> None:
    page = _chat_page()
    marker_checks = AsyncMock(return_value=False)

    with patch("app.browser._marker_shown", new=marker_checks):
        with patch("app.browser.visible_selector", new=AsyncMock(side_effect=[None, None, 'input[placeholder="搜索"]'])):
            await open_private_messages(page)

    polls = [call.args[0] for call in page.wait_for_timeout.await_args_list]
    assert polls == [250, 250, 3_000]
    page.reload.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_box_missing_recovers_after_reload() -> None:
    page = _chat_page()
    found = iter([None, None, None, 'input[placeholder="搜索"]'])

    with patch("app.browser._marker_shown", new=AsyncMock(return_value=False)):
        with patch("app.browser.visible_selector", new=AsyncMock(side_effect=lambda *_: next(found))):
            await open_private_messages(page, timeout_ms=500)

    page.reload.assert_awaited_once_with(wait_until="domcontentloaded", timeout=45_000)


@pytest.mark.asyncio
async def test_search_box_falls_back_to_goto_when_reload_fails() -> None:
    page = _chat_page()
    page.reload = AsyncMock(side_effect=RuntimeError("reload 失败"))
    found = iter([None, None, None, 'input[placeholder*="搜索"]'])

    with patch("app.browser._marker_shown", new=AsyncMock(return_value=False)):
        with patch("app.browser.visible_selector", new=AsyncMock(side_effect=lambda *_: next(found))):
            await open_private_messages(page, timeout_ms=500)

    page.reload.assert_awaited_once()
    assert page.goto.await_count == 2


@pytest.mark.asyncio
async def test_risk_control_raises_before_search_check() -> None:
    page = _chat_page()
    search = AsyncMock()

    with patch("app.browser._marker_shown", new=AsyncMock(return_value=True)):
        with patch("app.browser.visible_selector", new=search):
            with pytest.raises(RiskControlError, match="安全验证"):
                await open_private_messages(page)

    search.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_required_raises_before_search_check() -> None:
    page = _chat_page()

    with patch("app.browser._marker_shown", new=AsyncMock(side_effect=[False, True])):
        with pytest.raises(AuthenticationError, match="登录状态失效"):
            await open_private_messages(page)


@pytest.mark.asyncio
async def test_markers_ignore_text_written_by_friends() -> None:
    from playwright.async_api import async_playwright

    from app.browser import _marker_shown

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            await page.set_content(
                '<div class="conversationConversationListwrapper"><div data-e2e="conversation-item">'
                "<span>请先完成验证</span></div></div>"
                '<div class="messageMessageListlist"><p>登录后再说</p></div>'
                '<div style="display:none">安全验证</div>'
            )
            assert not await _marker_shown(page, RISK_MARKERS)
            assert not await _marker_shown(page, LOGIN_REQUIRED_MARKERS)

            await page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div>请完成安全验证</div>')")
            assert await _marker_shown(page, RISK_MARKERS)
        finally:
            await browser.close()


def test_normalizes_cookie_editor_export() -> None:
    cookies = [
        {
            "domain": ".douyin.com",
            "expirationDate": 1800175766.5,
            "hostOnly": False,
            "httpOnly": True,
            "name": "UIFID",
            "path": "/",
            "sameSite": "no_restriction",
            "secure": True,
            "session": False,
            "storeId": None,
            "value": "token",
        }
    ]

    assert _normalize_cookies(cookies) == [
        {
            "name": "UIFID",
            "value": "token",
            "domain": ".douyin.com",
            "path": "/",
            "expires": 1800175766.5,
            "httpOnly": True,
            "secure": True,
            "sameSite": "None",
        }
    ]


def test_session_cookie_ignores_expiration_date() -> None:
    cookies = [
        {
            "domain": ".douyin.com",
            "expirationDate": 1800175766.5,
            "name": "sessionid",
            "session": True,
            "value": "token",
        }
    ]

    assert _normalize_cookies(cookies)[0]["expires"] == -1


def test_ignores_cookie_editor_empty_name_artifact() -> None:
    cookies = [
        {"domain": "www.douyin.com", "name": "", "value": "douyin.com"},
        {"domain": ".douyin.com", "name": "sessionid", "value": "token"},
    ]

    assert [cookie["name"] for cookie in _normalize_cookies(cookies)] == ["sessionid"]


def test_rejects_cookie_without_domain() -> None:
    with pytest.raises(ConfigError, match="缺少有效的 domain"):
        _normalize_cookies([{"name": "UIFID", "value": "token"}])
