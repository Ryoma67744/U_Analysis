# =============================================================================
# MSI Analysis Application - Input validation (Loupe 参考: 範囲チェック+インライン警告)
# 数値入力の有効範囲を一元管理し、範囲外を即座に弾く純ロジック。
# Dash 非依存・副作用なしで単体テスト可能。Loupe の既定/範囲を参照値に採用。
# =============================================================================

from __future__ import annotations

__all__ = ["check_range", "validate_param", "PARAM_BOUNDS",
           "BOUNDS_INTENTIONALLY_ABSENT", "param_default", "coerce_number",
           "coerce_count"]

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

    # =====================================================================
    # ver52.3 ⑤: 画面の数値入力 28 個のうち **20 個が無検証**だった分を結線する。
    #
    # ★ 範囲は発明していない。**レイアウトの `min=` / `max=` / `value=` を正**とし、
    #   ここはその鏡写しにする。20 個のうち 18 個はレイアウトに境界が既に
    #   書かれていて、`PARAM_BOUNDS` に転記されていないだけだった。
    #   （番人 `test_bounds_agree_with_the_layout` が食い違いを検出する）
    #
    # ★ 0 を殺さないこと。`mz_align_ppm=0` は「アライメント無効」、
    #   `volcano_label_top_n=0` は「ラベルを出さない」という**正式な指定**で、
    #   どちらもレイアウトが `min=0` と宣言している。
    # =====================================================================
    # --- DEG の統計閾値（通常解析 / 再解析）。preflight_validation と同一 ---
    "p_thresh": (0, 1, 0.05, "p値閾値"),
    "logfc_thresh": (0, None, 0.25, "log2FC閾値"),
    "reanalysis_p_thresh": (0, 1, 0.05, "再解析 p値閾値"),
    "reanalysis_logfc_thresh": (0, None, 0.25, "再解析 log2FC閾値"),
    # --- m/z 照合許容差。誤った化合物同定に直結する ---
    "tolerance_mz": (0, None, 0.01, "m/z許容誤差"),
    "reanalysis_tolerance_mz": (0, None, 0.01, "再解析 m/z許容誤差"),
    "reann_tolerance": (0, None, 0.01, "再アノテーション許容差"),
    # --- m/z アライメント。★ 0 = 無効（analysis_runner:541 が falsy で注入を飛ばし、
    #     R 側も `if (MZ_ALIGN_PPM > 0 …)`、methods_text も 0 のとき別の文を出す）---
    "mz_align_ppm": (0, 500, 0, "m/z アライメント (ppm)"),
    # --- キャリブレーション。設定タブと対話タブは同じ次数クランプを通るので
    #     境界も揃える（従来は対話側だけ上限が無かった）---
    "calibration_search_window": (0.01, 2.0, 0.5, "検索ウィンドウ (Da)"),
    "calibration_min_peaks": (1, 10, 2, "最低マッチピーク数"),
    "int_cal_search_window": (0.01, 2.0, 0.5, "検索ウィンドウ (Da)"),
    "int_cal_min_peaks": (1, 10, 2, "最低マッチピーク数"),
    # --- 表示件数。API 側で直した `top` と同じ型が画面に残っていた ---
    "volcano_label_top_n": (0, 50, 5, "ラベル Top-N"),
    "input_export_top_n": (1, 20, 5, "エクスポート Top-N"),
    # --- 変換設定 ---
    "scils_spot_block": (10, 10000, 200, "スポットブロック数"),
}

# ★ ver52.3 ⑤: 意図的に範囲を作らなかった入力と、その理由。
#   「根拠が無いので広めに入れておく」は、検証しているように見えて
#   実際には何も弾かない——`PARAM_BOUNDS` の死んだ 4 定義（ver52.3 ②）と同じ形。
#   証拠が無いなら**書かない**でここに理由を残す。
BOUNDS_INTENTIONALLY_ABSENT = {
    "feature_mz_min":
        "レイアウト (interactive_tab.py:1319) に value も min も max も無く、"
        "クランプも定数もリポジトリのどこにも無い。空欄が「m/z で絞らない」という"
        "正当な状態 (interactive_deg.py が `is not None` で判定) なので、"
        "根拠の無い範囲を発明しない。"
        "★ `mz_min <= mz_max` の相互検証は id 単位の PARAM_BOUNDS では表現できない。"
        "必要なら別コールバックだが、同じ穴が feature_intensity_min/max にもあるので"
        "やるなら両方まとめて（ver52.4 以降）",
    "feature_mz_max": "同上",
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


def param_default(param_id, fallback=None):
    """`PARAM_BOUNDS` に宣言された既定値を返す（未知 id / 既定なしは fallback）。

    ★ ver52.3 ⑤: `x = x or 0.01` の置き換え先。既定値をコールバック側に
      直接書くと、**同じ入力に別々の既定値**が生まれる。実際 `tolerance_mz` は
      `interactive_callbacks:955` が 0.1、`:1119` が 0.01 を使っていて、
      画面の宣言（0.01）ともずれていた。既定値の出典を 1 つにする。
    """
    spec = PARAM_BOUNDS.get(param_id)
    if not spec or spec[2] is None:
        return fallback
    return spec[2]


def coerce_number(value, param_id, fallback=None):
    """空欄なら宣言された既定値、そうでなければ float 化して返す。

    ★ ver52.3 ⑤: `float(x or DEFAULT)` は **0 を既定値に化けさせる**
      （0 は falsy）。`mz_align_ppm=0`（アライメント無効）や
      `volcano_label_top_n=0`（ラベル無し）は正当な指定なので、
      「未入力」と「0」を区別する。数値化できない値も既定へ倒す
      （欄は `validate_param` で赤くなる）。
    """
    if value is None or value == "":
        return param_default(param_id, fallback)
    try:
        return float(value)
    except (TypeError, ValueError):
        return param_default(param_id, fallback)


def coerce_count(value, param_id, fallback=None):
    """件数系の入力を **0 を殺さずに** int へ丸める。

    ★ ver52.3 ⑤: `volcano_label_top_n = 0`（ラベルを出さない）を、
      画面と資料が **逆に解釈していた**:

        interactive_deg.py    int(label_top_n or 5)                     → 0 が 5 になりラベルが出る
        interactive_pptx.py   5 if v is None else max(0, int(v))        → 0 のままラベルが出ない

      レイアウトは `min=0` なので 0 は正当な入力で、PPTX 側が正しかった
      （ver51.9 B-2 で PPTX だけ直した取りこぼし）。
      ★ 直し方として「同じ式を画面側にも書く」は**採らない**。
        同じ判断が 2 箇所にあること自体が、この食い違いを生んだ原因なので、
        式をここ 1 つにして両方から呼ぶ。
    """
    v = coerce_number(value, param_id, fallback)
    if v is None:
        return None
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return fallback


def validate_param(param_id, value):
    """PARAM_BOUNDS の定義に基づき入力を検証して (ok, msg) を返す。

    未知の param_id は常に ok（検証対象外）。
    """
    spec = PARAM_BOUNDS.get(param_id)
    if not spec:
        return (True, "")
    lo, hi, _default, label = spec
    return check_range(value, lo, hi, allow_blank=True, name=label)
