"""切片・ROI の選択と任意の群情報を初回／再解析へ同じ形式で渡す。"""
from pathlib import Path
from dash import ALL, MATCH, Input, Output, State, callback, ctx, html, no_update
import dash_bootstrap_components as dbc
from app.services.data_manager import find_tims_file_path, read_parquet_annotations, read_desi_roi_list
from app.services.section_metadata import assign_group, build_section_manifest, manifest_group_rows, summarize_manifest
from app.services.session_manager import save_last_settings



def _spectral_preflight_notice(preflight):
    if not preflight:
        return None
    if preflight.get("status") != "ok":
        return dbc.Alert(
            "スペクトル事前検査に失敗しました: " + str(preflight.get("error") or "詳細不明"),
            color="danger", className="py-2 px-2 my-2 small",
        )
    ms = preflight.get("ms_level_interpretation") or {}
    axis = preflight.get("mz_axis_contract") or {}
    ms_status = ms.get("status")
    ms_label = ("明示MS1" if ms_status == "explicit_ms1" else
                "推定MS1（高信頼）" if ms.get("confidence") == "high" else
                "推定MS1")
    axis_status = axis.get("status")
    minimum = axis.get("feature_count_min")
    maximum = axis.get("feature_count_max")
    unique = axis.get("feature_count_unique")
    peak_text = ""
    if isinstance(minimum, int) and isinstance(maximum, int):
        peak_text = f"{minimum:,}–{maximum:,} peaks/pixel"
        if isinstance(unique, int):
            peak_text += f"（{unique}種類）"

    if axis_status == "individual_axis":
        return dbc.Alert([
            html.Div([html.Strong(f"△ {ms_label}"),
                      html.Span(f" ／ {preflight.get('representation', 'unknown')}-"
                                f"{preflight.get('spectrum_type', 'unknown')}")]),
            html.Div(f"× 画素ごとに異なるm/zピークリスト: {peak_text}"),
            html.Div("直接解析: 現在は非対応。R・UMAPは開始しません。"),
            html.Details([
                html.Summary("科学的背景と推奨入力", style={"cursor": "pointer"}),
                html.P(
                    "Mass alignment後に共通consensus feature行列を作ればPCA・UMAPは可能です。"
                    "ただしSCiLS等のTop-N／閾値付きpeak listでは、未出力と真の0を区別できません。",
                    className="mb-1 mt-1",
                ),
                html.P(
                    "そのためU_Analysisは自動union・0補完・広いbinning・mass alignmentを行いません。"
                    "共通feature listに対する全pixel強度行列、または共通m/z軸Parquetを使用してください。"
                    "全pixelのintersectionだけを使う方法も、局在分子を失うため推奨しません。",
                    className="mb-0",
                ),
            ]),
        ], color="warning", className="py-2 px-2 my-2 small")

    if ms_status == "inferred_ms1":
        return dbc.Alert(
            f"△ {ms_label}: MSn・precursor・product・fragmentation情報がなく、"
            "full-scan MSIとして整合するためMS1として処理します。 "
            "m/z配列長は共通候補で、全m/z値の一致は解析開始時に検証します。",
            color="info", className="py-2 px-2 my-2 small",
        )
    return html.Small(
        "✓ 明示MS1 ／ 共通m/z軸候補（全m/z値は解析開始時に検証）",
        className="d-block text-success my-1",
    )


def _selection_blocks(catalog, manifest, scope):
    saved = {f["path"]: f for f in manifest.get("files", [])}
    result = []
    names = [Path(item["path"]).name for item in catalog]
    for item in catalog:
        path = item["path"]
        f = saved[path]
        children = [html.Small(Path(path).name, className="fw-bold", title=path)]
        if names.count(Path(path).name) > 1:
            children.append(html.Small(str(Path(path).parent), className="d-block text-muted"))

        if f.get("roi_role") == "spatial" or item.get("spatial_layout") or item.get("spatial_error"):
            if f.get("spatial_error"):
                children.append(dbc.Alert(f["spatial_error"], color="warning", className="py-1 px-2 my-1 small"))
                result.append(html.Div(children, className="border rounded p-2 mb-2"))
                continue
            spatial_sections = f.get("spatial_sections", [])
            selected = [s["section_id"] for s in spatial_sections if s.get("selected", True)]
            children.append(html.Div(
                f"座標から {len(spatial_sections)} 切片候補を検出",
                className="small text-success mt-1"))
            warnings = (f.get("spatial_layout") or {}).get("warnings") or []
            if warnings:
                children.append(html.Small(" / ".join(warnings[:2]), className="d-block text-warning"))
            notice = _spectral_preflight_notice(f.get("spectral_preflight"))
            if notice is not None:
                children.append(notice)
            children.append(dbc.Checklist(
                id={"type": "section_roi_check", "scope": scope, "index": path},
                options=[{"label": f" {s['section_display_name']}  {int(s.get('pixel_count', 0)):,} pixels",
                          "value": s["section_id"]} for s in spatial_sections],
                value=selected, inline=False, className="mt-1 imzml-spatial-compact-list"))
            children.append(html.Div(className="d-flex flex-wrap gap-1 align-items-center", children=[
                dbc.Button("配置・切片情報を編集",
                           id={"type": "imzml_spatial_open", "scope": scope, "index": path},
                           n_clicks=0, size="sm", color="info", outline=True),
                dbc.Button("全選択", id={"type": "section_select_all", "scope": scope, "index": path},
                           n_clicks=0, size="sm", color="link"),
                dbc.Button("全解除", id={"type": "section_select_none", "scope": scope, "index": path},
                           n_clicks=0, size="sm", color="link"),
            ]))
        else:
            options = item["available_rois"]
            values = f["rois"] if options else (["__all__"] if f["selection_mode"] != "none" else [])
            if options:
                children.append(dbc.Select(
                    id={"type": "section_roi_role", "scope": scope, "index": path},
                    options=[{"label": "ROIの意味を選択（初回のみ）", "value": ""},
                             {"label": "各ROIが別の切片", "value": "section"},
                             {"label": "同じ切片内の領域（臓器など）", "value": "region"}],
                    value=f.get("roi_role") or "", className="mt-1", size="sm"))
            children.append(dbc.Checklist(
                id={"type": "section_roi_check", "scope": scope, "index": path},
                options=([{"label": " " + r, "value": r} for r in options] if options else
                         [{"label": " 全画素", "value": "__all__"}]),
                value=values, inline=True, className="mt-1"))
            children.append(html.Div([
                dbc.Button("全選択", id={"type": "section_select_all", "scope": scope, "index": path},
                           n_clicks=0, size="sm", color="link"),
                dbc.Button("全解除", id={"type": "section_select_none", "scope": scope, "index": path},
                           n_clicks=0, size="sm", color="link")]))
        result.append(html.Div(children, className="border rounded p-2 mb-2"))
    return result or html.Small("上で解析するファイルを選択してください。", className="text-muted")


# ★ ver71.0: imzMLは切片名ではなくXMLのx–y座標componentを候補化する。
def make_catalog(paths, is_tims, previous, overrides=None):
    catalog = []
    overrides = overrides or {}
    for path in list(dict.fromkeys(paths or [])):
        p = str(Path(path).expanduser().resolve())
        if is_tims and Path(p).suffix.lower() == ".imzml":
            item = {"path": p, "available_rois": [], "input_format": "imzml"}
            from app.services.imzml_spatial_layout import inspect_imzml_spatial_layout, SpatialLayoutError
            from app.services.imzml_validation import inspect_spectral_preflight
            from app.services.input_preparation import InputPreparationError
            try:
                item["spectral_preflight"] = inspect_spectral_preflight(p)
            except (OSError, InputPreparationError) as exc:
                item["spectral_preflight"] = {
                    "schema_version": 1, "status": "invalid", "error": str(exc),
                }
            try:
                layout = inspect_imzml_spatial_layout(p)
                item["spatial_layout"] = layout
                override = overrides.get(p) or {}
                if override.get("coordinate_hash") == layout.get("coordinate_hash"):
                    item["spatial_sections"] = override.get("spatial_sections")
            except (OSError, SpatialLayoutError) as exc:  # 入力エラーはmanifestへ残し、黙って1切片にしない。
                item["spatial_error"] = str(exc)
            catalog.append(item)
        else:
            rois = read_parquet_annotations(p) if is_tims else read_desi_roi_list(p)
            catalog.append({"path": p, "available_rois": rois})
    return catalog, build_section_manifest(catalog, previous=previous)


@callback(
    Output("section_catalog_store", "data"), Output("section_selector", "children"),
    Input("selected_sample_paths_store", "data"), Input("selected_samples_store", "data"),
    Input("data_folder", "value"), Input("analysis_method", "value"),
    Input("analysis_method_tims", "value"), Input("imzml_spatial_overrides", "data"),
    State("section_manifest_store", "data"))
def update_section_selector(paths, samples, folder, desi_method, tims_method, overrides, previous):
    active = desi_method or tims_method or "desi_v8"
    if active not in ("desi_v8", "tims_v8"):
        return no_update, no_update
    is_tims = active == "tims_v8"
    selected = paths if is_tims else [str(Path(folder) / (s + ".txt")) for s in (samples or [])] if folder else []
    catalog, manifest = make_catalog(selected, is_tims, previous, overrides)
    return catalog, _selection_blocks(catalog, manifest, "initial")


def reanalysis_source_manifest(source, folder, rds_path="", previous=None):
    """元結果の対応表が現在の再解析対象と一致する時だけ選択の正にする。"""
    candidates = [source or {}]
    if (previous or {}).get("source_rds_path"):
        candidates.append({"manifest": previous, "data_folder": previous.get("source_data_folder", ""),
                           "rds_path": previous["source_rds_path"]})
    for item in candidates:
        manifest = item.get("manifest") or {}
        if not manifest.get("files"):
            continue
        expected = item.get("data_folder") or ""
        if expected and Path(folder or "").resolve() != Path(expected).resolve():
            continue
        expected_rds = item.get("rds_path") or ""
        if rds_path and expected_rds and Path(rds_path).resolve() != Path(expected_rds).resolve():
            if Path(rds_path).parent.resolve() != Path(expected_rds).parent.resolve():
                continue
        return manifest
    return None


def source_reanalysis_catalog(manifest):
    """元解析で採用した画素のあるファイルだけを、同名でも別々に並べる。"""
    result = []
    for f in (manifest or {}).get("files", []):
        if f.get("selection_mode") == "none":
            continue
        rois = f.get("rois", []) if f.get("selection_mode") == "selected" else f.get("available_rois", [])
        item = {"path": f["path"], "file_id": f["file_id"],
                "available_rois": list(rois), "roi_role": f.get("roi_role")}
        for key in ("spatial_layout", "spatial_sections", "spatial_error",
                    "input_format", "spectral_preflight"):
            if key in f:
                item[key] = f[key]
        result.append(item)
    return result


@callback(
    Output("section_catalog_store_reanalysis", "data"), Output("section_selector_reanalysis", "children"),
    Input("selected_samples_reanalysis", "value"), Input("reanalysis_data_folder", "value"),
    Input("analysis_method", "value"), Input("analysis_method_tims", "value"),
    Input("reanalysis_source_manifest_store", "data"), Input("rds_path", "value"),
    Input("imzml_spatial_overrides_reanalysis", "data"),
    State("section_manifest_store_reanalysis", "data"))
def update_reanalysis_section_selector(samples, folder, desi_method, tims_method,
                                       source=None, rds_path="", overrides=None, previous=None):
    active = desi_method or tims_method or "desi_v8"
    if active not in ("desi_cluster_filter", "tims_cluster_filter"):
        return no_update, no_update
    is_tims = active == "tims_cluster_filter"
    saved = reanalysis_source_manifest(source, folder, rds_path, previous)
    if saved:
        catalog = source_reanalysis_catalog(saved)
        for item in catalog:
            override = (overrides or {}).get(item["path"]) or {}
            layout = item.get("spatial_layout") or {}
            if override.get("coordinate_hash") == layout.get("coordinate_hash"):
                item["spatial_sections"] = override.get("spatial_sections")
        same_source = (previous or {}).get("source_rds_path") == saved.get("source_rds_path")
        manifest = build_section_manifest(catalog, previous=previous if same_source else saved)
        return catalog, _selection_blocks(catalog, manifest, "reanalysis")
    paths = []
    for sample in samples or []:
        if not folder:
            continue
        p = find_tims_file_path(folder, sample) if is_tims else str(Path(folder) / (sample + ".txt"))
        if p:
            paths.append(str(p))
    catalog, manifest = make_catalog(paths, is_tims, previous, overrides)
    return catalog, _selection_blocks(catalog, manifest, "reanalysis")


@callback(
    Output({"type": "section_roi_check", "scope": MATCH, "index": MATCH}, "value"),
    Input({"type": "section_select_all", "scope": MATCH, "index": MATCH}, "n_clicks"),
    Input({"type": "section_select_none", "scope": MATCH, "index": MATCH}, "n_clicks"),
    State({"type": "section_roi_check", "scope": MATCH, "index": MATCH}, "options"),
    prevent_initial_call=True)
def select_section_rois(n_all, n_none, options):
    trigger = ctx.triggered_id
    if not isinstance(trigger, dict):
        return no_update
    return [] if trigger["type"] == "section_select_none" else [o["value"] for o in options or []]


def update_section_state(catalog, selected_values, role_values, rows, selected_ids, role_ids,
                         previous, bulk_rows=None, bulk_group=None, apply_bulk=False):
    selected = {i["index"]: value for i, value in zip(selected_ids or [], selected_values or [])}
    roles = {i["index"]: value for i, value in zip(role_ids or [], role_values or [])}
    manifest = build_section_manifest(catalog, selected, roles, rows, previous)
    data = manifest_group_rows(manifest)
    status = ""
    if apply_bulk:
        data, status = assign_group(data, bulk_rows, bulk_group)
        manifest = build_section_manifest(catalog, selected, roles, data, manifest)
    summary, groups, errors = summarize_manifest(manifest)
    children = [html.Div(summary)]
    if groups:
        children.append(html.Small(groups, className="d-block"))
    children.extend(html.Small(e, className="d-block text-warning") for e in errors)
    return data, manifest, children, status


def _register_state(scope):
    suffix = "" if scope == "initial" else "_reanalysis"
    @callback(
        Output("section_group_table" + suffix, "data"), Output("section_manifest_store" + suffix, "data"),
        Output("section_summary" + suffix, "children"), Output("section_group_status" + suffix, "children"),
        Input("section_catalog_store" + suffix, "data"),
        Input({"type": "section_roi_check", "scope": scope, "index": ALL}, "value"),
        Input({"type": "section_roi_role", "scope": scope, "index": ALL}, "value"),
        Input("section_group_table" + suffix, "data_timestamp"), Input("section_apply_group" + suffix, "n_clicks"),
        State("section_group_table" + suffix, "data"),
        State({"type": "section_roi_check", "scope": scope, "index": ALL}, "id"),
        State({"type": "section_roi_role", "scope": scope, "index": ALL}, "id"),
        State("section_manifest_store" + suffix, "data"), State("section_group_table" + suffix, "selected_rows"),
        State("section_bulk_group" + suffix, "value"))
    def sync_section_state(catalog, values, roles, timestamp, clicks, rows, ids, role_ids,
                           previous, selected_rows, group):
        if not catalog and previous and not ctx.triggered_id:
            return manifest_group_rows(previous), previous, no_update, ""
        result = update_section_state(catalog, values, roles,
            [] if ctx.triggered_id == "section_catalog_store" + suffix else rows,
            ids, role_ids, previous, selected_rows, group,
            ctx.triggered_id == "section_apply_group" + suffix)
        save_last_settings({"section_manifest" + suffix: result[1], "execution_policy": "section_auto_v1"})
        return result


_register_state("initial")
_register_state("reanalysis")


@callback(
    Output("ion_mode_panel", "style"), Output("db_tolerance_panel", "style"),
    Output("db_adduct_panel", "style"), Output("db_annotation_settings", "style"),
    Input("use_annotation_check", "value"), Input("calibration_enable", "value"))
def toggle_optional_molecule_settings(db_values, calibration):
    enabled = "db" in (db_values or [])
    show, hide = {}, {"display": "none"}
    return (show if enabled or calibration else hide, show if enabled else hide,
            show if enabled else hide, show if enabled else hide)


@callback(
    Output("reanalysis_ion_mode_panel", "style"), Output("reanalysis_db_match_panel", "style"),
    Output("reanalysis_db_annotation_settings", "style"),
    Input("reanalysis_use_annotation_check", "value"), Input("reanalysis_calibration_use_previous", "value"))
def toggle_optional_reanalysis_molecule_settings(db_values, calibration):
    enabled = "db" in (db_values or [])
    show, hide = {}, {"display": "none"}
    return show if enabled or calibration else hide, show if enabled else hide, show if enabled else hide


@callback(
    Output("rds_path", "value", allow_duplicate=True),
    Output("reanalysis_source_manifest_store", "data", allow_duplicate=True),
    Input("rds_folder_reanalysis", "value"), Input("cluster_source", "value"),
    State("rds_path", "value"), prevent_initial_call=True)
def clear_stale_reanalysis_source(folder, method, exact_path):
    """★ ver67.0: フォルダ／手法の変更を非表示の古いRDS指定で無効化させない。"""
    if not exact_path:
        return no_update, no_update
    path = Path(exact_path)
    parents = {path.parent.resolve()}
    if path.parent.name == "RDS_Files":
        parents.add(path.parent.parent.resolve())
    if not folder or Path(folder).resolve() not in parents:
        return "", None
    name = path.name.lower()
    actual = ("rpca" if "rpca" in name else "harmony" if "harmony" in name
              else "pca" if "pca" in name or "singlesample" in name else None)
    if actual and method and actual != method:
        return "", no_update
    return no_update, no_update
