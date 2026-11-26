"""Run one bounded task and record a durable status for the host timer."""

import fcntl
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONTAINER = "douyin-scheduled-run"


def ensure_image():
    configured = subprocess.run(
        ["docker", "compose", "--profile", "scheduled", "config", "--images", "douyin-ember"], cwd=BASE,
        capture_output=True, text=True, check=True, timeout=30,
    )
    images = set(configured.stdout.split())
    if len(images) != 1:
        raise RuntimeError("Expected exactly one configured runtime image")
    image = images.pop()
    inspect = ["docker", "image", "inspect", "--format", "{{.Id}}", image]
    current = subprocess.run(inspect, capture_output=True, text=True, timeout=30)
    if current.returncode == 0:
        manifest_path = BASE / "runtime-image.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text())
            if manifest["image"] == image and current.stdout.strip() != manifest["image_id"]:
                raise RuntimeError("Runtime image ID does not match the verified backup")
        return False

    # Keep recovery outside Docker's image store so pruning cannot remove it.
    manifest = json.loads((BASE / "runtime-image.json").read_text())
    if manifest["image"] != image:
        raise RuntimeError("Image backup does not match the configured runtime")
    archive = BASE / "runtime-image.tar.gz"
    with archive.open("rb") as stream:
        checksum = hashlib.file_digest(stream, "sha256").hexdigest()
    if checksum != manifest["sha256"]:
        raise RuntimeError("Image backup checksum mismatch")
    subprocess.run(
        ["docker", "image", "load", "--input", str(archive)],
        capture_output=True, check=True, timeout=120,
    )
    restored = subprocess.run(inspect, capture_output=True, text=True, check=True, timeout=30)
    if restored.stdout.strip() != manifest["image_id"]:
        raise RuntimeError("Restored image ID does not match the verified backup")
    return True


def write_status(status):
    path = BASE / "artifacts/scheduler-status.json"
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(status, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def run():
    if sys.argv[1:] not in ([], ["--dry-run"]):
        raise SystemExit("Only --dry-run is supported")
    dry_run = bool(sys.argv[1:])
    os.umask(0o077)
    artifacts = BASE / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    with (BASE / ".scheduler.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Another scheduled run holds the lock", flush=True)
            return 75
        # A failed previous run can leave a stopped container behind.
        subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=30)
        status = {"started_at": datetime.now(timezone.utc).isoformat(), "state": "running", "dry_run": dry_run}
        write_status(status)
        command = [
            "docker", "compose", "run", "--rm", "--no-deps", "--pull", "never", "-T", "--name", CONTAINER,
            "--entrypoint", "xvfb-run", "douyin-ember",
            "--auth-file=/tmp/douyin.xauth", "--server-num=99",
            "--server-args=-screen 0 1600x1200x24", "python", "run.py",
        ]
        if dry_run:
            command.append("--dry-run")
        try:
            status["image_restored"] = ensure_image()
            completed = subprocess.run(command, cwd=BASE, timeout=12 * 60)
            code = completed.returncode
            status.update(state="success" if code == 0 else "failed", exit_code=code)
        except subprocess.TimeoutExpired:
            code = 124
            status.update(state="timeout", exit_code=code)
        except Exception as exc:
            code = 1
            status.update(state="failed", error_type=type(exc).__name__, exit_code=code)
        finally:
            subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True, timeout=30)
            status["finished_at"] = datetime.now(timezone.utc).isoformat()
            stamp = status["started_at"].replace(":", "-")
            result_path = artifacts / "result.json"
            try:
                result = json.loads(result_path.read_text())
                if datetime.fromisoformat(result["finished_at"]) >= datetime.fromisoformat(status["started_at"]):
                    status["successful_targets"] = sum(item["status"] == "success" for item in result["results"])
                    status["failed_targets"] = sum(item["status"] == "failed" for item in result["results"])
                    status["messages_sent"] = sum(item["sent"] for item in result["results"])
                    (artifacts / f"result-{stamp}.json").write_text(json.dumps(result, indent=2) + "\n")
            except (OSError, ValueError, KeyError, TypeError):
                pass
            write_status(status)
            (artifacts / f"scheduler-{stamp}.json").write_text(json.dumps(status, indent=2) + "\n")
            cutoff = datetime.now(timezone.utc) - timedelta(days=30)
            for directory, pattern in ((artifacts, "scheduler-2*.json"), (artifacts, "result-2*.json"), (artifacts / "screenshots", "*.png"), (artifacts / "traces", "*.zip")):
                for path in directory.glob(pattern):
                    if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                        path.unlink()
        print(json.dumps(status), flush=True)
        return code


if __name__ == "__main__":
    raise SystemExit(run())
