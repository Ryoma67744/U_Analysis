"""★ ver74.0: Dockerを記録mock化し、破損/失敗で旧データを削除しない順序を検証。"""
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import pytest
spec = importlib.util.spec_from_file_location('volume_backup', Path(__file__).parents[1] / 'tools/volume_backup.py')
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def archive(path, name='file'):
    with tarfile.open(path, 'w:gz') as output:
        entry = tarfile.TarInfo(name)
        entry.size = 8
        output.addfile(entry, io.BytesIO(b'original'))
    return path


def container(tmp_path):
    files = [tmp_path / 'docker-compose.yml', tmp_path / 'docker-compose.prod.yml']
    for path in files:
        path.write_text('services: {}')
    labels = {'com.docker.compose.project': 'lab', 'com.docker.compose.project.working_dir': str(tmp_path),
              'com.docker.compose.project.config_files': ','.join(map(str, files)), 'com.docker.compose.service': 'msi-app'}
    return {'Id': 'existing-container-id', 'Config': {'Labels': labels}, 'Mounts': [
        {'Type': 'volume', 'Name': f'lab_{name}', 'Destination': dest} for name, dest in backup.TARGETS.items()]}


def test_no_mount_backup_fails_without_pruning(tmp_path, monkeypatch):
    old = archive(tmp_path / 'msi-projects-old.tar.gz')
    os.utime(old, (1, 1))
    monkeypatch.setattr(backup, 'inspect_container', lambda: {'Mounts': []})
    with pytest.raises(ValueError):
        backup.backup(tmp_path, 1)
    assert old.is_file()


@pytest.mark.parametrize('imzml_bind', [False, True])
def test_complete_backup_uses_actual_mounts_and_bind(tmp_path, monkeypatch, imzml_bind):
    info = container(tmp_path)
    index = next(i for i, mount in enumerate(info['Mounts'])
                 if mount['Destination'] == backup.TARGETS['msi-logs'])
    info['Mounts'][index] = {'Type': 'bind', 'Source': str(tmp_path / 'logs'), 'Destination': backup.TARGETS['msi-logs']}
    if imzml_bind:
        index = next(i for i, mount in enumerate(info['Mounts'])
                     if mount['Destination'] == backup.TARGETS['msi-imzml-assets'])
        info['Mounts'][index] = {'Type': 'bind', 'Source': str(tmp_path / 'imzml-assets'),
                                 'Destination': backup.TARGETS['msi-imzml-assets']}
    monkeypatch.setattr(backup, 'inspect_container', lambda: info)
    calls = []
    def docker(*args):
        calls.append(args)
        archive(tmp_path / Path(next(arg for arg in args if arg.startswith('/backup/'))).name)
        return ''
    monkeypatch.setattr(backup, 'docker', docker)
    backup.backup(tmp_path, 30)
    assert len(list(tmp_path.glob('*.tar.gz'))) == len(backup.TARGETS)
    assert any('src=lab_msi-projects' in str(call) for call in calls)
    assert any(f'type=bind,src={tmp_path}/logs' in str(call) for call in calls)
    expected_imzml = (f'type=bind,src={tmp_path}/imzml-assets' if imzml_bind
                      else 'type=volume,src=lab_msi-imzml-assets')
    assert any(expected_imzml in str(call) for call in calls)
    saved = json.loads(next(tmp_path.glob('backup-*.json')).read_text())['mounts']
    assert len(saved) == 8
    assert any(item['archive'].startswith('msi-imzml-assets-')
               and item['mount']['Destination'] == '/app/Data/Other/imzml_assets' for item in saved)


def test_restore_absolute_bind_prod_and_no_old_volume_delete(tmp_path, monkeypatch):
    source = archive(tmp_path / 'backup with space.tar.gz')
    info = container(tmp_path)
    monkeypatch.setattr(backup, 'inspect_container', lambda: info)
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(backup, 'docker', lambda *args: calls.append(args) or '')
    backup.restore('msi-projects', source.name, assume_yes=True)
    extract = next(call for call in calls if 'xzf' in call)
    assert f'type=bind,src={tmp_path},dst=/backup,readonly' in extract
    stop = next(call for call in calls if 'stop' in call)
    start = next(call for call in calls if 'start' in call)
    assert str(tmp_path / 'docker-compose.prod.yml') in backup.compose_command(info)[0]
    assert stop == ('stop', info['Id']) and start == ('start', info['Id'])
    assert not any('up' in call for call in calls)
    assert calls.index(extract) < calls.index(stop)
    assert not any(call[:3] == ('volume', 'rm', 'lab_msi-projects') for call in calls)
    assert not any(call[:2] == ('volume', 'rm') and 'recovery' in call[2] for call in calls)


def test_traversal_archive_does_not_call_docker(tmp_path, monkeypatch):
    path = archive(tmp_path / 'bad.tar.gz', '../escape')
    calls = []
    monkeypatch.setattr(backup, 'docker', lambda *args: calls.append(args))
    with pytest.raises(ValueError):
        backup.restore('msi-projects', path, assume_yes=True)
    assert calls == []


def test_restore_copy_failure_rolls_back_before_restart(tmp_path, monkeypatch):
    path = archive(tmp_path / 'ok.tar.gz')
    monkeypatch.setattr(backup, 'inspect_container', lambda: container(tmp_path))
    calls = []
    def docker(*args):
        calls.append(args)
        if 'sh' in args and 'rm -rf' in args[-1] and any('src=msi-restore-stage-' in str(arg) for arg in args):
            raise subprocess.CalledProcessError(1, 'copy')
        return ''
    monkeypatch.setattr(backup, 'docker', docker)
    with pytest.raises(subprocess.CalledProcessError):
        backup.restore('msi-projects', path, assume_yes=True)
    rollback = next(i for i, call in enumerate(calls) if 'sh' in call and 'rm -rf' in call[-1]
                    and any('src=msi-restore-recovery-' in str(arg) for arg in call))
    restart = next(i for i, call in enumerate(calls) if 'start' in call)
    assert rollback < restart
    assert not any('up' in call for call in calls)


def test_fixed_imzml_assets_are_backup_target():
    # ★ ver75.2: hyphen付きvolume名からunderscore付き保存先を推測しない。
    assert backup.TARGETS.get('msi-imzml-assets') == '/app/Data/Other/imzml_assets'


def test_missing_imzml_mount_aborts_before_backup_or_pruning(tmp_path, monkeypatch):
    # ★ ver75.2: 旧コンテナは必須資産が非永続。成功扱いで黙って欠落させない。
    info = container(tmp_path)
    info['Mounts'] = [mount for mount in info['Mounts']
                      if mount['Destination'] != '/app/Data/Other/imzml_assets']
    old = archive(tmp_path / 'msi-projects-old.tar.gz')
    os.utime(old, (1, 1))
    monkeypatch.setattr(backup, 'inspect_container', lambda: info)
    monkeypatch.setattr(backup, 'docker', lambda *args: pytest.fail('事前検証前にDockerを呼ばない'))
    with pytest.raises(ValueError, match='msi-imzml-assets'):
        backup.backup(tmp_path, 1)
    assert old.is_file()
    assert not list(tmp_path.glob('backup-*.json'))


def test_restore_imzml_assets_uses_inspected_named_mount(tmp_path, monkeypatch):
    path = archive(tmp_path / 'imzml-assets.tar.gz')
    info = container(tmp_path)
    monkeypatch.setattr(backup, 'inspect_container', lambda: info)
    calls = []
    monkeypatch.setattr(backup, 'docker', lambda *args: calls.append(args) or '')
    backup.restore('msi-imzml-assets', path, assume_yes=True)
    assert any('type=volume,src=lab_msi-imzml-assets,dst=/src,readonly' in call for call in calls)
    assert any('type=volume,src=lab_msi-imzml-assets,dst=/dest' in call for call in calls)
