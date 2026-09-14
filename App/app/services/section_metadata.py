"""切片・ROI・個体・群をファイル単位で管理する共通処理。"""
from __future__ import annotations
from copy import deepcopy
import hashlib
from pathlib import Path
SCHEMA_VERSION = 1


def stable_file_id(path: str) -> str:
    normalized = str(Path(path).expanduser().resolve())
    return "file_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def stable_section_id(file_id: str, roi: str | None = None) -> str:
    token = file_id + "\x00" + ("__whole_section__" if roi is None else str(roi))
    return "section_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:20]


def build_section_manifest(catalog, selections=None, roles=None, group_rows=None, previous=None):
    """★ ver67.0: ファイル別の選択と物理切片の意味を分け、全解除を全画素へ戻さない。"""
    selections, roles, previous = selections or {}, roles or {}, previous or {}
    old_files = dict(previous.get("file_settings", {}))
    old_files.update({f["path"]: f for f in previous.get("files", [])})
    saved_sections = dict(previous.get("section_settings", {}))
    for f in previous.get("files", []):
        saved_sections.update({s["section_id"]: s for s in f.get("sections", [])})
    groups = {r["section_id"]: r for r in (group_rows or []) if r.get("section_id")}
    result = {"schema_version": SCHEMA_VERSION, "files": [],
              "file_settings": deepcopy(previous.get("file_settings", {})),
              "section_settings": deepcopy(saved_sections)}
    for key in ("source_rds_path", "source_data_folder"):
        if key in previous:
            result[key] = previous[key]
    for item in catalog or []:
        path = str(item["path"])
        old = old_files.get(path, {})
        fid = old.get("file_id") or item.get("file_id") or stable_file_id(path)
        available = list(dict.fromkeys(str(r) for r in item.get("available_rois", [])))
        role = roles.get(path, old.get("roi_role", item.get("roi_role")))
        if not available:
            role = "region"
        if role not in ("section", "region"):
            role = None
        if path in selections:
            selected = list(selections[path] or [])
            mode = "selected" if selected else "none"
        elif old.get("selection_mode") in ("selected", "none"):
            selected, mode = list(old.get("rois", [])), old["selection_mode"]
        else:
            selected, mode = list(available), "all"
        if available:
            selected = [r for r in selected if r in available]
            # ★ ver67.0: 全ROI選択でも名前のない画素を追加の切片として混入させない。
            mode = "selected" if selected else "none"
        elif path in selections:
            mode = "all" if "__all__" in selected else "none"
            selected = []
        sections = []
        old_sections = dict(saved_sections)
        old_sections.update({r["section_id"]: r for r in old.get("sections", [])})
        if mode != "none" and role:
            for roi in (selected if role == "section" else [None]):
                sid = stable_section_id(fid, roi)
                saved = groups.get(sid, old_sections.get(sid, {}))
                sections.append({"section_id": sid, "roi": roi,
                    "subject_id": str(saved.get("subject_id") or "").strip(),
                    "group": str(saved.get("group") or "").strip(), "integration_unit_id": sid})
        result["files"].append({"file_id": fid, "path": path, "selection_mode": mode,
            "rois": selected, "available_rois": available, "roi_role": role, "sections": sections})
        result["file_settings"][path] = {"file_id": fid, "roi_role": role}
        result["section_settings"].update({r["section_id"]: r for r in sections})
    return result


def manifest_group_rows(manifest):
    rows = []
    for f in (manifest or {}).get("files", []):
        for s in f.get("sections", []):
            label = Path(f["path"]).stem
            if s.get("roi") is not None:
                label += " / " + str(s["roi"])
            rows.append({"section_id": s["section_id"], "section": label,
                "file": str(Path(f["path"]).parent), "subject_id": s.get("subject_id", ""),
                "group": s.get("group", "")})
    return rows


def assign_group(rows, selected_rows, group):
    result = deepcopy(rows or [])
    label = str(group or "").strip()
    if not label:
        return result, "割り当てる群名を入力してください。"
    indices = [i for i in (selected_rows or []) if isinstance(i, int) and 0 <= i < len(result)]
    if not indices:
        return result, "群を割り当てる切片の行を選択してください。"
    for i in indices:
        result[i]["group"] = label
    return result, f"{len(indices)} 切片に「{label}」を設定しました。"


def validate_section_manifest(manifest):
    errors = []
    files = (manifest or {}).get("files", [])
    if not files or not any(f.get("selection_mode") != "none" for f in files):
        errors.append("解析対象の切片／ROIを選択してください。")
    seen_paths = set()
    for f in files:
        path = str(Path(f.get("path") or "").expanduser().resolve())
        if not f.get("path"):
            errors.append("切片対応表の入力ファイルパスがありません。")
        elif path in seen_paths:
            errors.append(f"入力ファイルが重複しています: {Path(path).name}")
        seen_paths.add(path)
        if f.get("selection_mode") not in ("all", "selected", "none"):
            errors.append("切片対応表の選択方式が不正です。")
        if f.get("selection_mode") == "selected" and not f.get("rois"):
            errors.append(f"{Path(path).name}: 解析対象のROIが選択されていません。")
        if f.get("selection_mode") != "none" and f.get("roi_role") not in ("section", "region"):
            errors.append(f"{Path(f['path']).name}: ROI が切片か切片内領域かを指定してください。")
    subject_groups = {}
    for row in manifest_group_rows(manifest):
        subject, group = row["subject_id"], row["group"]
        if subject and group:
            subject_groups.setdefault(subject, set()).add(group)
    for subject, groups in subject_groups.items():
        if len(groups) > 1:
            errors.append(f"個体／独立試料ID「{subject}」に複数の群が設定されています。")
    return errors


def summarize_manifest(manifest):
    rows = manifest_group_rows(manifest)
    errors = validate_section_manifest(manifest)
    n = len(rows)
    # ★ ver67.0: 表の行数ではなく実際に共通化する統合単位数で算出手法を案内する。
    units = {str(s["integration_unit_id"]) for f in (manifest or {}).get("files", [])
             if f.get("selection_mode") != "none" for s in f.get("sections", [])
             if s.get("integration_unit_id")}
    methods = "PCA・Harmony・RPCA" if len(units) >= 2 else "PCA"
    line = f"解析対象：{n}切片 ／ 算出する結果：{methods}"
    groups = {}
    for row in rows:
        if row["group"]:
            info = groups.setdefault(row["group"], {"sections": 0, "subjects": set(), "missing": 0})
            info["sections"] += 1
            if row["subject_id"]:
                info["subjects"].add(row["subject_id"])
            else:
                info["missing"] += 1
    detail = []
    for name, info in groups.items():
        value = f"{name}：{info['sections']}切片・独立試料{len(info['subjects'])}例"
        if info["missing"]:
            value += f"（ID未設定{info['missing']}切片）"
        detail.append(value)
    return line, " ／ ".join(detail), errors
