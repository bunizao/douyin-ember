import json
from pathlib import Path

import pytest

from app.config import ConfigError, discover_storage_state, load_settings, load_task, read_env
from app.models import Settings


def test_loads_persistent_profile_without_cookie(tmp_path) -> None:
    settings = load_settings({"BROWSER_USER_DATA_DIR": str(tmp_path / "profile")})

    assert settings.browser_user_data_dir == tmp_path / "profile"
    assert settings.cookie is None
    assert settings.storage_state is None


def test_rejects_importing_cookie_into_persistent_profile(tmp_path) -> None:
    with pytest.raises(ConfigError, match="BROWSER_USER_DATA_DIR"):
        load_settings({"BROWSER_USER_DATA_DIR": str(tmp_path / "profile"), "DOUYIN_COOKIE": "cookie.json"})


def test_storage_state_file_is_discovered_only_without_other_login(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "storage-state.json").write_text("{}", encoding="utf-8")

    assert discover_storage_state({})["DOUYIN_STORAGE_STATE"] == "storage-state.json"
    assert "DOUYIN_STORAGE_STATE" not in discover_storage_state({"DOUYIN_COOKIE": "cookie.json"})
    assert "DOUYIN_STORAGE_STATE" not in discover_storage_state({"BROWSER_USER_DATA_DIR": "profile"})


def test_read_env_prefers_process_over_file(monkeypatch, tmp_path) -> None:
    env_file = tmp_path / "custom.env"
    env_file.write_text("HEADLESS=false\nTASK_CONFIG=from-file.json\n", encoding="utf-8")
    monkeypatch.setenv("HEADLESS", "true")

    env = read_env(env_file)

    assert (env["HEADLESS"], env["TASK_CONFIG"]) == ("true", "from-file.json")


def test_read_env_rejects_missing_explicit_file(tmp_path) -> None:
    with pytest.raises(ConfigError, match="环境文件不存在"):
        read_env(tmp_path / "missing.env")


def settings_for(path: Path) -> Settings:
    return Settings(
        task_config_path=path,
        storage_state='{"cookies": [], "origins": []}',
        cookie=None,
        headless=True,
        browser_path=None,
        artifacts_dir=path.parent / "artifacts",
        trace=True,
    )


def write_config(tmp_path: Path, payload: dict) -> Path:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    path = config_dir / "tasks.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def test_loads_multiple_targets_and_text(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "targets": [
                {"name": "好友A", "messages": [{"type": "text", "content": "你好"}]},
                {"name": "好友B", "messages": [{"type": "text", "content": "早上好"}]},
            ]
        },
    )

    task = load_task(settings_for(path))

    assert [target.name for target in task.targets] == ["好友A", "好友B"]
    assert task.targets[0].messages[0].content == "你好"


def test_rejects_empty_targets(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"targets": []})

    with pytest.raises(ConfigError, match="targets 必须是非空数组"):
        load_task(settings_for(path))


def test_rejects_missing_image(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {"targets": [{"name": "好友A", "messages": [{"type": "image", "path": "data/missing.png"}]}]},
    )

    with pytest.raises(ConfigError, match="文件不存在"):
        load_task(settings_for(path))


def test_requires_sticker_mapping(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {"targets": [{"name": "好友A", "messages": [{"type": "douyin_sticker", "sticker": "未知"}]}]},
    )

    with pytest.raises(ConfigError, match="原生表情未在"):
        load_task(settings_for(path))


def test_loads_sticker_mapping(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {"targets": [{"name": "好友A", "messages": [{"type": "douyin_sticker", "sticker": "比心"}]}]},
    )
    (path.parent / "stickers.json").write_text(
        json.dumps({"比心": {"accessible_name": "比心", "fallback_index": 2}}, ensure_ascii=False),
        encoding="utf-8",
    )

    task = load_task(settings_for(path))

    assert task.stickers["比心"].fallback_index == 2


def test_loads_simple_config(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "friends": ["好友A", "好友B"],
            "messages": [{"type": "text", "value": "你好"}],
        },
    )

    task = load_task(settings_for(path))

    assert len(task.targets) == 2
    assert task.targets[1].messages[0].content == "你好"


def test_loads_bound_friends_with_shared_messages(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "friends": [{"name": "Old nickname", "conversation_id": "0:1:123:456"}, "Legacy friend"],
            "messages": [{"type": "text", "value": "Good morning"}],
        },
    )

    task = load_task(settings_for(path))

    assert [target.conversation_id for target in task.targets] == ["0:1:123:456", None]
    assert task.targets[0].messages == task.targets[1].messages


def test_rejects_duplicate_conversation_ids(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "friends": [
                {"name": "Old name", "conversation_id": "0:1:123:456"},
                {"name": "New name", "conversation_id": "0:1:123:456"},
            ],
            "messages": [{"type": "text", "value": "Good morning"}],
        },
    )

    with pytest.raises(ConfigError, match="Duplicate conversation_id"):
        load_task(settings_for(path))


def test_loads_target_open_retries_and_timeout(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "targets": [{"name": "好友A", "messages": [{"type": "text", "content": "你好"}]}],
            "target_open_retries": 3,
            "target_open_timeout_seconds": 20,
        },
    )

    task = load_task(settings_for(path))

    assert task.target_open_retries == 3
    assert task.target_open_timeout_seconds == 20


def test_defaults_target_open_retries_and_timeout(tmp_path: Path) -> None:
    path = write_config(tmp_path, {"targets": [{"name": "好友A", "messages": [{"type": "text", "content": "你好"}]}]})

    task = load_task(settings_for(path))

    assert task.target_open_retries == 1
    assert task.target_open_timeout_seconds == 15.0


def test_rejects_negative_target_open_retries(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "targets": [{"name": "好友A", "messages": [{"type": "text", "content": "你好"}]}],
            "target_open_retries": -1,
        },
    )

    with pytest.raises(ConfigError, match="target_open_retries"):
        load_task(settings_for(path))


def test_rejects_non_positive_target_open_timeout(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        {
            "targets": [{"name": "好友A", "messages": [{"type": "text", "content": "你好"}]}],
            "target_open_timeout_seconds": 0,
        },
    )

    with pytest.raises(ConfigError, match="target_open_timeout_seconds"):
        load_task(settings_for(path))
