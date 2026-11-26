import importlib.util
import json
from pathlib import Path

REPOSITORY = "ghcr.io/bunizao/douyin-ember"


def load_updater(monkeypatch, tmp_path, *, revision="abc123", dry_run_code=0, status=None):
    path = Path(__file__).resolve().parents[1] / "deploy/persistent-browser/update-image.py"
    spec = importlib.util.spec_from_file_location("update_image", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "BASE", tmp_path)
    (tmp_path / ".env").write_text(f"TZ=Asia/Shanghai\nDOUYIN_IMAGE={REPOSITORY}:sha-old\n")
    (tmp_path / "artifacts").mkdir()
    if status is not None:
        (tmp_path / "artifacts/scheduler-status.json").write_text(json.dumps(status))
    calls = []

    def docker(*args, timeout=60):
        calls.append(("docker", *args))
        return revision if args[:2] == ("image", "inspect") and "revision" in args[3] else ""

    def run(command, **kwargs):
        calls.append(tuple(str(part) for part in command))
        code = dry_run_code if "--dry-run" in command else 0
        return type("Completed", (), {"returncode": code, "stdout": ""})()

    monkeypatch.setattr(module, "docker", docker)
    monkeypatch.setattr(module, "remove_other_images", lambda keep: calls.append(("remove", keep)))
    monkeypatch.setattr(module.subprocess, "run", run)
    return module, calls


def image(tmp_path):
    return (tmp_path / ".env").read_text().split("DOUYIN_IMAGE=")[1].strip()


def test_unchanged_image_skips_the_dry_run(monkeypatch, tmp_path):
    module, calls = load_updater(monkeypatch, tmp_path, revision="old")

    assert module.main() == 0
    assert not any("--dry-run" in call for call in calls)
    assert ("remove", f"{REPOSITORY}:sha-old") in calls


def test_new_image_is_kept_after_a_dry_run_with_one_missing_friend(monkeypatch, tmp_path):
    module, calls = load_updater(monkeypatch, tmp_path, dry_run_code=1, status={"successful_targets": 11})

    assert module.main() == 0
    assert image(tmp_path) == f"{REPOSITORY}:sha-abc123"
    assert any(call[-1].endswith("backup-image.py") for call in calls)
    assert ("remove", f"{REPOSITORY}:sha-abc123") in calls


def test_failed_dry_run_restores_the_previous_image(monkeypatch, tmp_path):
    module, calls = load_updater(monkeypatch, tmp_path, dry_run_code=2)

    assert module.main() == 1
    assert image(tmp_path) == f"{REPOSITORY}:sha-old"
    assert not any(call[-1].endswith("backup-image.py") for call in calls)
    assert not any(call[0] == "remove" for call in calls)
