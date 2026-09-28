"""★ ver75.1: 数値出力の対象選択。表示条件・解析結果は変更しない。"""
from pathlib import Path
import logging

from dash import Input, Output, State, callback, ctx, no_update

logger = logging.getLogger(__name__)


@callback(
    Output("data_export_catalog", "data"),
    Input("seurat_rds_path_store", "data"),
    Input("interactive_rds_map", "data"),
    Input("interactive_integration_method", "value"),
    Input("data_export_method_selector", "value"),
    Input("interactive_result_folder", "value"),
    Input("int_section_group_updated", "data"),
    Input("load_progress_container", "style"),
    Input("data_export_selection_mode", "value"),
)
def refresh_export_catalog(loaded_rds, rds_map, current_method, selected_methods,
                           result_folder, _metadata_updated, loading_style, mode="selected"):
    """表示のロード完了後だけ、現在の出力手法に属する候補を取得する。"""
    scope = str(Path(result_folder).expanduser().resolve()) if result_folder else None
    if scope and Path(scope).name == "RDS_Files":
        scope = str(Path(scope).parent)
    unavailable = {"scope": scope, "signature": None, "units": []}
    # ★ ver75.1: 全体出力では追加のRDS抽出をせず、対象選択を開いたときだけ読む。
    if mode != "selected":
        return unavailable
    # ★ ver75.1: フォルダ/手法の変更とRDS読込完了は別callbackなので、旧候補を使わない。
    if loading_style and loading_style.get("display") != "none":
        return {**unavailable, "error": "解析結果を読み込み中です。完了後に出力対象を確認してください。"}
    if not loaded_rds or not scope:
        return {**unavailable, "error": "解析結果を読み込むと、出力する群・個体・切片を選択できます。"}
    if not selected_methods:
        return {**unavailable, "error": "出力手法 (UMAP) を1つ以上選択してください。"}
    try:
        from app.callbacks.interactive_data_export import load_export_catalog
        return load_export_catalog(loaded_rds, rds_map, current_method,
                                   selected_methods, result_folder)
    except Exception as exc:
        logger.warning("出力対象の候補を取得できません: %s", exc)
        return {**unavailable, "error": f"出力対象を確認できません: {exc}"}


def _options(units, field, missing_label=None):
    counts = {}
    for unit in units:
        value = str(unit.get(field) or "")
        if field == "subject_id" and value == "該当なし":
            continue
        if not value and missing_label is None:
            continue
        counts[value] = counts.get(value, 0) + 1
    return [{"label": f"{value or missing_label} ({count}切片)", "value": value}
            for value, count in sorted(counts.items())]


def _unit_label(unit):
    name = str(unit.get("label") or unit.get("sample") or unit["id"])
    sample = str(unit.get("sample") or "")
    if sample and sample != name:
        name += f" [{sample}]"
    group = str(unit.get("group") or "（群未設定）")
    subject = str(unit.get("subject_id") or "未設定")
    return f"{name} — 群: {group} / 個体・独立試料: {subject} / {int(unit.get('pixels', 0)):,}画素"


@callback(
    Output("data_export_units", "options"),
    Output("data_export_units", "value"),
    Output("data_export_group_pick", "options"),
    Output("data_export_subject_pick", "options"),
    Output("data_export_selection_controls", "disabled"),
    Output("data_export_group_pick", "disabled"),
    Output("data_export_subject_pick", "disabled"),
    Output("data_export_selection", "data"),
    Output("data_export_selection_summary", "children"),
    Output("data_export_selection_error", "children"),
    Output("data_export_selection_editor", "style"),
    Output("data_export_exclude_unused", "disabled"),
    Output("data_export_selection_note", "children"),
    Output("data_export_group_pick", "value"),
    Output("data_export_subject_pick", "value"),
    Input("data_export_selection_mode", "value"),
    Input("data_export_catalog", "data"),
    Input("data_export_units", "value"),
    Input("data_export_select_all", "n_clicks"),
    Input("data_export_select_none", "n_clicks"),
    Input("data_export_group_add", "n_clicks"),
    Input("data_export_group_remove", "n_clicks"),
    Input("data_export_subject_add", "n_clicks"),
    Input("data_export_subject_remove", "n_clicks"),
    State("data_export_group_pick", "value"),
    State("data_export_subject_pick", "value"),
    State("data_export_selection", "data"),
)
def update_export_selection(mode, catalog, checked, _all, _none, _group_add,
                            _group_remove, _subject_add, _subject_remove,
                            groups, subjects, previous):
    """群/個体の操作も最終的には切片ID集合へ変換し、隠れたAND条件を作らない。"""
    trigger = ctx.triggered_id
    catalog, previous = catalog or {}, previous or {}
    selected_mode = mode == "selected"
    scope = catalog.get("scope")
    same_scope = bool(scope) and scope == previous.get("scope")
    units = catalog.get("units") or []
    valid = bool(scope and catalog.get("signature") and units and not catalog.get("error"))
    available = [unit["id"] for unit in units]
    old_ids = list(previous.get("ids") or []) if same_scope else []
    initialized = bool(previous.get("initialized"))
    initial_scope = previous.get("initial_scope")
    if selected_mode and not initialized and scope:
        if initial_scope and initial_scope != scope:
            # ★ ver75.1: 最初の候補取得中に別結果へ移った場合も、全選択を持ち越さない。
            initialized = True
        else:
            initial_scope = scope
    notice = previous.get("notice", "") if same_scope else ""

    # ★ ver75.1: 同じ結果の読込中は下書きだけ保持しsignatureを失効させる。
    # 結果切替時は空へ戻す。空選択を「全体」と解釈するとKO1まで再び混入する。
    ids = [unit_id for unit_id in available if unit_id in old_ids] if valid else old_ids
    if valid and selected_mode:
        if not initialized:
            ids = available[:]
            initialized = True
        elif same_scope and trigger == "data_export_units":
            ids = [unit_id for unit_id in available if unit_id in (checked or [])]
            notice = ""
        elif trigger == "data_export_select_all":
            ids = available[:]
            notice = ""
        elif trigger == "data_export_select_none":
            ids = []
            notice = ""
        elif trigger in {"data_export_group_add", "data_export_group_remove",
                         "data_export_subject_add", "data_export_subject_remove"}:
            is_group = trigger.startswith("data_export_group_")
            field, picked = ("group", groups) if is_group else ("subject_id", subjects)
            matched = {unit["id"] for unit in units
                       if str(unit.get(field) or "") in (picked or [])}
            chosen = set(ids)
            chosen = chosen | matched if trigger.endswith("_add") else chosen - matched
            ids = [unit_id for unit_id in available if unit_id in chosen]
            notice = "" if picked else "一括操作する群または個体／独立試料を選んでください。"
        if same_scope and trigger == "data_export_catalog" and set(old_ids) - set(available):
            notice = "出力候補が変わったため、対象外になった切片を選択から外しました。残りの対象を確認してください。"

    payload = {"mode": "selected" if selected_mode else "all", "scope": scope,
               "signature": catalog.get("signature") if valid else None,
               "ids": ids, "initialized": initialized, "initial_scope": initial_scope,
               "notice": notice}
    error = ""
    if selected_mode:
        if not valid:
            error = catalog.get("error") or "選択できる解析済みの切片がありません。"
        elif not ids:
            error = "出力対象が0件です。群・個体または切片を1つ以上選択してください。"
        else:
            error = notice
        if not valid:
            payload["error"] = error
        chosen = [unit for unit in units if unit["id"] in ids]
        chosen_groups = sorted({unit.get("group") or "（群未設定）" for unit in chosen})
        count_groups = len(chosen_groups)
        count_subjects = len({unit["subject_id"] for unit in chosen
                              if unit.get("subject_id") and unit["subject_id"] != "該当なし"})
        pixels = sum(int(unit.get("pixels", 0)) for unit in chosen)
        group_names = "、".join(chosen_groups)
        summary = (f"出力対象: {count_groups}群（{group_names}） / 登録済み独立試料 {count_subjects} / "
                   f"{len(chosen)}/{len(units)}切片 / {pixels:,}解析済み画素") if valid else "出力対象を確認中です。"
    else:
        summary = "全体を出力します（下の未解析切片の除外設定を適用）。"
    disabled = not valid
    return (
        [{"label": _unit_label(unit), "value": unit["id"], "disabled": disabled} for unit in units],
        ids if valid else [], _options(units, "group", "（群未設定）"),
        _options(units, "subject_id"), disabled, disabled, disabled, payload,
        summary, error, {"display": "block" if selected_mode else "none"}, selected_mode,
        "選択した解析済み画素だけを出力するため、下の未解析切片の除外設定は使いません。"
        if selected_mode else "",
        no_update if same_scope else [], no_update if same_scope else [],
    )
