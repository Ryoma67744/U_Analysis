"""Purpose-specific result references; never join methods from different runs."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid


def _read_json(path):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve_result_dir(sub, purpose="analysis"):
    """Resolve a selected subproject, without scanning siblings or other projects."""
    if purpose not in {"analysis", "reduction"}:
        raise ValueError("結果の用途が不正です")
    sub = sub or {}
    refs = sub.get("result_refs") or {}
    ref = refs.get(purpose)
    if isinstance(ref, dict):
        return "" if ref.get("unresolved") else str(ref.get("path") or "")
    legacy = sub.get("last_result_dir") or sub.get("output_dir") or ""
    reduction = refs.get("reduction") or {}
    if purpose == "analysis" and reduction.get("path") == legacy:
        return ""  # A known reduction-only result is not a finished cluster analysis.
    return str(legacy)


def _order(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except (ValueError, TypeError):
        return float("-inf")


def describe_result_reference(result_dir):
    """Use the writer's validated stage outcome; legacy absence stays unknown."""
    from app.services.execution_policy import method_outcome

    root = Path(result_dir)
    record = _read_json(root / "analysis_params.json")
    params = record.get("runtime_parameters") or record
    methods = _read_json(root / "analysis_methods.json")
    job = _read_json(root / "log" / "analysis_job.json")
    outcome = method_outcome(root)
    purposes = []
    if outcome:
        reductions = outcome.get("completed_reductions", [])
        analyses = outcome.get("completed_analyses", [])
        # v1's complete stage has a narrower, but still useful, meaning.
        if "completed_reductions" not in outcome and outcome.get("available"):
            reductions = outcome["available"]
            analyses = [] if outcome.get("reduction_only") else outcome["available"]
        if reductions:
            purposes.append("reduction")
        if analyses:
            purposes.append("analysis")
    run_id = methods.get("run_id") or params.get("run_id") or job.get("run_id")
    if not run_id:
        run_id = "legacy-" + uuid.uuid5(uuid.NAMESPACE_URL, str(root.resolve())).hex
    return {
        "run_id": str(run_id), "path": str(root), "purposes": purposes,
        "started_at": job.get("started_at") or record.get("timestamp") or "",
        "registered_at": datetime.now(timezone.utc).isoformat(),
        "data_folder": params.get("original_data_folder") or params.get("data_folder") or job.get("data_folder") or "",
        "available_methods": (outcome or {}).get("available", []),
        "incomplete_methods": (outcome or {}).get("incomplete", []),
        "known_state": outcome is not None,
    }


def apply_result_reference(sub, reference):
    """Return a patch. Late completion can add history but cannot rewind a pointer."""
    refs = deepcopy(sub.get("result_refs") or {})
    history = deepcopy(sub.get("result_history") or [])
    public = {k: deepcopy(v) for k, v in reference.items()
              if k not in {"purposes", "known_state"}}
    history = [r for r in history if r.get("run_id") != public["run_id"]]
    history.append(public)
    for purpose in reference["purposes"]:
        old = refs.get(purpose) or {}
        if old and _order(public.get("started_at")) < _order(old.get("started_at")):
            continue
        refs[purpose] = deepcopy(public)
    patch = {"result_refs": refs, "result_history": history}
    analysis = refs.get("analysis") or {}
    reduction = refs.get("reduction") or {}
    if analysis.get("path") and not analysis.get("unresolved"):
        patch["last_result_dir"] = analysis["path"]
    elif reduction.get("path"):
        previous = sub.get("last_result_dir") or ""
        # Keep a pre-v2 analysis reference until its contents have been inspected.
        previous_reduction = (sub.get("result_refs") or {}).get("reduction") or {}
        patch["last_result_dir"] = (
            previous if previous and previous != previous_reduction.get("path")
            and previous != reference["path"] else reduction["path"]
        )
    elif not reference["known_state"]:
        patch["last_result_dir"] = reference["path"]
    return patch


def restored_result_patch(meta, found_dir):
    """Relocate only the run belonging to this metadata file, never sibling runs."""
    sub = meta.get("sub_project") or {}
    refs = deepcopy(sub.get("result_refs") or {})
    this_run = meta.get("this_run_id")
    for ref in refs.values():
        if not isinstance(ref, dict):
            continue
        if this_run and ref.get("run_id") == this_run:
            ref.update(path=str(found_dir), unresolved=False)
        else:
            ref["unresolved"] = True
    patch = {"last_result_dir": str(found_dir)}
    if refs:
        patch["result_refs"] = refs
        analysis = refs.get("analysis") or {}
        if analysis.get("path") and not analysis.get("unresolved"):
            patch["last_result_dir"] = analysis["path"]
    if sub.get("result_history"):
        patch["result_history"] = [
            {**r, "path": str(found_dir), "unresolved": False}
            if this_run and r.get("run_id") == this_run else {**r, "unresolved": True}
            for r in sub["result_history"] if isinstance(r, dict)
        ]
    return patch
