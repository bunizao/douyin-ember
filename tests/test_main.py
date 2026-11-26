from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.browser import AuthenticationError
from app.douyin import TargetUnavailableError
from app.history import History
from app.models import Message, Settings, Target, TaskConfig
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


def _task() -> TaskConfig:
    message = Message(type="text", content="测试")
    return TaskConfig(
        task_id="daily-streak",
        timezone="Asia/Shanghai",
        targets=(Target(name="好友A", messages=(message,)), Target(name="好友B", messages=(message,))),
        stickers={},
        interval_min=0,
        interval_max=0,
        continue_on_error=True,
        prevent_duplicates=False,
    )


@pytest.mark.asyncio
async def test_authentication_failure_stops_remaining_targets_and_notifies(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    task = _task()
    page = MagicMock()
    session = SimpleNamespace(page=page, context=MagicMock())

    @asynccontextmanager
    async def fake_open_douyin(_settings):
        yield session

    history = MagicMock()
    history.run_date.return_value = "2026-08-09"
    chat = MagicMock()
    chat.open_target = AsyncMock()
    notify = AsyncMock()
    monkeypatch.setattr(main_module, "load_task", lambda _settings: task)
    monkeypatch.setattr(main_module, "History", MagicMock(return_value=history))
    monkeypatch.setattr(main_module, "open_douyin", fake_open_douyin)
    monkeypatch.setattr(main_module, "open_private_messages", AsyncMock())
    monkeypatch.setattr(main_module, "DouyinChat", MagicMock(return_value=chat))
    monkeypatch.setattr(main_module, "verify_login", AsyncMock(side_effect=AuthenticationError("登录失效")))
    monkeypatch.setattr(main_module, "_screenshot", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "_write_results", MagicMock())
    monkeypatch.setattr(main_module, "_notify_dingtalk", notify)
    monkeypatch.setattr(main_module, "_configure_logging", lambda _path, _aliases=None: None)

    with pytest.raises(AuthenticationError, match="登录失效"):
        await main_module.run(settings)

    # 新的实现使用 _open_target_with_retry() 包装器，内部调用时 retries=0
    chat.open_target.assert_awaited_once_with("好友A", retries=0, conversation_id=None)
    results = notify.await_args.args[3]
    assert [(result.target, result.status) for result in results] == [("好友A", "failed")]


@pytest.mark.asyncio
async def test_browser_start_failure_still_notifies(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)

    @asynccontextmanager
    async def broken_open_douyin(_settings):
        raise RuntimeError("浏览器启动失败")
        yield

    history = MagicMock()
    history.run_date.return_value = "2026-08-09"
    notify = AsyncMock()
    monkeypatch.setattr(main_module, "load_task", lambda _settings: _task())
    monkeypatch.setattr(main_module, "History", MagicMock(return_value=history))
    monkeypatch.setattr(main_module, "open_douyin", broken_open_douyin)
    monkeypatch.setattr(main_module, "_write_results", MagicMock())
    monkeypatch.setattr(main_module, "_notify_dingtalk", notify)
    monkeypatch.setattr(main_module, "_configure_logging", lambda _path, _aliases=None: None)

    with pytest.raises(RuntimeError, match="浏览器启动失败"):
        await main_module.run(settings)

    results = notify.await_args.args[3]
    assert [(result.target, result.status) for result in results] == [("运行检查", "failed")]


@pytest.mark.asyncio
async def test_waits_between_consecutive_messages_for_same_friend(monkeypatch, tmp_path) -> None:
    settings = _settings(tmp_path)
    messages = (Message(type="text", content="一"), Message(type="text", content="二"))
    task = TaskConfig(
        task_id="daily-streak",
        timezone="Asia/Shanghai",
        targets=(Target(name="好友A", messages=messages),),
        stickers={},
        interval_min=0.5,
        interval_max=0.5,
        continue_on_error=True,
        prevent_duplicates=False,
    )
    page = MagicMock()
    session = SimpleNamespace(page=page, context=MagicMock())

    @asynccontextmanager
    async def fake_open_douyin(_settings):
        yield session

    history = MagicMock()
    history.run_date.return_value = "2026-08-09"
    chat = MagicMock()
    chat.open_target = AsyncMock()
    send_message = AsyncMock()
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(main_module, "load_task", lambda _settings: task)
    monkeypatch.setattr(main_module, "History", MagicMock(return_value=history))
    monkeypatch.setattr(main_module, "open_douyin", fake_open_douyin)
    monkeypatch.setattr(main_module, "open_private_messages", AsyncMock())
    monkeypatch.setattr(main_module, "DouyinChat", MagicMock(return_value=chat))
    monkeypatch.setattr(main_module, "verify_login", AsyncMock())
    monkeypatch.setattr(main_module, "send_message", send_message)
    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    monkeypatch.setattr(main_module, "_screenshot", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "_write_results", MagicMock())
    monkeypatch.setattr(main_module, "_notify_dingtalk", AsyncMock())
    monkeypatch.setattr(main_module, "_configure_logging", lambda _path, _aliases=None: None)

    assert await main_module.run(settings) == 0
    assert send_message.await_count == 2
    assert sleeps == [0.5]


@pytest.mark.asyncio
async def test_missing_conversation_does_not_block_later_recipients(monkeypatch, tmp_path) -> None:
    settings = replace(_settings(tmp_path), dingtalk_webhook=None)
    task = replace(
        _task(),
        targets=tuple(
            Target(name, (Message(type="text", content="Hello"),), conversation_id)
            for name, conversation_id in (
                ("Migrated friend", "id-1"), ("Missing friend", "id-2"), ("Later friend", "id-3")
            )
        ),
        continue_on_error=False,
        prevent_duplicates=True,
    )

    @asynccontextmanager
    async def fake_open_douyin(_settings):
        yield SimpleNamespace(page=MagicMock(), context=MagicMock())

    history_path = settings.artifacts_dir / "history.json"
    history = History(history_path)
    date = history.run_date(task.timezone)
    message_id = main_module._message_id(0, task.targets[0].messages[0])
    history.reserve(history.key(task.task_id, date, "Migrated friend", message_id))
    chat = MagicMock()
    chat.open_target = AsyncMock(side_effect=[None, TargetUnavailableError("not found"), None])
    chat.confirm_conversation_id = AsyncMock()
    send_message = AsyncMock()
    monkeypatch.setattr(main_module, "load_task", lambda _settings: task)
    monkeypatch.setattr(main_module, "open_douyin", fake_open_douyin)
    monkeypatch.setattr(main_module, "open_private_messages", AsyncMock())
    monkeypatch.setattr(main_module, "verify_login", AsyncMock())
    monkeypatch.setattr(main_module, "DouyinChat", MagicMock(return_value=chat))
    monkeypatch.setattr(main_module, "_screenshot", AsyncMock(return_value=None))
    monkeypatch.setattr(main_module, "send_message", send_message)

    assert await main_module.run(settings) == 1
    assert [call.kwargs["conversation_id"] for call in chat.open_target.await_args_list] == ["id-1", "id-2", "id-3"]
    chat.confirm_conversation_id.assert_awaited_once_with("id-3")
    send_message.assert_awaited_once()
    key = history.key(task.task_id, date, "conversation:id-3", message_id)
    assert History(history_path).entries[key]["status"] == "success"
