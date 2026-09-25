#!/usr/bin/env python3
from __future__ import annotations
import base64, gzip, hashlib, json, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
MANIFEST = json.loads(gzip.decompress(base64.b64decode((ROOT / 'manifest.txt').read_text(encoding='ascii').strip())).decode('utf-8'))
BASE = MANIFEST['target_base_commit']
FINAL_BRANCH = 'claude/imzml-spatial-sections-ver71-0'
PATCH = Path('/tmp/imzml-spatial-v71.patch')


def run(*args: str, check: bool = True) -> str:
    p = subprocess.run(args, cwd=REPO, text=True, capture_output=True)
    if check and p.returncode:
        raise SystemExit((p.stdout + '\n' + p.stderr).strip())
    return p.stdout.rstrip()

parts = sorted(ROOT.glob('part*.txt'))
if not parts:
    raise SystemExit('patch chunks are missing')
encoded = ''.join(p.read_text(encoding='ascii').strip() for p in parts)
PATCH.write_bytes(gzip.decompress(base64.b64decode(encoded)))
actual_sha = hashlib.sha256(PATCH.read_bytes()).hexdigest()
if actual_sha != MANIFEST['patch_sha256']:
    raise SystemExit(f'patch SHA256 mismatch: {actual_sha}')

run('git', 'fetch', 'origin', 'main')
main_sha = run('git', 'rev-parse', 'origin/main')
if main_sha != BASE:
    raise SystemExit(f'main changed: expected {BASE}, got {main_sha}')
exists = subprocess.run(['git', 'ls-remote', '--exit-code', '--heads', 'origin', FINAL_BRANCH],
                        cwd=REPO, text=True, capture_output=True)
if exists.returncode == 0:
    raise SystemExit(f'remote branch already exists: {FINAL_BRANCH}')

run('git', 'checkout', '-B', FINAL_BRANCH, 'origin/main')
expected = {item['path']: item for item in MANIFEST['files']}
base_blobs = {}
base_mismatches = []
for name, item in expected.items():
    probe = subprocess.run(['git', 'rev-parse', f'HEAD:{name}'], cwd=REPO,
                           text=True, capture_output=True)
    actual = probe.stdout.strip() if probe.returncode == 0 else None
    base_blobs[name] = actual
    if actual != item.get('base_blob'):
        base_mismatches.append({
            'path': name,
            'package_base_blob': item.get('base_blob'),
            'main_base_blob': actual,
        })

run('git', 'apply', '--check', '--whitespace=error-all', str(PATCH))
run('git', 'apply', '--whitespace=error-all', str(PATCH))
run('git', 'diff', '--check')

changed_paths = set()
for line in run('git', 'status', '--porcelain').splitlines():
    path = line[3:]
    if ' -> ' in path:
        path = path.split(' -> ', 1)[1]
    changed_paths.add(path)
if changed_paths != set(expected):
    missing = sorted(set(expected) - changed_paths)
    extra = sorted(changed_paths - set(expected))
    raise SystemExit(f'changed path mismatch; missing={missing}; extra={extra}')

strict_verified = 0
for name, item in expected.items():
    path = REPO / name
    if not path.is_file():
        raise SystemExit(f'missing result file: {name}')
    blob = run('git', 'hash-object', f'--path={name}', str(path))
    if base_blobs[name] == item.get('base_blob'):
        if blob != item['result_blob']:
            raise SystemExit(f'result blob mismatch on matching base: {name}: {blob} != {item["result_blob"]}')
        strict_verified += 1

print(json.dumps({
    'base': BASE,
    'branch': FINAL_BRANCH,
    'changed_files': len(expected),
    'patch_sha256': actual_sha,
    'strict_result_blobs_verified': strict_verified,
    'main_preserved_base_mismatches': base_mismatches,
}, ensure_ascii=False, indent=2))
