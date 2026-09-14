"""切片の群情報の閲覧・編集・出力。解析処理は起動しない。"""
from dash import Input, Output, State, callback, dcc, html, no_update
from dash.exceptions import PreventUpdate
from app.services.section_group_metadata import (export_metadata_frame, filter_groups, group_rows,
    group_summary, overlay_result_metadata, save_group_edits)


def _data(rds_path):
    from app.callbacks.interactive_callbacks import _interactive_data, _set_active_key
    _set_active_key(rds_path)
    df = overlay_result_metadata(_interactive_data.get("plot_data"), rds_path)
    if df is not None:
        _interactive_data["plot_data"] = df
    return df


@callback(Output("int_section_group_table", "data"), Output("int_section_group_summary", "children"),
    Output("int_section_group_filter", "options"), Output("int_section_group_filter", "value"),
    Output("int_section_group_panel", "style"), Input("seurat_rds_path_store", "data"),
    Input("int_section_group_updated", "data"), prevent_initial_call=True)
def load_section_groups(rds_path, _updated):
    df = _data(rds_path)
    rows = group_rows(df)
    if not rows:
        return [], "", [], None, {"display": "none"}
    summary = group_summary(df)
    labels = [r["group"] for r in summary]
    parts = []
    for row in summary:
        text = f"{row['group']}: {row['sections']}切片 / 独立試料 {row['subjects']}"
        if row["missing_subjects"]:
            text += f"（個体ID未設定 {row['missing_subjects']}切片）"
        parts.append(html.Span(text, className="me-3"))
    return rows, parts, [{"label": g, "value": g} for g in labels], labels, {}


@callback(Output("int_section_group_status", "children"), Output("int_section_group_updated", "data"),
    Input("int_section_group_save", "n_clicks"), State("int_section_group_table", "data"),
    State("seurat_rds_path_store", "data"), State("int_section_group_updated", "data"), prevent_initial_call=True)
def save_section_groups(n_clicks, rows, rds_path, updated):
    if not n_clicks:
        raise PreventUpdate
    try:
        save_group_edits(rds_path, rows)
        _data(rds_path)
    except (ValueError, OSError) as exc:
        return f"保存できません: {exc}", no_update
    return "群・個体情報を保存しました。", int(updated or 0) + 1


@callback(Output("int_section_metadata_download", "data"), Input("int_section_metadata_export", "n_clicks"),
    State("seurat_rds_path_store", "data"), State("int_section_group_filter", "value"), prevent_initial_call=True)
def export_section_metadata(n_clicks, rds_path, groups):
    if not n_clicks:
        raise PreventUpdate
    df = _data(rds_path)
    if df is None:
        raise PreventUpdate
    return dcc.send_data_frame(export_metadata_frame(filter_groups(df, groups)).to_csv,
                               "pixel_section_metadata.csv", index=False)
