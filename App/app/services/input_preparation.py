"""★ ver70.0: 原本の識別子と数値入力を分離する。原本の更新で旧結果を変えない。"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from importlib.metadata import version, PackageNotFoundError
import inspect
import json
import math
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
    "conversion_key", "spectral_key", "source_fingerprint", "conversion_spec", "validation",
    "normalization", "conversion_receipt_path", "spectral_preflight", "source_peak_count", "registration_history", "source_selected_section_ids",
)
RECEIPT = "conversion_complete.json"
CACHE_MARKER = ".ua_imzml_assets"
# ★ ver71.0: component列と座標layoutを含まないver70 cacheを新規解析で再利用しない。
CONTRACT_VERSION = "tims-standard-all-sections-v6"


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


def conversion_spec(*, processed_alignment_ppm=0.0, registration_hash=""):
    # 保存バイトへ影響するコード・登録情報と依存版を含める。解析切片選択は含めない。
    try:
        processed_alignment_ppm = float(processed_alignment_ppm)
    except (TypeError, ValueError) as exc:
        raise InputPreparationError("m/zアライメント(ppm)は数値で指定してください。") from exc
    if not math.isfinite(processed_alignment_ppm) or processed_alignment_ppm < 0:
        raise InputPreparationError("m/zアライメント(ppm)は0以上の有限値で指定してください。")
    here = Path(__file__).parent
    return {
        "contract": CONTRACT_VERSION, "schema": 4, "dependencies": _versions(),
        "converter_sha256": sha256_file(here / "imzml_io.py"),
        "processed_converter_sha256": sha256_file(here / "imzml_processed.py"),
        "validator_sha256": sha256_file(here / "imzml_validation.py"),
        "spatial_layout_sha256": sha256_file(here / "imzml_spatial_layout.py"),
        "registration_sha256": sha256_file(here / "imzml_registration.py"),
        "parquet_contract_sha256": sha256_file(here / "tims_parquet_contract.py"),
        "processed_alignment_ppm": processed_alignment_ppm,
        "registration_hash": str(registration_hash or ""),
        "intensity_dtype": "float32",
        "mz_name_decimals": 6,
        "all_sections_preserved": True,
        "block_size": 128,
        "memory_budget_mb": int(os.environ.get("IMZML_BLOCK_MB", "64")),
        "processed_max_features": int(os.environ.get("IMZML_PROCESSED_MAX_FEATURES", "100000")),
    }


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
        if receipt.get("schema") not in {1, 2} or receipt.get("state") != "complete" \
                or receipt["conversion_key"] != entry["conversion_key"]:
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
    except FileNotFoundError as exc:
        # ★ ver75.2: receiptだけの低レベル例外では切片選択の不備に見えた。
        # 変換資産の不足を区別し、検証を省略せず復元が必要なファイルを示す。
        assets = [("変換完了記録", receipt_path),
                  ("変換済みParquet", entry.get("runtime_path")),
                  ("変換対応情報", entry.get("conversion_manifest_path"))]
        missing = [label for label, path in assets if path and not Path(path).is_file()]
        sample = Path(entry.get("path") or entry["runtime_path"]).name
        raise InputPreparationError(
            f"{sample}: 解析時のimzML変換データが見つかりません"
            f"（不足: {'、'.join(missing) or '読込対象ファイル'}）。"
            "切片の選択内容の問題ではありません。"
            "管理者が変換データの保存先・永続化設定を確認し、"
            "解析時と同一の変換データを復元してから再出力してください。"
            f" 保存先: {root}"
        ) from exc
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise InputPreparationError(f"完成済み変換資産を検証できません: {root}: {exc}") from exc
    return deepcopy(receipt)


def _entry_from_receipt(entry, root, receipt):
    out = deepcopy(entry)
    if out.pop("registration_revision_requested", False):
        from app.services.section_metadata import effective_registered_sections
        from app.services.section_completeness import normalized_registered_sections
        out.setdefault("registration_history", []).append({
            "conversion_key": entry.get("conversion_key"),
            "registered_sections": deepcopy(entry.get("registration_parent_sections") or entry.get("registered_sections"))})
        rows = effective_registered_sections(entry)
        for row in rows:
            row.pop("annotation_label", None)
        out["registered_sections"] = normalized_registered_sections(rows)
        out.pop("metadata_overlay", None)
        out.pop("registration_parent_sections", None)
    out.update({key: deepcopy(receipt[key]) for key in
                ("source_fingerprint", "conversion_spec", "conversion_key", "validation")})
    if receipt.get("spectral_key"):
        out["spectral_key"] = str(receipt["spectral_key"])
    out.update(input_format="imzml", source_ibd=receipt["source_fingerprint"]["ibd"]["path"],
               runtime_path=str(root / receipt["parquet_name"]),
               conversion_manifest_path=str(root / receipt["manifest_name"]),
               conversion_receipt_path=str(root / RECEIPT), normalization="unknown")
    return out


def _spectral_spec(spec: dict) -> dict:
    """切片名・個体・群を除いた、数値行列だけを決める変換仕様。"""
    value = deepcopy(spec)
    value["registration_hash"] = ""
    return value


def _find_spectral_asset(parent: Path, spectral_key: str, source: dict, entry: dict, *, cancel=None):
    """同じraw/feature条件から完成済みの標準Parquetを1件返す。"""
    for receipt_path in sorted(parent.glob(f"*/{RECEIPT}"), key=lambda path: path.stat().st_mtime_ns,
                               reverse=True):
        try:
            receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            if receipt.get("state") != "complete" or receipt.get("spectral_key") != spectral_key:
                continue
            candidate = _entry_from_receipt(entry, receipt_path.parent, receipt)
            candidate_source = receipt.get("source_fingerprint") or {}
            if not _same_content(source, candidate_source):
                continue
            validate_asset(candidate, cancel=cancel)
            return candidate, receipt
        except PreparationCancelled:
            raise
        except (OSError, KeyError, TypeError, json.JSONDecodeError, InputPreparationError):
            continue
    return None, None


def prepare_imzml(entry, *, cache_root=None, project_id="", pinned=False,
                  progress=None, cancel=None, converter=None, validator=None, spec=None,
                  alignment_ppm=0.0):
    """原本は読み取り専用。公開済み revision を上書きせず、未完了品は使わない。"""
    entry = deepcopy(entry)
    # ★ ver74.0: 明示編集は旧revisionの復元ではなく新revision。旧assetは変更しない。
    if entry.get("registration_revision_requested"):
        pinned = False
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
    from app.services.imzml_registration import build_registration_spec
    registration = build_registration_spec(entry)
    source = {"xml": fingerprint(xml, cancel), "ibd": fingerprint(ibd, cancel)}
    current_spec = deepcopy(spec if spec is not None else conversion_spec(
        processed_alignment_ppm=alignment_ppm,
        registration_hash=registration["registration_hash"],
    ))
    expected_ppm = float(current_spec.get("processed_alignment_ppm", alignment_ppm))
    if expected_ppm != float(alignment_ppm):
        raise InputPreparationError("変換仕様とm/zアライメント(ppm)が一致しません。")
    if current_spec.get("registration_hash") != registration["registration_hash"]:
        raise InputPreparationError("変換仕様と全切片登録情報が一致しません。")
    spectral_spec = _spectral_spec(current_spec)
    spectral_key = _key(source, spectral_spec)
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
            converter_kwargs = {
                "registration": registration,
                "alignment_ppm": expected_ppm,
                "block_size": current_spec["block_size"],
                "memory_budget_mb": current_spec["memory_budget_mb"],
                "progress": on_pixels,
                "cancel": cancel,
            }
            try:
                parameters = inspect.signature(converter).parameters
                accepts_kwargs = any(
                    parameter.kind == inspect.Parameter.VAR_KEYWORD
                    for parameter in parameters.values()
                )
                converter_kwargs = {
                    key: value for key, value in converter_kwargs.items()
                    if accepts_kwargs or key in parameters
                }
            except (TypeError, ValueError):
                pass
            reusable_entry, reusable_receipt = _find_spectral_asset(
                parent, spectral_key, source, entry, cancel=cancel
            )
            if reusable_entry is not None:
                if progress:
                    progress({"stage": "register", "sample": xml.name, "reused_spectral_core": True})
                from app.services.imzml_registration import rewrite_registered_parquet
                result = rewrite_registered_parquet(
                    reusable_entry["runtime_path"],
                    reusable_entry["conversion_manifest_path"],
                    registration, output, progress=on_pixels, cancel=cancel,
                )
                result["derived_from_conversion_key"] = reusable_receipt.get("conversion_key")
            else:
                result = converter(xml, output, **converter_kwargs)
            check_cancel(cancel)
            if progress:
                progress({"stage": "validate", "sample": xml.name})
            # checked_importは全量検証済みsummaryを返す。登録情報だけの書替えと代替converterはここで1回検証する。
            validation_summary = result.get("validation_summary") if isinstance(result, dict) else None
            if validation_summary is None:
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
            receipt = {"state": "complete", "schema": 2, "file_id": fid,
                       "conversion_key": key, "conversion_spec": current_spec,
                       "spectral_key": spectral_key, "spectral_spec": spectral_spec,
                       "source_fingerprint": source, "validation": validation,
                       "parquet_name": output.name, "manifest_name": manifest_path.name,
                       "derived_from_conversion_key": (result.get("derived_from_conversion_key")
                                                       if isinstance(result, dict) else None),
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




def _catalog_from_paths_for_backend(paths):
    """UIを経由しないAPI/CLIでも、実ファイルから全切片registryを復元する。"""
    from app.services.data_manager import read_parquet_annotations, read_parquet_section_registry
    result = []
    for raw in paths or []:
        path = str(Path(raw).expanduser().resolve())
        suffix = Path(path).suffix.lower()
        if suffix == ".imzml":
            from app.services.imzml_spatial_layout import (
                inspect_imzml_spatial_layout, default_spatial_sections,
            )
            from app.services.section_metadata import stable_file_id
            layout = inspect_imzml_spatial_layout(path)
            file_id = stable_file_id(path)
            sections = default_spatial_sections(file_id, layout)
            result.append({
                "path": path, "file_id": file_id, "available_rois": [],
                "roi_role": "spatial", "spatial_layout": layout,
                "spatial_sections": sections, "registered_sections": sections,
            })
            continue
        registry = read_parquet_section_registry(path) if suffix in {".parquet", ".pq"} else []
        if registry:
            result.append({
                "path": path, "available_rois": [
                    str(row.get("section_display_name") or "").strip()
                    for row in registry
                ],
                "roi_role": "section", "section_registry": registry,
                "registered_sections": registry,
            })
        else:
            rois = read_parquet_annotations(path) if suffix in {".parquet", ".pq"} else []
            result.append({"path": path, "available_rois": rois})
    return result


def _hydrate_parquet_registries(manifest):
    """Parquet footerを切片registryの正本として採用し、改変・欠落を拒否する。

    直接Parquet入力だけでなく、raw imzMLを指す固定revisionの``runtime_path``も
    検査する。これにより、再解析manifestだけを書き換えて未選択切片の必須情報を
    回避することはできない。
    """
    from app.services.data_manager import read_parquet_section_registry
    from app.services.section_completeness import normalized_registered_sections

    result = deepcopy(manifest or {})
    files = []
    for raw in result.get("files", []):
        entry = deepcopy(raw)
        source_path = Path(entry.get("path") or "").expanduser()
        runtime_path = Path(entry.get("runtime_path") or "").expanduser()
        registry_path = None
        if source_path.suffix.lower() in {".parquet", ".pq"} and source_path.is_file():
            registry_path = source_path
        elif runtime_path.suffix.lower() in {".parquet", ".pq"} and runtime_path.is_file():
            registry_path = runtime_path

        if registry_path is not None:
            registry = read_parquet_section_registry(str(registry_path))
            if registry:
                supplied = (entry.get("registration_parent_sections")
                            if entry.get("registration_revision_requested") else None)
                supplied = supplied or entry.get("registered_sections") or entry.get("spatial_sections") or []
                if supplied and normalized_registered_sections(supplied) != normalized_registered_sections(registry):
                    raise InputPreparationError(
                        f"{registry_path.name}: 解析条件の全切片情報がParquet内registryと一致しません。"
                    )
                from app.services.section_metadata import restore_registered_selection
                if source_path.suffix.lower() in {".parquet", ".pq"}:
                    entry["roi_role"] = "section"
                else:
                    entry["roi_role"] = "spatial"
                # ★ ver74.0: 登録は不変、選択/overlayは別に復元。geometry新revisionは旧assetを照合するだけ。
                if not entry.get("registration_parent_sections"):
                    entry = restore_registered_selection(entry, registry)
        files.append(entry)
    result["files"] = files
    return result

def preflight_registered_inputs(params):
    """★ ver74.0: 直接ParquetもR設定生成前に同じfooter/選択検証を通す。"""
    from app.services.stage_signatures import validated_import_record
    imported = validated_import_record(params)
    if imported:
        if not params.get("resume_from_rds") or params.get("pipeline_stage") != "downstream_from_reduction":
            raise InputPreparationError("検証済み旧結果は保存reductionからの下流解析として実行してください。")
        params["validated_legacy_import"] = imported["entry"]
        return params
    manifest = params.get("section_manifest")
    paths = params.get("original_input_paths") or params.get("input_paths") or []
    if not manifest:
        from app.services.data_manager import read_parquet_section_registry
        registry_found = False
        for value in paths:
            path = Path(value)
            if path.suffix.lower() in {".parquet", ".pq"} and path.is_file():
                registry_found = bool(read_parquet_section_registry(str(path))) or registry_found
        if not registry_found:
            return params
        from app.services.section_metadata import build_section_manifest
        manifest = build_section_manifest(_catalog_from_paths_for_backend(paths))
    from app.services.section_metadata import validate_section_manifest
    manifest = _hydrate_parquet_registries(manifest)
    errors = validate_section_manifest(manifest)
    if errors:
        raise InputPreparationError(" / ".join(errors))
    params["section_manifest"] = manifest
    return params


def prepare_inputs(params, *, cache_root=None, project_id="", progress=None, cancel=None, **kwargs):
    """すべての選択入力が完成してから、新しい manifest / runtime paths を返す。"""
    params = deepcopy(params)
    from app.services.stage_signatures import validated_import_record
    imported = validated_import_record(params)
    if imported:
        if not params.get("resume_from_rds") or params.get("pipeline_stage") != "downstream_from_reduction":
            raise InputPreparationError("検証済み旧結果は保存reductionからの下流解析として実行してください。")
        check_cancel(cancel)
        params["validated_legacy_import"] = imported["entry"]
        params["legacy_source_manifest"] = params.get("section_manifest")
        params["section_manifest"] = None
        params["input_paths"] = []
        params["_imzml_pipeline_prepared"] = True
        return params
    from app.services.section_metadata import build_section_manifest, validate_section_manifest
    manifest = params.get("section_manifest")
    if not manifest:
        paths = params.get("original_input_paths") or params.get("input_paths") or []
        manifest = build_section_manifest(_catalog_from_paths_for_backend(paths))
    manifest = _hydrate_parquet_registries(manifest)
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
            try:
                alignment_ppm = float(params.get("mz_align_ppm") or 0.0)
            except (TypeError, ValueError) as exc:
                raise InputPreparationError("m/zアライメント(ppm)は数値で指定してください。") from exc
            if not math.isfinite(alignment_ppm) or alignment_ppm < 0:
                raise InputPreparationError("m/zアライメント(ppm)は0以上の有限値で指定してください。")
            prepared[entry["file_id"]] = prepare_imzml(
                entry, cache_root=cache_root, project_id=project_id, pinned=pinned,
                progress=notify, cancel=cancel, alignment_ppm=alignment_ppm, **kwargs
            )
        else:
            if not Path(entry["path"]).is_file():
                raise InputPreparationError(f"入力が見つかりません: {entry['path']}")
            prepared[entry["file_id"]] = {**entry, "runtime_path": entry["path"]}
    manifest["files"] = [prepared.get(e["file_id"], e) for e in manifest["files"]]
    manifest = _hydrate_parquet_registries(manifest)
    post_errors = validate_section_manifest(manifest)
    if post_errors:
        raise InputPreparationError(" / ".join(post_errors))
    params["section_manifest"] = manifest
    paths = runtime_paths(manifest)
    params["input_paths"] = paths
    if "original_input_paths" in params or "original_data_folder" in params:
        params["original_input_paths"] = paths
    params["_imzml_pipeline_prepared"] = True
    return params
