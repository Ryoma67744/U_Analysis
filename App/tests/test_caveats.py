"""caveats（pixel-level 注意書きの単一出典）の単体テスト。"""
import pandas as pd

from app.services import caveats as cv


def test_constants_consistent_with_r_template():
    # R テンプレ markers_annotated.csv の inference_note と完全一致させる
    assert cv.RANKING_TYPE == "exploratory_pixel_level"
    assert cv.PIXEL_LEVEL_NOTE_EN == (
        "Exploratory pixel-level ranking; spatial autocorrelation not modeled; "
        "NOT sample-level statistical inference"
    )


def test_banner_text_lang():
    assert "pixel" in cv.banner_text("en")
    assert "探索的" in cv.banner_text("ja")
    assert cv.banner_text("en", short=True) == cv.PIXEL_LEVEL_SHORT_EN


def test_marker_caveat_columns():
    cols = cv.marker_caveat_columns()
    assert cols["ranking_type"] == "exploratory_pixel_level"
    assert "NOT sample-level" in cols["inference_note"]


def test_annotate_dataframe_adds_and_preserves():
    df = pd.DataFrame({"gene": ["a", "b"]})
    out = cv.annotate_dataframe(df)
    assert list(out["ranking_type"]) == ["exploratory_pixel_level"] * 2
    assert out["inference_note"].iloc[0].startswith("Exploratory pixel-level")
    # 既存列は上書きしない
    df2 = pd.DataFrame({"gene": ["a"], "inference_note": ["custom"]})
    out2 = cv.annotate_dataframe(df2)
    assert out2["inference_note"].iloc[0] == "custom"
