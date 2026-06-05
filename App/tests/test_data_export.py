"""データ出力 (interactive_data_export) の回帰テスト。

ver4.9 修正:
  DESIプロジェクトが metadata 未設定で TIMS 経路に入り、「生データ＋クラスター番号」でなく
  指標ファイルが出力される問題。_resolve_instrument がパスから DESI を判定し、
  _export_desi に正しくルーティングされることを担保する。
"""

import io
from collections import OrderedDict

from pathlib import Path

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


# ---------------------------------------------------------------------------
# プロジェクトスコープ（別プロジェクト混入の防止）
# ---------------------------------------------------------------------------

def _setup_data_root(tmp_path, monkeypatch):
    """tmp に DESI データルートを作り、config の候補をそこへ向ける。"""
    data_root = tmp_path / "DESI" / "Data"
    data_root.mkdir(parents=True)
    monkeypatch.setattr("app.config.DESI_DATA_CANDIDATES", [data_root])
    monkeypatch.setattr("app.config.TIMS_DATA_CANDIDATES", [])
    monkeypatch.setattr("app.config.OUTPUT_DATA_CANDIDATES", [])
    return data_root


def test_is_within():
    base = Path("/a/b")
    assert de._is_within(Path("/a/b"), base)
    assert de._is_within(Path("/a/b/c"), base)
    assert not de._is_within(Path("/a/x"), base)


def test_project_root_for(tmp_path, monkeypatch):
    data_root = _setup_data_root(tmp_path, monkeypatch)
    proj = data_root / "ProjA"
    run = proj / "umap_run"
    run.mkdir(parents=True)
    got = de._project_root_for(run)
    assert got is not None and got.resolve() == proj.resolve()
    # 既知のデータ/出力ルート配下でないパス → None
    assert de._project_root_for(tmp_path / "elsewhere") is None


def test_infer_data_folder_does_not_cross_into_other_project(tmp_path, monkeypatch):
    data_root = _setup_data_root(tmp_path, monkeypatch)
    # ProjA: 結果フォルダのみ（自前の .txt データは無い）
    projA = data_root / "ProjA_Embryo"
    run = projA / "umap_run"
    run.mkdir(parents=True)
    # 別プロジェクト ProjB: データルート直下に .txt を持つ
    # （旧実装は parent.parent=データルートを走査しこれを誤って返していた）
    projB = data_root / "AAA_OtherProj"
    projB.mkdir()
    (projB / "sample.txt").write_text("x", encoding="utf-8")

    got = de._infer_data_folder(str(run), None, None, "DESI")
    # 別プロジェクト(ProjB)を返さない（プロジェクト外は走査しないため None）
    assert got is None


def test_infer_data_folder_finds_own_project_data(tmp_path, monkeypatch):
    data_root = _setup_data_root(tmp_path, monkeypatch)
    projA = data_root / "ProjA_Embryo"
    run = projA / "umap_run"
    run.mkdir(parents=True)
    raw = projA / "raw"
    raw.mkdir()
    (raw / "S1.txt").write_text("x", encoding="utf-8")
    # 別プロジェクトの混入候補（返してはいけない）
    projB = data_root / "AAA_OtherProj"
    projB.mkdir()
    (projB / "sample.txt").write_text("x", encoding="utf-8")

    got = de._infer_data_folder(str(run), None, None, "DESI")
    assert got is not None
    assert Path(got).resolve() == raw.resolve()  # 自プロジェクトの raw を返す


def test_infer_data_folder_finds_data_directly_in_dataset_dir(tmp_path, monkeypatch):
    """生データが『データセットフォルダ直下』(=結果フォルダの親) に置かれているケース。

    ver4.11 リグレッション修正: ver4.10 はサブフォルダのみ走査していたため、
    .txt がデータセット直下にあると「見つかりません」になっていた。
    """
    data_root = _setup_data_root(tmp_path, monkeypatch)
    dataset = data_root / "251213_Embryo"
    run = dataset / "260403_UMAP_E16E18"   # 結果フォルダ
    run.mkdir(parents=True)
    # 生データ .txt はデータセット直下（run の中ではない）
    (dataset / "E16_PN.txt").write_text("x", encoding="utf-8")
    (dataset / "E18_PN.txt").write_text("x", encoding="utf-8")
    # 結果系サブフォルダ（.txt は持たない）
    (dataset / "Harmony").mkdir()
    (dataset / "RDS_Files").mkdir()
    # 別プロジェクト（返してはいけない）
    other = data_root / "AAA_Other"
    other.mkdir()
    (other / "x.txt").write_text("x", encoding="utf-8")

    got = de._infer_data_folder(str(run), None, None, "DESI")
    assert got is not None
    assert Path(got).resolve() == dataset.resolve()  # データセット直下を返す（別プロジェクトでない）
