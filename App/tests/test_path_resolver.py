"""Tests for app.services.path_resolver"""

from pathlib import Path

import pytest

from app.services import path_resolver


@pytest.fixture
def fake_data_roots(tmp_path, monkeypatch):
    """DESI / TIMS の外部データルートをモックしたファイルシステムを構築。"""
    desi_root = tmp_path / "ext_desi"
    tims_root = tmp_path / "ext_tims"
    desi_legacy = tmp_path / "repo" / "Data" / "DESI" / "Data"
    tims_legacy = tmp_path / "repo" / "Data" / "TIMS" / "Data"

    # DESI 実データ (新 ROOT)
    (desi_root / "Experiment_A" / "sample01").mkdir(parents=True)
    (desi_root / "Experiment_A" / "sample01" / "data.imzML").write_text("x")

    # DESI レガシー側にのみ存在するサンプル
    (desi_legacy / "Legacy_Exp" / "sampleL").mkdir(parents=True)

    # TIMS 実データ
    (tims_root / "TIMS_Project" / "run_007").mkdir(parents=True)
    (tims_root / "TIMS_Project" / "run_007" / "analysis.tdf").write_text("x")

    monkeypatch.setattr(
        path_resolver,
        "DESI_DATA_CANDIDATES",
        [desi_root, desi_legacy],
    )
    monkeypatch.setattr(
        path_resolver,
        "TIMS_DATA_CANDIDATES",
        [tims_root, tims_legacy],
    )

    return {
        "desi_root": desi_root,
        "tims_root": tims_root,
        "desi_legacy": desi_legacy,
        "tims_legacy": tims_legacy,
    }


class TestInferModality:
    def test_detects_desi(self):
        assert path_resolver._infer_modality(r"C:\Users\x\Dropbox\DESI\Data\exp1") == "desi"

    def test_detects_tims(self):
        assert path_resolver._infer_modality("/home/u/MSI/TIMS/Data/run1") == "tims"

    def test_returns_auto_when_unknown(self):
        assert path_resolver._infer_modality("/tmp/something") == "auto"


class TestSplitPath:
    def test_windows_separators(self):
        parts = path_resolver._split_path(r"C:\Users\x\Data\sample01")
        assert parts[-2:] == ["Data", "sample01"]

    def test_posix_separators(self):
        parts = path_resolver._split_path("/home/u/Data/sample01")
        assert parts[-2:] == ["Data", "sample01"]

    def test_empty(self):
        assert path_resolver._split_path("") == []


class TestResolveDataPath:
    def test_returns_existing_path_unchanged(self, fake_data_roots):
        existing = fake_data_roots["desi_root"] / "Experiment_A" / "sample01"
        result = path_resolver.resolve_data_path(str(existing), modality="desi")
        assert result == existing

    def test_finds_directory_by_tail_in_desi(self, fake_data_roots):
        broken = r"C:\Users\old\Dropbox\DESI\Data\Experiment_A\sample01"
        result = path_resolver.resolve_data_path(broken, modality="auto", is_file=False)
        assert result is not None
        assert result == fake_data_roots["desi_root"] / "Experiment_A" / "sample01"

    def test_finds_directory_by_tail_in_tims(self, fake_data_roots):
        broken = r"C:\Users\old\Dropbox\TIMS\Data\TIMS_Project\run_007"
        result = path_resolver.resolve_data_path(broken, modality="auto", is_file=False)
        assert result is not None
        assert result == fake_data_roots["tims_root"] / "TIMS_Project" / "run_007"

    def test_falls_back_to_legacy_candidate(self, fake_data_roots):
        broken = r"C:\Users\old\Dropbox\DESI\Data\Legacy_Exp\sampleL"
        result = path_resolver.resolve_data_path(broken, modality="desi", is_file=False)
        assert result is not None
        assert result == fake_data_roots["desi_legacy"] / "Legacy_Exp" / "sampleL"

    def test_returns_none_when_not_found(self, fake_data_roots):
        result = path_resolver.resolve_data_path(
            r"C:\Users\old\Dropbox\DESI\Data\NoSuch\folder",
            modality="desi",
        )
        assert result is None

    def test_returns_none_for_empty_input(self, fake_data_roots):
        assert path_resolver.resolve_data_path("", modality="auto") is None

    def test_finds_file_when_is_file_true(self, fake_data_roots):
        broken = r"C:\old\DESI\Experiment_A\sample01\data.imzML"
        result = path_resolver.resolve_data_path(broken, modality="desi", is_file=True)
        assert result is not None
        assert result.name == "data.imzML"
        assert result.is_file()


class TestResolveProjectPaths:
    def test_corrects_data_folder_and_output_dir(self, fake_data_roots):
        sub_info = {
            "data_folder": r"C:\old\DESI\Data\Experiment_A\sample01",
            "last_analysis_settings": {
                "data_folder": r"C:\old\DESI\Data\Experiment_A\sample01",
                "output_dir": r"C:\old\DESI\Data\Experiment_A\sample01",
            },
        }
        corrected, unresolved = path_resolver.resolve_project_paths(sub_info)
        expected = str(fake_data_roots["desi_root"] / "Experiment_A" / "sample01")
        assert corrected["data_folder"] == expected
        assert corrected["last_analysis_settings"]["data_folder"] == expected
        assert corrected["last_analysis_settings"]["output_dir"] == expected
        assert unresolved == []

    def test_collects_unresolved_paths(self, fake_data_roots):
        sub_info = {
            "data_folder": r"C:\old\DESI\Data\NoSuch\gone",
            "last_analysis_settings": {
                "data_folder": r"C:\old\DESI\Data\NoSuch\gone",
            },
        }
        corrected, unresolved = path_resolver.resolve_project_paths(sub_info)
        # 未解決パスは書き戻されない
        assert corrected["data_folder"].endswith("gone")
        assert any("data_folder" in msg for msg in unresolved)

    def test_ignores_empty_fields(self, fake_data_roots):
        sub_info = {
            "data_folder": "",
            "last_analysis_settings": {"data_folder": ""},
        }
        corrected, unresolved = path_resolver.resolve_project_paths(sub_info)
        assert unresolved == []

    def test_preserves_other_fields(self, fake_data_roots):
        sub_info = {
            "data_folder": r"C:\old\DESI\Data\Experiment_A\sample01",
            "project_name": "MyProject",
            "ms_instrument": "DESI",
        }
        corrected, _ = path_resolver.resolve_project_paths(sub_info)
        assert corrected["project_name"] == "MyProject"
        assert corrected["ms_instrument"] == "DESI"
