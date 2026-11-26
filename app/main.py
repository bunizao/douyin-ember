from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import random
import re
import time
from dataclasses import asdict
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.browser import (
    AuthenticationError,
    BrowserSession,
    RiskControlError,
    open_douyin,
    open_private_messages,
    save_trace,
    verify_login,
)
from app.config import ConfigError, discover_storage_state, load_settings, load_task, read_env
from app.douyin import DouyinChat, TargetUnavailableError
from app.history import AlreadyRunningError, History, run_date, run_lock
from app.models import Settings, Target, TargetResult, TaskConfig
from app.notifier import send_dingtalk_notification, send_webhook_notification
from app.privacy import RedactingFormatter, build_target_aliases, redact_text, target_alias
from app.sender import NotSentError, send_message


LOGGER = logging.getLogger("douyin_sender")

# Errors that make every later step pointless; continuing would only add risk.
FATAL_ERRORS = (AuthenticationError, RiskControlError, ConfigError)
OPEN_RETRY_DELAY_SECONDS = 3.0
ARTIFACT_RETENTION = timedelta(days=14)


async def run(settings: Settings, dry_run: bool = False) -> int:
    """Send the day's messages. Returns 0 when all succeed, 1 when any fails,
    and 2 when the run stopped on a login, risk-control or config error."""
    task = load_task(settings)
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    aliases = build_target_aliases(task.targets)
    _configure_logging(settings.artifacts_dir, aliases)
    started = time.monotonic()
    report = _Report(settings)

    try:
        async with open_douyin(settings) as session:
            report.session = session
            try:
                await open_private_messages(session.page)
            except Exception as exc:
                label = "登录检查" if isinstance(exc, (AuthenticationError, RiskControlError)) else "运行检查"
                await report.fail(label, exc, "login")
            else:
                await _send_all(session, task, report, dry_run)
            await report.finish_trace()
    except Exception as exc:
        await report.fail("运行检查", exc, None)

    _write_results(settings.artifacts_dir, task.task_id, dry_run, report.results, aliases, time.monotonic() - started)
    _prune_artifacts(settings.artifacts_dir)
    await _notify_dingtalk(settings, task.task_id, dry_run, report.results, report.screenshots)
    await _notify_webhook(settings, task.task_id, dry_run, report.results, report.screenshots)
    succeeded = sum(result.status == "success" for result in report.results)
    failed = len(report.results) - succeeded
    LOGGER.info("执行结束: 成功 %d，失败 %d，耗时 %.0f 秒", succeeded, failed, time.monotonic() - started)
    if report.stopped:
        return 2
    return 1 if failed else 0


async def _send_all(session: BrowserSession, task: TaskConfig, report: _Report, dry_run: bool) -> None:
    page = session.page
    chat = DouyinChat(page, timeout_ms=int(task.target_open_timeout_seconds * 1000))
    history = History(report.settings.artifacts_dir / "history.json")
    today = run_date(task.timezone)

    for index, target in enumerate(task.targets):
        if index and not dry_run:
            await asyncio.sleep(random.uniform(task.interval_min, task.interval_max))
        alias = target_alias(index)
        LOGGER.info("处理好友: %s", alias)
        sent = 0
        try:
            await _open_target(chat, target, task.target_open_retries)
            for message_index, message in enumerate(() if dry_run else target.messages):
                if message_index:
                    await asyncio.sleep(random.uniform(task.interval_min, task.interval_max))
                keys = _history_keys(history, task, today, target, message_index, message)
                if task.prevent_duplicates and any(history.contains(key) for key in keys):
                    LOGGER.info("跳过当天已处理或结果不确定的消息: %s #%d", alias, message_index + 1)
                    continue
                await verify_login(page, timeout_ms=3_000)
                if target.conversation_id is not None:
                    await chat.confirm_conversation_id(target.conversation_id)
                if task.prevent_duplicates:
                    history.reserve(keys[0])
                try:
                    await send_message(page, chat, message, task.stickers)
                except NotSentError:
                    # Nothing reached Douyin, so a later run today may retry it.
                    if task.prevent_duplicates:
                        history.release(keys[0])
                    raise
                if task.prevent_duplicates:
                    history.mark_success(keys[0])
                sent += 1
            report.results.append(TargetResult(target=target.name, status="success", sent=sent, target_alias=alias))
        except Exception as exc:
            await report.fail(target.name, exc, alias, sent=sent, alias=alias)
            if isinstance(exc, FATAL_ERRORS):
                LOGGER.warning("登录失效或触发风控，停止处理剩余好友")
                return
            if not task.continue_on_error and not isinstance(exc, TargetUnavailableError):
                return


class _Report:
    """Collects results and failure evidence for one run."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session: BrowserSession | None = None
        self.results: list[TargetResult] = []
        self.screenshots: list[Path] = []
        self.stopped = False
        self._trace_done = not settings.trace

    async def fail(
        self,
        target: str,
        exc: Exception,
        screenshot_label: str | None,
        *,
        sent: int = 0,
        alias: str | None = None,
    ) -> None:
        LOGGER.error("%s 失败", alias or target, exc_info=exc)
        self.stopped = self.stopped or isinstance(exc, FATAL_ERRORS)
        self.results.append(TargetResult(target=target, status="failed", sent=sent, error=str(exc), target_alias=alias))
        if self.session is None or screenshot_label is None:
            return
        screenshot = await _screenshot(self.session.page, self.settings.artifacts_dir, screenshot_label)
        if screenshot:
            self.screenshots.append(screenshot)
        if not self._trace_done:
            self._trace_done = True
            try:
                await save_trace(self.session, _trace_path(self.settings.artifacts_dir))
            except Exception:
                LOGGER.exception("保存 trace 失败")

    async def finish_trace(self) -> None:
        """Discard the trace of a run that needed none."""
        if self._trace_done or self.session is None:
            return
        self._trace_done = True
        try:
            await self.session.context.tracing.stop()
        except Exception:
            LOGGER.exception("停止 trace 失败")


async def _open_target(chat: DouyinChat, target: Target, retries: int) -> None:
    """Open a chat, retrying page hiccups but never a missing friend or a login problem."""
    for attempt in range(retries + 1):
        try:
            await chat.open_target(target.name, conversation_id=target.conversation_id)
            return
        except (TargetUnavailableError, *FATAL_ERRORS):
            raise
        except Exception as exc:
            if attempt == retries:
                raise
            LOGGER.info("打开聊天失败 (第 %d/%d 次)，%.0f 秒后重试: %s", attempt + 1, retries + 1, OPEN_RETRY_DELAY_SECONDS, type(exc).__name__)
            await asyncio.sleep(OPEN_RETRY_DELAY_SECONDS)


def _history_keys(history: History, task: TaskConfig, today: str, target: Target, index: int, message) -> list[str]:
    """The key to record first, then the legacy name key that also counts as sent."""
    message_id = _message_id(index, message)
    by_name = history.key(task.task_id, today, target.name, message_id)
    if target.conversation_id is None:
        return [by_name]
    return [history.key(task.task_id, today, f"conversation:{target.conversation_id}", message_id), by_name]


def main() -> int:
    args = _parse_cli_args()
    try:
        settings = load_settings(discover_storage_state(read_env(args.env_file)))
        with run_lock(settings.artifacts_dir / "run.lock"):
            return asyncio.run(run(settings, dry_run=args.dry_run))
    except (ConfigError, AlreadyRunningError) as exc:
        print(f"错误: {exc}")
        return 2
    except KeyboardInterrupt:
        print("任务已取消")
        return 130


def _parse_cli_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="向多个抖音好友发送配置的消息")
    parser.add_argument("--dry-run", action="store_true", help="只验证登录和好友，不发送消息")
    parser.add_argument("--env-file", help="指定 .env 文件路径")
    return parser.parse_args()


def _configure_logging(
    artifacts_dir: Path,
    aliases: dict[str, str] | None = None,
    *,
    label: str | None = None,
    reset: bool = False,
) -> None:
    if reset or not LOGGER.handlers:
        for handler in list(LOGGER.handlers):
            LOGGER.removeHandler(handler)
            handler.close()
        LOGGER.setLevel(logging.INFO)
        pattern = "%(asctime)s %(levelname)s %(message)s"
        if label:
            pattern = pattern.replace(" %(message)s", f" [{label}] %(message)s")
        formatter = RedactingFormatter(pattern, aliases=aliases)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            artifacts_dir / "run.log", maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        LOGGER.addHandler(file_handler)
        LOGGER.addHandler(stream_handler)
        return
    # Multi-account mode configures handlers first; run() only adds aliases.
    for handler in LOGGER.handlers:
        if isinstance(handler.formatter, RedactingFormatter):
            handler.formatter.aliases = dict(aliases or {})


async def _screenshot(page, artifacts_dir: Path, label: str) -> Path | None:
    safe_label = re.sub(r"[^A-Za-z0-9_.\-一-鿿]+", "_", label).strip("_")
    suffix = hashlib.sha1(label.encode("utf-8")).hexdigest()[:6]
    safe_label = f"{safe_label}-{suffix}" if safe_label else f"failure-{suffix}"
    directory = artifacts_dir / "screenshots"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{datetime.now():%Y%m%d-%H%M%S}-{safe_label}.png"
    try:
        await page.screenshot(path=path, full_page=True)
        return path
    except Exception:
        LOGGER.exception("保存截图失败")
        return None


def _write_results(
    artifacts_dir: Path,
    task_id: str,
    dry_run: bool,
    results: list[TargetResult],
    aliases: dict[str, str] | None = None,
    duration_seconds: float = 0.0,
) -> None:
    payload = {
        "task_id": task_id,
        "dry_run": dry_run,
        "finished_at": datetime.now().astimezone().isoformat(),
        "duration_seconds": round(duration_seconds, 1),
        "results": [_redacted_result(result, aliases) for result in results],
    }
    (artifacts_dir / "result.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _redacted_result(result: TargetResult, aliases: dict[str, str] | None = None) -> dict:
    aliases = dict(aliases or {})
    aliases[result.target] = result.target_alias or aliases.get(result.target, result.target)
    return {
        "target": aliases[result.target],
        "status": result.status,
        "sent": result.sent,
        "error": redact_text(result.error, aliases) if result.error else None,
    }


def _prune_artifacts(artifacts_dir: Path) -> None:
    """Delete failure screenshots and traces older than the retention window."""
    cutoff = time.time() - ARTIFACT_RETENTION.total_seconds()
    for pattern in ("screenshots/*.png", "traces/*.zip"):
        for path in artifacts_dir.glob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                LOGGER.warning("清理旧产物失败: %s", path.name)


async def _notify_dingtalk(
    settings: Settings,
    task_id: str,
    dry_run: bool,
    results: list[TargetResult],
    screenshots: list[Path],
) -> None:
    if not settings.dingtalk_webhook or not settings.dingtalk_secret:
        return
    try:
        await send_dingtalk_notification(
            settings.dingtalk_webhook,
            settings.dingtalk_secret,
            task_id,
            dry_run,
            results,
            screenshots,
        )
        LOGGER.info("钉钉通知发送成功")
    except Exception:
        LOGGER.exception("钉钉通知发送失败，不影响本次任务结果")


async def _notify_webhook(
    settings: Settings,
    task_id: str,
    dry_run: bool,
    results: list[TargetResult],
    screenshots: list[Path],
) -> None:
    if not settings.webhook_url:
        return
    try:
        await send_webhook_notification(
            settings.webhook_url,
            task_id,
            dry_run,
            results,
            screenshots,
            settings.webhook_template,
            settings.webhook_headers,
        )
        LOGGER.info("Webhook 通知发送成功")
    except Exception:
        LOGGER.exception("Webhook 通知发送失败，不影响本次任务结果")


def _trace_path(artifacts_dir: Path) -> Path:
    return artifacts_dir / "traces" / f"{datetime.now():%Y%m%d-%H%M%S}.zip"


def _message_id(index, message) -> str:
    payload = json.dumps(asdict(message), ensure_ascii=False, sort_keys=True, default=str)
    return f"{index}-{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:12]}"
