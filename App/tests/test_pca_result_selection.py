"""PCA の選択対象が補助表ではなく補正前結果であることを確認する。"""

from pathlib import Path

import pytest

from app.callbacks import interactive_callbacks as ic
from app.utils.integration_methods import method_options, resolve_method_key


def _write_results(folder, companion=True):
    folder.mkdir(parents=True, exist_ok=True)
    names = ["Step2_HarmonyPCA_Result.rds", "Step3_RPCA_Result.rds"]
    if companion:
        names.append("Step2_PCA_uncorrected.rds")
    for prefix in ("harmony", "rpca", "pca_uncorrected"):
        names.extend([f"pixel_table_{prefix}.rds", f"UMAP_{prefix}_umap_embedding.rds",
                      f"deg_{prefix}.rds", f"plotdata_volcano_{prefix}.rds"])
    for name in names:
        (folder / name).write_bytes(b"existing result must not change")
    return names


@pytest.mark.parametrize("subdir", ["", "RDS_Files", "nested/results/RDS_Files"])
def test_standard_tims_files_never_register_pixel_table_as_pca(tmp_path, subdir):
    folder = tmp_path / subdir
    names = _write_results(folder)
    before = {name: (folder / name).read_bytes() for name in names}
    got = ic._detect_integration_methods(str(tmp_path), include_derived=True)
    assert {key: Path(value).name for key, value in got.items()} == {
        "Harmony": "Step2_HarmonyPCA_Result.rds",
        "RPCA": "Step3_RPCA_Result.rds",
        "PCA (uncorrected)": "Step2_PCA_uncorrected.rds",
    }
    assert {name: (folder / name).read_bytes() for name in names} == before


@pytest.mark.parametrize("subdir", ["", "nested"])
def test_auxiliary_files_cannot_enter_any_detection_stage(tmp_path, subdir):
    folder = tmp_path / subdir
    folder.mkdir(exist_ok=True)
    for name in ("pixel_table_rpca.rds", "pixel_table_harmony.rds",
                 "pixel_table_step2_uncorrected.rds", "plotdata_step3_rpca.rds",
                 "feature_single_pca.rds"):
        (folder / name).write_bytes(b"table")
    assert ic._detect_integration_methods(str(tmp_path), include_derived=True) == {}


@pytest.mark.parametrize("subdir", ["", "nested"])
def test_another_corrected_result_does_not_fall_through_to_pca(tmp_path, subdir):
    folder = tmp_path / subdir
    _write_results(folder)
    for name in ("older_RPCA.rds", "older_HarmonyPCA.rds"):
        (folder / name).write_bytes(b"corrected result")
    assert "PCA" not in ic._detect_integration_methods(str(tmp_path), include_derived=True)


def test_legacy_derived_pca_is_available_despite_pixel_tables(tmp_path):
    _write_results(tmp_path / "RDS_Files", companion=False)
    got = ic._detect_integration_methods(str(tmp_path), include_derived=True)
    assert "derived_pca" in got["PCA"]
    assert Path(got["PCA"]).name != "pixel_table_rpca.rds"


def test_real_standalone_pca_is_retained(tmp_path):
    path = tmp_path / "DESI_SeuratCombined_PCA.rds"
    path.write_bytes(b"standalone")
    got = ic._detect_integration_methods(str(tmp_path), include_derived=True)
    assert got == {"PCA": str(path)}
    assert method_options(got) == [{"label": "PCA", "value": "PCA"}]


@pytest.mark.parametrize("auto", [False, True])
def test_viewer_pca_label_retains_companion_identity_and_path(tmp_path, auto):
    _write_results(tmp_path / "RDS_Files")
    options, selected, rds_map = (ic.auto_scan_rds_files(str(tmp_path), None) if auto
                                 else ic.scan_rds_files(1, str(tmp_path)))
    assert sorted(option["label"] for option in options) == ["Harmony", "PCA", "RPCA"]
    pca = next(option for option in options if option["label"] == "PCA")
    assert pca["value"] == "PCA (uncorrected)"
    assert Path(rds_map[pca["value"]]).name == "Step2_PCA_uncorrected.rds"
    assert selected == "Harmony"


def test_companion_is_the_only_visible_pca_when_both_real_results_exist():
    options = method_options(["PCA", "Harmony", "PCA (uncorrected)", "RPCA"])
    assert options == [{"label": "Harmony", "value": "Harmony"},
                       {"label": "PCA", "value": "PCA (uncorrected)"},
                       {"label": "RPCA", "value": "RPCA"}]


@pytest.mark.parametrize("method", ["PCA", "PCA (uncorrected)"])
def test_old_and_current_pca_shares_resolve_to_only_the_companion(tmp_path, method):
    _write_results(tmp_path / "RDS_Files")
    options, selected, rds_map = ic.auto_scan_rds_files(
        str(tmp_path), {"active": True, "integration_method": method})
    assert options == [{"label": "PCA", "value": "PCA (uncorrected)"}]
    assert selected == "PCA (uncorrected)"
    assert list(rds_map) == [selected]


def test_missing_share_method_does_not_expand_to_all_results(tmp_path):
    (tmp_path / "Step3_RPCA_Result.rds").write_bytes(b"result")
    assert ic.auto_scan_rds_files(
        str(tmp_path), {"active": True, "integration_method": "PCA"}) == ([], None, {})
    assert resolve_method_key("PCA", {"RPCA": "result"}) is None


def test_stale_auxiliary_selection_stops_before_loading(tmp_path):
    path = tmp_path / "pixel_table_rpca.rds"
    path.write_bytes(b"table")
    result = ic.load_stage_a_show_progress(1, "PCA", {"PCA": str(path)}, str(tmp_path))
    assert result[6] is ic.no_update
    assert "再スキャン" in str(result[5])
