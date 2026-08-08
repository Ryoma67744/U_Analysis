# =============================================================================
# MSI Analysis Application - 解析条件の収集コールバック
# =============================================================================
# 論文の図は Interactive タブから出てくるのに、そのパネルの設定はどこにも
# 残っていなかった。ここではパネルごとに collector を置き、値が変わるたびに
# <RDS と同じディレクトリ>/interactive_settings.json へ書く。
#
# 方式は既存の save_umap_display_settings（interactive_umap.py）と同じ:
#   Input = 各ウィジェット / State = seurat_rds_path_store / 出力はダミー Store。
# 書き込みは label_persistence 側で FileLock + 原子的書き込みになっている。
#
# ここに集約した理由: 「どの設定が記録対象か」を 1 ファイルで見渡せるようにする
# ため。パネル実装側（interactive_deg.py 等）に散らすと、パネルを増やしたときに
# 記録を足し忘れて静かに条件が抜け落ちる。
#
# キー追加は加算のみ。既存キー（umap_display / spatial_display / ...）の意味は
# 変えない（軽量ビューア lite_view_callbacks.py が同じファイルを読むため）。
# =============================================================================

from __future__ import annotations

import io
import logging
import zipfile
from datetime import datetime
from pathlib import Path

from dash import (Input, Output, State, callback, clientside_callback,
                  dcc, no_update)
from dash.exceptions import PreventUpdate

logger = logging.getLogger("msi.provenance_callbacks")
access_logger = logging.getLogger("msi.access")

_TRIGGER = Output("provenance_save_trigger", "data", allow_duplicate=True)


def _save(key: str, value: dict, rds_path):
    """interactive_settings.json の 1 キーを更新する（失敗しても画面は壊さない）。

    ★ ver51.8: `rds_path` を **真偽判定にしか使っていなかった**。実際の書き込み先は
      `_save_interactive_settings` がスレッドの active key から解決するため、
      `_set_active_key` を呼ばないと
        - active key 未設定 → `__default__` の rds_path=None → **記録が黙って捨てられる**
        - active key が別プロジェクト → **他プロジェクトの
          interactive_settings.json へ書き込む**
      のどちらかになる。waitress はスレッドを使い回すので後者は実際に起こる。
      正しい形は interactive_spatial.save_spatial_display_settings にある。
    """
    if not rds_path:
        raise PreventUpdate
    try:
        from app.callbacks.interactive_callbacks import (
            _save_interactive_settings, _set_active_key)
        _set_active_key(rds_path)
        _save_interactive_settings(key, value)
    except Exception as e:
        logger.warning("解析条件の保存に失敗 (%s): %s", key, e)
    return no_update


# ---------------------------------------------------------------------------
# Volcano
# ---------------------------------------------------------------------------
# 注意: ここの閾値は「表示・ラベル付け」専用で、検定には使われていない。
# 実際の統計判定は解析設定タブの p_thresh / logfc_thresh。
# Methods 生成側（methods_text.py）でその区別を明示している。

@callback(
    _TRIGGER,
    [Input("volcano_cluster_select", "value"),
     Input("volcano_fc_threshold", "value"),
     Input("volcano_p_threshold", "value"),
     Input("volcano_y_max", "value"),
     Input("volcano_marker_size", "value"),
     Input("volcano_label_top_n", "value"),
     Input("volcano_annotation_switch", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_volcano_settings(cluster, fc_th, p_th, y_max, marker_size,
                          label_top_n, annotation_switch, rds_path):
    return _save("volcano_display", {
        "cluster": cluster,
        "fc_threshold": fc_th,
        "p_threshold": p_th,
        "y_max": y_max,
        "marker_size": marker_size,
        "label_top_n": label_top_n,
        "show_annotation": bool(annotation_switch),
        "_note": "display/labelling only; not the statistical test thresholds",
    }, rds_path)


# ---------------------------------------------------------------------------
# Heatmap
# ---------------------------------------------------------------------------
# scale は Z-score / Raw の切替＝データ変換そのもの。図の見え方だけでなく
# 「何をプロットしたか」が変わるので、記録必須。

@callback(
    _TRIGGER,
    [Input("heatmap_top_n", "value"),
     Input("heatmap_scale", "value"),
     Input("heatmap_annotation_switch", "value"),
     Input("heatmap_cluster_select", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_heatmap_settings(top_n, scale, annotation_switch, cluster, rds_path):
    return _save("heatmap_display", {
        "top_n": top_n,
        "scale": scale,
        "show_annotation": bool(annotation_switch),
        "cluster": cluster,
    }, rds_path)


# ---------------------------------------------------------------------------
# Feature plot
# ---------------------------------------------------------------------------
# intensity_min/max は色スケールのクリップ（cmin/cmax）。強度画像の見え方を
# 直接変えるため、これが不明だと図の解釈が変わる。

@callback(
    _TRIGGER,
    [Input("feature_select", "value"),
     Input("feature_mz_min", "value"),
     Input("feature_mz_max", "value"),
     Input("feature_cluster_filter", "value"),
     Input("feature_filter_mode", "value"),
     Input("feature_intensity_min", "value"),
     Input("feature_intensity_max", "value"),
     Input("feature_colorscale", "value"),
     Input("feature_marker_size", "value"),
     Input("feature_violin_group_by", "value"),
     Input("feature_show_compound_names", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_feature_settings(feature, mz_min, mz_max, cluster_filter, filter_mode,
                          intensity_min, intensity_max, colorscale, marker_size,
                          violin_group_by, show_compound_names, rds_path):
    return _save("feature_display", {
        "feature": feature,
        "mz_min": mz_min,
        "mz_max": mz_max,
        "cluster_filter": cluster_filter,
        "filter_mode": filter_mode,
        "intensity_min": intensity_min,
        "intensity_max": intensity_max,
        "colorscale": colorscale,
        "marker_size": marker_size,
        "violin_group_by": violin_group_by,
        "show_compound_names": bool(show_compound_names),
        "_note": "intensity_min/max clip the colour scale (cmin/cmax), in % of range",
    }, rds_path)


# ---------------------------------------------------------------------------
# on-the-fly DE
# ---------------------------------------------------------------------------
# GUI に出ていない固定値（wilcox / min.pct=0.05 / logfc=0.25 / BH）は
# provenance.ONTHEFLY_DE_FIXED_PARAMS 側で常に添付される。

@callback(
    _TRIGGER,
    [Input("onthefly_de_mode", "value"),
     Input("onthefly_de_target", "value"),
     Input("onthefly_de_fc", "value"),
     Input("onthefly_de_p", "value"),
     Input("onthefly_de_top_n", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_onthefly_de_settings(mode, target, fc, p, top_n, rds_path):
    from app.services.provenance import ONTHEFLY_DE_FIXED_PARAMS
    return _save("onthefly_de", {
        "mode": mode,
        "target_clusters": target,
        "display_fc_threshold": fc,
        "display_p_threshold": p,
        "export_top_n": top_n,
        "fixed_params": dict(ONTHEFLY_DE_FIXED_PARAMS),
    }, rds_path)


# ---------------------------------------------------------------------------
# UMAP / Spatial のビュー設定（既存 umap_display / spatial_display の補集合）
# ---------------------------------------------------------------------------

@callback(
    _TRIGGER,
    [Input("umap_display_mode", "value"),
     Input("umap_highlight_cluster", "value"),
     Input("umap_facet_by", "value"),
     Input("umap_merge_toggle", "value"),
     Input("umap_merge_color_mode", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_umap_view_settings(display_mode, highlight, facet_by,
                            merge_toggle, merge_color_mode, rds_path):
    return _save("umap_view", {
        "display_mode": display_mode,
        "highlight_cluster": highlight,
        "facet_by": facet_by,
        # merged はクラスタ併合後の番号で描画するので、どちらを見せたかは重要
        "merge_toggle": merge_toggle,
        "merge_color_mode": merge_color_mode,
    }, rds_path)


@callback(
    _TRIGGER,
    [Input("interactive_sample", "value"),
     Input("spatial_highlight_cluster", "value"),
     Input("hne_overlay_show", "value"),
     Input("hne_overlay_mono", "value"),
     Input("hne_overlay_opacity", "value"),
     Input("hne_overlay_marker_size", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_spatial_view_settings(sample, highlight, overlay_show, overlay_mono,
                               overlay_opacity, overlay_marker_size, rds_path):
    return _save("spatial_view", {
        "sample": sample,
        "highlight_cluster": highlight,
        "hne_overlay_show": bool(overlay_show),
        "hne_overlay_mono": bool(overlay_mono),
        "hne_overlay_opacity": overlay_opacity,
        "hne_overlay_marker_size": overlay_marker_size,
    }, rds_path)


# ---------------------------------------------------------------------------
# H&E エクスポート条件
# ---------------------------------------------------------------------------
# intensity（linear / counts / data）と unit（compound / m/z）は
# MetaboAnalyst に渡る「濃度」そのものを変える。ZIP を失うと復元できなかった。

@callback(
    _TRIGGER,
    [Input("hne_export_method", "value"),
     Input("hne_export_intensity", "value"),
     Input("hne_export_unit", "value"),
     Input("hne_export_qea", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_hne_export_settings(method, intensity, unit, qea, rds_path):
    return _save("hne_export_options", {
        "method": method,
        "intensity_repr": intensity,
        "unit": unit,
        "include_qea": bool(qea),
        "_note": "intensity_repr changes the exported concentration values",
    }, rds_path)


# ---------------------------------------------------------------------------
# エクスポート条件（Top-N や対象手法は図表の中身を左右する）
# ---------------------------------------------------------------------------

@callback(
    _TRIGGER,
    [Input("input_export_top_n", "value"),
     Input("export_method_selector", "value"),
     Input("export_include_deg", "value"),
     Input("data_export_format", "value"),
     Input("data_export_method_selector", "value"),
     Input("marker_table_top_n", "value")],
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def save_export_settings(report_top_n, report_methods, include_deg,
                         data_format, data_methods, marker_top_n, rds_path):
    return _save("export_options", {
        "report_top_n": report_top_n,
        "report_methods": report_methods,
        "report_include_deg": bool(include_deg),
        "data_format": data_format,
        "data_methods": data_methods,
        "marker_table_top_n": marker_top_n,
    }, rds_path)


# ===========================================================================
# まとめ出力 と Methods 表示（Master Password ゲート）
# ===========================================================================

def _collect(rds_path, result_folder, method, extra=None):
    from app.services.provenance import collect_conditions
    return collect_conditions(rds_path=rds_path, result_folder=result_folder,
                              integration_method=method, extra=extra)


def _is_tier_a() -> bool:
    """サーバ側でのアクセス階層チェック（クライアントの Store を信用しない）。

    Master でログインした利用者は tier A。request context 外（テスト等）では
    True を返し、ゲート判定は verify_master 側に委ねる。
    """
    try:
        from flask import session, has_request_context
        if not has_request_context():
            return True
        return session.get("access_tier") == "A"
    except Exception:
        return True


# ---- まとめ出力ボタン -------------------------------------------------------

@callback(
    Output("div_conditions_status", "children"),
    Input("btn_export_conditions", "n_clicks"),
    [State("seurat_rds_path_store", "data"),
     State("interactive_result_folder", "value"),
     State("interactive_integration_method", "value")],
    prevent_initial_call=True,
)
def export_conditions_bundle(n_clicks, rds_path, result_folder, method):
    """解析条件 + 日英 Methods を <result-dir>/provenance/ に書き出す。"""
    if not n_clicks:
        raise PreventUpdate
    if not rds_path:
        return "⚠ データを読み込んでから実行してください。"

    from app.services.provenance import results_dir_for_rds, write_conditions_bundle
    result_dir = results_dir_for_rds(rds_path, result_folder)
    if result_dir is None:
        return ("⚠ この埋め込みは一時キャッシュ上にあり、結果フォルダに"
                "紐づいていないため書き出せません（PCA(uncorrected) 等）。")

    conditions = _collect(rds_path, result_folder, method)
    paths = write_conditions_bundle(result_dir, conditions)
    written = [p.name for p in paths.values() if p]
    if not written:
        return "⚠ 書き出しに失敗しました。ログを確認してください。"

    n_missing = len(conditions.get("_missing") or [])
    msg = f"✅ {result_dir / 'provenance'} に {', '.join(written)} を書き出しました。"
    if n_missing:
        msg += f" 未記録の項目が {n_missing} 件あります（Methods 末尾に一覧）。"
    return msg


# ---- Methods モーダル: 開閉 -------------------------------------------------

@callback(
    [Output("methods_modal", "is_open"),
     Output("methods_unlock_store", "data", allow_duplicate=True),
     Output("methods_unlock_error", "children", allow_duplicate=True)],
    [Input("btn_show_methods", "n_clicks"),
     Input("btn_methods_close", "n_clicks")],
    State("methods_modal", "is_open"),
    prevent_initial_call=True,
)
def toggle_methods_modal(show_clicks, close_clicks, is_open):
    """開くたびに解錠状態をリセットする（開きっぱなしの解錠を持ち越さない）。"""
    from dash import ctx
    if ctx.triggered_id == "btn_methods_close":
        return False, None, ""
    return True, None, ""


# ---- Methods モーダル: 解錠 -------------------------------------------------

@callback(
    [Output("methods_unlock_store", "data"),
     Output("methods_unlock_error", "children"),
     Output("methods_lock_panel", "style"),
     Output("methods_content_panel", "style"),
     Output("methods_rendered_store", "data"),
     Output("btn_download_methods", "disabled"),
     Output("btn_methods_copy", "disabled"),
     Output("methods_password", "value")],
    Input("btn_methods_unlock", "n_clicks"),
    [State("methods_password", "value"),
     State("seurat_rds_path_store", "data"),
     State("interactive_result_folder", "value"),
     State("interactive_integration_method", "value")],
    prevent_initial_call=True,
)
def unlock_methods(n_clicks, password, rds_path, result_folder, method):
    """Master Password を検証し、通ったときだけ本文を組み立てて返す。

    パスワードは検証に使うだけで、Store にも Output にも残さない
    （最後に methods_password の値を空文字で上書きして入力欄を消す）。
    本文は 4 種（日英 × 平文/表）まとめて 1 つの Store に入れ、表示の切替は
    別コールバックで行う。ロック時は Store を None にするので、
    未解錠のまま本文がブラウザへ渡ることはない。
    """
    if not n_clicks:
        raise PreventUpdate

    def _locked(msg):
        """未解錠のまま返す。本文は一切外へ出さない。"""
        return (None, msg, {"display": "block"}, {"display": "none"},
                None, True, True, "")

    if not password:
        return _locked("パスワードを入力してください。")

    from app.services.auth_service import verify_master
    try:
        ok = verify_master(password)
    except Exception as e:
        logger.warning("Master Password の検証に失敗: %s", e)
        ok = False

    if not ok:
        access_logger.warning("Methods 表示: Master Password 認証に失敗")
        return _locked("Master Password が違います。")

    if not _is_tier_a():
        access_logger.warning("Methods 表示: tier A 以外からの要求を拒否")
        return _locked("この操作の権限がありません。")

    if not rds_path:
        return _locked("データを読み込んでから実行してください。")

    from app.services.methods_text import build_methods_prose, render_methods
    conditions = _collect(rds_path, result_folder, method)
    rendered = {
        "prose": {lang: build_methods_prose(conditions, lang) for lang in ("ja", "en")},
        "table": {lang: render_methods(conditions, lang) for lang in ("ja", "en")},
    }
    access_logger.info("Methods 表示: 解錠 (rds=%s)", rds_path)
    return ({"ok": True, "at": datetime.now().isoformat(timespec="seconds")},
            "", {"display": "none"}, {"display": "block"},
            rendered, False, False, "")


# ---- Methods モーダル: 表示形式の切替 -------------------------------------

@callback(
    [Output("methods_body_ja", "children"),
     Output("methods_body_en", "children"),
     Output("methods_legend", "style")],
    [Input("methods_format", "value"),
     Input("methods_rendered_store", "data")],
    prevent_initial_call=True,
)
def switch_methods_format(fmt, rendered):
    """「論文用（平文）」と「表形式」を切替える。

    平文は Dash コンポーネントで組む（HTML 文字列を作らないので、クラスタ名などの
    ユーザー入力が入っても Dash 側でエスケープされる）。色は CSS クラスで当たる。
    """
    from dash import html as _html

    if not rendered:
        return "", "", {"display": "none"}
    if fmt == "table":
        from dash import dcc as _dcc
        table = rendered.get("table") or {}
        return (_dcc.Markdown(table.get("ja", "")),
                _dcc.Markdown(table.get("en", "")),
                {"display": "none"})

    from app.services.methods_text import prose_to_dash
    prose = rendered.get("prose") or {}
    return (prose_to_dash(prose.get("ja") or [], "ja"),
            prose_to_dash(prose.get("en") or [], "en"),
            {"display": "block"})


# ---- Methods モーダル: 書式つきコピー -------------------------------------
# 画面の DOM をそのままクリップボードへ載せるので、赤／青が Word や Google Docs に
# 貼り付けたときも残る。HTTPS 配信なので navigator.clipboard が使えるが、
# 非セキュアコンテキスト用に execCommand('copy') へフォールバックする。

clientside_callback(
    """
    function(n_clicks, activeTab) {
        if (!n_clicks) { return window.dash_clientside.no_update; }
        const id = (activeTab === "methods_tab_en") ? "methods_body_en" : "methods_body_ja";
        const el = document.getElementById(id);
        if (!el) { return "コピーする本文が見つかりません。"; }
        const html = el.innerHTML;
        const text = el.innerText;
        const fallback = function () {
            // 選択範囲からコピーすると書式が保たれる（HTTP でも動く）
            const sel = window.getSelection();
            const range = document.createRange();
            range.selectNodeContents(el);
            sel.removeAllRanges();
            sel.addRange(range);
            let ok = false;
            try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
            sel.removeAllRanges();
            return ok;
        };
        if (navigator.clipboard && window.ClipboardItem && window.isSecureContext) {
            try {
                navigator.clipboard.write([new ClipboardItem({
                    "text/html": new Blob([html], {type: "text/html"}),
                    "text/plain": new Blob([text], {type: "text/plain"})
                })]);
                return "コピーしました（書式つき）。Word 等に貼り付けてください。";
            } catch (e) { /* 下のフォールバックへ */ }
        }
        return fallback()
            ? "コピーしました（書式つき）。Word 等に貼り付けてください。"
            : "コピーできませんでした。本文を選択して Ctrl+C を使ってください。";
    }
    """,
    Output("methods_copy_status", "children"),
    Input("btn_methods_copy", "n_clicks"),
    State("methods_tabs", "active_tab"),
    prevent_initial_call=True,
)


# ---- Methods モーダル: ダウンロード ----------------------------------------

@callback(
    Output("dl_conditions_bundle", "data"),
    Input("btn_download_methods", "n_clicks"),
    [State("methods_unlock_store", "data"),
     State("seurat_rds_path_store", "data"),
     State("interactive_result_folder", "value"),
     State("interactive_integration_method", "value")],
    prevent_initial_call=True,
)
def download_methods_bundle(n_clicks, unlock, rds_path, result_folder, method):
    """解錠済みのときだけ、条件 JSON + 日英 Methods を ZIP で返す。"""
    if not n_clicks:
        raise PreventUpdate
    if not (unlock or {}).get("ok") or not _is_tier_a():
        access_logger.warning("Methods ダウンロード: 未解錠のまま要求されたので拒否")
        raise PreventUpdate
    if not rds_path:
        raise PreventUpdate

    from app.services.methods_text import (render_methods, render_methods_prose,
                                           render_methods_prose_html)
    from app.services.provenance import (conditions_json_bytes, CONDITIONS_JSON,
                                         results_dir_for_rds, latest_runtime_script)

    conditions = _collect(rds_path, result_folder, method)
    result_dir = results_dir_for_rds(rds_path, result_folder)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(CONDITIONS_JSON, conditions_json_bytes(conditions))
        # 論文用の平文。HTML 版は赤（未記録）と青（復元・要確認）の色が残るので、
        # ブラウザで開いてコピーすれば書式つきで Word へ貼れる。
        for lang in ("ja", "en"):
            zf.writestr(f"METHODS_prose_{lang}.html",
                        render_methods_prose_html(conditions, lang))
            zf.writestr(f"METHODS_prose_{lang}.md",
                        render_methods_prose(conditions, lang))
        # 条件一覧（表形式）。点検用。
        zf.writestr("METHODS_ja.md", render_methods(conditions, "ja"))
        zf.writestr("METHODS_en.md", render_methods(conditions, "en"))
        # 裏付けとなる既存ファイルも同梱する（レシートと実行スクリプト）。
        # これがあれば ZIP 単体で条件を第三者が検証できる。
        if result_dir:
            for name in ("receipt.json", "RECEIPT.md", "analysis_params.json",
                         "analysis_receipt_r.json"):
                p = Path(result_dir) / name
                if p.is_file():
                    zf.write(p, name)
            script = latest_runtime_script(result_dir)
            if script:
                zf.write(script, f"log/{script.name}")
    buf.seek(0)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return dcc.send_bytes(buf.getvalue(), f"analysis_conditions_{ts}.zip")
