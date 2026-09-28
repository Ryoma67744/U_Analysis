"""★ ver75.2: 永続化漏れで資産が消えた場合を、切片選択の不備と区別する。"""
from pathlib import Path
import shutil

import pytest

from app.services import input_preparation as ip
from app.services import section_group_metadata as gm
from tests.test_imzml_autoimport_core import prep, source


@pytest.mark.parametrize("missing,labels", [
    ("all", ["変換完了記録", "変換済みParquet", "変換対応情報"]),
    ("receipt", ["変換完了記録"]),
    ("parquet", ["変換済みParquet"]),
    ("manifest", ["変換対応情報"]),
])
def test_export_identifies_missing_assets_without_replacing_input(tmp_path, monkeypatch, missing, labels):
    raw = source(tmp_path / "raw")
    entry = prep(raw, tmp_path / "assets")
    root = Path(entry["runtime_path"]).parent
    files = {"receipt": root / ip.RECEIPT, "parquet": Path(entry["runtime_path"]),
             "manifest": Path(entry["conversion_manifest_path"])}
    if missing == "all":
        shutil.rmtree(root)
    else:
        files[missing].unlink()
    # 同名の別Parquetがあっても、欠けた解析時の入力の代用品にしない。
    replacement = raw.with_suffix(".parquet")
    replacement.write_bytes(b"different source")
    before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    monkeypatch.setattr(gm, "load_result_manifest", lambda _: {"files": [entry]})
    with pytest.raises(ip.InputPreparationError) as caught:
        gm.selected_input_paths("result.rds", {".parquet"}, source_file_ids={entry["file_id"]})
    message = str(caught.value)
    assert raw.name in message
    assert "切片の選択内容の問題ではありません" in message
    assert "復元してから再出力" in message
    assert "（不足: " + "、".join(labels) + "）" in message
    assert str(root) in message
    after = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert before == after


def test_broken_receipt_is_still_rejected(tmp_path):
    entry = prep(source(tmp_path / "raw"), tmp_path / "assets")
    (Path(entry["runtime_path"]).parent / ip.RECEIPT).write_text("invalid json")
    with pytest.raises(ip.InputPreparationError, match="完成済み変換資産を検証できません"):
        ip.validate_asset(entry)
