"""★ ver70.0: 原本の識別子と数値入力を分離する。原本の更新で旧結果を変えない。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from importlib.metadata import version, PackageNotFoundError
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from typing import Callable
import uuid

from filelock import FileLock, Timeout

DESCRIPTOR_KEYS = (
    "input_format", "source_ibd", "runtime_path", "conversion_manifest_path",
    "conversion_key", "source_fingerprint", "conversion_spec", "validation",
    "normalization", "conversion_receipt_path", "spectral_preflight",
)
RECEIPT = "conversion_complete.json"
CACHE_MARKER = ".ua_imzml_assets"
# ★ ver71.0: component列と座標layoutを含まないver70 cacheを新規解析で再利用しない。
CONTRACT_VERSION = "common-axis-ms1-spatial-v4"


class InputPreparationError(ValueError):
    """不完全・不一致の入力を解析へ渡さないための診断。"""


class PreparationCancelled(InputPreparationError):
    pass


def check_cancel(cancel: Callable[[], bool] | None = None):
    if cancel and cancel():
        raise PreparationCancelled("入力準備を停止しました。")


def write_json(path, value):
    """同一ファイルシステム内で完成した JSON だけを公開する。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ua-json-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False, sort_keys=True, allow_nan=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def sha256_file(path, cancel=None):
    h = sha256()
    with Path(path).open("rb") as fh:
        while True:
            check_cancel(cancel)
            block = fh.read(4 * 1024 * 1024)  # hash の読込バッファ。行列の大きさとは独立。
            if not block:
                return h.hexdigest()
            h.update(block)


def fingerprint(path, cancel=None):
    path = Path(path).expanduser().resolve()
    before = path.stat()
    digest = sha256_file(path, cancel)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
        raise InputPreparationError(f"読み取り中に原本が変更されました: {path}")
    return {"path": str(path), "size": after.st_size, "mtime_ns": after.st_mtime_ns, "sha256": digest}


def pair_paths(path):
    xml = Path(path).expanduser().resolve()
    if xml.suffix.lower() != ".imzml" or not xml.is_file():
        raise InputPreparationError(f".imzML が見つかりません: {xml}")
    matches = [p for p in xml.parent.iterdir()
               if p.is_file() and p.stem == xml.stem and p.suffix.lower() == ".ibd"]
    if len(matches) != 1:
        raise InputPreparationError(f"{xml.name}: 対応する .ibd は同じフォルダに1つ必要です（検出 {len(matches)} 件）。")
    return xml, matches[0].resolve()


def candidate_info(path):
    """一覧表示用。大きい原本の hash・XML・スペクトルは読まない。"""
    p = Path(path).resolve()
    result = {"path": str(p), "input_format": p.suffix.lower().lstrip("."),
              "label": p.name, "disabled": False, "normalization": "unknown"}
    if p.suffix.lower() == ".imzml":
        try:
            _, ibd = pair_paths(p)
            result.update(source_ibd=str(ibd), label=f"{p.name} — 開始時に自動変換／再利用確認")
        except (OSError, InputPreparationError) as exc:
            result.update(disabled=True, reason=str(exc), label=f"{p.name} — {exc}")
    return result


def default_candidates(paths, saved=None):
    """同フォルダ同 stem は1形式を既定にする。明示した全解除を復活させない。"""
    infos = [candidate_info(p) for p in paths]
    valid = [i["path"] for i in infos if not i["disabled"]]
    if saved is not None:
        return [p for p in saved if p in valid]
    preferred = {}
    priority = {".parquet": 0, ".pq": 1, ".imzml": 2}
    for p in sorted(valid, key=lambda s: (priority.get(Path(s).suffix.lower(), 3), s)):
        preferred.setdefault((str(Path(p).parent), Path(p).stem), p)
    return list(preferred.values())


def validate_selected_paths(paths):
    seen, stems = set(), {}
    for value in paths:
        p = Path(value).expanduser().resolve()
        if str(p) in seen:
            raise InputPreparationError(f"同じ入力を重複選択しています: {p}")
        seen.add(str(p))
        key = (str(p.parent), p.stem)
        if key in stems:
            raise InputPreparationError(f"同じフォルダの {p.stem} は1形式だけ選択してください: {stems[key].name}, {p.name}")
        stems[key] = p
    if not seen:
        raise InputPreparationError("解析対象が選択されていません。")


def contains_imzml(params):
    entries = (params.get("section_manifest") or {}).get("files", [])
    paths = [f.get("path", "") for f in entries if f.get("selection_mode") != "none"]
    if not entries:
        paths = params.get("original_input_paths") or params.get("input_paths") or []
    return any(Path(p).suffix.lower() == ".imzml" for p in paths)



def pinned_inputs_present(manifest):
    """画面用の存在確認だけ。内容の検証・旧revision復元はworkerで行う。"""
    files = [f for f in (manifest or {}).get("files", []) if f.get("selection_mode") != "none"]
    if not files:
        return False
    for entry in files:
        path = Path(entry.get("path") or "")
        if path.suffix.lower() != ".imzml":
            if not path.is_file():
                return False
            continue
        if entry.get("conversion_key"):
            runtime = entry.get("runtime_path")
            sidecar = entry.get("conversion_manifest_path")
            if (runtime and sidecar and Path(runtime).is_file() and Path(sidecar).is_file()
                    and (Path(runtime).parent / RECEIPT).is_file()):
                continue
        try:
            pair_paths(path)
        except (OSError, InputPreparationError):
            return False
    return True


def runtime_paths(manifest, *, validate=False, cancel=None):
    result = []
    for entry in (manifest or {}).get("files", []):
        if entry.get("selection_mode") == "none":
            continue
        if Path(entry["path"]).suffix.lower() == ".imzml":
            if not entry.get("runtime_path") or not entry.get("conversion_key"):
                raise InputPreparationError(f"imzML の入力準備が未完了です: {entry['path']}")
            if validate:
                validate_asset(entry, cancel=cancel)
            value = entry["runtime_path"]
        else:
            value = entry.get("runtime_path") or entry["path"]
        if Path(value).suffix.lower() == ".imzml":
            raise InputPreparationError("未解決の imzML を R / Parquet reader に渡すことはできません。")
        result.append(str(Path(value).resolve()))
    return result


def _versions():
    versions = {}
    for name in ("pyimzml", "pyarrow", "numpy"):
        try:
            versions[name] = version(name)
        except PackageNotFoundError as exc:
            raise InputPreparationError(f"{name} がありません。アプリの requirements.txt で環境を再構築してください。") from exc
    return versions


def conversion_spec():
    # 保存バイトへ影響するコードと依存版を含める。UMAP の設定は含めない。
    here = Path(__file__).parent
    return {"contract": CONTRACT_VERSION, "schema": 2, "dependencies": _versions(),
            "converter_sha256": sha256_file(here / "imzml_io.py"),
            "validator_sha256": sha256_file(here / "imzml_validation.py"),
            "spatial_layout_sha256": sha256_file(here / "imzml_spatial_layout.py"),
            "block_size": 128, "memory_budget_mb": int(os.environ.get("IMZML_BLOCK_MB", "64"))}


def _key(source, spec):
    body = {"xml": source["xml"]["sha256"], "ibd": source["ibd"]["sha256"], "spec": spec}
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _same_content(a, b):
    return all(a[k]["sha256"] == b[k]["sha256"] and a[k]["size"] == b[k]["size"] for k in ("xml", "ibd"))


def default_cache_root():
    from app.config import IMZML_CACHE_DIR
    return Path(IMZML_CACHE_DIR).expanduser().resolve()


def _file_validation(path, cancel):
    return {"size": Path(path).stat().st_size, "sha256": sha256_file(path, cancel)}


def validate_asset(entry, *, cancel=None):
    """結果内に固定した hash と、完了記録・実ファイルの両方を照合する。"""
    root = Path(entry["runtime_path"]).resolve().parent
    receipt_path = root / RECEIPT
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if receipt.get("schema") != 1 or receipt.get("state") != "complete" or receipt["conversion_key"] != entry["conversion_key"]:
            raise InputPreparationError("変換完了記録または revision が一致しません。")
        expected = entry["validation"]
        if receipt["validation"] != expected or receipt["file_id"] != entry["file_id"]:
            raise InputPreparationError("結果に固定した検証記録と cache が一致しません。")
        layout_hash = (entry.get("spatial_layout") or {}).get("coordinate_hash")
        saved_hash = (expected.get("summary") or {}).get("coordinate_hash")
        if layout_hash and saved_hash and layout_hash != saved_hash:
            raise InputPreparationError("画面で確定した座標layoutと変換資産が一致しません。")
        for kind, path in (("parquet", entry["runtime_path"]), ("manifest", entry["conversion_manifest_path"])):
            if Path(path).resolve().parent != root:
                raise InputPreparationError("変換ファイルの所属 revision が一致しません。")
            if _file_validation(path, cancel) != expected[kind]:
                raise InputPreparationError(f"変換資産の破損を検出しました: {path}")
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InputPreparationError(f"完成済み変換資産を検証できません: {root}: {exc}") from exc
    return deepcopy(receipt)


def _entry_from_receipt(entry, root, receipt):
    out = deepcopy(entry)
    out.update({key: deepcopy(receipt[key]) for key in
                ("source_fingerprint", "conversion_spec", "conversion_key", "validation")})
    out.update(input_format="imzml", source_ibd=receipt["source_fingerprint"]["ibd"]["path"],
               runtime_path=str(root / receipt["parquet_name"]),
               conversion_manifest_path=str(root / receipt["manifest_name"]),
               conversion_receipt_path=str(root / RECEIPT), normalization="unknown")
    return out


def prepare_imzml(entry, *, cache_root=None, project_id="", pinned=False,
                  progress=None, cancel=None, converter=None, validator=None, spec=None):
    """原本は読み取り専用。公開済み revision を上書きせず、未完了品は使わない。"""
    entry = deepcopy(entry)
    check_cancel(cancel)
    if pinned and entry.get("conversion_key"):
        try:
            validate_asset(entry, cancel=cancel)
            if progress:
                progress({"stage": "reuse", "sample": Path(entry["path"]).name, "pinned": True})
            return entry
        except PreparationCancelled:
            raise
        except InputPreparationError:
            # 再現可能な場合だけ同一 hash を復元する。変更された原本を代用しない。
            pass
    if progress:
        progress({"stage": "hash", "sample": Path(entry["path"]).name})
    xml, ibd = pair_paths(entry["path"])
    source = {"xml": fingerprint(xml, cancel), "ibd": fingerprint(ibd, cancel)}
    current_spec = deepcopy(spec if spec is not None else conversion_spec())
    if pinned and entry.get("conversion_key"):
        if not _same_content(source, entry["source_fingerprint"]) or current_spec != entry["conversion_spec"]:
            raise InputPreparationError("旧解析の変換資産を復元できません。原本内容または変換仕様が保存時と異なります。")
    key = _key(source, current_spec)
    if pinned and entry.get("conversion_key") and entry["conversion_key"] != key:
        raise InputPreparationError("旧解析の conversion_key を再現できません。")
    from app.services.section_metadata import stable_file_id
    entry.setdefault("file_id", stable_file_id(xml))
    fid = entry["file_id"]
    # 保存場所へ任意パスを注入させず、原本 file_id は manifest にそのまま保持。
    fid_dir = sha256(fid.encode()).hexdigest()[:24]
    project_dir = sha256(str(project_id or "default").encode()).hexdigest()[:20]
    root = Path(cache_root).resolve() if cache_root is not None else default_cache_root()
    root.mkdir(parents=True, exist_ok=True)
    (root / CACHE_MARKER).touch(exist_ok=True)
    parent = root / project_dir / fid_dir
    parent.mkdir(parents=True, exist_ok=True)
    target = Path(entry["runtime_path"]).resolve().parent if pinned and entry.get("conversion_key") else parent / key
    # 旧 manifest が参照する場所を復元する場合も管理領域外には書かない。
    if not target.is_relative_to(root):
        raise InputPreparationError("変換資産の復元先が設定された管理領域外です。保存設定を確認してください。")
    target.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(target) + ".lock")
    while True:
        check_cancel(cancel)
        try:
            lock.acquire(timeout=0.2)
            break
        except Timeout:
            continue
    try:
        # この revision のロックを取った後だけ中断した一時領域を回収する。
        for stale in target.parent.glob(f".stage-{key}-*"):
            if stale.is_dir() and not stale.is_symlink():
                shutil.rmtree(stale)
        expected = entry.get("validation") if pinned else None
        if target.exists():
            try:
                receipt = json.loads((target / RECEIPT).read_text(encoding="utf-8"))
                candidate = _entry_from_receipt(entry, target, receipt)
                if candidate["conversion_key"] != key:
                    raise InputPreparationError("cache キー不一致")
                validate_asset(candidate, cancel=cancel)
                if expected is not None and candidate["validation"] != expected:
                    raise InputPreparationError("保存時と違う cache に置換されています。")
            except PreparationCancelled:
                raise
            except (OSError, KeyError, TypeError, json.JSONDecodeError, InputPreparationError):
                if expected is None:
                    try:
                        old = json.loads((target / RECEIPT).read_text(encoding="utf-8"))
                        expected = old["validation"]
                    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
                        raise InputPreparationError("破損 cache の元の検証記録がなく、同一 revision の復元を保証できません。") from exc
                quarantine = target.with_name(f".corrupt-{key}-{uuid.uuid4().hex}")
                os.rename(target, quarantine)
            else:
                # 原本の更新はcache破損と区別し、正常な旧資産を隔離しない。
                after = {"xml": fingerprint(xml, cancel), "ibd": fingerprint(ibd, cancel)}
                if not _same_content(source, after):
                    raise InputPreparationError("再利用の検証中に原本が変更されました。")
                if progress:
                    progress({"stage": "reuse", "sample": xml.name})
                return candidate
        if converter is None or validator is None:
            from app.services.imzml_validation import checked_import, validate_converted_table
            converter = converter or checked_import
            validator = validator or validate_converted_table
        # 見積りは圧縮率を仮定しない保守値。最終的な ENOSPC も失敗扱いにする。
        required = max(64 * 1024 * 1024, ibd.stat().st_size * 2)
        if shutil.disk_usage(target.parent).free < required:
            raise InputPreparationError(f"変換先の空き容量が不足しています（必要見積り {required} bytes）。")
        stage = Path(tempfile.mkdtemp(prefix=f".stage-{key}-", dir=target.parent))
        try:
            output = stage / f"{xml.stem}.parquet"
            def on_pixels(done, total):
                check_cancel(cancel)
                if progress:
                    progress({"stage": "convert", "sample": xml.name, "done": done, "total": total})
            result = converter(xml, output, block_size=current_spec["block_size"],
                               memory_budget_mb=current_spec["memory_budget_mb"],
                               progress=on_pixels, cancel=cancel)
            check_cancel(cancel)
            if progress:
                progress({"stage": "validate", "sample": xml.name})
            validation_summary = validator(output, cancel=cancel)
            layout_hash = (entry.get("spatial_layout") or {}).get("coordinate_hash")
            if layout_hash and validation_summary.get("coordinate_hash") != layout_hash:
                raise InputPreparationError("選択時と変換時でimzML座標が変わったため解析を開始しません。")
            manifest_path = output.with_suffix(".imzml.json")
            validation = {"parquet": _file_validation(output, cancel),
                          "manifest": _file_validation(manifest_path, cancel),
                          "summary": validation_summary}
            after = {"xml": fingerprint(xml, cancel), "ibd": fingerprint(ibd, cancel)}
            if not _same_content(source, after):
                raise InputPreparationError("変換中に原本が変更されたため完成品を公開しません。")
            if expected is not None and validation != expected:
                raise InputPreparationError("同じ revision の出力 hash を再現できません。過去の入力は置き換えません。")
            receipt = {"state": "complete", "schema": 1, "file_id": fid,
                       "conversion_key": key, "conversion_spec": current_spec,
                       "source_fingerprint": source, "validation": validation,
                       "parquet_name": output.name, "manifest_name": manifest_path.name,
                       "created_at": time.time(), "restored": expected is not None}
            write_json(stage / RECEIPT, receipt)
            check_cancel(cancel)
            os.rename(stage, target)
            return _entry_from_receipt(entry, target, receipt)
        finally:
            if stage.exists():
                shutil.rmtree(stage, ignore_errors=True)
    finally:
        lock.release()


def prepare_inputs(params, *, cache_root=None, project_id="", progress=None, cancel=None, **kwargs):
    """すべての選択入力が完成してから、新しい manifest / runtime paths を返す。"""
    params = deepcopy(params)
    from app.services.section_metadata import build_section_manifest, validate_section_manifest
    manifest = params.get("section_manifest")
    if not manifest:
        paths = params.get("original_input_paths") or params.get("input_paths") or []
        manifest = build_section_manifest([{"path": p, "available_rois": []} for p in paths])
    manifest = deepcopy(manifest)
    errors = validate_section_manifest(manifest)
    if errors:
        raise InputPreparationError(" / ".join(errors))
    entries = [e for e in manifest["files"] if e.get("selection_mode") != "none"]
    validate_selected_paths([e["path"] for e in entries])
    pinned = bool(params.get("source_result_dir") or params.get("source_rds_path") or
                  params.get("resume_reanalysis") or params.get("resume_from_rds") or
                  manifest.get("source_rds_path") or "original_data_folder" in params or params.get("rds_path"))
    prepared = {}
    for number, entry in enumerate(entries, 1):
        check_cancel(cancel)
        def notify(state):
            if progress:
                progress({**state, "sample_number": number, "sample_total": len(entries)})
        if Path(entry["path"]).suffix.lower() == ".imzml":
            prepared[entry["file_id"]] = prepare_imzml(entry, cache_root=cache_root,
                project_id=project_id, pinned=pinned, progress=notify, cancel=cancel, **kwargs)
        else:
            if not Path(entry["path"]).is_file():
                raise InputPreparationError(f"入力が見つかりません: {entry['path']}")
            prepared[entry["file_id"]] = {**entry, "runtime_path": entry["path"]}
    manifest["files"] = [prepared.get(e["file_id"], e) for e in manifest["files"]]
    params["section_manifest"] = manifest
    paths = runtime_paths(manifest)
    params["input_paths"] = paths
    if "original_input_paths" in params or "original_data_folder" in params:
        params["original_input_paths"] = paths
    params["_imzml_pipeline_prepared"] = True
    return params
