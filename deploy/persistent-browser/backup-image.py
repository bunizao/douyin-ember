"""Save the configured runtime image outside Docker's pruneable image store."""

import fcntl
import gzip
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent


def main():
    os.umask(0o077)
    with (BASE / ".scheduler.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        configured = subprocess.run(
            ["docker", "compose", "--profile", "scheduled", "config", "--images", "douyin-auto-fire"], cwd=BASE,
            capture_output=True, text=True, check=True, timeout=30,
        )
        images = set(configured.stdout.split())
        if len(images) != 1:
            raise RuntimeError("Expected exactly one configured runtime image")
        image = images.pop()
        inspect = ["docker", "image", "inspect", "--format", "{{.Id}}", image]
        image_id = subprocess.check_output(inspect, text=True, timeout=30).strip()
        temporary = BASE / "runtime-image.tar.gz.tmp"
        try:
            with subprocess.Popen(["docker", "image", "save", image], stdout=subprocess.PIPE) as process:
                with gzip.open(temporary, "wb", compresslevel=1) as archive:
                    shutil.copyfileobj(process.stdout, archive, length=1024 * 1024)
                if process.wait() != 0:
                    raise RuntimeError("Docker image export failed")
            if subprocess.check_output(inspect, text=True, timeout=30).strip() != image_id:
                raise RuntimeError("Runtime image changed during backup")
            with temporary.open("rb") as stream:
                checksum = hashlib.file_digest(stream, "sha256").hexdigest()
                os.fsync(stream.fileno())
            manifest = {"image": image, "image_id": image_id, "sha256": checksum}
            manifest_tmp = BASE / "runtime-image.json.tmp"
            with manifest_tmp.open("w") as stream:
                json.dump(manifest, stream, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(BASE / "runtime-image.tar.gz")
            manifest_tmp.replace(BASE / "runtime-image.json")
        finally:
            temporary.unlink(missing_ok=True)
        print(json.dumps({"image": image, "image_id": image_id, "backup_saved": True}))


if __name__ == "__main__":
    main()
