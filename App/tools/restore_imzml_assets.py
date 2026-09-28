#!/usr/bin/env python3
"""★ ver75.2: 失われた固定imzML資産を診断し、同一hashを再現できる場合だけ復元する。"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services import input_preparation as ip  # noqa: E402
from app.services.imzml_registration import build_registration_spec  # noqa: E402
from app.services.section_group_metadata import load_result_manifest  # noqa: E402


_SPEC_KEYS = {
    "contract", "schema", "dependencies", "converter_sha256",
    "processed_converter_sha256", "validator_sha256", "spatial_layout_sha256",
    "registration_sha256", "parquet_contract_sha256", "processed_alignment_ppm",
    "registration_hash", "intensity_dtype", "mz_name_decimals",
    "all_sections_preserved", "block_size", "memory_budget_mb", "processed_max_features",
}
_BACKUP_ACTION = (
    "保存時の変換資産一式をバックアップから同じパスへ戻してください。"
    "再変換する場合は、保存時と同じ原本・変換コード・依存ライブラリ・設定が必要です。"
    "このコマンドは解析結果や登録情報を書き換えません。"
)


def _digest_record(value):
    return (
        isinstance(value, dict)
        and isinstance(value.get("size"), int)
        and not isinstance(value["size"], bool)
        and value["size"] >= 0
        and isinstance(value.get("sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None
    )


def _pinned_entry(entry):
    """不完全な記録を、新規変換の入力として補完しない。"""
    if not isinstance(entry, dict):
        raise ip.InputPreparationError("固定入力の記録が不正です。")
    if entry.get("registration_revision_requested"):
        raise ip.InputPreparationError("登録revisionの変更要求があり、旧資産の復元として実行できません。")
    for key in ("path", "file_id", "runtime_path", "conversion_manifest_path", "conversion_key"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise ip.InputPreparationError(f"保存済み固定descriptorに {key} がありません。")
    if not re.fullmatch(r"[0-9a-f]{64}", entry["conversion_key"]):
        raise ip.InputPreparationError("保存済みconversion_keyが不正です。")
    for key in ("path", "runtime_path", "conversion_manifest_path"):
        if not Path(entry[key]).is_absolute():
            raise ip.InputPreparationError(f"保存済み {key} が絶対パスではありません。")
    runtime = Path(entry["runtime_path"])
    sidecar = Path(entry["conversion_manifest_path"])
    if runtime.suffix.lower() != ".parquet" or sidecar != runtime.with_suffix(".imzml.json"):
        raise ip.InputPreparationError("保存済み変換ファイルの組み合わせが不正です。")
    source = entry.get("source_fingerprint")
    if not isinstance(source, dict) or any(
        not _digest_record(source.get(kind)) or not source[kind].get("path")
        for kind in ("xml", "ibd")
    ):
        raise ip.InputPreparationError("保存済み原本fingerprintが不足しています。")
    spec = entry.get("conversion_spec")
    if not isinstance(spec, dict) or _SPEC_KEYS - set(spec):
        raise ip.InputPreparationError("保存済み変換仕様が不足しています。")
    try:
        ppm = float(spec["processed_alignment_ppm"])
    except (TypeError, ValueError) as exc:
        raise ip.InputPreparationError("保存済みm/zアライメント条件が不正です。") from exc
    if not math.isfinite(ppm) or ppm < 0:
        raise ip.InputPreparationError("保存済みm/zアライメント条件が不正です。")
    validation = entry.get("validation")
    if not isinstance(validation, dict) or any(
        not _digest_record(validation.get(kind)) for kind in ("parquet", "manifest")
    ) or not isinstance(validation.get("summary"), dict) or not validation["summary"]:
        raise ip.InputPreparationError("保存済み変換出力の検証記録が不足しています。")
    if not isinstance(entry.get("registered_sections"), list) or not entry["registered_sections"]:
        raise ip.InputPreparationError("保存時の全切片登録情報がありません。")

    result = deepcopy(entry)
    # ★ ver75.2: 群編集overlayを再変換へ混ぜると保存時のhashを再現できない。
    # 元registryだけで復元し、結果側のoverlayは読み取り専用で保持する。
    result.pop("metadata_overlay", None)
    return result


def _check_recoverable(entry, cache_root):
    target = Path(entry["runtime_path"]).resolve().parent
    if not target.is_relative_to(cache_root):
        raise ip.InputPreparationError(
            "復元先が現在の変換資産管理領域外です。保存時のマウントを確認し、"
            "必要な場合だけ --cache-root で管理領域を指定してください。"
        )
    xml, ibd = ip.pair_paths(entry["path"])
    source = {"xml": ip.fingerprint(xml), "ibd": ip.fingerprint(ibd)}
    for kind in ("xml", "ibd"):
        saved = entry["source_fingerprint"][kind]
        if any(source[kind][key] != saved[key] for key in ("size", "sha256")):
            raise ip.InputPreparationError("原本内容が保存時と異なるため、固定資産を復元できません。")
    registration = build_registration_spec(entry)
    # ★ ver75.2: 保存specを現在specとして渡すと変換コードの差を隠すため禁止する。
    current = ip.conversion_spec(
        processed_alignment_ppm=entry["conversion_spec"]["processed_alignment_ppm"],
        registration_hash=registration["registration_hash"],
    )
    if current != entry["conversion_spec"]:
        raise ip.InputPreparationError(
            "変換仕様（コード・依存版・登録・設定）が保存時と異なるため復元できません。"
        )
    if ip._key(source, current) != entry["conversion_key"]:
        raise ip.InputPreparationError("保存済みconversion_keyを再現できません。")


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
            _check_recoverable(entry, root)
            item["restorable"] = True
            if not restore:
                item.update(status="needs_restore", action="同じコマンドに --restore を付けて実行してください。")
                continue
            restored = ip.prepare_imzml(
                entry, cache_root=root, pinned=True,
                alignment_ppm=entry["conversion_spec"]["processed_alignment_ppm"],
            )
            ip.validate_asset(restored)
            if restored["validation"] != entry["validation"]:
                raise ip.InputPreparationError("復元後の検証記録が保存時と一致しません。")
            item.update(status="restored", action="固定変換資産を復元しました。数値データの出力を再実行してください。")
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
