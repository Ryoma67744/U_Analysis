"""Tests for annotation_preview_callbacks（ver44.1 の2段化）。

open は即時（I/O なし）でモーダルを開き、populate が重い判定＋描画を担う。
ここでは Dash ctx を要さない populate 側を直接検証する（描画失敗も捕捉して
モーダル内エラー表示になり、無反応化しないこと＝本修正の要点）。
"""

import dash_bootstrap_components as dbc
from dash import html, no_update

import app.callbacks.annotation_preview_callbacks as apc


def test_populate_none_target_no_update():
    assert apc.populate_annotation_preview(None) is no_update


def test_populate_sub_not_found(monkeypatch):
    monkeypatch.setattr(apc, "get_sub_project", lambda p, s: None)
    out = apc.populate_annotation_preview({"project_id": "p", "sub_id": "x"})
    assert isinstance(out, dbc.Alert)
    assert "見つかりません" in str(out.children)


def test_populate_inspect_exception_is_caught(monkeypatch):
    """inspect_annotations が例外でも populate は例外を投げず、エラー Alert を返す。"""
    monkeypatch.setattr(apc, "get_sub_project", lambda p, s: {"id": "s"})

    def boom(sub, *a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(apc, "inspect_annotations", boom)
    out = apc.populate_annotation_preview({"project_id": "p", "sub_id": "s"})
    assert isinstance(out, dbc.Alert)
    assert "エラー" in str(out.children) and "boom" in str(out.children)


def test_populate_renders_annotated(monkeypatch):
    monkeypatch.setattr(apc, "get_sub_project", lambda p, s: {"id": "s"})
    info = {
        "status": "annotated", "n_annotated": 2, "n_total": 5,
        "coverage_pct": 40.0,
        "examples": [{"compound": "PI 38:4", "mz": 760.5851}],
        "source_file": "x.parquet", "note": "",
    }
    monkeypatch.setattr(apc, "inspect_annotations", lambda sub, *a, **k: info)
    out = apc.populate_annotation_preview({"project_id": "p", "sub_id": "s"})
    assert isinstance(out, html.Div)          # エラー Alert ではなく通常描画


def test_populate_renders_none_status(monkeypatch):
    monkeypatch.setattr(apc, "get_sub_project", lambda p, s: {"id": "s"})
    monkeypatch.setattr(apc, "inspect_annotations",
                        lambda sub, *a, **k: {"status": "none", "note": ""})
    out = apc.populate_annotation_preview({"project_id": "p", "sub_id": "s"})
    assert isinstance(out, html.Div)
