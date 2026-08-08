# =============================================================================
# MSI Analysis Application - Input validation (Loupe 参考: 範囲チェック+インライン警告)
# 数値入力の有効範囲を一元管理し、範囲外を即座に弾く純ロジック。
# Dash 非依存・副作用なしで単体テスト可能。Loupe の既定/範囲を参照値に採用。
# =============================================================================

from __future__ import annotations

__all__ = ["check_range", "validate_param", "PARAM_BOUNDS"]

# param_id -> (lo, hi, default, label)。lo/hi が None は無制限。
PARAM_BOUNDS = {
    # Volcano / DEG 表示閾値
    "volcano_fc_threshold": (0.0, None, 0.5, "FC 閾値"),
    "volcano_p_threshold": (0.0, None, 1.3, "-log10(p) 閾値"),
    "volcano_y_max": (0.0, None, None, "Y軸上限"),
    # ★ ver52.3: 上限が画面 (interactive_tab.py:1658 の max=20) と食い違って
    #   100 になっていた。検証が画面より緩いので、21〜100 を入れても欄は
    #   赤くならないのに画面の宣言には反する。レイアウトを正として揃えた。
    "heatmap_top_n": (1, 20, 5, "Top N"),
    # アプリ内 DE（選択DE）表示閾値
    "onthefly_de_fc": (0.0, None, 0.5, "FC 閾値"),
    "onthefly_de_p": (0.0, None, 1.3, "-log10(p) 閾値"),
    # Feature 強度レンジ (%)
    "feature_intensity_min": (0.0, 100.0, None, "強度 最小値(%)"),
    "feature_intensity_max": (0.0, 100.0, None, "強度 最大値(%)"),
    # 埋め込みパラメータ (設定タブの「UMAP 条件」)。
    # ★ ver52.3: キー名が画面の id と違い、**一度も適用されていなかった**。
    #   `validate_param` は未知の id を常に ok として通すので、書いたのに
    #   何も起きず、エラーも出ない（監査 R-01 と同じ「宣言した対象が実在しない」型）。
    #     旧 "umap_min_dist"     → 実 id "umap_min_dist_input"
    #     旧 "umap_n_neighbors"  → 実 id "umap_n_neighbors_input"
    #     旧 "pca_dims"          → 実 id "umap_dims_input"
    #     旧 "perplexity"        → 対応する入力が画面に無いので削除
    #
    #   ★ 範囲も既定値もレイアウト（および R テンプレ）と食い違っていたので、
    #     キー名だけ直すと今度は正当な入力を弾く。中身も実際に合わせた:
    #       min_dist    旧既定 0.1  → 0.3   (settings_tab:1014 / R の UMAP_MIN_DIST)
    #       n_neighbors 旧既定 15   → 30    (settings_tab:1004 / R の UMAP_N_NEIGHBORS <- 30L)
    #                   旧上限 なし → 100   (settings_tab:1004 の max)
    #       dims        旧範囲 10-100 → 2-50 (settings_tab:1035。lo=10 の根拠は無かった)
    "umap_min_dist_input": (0.0, 1.0, 0.3, "UMAP 最小距離"),
    "umap_n_neighbors_input": (2, 100, 30, "近傍数"),
    "umap_dims_input": (2, 50, 30, "次元数"),
}


def _fmt(n):
    """境界値の表示用整形（整数は .0 を出さない）。"""
    try:
        f = float(n)
    except (TypeError, ValueError):
        return str(n)
    return str(int(f)) if f.is_integer() else str(n)


def check_range(value, lo=None, hi=None, *, allow_blank=True, name="値"):
    """value が [lo, hi] に収まるか判定して (ok: bool, msg: str) を返す。

    空欄は allow_blank=True なら許容。数値化できなければエラー。
    """
    if value is None or value == "":
        return (True, "") if allow_blank else (False, f"{name}を入力してください")
    try:
        v = float(value)
    except (TypeError, ValueError):
        return (False, f"{name}は数値で入力してください")
    if lo is not None and v < lo:
        return (False, f"{name}は {_fmt(lo)} 以上にしてください")
    if hi is not None and v > hi:
        return (False, f"{name}は {_fmt(hi)} 以下にしてください")
    return (True, "")


def validate_param(param_id, value):
    """PARAM_BOUNDS の定義に基づき入力を検証して (ok, msg) を返す。

    未知の param_id は常に ok（検証対象外）。
    """
    spec = PARAM_BOUNDS.get(param_id)
    if not spec:
        return (True, "")
    lo, hi, _default, label = spec
    return check_range(value, lo, hi, allow_blank=True, name=label)
