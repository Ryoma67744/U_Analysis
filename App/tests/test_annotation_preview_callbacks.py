"""Tests for annotation_preview_callbacks（ver44.1 の2段化）。

open は即時（I/O なし）でモーダルを開き、populate が重い判定＋描画を担う。
ここでは Dash ctx を要さない populate 側を直接検証する（描画失敗も捕捉して
モーダル内エラー表示になり、無反応化しないこと＝本修正の要点）。
"""

import base64
from types import SimpleNamespace

import pytest
import dash_bootstrap_components as dbc
from dash import html, no_update
from dash.exceptions import PreventUpdate

import app.callbacks.annotation_preview_callbacks as apc


def test_populate_none_target_no_update():
    assert apc.populate_annotation_preview(None) is no_update


def test_populate_sub_not_found(monkeypatch):
    monkeypatch.setattr(apc, "get_sub_project", lambda p, s: None)
    out = apc.populate_annotation_preview({"project_id": "p", "sub_id": "x"})
    assert isinstance(out["body"], dbc.Alert)
    assert "見つかりません" in str(out["body"].children)
    assert out["source"] is None


def test_populate_inspect_exception_is_caught(monkeypatch):
    """inspect_annotations が例外でも populate は例外を投げず、エラー Alert を返す。"""
    monkeypatch.setattr(apc, "get_sub_project", lambda p, s: {"id": "s"})

    def boom(sub, *a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(apc, "inspect_annotations", boom)
    out = apc.populate_annotation_preview({"project_id": "p", "sub_id": "s"})
    assert isinstance(out["body"], dbc.Alert)
    assert "エラー" in str(out["body"].children) and "boom" in str(out["body"].children)
    assert out["source"] is None


def test_populate_renders_annotated(monkeypatch):
    monkeypatch.setattr(apc, "get_sub_project", lambda p, s: {"id": "s"})
    info = {
        "status": "annotated", "n_annotated": 2, "n_total": 5,
        "coverage_pct": 40.0,
        "examples": [{"compound": "PI 38:4", "mz": 760.5851}],
        "source_file": "x.parquet", "note": "",
        "export_source": {"digest": "source"}, "n_unique": 1,
    }
    monkeypatch.setattr(apc, "inspect_annotations", lambda sub, *a, **k: info)
    out = apc.populate_annotation_preview({"project_id": "p", "sub_id": "s"})
    assert isinstance(out["body"], html.Div)
    assert out["source"] == info["export_source"]
    assert out["n_annotated"] == 2 and out["n_unique"] == 1
    assert out["target"] == {"project_id": "p", "sub_id": "s"}


def test_populate_renders_none_status(monkeypatch):
    monkeypatch.setattr(apc, "get_sub_project", lambda p, s: {"id": "s"})
    monkeypatch.setattr(apc, "inspect_annotations",
                        lambda sub, *a, **k: {"status": "none", "note": ""})
    out = apc.populate_annotation_preview({"project_id": "p", "sub_id": "s"})
    assert isinstance(out["body"], html.Div)
    assert out["source"] is None


def test_desi_preview_remains_visible_but_export_unavailable(monkeypatch):
    monkeypatch.setattr(apc, "get_sub_project", lambda p, s: {"id": "s"})
    monkeypatch.setattr(apc, "inspect_annotations", lambda sub: {
        "status": "annotated", "source_kind": "desi_header",
        "n_annotated": 1, "examples": [{"compound": "GSH"}],
    })
    out = apc.populate_annotation_preview({"project_id": "p", "sub_id": "s"})
    assert isinstance(out["body"], html.Div)
    assert out["source"] is None
    assert "まだ対応していません" in out["note"]


@pytest.fixture
def ready_target():
    target = {"project_id": "p", "sub_id": "s", "nonce": "opened-once"}
    return target, {"target": target.copy(), "source": {"digest": "v1"}}


def test_download_has_no_initial_side_effect():
    with pytest.raises(PreventUpdate):
        apc.download_annotation_names(None, None, None, "txt", ["unique"])


@pytest.mark.parametrize("key", ["project_id", "sub_id", "nonce"])
def test_download_rejects_stale_ready_before_reading(monkeypatch, ready_target, key):
    target, ready = ready_target
    ready["target"][key] = "previous"
    monkeypatch.setattr(apc, "get_sub_project", lambda *_: pytest.fail("must not read stale target"))
    out = apc.download_annotation_names(1, target, ready, "txt", ["unique"])
    assert out["download"] is None
    assert "再度開いて" in out["status"]


@pytest.mark.parametrize("unique,expected", [(["unique"], True), ([], False)])
def test_download_uses_server_subproject_and_selected_options(monkeypatch, ready_target, unique, expected):
    target, ready = ready_target
    sub = {"id": "s", "name": "test", "data_folder": "/server/resolved"}
    def lookup(project_id, sub_id):
        assert (project_id, sub_id) == ("p", "s")
        return sub
    def export(actual_sub, source, fmt, deduplicate, content):
        assert actual_sub is sub
        assert source == ready["source"]
        assert fmt == "csv" and deduplicate is expected
        assert content == "names"
        return SimpleNamespace(content=b"\xef\xbb\xbfcompound\r\nGSH\r\n",
                               filename="test.csv", count=1, mime_type="text/csv")
    monkeypatch.setattr(apc, "get_sub_project", lookup)
    monkeypatch.setattr(apc, "export_compound_names", export)
    out = apc.download_annotation_names(1, target, ready, "csv", unique)
    assert out["target"] == target
    assert base64.b64decode(out["download"]["content"]).startswith(b"\xef\xbb\xbfcompound")
    assert out["download"]["filename"] == "test.csv"
    assert "1件" in out["status"]


def test_mapping_disables_name_dedup_and_reports_feature_rows(monkeypatch, ready_target):
    target, ready = ready_target
    sub = {"id": "s", "name": "mapping"}
    monkeypatch.setattr(apc, "get_sub_project", lambda *_: sub)
    def export(actual_sub, source, fmt, deduplicate, content):
        assert actual_sub is sub and source == ready["source"]
        assert content == "mz_compound" and fmt == "csv"
        assert deduplicate is False
        return SimpleNamespace(
            content=b"\xef\xbb\xbfmz,compound\r\n100.123456789,GSH\r\n101.123456789,GSH\r\n",
            filename="mapping_mz_compounds_all.csv", count=2, mime_type="text/csv",
        )
    monkeypatch.setattr(apc, "export_compound_names", export)
    out = apc.download_annotation_names(1, target, ready, "csv", ["unique"], "mz_compound")
    assert out["content"] == "mz_compound"
    assert "2行のm/z" in out["status"]
    assert base64.b64decode(out["download"]["content"]).startswith(b"\xef\xbb\xbfmz,compound")


def test_preview_exposes_mapping_counts_without_names(monkeypatch):
    target = {"project_id": "p", "sub_id": "s", "nonce": "open"}
    monkeypatch.setattr(apc, "get_sub_project", lambda *_: {"id": "s"})
    monkeypatch.setattr(apc, "inspect_annotations", lambda sub: {
        "status": "none", "source_kind": "sidecar", "n_total": 3,
        "n_annotated": 0, "n_unique": 0, "export_source": {"digest": "v1"},
        "mz_export_available": True, "mz_export_note": "",
    })
    out = apc.populate_annotation_preview(target)
    assert out["source"] == {"digest": "v1"}
    assert out["n_total"] == 3 and out["n_annotated"] == 0
    assert out["mz_export_available"] is True


def test_default_layout_offers_mz_compound_csv_and_hides_name_dedup():
    from app.layouts.annotation_preview_modal import create_annotation_preview_modal
    def components(node):
        if isinstance(node, (list, tuple)):
            for child in node:
                yield from components(child)
        elif hasattr(node, "to_plotly_json"):
            yield node
            yield from components(getattr(node, "children", None))
    fields = {node.id: node for node in components(create_annotation_preview_modal())
              if getattr(node, "id", None)}
    assert fields["annotation_export_content"].value == "mz_compound"
    assert fields["annotation_export_format"].value == "csv"
    assert fields["annotation_export_unique_section"].style["display"] == "none"


def test_download_missing_subproject_does_not_create_empty_file(monkeypatch, ready_target):
    target, ready = ready_target
    monkeypatch.setattr(apc, "get_sub_project", lambda *_: None)
    out = apc.download_annotation_names(1, target, ready, "txt", ["unique"])
    assert out["download"] is None
    assert "見つかりません" in out["status"]


def test_download_reports_source_changed_without_file(monkeypatch, ready_target):
    target, ready = ready_target
    monkeypatch.setattr(apc, "get_sub_project", lambda *_: {"id": "s"})
    def changed(*args, **kwargs):
        raise apc.AnnotationExportError("参照元が更新されました。画面を閉じて再度開いてください。")
    monkeypatch.setattr(apc, "export_compound_names", changed)
    out = apc.download_annotation_names(1, target, ready, "txt", ["unique"])
    assert out["download"] is None
    assert "更新" in out["status"]


def test_download_unexpected_error_is_visible_without_file(monkeypatch, ready_target):
    target, ready = ready_target
    monkeypatch.setattr(apc, "get_sub_project", lambda *_: {"id": "s"})
    def broken(*args, **kwargs):
        raise OSError("internal file detail")
    monkeypatch.setattr(apc, "export_compound_names", broken)
    out = apc.download_annotation_names(1, target, ready, "txt", ["unique"])
    assert out["download"] is None
    assert "出力に失敗" in out["status"]
    assert "internal file detail" not in out["status"]
