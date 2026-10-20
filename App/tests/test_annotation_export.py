"""化合物名リストの全件出力、名称の保持、参照元の一致を確認する。"""

import csv
import io
import os
import re

import pandas as pd
import pytest

from app.services import annotation_export as export
from app.services import annotation_inspect as inspect


def write_sidecar(folder, names, base="SAMPLE"):
    path = folder / f"{base}_feature_annotations.parquet"
    pd.DataFrame({
        "mz": [100.0 + i for i in range(len(names))],
        "compound": names,
        "display_name": [f"not_the_name_{i}" for i in range(len(names))],
    }).to_parquet(path, index=False)
    return path


def subproject(folder, **fields):
    return {"id": "sub-1", "name": "Sample", "data_folder": str(folder),
            "ms_instrument": "TIMS", **fields}


def preview_source(sub):
    return inspect.inspect_annotations(sub)["export_source"]


def csv_names(result):
    rows = list(csv.reader(io.StringIO(result.content.decode("utf-8-sig"), newline="")))
    assert rows[0] == ["compound"]
    assert all(len(row) == 1 for row in rows)
    return [row[0] for row in rows[1:]]


def test_all_registered_names_are_exported_beyond_preview(tmp_path):
    names = [f"Compound {i}" for i in range(275)]
    write_sidecar(tmp_path, names)
    sub = subproject(tmp_path)
    preview = inspect.inspect_annotations(sub)
    assert len(preview["examples"]) == 200
    result = export.export_compound_names(sub, preview["export_source"])
    assert result.count == 275
    assert result.content.decode("utf-8").splitlines() == names
    assert not result.content.startswith(b"\xef\xbb\xbf")
    assert result.mime_type == "text/plain;charset=utf-8"
    assert result.filename == "Sample_compound_names_unique.txt"


@pytest.mark.parametrize("deduplicate,expected", [
    (True, ["PI 38:4", "PC 34:1", "pi 38:4", "1,3,5-Triazine"]),
    (False, ["PI 38:4", "PC 34:1", "PI 38:4", "pi 38:4", "1,3,5-Triazine"]),
])
def test_filter_trim_case_and_order(tmp_path, deduplicate, expected):
    write_sidecar(tmp_path, [" PI 38:4 ", "", "No DB hit", "400.3", None,
                            "PC 34:1", "PI 38:4", "pi 38:4", "1,3,5-Triazine"])
    sub = subproject(tmp_path)
    result = export.export_compound_names(
        sub, preview_source(sub), deduplicate=deduplicate)
    assert result.content.decode("utf-8").splitlines() == expected
    assert result.count == len(expected)
    assert result.filename == f"Sample_compound_names_{'unique' if deduplicate else 'all'}.txt"


def test_csv_preserves_punctuation_unicode_and_embedded_newlines(tmp_path):
    names = ['1,3,5-Triazine', 'A "quoted" name', "β-アラニン",
             "POS_AA_Ala", "first\nsecond", "first\rsecond"]
    write_sidecar(tmp_path, names)
    sub = subproject(tmp_path)
    result = export.export_compound_names(sub, preview_source(sub), fmt="csv")
    assert result.content.startswith(b"\xef\xbb\xbf")
    assert csv_names(result) == names
    assert result.count == len(names)
    assert result.filename.endswith(".csv")
    assert result.mime_type == "text/csv;charset=utf-8"


@pytest.mark.parametrize("name", ["first\nsecond", "first\rsecond"])
def test_txt_embedded_newline_requires_csv(tmp_path, name):
    write_sidecar(tmp_path, [name])
    sub = subproject(tmp_path)
    with pytest.raises(export.AnnotationExportError, match="CSV"):
        export.export_compound_names(sub, preview_source(sub))


def test_filename_cannot_contain_path_or_control_characters(tmp_path):
    write_sidecar(tmp_path, ["Glucose"])
    sub = subproject(tmp_path, name='../CON:<test>|*?\r\n/\\.' + "a" * 200)
    result = export.export_compound_names(sub, preview_source(sub))
    assert not re.search(r'[<>:"/\\|?*\x00-\x1f\x7f]', result.filename)
    assert result.filename.endswith("_compound_names_unique.txt")
    assert len(result.filename.encode("utf-8")) <= 200


@pytest.mark.parametrize("name", ["CON.txt", "prn.csv", "Aux.data", "NUL.txt",
                                 "COM1.txt", "com9.csv", "LPT1.txt", "lpt9.csv"])
def test_windows_reserved_stem_with_extension_has_a_safe_download_name(tmp_path, name):
    write_sidecar(tmp_path, ["Glucose"])
    sub = subproject(tmp_path, name=name)
    result = export.export_compound_names(sub, preview_source(sub))
    assert result.filename == f"_{name}_compound_names_unique.txt"
    downloaded = tmp_path / result.filename
    downloaded.write_bytes(result.content)
    assert downloaded.read_bytes() == b"Glucose\n"


def test_long_japanese_download_name_fits_utf8_filename_limit(tmp_path):
    write_sidecar(tmp_path, ["β-アラニン"])
    sub = subproject(tmp_path, name="日本語の長い試料名" * 30)
    result = export.export_compound_names(sub, preview_source(sub), fmt="csv")
    assert result.filename.startswith("日本語の長い試料名")
    assert result.filename.endswith("_compound_names_unique.csv")
    assert len(result.filename.encode("utf-8")) <= 200
    downloaded = tmp_path / result.filename
    downloaded.write_bytes(result.content)
    assert downloaded.read_bytes() == result.content
    assert csv_names(result) == ["β-アラニン"]


@pytest.mark.parametrize("fmt,deduplicate", [("xlsx", True), ("txt", "yes")])
def test_invalid_options_are_rejected_before_read(tmp_path, monkeypatch, fmt, deduplicate):
    monkeypatch.setattr(export, "annotation_sidecar_identity",
                        lambda *_: pytest.fail("Invalid options must not read files"))
    with pytest.raises(export.AnnotationExportError):
        export.export_compound_names(subproject(tmp_path), {"kind": "sidecar"},
                                     fmt=fmt, deduplicate=deduplicate)


@pytest.mark.parametrize("expected", [None, {}, "../outside.parquet",
                                     {"kind": "sidecar", "path": "../outside.parquet"}])
def test_client_supplied_source_cannot_choose_a_file(tmp_path, expected):
    write_sidecar(tmp_path, ["Glucose"])
    with pytest.raises(export.AnnotationExportError):
        export.export_compound_names(subproject(tmp_path), expected)


def test_stale_preview_after_in_place_update_is_rejected(tmp_path):
    path = write_sidecar(tmp_path, ["Glucose"])
    sub = subproject(tmp_path)
    source = preview_source(sub)
    file_stat, folder_stat = path.stat(), tmp_path.stat()
    write_sidecar(tmp_path, ["Fructose"])
    os.utime(path, ns=(file_stat.st_atime_ns, file_stat.st_mtime_ns))
    os.utime(tmp_path, ns=(folder_stat.st_atime_ns, folder_stat.st_mtime_ns))
    with pytest.raises(export.AnnotationExportError, match="更新"):
        export.export_compound_names(sub, source)


@pytest.mark.parametrize("change", ["delete", "earlier_source", "corrupt"])
def test_source_deleted_switched_or_corrupted_after_preview(tmp_path, change):
    path = write_sidecar(tmp_path, ["Glucose"])
    sub = subproject(tmp_path)
    source = preview_source(sub)
    if change == "delete":
        path.unlink()
    elif change == "earlier_source":
        write_sidecar(tmp_path, ["Wrong source"], base="AAA")
    else:
        path.write_bytes(b"not parquet")
    with pytest.raises(export.AnnotationExportError):
        export.export_compound_names(sub, source)


@pytest.mark.parametrize("change", ["rewrite", "delete", "earlier_source"])
def test_source_change_during_parquet_read_is_rejected(tmp_path, monkeypatch, change):
    path = write_sidecar(tmp_path, ["Glucose"])
    sub = subproject(tmp_path)
    source = preview_source(sub)
    read_parquet = pd.read_parquet

    def changing_read(*args, **kwargs):
        result = read_parquet(*args, **kwargs)
        if change == "rewrite":
            write_sidecar(tmp_path, ["Changed"])
        elif change == "delete":
            path.unlink()
        else:
            write_sidecar(tmp_path, ["Wrong source"], base="AAA")
        return result

    monkeypatch.setattr(pd, "read_parquet", changing_read)
    with pytest.raises(export.AnnotationExportError):
        export.export_compound_names(sub, source)


def test_source_change_during_serialization_is_rejected(tmp_path, monkeypatch):
    write_sidecar(tmp_path, ["Glucose"])
    sub = subproject(tmp_path)
    source = preview_source(sub)
    names = export.compound_names

    def changing_names(*args, **kwargs):
        result = names(*args, **kwargs)
        write_sidecar(tmp_path, ["Changed"])
        return result

    monkeypatch.setattr(export, "compound_names", changing_names)
    with pytest.raises(export.AnnotationExportError, match="更新"):
        export.export_compound_names(sub, source)


@pytest.mark.parametrize("frame", [
    pd.DataFrame({"compound": ["", "No DB hit", None, "123.4"]}),
    pd.DataFrame({"compound": pd.Series([], dtype=str)}),
    pd.DataFrame({"mz": [123.4]}),
])
def test_no_usable_names_never_produces_an_empty_download(tmp_path, frame):
    path = tmp_path / "SAMPLE_feature_annotations.parquet"
    frame.to_parquet(path, index=False)
    sub = subproject(tmp_path)
    assert preview_source(sub) is None
    # Even a forged ready state with the current fingerprint cannot create an empty file.
    source = inspect.annotation_sidecar_identity(sub)
    with pytest.raises(export.AnnotationExportError, match="ありません"):
        export.export_compound_names(sub, source)


def test_corrupt_parquet_has_user_facing_error(tmp_path):
    (tmp_path / "SAMPLE_feature_annotations.parquet").write_bytes(b"broken")
    sub = subproject(tmp_path)
    assert preview_source(sub) is None
    with pytest.raises(export.AnnotationExportError, match="読み取れません"):
        export.export_compound_names(sub, inspect.annotation_sidecar_identity(sub))


def test_export_never_opens_intensity_data_or_parquet_metadata(tmp_path, monkeypatch):
    write_sidecar(tmp_path, ["Glucose"])
    (tmp_path / "large_intensity.parquet").write_bytes(b"must not be read")
    sub = subproject(tmp_path)
    source = preview_source(sub)
    read_parquet = pd.read_parquet

    def memory_only_read(path, *args, **kwargs):
        assert isinstance(path, io.BytesIO)
        return read_parquet(path, *args, **kwargs)

    monkeypatch.setattr(pd, "read_parquet", memory_only_read)
    monkeypatch.setattr(inspect, "_main_parquet_peaklist",
                        lambda *_: pytest.fail("Export must not inspect intensity data"))
    assert export.export_compound_names(sub, source).count == 1


def test_metadata_only_and_desi_have_no_export_source(tmp_path, monkeypatch):
    monkeypatch.setattr(inspect, "_main_parquet_peaklist", lambda *_: "peaks.csv")
    sub = subproject(tmp_path)
    preview = inspect.inspect_annotations(sub)
    assert preview["source_kind"] == "parquet_meta"
    assert preview["export_source"] is None
    with pytest.raises(export.AnnotationExportError):
        export.export_compound_names(sub, {"kind": "sidecar"})
    write_sidecar(tmp_path, ["Glucose"])
    desi = subproject(tmp_path, ms_instrument="DESI")
    assert inspect.annotation_sidecar_identity(desi) is None
    with pytest.raises(export.AnnotationExportError):
        export.export_compound_names(desi, {"kind": "sidecar"})
