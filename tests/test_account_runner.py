from pathlib import Path
from types import SimpleNamespace

import pytest

import app.account_runner as runner_module
from app.accounts import Account
from app.config import ConfigError


def _env_file(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def _account(account_id: str, env_file: Path) -> Account:
    return Account(id=account_id, enabled=True, env_file=env_file)


def _patch_runner(monkeypatch, tmp_path: Path, accounts, fake_run) -> list:
    """Run accounts against fake_run; return the settings each account received."""
    seen = []

    async def recording_run(settings, dry_run: bool = False) -> int:
        seen.append(settings)
        return await fake_run(settings)

    monkeypatch.chdir(tmp_path)
    for key in ("DOUYIN_COOKIE", "DOUYIN_STORAGE_STATE", "BROWSER_USER_DATA_DIR", "TASK_CONFIG", "ARTIFACTS_DIR"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(runner_module, "load_accounts", lambda: accounts)
    monkeypatch.setattr(runner_module, "_parse_cli_args", lambda: SimpleNamespace(dry_run=False, env_file=None))
    monkeypatch.setattr(runner_module, "run", recording_run)
    monkeypatch.setattr(runner_module, "_configure_logging", lambda *args, **kwargs: None)
    return seen


async def _ok(settings) -> int:
    return 0


def test_account_environment_layers_account_over_shared(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path, ".env.a", "DOUYIN_COOKIE=cookie-a\nHEADLESS=false\n")

    env = runner_module.account_environment(_account("a", env_file), {"HEADLESS": "true", "TZ": "UTC"})

    assert env == {"ARTIFACTS_DIR": "artifacts/a", "HEADLESS": "false", "TZ": "UTC", "DOUYIN_COOKIE": "cookie-a"}


def test_account_environment_explicit_artifacts_dir_wins(tmp_path: Path) -> None:
    env_file = _env_file(tmp_path, ".env.a", "ARTIFACTS_DIR=my/own/dir\n")

    assert runner_module.account_environment(_account("a", env_file), {})["ARTIFACTS_DIR"] == "my/own/dir"


def test_account_environment_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="环境文件不存在"):
        runner_module.account_environment(_account("a", tmp_path / ".env.missing"), {})


def test_shared_env_never_supplies_login_or_task(monkeypatch, tmp_path: Path) -> None:
    _env_file(tmp_path, ".env", "DOUYIN_STORAGE_STATE=root-state.json\nTASK_CONFIG=root.json\nHEADLESS=true\n")
    env_a = _env_file(tmp_path, ".env.a", "DOUYIN_COOKIE=cookie-a\nDINGTALK_WEBHOOK=hook-a\nDINGTALK_SECRET=s\n")
    env_b = _env_file(tmp_path, ".env.b", "DOUYIN_COOKIE=cookie-b\n")
    seen = _patch_runner(monkeypatch, tmp_path, [_account("a", env_a), _account("b", env_b)], _ok)
    monkeypatch.setenv("DOUYIN_COOKIE", "legacy-cookie")

    assert runner_module.run_all_accounts() == 0

    assert [(s.cookie, s.storage_state, s.headless) for s in seen] == [
        ("cookie-a", None, True),
        ("cookie-b", None, True),
    ]
    assert [s.task_config_path for s in seen] == [Path("config.json"), Path("config.json")]
    assert seen[1].dingtalk_webhook is None


def test_account_failure_does_not_block_others(monkeypatch, tmp_path: Path) -> None:
    env_a = _env_file(tmp_path, ".env.a", "DOUYIN_COOKIE=cookie-a\n")
    env_b = _env_file(tmp_path, ".env.b", "DOUYIN_COOKIE=cookie-b\n")

    async def fail_a(settings) -> int:
        if settings.cookie == "cookie-a":
            raise RuntimeError("账号A的Cookie失效")
        return 0

    seen = _patch_runner(monkeypatch, tmp_path, [_account("a", env_a), _account("b", env_b)], fail_a)

    assert runner_module.run_all_accounts() == 1
    assert [s.cookie for s in seen] == ["cookie-a", "cookie-b"]


def test_missing_env_file_fails_only_that_account(monkeypatch, tmp_path: Path) -> None:
    env_b = _env_file(tmp_path, ".env.b", "DOUYIN_COOKIE=cookie-b\n")
    seen = _patch_runner(
        monkeypatch, tmp_path, [_account("a", tmp_path / ".env.missing"), _account("b", env_b)], _ok
    )

    assert runner_module.run_all_accounts() == 1
    assert [s.cookie for s in seen] == ["cookie-b"]


def test_no_enabled_accounts_skips(monkeypatch, tmp_path: Path) -> None:
    seen = _patch_runner(monkeypatch, tmp_path, [], _ok)

    assert runner_module.run_all_accounts() == 0
    assert seen == []


def test_uses_per_account_artifacts_dir_and_lock(monkeypatch, tmp_path: Path) -> None:
    env_a = _env_file(tmp_path, ".env.a", "DOUYIN_COOKIE=cookie-a\n")
    seen = _patch_runner(monkeypatch, tmp_path, [_account("a", env_a)], _ok)

    assert runner_module.run_all_accounts() == 0
    assert seen[0].artifacts_dir == Path("artifacts") / "a"
    assert (tmp_path / "artifacts" / "a" / "run.lock").is_file()
