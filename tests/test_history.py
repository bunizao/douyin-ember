from pathlib import Path

import pytest

from app.history import AlreadyRunningError, History, run_date, run_lock


def test_history_persists_success(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    history = History(path)
    key = history.key("task", run_date("Asia/Shanghai"), "好友A", "0-abc")

    history.mark_success(key)

    assert History(path).contains(key)


def test_corrupt_history_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text("{broken", encoding="utf-8")

    with pytest.raises(ValueError, match="发送历史损坏"):
        History(path)


def test_run_lock_rejects_second_process(tmp_path: Path) -> None:
    path = tmp_path / "run.lock"

    with run_lock(path):
        with pytest.raises(AlreadyRunningError):
            with run_lock(path):
                pass

    with run_lock(path):
        assert path.exists()


def test_history_drops_entries_older_than_a_week(tmp_path: Path) -> None:
    import json
    from datetime import datetime, timedelta

    path = tmp_path / "history.json"
    now = datetime.now().astimezone()
    path.write_text(json.dumps({
        "old": {"status": "success", "finished_at": (now - timedelta(days=8)).isoformat()},
        "recent": {"status": "unknown", "started_at": (now - timedelta(days=1)).isoformat()},
        "unstamped": {"status": "success"},
    }), encoding="utf-8")

    history = History(path)

    assert set(history.entries) == {"recent", "unstamped"}
