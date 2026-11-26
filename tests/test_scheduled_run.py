import importlib.util
import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest


def load_runner(monkeypatch, tmp_path):
    path = Path(__file__).resolve().parents[1] / 'deploy/persistent-browser/scheduled-run.py'
    spec = importlib.util.spec_from_file_location('scheduled_run', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'BASE', tmp_path)
    monkeypatch.setattr(module.sys, 'argv', ['scheduled-run.py'])
    return module


def image_backup(tmp_path, image='douyin:test', image_id='sha256:verified'):
    archive = tmp_path / 'runtime-image.tar.gz'
    archive.write_bytes(b'verified image archive')
    manifest = {
        'image': image,
        'image_id': image_id,
        'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(),
    }
    (tmp_path / 'runtime-image.json').write_text(json.dumps(manifest))


@pytest.mark.parametrize('dry_run', [False, True])
def test_missing_image_is_restored_before_running_sender(monkeypatch, tmp_path, dry_run):
    module = load_runner(monkeypatch, tmp_path)
    if dry_run:
        monkeypatch.setattr(module.sys, 'argv', ['scheduled-run.py', '--dry-run'])
    image_backup(tmp_path)
    calls = []
    restored = False

    def execute(command, **kwargs):
        nonlocal restored
        calls.append(command)
        if command == ['docker', 'compose', '--profile', 'scheduled', 'config', '--images', 'douyin-ember']:
            return subprocess.CompletedProcess(command, 0, 'douyin:test\n')
        if command[:3] == ['docker', 'image', 'inspect']:
            return subprocess.CompletedProcess(command, 0 if restored else 1, 'sha256:verified\n' if restored else '')
        if command[:3] == ['docker', 'image', 'load']:
            restored = True
        if command[:3] == ['docker', 'compose', 'run']:
            assert restored
            assert command[command.index('--pull') + 1] == 'never'
            assert ('--dry-run' in command) == dry_run
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, 'run', execute)
    monkeypatch.setattr(module.os, 'umask', Mock())
    assert module.run() == 0
    status = json.loads((tmp_path / 'artifacts/scheduler-status.json').read_text())
    assert status['image_restored'] is True
    assert status['dry_run'] is dry_run
    assert any(c[:3] == ['docker', 'image', 'load'] for c in calls)


def test_existing_image_does_not_require_backup(monkeypatch, tmp_path):
    module = load_runner(monkeypatch, tmp_path)
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, 'douyin:test\n')

    monkeypatch.setattr(module.subprocess, 'run', execute)
    assert module.ensure_image() is False
    assert len(calls) == 2
    assert not (tmp_path / 'runtime-image.tar.gz').exists()


@pytest.mark.parametrize('failure', ['missing_backup', 'wrong_image', 'corrupt_archive', 'wrong_loaded_image'])
def test_invalid_recovery_never_starts_sender(monkeypatch, tmp_path, failure):
    module = load_runner(monkeypatch, tmp_path)
    if failure != 'missing_backup':
        image_backup(tmp_path, image='other:test' if failure == 'wrong_image' else 'douyin:test')
    if failure == 'corrupt_archive':
        (tmp_path / 'runtime-image.tar.gz').write_bytes(b'corrupt')
    calls = []
    restored = False

    def execute(command, **kwargs):
        nonlocal restored
        calls.append(command)
        if command == ['docker', 'compose', '--profile', 'scheduled', 'config', '--images', 'douyin-ember']:
            return subprocess.CompletedProcess(command, 0, 'douyin:test\n')
        if command[:3] == ['docker', 'image', 'inspect']:
            return subprocess.CompletedProcess(command, 0 if restored else 1, 'sha256:unexpected\n' if restored else '')
        if command[:3] == ['docker', 'image', 'load']:
            restored = True
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, 'run', execute)
    monkeypatch.setattr(module.os, 'umask', Mock())
    assert module.run() == 1
    status = json.loads((tmp_path / 'artifacts/scheduler-status.json').read_text())
    assert status['state'] == 'failed'
    assert module.run() == 1
    assert not any(c[:3] == ['docker', 'compose', 'run'] for c in calls)


@pytest.mark.parametrize('exit_code', [0, 2])
def test_scheduled_exit_is_recorded_and_container_cleaned(monkeypatch, tmp_path, exit_code):
    module = load_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(module, 'ensure_image', lambda: False)
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, exit_code if command[:2] == ['docker', 'compose'] else 0)

    monkeypatch.setattr(module.subprocess, 'run', execute)
    monkeypatch.setattr(module.os, 'umask', Mock())
    assert module.run() == exit_code
    status = json.loads((tmp_path / 'artifacts/scheduler-status.json').read_text())
    assert status['state'] == ('success' if exit_code == 0 else 'failed')
    assert status['exit_code'] == exit_code
    assert calls[-1] == ['docker', 'rm', '-f', module.CONTAINER]
    assert len(list((tmp_path / 'artifacts').glob('scheduler-2*.json'))) == 1


def test_timeout_cleans_detached_browser_container_and_records_failure(monkeypatch, tmp_path):
    module = load_runner(monkeypatch, tmp_path)
    monkeypatch.setattr(module, 'ensure_image', lambda: False)
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        if command[:2] == ['docker', 'compose']:
            assert kwargs['timeout'] == 720
            raise subprocess.TimeoutExpired(command, kwargs['timeout'])
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module.subprocess, 'run', execute)
    monkeypatch.setattr(module.os, 'umask', Mock())
    assert module.run() == 124
    status = json.loads((tmp_path / 'artifacts/scheduler-status.json').read_text())
    assert status['state'] == 'timeout'
    assert status['finished_at']
    assert calls[-1] == ['docker', 'rm', '-f', module.CONTAINER]
