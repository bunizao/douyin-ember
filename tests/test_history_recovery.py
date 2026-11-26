import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.history import AlreadyRunningError, History, run_lock


def test_history_rejects_non_object_json(tmp_path):
    path = tmp_path / 'history.json'
    path.write_text('[]')
    with pytest.raises(ValueError, match='发送历史损坏'):
        History(path)


@pytest.mark.skipif(os.name == 'nt', reason='POSIX process termination test')
def test_process_death_releases_lock_without_manual_cleanup(tmp_path):
    path = tmp_path / 'run.lock'
    code = '''
import sys, time
from pathlib import Path
from app.history import run_lock
with run_lock(Path(sys.argv[1])):
    print('locked', flush=True)
    time.sleep(60)
'''
    process = subprocess.Popen([sys.executable, '-c', code, str(path)], stdout=subprocess.PIPE, text=True)
    try:
        assert process.stdout.readline().strip() == 'locked'
        with pytest.raises(AlreadyRunningError):
            with run_lock(path):
                pass
        process.kill()
        process.wait(timeout=5)
        with run_lock(path):
            with pytest.raises(AlreadyRunningError):
                with run_lock(path):
                    pass
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
