import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.browser import AuthenticationError
from app.douyin import PageOperationError, TargetUnavailableError
from app.history import History, run_date
from app.models import Message, Settings, Target, TaskConfig
from app.sender import NotSentError
import app.main as main_module


def _settings(tmp_path) -> Settings:
    return Settings(
        task_config_path=tmp_path / "config.json",
        storage_state=None,
        cookie="[]",
        headless=True,
        browser_path=None,
        artifacts_dir=tmp_path / "artifacts",
        trace=False,
        dingtalk_webhook="https://oapi.dingtalk.com/robot/send?access_token=token",
        dingtalk_secret="SEC-secret",
    )


def _task(**changes) -> TaskConfig:
    message = Message(type="text", content="测试")
    task = TaskConfig(
        task_id="daily-streak",
        timezone="Asia/Shanghai",
        targets=(Target(name="好友A", messages=(message,)), Target(name="好友B", messages=(message,))),
        stickers={},
        interval_min=0,
        interval_max=0,
        continue_on_error=True,
        prevent_duplicates=False,
    )
    return replace(task, **changes)


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Patch the browser boundary; return the mocks a test may adjust."""
    page = MagicMock()

    @asynccontextmanager
    async def fake_open_douyin(_settings):
        yield SimpleNamespace(page=page, context=MagicMock())

    chat = MagicMock()
    chat.open_target = AsyncMock()
    chat.confirm_conversation_id = AsyncMock()
    mocks = SimpleNamespace(
        chat=chat,
        notify=AsyncMock(),
        send_message=AsyncMock(),
        verify_login=AsyncMock(),
        sleeps=[],
        task=_task(),
    )

    async def fake_sleep(seconds):
        mocks.sleeps.append(seconds)

    monkeypatch.setattr(main_module, "load_task", lambda _settings: mocks.task)
    monkeypatch.setattr(main_module, "open_douyin", fake_open_douyin)
    monkeypatch.setattr(main_module, "open_private_messages", AsyncMock())
    monkeypatch.setattr(main_module, "DouyinChat", MagicMock(return_value=chat))
    monkeypatch.setattr(main_module, "verify_login", mocks.verify_login)
    monkeypatch.setattr(main_module, "send_message", mocks.send_message)
    monkeypatch.setattr(main_module, "_screenshot", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "_notify_dingtalk", mocks.notify)
    monkeypatch.setattr(main_module, "_configure_logging", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    return mocks


def _notified(mocks) -> list[tuple[str, str]]:
    return [(result.target, result.status) for result in mocks.notify.await_args.args[3]]


@pytest.mark.asyncio
async def test_authentication_failure_stops_remaining_targets_and_notifies(harness, tmp_path) -> None:
    harness.verify_login.side_effect = AuthenticationError("登录失效")

    assert await main_module.run(_settings(tmp_path)) == 2

    harness.chat.open_target.assert_awaited_once_with("好友A", conversation_id=None)
    assert _notified(harness) == [("好友A", "failed")]


@pytest.mark.asyncio
async def test_browser_start_failure_still_notifies(harness, monkeypatch, tmp_path) -> None:
    @asynccontextmanager
    async def broken_open_douyin(_settings):
        raise RuntimeError("浏览器启动失败")
        yield

    monkeypatch.setattr(main_module, "open_douyin", broken_open_douyin)

    assert await main_module.run(_settings(tmp_path)) == 1
    assert _notified(harness) == [("运行检查", "failed")]
    result = json.loads((tmp_path / "artifacts" / "result.json").read_text(encoding="utf-8"))
    assert result["results"][0]["error"] == "浏览器启动失败"


@pytest.mark.asyncio
async def test_waits_between_messages_and_targets(harness, tmp_path) -> None:
    messages = (Message(type="text", content="一"), Message(type="text", content="二"))
    harness.task = _task(
        targets=(Target(name="好友A", messages=messages), Target(name="好友B", messages=messages[:1])),
        interval_min=0.5,
        interval_max=0.5,
    )

    assert await main_module.run(_settings(tmp_path)) == 0
    assert harness.send_message.await_count == 3
    assert harness.sleeps == [0.5, 0.5]


@pytest.mark.asyncio
async def test_dry_run_opens_chats_without_sending(harness, tmp_path) -> None:
    assert await main_module.run(_settings(tmp_path), dry_run=True) == 0
    assert harness.chat.open_target.await_count == 2
    harness.send_message.assert_not_awaited()
    assert harness.sleeps == []


@pytest.mark.asyncio
async def test_open_retries_page_errors_but_not_missing_friends(harness, tmp_path) -> None:
    harness.task = _task(target_open_retries=2)
    harness.chat.open_target.side_effect = [
        PageOperationError("slow"),
        None,
        TargetUnavailableError("not found"),
    ]

    assert await main_module.run(_settings(tmp_path)) == 1
    assert harness.chat.open_target.await_count == 3
    assert _notified(harness) == [("好友A", "success"), ("好友B", "failed")]


@pytest.mark.asyncio
async def test_missing_conversation_does_not_block_later_recipients(harness, tmp_path) -> None:
    settings = _settings(tmp_path)
    harness.task = _task(
        targets=tuple(
            Target(name, (Message(type="text", content="Hello"),), conversation_id)
            for name, conversation_id in (
                ("Migrated friend", "id-1"), ("Missing friend", "id-2"), ("Later friend", "id-3")
            )
        ),
        continue_on_error=False,
        prevent_duplicates=True,
    )
    task = harness.task
    history_path = settings.artifacts_dir / "history.json"
    history = History(history_path)
    today = run_date(task.timezone)
    message_id = main_module._message_id(0, task.targets[0].messages[0])
    history.reserve(history.key(task.task_id, today, "Migrated friend", message_id))
    harness.chat.open_target.side_effect = [None, TargetUnavailableError("not found"), None]

    assert await main_module.run(settings) == 1
    assert [call.kwargs["conversation_id"] for call in harness.chat.open_target.await_args_list] == ["id-1", "id-2", "id-3"]
    harness.chat.confirm_conversation_id.assert_awaited_once_with("id-3")
    harness.send_message.assert_awaited_once()
    key = history.key(task.task_id, today, "conversation:id-3", message_id)
    assert History(history_path).entries[key]["status"] == "success"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "kept"),
    [(NotSentError("文字未能写入聊天输入框"), False), (PageOperationError("状态未能确认"), True)],
)
async def test_failed_send_keeps_reservation_only_when_uncertain(harness, tmp_path, error, kept) -> None:
    settings = _settings(tmp_path)
    harness.task = _task(targets=_task().targets[:1], prevent_duplicates=True)
    harness.send_message.side_effect = error

    assert await main_module.run(settings) == 1

    entries = History(settings.artifacts_dir / "history.json").entries
    assert bool(entries) is kept


def test_prune_artifacts_keeps_recent_files(tmp_path) -> None:
    screenshots = tmp_path / "screenshots"
    screenshots.mkdir()
    old, recent = screenshots / "old.png", screenshots / "recent.png"
    old.write_bytes(b"")
    recent.write_bytes(b"")
    stale = time.time() - 30 * 86_400
    os.utime(old, (stale, stale))

    main_module._prune_artifacts(tmp_path)

    assert not old.exists()
    assert recent.exists()
