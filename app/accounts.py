from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from app.config import ConfigError

DEFAULT_ACCOUNTS_FILE = Path("config") / "accounts.json"


@dataclass(frozen=True)
class Account:
    """One multi-account entry. env_file resolves against the project root."""

    id: str
    enabled: bool
    env_file: Path


def load_accounts(path: Path = DEFAULT_ACCOUNTS_FILE) -> list[Account] | None:
    """Load the enabled accounts.

    Returns None without accounts.json (single-account mode) and [] when the
    file exists but enables no account.
    """
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"多账号配置不是有效 JSON: {path}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("accounts"), list):
        raise ConfigError(f"多账号配置缺少 accounts 数组: {path}")

    accounts: list[Account] = []
    seen: set[str] = set()
    for index, item in enumerate(raw["accounts"]):
        label = f"accounts[{index}]"
        if not isinstance(item, dict):
            raise ConfigError(f"{label} 必须是对象")
        account_id = item.get("id")
        if not isinstance(account_id, str) or not account_id.strip():
            raise ConfigError(f"{label}.id 必须是非空字符串")
        account_id = account_id.strip()
        if account_id in seen:
            raise ConfigError(f"账号 id 重复: {account_id}")
        seen.add(account_id)
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ConfigError(f"{label}.enabled 必须是布尔值")
        env_file = item.get("env_file")
        if not isinstance(env_file, str) or not env_file.strip():
            raise ConfigError(f"{label}.env_file 必须是非空字符串")
        accounts.append(Account(id=account_id, enabled=enabled, env_file=Path(env_file).expanduser()))
    return [account for account in accounts if account.enabled]
