# =============================================================================
# MSI Analysis Application - SCiLS Converter Callbacks
# SCiLS 変換モーダルの開閉・変換実行ロジック
# =============================================================================

import logging
from pathlib import Path

from dash import callback, Input, Output, State, no_update, html
import dash_bootstrap_components as dbc

from app.services.scils_converter import convert_scils_to_parquet

logger = logging.getLogger("msi.scils_converter_callbacks")


# ---------------------------------------------------------------------------
# モーダル開閉
# ---------------------------------------------------------------------------

@callback(
    Output("scils_converter_modal", "is_open"),
    Output("scils_conversion_result", "children", allow_duplicate=True),
    Input("open_scils_converter_modal", "n_clicks"),
    Input("scils_cancel_btn", "n_clicks"),
    State("scils_converter_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_scils_converter_modal(open_clicks, cancel_clicks, is_open):
    if not (open_clicks or cancel_clicks):
        return no_update, no_update
    if not is_open:
        return True, ""
    return False, no_update


# ---------------------------------------------------------------------------
# 変換実行
# ---------------------------------------------------------------------------

def _auto_sample_name(input_folder: str) -> str:
    """入力フォルダ名をベースにサンプル名を生成"""
    name = Path(input_folder).name or "converted_sample"
    for bad in ("/", "\\", ":", "*", "?", "\"", "<", ">", "|"):
        name = name.replace(bad, "_")
    return name


@callback(
    Output("scils_conversion_result", "children"),
    Input("scils_run_btn", "n_clicks"),
    State("scils_input_folder", "value"),
    State("scils_output_folder", "value"),
    State("scils_sample_name", "value"),
    State("scils_organize_check", "value"),
    State("scils_float32_check", "value"),
    State("scils_spot_block", "value"),
    prevent_initial_call=True,
)
def run_scils_conversion(
    n_clicks, input_folder, output_folder, sample_name,
    organize_value, float32_value, spot_block_value,
):
    if not n_clicks:
        return no_update

    if not input_folder:
        return dbc.Alert("入力フォルダを指定してください。", color="warning")
    if not output_folder:
        return dbc.Alert("出力先フォルダを指定してください。", color="warning")

    sample = (sample_name or "").strip() or _auto_sample_name(input_folder)
    if not sample.lower().endswith(".parquet"):
        sample = f"{sample}.parquet"

    out_path = str(Path(output_folder) / sample)

    organize = bool(organize_value) and "on" in (organize_value or [])
    store_float32 = bool(float32_value) and "on" in (float32_value or [])
    try:
        spot_block = int(spot_block_value) if spot_block_value else 200
    except (TypeError, ValueError):
        spot_block = 200

    try:
        result = convert_scils_to_parquet(
            input_folder, out_path,
            spot_block=spot_block,
            store_float32=store_float32,
            organize=organize,
        )
    except FileNotFoundError as exc:
        logger.warning("SCiLS 変換失敗 (入力不備): %s", exc)
        return dbc.Alert(str(exc), color="danger")
    except ValueError as exc:
        logger.warning("SCiLS 変換失敗 (形式/整合性エラー): %s", exc)
        return dbc.Alert(html.Pre(str(exc), className="mb-0 small"), color="danger")
    except Exception as exc:  # noqa: BLE001 — UI にエラーを出して継続
        logger.exception("SCiLS 変換で予期せぬエラー")
        return dbc.Alert(f"変換エラー: {exc}", color="danger")

    return _render_success(result)


def _render_success(result) -> html.Div:
    """ConversionResult を成功パネルとしてレンダリング"""
    warning_items = [html.Li(w) for w in result.warnings]
    ann_labels = ", ".join(result.annotation_labels) if result.annotation_labels else "(なし)"

    details = [
        (html.Dt("出力ファイル"), html.Dd(result.output_path)),
        (html.Dt("Intensity CSV"), html.Dd(Path(result.source_intensity).name)),
        (html.Dt("Spot CSV"), html.Dd(Path(result.source_spot).name)),
        (
            html.Dt("Annotation CSV"),
            html.Dd(
                ", ".join(Path(p).name for p in result.annotation_files)
                if result.annotation_files else "(なし)"
            ),
        ),
        (
            html.Dt("Peak-list (化合物名)"),
            html.Dd(
                f"{Path(result.peak_list_file).name}"
                f"（{result.n_annotated:,} / {result.n_mz_features:,} feature に付与）"
                if result.has_peak_list else "(なし)"
            ),
        ),
        (html.Dt("spot 数"), html.Dd(f"{result.n_spots:,}")),
        (html.Dt("m/z 列数"), html.Dd(f"{result.n_mz_features:,}")),
        (html.Dt("annotation ラベル"), html.Dd(ann_labels)),
        (html.Dt("所要時間"), html.Dd(f"{result.duration_sec:.1f} 秒")),
    ]
    if result.organized:
        details.append((html.Dt("整理モード"), html.Dd("ON (元 CSV をサブフォルダに移動)")))

    dl_children = []
    for dt, dd in details:
        dl_children.append(dt)
        dl_children.append(dd)

    return html.Div([
        dbc.Alert("✅ 変換完了", color="success", className="mb-2"),
        html.Dl(className="mb-0", children=dl_children),
        html.Div(
            style={"marginTop": "8px"} if warning_items else {"display": "none"},
            children=[
                html.Strong("警告:"),
                html.Ul(warning_items),
            ],
        ),
        html.Small(
            "解析タブの「データフォルダ」に出力先を指定するとそのまま読込できます。",
            className="text-muted d-block mt-2",
        ),
    ])
