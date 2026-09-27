"""★ ver74.0: 共有Storeは権限にならない。登録callbackと共有対象をサーバ側で照合する。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from flask import g, has_request_context

# 共有では閲覧・描画だけを許可する。保存/変換/解析/プロジェクト列挙を既定拒否。
# 新callbackは本文と入力スコープを監査してからここへ追加する。
_READ_CALLBACKS = {
    "share_callbacks": {"route_share_url", "_shared_activate_interactive_tab"},
    "analysis_callbacks": {"toggle_sidebar_content"},
    "project_callbacks": {"toggle_pages", "apply_shared_mode"},
    "tab_url_routing": {"_sync_tab_from_url", "_sync_url_from_tab"},
    "interactive_project": {"toggle_project_dropdown_visibility"},
    "interactive_callbacks": {"auto_scan_rds_files", "auto_load_on_rds_ready",
        "toggle_integration_method", "_toggle_cancel_button", "load_stage_a_show_progress",
        "load_stage_b_extract", "load_stage_c_deg", "load_stage_d_finish"},
    "interactive_cluster": {"update_cluster_stats", "update_cluster_info", "update_cluster_dashboard",
        "update_cluster_top_markers", "load_saved_cluster_name_map", "update_cluster_dropdown_labels"},
    "interactive_umap": {"update_umap_plot", "toggle_umap_integrated_visibility", "toggle_merge_controls",
        "update_umap_per_sample"},
    "interactive_spatial": {"toggle_sample_rotation_visibility", "toggle_cluster_color_visibility",
        "update_swatch_disabled_state", "update_sample_dropdown_labels", "update_spatial_plots"},
    "interactive_deg": {"filter_features", "apply_mz_filter", "update_feature_options_on_mz_filter",
        "_render_feature_plot_for_view", "request_feature_intensity", "update_bookmark_options",
        "bookmark_to_feature", "update_volcano_cluster_options", "update_volcano_plot", "update_heatmap"},
    "interactive_loupe": {"render_selection_summary", "update_feature_violin", "populate_marker_table"},
    "interactive_section_groups": {"load_section_groups"},
    "interactive_fullscreen": {"render_light_fullscreen_request", "render_feature_fullscreen_request",
        "render_deg_fullscreen_request", "toggle_fullscreen", "update_fs_umap", "update_fs_spatial"},
}


def shared_readonly_request():
    return bool(has_request_context() and getattr(g, "ua_shared_readonly", False))


def callback_identity(callback):
    import inspect
    fn = inspect.unwrap(callback)
    return fn.__module__, fn.__name__


def _share_rds_map(share):
    from app.callbacks.interactive_callbacks import _detect_integration_methods
    from app.utils.integration_methods import resolve_method_key
    root = Path(share["result_dir"]).expanduser().resolve()
    # 派生RDSはcache内の決定的なパス。読込時に生成しても原結果は変更しない。
    methods = _detect_integration_methods(str(root), include_derived=True)
    scoped = {name: str(Path(path).resolve()) for name, path in methods.items()
              if Path(path).resolve().is_relative_to(root)}
    # ★ ver74.0: cacheディレクトリ全体を許可せず、共有内Harmonyから算出した1件だけ。
    from app.config import SEURAT_CACHE_DIR
    from app.callbacks.interactive_callbacks import _DERIVE_PCA_VERSION
    if "Harmony" in scoped and "PCA" in methods and "PCA" not in scoped:
        import hashlib
        digest = hashlib.md5(f'{methods["Harmony"]}|{_DERIVE_PCA_VERSION}'.encode()).hexdigest()[:16]
        expected = Path(SEURAT_CACHE_DIR).resolve() / "derived_pca" / f"{digest}_pca_uncorrected.rds"
        if expected.resolve() == expected and Path(methods["PCA"]).resolve() == expected:
            scoped["PCA"] = str(expected)
    requested = share.get("integration_method") or "all"
    if requested != "all":
        key = resolve_method_key(requested, scoped)
        scoped = {key: scoped[key]} if key else {}
    return scoped


def _matches_declared_id(actual, expected):
    """Dash ALL/MATCH入力も、宣言済みのpattern構造以外へ置換させない。"""
    import json
    if actual == expected:
        return True
    try:
        pattern = json.loads(expected) if isinstance(expected, str) else expected
        value = json.loads(actual) if isinstance(actual, str) else actual
    except (TypeError, ValueError):
        return False
    if not isinstance(pattern, dict) or not isinstance(value, dict) or pattern.keys() != value.keys():
        return False
    return all((isinstance(wanted, list) and len(wanted) == 1 and wanted[0] in {"ALL", "MATCH", "ALLSMALLER"}
                and isinstance(value[key], (str, int))) or value[key] == wanted
               for key, wanted in pattern.items())


def authorize_shared_callback(body, entry, share, kind, token):
    """JSONの見かけのIDだけでなく登録Input/Stateの順序と値を照合する。"""
    callback = entry.get("callback")
    if callback is None:
        return False
    module, name = callback_identity(callback)
    if not module.startswith("app.callbacks.") or name not in _READ_CALLBACKS.get(module.rsplit(".", 1)[-1], set()):
        return False
    posted = []
    for group in ("inputs", "state"):
        actual, expected = body.get(group, []), entry.get(group, [])
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        for item, declaration in zip(actual, expected):
            values = item if isinstance(item, list) else [item]
            if isinstance(item, list) and not any(tag in str(declaration.get("id")) for tag in ('"ALL"', '"ALLSMALLER"')):
                return False
            for value in values:
                if (not isinstance(value, dict) or value.get("property") != declaration.get("property")
                        or not _matches_declared_id(value.get("id"), declaration.get("id"))):
                    return False
                posted.append(value)
    root = Path(share.get("result_dir") or "").expanduser().resolve()
    if not share.get("result_dir"):
        return False
    canonical_shared = {"active": True, "token": token, "kind": kind,
        "project_id": share.get("project_id", ""), "sub_project_id": share.get("sub_project_id", ""),
        "integration_method": share.get("integration_method") or "all", "read_only": True}
    # route_share_urlはtokenのみで台帳を読み、クライアントのfolder値を使わない。
    if name == "route_share_url":
        prefix = "/view/" if kind == "persistent" else "/share/"
        return len(posted) == 1 and str(posted[0].get("value", "")).rstrip("/") == prefix + token
    methods = _share_rds_map(share)
    allowed_rds = set(methods.values())
    if name in {"load_stage_b_extract", "load_stage_c_deg", "load_stage_d_finish"}:
        trigger = posted[0].get("value") if posted else None
        # ★ ver74.0: key欠落時にdefault project stateを参照するfallbackを共有で許さない。
        if trigger and (not isinstance(trigger, dict)
                or not isinstance(trigger.get("rds_path"), str)
                or not isinstance(trigger.get("method"), str)
                or trigger.get("rds_path") not in allowed_rds
                or trigger.get("method") not in methods
                or methods.get(trigger.get("method")) != trigger.get("rds_path")
                or not isinstance(trigger.get("result_folder"), str)
                or Path(trigger["result_folder"]).resolve() != root):
            return False
    from app.services.seurat_bridge import SeuratBridge
    bridge = SeuratBridge()
    cache_dirs = {(bridge._cache_base / bridge._get_cache_key(path)).resolve() for path in allowed_rds}
    # callbackが指定するRDSとcacheの対応を固定し、別手法の抽出値を混ぜない。
    requested_rds = [field.get("value") for field in posted
                     if "rds_path" in str(field.get("id", "")).lower()
                     and isinstance(field.get("value"), str) and field.get("value")]
    if len(set(requested_rds)) == 1 and requested_rds[0] in allowed_rds:
        cache_dirs = {(bridge._cache_base / bridge._get_cache_key(requested_rds[0])).resolve()}
    allowed_paths = {root, *(Path(path) for path in allowed_rds), *cache_dirs}
    # 原データの自由な参照は共有に不要。viewerは解析結果と抽出cacheだけを読む。
    def validate(value, key=""):
        lower = key.lower()
        # 型検査より前の汎用dict/list再帰では、空コンテナがRDS指定を迂回できた。
        if "rds_map" in lower:
            return value is None or (isinstance(value, dict) and all(
                isinstance(path, str) and methods.get(method) == str(Path(path).resolve())
                for method, path in value.items()))
        if "rds_path" in lower:
            return isinstance(value, str) and bool(value) and str(Path(value).expanduser().resolve()) in allowed_rds
        if lower == "derive_from":
            return value is None or (isinstance(value, str) and value in allowed_rds)
        if "sub_project" in lower:
            return value in (None, "") or (isinstance(value, str) and value == str(share.get("sub_project_id", "")))
        if "project" in lower and ("id" in lower or "select" in lower):
            return value in (None, "") or (isinstance(value, str) and value == str(share.get("project_id", "")))
        if "cache_dir" in lower:
            return value in (None, "") or (isinstance(value, str) and Path(value).expanduser().resolve() in cache_dirs)
        if "folder" in lower:
            return value in (None, "") or (isinstance(value, str) and Path(value).expanduser().resolve() == root)
        if "path" in lower:
            return value in (None, "") or (isinstance(value, str) and Path(value).expanduser().resolve() in allowed_paths)
        if lower in {"method", "interactive_integration_method"}:
            return value in (None, "") or (isinstance(value, str) and value in methods)
        if isinstance(value, dict):
            return all(validate(child, str(child_key)) for child_key, child in value.items())
        if isinstance(value, list):
            return all(validate(child, key) for child in value)
        return True
    for field in posted:
        ident = str(field["id"])
        if ident == "shared_session":
            field["value"] = deepcopy(canonical_shared)
        elif ident in {"annotation_path", "default_annotation_csv", "calibration_enable"}:
            # 保存された注釈はサーバが結果来歴から読込む。共有側の別DBパス/再校正は採用しない。
            field["value"] = False if ident == "calibration_enable" else ""
        elif ident == "session_id_store":
            from flask import session
            import uuid
            field["value"] = session.setdefault("shared_viewer_id", uuid.uuid4().hex)
        elif ident == "current_page":
            field["value"] = "analysis"
        elif ident == "interactive_entry_mode":
            field["value"] = "shared"
        elif not validate(field.get("value"), ident):
            return False
    g.ua_shared_readonly = True
    g.ua_shared_record = deepcopy(share)
    return True
