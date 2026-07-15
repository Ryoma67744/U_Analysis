"""データ出力 (interactive_data_export) の回帰テスト。

ver4.9 修正:
  DESIプロジェクトが metadata 未設定で TIMS 経路に入り、「生データ＋クラスター番号」でなく
  指標ファイルが出力される問題。_resolve_instrument がパスから DESI を判定し、
  _export_desi に正しくルーティングされることを担保する。
"""

import io
from collections import OrderedDict

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

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


# ---------------------------------------------------------------------------
# ② 出力にクラスタ変更名を反映
# ---------------------------------------------------------------------------

def test_build_cluster_lookup_uses_renamed_names():
    plot = pd.DataFrame({
        "SpatialX": [10.0, 11.0, 12.0],
        "SpatialY": [20.0, 21.0, 22.0],
        "Sample": ["S1", "S1", "S1"],
        "Cluster": [1, 2, 1],
    })
    lookup = de._build_cluster_lookup(plot, {"1": "Epithelial"})
    assert lookup[("S1", 10.0, 20.0)] == "Epithelial"  # cluster 1 -> 変更名
    assert lookup[("S1", 11.0, 21.0)] == "2"            # cluster 2 -> 番号のまま
    assert lookup[("S1", 12.0, 22.0)] == "Epithelial"
    # name_map なし → 番号
    lookup2 = de._build_cluster_lookup(plot, None)
    assert lookup2[("S1", 10.0, 20.0)] == "1"


# ---------------------------------------------------------------------------
# ① data_folder 自動保存（バックフィル）
# ---------------------------------------------------------------------------

def test_ensure_sub_project_data_folder_backfills(tmp_path, monkeypatch):
    data_root = _setup_data_root(tmp_path, monkeypatch)
    dataset = data_root / "251213_Embryo"
    run = dataset / "260403_UMAP_E16E18"
    run.mkdir(parents=True)
    (dataset / "E16_PN.txt").write_text("x", encoding="utf-8")

    saved = {}
    fake_sub = {"id": "s1", "data_folder": ""}  # 空 → バックフィル対象
    monkeypatch.setattr("app.services.project_manager.get_sub_project",
                        lambda p, s: fake_sub)

    def fake_update(p, s, updates):
        saved.update(updates)
        return {**fake_sub, **updates}
    monkeypatch.setattr("app.services.project_manager.update_sub_project", fake_update)

    # ms_instrument 空でも、パス /DESI/ から DESI と解決して .txt を見つける
    got = de.ensure_sub_project_data_folder("p1", "s1", str(run), "")
    assert got is not None
    assert Path(got).resolve() == dataset.resolve()
    assert saved.get("data_folder") == got  # サブプロジェクトに保存された


def test_ensure_sub_project_data_folder_keeps_existing(monkeypatch):
    monkeypatch.setattr("app.services.project_manager.get_sub_project",
                        lambda p, s: {"data_folder": "/already/set"})
    called = {"n": 0}
    monkeypatch.setattr("app.services.project_manager.update_sub_project",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    got = de.ensure_sub_project_data_folder("p", "s", "/some/result", "DESI")
    assert got == "/already/set"
    assert called["n"] == 0  # 既存値があれば更新しない


# ---------------------------------------------------------------------------
# TIMS Parquet 出力: 入力(登録)parquet と同一の内部構造で書き出す
#   - スキーマメタ (mz_sorted/annotation_files/peak_list) を保持
#   - 圧縮を zstd に統一
#   - 追加解析列を analysis_columns メタに記録
#   - 出力を再登録しても壊れない（読取側ガード）
# ---------------------------------------------------------------------------

def _make_input_parquet(path, rows, mz_values, *, annotated, with_meta=True):
    """scils_converter 出力を模した TIMS 入力 parquet を1本作る。

    rows: [(sample_name, x, y), ...]（各行=1スポット、annotation 列にサンプル名）
    列: id, x, y, <特徴量>, annotation ＋ スキーマメタ mz_sorted/annotation_files。
    戻り値: (mz_sorted(np.ndarray), 特徴量列名 list)
    """
    mz_sorted = np.sort(np.asarray(mz_values, dtype=float))
    if annotated:
        feat_names = [f"Cpd{i}_{m:.4f} | HMDB | [M+H]+"
                      for i, m in enumerate(mz_sorted)]
    else:
        feat_names = [f"{m:.6f}" for m in mz_sorted]

    n = len(rows)
    ids = pa.array(np.arange(1, n + 1, dtype=np.int64))
    xs = pa.array(np.array([r[1] for r in rows], dtype=np.float64))
    ys = pa.array(np.array([r[2] for r in rows], dtype=np.float64))
    anns = pa.array([r[0] for r in rows], type=pa.string())
    feat_arrays = [pa.array(np.full(n, float(j + 1), dtype=np.float32))
                   for j in range(len(feat_names))]

    fields = (
        [pa.field("id", pa.int64()), pa.field("x", pa.float64()),
         pa.field("y", pa.float64())]
        + [pa.field(nm, pa.float32()) for nm in feat_names]
        + [pa.field("annotation", pa.string())]
    )
    md = {}
    if with_meta:
        md[b"mz_sorted"] = ",".join(f"{v:.10g}" for v in mz_sorted).encode("utf-8")
        md[b"annotation_files"] = b"ann.csv"
    schema = pa.schema(fields, metadata=md or None)
    table = pa.Table.from_arrays([ids, xs, ys] + feat_arrays + [anns], schema=schema)
    pq.write_table(table, str(path), compression="zstd")
    return mz_sorted, feat_names


def _mz_sorted_bytes(mz_sorted):
    return ",".join(f"{v:.10g}" for v in mz_sorted).encode("utf-8")


def test_export_tims_parquet_carries_metadata_and_zstd(tmp_path):
    mz_sorted, _ = _make_input_parquet(
        tmp_path / "S1.parquet",
        [("S1", 10.0, 20.0), ("S1", 11.0, 21.0), ("S1", 99.0, 99.0)],
        [700.1234, 759.5678, 810.9999], annotated=True)
    lookups = OrderedDict([
        ("Harmony", {("S1", 10.0, 20.0): "3", ("S1", 11.0, 21.0): "5"}),
    ])
    region = {("S1", 10.0, 20.0): "Tumor"}

    out_bytes, filename = de._export_tims(
        str(tmp_path), lookups, "parquet", region_lookup=region)
    assert filename == "UMAP_cluster_TIMS.parquet"

    pf = pq.ParquetFile(io.BytesIO(out_bytes))
    md = pf.schema_arrow.metadata or {}
    # 入力のスキーマメタを保持
    assert md.get(b"mz_sorted") == _mz_sorted_bytes(mz_sorted)
    assert md.get(b"annotation_files") == b"ann.csv"
    # 追加解析列をメタに記録
    assert md.get(b"analysis_columns") == "UMAP cluster,領域名".encode("utf-8")
    # 追加列が実在
    names = pf.schema_arrow.names
    assert "UMAP cluster" in names and "領域名" in names
    # 圧縮は zstd（入力と一致）
    assert pf.metadata.row_group(0).column(0).compression == "ZSTD"


def test_export_tims_parquet_multi_file_reconciles(tmp_path):
    mz = [700.1, 800.2]
    m1, _ = _make_input_parquet(
        tmp_path / "S1.parquet", [("S1", 1.0, 1.0)], mz, annotated=False)
    _make_input_parquet(
        tmp_path / "S2.parquet", [("S2", 2.0, 2.0)], mz, annotated=False)
    lookups = OrderedDict([
        ("Harmony", {("S1", 1.0, 1.0): "1", ("S2", 2.0, 2.0): "2"}),
    ])

    out_bytes, _ = de._export_tims(str(tmp_path), lookups, "parquet")
    pf = pq.ParquetFile(io.BytesIO(out_bytes))
    md = pf.schema_arrow.metadata or {}
    # 入力間で mz_sorted が一致 → 保持
    assert md.get(b"mz_sorted") == _mz_sorted_bytes(m1)
    assert pf.metadata.num_rows == 2  # 2ファイル分の行が連結


def test_export_tims_parquet_meta_mismatch_drops(tmp_path, caplog):
    _make_input_parquet(
        tmp_path / "S1.parquet", [("S1", 1.0, 1.0)], [700.1, 800.2], annotated=False)
    _make_input_parquet(
        tmp_path / "S2.parquet", [("S2", 2.0, 2.0)], [700.1, 900.3], annotated=False)
    lookups = OrderedDict([("Harmony", {("S1", 1.0, 1.0): "1"})])

    with caplog.at_level("WARNING"):
        out_bytes, _ = de._export_tims(str(tmp_path), lookups, "parquet")

    pf = pq.ParquetFile(io.BytesIO(out_bytes))
    md = pf.schema_arrow.metadata or {}
    # 不一致なので誤った m/z 軸は書かない
    assert b"mz_sorted" not in md
    # analysis_columns は付く（region なしなので UMAP cluster のみ）
    assert md.get(b"analysis_columns") == "UMAP cluster".encode("utf-8")
    assert any("mz_sorted" in r.message for r in caplog.records)


def test_export_tims_parquet_csv_input_graceful(tmp_path):
    df = pd.DataFrame({
        "id": [1, 2], "x": [1.0, 2.0], "y": [1.0, 2.0],
        "700.100000": [0.1, 0.2], "800.200000": [0.3, 0.4],
        "annotation": ["S1", "S1"],
    })
    df.to_csv(tmp_path / "S1.csv", index=False)
    lookups = OrderedDict([("Harmony", {("S1", 1.0, 1.0): "1"})])

    out_bytes, _ = de._export_tims(str(tmp_path), lookups, "parquet")
    pf = pq.ParquetFile(io.BytesIO(out_bytes))
    md = pf.schema_arrow.metadata or {}
    # CSV 入力はメタを持たない → mz_sorted 無し・破綻せず出力
    assert b"mz_sorted" not in md
    assert md.get(b"analysis_columns") == "UMAP cluster".encode("utf-8")
    assert pf.metadata.row_group(0).column(0).compression == "ZSTD"


def test_export_tims_parquet_multi_method_analysis_columns(tmp_path):
    _make_input_parquet(
        tmp_path / "S1.parquet", [("S1", 1.0, 1.0)], [700.1, 800.2], annotated=False)
    lookups = OrderedDict([
        ("Harmony", {("S1", 1.0, 1.0): "1"}),
        ("RPCA", {("S1", 1.0, 1.0): "2"}),
    ])
    region = {("S1", 1.0, 1.0): "R"}

    out_bytes, _ = de._export_tims(
        str(tmp_path), lookups, "parquet", region_lookup=region)
    pf = pq.ParquetFile(io.BytesIO(out_bytes))
    md = pf.schema_arrow.metadata or {}
    # 複数手法は手法名が列名 → analysis_columns も手法名＋領域名
    assert md.get(b"analysis_columns") == "Harmony,RPCA,領域名".encode("utf-8")
    names = pf.schema_arrow.names
    assert "Harmony" in names and "RPCA" in names and "領域名" in names


def test_output_reregisters_via_reader(tmp_path):
    """出力 parquet を新規入力として再登録 → 読取側が特徴量列を正しく復元
    （追加解析列が m/z 特徴量に混入しない）ことを確認。"""
    from app.services import data_manager as dm

    mz_sorted, _ = _make_input_parquet(
        tmp_path / "S1.parquet",
        [("S1", 10.0, 20.0), ("S1", 11.0, 21.0)],
        [700.1234, 759.5678, 810.9999], annotated=True)
    lookups = OrderedDict([
        ("Harmony", {("S1", 10.0, 20.0): "3", ("S1", 11.0, 21.0): "5"}),
    ])
    region = {("S1", 10.0, 20.0): "Tumor"}
    out_bytes, _ = de._export_tims(
        str(tmp_path), lookups, "parquet", region_lookup=region)

    # 出力を別フォルダに「再登録」
    reg = tmp_path / "reg"
    reg.mkdir()
    (reg / "UMAP_cluster_TIMS.parquet").write_bytes(out_bytes)

    avg = dm._read_tims_raw(reg, "UMAP_cluster_TIMS")
    assert avg is not None
    # 特徴量列数 = m/z 数（UMAP cluster / 領域名 が混入しない）
    assert avg.shape[1] == len(mz_sorted)
    # mz_sorted メタから mz_ 正規化された列名になる
    assert all(str(c).startswith("mz_") for c in avg.columns)
