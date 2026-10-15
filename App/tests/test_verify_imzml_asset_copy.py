import os
from pathlib import Path
import shutil

import pytest

from tools.verify_imzml_asset_copy import compare, inventory, main


@pytest.fixture
def trees(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".ua_imzml_assets").touch()
    (source / "empty").mkdir()
    (source / "revision").mkdir()
    (source / "revision" / "sample.parquet").write_bytes(b"parquet test double")
    (source / "revision" / "conversion_complete.json").write_text("test double")
    copy = tmp_path / "copy"
    shutil.copytree(source, copy)
    return source, copy


def test_full_tree_equal_is_distinct_from_saved_result_completeness(trees):
    source, copy = trees
    before = inventory(source)
    result = compare(source, copy)
    assert result["tree_equal"]
    assert result["file_count"] == 3
    assert result["saved_result_completeness_checked"] is False
    assert inventory(source) == before


@pytest.mark.parametrize("change", ["missing", "extra", "bytes", "empty_directory"])
def test_copy_mismatch_blocks_acceptance(trees, change):
    source, copy = trees
    path = copy / "revision" / "sample.parquet"
    if change == "missing":
        path.unlink()
    elif change == "extra":
        (copy / "extra").touch()
    elif change == "bytes":
        path.write_bytes(b"changed")
    else:
        (copy / "empty").rmdir()
    assert not compare(source, copy)["tree_equal"]
    assert main(["--source", str(source), "--copy", str(copy)]) == 2


def test_empty_tree_cannot_certify_assets(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="empty tree"):
        inventory(empty)


def test_same_or_nested_tree_is_rejected(trees):
    source, _ = trees
    with pytest.raises(ValueError, match="separate"):
        compare(source, source)
    with pytest.raises(ValueError, match="separate"):
        compare(source, source / "revision")


@pytest.mark.skipif(os.name != "posix", reason="symlink creation requires POSIX test host")
def test_symlink_is_never_followed(trees, tmp_path):
    source, copy = trees
    outside = tmp_path / "outside"
    outside.write_bytes(b"not part of assets")
    (source / "link").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        compare(source, copy)
    assert outside.read_bytes() == b"not part of assets"
