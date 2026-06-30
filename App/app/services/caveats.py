# =============================================================================
# MSI Analysis Application - 統計の解釈に関する注意書き（単一の出典）
# =============================================================================
# pixel 単位の検定は「探索的ランキング」であり、サンプル間・群間差の最終的な
# 統計推論ではない。この注意書きを UI / PPTX / データ出力 / 共有画面で一貫して
# 表示するため、文言をここ 1 箇所に集約する（TIMS R テンプレの既存文言と一致）。
#
# 依存なし（標準ライブラリのみ）。Dash 部品はこの定数を使って各画面で組み立てる。
# =============================================================================

from __future__ import annotations

RANKING_TYPE = "exploratory_pixel_level"

# R テンプレ（markers_annotated.csv の inference_note 列）と完全一致させる英文。
PIXEL_LEVEL_NOTE_EN = (
    "Exploratory pixel-level ranking; spatial autocorrelation not modeled; "
    "NOT sample-level statistical inference"
)

PIXEL_LEVEL_NOTE_JA = (
    "このp値はpixel単位の探索的ランキングです。隣接ピクセルは独立ではないため、"
    "サンプル間・群間差の最終的な統計推論ではありません"
    "（空間自己相関は未補正）。群間主張にはサンプル/ROI集約（pseudobulk）をご利用ください。"
)

# 共有・軽量ビュー用の短い一行（バッジ/フッター向け）。
PIXEL_LEVEL_SHORT_JA = "探索的pixel-levelランキング（群間の統計推論ではありません）"
PIXEL_LEVEL_SHORT_EN = "Exploratory pixel-level ranking (not sample-level inference)"


def banner_text(lang: str = "ja", short: bool = False) -> str:
    """注意書き本文を返す。"""
    if lang.lower().startswith("en"):
        return PIXEL_LEVEL_SHORT_EN if short else PIXEL_LEVEL_NOTE_EN
    return PIXEL_LEVEL_SHORT_JA if short else PIXEL_LEVEL_NOTE_JA


def marker_caveat_columns() -> dict:
    """マーカー表/エクスポートに付与する 2 列（R 側 markers_annotated.csv と同一）。"""
    return {"ranking_type": RANKING_TYPE, "inference_note": PIXEL_LEVEL_NOTE_EN}


def annotate_dataframe(df, ranking_type_col: str = "ranking_type",
                       note_col: str = "inference_note"):
    """pandas DataFrame に注意書き 2 列を付与して返す（既存列があれば上書きしない）。"""
    out = df.copy()
    if ranking_type_col not in out.columns:
        out[ranking_type_col] = RANKING_TYPE
    if note_col not in out.columns:
        out[note_col] = PIXEL_LEVEL_NOTE_EN
    return out
