from __future__ import annotations

import asyncio
from pathlib import Path

from dotenv import dotenv_values

from app.accounts import Account, load_accounts
from app.config import ConfigError, load_settings, read_env
from app.history import run_lock
from app.main import LOGGER, _configure_logging, _parse_cli_args, run


# Per-account keys. The shared environment never provides them, so one
# account cannot inherit another account's login, task or history.
ACCOUNT_ONLY_KEYS = (
    "DOUYIN_COOKIE",
    "DOUYIN_STORAGE_STATE",
    "BROWSER_USER_DATA_DIR",
    "TASK_CONFIG",
    "ARTIFACTS_DIR",
)


def run_all_accounts() -> int:
    """Run every enabled account in config/accounts.json in sequence.

    One account failing never stops the others. Exit codes: 0 when all
    succeed, 1 when any fails.
    """
    args = _parse_cli_args()
    accounts = load_accounts()
    if not accounts:
        print("没有启用任何账号，本次任务跳过")
        return 0
    shared = {key: value for key, value in read_env(args.env_file).items() if key not in ACCOUNT_ONLY_KEYS}

    _configure_logging(Path("artifacts"), label=None, reset=True)
    LOGGER.info("多账号模式：共 %d 个启用账号", len(accounts))

    summary: list[tuple[str, str, str | None]] = []
    for account in accounts:
        # Log under the default directory first so a broken env file is still
        # reported with the account prefix.
        _configure_logging(Path("artifacts") / account.id, label=account.id, reset=True)
        LOGGER.info("开始执行任务")
        try:
            settings = load_settings(account_environment(account, shared))
            _configure_logging(settings.artifacts_dir, label=account.id, reset=True)
            with run_lock(settings.artifacts_dir / "run.lock"):
                code = asyncio.run(run(settings, dry_run=args.dry_run))
            status = "success" if code == 0 else "failed"
            summary.append((account.id, status, None))
            LOGGER.info("执行完成: %s", status)
        except Exception as exc:
            # Exception text can carry a friend's real name; run() already
            # wrote the redacted details to the account's run.log.
            summary.append((account.id, "failed", type(exc).__name__))
            LOGGER.exception("执行失败: %s", exc)

    _configure_logging(Path("artifacts"), label=None, reset=True)
    for account_id, status, error in summary:
        detail = f" - {error}" if error else ""
        LOGGER.info("[%s] 结果: %s%s", account_id, status, detail)
    failed = sum(1 for _, status, _ in summary if status == "failed")
    LOGGER.info("多账号执行结束: 成功 %d，失败 %d", len(summary) - failed, failed)
    return 1 if failed else 0


def account_environment(account: Account, shared: dict[str, str]) -> dict[str, str]:
    """Shared settings overlaid with the account's env file."""
    if not account.env_file.is_file():
        raise ConfigError(f"账号环境文件不存在: {account.env_file}")
    values = {key: value for key, value in dotenv_values(account.env_file).items() if value is not None}
    return {"ARTIFACTS_DIR": f"artifacts/{account.id}", **shared, **values}
