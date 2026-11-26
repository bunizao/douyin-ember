"""Switch to the newest published image once a dry run passes, then drop older ones."""

import json
import os
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
REPOSITORY = "ghcr.io/bunizao/douyin-ember"
REVISION_LABEL = '{{index .Config.Labels "org.opencontainers.image.revision"}}'


def docker(*args: str, timeout: int = 60) -> str:
    return subprocess.run(
        ["docker", *args], cwd=BASE, capture_output=True, text=True, check=True, timeout=timeout,
    ).stdout.strip()


def configured_image() -> str:
    for line in (BASE / ".env").read_text().splitlines():
        if line.startswith("DOUYIN_IMAGE="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("DOUYIN_IMAGE is not set in .env")


def set_image(image: str) -> None:
    path = BASE / ".env"
    lines = [f"DOUYIN_IMAGE={image}" if line.startswith("DOUYIN_IMAGE=") else line for line in path.read_text().splitlines()]
    temporary = path.with_suffix(".tmp")
    temporary.write_text("\n".join(lines) + "\n")
    os.chmod(temporary, path.stat().st_mode)
    os.chown(temporary, path.stat().st_uid, path.stat().st_gid)
    temporary.replace(path)


def dry_run_passed() -> bool:
    """Accept a run that reached at least one friend; one missing friend must not block updates."""
    code = subprocess.run([sys.executable, str(BASE / "scheduled-run.py"), "--dry-run"], cwd=BASE, timeout=20 * 60).returncode
    if code == 0:
        return True
    if code != 1:
        return False
    status = json.loads((BASE / "artifacts/scheduler-status.json").read_text())
    return status.get("successful_targets", 0) > 0


def remove_other_images(keep: str) -> None:
    keep_id = docker("image", "inspect", "--format", "{{.Id}}", keep)
    for line in docker("image", "ls", REPOSITORY, "--format", "{{.Repository}}:{{.Tag}} {{.ID}}").splitlines():
        reference, short_id = line.split()
        if not keep_id.removeprefix("sha256:").startswith(short_id) and not reference.endswith(":<none>"):
            subprocess.run(["docker", "image", "rm", reference], capture_output=True, timeout=120)


def main() -> int:
    current = configured_image()
    docker("pull", "--quiet", f"{REPOSITORY}:latest", timeout=900)
    revision = docker("image", "inspect", "--format", REVISION_LABEL, f"{REPOSITORY}:latest")
    if not revision:
        raise RuntimeError("The latest image has no revision label")
    candidate = f"{REPOSITORY}:sha-{revision}"
    if candidate == current:
        remove_other_images(current)
        print(json.dumps({"image": current, "updated": False}), flush=True)
        return 0
    docker("tag", f"{REPOSITORY}:latest", candidate)
    set_image(candidate)
    try:
        passed = dry_run_passed()
    except Exception as exc:
        print(f"Dry run failed: {exc!r}", flush=True)
        passed = False
    if not passed:
        set_image(current)
        print(json.dumps({"image": current, "rejected": candidate}), flush=True)
        return 1
    subprocess.run([sys.executable, str(BASE / "backup-image.py")], cwd=BASE, check=True, timeout=30 * 60)
    remove_other_images(candidate)
    print(json.dumps({"image": candidate, "previous": current, "updated": True}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
