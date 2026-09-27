#!/usr/bin/env python3
"""実コンテナの mount と Compose 構成を使うバックアップ/復元（Python標準ライブラリ）。"""
from datetime import datetime, timezone
import argparse
import gzip
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
import time
import uuid

TARGETS = {f'msi-{name}': f'/app/Data/Other/{name}' for name in
           ('projects', 'sessions', 'presets', 'shares', 'common', 'output', 'logs')}


def docker(*args):
    return subprocess.run(['docker', *map(str, args)], check=True,
                          text=True, capture_output=True).stdout.strip()


def inspect_container():
    return json.loads(docker('inspect', os.environ.get('MSI_CONTAINER', 'msi-analysis-app')))[0]


def resolve_mount(info, name):
    matches = [m for m in info.get('Mounts', []) if m.get('Destination') == TARGETS.get(name)
               or (m.get('Name') and m['Name'] == name)]
    if len(matches) != 1 or matches[0].get('Type') not in {'volume', 'bind'}:
        raise ValueError(f'対象 mount を一意に解決できません: {name}')
    return matches[0]


def mount_arg(mount, destination, readonly=False):
    source = mount.get('Name') if mount['Type'] == 'volume' else mount['Source']
    if not source or ',' in source:
        raise ValueError('mount source が不正です')
    return f"type={mount['Type']},src={source},dst={destination}" + (',readonly' if readonly else '')


def compose_command(info):
    # ★ ver74.0: 復元時にprod設定を失わない。実コンテナの起動ラベルを正本とする。
    labels = info.get('Config', {}).get('Labels', {})
    files = labels.get('com.docker.compose.project.config_files', '').split(',')
    workdir = labels.get('com.docker.compose.project.working_dir', '')
    project = labels.get('com.docker.compose.project', '')
    service = labels.get('com.docker.compose.service', '')
    if not project or not workdir or not service or not all(files):
        raise ValueError('Compose起動情報が不足しています。停止/復元を開始しません。')
    args = ['compose', '--project-directory', workdir, '-p', project]
    for file in files:
        path = Path(file) if Path(file).is_absolute() else Path(workdir) / file
        if not path.is_file():
            raise ValueError(f'Compose設定を確認できません: {path}')
        args.extend(['-f', str(path)])
    return args, service


def validate_archive(path):
    # ★ ver74.0: 破損・外部参照は既存データ変更より前に拒否する。
    count = 0
    with tarfile.open(path, 'r:gz') as archive:
        for item in archive:
            if item.name.startswith('/') or '..' in PurePosixPath(item.name).parts or item.isdev() or item.isfifo():
                raise ValueError(f'安全に復元できないentry: {item.name}')
            if item.issym() or item.islnk():
                link = PurePosixPath(item.linkname)
                if link.is_absolute() or '..' in link.parts:
                    raise ValueError(f'外部参照link: {item.name}')
            if item.isfile():
                stream = archive.extractfile(item)
                while stream.read(1024 * 1024):
                    pass
            count += 1
    if not count:
        raise ValueError('空のarchiveです')
    # tar終端より後のgzip CRC/長さも確認し、末尾切れを完成品にしない。
    with gzip.open(path, 'rb') as stream:
        while stream.read(1024 * 1024):
            pass


def backup(directory, retention_days):
    info = inspect_container()
    mounts = [(name, resolve_mount(info, name)) for name in TARGETS]
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    created = []
    for name, mount in mounts:
        final = directory / f'{name}-{stamp}.tar.gz'
        temp = final.with_suffix(final.suffix + '.partial')
        try:
            docker('run', '--rm', '--mount', mount_arg(mount, '/data', True), '--mount',
                   mount_arg({'Type': 'bind', 'Source': str(directory)}, '/backup'),
                   'alpine', 'tar', 'czf', f'/backup/{temp.name}', '-C', '/data', '.')
            validate_archive(temp)
            temp.replace(final)
        finally:
            temp.unlink(missing_ok=True)
        created.append({'archive': final.name, 'mount': mount})
        print(f'OK {name}: {final}', flush=True)
    manifest = directory / f'backup-{stamp}.json'
    manifest.write_text(json.dumps({'schema': 1, 'mounts': created,
                                   'compose_labels': info.get('Config', {}).get('Labels', {})},
                                  ensure_ascii=False, indent=2), encoding='utf-8')
    # ★ ver74.0: 全skip/一部失敗は成功とせず、期限削除は全対象取得成功時だけ。
    cutoff = time.time() - retention_days * 86400
    for name, _ in mounts:
        for old in directory.glob(f'{name}-*.tar.gz'):
            if old.stat().st_mtime < cutoff:
                old.unlink()
    print(f'Backup complete: {len(created)} 件 / manifest={manifest}')


def copy_mount(source, destination, *, clear=False):
    command = 'cp -a /src/. /dest/'
    if clear:
        command = 'find /dest -mindepth 1 -maxdepth 1 -exec rm -rf -- {} + && ' + command
    docker('run', '--rm', '--mount', mount_arg(source, '/src', True), '--mount',
           mount_arg(destination, '/dest'), 'alpine', 'sh', '-ec', command)


def restore(name, archive, *, dry_run=False, assume_yes=False):
    archive = Path(archive).resolve(strict=True)
    validate_archive(archive)
    info = inspect_container()
    target = resolve_mount(info, name)
    compose, service = compose_command(info)
    container_id = info.get('Id')
    if not isinstance(container_id, str) or not container_id:
        raise ValueError('復元先containerの実IDがありません')
    print(json.dumps({'archive': str(archive), 'target': target, 'compose': compose}, ensure_ascii=False, indent=2))
    if dry_run:
        print('DRY-RUN: 事前展開 → 停止 → 旧内容保全 → 復元 → 同じ構成で起動')
        return
    if not assume_yes and input('旧内容を保全して置換します。続行しますか? (yes/[no]): ') != 'yes':
        print('キャンセルしました。')
        return
    suffix = uuid.uuid4().hex
    stage = {'Type': 'volume', 'Name': f'msi-restore-stage-{suffix}'}
    recovery = {'Type': 'volume', 'Name': f'msi-restore-recovery-{suffix}'}
    docker('volume', 'create', stage['Name'])
    try:
        docker('run', '--rm', '--mount', mount_arg(stage, '/data'), '--mount',
               mount_arg({'Type': 'bind', 'Source': str(archive.parent)}, '/backup', True),
               'alpine', 'tar', 'xzf', f'/backup/{archive.name}', '-C', '/data')
        # ★ ver74.0: compose upは変更後の.env/image/mountで再作成し得る。
        # inspectした同じcontainerを停止/再開し、稼働時の実構成を保持する。
        docker('stop', container_id)
        try:
            docker('volume', 'create', recovery['Name'])
            copy_mount(target, recovery)
        except Exception:
            docker('start', container_id)
            raise
        print(f"復旧用の旧内容を保持: {recovery['Name']}", flush=True)
        try:
            # ★ ver74.0: 旧volumeは削除しない。失敗時は保全copyから戻す。
            copy_mount(stage, target, clear=True)
        except Exception:
            copy_mount(recovery, target, clear=True)
            docker('start', container_id)
            raise
        docker('start', container_id)
        print(f"Restore complete. 旧内容は {recovery['Name']} に保持しています。")
    finally:
        docker('volume', 'rm', stage['Name'])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['backup', 'restore'])
    parser.add_argument('volume', nargs='?')
    parser.add_argument('archive', nargs='?')
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--list', action='store_true')
    args = parser.parse_args(argv)
    directory = Path(os.environ.get('BACKUP_DIR', './backups'))
    try:
        if args.list:
            for path in sorted(directory.glob('*.tar.gz')):
                print(path)
        elif args.action == 'backup':
            retention = int(os.environ.get('RETENTION_DAYS', '30'))
            if retention < 1:
                raise ValueError('RETENTION_DAYSは1以上が必要です')
            backup(directory, retention)
        else:
            if not args.volume or not args.archive:
                parser.error('restoreにはvolumeとarchiveが必要です')
            archive = Path(args.archive)
            if not archive.is_file():
                archive = directory / archive
            restore(args.volume, archive, dry_run=args.dry_run, assume_yes=args.yes)
        return 0
    except (OSError, ValueError, tarfile.TarError, subprocess.CalledProcessError) as exc:
        print(f'失敗: {exc}', file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
