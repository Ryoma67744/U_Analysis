"""切片・ROI の選択と任意の群情報を初回／再解析へ同じ形式で渡す。"""
from pathlib import Path
from dash import ALL, MATCH, Input, Output, State, callback, ctx, html, no_update
import dash_bootstrap_components as dbc
from app.services.data_manager import find_tims_file_path, read_parquet_annotations, read_desi_roi_list
from app.services.section_metadata import assign_group, build_section_manifest, manifest_group_rows, summarize_manifest
from app.services.session_manager import save_last_settings


def _selection_blocks(catalog, manifest, scope):
    saved = {f["path"]: f for f in manifest.get("files", [])}
    result = []
    names = [Path(item["path"]).name for item in catalog]
    for item in catalog:
        path = item["path"]
        f = saved[path]
        options = item["available_rois"]
        values = f["rois"] if options else (["__all__"] if f["selection_mode"] != "none" else [])
        children = [html.Small(Path(path).name, className="fw-bold", title=path)]
        if names.count(Path(path).name) > 1:
            children.append(html.Small(str(Path(path).parent), className="d-block text-muted"))
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


def make_catalog(paths, is_tims, previous):
    catalog = []
    for path in list(dict.fromkeys(paths or [])):
        p = str(Path(path).expanduser().resolve())
        rois = read_parquet_annotations(p) if is_tims else read_desi_roi_list(p)
        catalog.append({"path": p, "available_rois": rois})
    return catalog, build_section_manifest(catalog, previous=previous)


@callback(
    Output("section_catalog_store", "data"), Output("section_selector", "children"),
    Input("selected_sample_paths_store", "data"), Input("selected_samples_store", "data"),
    Input("data_folder", "value"), Input("analysis_method", "value"),
    Input("analysis_method_tims", "value"), State("section_manifest_store", "data"))
def update_section_selector(paths, samples, folder, desi_method, tims_method, previous):
    active = desi_method or tims_method or "desi_v8"
    if active not in ("desi_v8", "tims_v8"):
        return no_update, no_update
    is_tims = active == "tims_v8"
    selected = paths if is_tims else [str(Path(folder) / (s + ".txt")) for s in (samples or [])] if folder else []
    catalog, manifest = make_catalog(selected, is_tims, previous)
    return catalog, _selection_blocks(catalog, manifest, "initial")


def reanalysis_source_manifest(source, folder, rds_path="", previous=None):
    """★ verNEXT: 元結果の対応表が現在の再解析対象と一致する時だけ選択の正にする。"""
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
        result.append({"path": f["path"], "file_id": f["file_id"],
                       "available_rois": list(rois), "roi_role": f.get("roi_role")})
    return result


@callback(
    Output("section_catalog_store_reanalysis", "data"), Output("section_selector_reanalysis", "children"),
    Input("selected_samples_reanalysis", "value"), Input("reanalysis_data_folder", "value"),
    Input("analysis_method", "value"), Input("analysis_method_tims", "value"),
    Input("reanalysis_source_manifest_store", "data"), Input("rds_path", "value"),
    State("section_manifest_store_reanalysis", "data"))
def update_reanalysis_section_selector(samples, folder, desi_method, tims_method,
                                       source=None, rds_path="", previous=None):
    active = desi_method or tims_method or "desi_v8"
    if active not in ("desi_cluster_filter", "tims_cluster_filter"):
        return no_update, no_update
    is_tims = active == "tims_cluster_filter"
    saved = reanalysis_source_manifest(source, folder, rds_path, previous)
    if saved:
        catalog = source_reanalysis_catalog(saved)
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
    catalog, manifest = make_catalog(paths, is_tims, previous)
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
    """★ verNEXT: フォルダ／手法の変更を非表示の古いRDS指定で無効化させない。"""
    if not exact_path:
        return no_update, no_update
    path = Path(exact_path)
    parents = {path.parent.resolve()}
    if path.parent.name == "RDS_Files":
        parents.add(path.parent.parent.resolve())
    if not folder or Path(folder).resolve() not in parents:
        return "", None
    name = path.name.lower()
    actual = ("rpca" if "rpca" in name else "pca" if "pca" in name or "singlesample" in name
              else "harmony" if "harmony" in name else None)
    if actual and method and actual != method:
        return "", no_update
    return no_update, no_update
