"""_apply_feature_annotation_columns（ver45.0）: エクスポート時のサイドカー列名変換。

素 m/z 列 → 埋め込み名（化合物名_<m/z> | …）へのリネーム、非特徴量列の保持、
サイドカー無しでの無変換、冪等性を検証する（本体 parquet は書き換えない）。
"""

import pandas as pd

from app.callbacks.interactive_data_export import _apply_feature_annotation_columns


def _write_sidecar(folder):
    side = pd.DataFrame({
        "mz": [104.1059, 184.0713],
        "raw": [
            "Choline | HMDB | [M+H]+ | 0.34ppm | formula=C5H14NO",
            "Phosphocholine | CE_MS | [M]+ | 0.85ppm | formula=C5H15NO4P",
        ],
    })
    side.to_parquet(str(folder / "SAMPLE_feature_annotations.parquet"), index=False)


def _df():
    return pd.DataFrame({
        "id": [1, 2], "x": [0.0, 1.0], "y": [0.0, 1.0],
        "184.071300": [0.1, 0.2],
        "104.105900": [0.3, 0.4],
        "annotation": ["A", "A"],
        "UMAP cluster": [1, 2],
    })


def test_renames_mz_columns_to_embedded(tmp_path):
    _write_sidecar(tmp_path)
    out = _apply_feature_annotation_columns(_df(), str(tmp_path))
    cols = list(out.columns)
    # m/z 列が化合物名付きの埋め込み名に変わる
    assert any(c.startswith("Phosphocholine_184.0713 |") for c in cols)
    assert any(c.startswith("Choline_104.1059 |") for c in cols)
    # 非特徴量列は不変
    for keep in ("id", "x", "y", "annotation", "UMAP cluster"):
        assert keep in cols


def test_no_sidecar_is_noop(tmp_path):
    before = _df()
    out = _apply_feature_annotation_columns(before.copy(), str(tmp_path))
    assert list(out.columns) == list(before.columns)


def test_idempotent_on_already_embedded(tmp_path):
    _write_sidecar(tmp_path)
    once = _apply_feature_annotation_columns(_df(), str(tmp_path))
    twice = _apply_feature_annotation_columns(once.copy(), str(tmp_path))
    assert list(once.columns) == list(twice.columns)


# ---------------------------------------------------------------------------
# ver51.8: 数字を含む化合物名（このテスト群の従来の穴）
# ---------------------------------------------------------------------------
# 上のケースは "Choline" / "Phosphocholine" という **数字を含まない**化合物名しか
# 使っておらず、m/z 抽出が「文字列中の最初の数字」でも偶然通っていた。
# 同梱 DB は 4,546 化合物中 2,409 件 (53%) が名前に数字を含むので、
# 実データではこちらが主。冪等性の主張はここで初めて意味を持つ。

def _write_sidecar_digit_names(folder):
    side = pd.DataFrame({
        "mz": [760.5851, 61.0648],
        "raw": [
            "PI 38:4 (PI 18:0/20:4) | LIPID MAPS | [M-H]- | 1.10ppm",
            "Propan-2-ol | HMDB | [M+H]+ | 0.42ppm",
        ],
    })
    side.to_parquet(str(folder / "SAMPLE_feature_annotations.parquet"), index=False)


def _df_digit_names():
    return pd.DataFrame({
        "id": [1, 2], "x": [0.0, 1.0], "y": [0.0, 1.0],
        "760.585100": [0.1, 0.2],
        "61.064800": [0.3, 0.4],
        "annotation": ["A", "A"],
    })


def test_renames_digit_bearing_compound_names(tmp_path):
    """★ 化合物名に数字があってもサイドカーと正しく結合されること。"""
    _write_sidecar_digit_names(tmp_path)
    cols = list(_apply_feature_annotation_columns(_df_digit_names(), str(tmp_path)).columns)
    assert any(c.startswith("PI 38:4 (PI 18:0/20:4)_760.5851 |") for c in cols), cols
    assert any(c.startswith("Propan-2-ol_61.0648 |") for c in cols), cols


def test_embedded_name_still_resolves_to_its_mz(tmp_path):
    """★ 埋め込み後の列名から m/z を読み直せること（冪等性の実体）。

    ★ 「列名が 2 回目で変わらない」だけの assert では**不十分**だった。
      旧規則は埋め込み名 `PI 38:4 (...)_760.5851 | …` を 38.0 と読むので
      サイドカーに一致せず、列名は「変換されないまま」据え置かれる。
      つまり列名比較は「正しく再解決できた」場合と「解決に失敗して放置した」
      場合を区別できず、壊れていても通ってしまう。

      本当に確かめたいのは `_apply_feature_annotation_columns` の docstring が
      主張している「列名が既に埋め込み済みでも m/z を再抽出して同名に解決する」
      なので、**出力列名から m/z が復元できること**を直接見る。
    """
    from app.utils.deg_utils import extract_mz_numeric

    _write_sidecar_digit_names(tmp_path)
    once = _apply_feature_annotation_columns(_df_digit_names(), str(tmp_path))

    embedded = [c for c in once.columns if "_" in c and "|" in c]
    assert len(embedded) == 2, once.columns

    # 列名の先頭（化合物名）で期待 m/z を引く。並べ替え順に依存させない。
    expected_by_prefix = {"PI 38:4": 760.5851, "Propan-2-ol": 61.0648}
    for col in embedded:
        expected = next(v for k, v in expected_by_prefix.items() if col.startswith(k))
        got = extract_mz_numeric(col)
        assert abs(got - expected) < 1e-6, f"{col!r} -> {got} (期待 {expected})"

    # 2 回目も同じ列名（解決に成功したうえでの冪等性）
    twice = _apply_feature_annotation_columns(once.copy(), str(tmp_path))
    assert list(once.columns) == list(twice.columns)


# ---------------------------------------------------------------------------
# ver51.8: 生ファイル名 → Sample 名の対応付け
# ---------------------------------------------------------------------------
# ★ 部分一致で「最初に見つかったもの」を返していた。返り値は
#   `key = (matched_sample, x, y)` の第 1 要素になり、TIMS のようにサンプル間で
#   座標グリッドが共通だと **別サンプルのクラスタ名・ROI 名がエクスポートに
#   書き出される**。行ごとに、警告も無く。

class TestMatchSampleName:
    def _f(self):
        from app.callbacks.interactive_data_export import _match_sample_name
        return _match_sample_name

    def test_exact_match_wins(self):
        assert self._f()("brain_A", ["brain_A", "brain_B"]) == "brain_A"

    def test_unique_partial_match_is_used(self):
        """一意なら従来どおり部分一致で拾う（過剰な締め付けの番人）。"""
        assert self._f()("brain", ["brain_A", "liver_B"]) == "brain_A"

    def test_ambiguous_partial_match_returns_none(self):
        """★ 複数候補に当たるときは選ばない。

        旧実装は並び順だけの理由で brain_A を返していた。
        """
        assert self._f()("brain", ["brain_A", "brain_B"]) is None

    def test_no_match_returns_none(self):
        assert self._f()("kidney", ["brain_A", "brain_B"]) is None
