"""★ ver81.0: 保存済み解析の条件を固定し、不足した変換資産だけを復元する。

画面の軽量診断とworkerの全量検証を分離する。旧出力を検証前に退避せず、
公開の途中停止はjournalで回収する。プロジェクト・RDS・manifestは更新しない。
"""
from __future__ import annotations

from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import uuid

from filelock import FileLock, Timeout

from app.services import input_preparation as ip

_SPEC_KEYS = {
    "contract", "schema", "dependencies", "converter_sha256", "processed_converter_sha256",
    "validator_sha256", "spatial_layout_sha256", "registration_sha256", "parquet_contract_sha256",
    "processed_alignment_ppm", "registration_hash", "intensity_dtype", "mz_name_decimals",
    "all_sections_preserved", "block_size", "memory_budget_mb", "processed_max_features",
}


def _digest_record(value):
    return (isinstance(value, dict) and isinstance(value.get("size"), int)
            and not isinstance(value["size"], bool) and value["size"] >= 0
            and isinstance(value.get("sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", value["sha256"]) is not None)


def pinned_entry(entry):
    """不足した固定記録を現在の設定で補完しない。群編集は生成からだけ除外する。"""
    if not isinstance(entry, dict):
        raise ip.InputPreparationError("固定入力の記録が不正です。")
    if entry.get("registration_revision_requested"):
        raise ip.InputPreparationError("登録revisionの変更要求は旧資産の復元として実行できません。")
    for key in ("path", "file_id", "runtime_path", "conversion_manifest_path", "conversion_key"):
        if not isinstance(entry.get(key), str) or not entry[key].strip():
            raise ip.InputPreparationError(f"保存済み固定descriptorに {key} がありません。")
    if not re.fullmatch(r"[0-9a-f]{64}", entry["conversion_key"]):
        raise ip.InputPreparationError("保存済みconversion_keyが不正です。")
    for key in ("path", "runtime_path", "conversion_manifest_path"):
        if not Path(entry[key]).is_absolute():
            raise ip.InputPreparationError(f"保存済み {key} が絶対パスではありません。")
    runtime, sidecar = Path(entry["runtime_path"]), Path(entry["conversion_manifest_path"])
    if (runtime.suffix.lower() != ".parquet" or sidecar != runtime.with_suffix(".imzml.json")
            or runtime.stem != Path(entry["path"]).stem
            or runtime.parent.name != entry["conversion_key"]):
        raise ip.InputPreparationError("保存済み変換ファイルの組み合わせまたはrevisionが不正です。")
    source = entry.get("source_fingerprint")
    if not isinstance(source, dict) or any(
        not _digest_record(source.get(kind)) or not isinstance(source[kind].get("path"), str)
        or not Path(source[kind]["path"]).is_absolute() for kind in ("xml", "ibd")
    ):
        raise ip.InputPreparationError("保存済み原本fingerprintが不足しています。")
    if Path(source["xml"]["path"]) != Path(entry["path"]):
        raise ip.InputPreparationError("保存済み原本パスが一致しません。")
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
    if (not isinstance(validation, dict)
            or any(not _digest_record(validation.get(k)) for k in ("parquet", "manifest"))
            or not isinstance(validation.get("summary"), dict) or not validation["summary"]):
        raise ip.InputPreparationError("保存済み変換出力の検証記録が不足しています。")
    if not isinstance(entry.get("registered_sections"), list) or not entry["registered_sections"]:
        raise ip.InputPreparationError("保存時の全切片登録情報がありません。")
    if ip._key(source, spec) != entry["conversion_key"]:
        raise ip.InputPreparationError("保存済みconversion_keyと変換条件が一致しません。")
    result = deepcopy(entry)
    result.pop("metadata_overlay", None)
    return result


def selected_entries(manifest, file_ids=None):
    files = (manifest or {}).get("files", [])
    if not isinstance(files, list) or not all(isinstance(e, dict) for e in files):
        raise ip.InputPreparationError("固定入力manifestが不正です。")
    wanted = None if file_ids is None else set(map(str, file_ids))
    selected = [e for e in files if (str(e.get("file_id", "")) in wanted if wanted is not None
                else e.get("selection_mode") != "none")
                and Path(e.get("path") or "").suffix.lower() == ".imzml"]
    ids = [str(e.get("file_id", "")) for e in selected]
    if len(ids) != len(set(ids)):
        raise ip.InputPreparationError("固定入力manifestにfile_idの重複があります。")
    if wanted is not None and set(ids) != wanted:
        raise ip.InputPreparationError("指定したfile_idが解析結果のimzML入力に登録されていません。")
    return deepcopy(selected)


def _managed_target(entry, cache_root=None):
    root = Path(cache_root or ip.default_cache_root()).expanduser().resolve()
    target = Path(entry["runtime_path"]).parent
    if target == root or not target.resolve().is_relative_to(root):
        raise ip.InputPreparationError("復元先が現在の変換資産管理領域外です。保存時のマウントを確認してください。")
    # ★ ver81.0: 保存記録からのパスでもリンク経由の置換は行わない。
    for part in [target, *target.parents]:
        if part.is_symlink() or getattr(part, "is_junction", lambda: False)():
            raise ip.InputPreparationError("復元先にシンボリックリンクまたはjunctionがあります。")
        if part == root:
            break
    return root, target


def _regular(path):
    return path.is_file() and not path.is_symlink() and path.stat().st_nlink == 1


def _asset_paths(entry, target=None):
    target = target or Path(entry["runtime_path"]).parent
    return {"parquet": target / Path(entry["runtime_path"]).name,
            "manifest": target / Path(entry["conversion_manifest_path"]).name,
            "receipt": target / ip.RECEIPT}


def inspect_requirements(manifest, *, cache_root=None, file_ids=None):
    """画面用: 原本・Parquetのhash、XML解析、ディレクトリ作成を行わない。"""
    from app.services.imzml_recovery_runtime import profile_for
    entries = selected_entries(manifest, file_ids)
    report = {"schema": 1, "files": [], "selected_file_ids": [e.get("file_id") for e in entries]}
    for original in entries:
        item = {"file_id": original.get("file_id"), "source_path": original.get("path"),
                "runtime_path": original.get("runtime_path"), "status": "record_incomplete",
                "missing": [], "reason": "", "can_restore": False}
        report["files"].append(item)
        try:
            entry = pinned_entry(original)
            _managed_target(entry, cache_root)
            paths = _asset_paths(entry)
            item["missing"] = [kind for kind, path in paths.items() if not path.is_file()]
            outputs_present = all(_regular(paths[k]) and paths[k].stat().st_size == entry["validation"][k]["size"]
                                  for k in ("parquet", "manifest"))
            receipt_ok = False
            if _regular(paths["receipt"]) and paths["receipt"].stat().st_size <= 1024 * 1024:
                try:
                    receipt = json.loads(paths["receipt"].read_text(encoding="utf-8"))
                    receipt_ok = (receipt.get("state") == "complete" and receipt.get("schema") in (1, 2)
                                  and receipt.get("file_id") == entry["file_id"]
                                  and receipt.get("conversion_key") == entry["conversion_key"]
                                  and receipt.get("validation") == entry["validation"])
                except (ValueError, OSError, AttributeError):
                    pass
            if outputs_present and receipt_ok:
                item.update(status="present", reason="変換データあり（出力時に内容を検証します）。")
                continue
            if outputs_present:
                item.update(status="receipt_missing", can_restore=True,
                            reason="完了記録を確認できません。出力内容が保存値と一致すれば記録を復元します。")
                continue
            item["status"] = "missing" if len(item["missing"]) == 3 else "partial"
            try:
                xml, ibd = ip.pair_paths(entry["path"])
            except (OSError, ip.InputPreparationError) as exc:
                item.update(status="source_missing", reason=str(exc))
                continue
            if any(p.stat().st_size != entry["source_fingerprint"][k]["size"]
                   for k, p in (("xml", xml), ("ibd", ibd))):
                item.update(status="source_changed", reason="原本サイズが保存時と異なります。")
                continue
            profile = profile_for(entry)
            item["profile"] = {k: profile.get(k) for k in ("id", "available", "reason")}
            if not profile.get("available"):
                item.update(status="profile_unavailable", reason=profile.get("reason") or "対応する変換環境がありません。")
            else:
                item.update(can_restore=True, reason="原本あり。復元時に解析時の内容との一致を検証します。")
        except (ip.InputPreparationError, OSError, ValueError, TypeError, KeyError) as exc:
            item.update(status="record_incomplete", reason=str(exc))
    needs = [i for i in report["files"] if i["status"] != "present"]
    report.update(needs_recovery=bool(needs), can_restore=bool(needs) and all(i["can_restore"] for i in needs))
    return report


def verify_source(entry, *, cancel=None):
    xml, ibd = ip.pair_paths(entry["path"])
    source = {"xml": ip.fingerprint(xml, cancel), "ibd": ip.fingerprint(ibd, cancel)}
    if not ip._same_content(source, entry["source_fingerprint"]):
        raise ip.InputPreparationError("原本内容が保存時と異なるため、固定資産を復元できません。")
    if any(str(p) != str(Path(entry["source_fingerprint"][k]["path"]).resolve())
           for k, p in (("xml", xml), ("ibd", ibd))):
        raise ip.InputPreparationError("原本の保存場所が解析時と異なります。保存時のマウントを確認してください。")
    return source


def check_recoverable(entry, cache_root=None, *, cancel=None):
    from app.services.imzml_recovery_runtime import profile_for
    entry = pinned_entry(entry)
    _, target = _managed_target(entry, cache_root)
    try:
        _verify_outputs(entry, target, cancel)
        return entry["source_fingerprint"], {"id": "verified-existing-output", "available": True}
    except ip.PreparationCancelled:
        raise
    except (ip.InputPreparationError, OSError):
        pass
    source = verify_source(entry, cancel=cancel)
    profile = profile_for(entry)
    if not profile.get("available"):
        raise ip.InputPreparationError("変換仕様を再現する環境がありません: " + str(profile.get("reason", "")))
    return source, profile


def _verify_outputs(entry, target, cancel=None):
    from app.services.imzml_validation import validate_converted_table
    paths = _asset_paths(entry, target)
    if not all(_regular(paths[k]) and paths[k].stat().st_size == entry["validation"][k]["size"]
               for k in ("parquet", "manifest")):
        raise ip.InputPreparationError("保存時の出力hashを照合できません（変換ファイル不足またはサイズ不一致）。")
    for key in ("parquet", "manifest"):
        if not _regular(paths[key]) or ip._file_validation(paths[key], cancel) != entry["validation"][key]:
            raise ip.InputPreparationError(f"保存時の出力hashを再現できません: {key}")
    summary = validate_converted_table(paths["parquet"], cancel=cancel)
    if summary != entry["validation"]["summary"]:
        raise ip.InputPreparationError("復元出力の全量検証結果が保存時と一致しません。")
    return summary


def _healthy(entry, cancel=None):
    try:
        if not all(_regular(p) for p in _asset_paths(entry).values()):
            return False
        ip.validate_asset(entry, cancel=cancel)
        return True
    except ip.PreparationCancelled:
        raise
    except (ip.InputPreparationError, OSError):
        return False


def _journal_path(target):
    return target.with_name(f".recovery-{target.name}.json")


def _owned_sibling(value, target, prefix):
    path = Path(value)
    if (path.parent != target.parent or not path.name.startswith(prefix)
            or path.is_symlink() or path.resolve().parent != target.parent.resolve()):
        raise ip.InputPreparationError("復元journalの一時保存先が不正です。")
    return path


def _reconcile(entry, target, cancel=None):
    journal_path = _journal_path(target)
    if not journal_path.exists():
        return
    if not _regular(journal_path):
        raise ip.InputPreparationError("復元journalが通常ファイルではありません。")
    record = json.loads(journal_path.read_text(encoding="utf-8"))
    if record.get("conversion_key") != entry["conversion_key"] or record.get("target") != str(target):
        raise ip.InputPreparationError("復元journalの対象が一致しません。")
    stage = _owned_sibling(record["stage"], target, f".stage-recovery-{target.name[:12]}-")
    backup = _owned_sibling(record["backup"], target, f".corrupt-{target.name[:12]}-")
    if record.get("state") in ("committed", "rolled_back"):
        return
    if _healthy(entry, cancel):
        record["state"] = "committed"
    elif not target.exists() and backup.exists():
        os.rename(backup, target)
        record["state"] = "rolled_back"
    elif target.exists() and not backup.exists():
        record["state"] = "rolled_back"
    elif not target.exists() and not backup.exists():
        record["state"] = "rolled_back"
    else:
        raise ip.InputPreparationError("復元公開の途中状態を自動回復できません。journalと退避資産を確認してください。")
    ip.write_json(journal_path, record)
    if stage.exists():
        shutil.rmtree(stage)


def _publish(entry, stage, target, *, cache_root, operation_id, cancel=None, policy=None):
    _managed_target(entry, cache_root)  # 公開直前にもリンクを拒否する。
    if _healthy(entry, cancel):
        return
    if policy:
        policy.prepare_stage(stage, cancel=lambda: ip.check_cancel(cancel))
    ip.check_cancel(cancel)
    backup = target.with_name(f".corrupt-{target.name[:12]}-{uuid.uuid4().hex}")
    record = {"schema": 1, "conversion_key": entry["conversion_key"], "target": str(target),
              "stage": str(stage), "backup": str(backup), "operation_id": operation_id,
              "state": "prepared", "created_at": time.time()}
    ip.write_json(_journal_path(target), record)
    try:
        if target.exists():
            os.rename(target, backup)
        os.rename(stage, target)
    except BaseException:
        if not target.exists() and backup.exists():
            os.rename(backup, target)
        raise
    record["state"] = "committed"
    ip.write_json(_journal_path(target), record)


def recover_entry(entry, *, cache_root=None, cancel=None, progress=None,
                  child_callback=None, operation_id=None):
    """保存時と同一の資産だけ公開する。戻り値でも結果側の編集情報を保持する。"""
    from app.services.imzml_recovery_runtime import generate
    from app.services.imzml_shared_permissions import shared_read_policy
    original = deepcopy(entry)
    entry = pinned_entry(entry)
    root, target = _managed_target(entry, cache_root)
    operation_id = operation_id or uuid.uuid4().hex
    def notify(stage, **kwargs):
        ip.check_cancel(cancel)
        if progress:
            progress({"stage": stage, "sample": Path(entry["path"]).name, **kwargs})
    if not _journal_path(target).exists() and _healthy(entry, cancel):
        notify("reuse")
        return original
    policy = shared_read_policy(root)
    if policy:
        policy.ensure_parent(target.parent)
        policy.ensure_marker(ip.CACHE_MARKER)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(target) + ".lock")
    while True:
        ip.check_cancel(cancel)
        try:
            lock.acquire(timeout=0.2)
            break
        except Timeout:
            continue
    stage = None
    try:
        _managed_target(entry, root)
        _reconcile(entry, target, cancel)
        if _healthy(entry, cancel):
            notify("reuse")
            return original
        # ★ ver81.0: 完了記録だけの消失なら、残存出力を全照合して再変換を省く。
        exact_outputs = False
        try:
            _verify_outputs(entry, target, cancel)
            exact_outputs = True
        except ip.PreparationCancelled:
            raise
        except (ip.InputPreparationError, OSError):
            pass
        if not exact_outputs:
            notify("verify_source")
            source, profile = check_recoverable(entry, root, cancel=cancel)
        else:
            source, profile = entry["source_fingerprint"], {"id": "verified-existing-output"}
        expected_bytes = sum(entry["validation"][k]["size"] for k in ("parquet", "manifest"))
        # 保存サイズの2倍に原本ibdと64MiBを加える。失敗時の退避はrenameなので追加copyしない。
        required = expected_bytes * 2 + (0 if exact_outputs else source["ibd"]["size"]) + 64 * 1024**2
        if shutil.disk_usage(target.parent).free < required:
            raise ip.InputPreparationError(f"変換先の空き容量が不足しています（必要見積り {required} bytes）。")
        # Windowsの入れ子一時ファイル長を抑える。完全keyはlock/journalで照合する。
        stage = Path(tempfile.mkdtemp(prefix=f".stage-recovery-{target.name[:12]}-", dir=target.parent))
        generated = {}
        if exact_outputs:
            for kind in ("parquet", "manifest"):
                ip.check_cancel(cancel)
                shutil.copyfile(_asset_paths(entry)[kind], _asset_paths(entry, stage)[kind])
        else:
            notify("convert", profile=profile.get("id"))
            generated = generate(entry, stage, cancel=cancel, progress=progress, child_callback=child_callback)
        notify("validate")
        _verify_outputs(entry, stage, cancel)
        if not exact_outputs:
            verify_source(entry, cancel=cancel)
        receipt = {"schema": 2, "state": "complete", "file_id": entry["file_id"],
                   "conversion_key": entry["conversion_key"], "conversion_spec": entry["conversion_spec"],
                   "source_fingerprint": source, "validation": entry["validation"],
                   "parquet_name": Path(entry["runtime_path"]).name,
                   "manifest_name": Path(entry["conversion_manifest_path"]).name,
                   "created_at": time.time(), "restored": True,
                   "recovery": {"operation_id": operation_id, "profile_id": profile.get("id"),
                                "receipt_only": exact_outputs,
                                "runtime": (generated or {}).get("runtime", {})}}
        ip.write_json(stage / ip.RECEIPT, receipt)
        notify("publish")
        _publish(entry, stage, target, cache_root=root, operation_id=operation_id, cancel=cancel, policy=policy)
        # 公開確定後の取消では完成資産を取り消さない。
        if progress:
            progress({"stage": "complete", "sample": Path(entry["path"]).name})
        return original
    finally:
        if stage is not None and stage.exists():
            _owned_sibling(str(stage), target, f".stage-recovery-{target.name[:12]}-")
            shutil.rmtree(stage)
        lock.release()
