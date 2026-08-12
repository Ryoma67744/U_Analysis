# =============================================================================
# ver55.0: 「指定していないアノテーションは付けない」を振る舞いで固定する。
#
# 直した実害は 3 本の独立した経路:
#   (1) 変換時、フォルダに同居する SCiLS Feature list (peak-list) が無条件に採用され、
#       化合物名が **Parquet の列名そのもの**に焼き込まれていた。列名は「注釈」ではなく
#       feature の識別子（R の rowname → deg$gene → 画面・CSV・PPTX・PNG 名）なので、
#       表示トグルでは消せず、後から戻すこともできなかった。
#   (2) 領域アノテーション CSV が 1 枚も無いとき、**Spot ファイル名**からラベルを作って
#       全 spot に割り当てていた。「Annotation CSV: (なし)」と「annotation ラベル: sample」が
#       同時に表示され、指定していない領域注釈が付いているように見えた。
#   (3) 解析時、サイドバー「⚙ TIMS初期設定」の既定値（同梱の 4500 種代謝物 DB）が
#       無条件で R に渡り、利用者が一度も指定していなくても m/z 照合が走っていた。
#       しかも R テンプレは ANNOTATION_ENABLE <- TRUE と Windows の Dropbox パスを
#       直書きしており、Python から一度も注入されていなかった。
#
# 方針: 利用者自身が Export した Feature list 由来の化合物名は**可逆な**サイドカーに
# 登録して既定で使う。同梱 DB による m/z 照合は明示的に選んだときだけ。
# =============================================================================

import re
from pathlib import Path

import pytest

pytest.importorskip("pyarrow")

import pyarrow.parquet as pq  # noqa: E402

RUNNER = Path(__file__).resolve().parent.parent / "app" / "services" / "analysis_runner.py"
TIMS_TEMPLATE = (Path(__file__).resolve().parent.parent / "Script" / "TIMS"
                 / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _make_folder(folder: Path, *, peaklist: bool, extra_region_csv: bool) -> Path:
    """最小の SCiLS 一式。peak-list / 追加の Spot 様 CSV の有無を選べる。"""
    _write(folder / "s_Intensity.csv",
           "m/z,Spot 1,Spot 2,Spot 3,Spot 4,Spot 5\n"
           "419.2572,10,20,30,40,50\n"
           "673.4831,11,21,31,41,51\n"
           "885.5494,12,22,32,42,52\n")
    _write(folder / "s_Spot.csv",
           "SpotIndex,X,Y\n1,0,0\n2,1,0\n3,2,0\n4,0,1\n5,1,1\n")
    if peaklist:
        _write(folder / "s_peaklist.csv",
               "m/z,Interval Width,Color,Name\n"
               "419.2572,0.01,#fff,ADP | inhouse_or_list | [M-H]- | 0.30ppm\n"
               "673.4831,0.01,#fff,PA 36:1 | PA | LipidMaps_COMPDB | [M-H]- | 0.57ppm\n"
               "885.5494,0.01,#fff,PI 38:4 | PI | Calibration | [M-H]- | 2.13ppm\n")
    if extra_region_csv:
        # Spot 本体より小さい Spot 様 CSV = 領域アノテーション扱いされる候補
        _write(folder / "s_region.csv", "SpotIndex,X,Y\n1,0,0\n2,1,0\n")
    return folder


class TestCompoundNamesGoToSidecarNotColumnNames:
    """(1) 化合物名は列名に焼き込まず、サイドカーに登録する。"""

    def test_columns_stay_numeric_and_sidecar_is_written(self, tmp_path):
        from app.services.scils_converter import convert_scils_to_parquet

        folder = _make_folder(tmp_path / "in", peaklist=True, extra_region_csv=False)
        out = tmp_path / "out.parquet"
        res = convert_scils_to_parquet(str(folder), str(out), organize=False)

        assert res.has_peak_list is True
        # 登録はされる（= 変換の最後にサイドカーが自動生成される）
        assert res.sidecar_path and Path(res.sidecar_path).exists()
        assert res.n_annotated == 3

        names = list(pq.read_schema(res.output_path).names)
        feat = [n for n in names if n not in ("id", "x", "y", "annotation")]
        assert all(re.fullmatch(r"\d+\.\d{6}", n) for n in feat), (
            f"列名に化合物名が焼き込まれている: {feat}")

    def test_sidecar_carries_the_compound_names(self, tmp_path):
        pd = pytest.importorskip("pandas")
        from app.services.scils_converter import convert_scils_to_parquet

        folder = _make_folder(tmp_path / "in", peaklist=True, extra_region_csv=False)
        res = convert_scils_to_parquet(str(folder), str(tmp_path / "out.parquet"),
                                       organize=False)
        side = pd.read_parquet(res.sidecar_path)
        assert sorted(side["mz"]) == [419.2572, 673.4831, 885.5494]
        assert "ADP" in set(side["compound"])


class TestRegionAnnotationIsNotFabricated:
    """(2) 領域アノテーションを Spot ファイル名から捏造しない。"""

    def test_no_annotation_csv_means_unannotated(self, tmp_path):
        from app.services.scils_converter import convert_scils_to_parquet

        folder = _make_folder(tmp_path / "in", peaklist=False, extra_region_csv=False)
        res = convert_scils_to_parquet(str(folder), str(tmp_path / "out.parquet"),
                                       organize=False)

        assert res.has_annotation is False
        assert res.annotation_source == "none"
        assert res.annotation_labels == ["Unannotated"], (
            "Spot ファイル名由来のラベルが復活している")

    def test_source_is_recorded_in_the_parquet_footer(self, tmp_path):
        """由来をファイルに残す。UI が「領域の選択肢を出してよいか」を判断できる。"""
        from app.services.scils_converter import convert_scils_to_parquet

        folder = _make_folder(tmp_path / "in", peaklist=False, extra_region_csv=False)
        res = convert_scils_to_parquet(str(folder), str(tmp_path / "out.parquet"),
                                       organize=False)
        md = pq.read_schema(res.output_path).metadata or {}
        assert md.get(b"annotation_source") == b"none"

    def test_annotation_column_still_exists(self, tmp_path):
        """列そのものは残す。R が slice_id → condition の組み立てに使う。"""
        from app.services.scils_converter import convert_scils_to_parquet

        folder = _make_folder(tmp_path / "in", peaklist=False, extra_region_csv=False)
        res = convert_scils_to_parquet(str(folder), str(tmp_path / "out.parquet"),
                                       organize=False)
        assert "annotation" in pq.read_schema(res.output_path).names


class TestMetaboliteDbIsOptIn:
    """(3) 同梱 DB は明示的に選んだときだけ R へ渡る。"""

    def test_r_template_defaults_to_disabled(self):
        body = TIMS_TEMPLATE.read_text(encoding="utf-8")
        assert re.search(r"^ANNOTATION_ENABLE\s*<-\s*FALSE\s*$", body, re.M), (
            "R テンプレの ANNOTATION_ENABLE 既定が TRUE のまま")
        assert re.search(r'^ANNOTATION_CSV_PATH\s*<-\s*""\s*$', body, re.M), (
            "R テンプレに直書きの DB パスが残っている")
        assert re.search(r"^USE_EMBEDDED_COMPOUND_NAMES\s*<-\s*FALSE\s*$", body, re.M), (
            "USE_EMBEDDED_COMPOUND_NAMES が宣言されていない（注入先が無いと無言で素通りする）")
        # 他のパス変数（INPUT_PATHS 等）は毎回注入で置換される既定値なので対象外。
        # アノテーション系だけは「指定しない」が正常系なので、直書きが残ると必ず発火する。
        for line in body.splitlines():
            if re.match(r"^\s*(ANNOTATION_CSV_PATH|ANNOTATION_ENABLE)\s*<-", line):
                assert "Dropbox" not in line, f"直書きパスが残っている: {line}"

    def test_injection_is_unconditional(self):
        """条件付き注入は「注入しない = 直書きが生き残る」を意味するので不可。"""
        py = RUNNER.read_text(encoding="utf-8")
        for var in ("ANNOTATION_CSV_PATH", "ANNOTATION_ENABLE",
                    "USE_EMBEDDED_COMPOUND_NAMES"):
            assert re.search(rf'_replace_assign\(\s*\n?\s*lines,\s*"{var}"', py), (
                f"{var} が無条件注入になっていない")

    def test_blank_field_does_not_reach_the_run(self):
        """`_use_annot` に "db" が無ければ annotation_csv_path を積まない。"""
        src = (Path(__file__).resolve().parent.parent / "app" / "callbacks"
               / "analysis_callbacks.py").read_text(encoding="utf-8")
        assert 'if "db" in _use_annot:' in src, (
            "代謝物 DB の指定が opt-in で囲まれていない")
        assert "sorted(_acsv.glob" not in src, (
            "ディレクトリ指定時に先頭 CSV を拾う分岐が残っている"
            "（App/DB/TIMS には DB が 2 本あり別物が選ばれる）")


class TestReplaceAssignIsNotSilent:
    """`_replace_assign` の 0 件一致が無言で通らないこと。

    これが無言だったせいで、UI 指定が捨てられて直書き値が走る事故（R-01）が
    エラーもログも無いまま起き続けていた。
    """

    def test_missing_variable_is_logged(self, caplog):
        from app.services.analysis_runner import _replace_assign

        with caplog.at_level("WARNING"):
            out = _replace_assign(["X <- 1"], "NOT_THERE", '"v"')
        assert out == ["X <- 1"]
        assert any("NOT_THERE" in r.getMessage() for r in caplog.records), (
            "変数が無いのに警告が出ていない")
