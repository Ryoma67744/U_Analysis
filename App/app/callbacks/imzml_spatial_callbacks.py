"""imzML座標切片のモデルレスfloatと登録情報・解析選択を同期する。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from dash import ALL, Input, Output, State, callback, ctx, html, no_update
import plotly.graph_objects as go

from app.services.imzml_spatial_layout import (
    SpatialLayoutError,
    merge_spatial_sections,
    normalize_spatial_sections,
    reset_spatial_sections,
)
from app.services.section_completeness import (
    format_section_issues,
    is_metadata_confirmed,
    normalized_registered_sections,
    validate_registered_sections,
)


def _entry(rows, path):
    target = str(Path(path).expanduser().resolve())
    for row in rows or []:
        if str(Path(row.get("path", "")).expanduser().resolve()) == target:
            return row
    return None


def _table_rows(sections):
    return [{
        "section_id": row["section_id"],
        "section_display_name": row.get("section_display_name", row["section_id"]),
        "subject_id": row.get("subject_id", ""),
        "group": row.get("group", ""),
        "metadata_confirmed": "確認済" if is_metadata_confirmed(row.get("metadata_confirmed", False)) else "未確認",
        "component_count": len(row.get("component_ids", [])),
        "pixel_count": int(row.get("pixel_count", 0)),
        "selected": "使用" if row.get("selected", True) else "使用しない",
    } for row in sections or []]


def _registration_guidance(table_rows, draft, layout):
    """★ ver74.1: 入力中の値を検査し、対象外の切片も同じ明瞭さで不足を示す。"""
    labels = {"section_display_name": "切片名", "subject_id": "個体／独立試料ID", "group": "群"}
    originals = {str(row.get("section_id")): row for row in draft or []}
    sections, styles, cells, statuses, tips = [], [], [], [], []
    missing_count = unconfirmed_count = selected_count = 0
    for index, edited in enumerate(table_rows or []):
        original = originals.get(str(edited.get("section_id")), {})
        row = deepcopy(original)
        row["section_id"] = edited.get("section_id", "")
        changed = False
        missing = []
        tooltip = {}
        for column, label in labels.items():
            value = str(edited.get(column) or "").strip()
            changed = changed or value != str(original.get(column) or "").strip()
            row[column] = value
            if not value:
                missing.append(label)
                cells.append({"row": index, "column": list(labels).index(column), "column_id": column})
                styles.append({"if": {"row_index": index, "column_id": column},
                               "backgroundColor": "#fff0f0", "border": "2px solid #c92a2a", "color": "#842029"})
                tooltip[column] = {"value": label + "が未入力です。解析対象外の切片も入力してください。", "type": "text"}
        confirmed = is_metadata_confirmed(edited.get("metadata_confirmed")) and not changed
        row["metadata_confirmed"] = confirmed
        sections.append(row)
        selected_count += edited.get("selected") != "使用しない"
        missing_count += bool(missing)
        unconfirmed_count += not confirmed
        if not confirmed:
            styles.append({"if": {"row_index": index, "column_id": "metadata_confirmed"},
                           "backgroundColor": "#fff3cd", "color": "#664d03",
                           "textDecoration": "line-through" if changed and is_metadata_confirmed(edited.get("metadata_confirmed")) else "none"})
            tooltip["metadata_confirmed"] = {"value": "編集後は②「全切片を確認済みにする」を押してください。", "type": "text"}
        tips.append(tooltip)
        text = "未入力：" + "・".join(missing) if missing else ("入力済み・②で確認" if not confirmed else "確認済み")
        statuses.append(html.Li([
            html.Strong(str(row.get("section_display_name") or f"切片 {index + 1}")),
            html.Span("（解析対象外）" if edited.get("selected") == "使用しない" else "（解析対象）", className="ms-1"),
            html.Span(" — " + text, className="ms-1"),
        ], className="registration-missing" if missing else "registration-unconfirmed" if not confirmed else "registration-complete"))
    structural = []
    try:
        issues = validate_registered_sections(sections, components=_component_counts(layout)) if sections else []
        structural = [f"{issue.display_name}: {issue.reason}" for issue in issues if issue.reason]
    except (ValueError, TypeError, KeyError) as exc:
        structural = [f"登録情報の形式を確認してください：{exc}"]
    total = len(sections)
    if not total:
        headline = "切片情報の読み込みを待っています"
    elif missing_count:
        headline = f"① 入力が必要：{missing_count}切片・{len(cells)}項目"
    elif structural:
        headline = "登録情報の整合性を確認してください"
    elif unconfirmed_count:
        headline = f"① 入力完了 → ② 全{total}切片の内容を確認してください"
    else:
        headline = "② 確認完了 → ③ 適用できます"
    children = [html.Div(headline, className="fw-semibold"),
                html.Small(f"登録：全{total}切片 ／ 今回の解析：{selected_count}切片 ／ 未確認：{unconfirmed_count}切片"),
                html.Ul(statuses, className="imzml-registration-status-list")]
    if structural:
        children.append(html.Div([html.Strong("登録情報の問題"), html.Ul([html.Li(message) for message in structural])],
                                 className="registration-missing"))
    next_text = (f"次に入力：{table_rows[cells[0]['row']].get('section_display_name') or '切片 ' + str(cells[0]['row'] + 1)} の{labels[cells[0]['column_id']]}"
                 if cells else "未入力項目はありません")
    return styles, children, cells, not bool(cells), next_text, tips


def _apply_table_metadata(file_id, layout, sections, table_rows):
    edits = {
        str(row.get("section_id")): row
        for row in (table_rows or [])
        if row.get("section_id")
    }
    updated = deepcopy(sections or [])
    for row in updated:
        edited = edits.get(str(row.get("section_id")))
        if not edited:
            continue
        changed = False
        for key in ("section_display_name", "subject_id", "group"):
            value = str(edited.get(key) or "").strip()
            changed = changed or value != str(row.get(key) or "").strip()
            row[key] = value
        requested_confirmed = str(
            edited.get("metadata_confirmed") or ""
        ).strip() == "確認済"
        # 名称・個体・群を変更した行は、変更後に改めて確認操作を要求する。
        row["metadata_confirmed"] = requested_confirmed and not changed
    return normalize_spatial_sections(file_id, layout, updated)


def _figure(layout, sections):
    figure = go.Figure()
    components = {str(c["component_id"]): c for c in (layout or {}).get("components", [])}
    component_to_section = {}
    for row in sections or []:
        for cid in row.get("component_ids", []):
            component_to_section[str(cid)] = row
    for index, row in enumerate(sections or []):
        points = []
        for cid in row.get("component_ids", []):
            points.extend(components.get(str(cid), {}).get("preview", []))
        figure.add_trace(go.Scattergl(
            x=[p["x"] for p in points], y=[p["y"] for p in points],
            mode="markers", name=row.get("section_display_name", f"Section {index + 1:02d}"),
            marker={"size": 4, "opacity": 0.86 if row.get("selected", True) else 0.2},
            hovertemplate="x=%{x}<br>y=%{y}<extra>%{fullData.name}</extra>",
        ))
    annotations = []
    for component in components.values():
        section = component_to_section.get(str(component["component_id"]), {})
        annotations.append({
            "x": component["centroid"]["x"], "y": component["centroid"]["y"],
            "text": str(component.get("order", "")), "showarrow": False,
            "font": {"size": 12}, "bgcolor": "rgba(255,255,255,0.75)",
            "bordercolor": "rgba(60,60,60,0.35)",
            "opacity": 1.0 if section.get("selected", True) else 0.35,
        })
    figure.update_layout(
        margin={"l": 35, "r": 10, "t": 10, "b": 30},
        legend={"orientation": "h", "y": -0.12},
        xaxis={"title": "x", "scaleanchor": "y", "constrain": "domain"},
        yaxis={"title": "y", "autorange": "reversed", "constrain": "domain"},
        annotations=annotations,
        uirevision=(layout or {}).get("coordinate_hash", "spatial"),
        plot_bgcolor="white",
    )
    return figure


def _layout_for_path(catalog, path):
    row = _entry(catalog, path)
    if not row:
        raise SpatialLayoutError("選択したimzMLが候補一覧にありません。")
    layout = row.get("spatial_layout") or {}
    if not layout.get("components"):
        raise SpatialLayoutError(row.get("spatial_error") or "座標layoutがありません。")
    return row, layout


def _component_counts(layout):
    return {
        str(row["component_id"]): int(row.get("pixel_count", 0))
        for row in (layout or {}).get("components", [])
        if row.get("component_id")
    }


def _editor_revision(file_entry, catalog_entry):
    """編集中の背景変更を検出し、古いfloatで新しい入力を上書きしない。"""
    source = file_entry or catalog_entry or {}
    rows = (source.get("registered_sections") or source.get("spatial_sections") or [])
    selected = source.get("selected_section_ids")
    if selected is None:
        selected = [row["section_id"] for row in rows if row.get("selected", True)]
    return {
        "coordinate_hash": (source.get("spatial_layout") or {}).get("coordinate_hash"),
        "sections": normalized_registered_sections(rows),
        "source_selected_section_ids": deepcopy(source.get("source_selected_section_ids")),
        "metadata_overlay": deepcopy(source.get("metadata_overlay") or {}),
        "selected_section_ids": sorted(str(value) for value in selected),
    }


def _register(scope):
    suffix = {"initial": "", "reanalysis": "_reanalysis", "conversion": "_conversion"}.get(scope)
    if suffix is None:
        raise ValueError(f"未知のimzML float scopeです: {scope}")
    panel = "imzml_spatial_float" + suffix

    @callback(
        Output(panel, "style", allow_duplicate=True),
        Output(panel, "className", allow_duplicate=True),
        Output("imzml_spatial_active_path" + suffix, "data", allow_duplicate=True),
        Output("imzml_spatial_draft" + suffix, "data", allow_duplicate=True),
        Output("imzml_spatial_graph" + suffix, "figure", allow_duplicate=True),
        Output("imzml_spatial_table" + suffix, "data", allow_duplicate=True),
        Output("imzml_spatial_table" + suffix, "selected_rows", allow_duplicate=True),
        Output("imzml_spatial_title" + suffix, "children", allow_duplicate=True),
        Output("imzml_spatial_message" + suffix, "children", allow_duplicate=True),
        Output("imzml_spatial_base" + suffix, "data"),
        Input({"type": "imzml_spatial_open", "scope": scope, "index": ALL}, "n_clicks"),
        State({"type": "imzml_spatial_open", "scope": scope, "index": ALL}, "id"),
        State("section_catalog_store" + suffix, "data"),
        State("section_manifest_store" + suffix, "data"),
        State("imzml_spatial_overrides" + suffix, "data"),
        prevent_initial_call=True,
    )
    def open_panel(clicks, ids, catalog, manifest, overrides=None):
        trigger = ctx.triggered_id
        triggered_value = (ctx.triggered[0].get("value") if getattr(ctx, "triggered", None) else None)
        if (not isinstance(trigger, dict) or trigger.get("type") not in {"imzml_spatial_open", "imzml_registration_fix"}
                or not triggered_value):
            return (no_update,) * 10
        path = trigger["index"]
        file_entry = _entry((manifest or {}).get("files", []), path)
        catalog_entry, layout = _layout_for_path(catalog, path)
        source = ((file_entry or {}).get("spatial_sections")
                  or (file_entry or {}).get("registered_sections")
                  or catalog_entry.get("spatial_sections"))
        # ★ ver74.0: 手動変換にはoverride→catalogの更新callbackがないため、
        # 再表示も実行specと同じ適用済み情報を読む。別座標の登録は使わない。
        override = (overrides or {}).get(path) or {}
        if (scope == "conversion" and override
                and override.get("coordinate_hash") == layout.get("coordinate_hash")):
            source = override.get("registered_sections") or override.get("spatial_sections") or source
        file_id = (file_entry or {}).get("file_id")
        if not file_id:
            from app.services.section_metadata import stable_file_id
            file_id = stable_file_id(path)
        sections = normalize_spatial_sections(file_id, layout, source)
        issues = validate_registered_sections(sections, components=_component_counts(layout))
        message = (f"{layout.get('component_count', len(layout.get('components', [])))} 個の座標成分、"
                   f"{layout.get('pixel_count', 0):,} pixels")
        if issues:
            message += f" ／ 必須情報未完了 {len(issues)} 件"
        warnings = layout.get("warnings") or []
        if warnings:
            message += " ／ " + " ／ ".join(warnings[:2])
        if scope == "reanalysis" and "source_selected_section_ids" in (file_entry or {}):
            message += " ／ 再解析は元RDSに含まれる切片だけを選択できます。切片の結合・分割は新規解析で行ってください。"
        panel_title = ("imzML全切片登録" if scope == "conversion"
                       else "imzML全切片登録・解析選択")
        return ({"display": "block"}, "imzml-spatial-float", path, sections,
                _figure(layout, sections), _table_rows(sections), [],
                f"{panel_title} — {Path(path).name}", message,
                _editor_revision(file_entry, catalog_entry))

    # ★ ver74.1: 外側の不足一覧から該当ファイルへ直接移動する。既存編集callbackの契約は維持。
    @callback(
        Output(panel, "style", allow_duplicate=True),
        Output(panel, "className", allow_duplicate=True),
        Output("imzml_spatial_active_path" + suffix, "data", allow_duplicate=True),
        Output("imzml_spatial_draft" + suffix, "data", allow_duplicate=True),
        Output("imzml_spatial_graph" + suffix, "figure", allow_duplicate=True),
        Output("imzml_spatial_table" + suffix, "data", allow_duplicate=True),
        Output("imzml_spatial_table" + suffix, "selected_rows", allow_duplicate=True),
        Output("imzml_spatial_title" + suffix, "children", allow_duplicate=True),
        Output("imzml_spatial_message" + suffix, "children", allow_duplicate=True),
        Output("imzml_spatial_base" + suffix, "data", allow_duplicate=True),
        Input({"type": "imzml_registration_fix", "scope": scope, "index": ALL}, "n_clicks"),
        State({"type": "imzml_registration_fix", "scope": scope, "index": ALL}, "id"),
        State("section_catalog_store" + suffix, "data"),
        State("section_manifest_store" + suffix, "data"),
        State("imzml_spatial_overrides" + suffix, "data"), prevent_initial_call=True,
    )
    def open_registration_issue(clicks, ids, catalog, manifest, overrides=None):
        return open_panel(clicks, ids, catalog, manifest, overrides)

    @callback(
        Output("imzml_spatial_table" + suffix, "style_data_conditional"),
        Output("imzml_spatial_guidance" + suffix, "children"),
        Output("imzml_spatial_missing_cells" + suffix, "data"),
        Output("imzml_spatial_next_missing" + suffix, "disabled"),
        Output("imzml_spatial_next_missing" + suffix, "children"),
        Output("imzml_spatial_table" + suffix, "tooltip_data"),
        Input("imzml_spatial_table" + suffix, "data"),
        Input("imzml_spatial_draft" + suffix, "data"),
        Input("section_catalog_store" + suffix, "data"),
        Input("imzml_spatial_active_path" + suffix, "data"),
    )
    def show_registration_guidance(table_rows, draft, catalog, path):
        entry = _entry(catalog, path) if path else {}
        return _registration_guidance(table_rows, draft, (entry or {}).get("spatial_layout") or {})

    @callback(
        Output("imzml_spatial_table" + suffix, "active_cell"),
        Output("imzml_spatial_table" + suffix, "selected_cells"),
        Input("imzml_spatial_next_missing" + suffix, "n_clicks"),
        State("imzml_spatial_missing_cells" + suffix, "data"),
        State("imzml_spatial_table" + suffix, "active_cell"), prevent_initial_call=True,
    )
    def focus_next_missing(clicks, cells, active):
        if not clicks or not cells:
            return no_update, no_update
        # ★ ver74.1: ボタンに表示した先頭の不足セルと移動先を必ず一致させる。
        # 入力が完了するとlive表示で次の不足セルへ進む。
        target = cells[0]
        return target, [target]

    @callback(
        Output("imzml_spatial_table" + suffix, "selected_rows", allow_duplicate=True),
        Input("imzml_spatial_graph" + suffix, "clickData"),
        State("imzml_spatial_table" + suffix, "selected_rows"),
        prevent_initial_call=True,
    )
    def select_from_graph(click_data, selected_rows):
        if not click_data or not click_data.get("points"):
            return no_update
        try:
            row = int(click_data["points"][0]["curveNumber"])
        except (KeyError, TypeError, ValueError):
            return no_update
        selected = list(selected_rows or [])
        if row in selected:
            selected.remove(row)
        else:
            selected.append(row)
        return sorted(set(selected))

    @callback(
        Output("imzml_spatial_draft" + suffix, "data", allow_duplicate=True),
        Output("imzml_spatial_graph" + suffix, "figure", allow_duplicate=True),
        Output("imzml_spatial_table" + suffix, "data", allow_duplicate=True),
        Output("imzml_spatial_table" + suffix, "selected_rows", allow_duplicate=True),
        Output("imzml_spatial_overrides" + suffix, "data", allow_duplicate=True),
        Output(panel, "style", allow_duplicate=True),
        Output(panel, "className", allow_duplicate=True),
        Output("imzml_spatial_message" + suffix, "children", allow_duplicate=True),
        Input("imzml_spatial_toggle" + suffix, "n_clicks"),
        Input("imzml_spatial_merge" + suffix, "n_clicks"),
        Input("imzml_spatial_one" + suffix, "n_clicks"),
        Input("imzml_spatial_reset" + suffix, "n_clicks"),
        Input("imzml_spatial_bulk_apply" + suffix, "n_clicks"),
        Input("imzml_spatial_confirm_all" + suffix, "n_clicks"),
        Input("imzml_spatial_apply" + suffix, "n_clicks"),
        Input("imzml_spatial_cancel" + suffix, "n_clicks"),
        Input("imzml_spatial_close" + suffix, "n_clicks"),
        Input("imzml_spatial_minimize" + suffix, "n_clicks"),
        State("imzml_spatial_active_path" + suffix, "data"),
        State("imzml_spatial_draft" + suffix, "data"),
        State("imzml_spatial_table" + suffix, "data"),
        State("imzml_spatial_table" + suffix, "selected_rows"),
        State("imzml_spatial_bulk_group" + suffix, "value"),
        State("imzml_spatial_overrides" + suffix, "data"),
        State("section_catalog_store" + suffix, "data"),
        State("section_manifest_store" + suffix, "data"),
        State(panel, "className"),
        State("imzml_spatial_base" + suffix, "data"),
        prevent_initial_call=True,
    )
    def act_on_panel(toggle, merge, one, reset, bulk_apply, confirm_all, apply,
                     cancel, close, minimize, path, draft, table_rows,
                     selected_rows, bulk_group, overrides, catalog, manifest,
                     class_name, base_revision=None):
        trigger = ctx.triggered_id
        if not path:
            return (no_update,) * 8
        if trigger == "imzml_spatial_minimize" + suffix:
            minimized = "minimized" in (class_name or "")
            next_class = "imzml-spatial-float" if minimized else "imzml-spatial-float minimized"
            return no_update, no_update, no_update, no_update, no_update, no_update, next_class, no_update
        if trigger in ("imzml_spatial_cancel" + suffix, "imzml_spatial_close" + suffix):
            return (None, no_update, no_update, [], no_update, {"display": "none"},
                    "imzml-spatial-float", "未適用の変更を破棄しました。")

        file_entry = _entry((manifest or {}).get("files", []), path)
        catalog_entry, layout = _layout_for_path(catalog, path)
        file_id = (file_entry or {}).get("file_id")
        if not file_id:
            from app.services.section_metadata import stable_file_id
            file_id = stable_file_id(path)
        sections = normalize_spatial_sections(file_id, layout, draft)
        # ★ ver74.0: 旧manifestを操作ごとにdraftへ戻すと、確認した名称変更を
        # 毎回「新たな編集」と判定して適用不能になった。draftを編集の正とする。
        overrides = deepcopy(overrides or {})

        try:
            sections = _apply_table_metadata(file_id, layout, sections, table_rows)
            source_ids = None
            if scope == "reanalysis" and "source_selected_section_ids" in (file_entry or {}):
                source_ids = set(file_entry["source_selected_section_ids"])
                # ★ ver74.0: 切片ID変更を伴う再登録は元RDSの画素対応を壊すため新規解析で行う。
                if trigger in {"imzml_spatial_" + action + suffix for action in ("reset", "one", "merge")}:
                    raise SpatialLayoutError("元解析RDSの切片構成は変更できません。結合・分割は新規解析で行ってください。")
            if trigger == "imzml_spatial_toggle" + suffix:
                indices = [i for i in (selected_rows or [])
                           if isinstance(i, int) and 0 <= i < len(sections)]
                if not indices:
                    raise SpatialLayoutError("今回の解析への使用状態を変更する行を選択してください。")
                next_value = not all(sections[i].get("selected", True) for i in indices)
                if next_value and source_ids is not None and any(sections[i]["section_id"] not in source_ids for i in indices):
                    raise SpatialLayoutError("元解析RDSに含まれない切片は再解析に追加できません。新規解析で選択してください。")
                for i in indices:
                    sections[i]["selected"] = next_value
                message = (f"選択した {len(indices)} 切片を今回の解析で"
                           f"{'使用' if next_value else '使用しない'}設定にしました。")
            elif trigger == "imzml_spatial_bulk_apply" + suffix:
                indices = [i for i in (selected_rows or [])
                           if isinstance(i, int) and 0 <= i < len(sections)]
                label = str(bulk_group or "").strip()
                if not indices:
                    raise SpatialLayoutError("群を一括設定する行を選択してください。")
                if not label:
                    raise SpatialLayoutError("一括設定する群名を入力してください。")
                for i in indices:
                    sections[i]["group"] = label
                    sections[i]["metadata_confirmed"] = False
                message = f"選択した {len(indices)} 切片に群「{label}」を設定しました。再確認してください。"
            elif trigger == "imzml_spatial_confirm_all" + suffix:
                missing = [row for row in sections if not str(row.get("section_display_name") or "").strip()
                           or not str(row.get("subject_id") or "").strip()
                           or not str(row.get("group") or "").strip()]
                if missing:
                    raise SpatialLayoutError(
                        f"まだ {len(missing)} 切片に未入力があります。①の赤枠セルを入力してください。"
                        "「次に入力」ボタンで不足セルへ移動できます。"
                    )
                for row in sections:
                    row["metadata_confirmed"] = True
                message = f"全 {len(sections)} 切片の登録情報を確認済みにしました。"
            elif trigger == "imzml_spatial_reset" + suffix:
                sections = reset_spatial_sections(file_id, layout, sections)
                message = "座標の自動検出結果へ戻しました。全切片の情報を再確認してください。"
            elif trigger == "imzml_spatial_one" + suffix:
                if len(sections) > 1:
                    sections = merge_spatial_sections(file_id, layout, sections, range(len(sections)))
                message = "すべての座標成分を1切片にまとめました。登録情報を再確認してください。"
            elif trigger == "imzml_spatial_merge" + suffix:
                sections = merge_spatial_sections(file_id, layout, sections, selected_rows or [])
                message = "選択した切片を結合しました。登録情報を再確認してください。"
            elif trigger == "imzml_spatial_apply" + suffix:
                if (base_revision is not None
                        and base_revision != _editor_revision(file_entry, catalog_entry)):
                    raise SpatialLayoutError(
                        "編集中に背景の切片情報または解析選択が変更されました。"
                        "変更内容を控え、この画面を閉じて開き直してください。"
                    )
                issues = validate_registered_sections(
                    sections, components=_component_counts(layout)
                )
                if issues:
                    raise SpatialLayoutError(format_section_issues(issues, filename=path))
                if source_ids is not None and any(row.get("selected", True) and row["section_id"] not in source_ids for row in sections):
                    raise SpatialLayoutError("元解析RDSに含まれない切片は再解析に追加できません。新規解析で選択してください。")
                overrides[path] = {
                    "coordinate_hash": layout.get("coordinate_hash"),
                    "spatial_sections": sections,
                    "registered_sections": sections,
                    "selected_section_ids": [
                        row["section_id"] for row in sections if row.get("selected", True)
                    ],
                }
                applied_message = ("全切片の登録情報を適用しました。" if scope == "conversion"
                                   else "全切片の登録情報と今回の解析選択を適用しました。")
                return (sections, _figure(layout, sections), _table_rows(sections), [], overrides,
                        {"display": "none"}, "imzml-spatial-float", applied_message)
            else:
                return (no_update,) * 8
        except SpatialLayoutError as exc:
            return (no_update, no_update, no_update, no_update, no_update, no_update,
                    no_update, str(exc))
        return (sections, _figure(layout, sections), _table_rows(sections), [], no_update,
                no_update, "imzml-spatial-float", message)


_register("initial")
_register("reanalysis")
_register("conversion")
