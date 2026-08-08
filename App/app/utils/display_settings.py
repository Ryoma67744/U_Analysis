# =============================================================================
# MSI Analysis Application - 表示設定の取り出し口を 1 つにする (ver52.3 ⑥)
#
# 同じ図を描く経路が 4 系統ある（画面 / PPTX / Lite ビュー / 共有）。
# うち 3 系統は **同じ `interactive_settings.json`** を出典にしているのに、
# 読み出すコードが別々だった。結果:
#
#   - ver51.9 B-2 で PPTX の閾値無視を直したが、Lite と共有は 0.5 / 1.3 のまま
#   - Lite の Heatmap は Top-N 既定 3、画面は 5（既定値どうしが食い違う）
#
# 「片方だけ直す」が起きるのは、読み出し口が経路の数だけあるから。
# ここに 1 つ置いて全経路から呼ぶ。Dash 非依存・副作用なしの純ロジック。
# =============================================================================

from __future__ import annotations

from app.utils.validation import param_default

__all__ = ["DISPLAY_DEFAULTS", "read_display_settings"]

# 既定値の宣言は 1 箇所に置く。既定が 2 つあると、それ自体が
# 「同じ設定なのに経路で答えが違う」の温床になる (ver52.3 ⑤ の tolerance_mz)。
#
# ★ `volcano_fc` / `volcano_p` / `heatmap_top_n` は画面の入力欄と同じ既定なので
#   `PARAM_BOUNDS` から引く。ここに数値を書き写すと出典が 2 つに戻る。
DISPLAY_DEFAULTS = {
    "volcano_fc": param_default("volcano_fc_threshold", 0.5),
    "volcano_p": param_default("volcano_p_threshold", 1.3),
    "volcano_top_n": None,          # None = ラベル件数は経路ごとの従来動作
    "heatmap_top_n": param_default("heatmap_top_n", 5),
    "heatmap_scale": None,          # None = 従来の自動判定 (アプリ図の zmid を見る)
    "feature_intensity_min": None,  # None = データ全域
    "feature_intensity_max": None,
}


def _num(value, default):
    """数値として読めるときだけ採る。読めなければ既定へ落とす。

    設定ファイルが壊れていても長時間のエクスポートを落とさない。
    """
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_or(value, default):
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def read_display_settings(interactive) -> dict:
    """`interactive_settings.json` の内容から、図の生成に要る表示設定を取り出す。

    Args:
        interactive: `interactive_settings.json` を読んだ dict。
            PPTX は `provenance.collect_conditions()` 経由なので
            `conditions["interactive"]` を渡す。Lite はファイルを直接読むので
            その dict をそのまま渡す。**同じ中身の別の入り口**なので、
            ここでは 1 段の dict として受ける。

    Returns:
        dict — 経路をまたいで同じキーを持つ表示設定。

    ★ `heatmap_top_n` は「ヒートマップに 1 クラスタ何遺伝子出すか」。
      PPTX がスライド生成に使う `export_top_n_store`（資料に載せる件数）とは
      **同名ではないが紛らわしい別物**なので、混ぜないこと。
    """
    inter = interactive or {}
    vol = inter.get("volcano_display") or {}
    hm = inter.get("heatmap_display") or {}
    feat = inter.get("feature_display") or {}
    umap_view = inter.get("umap_view") or {}

    top_n = vol.get("label_top_n")
    try:
        top_n = int(top_n) if top_n is not None else None
    except (TypeError, ValueError):
        top_n = None

    scale = hm.get("scale")
    if scale is not None and not isinstance(scale, str):
        scale = None

    return {
        "volcano_fc": _num(vol.get("fc_threshold"),
                           DISPLAY_DEFAULTS["volcano_fc"]),
        "volcano_p": _num(vol.get("p_threshold"),
                          DISPLAY_DEFAULTS["volcano_p"]),
        "volcano_top_n": top_n,
        "heatmap_top_n": _int_or(hm.get("top_n"),
                                 DISPLAY_DEFAULTS["heatmap_top_n"]),
        "heatmap_scale": scale,
        "feature_intensity_min": _num(feat.get("intensity_min"), None),
        "feature_intensity_max": _num(feat.get("intensity_max"), None),
        # ver51.9 / B-7: マージ表示。画面は Cluster_merged で描くのに
        # PPTX には参照が 1 つも無く、**別のクラスタリングの資料**が出ていた。
        "merge_toggle": umap_view.get("merge_toggle"),
        "merge_color_mode": umap_view.get("merge_color_mode") or "shade",
    }
