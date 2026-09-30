"""Read-only legacy audit and verified copies for a new downstream run."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile
import uuid

from app.services.result_catalog import discover_results, file_sha256
from app.services.result_contract import descriptor_method, require_capabilities
from app.services.seurat_bridge import ExtractionCancelled
from app.utils.file_locks import atomic_write_json


def _bridge(bridge):
    if bridge is None:
        from app.services.seurat_bridge import SeuratBridge
        bridge = SeuratBridge()
    return bridge


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _sha256(value):
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value.lower())


def _assess_import_consistency(selected, runtime, records, source):
    """Cell IDs prove correspondence, not shared measurements or a shared run.

    A multi-method import therefore also needs RDS-embedded run/request evidence
    bound to the saved v2 manifest. Unsigned legacy reductions remain individually
    usable; a common folder or matching sample labels do not promote their origin.
    """
    members, cell_sets, counts, run_ids = [], set(), set(), set()
    saved_run = runtime.get("run_id")
    signatures = _mapping(runtime.get("stage_signatures"))
    input_signature = signatures.get("input")
    for row, method, reduction, _ in selected:
        descriptor = row["descriptor"]
        src = _mapping(descriptor.get("source"))
        evidence = _mapping(row.get("provenance_evidence"))
        cells, count, run_id = src.get("cell_set_sha256"), src.get("n_cells"), evidence.get("run_id")
        if _sha256(cells):
            cell_sets.add(cells)
        if type(count) is int:
            counts.add(count)
        if run_id:
            run_ids.add(run_id)
            if saved_run and records.get("intent") != "validated_reduction_import" and run_id != saved_run:
                raise ValueError("RDS と保存条件の実行IDが異なります。別runの結果を同時に取り込めません。")
        matches = []
        for record in _mapping(records.get("methods")).values():
            if not isinstance(record, dict):
                continue
            stage = _mapping(_mapping(record.get("stages")).get("reduction"))
            name = stage.get("rds_path") or record.get("rds_path")
            if not name:
                continue
            path = Path(name)
            path = path if path.is_absolute() else source / path
            if path.resolve() == Path(row["path"]).resolve():
                matches.append((record, stage))
        bound = False
        if len(matches) == 1:
            record, stage = matches[0]
            bound = (records.get("schema_version") == 2 and bool(run_id)
                     and records.get("run_id") == run_id == saved_run
                     and record.get("run_id", run_id) == run_id
                     and stage.get("status") in {"complete", "completed"}
                     and (stage.get("artifact_sha256") or record.get("artifact_sha256")) == src.get("sha256")
                     and runtime.get("signature_schema_version") == 2
                     and _sha256(input_signature)
                     and bool(evidence.get("reduction_request_signature"))
                     and evidence["reduction_request_signature"] == signatures.get(method.lower()))
        members.append({"method": method, "source_result_id": descriptor.get("result_id"),
                        "cell_set_sha256": cells, "n_cells": count, "recorded_run_id": run_id,
                        "input_run_binding": "recorded" if bound else "unknown"})
    if len(cell_sets) > 1 or len(counts) > 1:
        raise ValueError("手法間で解析画素の集合が異なります。別datasetや部分集合を混在させず、--method で1手法ずつ取り込んでください。")
    if len(run_ids) > 1:
        raise ValueError("異なる実行IDのRDSが混在しています。元のrunを分けて取り込んでください。")
    complete_cells = all(_sha256(member["cell_set_sha256"]) and type(member["n_cells"]) is int
                         and member["n_cells"] > 0 for member in members)
    bound = all(member["input_run_binding"] == "recorded" for member in members)
    if len(selected) > 1 and (not complete_cells or not bound):
        raise ValueError("複数RDSの入力・実行由来を確認できません。CellIDやフォルダ名の一致だけでは証明になりません。--method で1手法を明示して個別に取り込んでください。")
    limitations = [] if bound else [
        "元入力および実行との対応は未確認です。検証済みなのはRDSの内容・reduction構造であり、元解析条件の適用は証明していません。"]
    if not complete_cells:
        limitations.append("CellID集合のハッシュを確認できません。")
    return {"schema_version": 1, "state": "recorded_coherent" if bound and complete_cells else "single_origin_unconfirmed",
            "cell_relation": "equal" if len(selected) > 1 else "single",
            "input_run_binding": "recorded" if bound else "unknown",
            "input_signature": input_signature if bound else None,
            "members": members, "limitations": limitations}


def audit_result_folder(folder, *, bridge=None, cancel_event=None):
    """Inspect one run; never search other projects or write to source artifacts."""
    root = Path(folder).expanduser().resolve(strict=True)
    bridge = _bridge(bridge)
    rows = []
    for candidate in discover_results(root):
        path = candidate["rds_path"]
        try:
            result = bridge.extract_data(path, force_verify=True, cancel_event=cancel_event)
            descriptor = result["result_descriptor"]
            caps = descriptor["capabilities"]
            facts = _mapping(_mapping(result.get("meta")).get("result_facts"))
            provenance = _mapping(facts.get("result_provenance"))
            reduction_stage = _mapping(_mapping(provenance.get("stages")).get("reduction"))
            action = ("reexport" if caps.get("independent_comparison") and caps.get("umap")
                      else "downstream" if caps.get("resume") else "upstream")
            rows.append({"path": str(Path(path).resolve()), "descriptor": descriptor,
                         "recommended_action": action,
                         "provenance_evidence": {
                             "run_id": provenance.get("run_id"),
                             "reduction_request_signature": reduction_stage.get("request_signature")}})
        except (InterruptedError, ExtractionCancelled):
            raise
        except Exception as exc:
            rows.append({"path": str(path), "recommended_action": "upstream", "error": str(exc)})
    return {"schema_version": 1, "source_directory": str(root),
            "audited_at": datetime.now(timezone.utc).isoformat(), "artifacts": rows}


def import_reductions(folder, destination, *, methods=None, bridge=None, cancel_event=None):
    """Publish a new folder only after all selected reductions pass verification.

    No method/status signature is written into old RDS. The import manifest is a
    hash-bound permission to read those exact reductions, not a new calculation.
    """
    source = Path(folder).expanduser().resolve(strict=True)
    target = Path(destination).expanduser().resolve()
    if target.exists() or target == source or source in target.parents:
        raise ValueError("取込先は元結果の外にある、未作成のフォルダを指定してください。")
    params_path = source / "analysis_params.json"
    if not params_path.is_file():
        raise ValueError("元の解析条件がありません。上流から新規解析してください。")
    params_sha256 = file_sha256(params_path, cancel_event)
    record = json.loads(params_path.read_text(encoding="utf-8"))
    method_path = source / "analysis_methods.json"
    method_sha256 = file_sha256(method_path, cancel_event) if method_path.is_file() else None
    method_records = json.loads(method_path.read_text(encoding="utf-8")) if method_sha256 else {}
    runtime = deepcopy(record.get("runtime_parameters") or record)
    if not isinstance(runtime, dict) or not runtime:
        raise ValueError("元の解析条件を確認できません。")
    audit = audit_result_folder(source, bridge=bridge, cancel_event=cancel_event)
    selected = []
    wanted = {str(x).casefold() for x in methods} if methods else None
    seen = set()
    for row in audit["artifacts"]:
        descriptor = row.get("descriptor") or {}
        method = descriptor_method(descriptor)
        if wanted is not None and str(method).casefold() not in wanted:
            continue
        require_capabilities(descriptor, "resume")
        if descriptor.get("classification", {}).get("state") not in {"recorded", "inferred"}:
            raise ValueError("計算由来を確認できない reduction は取込できません。")
        if method in seen:
            raise ValueError(f"{method} の候補が複数あります。元の実行を分けて指定してください。")
        seen.add(method)
        reduction = descriptor["classification"].get("reduction")
        facts = descriptor.get("reductions", {}).get(reduction) or {}
        if not reduction or not facts.get("assay") or int(facts.get("n_dims") or 0) < 2:
            raise ValueError(f"{method}: reduction の assay／保存次元を確認できません。")
        selected.append((row, method, reduction, facts))
    if not selected or (wanted is not None and wanted != {str(x).casefold() for x in seen}):
        raise ValueError("指定した全手法の検証済み reduction が必要です。")
    consistency = _assess_import_consistency(selected, runtime, _mapping(method_records), source)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".ua-import-", dir=target.parent))
    try:
        artifacts = []
        names = {"PCA": "Step2_PCA_uncorrected.rds", "Harmony": "Step2_HarmonyPCA_Result.rds",
                 "RPCA": "Step3_RPCA_Result.rds"}
        (staging / "RDS_Files").mkdir()
        for row, method, reduction, facts in selected:
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("取込をキャンセルしました")
            original = Path(row["path"])
            descriptor = row["descriptor"]
            expected = descriptor["source"]["sha256"]
            relative = "RDS_Files/" + names[method]
            copied = staging / relative
            shutil.copyfile(original, copied)
            if (file_sha256(copied, cancel_event) != expected
                    or file_sha256(original, cancel_event) != expected):
                raise ValueError("検証後に元RDSが変更されました。再監査してください。")
            artifacts.append({"path": relative, "sha256": expected, "method": method,
                              "reduction": reduction, "n_cells": descriptor["source"]["n_cells"],
                              "n_dims": int(facts["n_dims"]), "assay": facts["assay"],
                              "source_result_id": descriptor["result_id"], "descriptor": descriptor})
        manifest = {"schema_version": 1, "kind": "validated_reduction_import",
                    "import_id": uuid.uuid4().hex, "artifacts": artifacts,
                    "source_directory": str(source), "created_at": datetime.now(timezone.utc).isoformat(),
                    "consistency": consistency,
                    "source_records": {"analysis_params_sha256": params_sha256,
                                       "analysis_methods_sha256": method_sha256}}
        manifest_name = "legacy_import.json"
        atomic_write_json(manifest, staging / manifest_name)
        runtime["legacy_signatures"] = {key: runtime[key] for key in
                                       ("analysis_signature", "reduction_signature", "stage_signatures")
                                       if runtime.get(key)}
        for key in ("analysis_signature", "reduction_signature", "stage_signatures", "signature_schema_version",
                    "run_id", "parent_run_id"):
            runtime.pop(key, None)
        runtime["validated_legacy_import"] = {
            "manifest_path": str(target / manifest_name),
            "manifest_sha256": file_sha256(staging / manifest_name)}
        runtime["pipeline_stage"] = "reduction_only"
        runtime["execution_policy"] = "validated_legacy"
        runtime["legacy_import_consistency"] = consistency
        runtime["run_id"] = manifest["import_id"]
        atomic_write_json({"timestamp": manifest["created_at"], "runtime_parameters": runtime,
                           "legacy_source_parameters": record}, staging / "analysis_params.json")
        atomic_write_json(audit, staging / "legacy_audit.json")
        atomic_write_json({"schema_version": 2, "run_id": manifest["import_id"],
                           "intent": "validated_reduction_import", "methods": {
                               a["method"].lower(): {
                                   "result_id": a["source_result_id"], "status": "complete",
                                   "stage": "reduction", "rds_path": a["path"],
                                   "artifact_sha256": a["sha256"], "origin": "validated_legacy_import",
                                   "stages": {"reduction": {"status": "complete", "rds_path": a["path"],
                                                            "effective": {"n_dims": a["n_dims"]},
                                                            "artifact_sha256": a["sha256"]}}}
                               for a in artifacts}}, staging / "analysis_methods.json")
        if target.exists():
            raise ValueError("取込先が作成されました。別の空きフォルダを指定してください。")
        if (file_sha256(params_path, cancel_event) != params_sha256 or
                (file_sha256(method_path, cancel_event) if method_path.is_file() else None) != method_sha256):
            raise ValueError("監査後に保存条件または手法記録が変更されました。再監査してください。")
        staging.rename(target)
    finally:
        if staging.exists():
            if not staging.resolve().is_relative_to(target.parent.resolve()):
                raise ValueError("取込の一時フォルダが指定先の外を参照しています。")
            shutil.rmtree(staging)
    return {"directory": str(target), "manifest": manifest,
            "manifest_sha256": file_sha256(target / "legacy_import.json")}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description="旧結果を読取専用で監査し、必要に応じて別フォルダへ検証済みコピーを作成")
    parser.add_argument("source")
    parser.add_argument("--report", help="診断JSONの保存先（元結果の外）")
    parser.add_argument("--import-to", dest="destination", help="未作成の取込先フォルダ")
    parser.add_argument("--method", action="append", choices=["PCA", "Harmony", "RPCA"])
    parser.add_argument("--project-id", help="取込結果を登録する既存project ID")
    parser.add_argument("--subproject-id", help="取込結果を登録する既存subproject ID")
    args = parser.parse_args(argv)
    if args.project_id or args.subproject_id:
        if not (args.destination and args.project_id and args.subproject_id):
            parser.error("登録には --import-to / --project-id / --subproject-id の全てが必要です")
        from app.services.project_manager import get_sub_project
        if not get_sub_project(args.project_id, args.subproject_id):
            parser.error("指定したサブプロジェクトが存在しません")
    if args.report:
        report = Path(args.report).resolve()
        source = Path(args.source).resolve()
        if source == report or source in report.parents:
            parser.error("診断出力は元結果の外へ指定してください")
    result = (import_reductions(args.source, args.destination, methods=args.method)
              if args.destination else audit_result_folder(args.source))
    if args.project_id:
        from app.services.project_manager import save_sub_project_result_dir
        save_sub_project_result_dir(args.project_id, args.subproject_id, result["directory"])
    if args.report:
        atomic_write_json(result, args.report)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
