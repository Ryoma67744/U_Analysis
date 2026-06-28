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
    "heatmap_top_n": (1, 100, 5, "Top N"),
    # アプリ内 DE（選択DE）表示閾値
    "onthefly_de_fc": (0.0, None, 0.5, "FC 閾値"),
    "onthefly_de_p": (0.0, None, 1.3, "-log10(p) 閾値"),
    # Feature 強度レンジ (%)
    "feature_intensity_min": (0.0, 100.0, None, "強度 最小値(%)"),
    "feature_intensity_max": (0.0, 100.0, None, "強度 最大値(%)"),
    # 埋め込み/再解析パラメータ (Loupe 既定参照。将来の再解析UIで使用)
    "umap_min_dist": (0.0, 1.0, 0.1, "UMAP 最小距離"),
    "umap_n_neighbors": (2, None, 15, "近傍数"),
    "perplexity": (1, None, 30, "perplexity"),
    "pca_dims": (10, 100, None, "主成分数"),
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
