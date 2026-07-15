"""Tests for app.utils.annotation_label (feature → 表示ラベル解決の純関数)。

Dash 非依存。純コア feature_display_label と、deg_utils.backfill_annotations を検証する。
"""

from app.utils.annotation_label import feature_display_label
from app.utils.deg_utils import backfill_annotations


FEAT = "760.5851"
FA = {FEAT: {"display_name": "PI 38:4_760.5851", "compound": "PI 38:4"}}


# ---- ソース優先順位 ----

class TestPrecedence:
    def test_annotation_map_wins_over_feature_annotations(self):
        out = feature_display_label(
            FEAT, style="paren",
            annotation_map={FEAT: "FromMap"},
            feature_annotations={FEAT: {"compound": "FromFA"}},
            deg_annotation="FromDeg",
        )
        assert out == f"{FEAT} (FromMap)"

    def test_feature_annotations_over_deg(self):
        out = feature_display_label(
            FEAT, style="paren",
            feature_annotations={FEAT: {"compound": "FromFA"}},
            deg_annotation="FromDeg",
        )
        assert out == f"{FEAT} (FromFA)"

    def test_deg_used_when_only_source(self):
        out = feature_display_label(FEAT, style="paren", deg_annotation="Glucose")
        assert out == f"{FEAT} (Glucose)"

    def test_numeric_only_map_value_falls_through(self):
        # annotation_map の値が数値のみ（無意味）なら次のソースへ
        out = feature_display_label(
            FEAT, style="paren",
            annotation_map={FEAT: "240.984"},
            deg_annotation="Alanine",
        )
        assert out == f"{FEAT} (Alanine)"

    def test_compound_equal_to_feature_rejected(self):
        out = feature_display_label(
            FEAT, style="paren", annotation_map={FEAT: FEAT})
        assert out == FEAT


# ---- style="heading" ----

class TestHeading:
    def test_display_name_preferred(self):
        out = feature_display_label(FEAT, style="heading", feature_annotations=FA)
        assert out == "PI 38:4_760.5851"

    def test_annotation_map_only_falls_back_to_paren_form(self):
        # A2 退行ロック: display_name が無く annotation_map にだけ化合物名がある場合、
        # 見出しは "feat  (compound)" になる（従来は annotation_map を無視して m/z のままだった）。
        out = feature_display_label(
            FEAT, style="heading", annotation_map={FEAT: "PI 38:4"})
        assert out == f"{FEAT}  (PI 38:4)"

    def test_show_compound_false_gives_bare_feat(self):
        out = feature_display_label(
            FEAT, style="heading", feature_annotations=FA, show_compound=False)
        assert out == FEAT

    def test_no_annotation_gives_bare_feat(self):
        out = feature_display_label(FEAT, style="heading")
        assert out == FEAT


# ---- style="paren" ----

class TestParen:
    def test_bare_when_no_compound(self):
        assert feature_display_label(FEAT, style="paren") == FEAT

    def test_with_compound(self):
        out = feature_display_label(
            FEAT, style="paren", annotation_map={FEAT: "Glucose"})
        assert out == f"{FEAT} (Glucose)"


# ---- style="compound" ----

class TestCompound:
    def test_compound_only(self):
        out = feature_display_label(
            FEAT, style="compound", annotation_map={FEAT: "Glucose"})
        assert out == "Glucose"

    def test_falls_back_to_feat(self):
        assert feature_display_label(FEAT, style="compound") == FEAT


# ---- style="filename" ----

class TestFilename:
    def test_uses_display_name_sanitized(self):
        # "PI 38:4_760.5851" → 空白/':' を除去。パス区切りを含まない。
        feat = "m/z 760.58510"
        fa = {feat: {"display_name": "PI 38:4_760.5851", "compound": "PI 38:4"}}
        out = feature_display_label(feat, style="filename", feature_annotations=fa)
        assert "/" not in out and "\\" not in out
        assert ":" not in out and " " not in out and "|" not in out
        assert out == "PI_38_4_760.5851"

    def test_compound_only_builds_compound_mz(self):
        feat = "m/z 300.20"
        out = feature_display_label(
            feat, style="filename", annotation_map={feat: "Alanine"})
        assert "/" not in out and " " not in out
        assert out.startswith("Alanine_300")

    def test_bare_feature_sanitized(self):
        feat = "m/z 300.20"
        out = feature_display_label(feat, style="filename")
        assert "/" not in out and " " not in out
        assert out == "m_z_300.20"


# ---- 端条件 ----

class TestEdge:
    def test_all_none_returns_bare_feature(self):
        assert feature_display_label(FEAT) == FEAT

    def test_none_feature(self):
        assert feature_display_label(None) == ""

    def test_auto_picks_heading_with_display_name(self):
        out = feature_display_label(FEAT, style="auto", feature_annotations=FA)
        assert out == "PI 38:4_760.5851"

    def test_auto_picks_paren_without_display_name(self):
        out = feature_display_label(
            FEAT, style="auto", annotation_map={FEAT: "Glucose"})
        assert out == f"{FEAT} (Glucose)"

    def test_show_compound_false_collapses_all_styles(self):
        for style in ("heading", "paren", "compound", "filename", "auto"):
            assert feature_display_label(
                FEAT, style=style, feature_annotations=FA,
                show_compound=False) == FEAT


# ---- backfill_annotations ----

class TestBackfill:
    def _deg(self):
        return [
            {"gene": "100.5", "cluster": "0", "annotation": ""},
            {"gene": "200.1", "cluster": "0", "annotation": "Glucose"},  # 既存・意味あり
            {"gene": "300.2", "cluster": "1", "annotation": "300.2"},    # 数値のみ→補完対象
        ]

    def test_fills_empty_from_map(self):
        deg = self._deg()
        backfill_annotations(deg, {"100.5": "Alanine", "300.2": "ATP"})
        by = {r["gene"]: r["annotation"] for r in deg}
        assert by["100.5"] == "Alanine"
        assert by["300.2"] == "ATP"

    def test_preserves_meaningful_existing(self):
        deg = self._deg()
        backfill_annotations(deg, {"200.1": "SHOULD_NOT_OVERWRITE"})
        by = {r["gene"]: r["annotation"] for r in deg}
        assert by["200.1"] == "Glucose"

    def test_none_deg_is_noop(self):
        assert backfill_annotations(None, {"100.5": "Alanine"}) is None

    def test_empty_map_is_noop(self):
        deg = self._deg()
        assert backfill_annotations(deg, {}) is deg
        assert deg[0]["annotation"] == ""

    def test_numeric_map_value_not_applied(self):
        deg = [{"gene": "100.5", "cluster": "0", "annotation": ""}]
        backfill_annotations(deg, {"100.5": "100.51"})  # 数値のみ→無意味
        assert deg[0]["annotation"] == ""

    def test_returns_same_object(self):
        deg = self._deg()
        assert backfill_annotations(deg, {"100.5": "Alanine"}) is deg
