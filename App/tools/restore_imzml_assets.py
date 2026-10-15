#!/usr/bin/env python3
"""★ ver81.0: 失われた固定imzML資産を診断し、同一hashを再現できる場合だけ復元する。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import input_preparation as ip  # noqa: E402
from app.services.imzml_asset_recovery import (  # noqa: E402
    pinned_entry as _pinned_entry, check_recoverable as _check_recoverable, recover_entry,
)
from app.services.section_group_metadata import load_result_manifest  # noqa: E402


_BACKUP_ACTION = (
    "保存時の変換資産一式をバックアップから同じパスへ戻してください。"
    "再変換する場合は、保存時と同じ原本・変換コード・依存ライブラリ・設定が必要です。"
    "このコマンドは解析結果や登録情報を書き換えません。"
)


def run(rds_path, *, file_ids=None, cache_root=None, restore=False):
    """診断は読み取り専用。restore=TrueでもRDS・条件記録・群編集は更新しない。"""
    rds = Path(rds_path).expanduser().resolve()
    if rds.suffix.lower() != ".rds" or not rds.is_file():
        raise ip.InputPreparationError("--rds に既存の解析結果 .rds ファイルを指定してください。")
    manifest = load_result_manifest(str(rds))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list) or not files:
        raise ip.InputPreparationError("解析結果に固定入力manifestがありません。")
    if not all(isinstance(entry, dict) for entry in files):
        raise ip.InputPreparationError("解析結果の固定入力manifestが不正です。")
    wanted = set(file_ids or [])
    if wanted:
        selected = [entry for entry in files if str(entry.get("file_id", "")) in wanted]
        if {str(entry.get("file_id", "")) for entry in selected} != wanted:
            raise ip.InputPreparationError("指定した --file-id が解析結果に登録されていません。")
        if any(Path(entry.get("path", "")).suffix.lower() != ".imzml" for entry in selected):
            raise ip.InputPreparationError("--file-id にはimzML入力のIDを指定してください。")
    else:
        selected = [entry for entry in files if entry.get("selection_mode") != "none"
                    and Path(entry.get("path", "")).suffix.lower() == ".imzml"]
    if not selected:
        raise ip.InputPreparationError("解析結果に対象のimzML入力がありません。")
    ids = [str(entry.get("file_id", "")) for entry in selected]
    if len(ids) != len(set(ids)):
        raise ip.InputPreparationError("固定入力manifestにfile_idの重複があります。")
    root = Path(cache_root).expanduser().resolve() if cache_root else ip.default_cache_root()
    report = {"schema": 1, "mode": "restore" if restore else "diagnose",
              "rds_path": str(rds), "cache_root": str(root), "files": []}
    for original in selected:
        item = {"file_id": original.get("file_id"), "source_path": original.get("path"),
                "runtime_path": original.get("runtime_path"), "restorable": False}
        report["files"].append(item)
        try:
            entry = _pinned_entry(original)
            try:
                ip.validate_asset(entry)
            except ip.InputPreparationError as exc:
                item["asset_error"] = str(exc)
            else:
                # 正常な固定資産は原本消失・環境更新後でもそのまま利用できる。
                item.update(status="healthy", action="復元は不要です。")
                continue
            if not restore:
                _check_recoverable(entry, root)
                item["restorable"] = True
                item.update(status="needs_restore", action="同じコマンドに --restore を付けて実行してください。")
                continue
            restored = recover_entry(entry, cache_root=root)
            ip.validate_asset(restored)
            if restored["validation"] != entry["validation"]:
                raise ip.InputPreparationError("復元後の検証記録が保存時と一致しません。")
            item.update(status="restored", restorable=True,
                        action="固定変換資産を復元しました。数値データの出力を再実行してください。")
        except (ip.InputPreparationError, OSError, ValueError, KeyError, TypeError) as exc:
            item.update(status="blocked", restorable=False, error=str(exc), action=_BACKUP_ACTION)
    report["ok"] = all(item["status"] in {"healthy", "restored"} for item in report["files"])
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rds", required=True, help="既存の解析結果 .rds ファイル")
    parser.add_argument("--file-id", action="append", help="対象の元ファイルID（繰り返し指定可）")
    parser.add_argument("--cache-root", help="固定資産の管理領域。保存パスの変更・移動は行いません")
    parser.add_argument("--restore", action="store_true", help="同一hashの資産を復元する（既定は診断のみ）")
    args = parser.parse_args(argv)
    try:
        report = run(args.rds, file_ids=args.file_id, cache_root=args.cache_root, restore=args.restore)
    except (ip.InputPreparationError, OSError, ValueError, KeyError, TypeError) as exc:
        report = {"schema": 1, "mode": "restore" if args.restore else "diagnose",
                  "ok": False, "error": str(exc), "action": _BACKUP_ACTION, "files": []}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
