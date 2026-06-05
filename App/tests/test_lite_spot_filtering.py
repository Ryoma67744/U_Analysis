"""簡易ビューアー(/lite)の Otsu スポット除去 QC 画像セクションのテスト（ver4.18）。

`_build_spot_filtering_section` は結果フォルダ直下の `spot_filtering_*.png` を base64 で
埋め込む節を返し、画像が無ければ None（節を出さない）ことを検証する。
"""

from app.callbacks.lite_view_callbacks import _build_spot_filtering_section


# base64 エンコードできれば良い（描画はしないため中身は任意のバイト列）
_PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"fake-png-content-for-test"


def test_returns_none_without_result_dir():
    assert _build_spot_filtering_section("") is None
    assert _build_spot_filtering_section(None) is None


def test_returns_none_when_dir_missing(tmp_path):
    assert _build_spot_filtering_section(str(tmp_path / "does_not_exist")) is None


def test_returns_none_when_no_filtering_png(tmp_path):
    # スポット除去とは無関係な png のみ → 節を出さない
    (tmp_path / "umap_cluster.png").write_bytes(_PNG_BYTES)
    assert _build_spot_filtering_section(str(tmp_path)) is None


def test_builds_section_with_spot_filtering_png(tmp_path):
    (tmp_path / "spot_filtering_S1_otsu.png").write_bytes(_PNG_BYTES)
    (tmp_path / "spot_filtering_S2_otsu.png").write_bytes(_PNG_BYTES)
    sec = _build_spot_filtering_section(str(tmp_path))
    assert sec is not None
    s = str(sec)
    # 両ファイル名がキャプションに含まれ、base64 データ URI が埋め込まれている
    assert "spot_filtering_S1_otsu.png" in s
    assert "spot_filtering_S2_otsu.png" in s
    assert "data:image/png;base64," in s
