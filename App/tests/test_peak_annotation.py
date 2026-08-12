"""SCiLS `Name` パーサ（peak_annotation）のテスト。

確定ルールの全ケースを検証する:
  2-field(分類+DB) / 1-field(DBのみ) / No DB hit / key=value /
  adduct_family / 未知キー / NA / 表示名 / 列名 / 最近傍 join。
"""

import numpy as np

from app.services.peak_annotation import (
    parse_scils_name,
    display_label,
    make_column_name,
    build_feature_annotation_table,
)


def test_two_fields_class_and_db():
    name = ("PI 38:4 (PI 18:0/20:4) | PI | Calibration_Priority_9AA_v1_7 | "
            "[M-H]- | 2.13ppm | annotation_tol=10ppm | mz_window=10ppm | "
            "formula=C47H83O13P | SMILES=NA | adduct_image=single_ion_or_no_family")
    r = parse_scils_name(name)
    assert r["compound"] == "PI 38:4 (PI 18:0/20:4)"
    assert r["lipid_class"] == "PI"
    assert r["database"] == "Calibration_Priority_9AA_v1_7"
    assert r["adduct"] == "[M-H]-"
    assert r["ppm"] == 2.13
    assert r["formula"] == "C47H83O13P"
    assert r["smiles"] is None          # NA -> None
    assert r["adduct_image"] == "single_ion_or_no_family"
    assert r["annotation_tol"] == "10ppm"
    assert r["mz_window"] == "10ppm"
    assert r["is_db_hit"] is True
    assert r["raw"] == name


def test_one_field_db_only_no_class():
    # 分類が消え、化合物名の次が DB（inhouse_or_list）になる行
    name = "ADP | inhouse_or_list | [M-H]- | 0.30ppm | formula=C10H15N5O10P2 | SMILES=NA"
    r = parse_scils_name(name)
    assert r["compound"] == "ADP"
    assert r["lipid_class"] is None       # 補完しない
    assert r["database"] == "inhouse_or_list"
    assert r["adduct"] == "[M-H]-"
    assert r["ppm"] == 0.30
    assert r["formula"] == "C10H15N5O10P2"


def test_no_db_hit():
    name = "No DB hit | m/z=512.345600 | annotation_tol=10ppm | mz_window=10ppm | formula=NA | SMILES=NA"
    r = parse_scils_name(name)
    assert r["is_db_hit"] is False
    assert r["compound"] == "No DB hit"
    assert r["lipid_class"] is None
    assert r["database"] is None
    assert r["adduct"] is None
    assert r["ppm"] is None
    assert r["formula"] is None           # NA -> None
    assert r["extras"].get("mz_field") == "512.345600"


def test_adduct_family_kept_raw():
    name = ("PA 36:1 | PA | LipidMaps_COMPDB | [M-H]- | 0.57ppm | "
            "adduct_image=similar_distribution | "
            "adduct_family=[M-H]-,[M]-; max_corr=1.00; median_corr=1.00; max_hotspot_jaccard=0.50")
    r = parse_scils_name(name)
    assert r["lipid_class"] == "PA"
    assert r["database"] == "LipidMaps_COMPDB"
    assert r["adduct_image"] == "similar_distribution"
    # adduct_family は ; , = を含む値を丸ごと raw 保持（先頭 = で分割）
    assert r["adduct_family"] == "[M-H]-,[M]-; max_corr=1.00; median_corr=1.00; max_hotspot_jaccard=0.50"


def test_unknown_key_kept_in_extras():
    name = "X 1:0 | CL | HMDB | [M]- | 1.00ppm | some_new_key=hello | formula=C1"
    r = parse_scils_name(name)
    assert r["adduct"] == "[M]-"
    assert r["extras"].get("some_new_key") == "hello"
    assert r["formula"] == "C1"


def test_more_than_two_leading_goes_to_extras():
    name = "Comp | A | B | C | [M-H]- | 1.0ppm"
    r = parse_scils_name(name)
    assert r["lipid_class"] == "A"
    assert r["database"] == "B"
    assert r["extras"].get("unparsed_leading") == ["C"]


def test_empty_name():
    r = parse_scils_name("")
    assert r["is_db_hit"] is False
    assert r["compound"] == ""
    assert r["raw"] == ""


def test_display_label():
    hit = parse_scils_name("PI 38:4 (PI 18:0/20:4) | PI | DB | [M-H]- | 2.13ppm")
    assert display_label(hit, 885.5494) == "PI 38:4 (PI 18:0/20:4)_885.5494"
    miss = parse_scils_name("No DB hit | m/z=512.3456")
    assert display_label(miss, 512.345600) == "512.3456"   # 数値のみ


def test_make_column_name():
    raw = "PI 38:4 (PI 18:0/20:4) | PI | DB | [M-H]- | 2.13ppm"
    col = make_column_name(raw, 885.549400)
    assert col.startswith("PI 38:4 (PI 18:0/20:4)_885.5494 | PI | DB | [M-H]-")
    # Name 無し -> 従来の数値列名（6桁）にフォールバック
    assert make_column_name("", 419.2572) == "419.257200"


def test_build_feature_annotation_table_join():
    mz_values = np.array([419.2572, 673.4831, 885.5494])
    pk_mz = np.array([885.5494, 419.2572, 673.4831, 999.9999])  # 順不同・余分あり
    pk_names = [
        "PI 38:4 (PI 18:0/20:4) | PI | Calibration | [M-H]- | 2.13ppm | formula=C47H83O13P",
        "ADP | inhouse_or_list | [M-H]- | 0.30ppm",
        "PA 36:1 | PA | LipidMaps_COMPDB | [M-H]- | 0.57ppm",
        "Other | X | Y | [M-H]- | 9.9ppm",
    ]
    df = build_feature_annotation_table(mz_values, pk_mz, pk_names, tol_da=0.01)
    assert list(df["mz"]) == [419.2572, 673.4831, 885.5494]
    # 最近傍で割当
    assert df.loc[df["mz"] == 419.2572, "compound"].iloc[0] == "ADP"
    assert df.loc[df["mz"] == 419.2572, "database"].iloc[0] == "inhouse_or_list"
    assert df.loc[df["mz"] == 419.2572, "lipid_class"].iloc[0] is None
    assert df.loc[df["mz"] == 673.4831, "lipid_class"].iloc[0] == "PA"
    assert df.loc[df["mz"] == 885.5494, "compound"].iloc[0] == "PI 38:4 (PI 18:0/20:4)"
    assert df.loc[df["mz"] == 885.5494, "display_name"].iloc[0] == "PI 38:4 (PI 18:0/20:4)_885.5494"


def test_build_table_no_match_within_tol():
    mz_values = np.array([100.0])
    df = build_feature_annotation_table(mz_values, np.array([200.0]), ["Z | A | [M-H]- | 1ppm"], tol_da=0.01)
    # 許容外 -> 注釈なし（No DB hit 相当の空）
    assert df.loc[0, "compound"] == ""
    assert df.loc[0, "display_name"] == "100.0000"


# ---------------------------------------------------------------------------
# 変換器（埋め込み列名＋サイドカー＋mz_sortedメタ）の E2E
# ---------------------------------------------------------------------------

def _write(p, text):
    p.write_text(text, encoding="utf-8")


def test_convert_embeds_annotation_and_sidecar(tmp_path):
    import pyarrow.parquet as pq
    import pandas as pd
    from pathlib import Path
    from app.services.scils_converter import convert_scils_to_parquet

    folder = tmp_path / "data"
    folder.mkdir()
    _write(folder / "test_Intensity.csv",
           "m/z,Spot 1,Spot 2,Spot 3,Spot 4,Spot 5\n"
           "419.2572,10,20,30,40,50\n"
           "673.4831,11,21,31,41,51\n"
           "885.5494,12,22,32,42,52\n")
    _write(folder / "test_Spot.csv",
           "SpotIndex,X,Y\n1,0,0\n2,1,0\n3,2,0\n4,0,1\n5,1,1\n")
    _write(folder / "test_peaklist.csv",
           "m/z,Interval Width,Color,Name\n"
           "419.2572,0.01,#fff,ADP | inhouse_or_list | [M-H]- | 0.30ppm | formula=C10H15N5O10P2 | SMILES=NA\n"
           "673.4831,0.01,#fff,PA 36:1 | PA | LipidMaps_COMPDB | [M-H]- | 0.57ppm\n"
           "885.5494,0.01,#fff,PI 38:4 (PI 18:0/20:4) | PI | Calibration | [M-H]- | 2.13ppm | formula=C47H83O13P\n")

    out = tmp_path / "out.parquet"
    res = convert_scils_to_parquet(str(folder), str(out), organize=False)

    assert res.has_peak_list is True
    assert res.n_annotated == 3
    assert res.sidecar_path and Path(res.sidecar_path).exists()

    # ★ ver55.0: 化合物名は **列名に焼き込まない**。列名は常に m/z の数値で、
    #   化合物名はサイドカーが持つ（= 後から付け外しできる）。
    #   焼き込みは不可逆で、しかも列名は feature の識別子として R の rowname →
    #   deg$gene → 画面・CSV・PPTX・PNG 名まで伝播するため表示を切ることもできなかった。
    schema = pq.read_schema(res.output_path)
    names = list(schema.names)
    for meta in ("id", "x", "y", "annotation"):
        assert meta in names
    feat_cols = [n for n in names if n not in ("id", "x", "y", "annotation")]
    assert feat_cols == ["419.257200", "673.483100", "885.549400"], feat_cols
    assert not any("|" in c for c in feat_cols), (
        "化合物名が列名に焼き込まれている（サイドカーへ入れること）")

    # mz_sorted / peak_list メタがファイルに永続化されている（フル桁）
    md = schema.metadata or {}
    assert b"mz_sorted" in md
    mz_list = [float(x) for x in md[b"mz_sorted"].decode().split(",")]
    assert mz_list == [419.2572, 673.4831, 885.5494]
    assert b"peak_list" in md

    # サイドカー（per-feature 表）
    side = pd.read_parquet(res.sidecar_path)
    assert list(side["mz"]) == [419.2572, 673.4831, 885.5494]
    assert side.loc[side["mz"] == 419.2572, "database"].iloc[0] == "inhouse_or_list"
    assert side.loc[side["mz"] == 419.2572, "lipid_class"].iloc[0] is None
    assert side.loc[side["mz"] == 885.5494, "compound"].iloc[0] == "PI 38:4 (PI 18:0/20:4)"


def test_convert_without_peaklist_keeps_numeric_columns(tmp_path):
    import pyarrow.parquet as pq
    from app.services.scils_converter import convert_scils_to_parquet

    folder = tmp_path / "data"
    folder.mkdir()
    _write(folder / "x_Intensity.csv",
           "m/z,Spot 1,Spot 2,Spot 3,Spot 4,Spot 5\n"
           "419.2572,10,20,30,40,50\n885.5494,12,22,32,42,52\n")
    _write(folder / "x_Spot.csv",
           "SpotIndex,X,Y\n1,0,0\n2,1,0\n3,2,0\n4,0,1\n5,1,1\n")

    out = tmp_path / "out.parquet"
    res = convert_scils_to_parquet(str(folder), str(out), organize=False)
    assert res.has_peak_list is False
    assert res.sidecar_path == ""
    names = list(pq.read_schema(res.output_path).names)
    # 従来どおり 6 桁の数値列名（後方互換）
    assert "419.257200" in names and "885.549400" in names


def test_convert_semicolon_peaklist_with_adduct_family(tmp_path):
    """';' 区切り peak-list で Name 内に adduct_family の ';' を含んでも注釈が付く（回帰防止）。

    修正前は _read_peaklist が ParserError → try/except で注釈なし
    （n_annotated=0・sidecar 未出力）になっていた。
    """
    import pandas as pd
    from pathlib import Path
    from app.services.scils_converter import convert_scils_to_parquet

    folder = tmp_path / "data"
    folder.mkdir()
    _write(folder / "s_Intensity.csv",
           "m/z,Spot 1,Spot 2,Spot 3,Spot 4,Spot 5\n"
           "419.2572,10,20,30,40,50\n"
           "885.5494,12,22,32,42,52\n")
    _write(folder / "s_Spot.csv",
           "SpotIndex,X,Y\n1,0,0\n2,1,0\n3,2,0\n4,0,1\n5,1,1\n")
    # ';' 区切り Feature list。885 行は adduct_family に ';' を含む。
    _write(folder / "s_peaklist.csv",
           "m/z;Interval Width;Color;Name;Int1\n"
           "419.2572;0.01;#fff;ADP | inhouse_or_list | [M-H]- | 0.30ppm | formula=C10H15N5O10P2 | SMILES=NA;5\n"
           "885.5494;0.01;#fff;PI 38:4 | PI | [M-H]- | 2.13ppm | formula=C47H83O13P | "
           "adduct_family=mass_only;n=2;adducts=[M-H]-,[M]-;peaks=1,2 | image_distribution=no;9\n")

    out = tmp_path / "out.parquet"
    res = convert_scils_to_parquet(str(folder), str(out), organize=False)

    assert res.has_peak_list is True
    assert res.n_annotated == 2
    assert res.sidecar_path and Path(res.sidecar_path).exists()

    side = pd.read_parquet(res.sidecar_path)
    assert side.loc[side["mz"] == 885.5494, "compound"].iloc[0] == "PI 38:4"
    assert (side.loc[side["mz"] == 885.5494, "adduct_family"].iloc[0]
            == "mass_only;n=2;adducts=[M-H]-,[M]-;peaks=1,2")


def test_read_raw_mz_spectrum_handles_embedded_columns(tmp_path):
    """埋め込み列名 + mz_sorted メタの parquet から m/z 平均スペクトルを復元できる。"""
    import pyarrow as pa
    import pyarrow.parquet as pq
    from app.services.data_manager import read_raw_mz_spectrum

    folder = tmp_path / "raw"
    folder.mkdir()
    c1 = "ADP_419.2572 | inhouse_or_list | [M-H]- | 0.30ppm"
    c2 = "PI 38:4_885.5494 | PI | DB | [M-H]- | 2.13ppm"
    schema = pa.schema(
        [("id", pa.int64()), ("x", pa.float64()), ("y", pa.float64()),
         (c1, pa.float32()), (c2, pa.float32()), ("annotation", pa.string())],
        metadata={b"mz_sorted": b"419.2572,885.5494"},
    )
    table = pa.table(
        {"id": [1, 2], "x": [0.0, 1.0], "y": [0.0, 0.0],
         c1: [10.0, 20.0], c2: [30.0, 50.0], "annotation": ["R1", "R1"]},
        schema=schema,
    )
    pq.write_table(table, folder / "sample.parquet")

    avg = read_raw_mz_spectrum(str(folder), is_tims=True)
    assert avg is not None
    assert list(avg.columns) == ["mz_419.2572", "mz_885.5494"]
    assert abs(float(avg.iloc[0]["mz_419.2572"]) - 15.0) < 1e-5
    assert abs(float(avg.iloc[0]["mz_885.5494"]) - 40.0) < 1e-5
