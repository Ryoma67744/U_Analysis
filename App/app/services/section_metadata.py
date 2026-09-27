"""切片・ROI・個体・群をファイル単位で管理する共通処理。"""
from __future__ import annotations
from copy import deepcopy
import hashlib
from pathlib import Path
from app.services.section_completeness import is_metadata_confirmed

SCHEMA_VERSION = 3


def stable_file_id(path: str) -> str:
    normalized = str(Path(path).expanduser().resolve())
    return "file_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]


def stable_section_id(file_id: str, roi: str | None = None) -> str:
    token = file_id + "\x00" + ("__whole_section__" if roi is None else str(roi))
    return "section_" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:20]



# ★ ver74.0: footer登録と可変metadataを混ぜると再openで旧値へ戻るため分離する。
_METADATA_FIELDS = ("section_display_name", "subject_id", "group", "metadata_confirmed")


def effective_registered_sections(entry):
    """原登録を保持したまま、明示overlayを適用した全切片を返す。"""
    from app.services.section_completeness import is_metadata_confirmed
    rows = deepcopy(entry.get("registered_sections") or entry.get("spatial_sections") or [])
    overlay = entry.get("metadata_overlay") or {}
    known = {str(row.get("section_id")) for row in rows}
    if not isinstance(overlay, dict) or set(overlay) - known:
        raise ValueError("metadata overlayに未登録section_idがあります。")
    for row in rows:
        row["annotation_label"] = row.get("annotation_label", row.get("section_display_name", ""))
        patch = overlay.get(str(row.get("section_id")), {})
        if not isinstance(patch, dict) or set(patch) - set(_METADATA_FIELDS):
            raise ValueError("metadata overlayの項目が不正です。")
        for key, value in patch.items():
            row[key] = (is_metadata_confirmed(value) if key == "metadata_confirmed"
                        else str(value or "").strip())
    return rows


def restore_registered_selection(entry, registry=None):
    """全登録と明示ID選択から派生表を復元し、空選択を全選択へ戻さない。"""
    result = deepcopy(entry)
    if registry is not None:
        result["registered_sections"] = deepcopy(registry)
    rows = effective_registered_sections(result)
    known = {str(row.get("section_id")) for row in rows}
    if "selected_section_ids" in result:
        selected = [str(value) for value in result["selected_section_ids"]]
    elif "sections" in result:
        selected = [str(row.get("section_id")) for row in result["sections"]]
    elif result.get("selection_mode") == "none":
        selected = []
    elif result.get("spatial_sections"):
        selected = [str(row.get("section_id")) for row in result["spatial_sections"]
                    if row.get("selected") is True]
    else:
        selected = [str(row.get("section_id")) for row in rows]
    if len(selected) != len(set(selected)) or set(selected) - known:
        raise ValueError("解析対象section_idが重複または未登録です。")
    selected_set = set(selected)
    spatial = result.get("roi_role") == "spatial"
    for row in rows:
        sid = str(row["section_id"])
        row.update(selected=sid in selected_set, integration_unit_id=sid,
                   roi=None if spatial else row["annotation_label"])
    by_id = {str(row["section_id"]): row for row in rows}
    result["selected_section_ids"] = selected
    result["sections"] = [deepcopy(by_id[sid]) for sid in selected]
    if spatial:
        result["spatial_sections"] = rows
    else:
        result["available_rois"] = [row["annotation_label"] for row in rows]
    result["rois"] = [] if spatial else [by_id[sid]["annotation_label"] for sid in selected]
    result["selection_mode"] = "selected" if selected else "none"
    return result


def apply_metadata_updates(manifest, rows, *, confirmed=False, registration_revision=False):
    """明示された編集だけをoverlayへ保存し、元解析の登録値は改変しない。"""
    from app.services.section_completeness import is_metadata_confirmed
    result = deepcopy(manifest or {})
    lookup = {}
    for row in rows or []:
        sid = str(row.get("section_id") or "")
        if not sid or sid in lookup:
            raise ValueError("編集対象section_idが欠落または重複しています。")
        lookup[sid] = row
    known = set()
    for entry in result.get("files", []):
        registered = bool(entry.get("registered_sections"))
        current = effective_registered_sections(entry) if registered else entry.get("sections", [])
        for row in current:
            sid = str(row.get("section_id"))
            known.add(sid)
            if sid not in lookup:
                continue
            update = lookup[sid]
            patch = {key: str(update[key] or "").strip() for key in _METADATA_FIELDS[:-1]
                     if key in update}
            changed = any(row.get(key, "") != value for key, value in patch.items())
            if registered:
                if changed or "metadata_confirmed" in update:
                    patch["metadata_confirmed"] = (is_metadata_confirmed(update["metadata_confirmed"])
                        if "metadata_confirmed" in update else (True if confirmed or Path(entry.get("path", "")).suffix.lower() in {".parquet", ".pq"} else False))
                    entry.setdefault("metadata_overlay", {}).setdefault(sid, {}).update(patch)
                    if (registration_revision and not result.get("source_rds_path")
                            and "source_selected_section_ids" not in entry
                            and Path(entry.get("path", "")).suffix.lower() == ".imzml"):
                        entry["registration_revision_requested"] = True
            else:
                row.update(patch)
        if registered:
            entry.update(restore_registered_selection(entry))
        result.setdefault("section_settings", {}).update({str(row["section_id"]): deepcopy(row)
            for row in (effective_registered_sections(entry) if registered else entry.get("sections", []))})
        if entry.get("path") in result.get("file_settings", {}):
            result["file_settings"][entry["path"]].update(deepcopy(entry))
    if set(lookup) - known:
        raise ValueError("編集対象に未登録section_idがあります。")
    return result


def _saved_section_sources(previous, old):
    saved = dict(previous.get("section_settings", {}))
    for file_entry in previous.get("files", []):
        source = (effective_registered_sections(file_entry) if file_entry.get("registered_sections")
                  else file_entry.get("spatial_sections") or file_entry.get("sections", []))
        for section in source:
            if section.get("section_id"):
                saved[section["section_id"]] = section
    for section in (effective_registered_sections(old) if old.get("registered_sections")
                    else old.get("spatial_sections") or old.get("sections", [])):
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
        changed = False
        for key in ("section_display_name", "subject_id", "group"):
            if key in source:
                value = str(source.get(key) or "").strip()
                changed = changed or value != str(row.get(key) or "").strip()
                row[key] = value
        if changed:
            # 登録済み内容の変更後は、解析対象外の切片を含め再確認を必須にする。
            row["metadata_confirmed"] = False
        elif "metadata_confirmed" in source:
            from app.services.section_completeness import is_metadata_confirmed
            row["metadata_confirmed"] = is_metadata_confirmed(
                source.get("metadata_confirmed", False)
            )
        row["integration_unit_id"] = row["section_id"]
        row["roi"] = None

    if str(item["path"]) in selections:
        explicit_ids = [str(x) for x in (selections[str(item["path"])] or [])]
        selected_ids = set(explicit_ids)
        if len(explicit_ids) != len(selected_ids) or selected_ids - {row["section_id"] for row in spatial_sections}:
            raise ValueError("解析対象section_idが重複または未登録です。")
        for row in spatial_sections:
            row["selected"] = row["section_id"] in selected_ids
    elif "selected_section_ids" in item or ("selected_section_ids" in old and not explicit_candidate):
        explicit_ids = list(map(str, item.get("selected_section_ids", old.get("selected_section_ids", []))))
        selected_ids = set(explicit_ids)
        known_ids = {row["section_id"] for row in spatial_sections}
        if len(explicit_ids) != len(selected_ids) or selected_ids - known_ids:
            raise ValueError("解析対象section_idが重複または未登録です。")
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
        "registered_sections": deepcopy(spatial_sections),
        "selected_section_ids": [row["section_id"] for row in selected_sections],
        "spatial_layout": layout, "spatial_error": "",
    }


def build_section_manifest(catalog, selections=None, roles=None, group_rows=None, previous=None):
    """ファイル選択、ROI切片、imzML座標切片を同一manifestへ確定する。"""
    selections, roles, previous = selections or {}, roles or {}, previous or {}
    old_files = dict(previous.get("file_settings", {}))
    old_files.update({f["path"]: f for f in previous.get("files", [])})
    saved_sections = dict(previous.get("section_settings", {}))
    for f in previous.get("files", []):
        source = effective_registered_sections(f) if f.get("registered_sections") else f.get("spatial_sections") or f.get("sections", [])
        saved_sections.update({s["section_id"]: s for s in source})
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
        # ★ ver74.0: 再build時も不正IDをset化して消さず、原入力を検査する。
        if "selected_section_ids" in old:
            saved_ids = list(map(str, old["selected_section_ids"]))
            known_ids = {str(row.get("section_id")) for row in
                         (old.get("registered_sections") or old.get("spatial_sections") or old.get("sections", []))}
            if len(saved_ids) != len(set(saved_ids)) or set(saved_ids) - known_ids:
                raise ValueError("保存済み解析対象section_idが重複または未登録です。")
        fid = old.get("file_id") or item.get("file_id") or stable_file_id(path)
        is_spatial = bool(item.get("spatial_layout") or old.get("spatial_layout")
                          or item.get("spatial_error") or old.get("roi_role") == "spatial")

        if is_spatial:
            core = _build_spatial_file(item, old, fid, selections, {}, previous)
            if "source_selected_section_ids" in item or "source_selected_section_ids" in old:
                core["source_selected_section_ids"] = deepcopy(item.get("source_selected_section_ids", old.get("source_selected_section_ids")))
            # ★ ver74.0: explicit draftは新revisionの意思。固定footerとの比較用原登録を残す。
            if old.get("registered_sections"):
                from app.services.section_completeness import normalized_registered_sections
                candidate = deepcopy(core.get("spatial_sections") or [])
                core["registered_sections"] = deepcopy(old["registered_sections"])
                core["metadata_overlay"] = deepcopy(old.get("metadata_overlay") or {})
                if item.get("spatial_sections") is not None:
                    old_effective = normalized_registered_sections(effective_registered_sections(old))
                    new_effective = normalized_registered_sections(candidate)
                    # annotation_labelは派生値なので登録差分には含めない。
                    for values in (old_effective, new_effective):
                        for value in values:
                            value.pop("annotation_label", None)
                    if old_effective != new_effective:
                        geometry = lambda values: {(r["section_id"], tuple(r.get("component_ids", [])), r.get("pixel_count")) for r in values}
                        if geometry(old_effective) != geometry(new_effective):
                            core["registration_parent_sections"] = deepcopy(old["registered_sections"])
                            core["registered_sections"] = new_effective
                            core["metadata_overlay"] = {}
                            core["registration_revision_requested"] = True
                            core = restore_registered_selection(core)
                        else:
                            core = apply_metadata_updates({"files": [core]}, candidate,
                                registration_revision=True)["files"][0]
        else:
            registry = deepcopy(
                item.get("section_registry") or item.get("registered_sections")
                or old.get("registered_sections") or []
            )
            if registry:
                from app.services.section_completeness import normalized_registered_sections
                registered = normalized_registered_sections(registry)
                available = [row["section_display_name"] for row in registered]
                role = "section"
                if path in selections:
                    selected = [str(value) for value in (selections[path] or [])]
                elif "selected_section_ids" in item or "selected_section_ids" in old:
                    selected = list(map(str, item.get("selected_section_ids", old.get("selected_section_ids", []))))
                elif old.get("selection_mode") == "none":
                    selected = []
                else:
                    selected = list(available)
                by_id = {row["section_id"]: row["section_display_name"] for row in registered}
                selected = [by_id.get(value, value) for value in selected]
                if len(selected) != len(set(selected)) or set(selected) - set(available):
                    raise ValueError("解析対象section_id/annotationが重複または未登録です。")
                mode = "selected" if selected else "none"
                sections = []
                for row in registered:
                    if row["section_display_name"] not in selected:
                        continue
                    section = deepcopy(row)
                    section["roi"] = row["section_display_name"]
                    section["integration_unit_id"] = row["section_id"]
                    sections.append(section)
                core = {
                    "file_id": fid, "path": path, "selection_mode": mode,
                    "rois": selected, "available_rois": available,
                    "roi_role": role, "sections": sections,
                    "registered_sections": registered,
                    "selected_section_ids": [row["section_id"] for row in sections],
                }
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
                            "group": str(saved.get("group") or "").strip(),
                            "metadata_confirmed": is_metadata_confirmed(saved.get("metadata_confirmed", False)),
                            "integration_unit_id": sid})
                core = {"file_id": fid, "path": path, "selection_mode": mode,
                        "rois": selected, "available_rois": available,
                        "roi_role": role, "sections": sections}

        if core.get("registered_sections"):
            core.setdefault("metadata_overlay", deepcopy(old.get("metadata_overlay") or {}))
            for state_key in ("registration_revision_requested", "registration_parent_sections"):
                if state_key in old:
                    core.setdefault(state_key, deepcopy(old[state_key]))
            core = restore_registered_selection(core)
            matching_groups = [row for sid, row in groups.items()
                               if sid in {r["section_id"] for r in core["registered_sections"]}]
            if matching_groups:
                # 補助表の保存/実行は明示適用。確認のないraw登録draftはfloat側で検証する。
                core = apply_metadata_updates({"files": [core]}, matching_groups,
                    confirmed=Path(path).suffix.lower() != ".imzml", registration_revision=True)["files"][0]

        # 原本→runtimeの対応・固定revisionをGUI再構築で消さない。
        from app.services.input_preparation import DESCRIPTOR_KEYS
        descriptors = {k: deepcopy(old[k] if k in old else item[k])
                       for k in DESCRIPTOR_KEYS if k in old or k in item}
        # ★ ver74.0: 次の再解析では今回の元RDSの実選択が上限。祖先runの広いcapへ戻さない。
        if "source_selected_section_ids" in item:
            descriptors["source_selected_section_ids"] = deepcopy(item["source_selected_section_ids"])
        entry = {**descriptors, **core}
        result["files"].append(entry)
        file_settings = {**descriptors, "file_id": fid, "roi_role": entry.get("roi_role")}
        for key in ("spatial_layout", "spatial_sections", "registered_sections", "selected_section_ids", "spatial_error", "metadata_overlay", "registration_revision_requested", "registration_parent_sections"):
            if key in entry:
                file_settings[key] = deepcopy(entry[key])
        result["file_settings"][path] = file_settings
        result["section_settings"].update({r["section_id"]: deepcopy(r)
                                           for r in (entry.get("registered_sections")
                                                     or entry.get("spatial_sections")
                                                     or entry.get("sections", []))})
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
        if row.get("selected") is True:
            selected_ids.add(sid)
    if seen_components != set(components):
        errors.append(f"{Path(f['path']).name}: componentの所属が欠落しています。")
    if len(names) != len(set(names)):
        errors.append(f"{Path(f['path']).name}: 切片名が重複しています。")
    from app.services.section_completeness import validate_registered_sections, format_section_issues
    completeness = validate_registered_sections(rows, components=components)
    if completeness:
        errors.append(format_section_issues(completeness, filename=f.get("path", "")))
    manifest_selected = {str(s.get("section_id")) for s in f.get("sections", [])}
    if "selected_section_ids" in f:
        explicit = list(map(str, f["selected_section_ids"]))
        if len(explicit) != len(set(explicit)) or set(explicit) != selected_ids:
            errors.append(f"{Path(f['path']).name}: 選択IDと切片選択状態が一致しません。")
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
            registered = effective_registered_sections(f) if f.get("registered_sections") else []
            if registered:
                from app.services.section_completeness import (
                    validate_registered_sections, format_section_issues,
                )
                completeness = validate_registered_sections(registered)
                if completeness:
                    errors.append(format_section_issues(completeness, filename=f.get("path", "")))
                registry_ids = {str(row.get("section_id")) for row in registered}
                selected_ids = {str(row.get("section_id")) for row in f.get("sections", [])}
                if not selected_ids.issubset(registry_ids):
                    errors.append(f"{Path(path).name}: 解析対象切片が全切片registryにありません。")
            if f.get("selection_mode") == "selected" and not f.get("rois"):
                errors.append(f"{Path(path).name}: 解析対象のROIが選択されていません。")
            if f.get("selection_mode") != "none" and f.get("roi_role") not in ("section", "region"):
                errors.append(f"{Path(f['path']).name}: ROI が切片か切片内領域かを指定してください。")
        # ★ ver74.0: 全登録とRDS内の元選択は別。存在しない未選択切片を再解析で追加しない。
        if "source_selected_section_ids" in f:
            cap = list(map(str, f["source_selected_section_ids"]))
            current_ids = {str(row.get("section_id")) for row in f.get("sections", [])}
            if len(cap) != len(set(cap)) or not current_ids.issubset(set(cap)):
                errors.append(f"{Path(path).name}: 元のRDSに含まれない切片を再解析へ追加できません。新規解析を実行してください。")
        for section in f.get("sections", []):
            sid = str(section.get("section_id") or "")
            if not sid or sid in seen_section_ids:
                errors.append("section_id がファイル間で欠落または重複しています。")
            seen_section_ids.add(sid)
    subject_groups = {}
    for file_entry in files:
        source = (effective_registered_sections(file_entry) if file_entry.get("registered_sections")
                  else file_entry.get("spatial_sections") or file_entry.get("sections", []))
        for row in source:
            subject = str(row.get("subject_id") or "").strip()
            group = str(row.get("group") or "").strip()
            if subject and subject != "該当なし" and group:
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
            if row["subject_id"] and row["subject_id"] != "該当なし":
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
