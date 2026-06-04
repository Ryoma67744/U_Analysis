"""データ出力 (interactive_data_export) の回帰テスト。

ver4.9 修正:
  DESIプロジェクトが metadata 未設定で TIMS 経路に入り、「生データ＋クラスター番号」でなく
  指標ファイルが出力される問題。_resolve_instrument がパスから DESI を判定し、
  _export_desi に正しくルーティングされることを担保する。
"""

import io
from collections import OrderedDict

import pandas as pd

import app.callbacks.interactive_data_export as de


# ---------------------------------------------------------------------------
# _resolve_instrument
# ---------------------------------------------------------------------------

def test_resolve_instrument_explicit_desi_wins():
    assert de._resolve_instrument("DESI", "/app/Data/TIMS/Data/x") == "DESI"


def test_resolve_instrument_path_desi_overrides_defaulted_tims():
    # metadata が既定の "TIMS" でも、パスが /DESI/ なら DESI に補正（本バグの本丸）
    assert de._resolve_instrument("TIMS", "/app/Data/DESI/Data/proj/run") == "DESI"
    assert de._resolve_instrument("", "/app/Data/DESI/Data/proj") == "DESI"


def test_resolve_instrument_path_tims():
    assert de._resolve_instrument("", "/app/Data/TIMS/Data/x") == "TIMS"
    assert de._resolve_instrument("TIMS", "/app/Data/TIMS/Data/x") == "TIMS"


def test_resolve_instrument_default_tims_when_unknown():
    assert de._resolve_instrument("", "/some/other/path") == "TIMS"
    assert de._resolve_instrument(None) == "TIMS"


# ---------------------------------------------------------------------------
# _export_desi: 元 .txt にサンプル別シート + UMAP cluster 列を付与
# ---------------------------------------------------------------------------

def test_export_desi_appends_cluster_column(tmp_path):
    # DESI .txt（先頭5行ヘッダ + データ行: col1=x, col2=y）
    txt = (
        "h0a\th0b\n"
        "h1a\th1b\n"
        "h2a\th2b\n"
        "h3a\th3b\n"
        "h4a\th4b\n"
        "px1\t10.0\t20.0\t0.5\n"
        "px2\t11.0\t21.0\t0.6\n"
        "px3\t99.0\t99.0\t0.7\n"
    )
    (tmp_path / "S1.txt").write_text(txt, encoding="utf-8")

    # 単一手法のクラスタールックアップ: (sample, x, y) -> cluster番号
    lookups = OrderedDict([
        ("Harmony", {
            ("S1", 10.0, 20.0): "3",
            ("S1", 11.0, 21.0): "5",
        }),
    ])

    excel_bytes, filename = de._export_desi(str(tmp_path), lookups)
    assert filename == "UMAP_cluster_DESI.xlsx"

    df = pd.read_excel(io.BytesIO(excel_bytes), sheet_name="S1", header=None)
    # "UMAP cluster" 列を特定
    header_row = list(df.iloc[0])
    col = header_row.index("UMAP cluster")

    def _norm(v):
        s = str(v)
        return s[:-2] if s.endswith(".0") else s

    got = {
        _norm(df.iloc[r, col])
        for r in range(5, len(df))
        if str(df.iloc[r, col]) not in ("nan", "")
    }
    # 座標一致した px1->3, px2->5 が付与され、未一致 px3 は空
    assert got == {"3", "5"}
