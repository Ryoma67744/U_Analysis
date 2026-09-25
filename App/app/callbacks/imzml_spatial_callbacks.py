"""imzML座標切片のモデルレスfloatとsection構成を同期する。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from dash import ALL, Input, Output, State, callback, ctx, no_update
import plotly.graph_objects as go

from app.services.imzml_spatial_layout import (
    SpatialLayoutError,
    default_spatial_sections,
    merge_spatial_sections,
    normalize_spatial_sections,
    reset_spatial_sections,
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
        "component_count": len(row.get("component_ids", [])),
        "pixel_count": int(row.get("pixel_count", 0)),
        "selected": "対象" if row.get("selected", True) else "除外",
    } for row in sections or []]


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


def _register(scope):
    suffix = "" if scope == "initial" else "_reanalysis"
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
        Input({"type": "imzml_spatial_open", "scope": scope, "index": ALL}, "n_clicks"),
        State({"type": "imzml_spatial_open", "scope": scope, "index": ALL}, "id"),
        State("section_catalog_store" + suffix, "data"),
        State("section_manifest_store" + suffix, "data"),
        prevent_initial_call=True,
    )
    def open_panel(clicks, ids, catalog, manifest):
        trigger = ctx.triggered_id
        triggered_value = (ctx.triggered[0].get("value") if getattr(ctx, "triggered", None) else None)
        if (not isinstance(trigger, dict) or trigger.get("type") != "imzml_spatial_open"
                or not triggered_value):
            return (no_update,) * 9
        path = trigger["index"]
        file_entry = _entry((manifest or {}).get("files", []), path)
        catalog_entry, layout = _layout_for_path(catalog, path)
        source = ((file_entry or {}).get("spatial_sections")
                  or catalog_entry.get("spatial_sections"))
        file_id = (file_entry or {}).get("file_id")
        if not file_id:
            from app.services.section_metadata import stable_file_id
            file_id = stable_file_id(path)
        sections = normalize_spatial_sections(file_id, layout, source)
        warnings = layout.get("warnings") or []
        message = (f"{layout.get('component_count', len(layout.get('components', [])))} 個の座標成分、"
                   f"{layout.get('pixel_count', 0):,} pixels")
        if warnings:
            message += " ／ " + " ／ ".join(warnings[:2])
        return ({"display": "block"}, "imzml-spatial-float", path, sections,
                _figure(layout, sections), _table_rows(sections), [],
                f"imzML切片配置 — {Path(path).name}", message)

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
        Input("imzml_spatial_apply" + suffix, "n_clicks"),
        Input("imzml_spatial_cancel" + suffix, "n_clicks"),
        Input("imzml_spatial_close" + suffix, "n_clicks"),
        Input("imzml_spatial_minimize" + suffix, "n_clicks"),
        State("imzml_spatial_active_path" + suffix, "data"),
        State("imzml_spatial_draft" + suffix, "data"),
        State("imzml_spatial_table" + suffix, "selected_rows"),
        State("imzml_spatial_overrides" + suffix, "data"),
        State("section_catalog_store" + suffix, "data"),
        State("section_manifest_store" + suffix, "data"),
        State(panel, "className"),
        prevent_initial_call=True,
    )
    def act_on_panel(toggle, merge, one, reset, apply, cancel, close, minimize,
                     path, draft, selected_rows, overrides, catalog, manifest, class_name):
        trigger = ctx.triggered_id
        if not path:
            return (no_update,) * 8
        file_entry = _entry((manifest or {}).get("files", []), path)
        _catalog_entry, layout = _layout_for_path(catalog, path)
        file_id = (file_entry or {}).get("file_id")
        if not file_id:
            from app.services.section_metadata import stable_file_id
            file_id = stable_file_id(path)
        sections = normalize_spatial_sections(file_id, layout, draft)
        # モデルレスfloatを開いたまま背景の切片名・個体・群を編集した場合、
        # 同じsection_idの最新メタデータを採用し、古いdraftで巻き戻さない。
        latest = {str(row.get("section_id")): row
                  for row in (file_entry or {}).get("spatial_sections", [])
                  if row.get("section_id")}
        for row in sections:
            current = latest.get(str(row.get("section_id")))
            if current:
                for key in ("section_display_name", "subject_id", "group"):
                    row[key] = str(current.get(key) or row.get(key) or "").strip()
        overrides = deepcopy(overrides or {})

        if trigger == "imzml_spatial_minimize" + suffix:
            minimized = "minimized" in (class_name or "")
            next_class = "imzml-spatial-float" if minimized else "imzml-spatial-float minimized"
            return no_update, no_update, no_update, no_update, no_update, no_update, next_class, no_update
        if trigger in ("imzml_spatial_cancel" + suffix, "imzml_spatial_close" + suffix):
            return None, no_update, no_update, [], no_update, {"display": "none"}, "imzml-spatial-float", "未適用の変更を破棄しました。"
        try:
            if trigger == "imzml_spatial_toggle" + suffix:
                indices = [i for i in (selected_rows or []) if isinstance(i, int) and 0 <= i < len(sections)]
                if not indices:
                    raise SpatialLayoutError("対象／除外を切り替える行を選択してください。")
                # 選択行がすべて対象なら除外、1件でも除外ならすべて対象へ戻す。
                next_value = not all(sections[i].get("selected", True) for i in indices)
                for i in indices:
                    sections[i]["selected"] = next_value
                message = f"選択した {len(indices)} 切片を「{'対象' if next_value else '除外'}」にしました。"
            elif trigger == "imzml_spatial_reset" + suffix:
                sections = reset_spatial_sections(file_id, layout, sections)
                message = "座標の自動検出結果へ戻しました。適用するまで解析条件は変わりません。"
            elif trigger == "imzml_spatial_one" + suffix:
                if len(sections) > 1:
                    sections = merge_spatial_sections(file_id, layout, sections, range(len(sections)))
                message = "すべての座標成分を1切片にまとめました。"
            elif trigger == "imzml_spatial_merge" + suffix:
                sections = merge_spatial_sections(file_id, layout, sections, selected_rows or [])
                message = "選択した切片を結合しました。"
            elif trigger == "imzml_spatial_apply" + suffix:
                overrides[path] = {"coordinate_hash": layout.get("coordinate_hash"),
                                   "spatial_sections": sections}
                return (sections, _figure(layout, sections), _table_rows(sections), [], overrides,
                        {"display": "none"}, "imzml-spatial-float",
                        "切片構成を適用しました。")
            else:
                return (no_update,) * 8
        except SpatialLayoutError as exc:
            return (no_update, no_update, no_update, no_update, no_update, no_update,
                    no_update, str(exc))
        return (sections, _figure(layout, sections), _table_rows(sections), [], no_update,
                no_update, "imzml-spatial-float", message)


_register("initial")
_register("reanalysis")
