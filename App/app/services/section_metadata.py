"""切片・ROI・個体・群をファイル単位で管理する共通処理。"""
from __future__ import annotations
from copy import deepcopy
import hashlib
from pathlib import Path

SCHEMA_VERSION = 2


def stable_file_id(path: str) -> str:
    normalized = str(Path(path).expanduser().resolve())
    return "file_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def stable_section_id(file_id: str, roi: str | None = None) -> str:
    token = file_id + "\x00" + ("__whole_section__" if roi is None else str(roi))
    return "section_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:20]


def _saved_section_sources(previous, old):
    saved = dict(previous.get("section_settings", {}))
    for file_entry in previous.get("files", []):
        for section in file_entry.get("spatial_sections", file_entry.get("sections", [])):
            if section.get("section_id"):
                saved[section["section_id"]] = section
    for section in old.get("spatial_sections", old.get("sections", [])):
        if section.get("section_id"):
            saved[section["section_id"]] = section
    return saved


# ★ ver71.0: 座標component構成と表示名を分離し、名称変更だけでsection IDを変えない。
def _build_spatial_file(item, old, fid, selections, groups, previous):
    from app.services.imzml_spatial_layout import normalize_spatial_sections, SpatialLayoutError

    error = str(item.get("spatial_error") or old.get("spatial_error") or "").strip()
    layout = deepcopy(item.get("spatial_layout") or old.get("spatial_layout") or {})
    if error or not layout.get("components"):
        return {
            "file_id": fid, "path": str(item["path"]), "selection_mode": "none",
            "rois": [], "available_rois": [], "roi_role": "spatial", "sections": [],
            "spatial_sections": [], "spatial_layout": layout, "spatial_error": error or "座標layoutがありません。",
        }

    old_layout = old.get("spatial_layout") or {}
    same_layout = (not old_layout or old_layout.get("coordinate_hash") == layout.get("coordinate_hash"))
    explicit_candidate = item.get("spatial_sections") is not None
    candidate_sections = item.get("spatial_sections")
    if candidate_sections is None and same_layout:
        candidate_sections = old.get("spatial_sections")
    try:
        spatial_sections = normalize_spatial_sections(fid, layout, candidate_sections)
    except SpatialLayoutError as exc:
        return {
            "file_id": fid, "path": str(item["path"]), "selection_mode": "none",
            "rois": [], "available_rois": [], "roi_role": "spatial", "sections": [],
            "spatial_sections": [], "spatial_layout": layout, "spatial_error": str(exc),
        }

    saved = _saved_section_sources(previous, old)
    for row in spatial_sections:
        source = groups.get(row["section_id"])
        if source is None and not explicit_candidate:
            source = saved.get(row["section_id"], {})
        source = source or {}
        for key in ("section_display_name", "subject_id", "group"):
            if key in source:
                row[key] = str(source.get(key) or "").strip()
        row["integration_unit_id"] = row["section_id"]
        row["roi"] = None

    if str(item["path"]) in selections:
        selected_ids = set(str(x) for x in (selections[str(item["path"])] or []))
        for row in spatial_sections:
            row["selected"] = row["section_id"] in selected_ids
    elif candidate_sections is None and not old.get("spatial_sections"):
        for row in spatial_sections:
            row["selected"] = True

    selected_sections = [deepcopy(row) for row in spatial_sections if row.get("selected", True)]
    mode = "selected" if selected_sections else "none"
    return {
        "file_id": fid, "path": str(item["path"]), "selection_mode": mode,
        "rois": [], "available_rois": [], "roi_role": "spatial",
        "sections": selected_sections, "spatial_sections": spatial_sections,
        "spatial_layout": layout, "spatial_error": "",
    }


def build_section_manifest(catalog, selections=None, roles=None, group_rows=None, previous=None):
    """ファイル選択、ROI切片、imzML座標切片を同一manifestへ確定する。"""
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
        is_spatial = bool(item.get("spatial_layout") or old.get("spatial_layout")
                          or item.get("spatial_error") or old.get("roi_role") == "spatial")

        if is_spatial:
            core = _build_spatial_file(item, old, fid, selections, groups, previous)
        else:
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
                    display = str(saved.get("section_display_name") or
                                  (str(roi) if roi is not None else Path(path).stem)).strip()
                    sections.append({"section_id": sid, "roi": roi,
                        "section_display_name": display,
                        "subject_id": str(saved.get("subject_id") or "").strip(),
                        "group": str(saved.get("group") or "").strip(), "integration_unit_id": sid})
            core = {"file_id": fid, "path": path, "selection_mode": mode,
                    "rois": selected, "available_rois": available,
                    "roi_role": role, "sections": sections}

        # 原本→runtimeの対応・固定revisionをGUI再構築で消さない。
        from app.services.input_preparation import DESCRIPTOR_KEYS
        descriptors = {k: deepcopy(old[k] if k in old else item[k])
                       for k in DESCRIPTOR_KEYS if k in old or k in item}
        entry = {**descriptors, **core}
        result["files"].append(entry)
        file_settings = {**descriptors, "file_id": fid, "roi_role": entry.get("roi_role")}
        for key in ("spatial_layout", "spatial_sections", "spatial_error"):
            if key in entry:
                file_settings[key] = deepcopy(entry[key])
        result["file_settings"][path] = file_settings
        result["section_settings"].update({r["section_id"]: deepcopy(r)
                                           for r in entry.get("spatial_sections", entry.get("sections", []))})
    return result


def manifest_group_rows(manifest):
    rows = []
    for f in (manifest or {}).get("files", []):
        for s in f.get("sections", []):
            display = str(s.get("section_display_name") or "").strip()
            if f.get("roi_role") == "spatial":
                label = f"{Path(f['path']).stem} / {display or s['section_id']}"
            else:
                label = Path(f["path"]).stem
                if s.get("roi") is not None:
                    label += " / " + str(s["roi"])
            rows.append({"section_id": s["section_id"], "section": label,
                "section_display_name": display or label,
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


def _validate_spatial_file(f, errors):
    layout = f.get("spatial_layout") or {}
    if f.get("spatial_error"):
        errors.append(f"{Path(f.get('path', '')).name}: {f['spatial_error']}")
        return
    components = {str(c.get("component_id")): int(c.get("pixel_count", 0))
                  for c in layout.get("components", []) if c.get("component_id")}
    rows = f.get("spatial_sections") or []
    if not components or not rows:
        errors.append(f"{Path(f.get('path', '')).name}: 座標切片情報がありません。")
        return
    seen_components, seen_sections, names = set(), set(), []
    selected_ids = set()
    for row in rows:
        sid = str(row.get("section_id") or "")
        ids = [str(x) for x in row.get("component_ids", [])]
        name = str(row.get("section_display_name") or "").strip()
        if not sid or sid in seen_sections:
            errors.append(f"{Path(f['path']).name}: section_id が欠落または重複しています。")
        seen_sections.add(sid)
        if not name:
            errors.append(f"{Path(f['path']).name}: 切片名は空にできません。")
        names.append(name)
        for cid in ids:
            if cid not in components:
                errors.append(f"{Path(f['path']).name}: 未知の座標componentです: {cid}")
            if cid in seen_components:
                errors.append(f"{Path(f['path']).name}: componentが複数切片へ重複しています: {cid}")
            seen_components.add(cid)
        expected_pixels = sum(components.get(cid, 0) for cid in ids)
        if int(row.get("pixel_count", -1)) != expected_pixels:
            errors.append(f"{Path(f['path']).name}: 切片の画素数が一致しません: {name}")
        if row.get("selected", True):
            selected_ids.add(sid)
    if seen_components != set(components):
        errors.append(f"{Path(f['path']).name}: componentの所属が欠落しています。")
    if len(names) != len(set(names)):
        errors.append(f"{Path(f['path']).name}: 切片名が重複しています。")
    manifest_selected = {str(s.get("section_id")) for s in f.get("sections", [])}
    if manifest_selected != selected_ids:
        errors.append(f"{Path(f['path']).name}: 選択状態と解析対象切片が一致しません。")
    if f.get("selection_mode") != ("selected" if selected_ids else "none"):
        errors.append(f"{Path(f['path']).name}: 座標切片の選択方式が一致しません。")


def validate_section_manifest(manifest):
    errors = []
    files = (manifest or {}).get("files", [])
    if not files or not any(f.get("selection_mode") != "none" for f in files):
        errors.append("解析対象の切片／ROIを選択してください。")
    from app.services.input_preparation import validate_selected_paths, InputPreparationError
    try:
        selected = [f["path"] for f in files if f.get("path") and f.get("selection_mode") != "none"]
        if selected:
            validate_selected_paths(selected)
    except InputPreparationError as exc:
        errors.append(str(exc))
    seen_paths, seen_section_ids = set(), set()
    for f in files:
        path = str(Path(f.get("path") or "").expanduser().resolve())
        if not f.get("path"):
            errors.append("切片対応表の入力ファイルパスがありません。")
        elif path in seen_paths:
            errors.append(f"入力ファイルが重複しています: {Path(path).name}")
        seen_paths.add(path)
        if f.get("selection_mode") not in ("all", "selected", "none"):
            errors.append("切片対応表の選択方式が不正です。")
        if f.get("roi_role") == "spatial":
            _validate_spatial_file(f, errors)
        else:
            if f.get("selection_mode") == "selected" and not f.get("rois"):
                errors.append(f"{Path(path).name}: 解析対象のROIが選択されていません。")
            if f.get("selection_mode") != "none" and f.get("roi_role") not in ("section", "region"):
                errors.append(f"{Path(f['path']).name}: ROI が切片か切片内領域かを指定してください。")
        for section in f.get("sections", []):
            sid = str(section.get("section_id") or "")
            if not sid or sid in seen_section_ids:
                errors.append("section_id がファイル間で欠落または重複しています。")
            seen_section_ids.add(sid)
    subject_groups = {}
    for row in manifest_group_rows(manifest):
        subject, group = row["subject_id"], row["group"]
        if subject and group:
            subject_groups.setdefault(subject, set()).add(group)
    for subject, groups in subject_groups.items():
        if len(groups) > 1:
            errors.append(f"個体／独立試料ID「{subject}」に複数の群が設定されています。")
    return list(dict.fromkeys(errors))


def summarize_manifest(manifest):
    rows = manifest_group_rows(manifest)
    errors = validate_section_manifest(manifest)
    n = len(rows)
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
