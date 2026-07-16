"""add_molinfo_callbacks の preview/confirm（ctx 不要な部分）を検証（ver45.0）。

open はパターンマッチ ctx を要するため対象外。preview/confirm は get_sub_project /
attach_molecular_info をモンキーパッチして、UI 応答（プレビュー件数・実行ボタン活性/非活性・
カード再描画のインクリメント）を確認する。
"""

import base64

import dash_bootstrap_components as dbc
from dash import no_update

import app.callbacks.add_molinfo_callbacks as amc

_CONTENTS = "data:text/csv;base64," + base64.b64encode(b"m/z;Name\n1;A").decode()
_TARGET = {"project_id": "p", "sub_id": "s", "nonce": 1}


def _patch(monkeypatch, result):
    monkeypatch.setattr(amc, "get_sub_project", lambda p, s: {"id": "s", "data_folder": "/x"})
    monkeypatch.setattr(amc, "attach_molecular_info", lambda sub, path, **k: result)


def test_preview_enables_confirm_on_match(monkeypatch):
    _patch(monkeypatch, {"status": "preview", "n_features": 100, "n_peaklist": 90,
                         "n_matched": 80, "sidecar_paths": [], "base": "B"})
    body, disabled = amc.preview_add_molinfo(_CONTENTS, "f.csv", _TARGET)
    assert disabled is False
    assert isinstance(body, dbc.Alert)


def test_preview_zero_match_keeps_disabled(monkeypatch):
    _patch(monkeypatch, {"status": "preview", "n_features": 100, "n_peaklist": 90,
                         "n_matched": 0, "sidecar_paths": [], "base": "B"})
    body, disabled = amc.preview_add_molinfo(_CONTENTS, "f.csv", _TARGET)
    assert disabled is True


def test_preview_no_contents_noop(monkeypatch):
    out = amc.preview_add_molinfo(None, None, _TARGET)
    assert out == (no_update, no_update)


def test_preview_error_is_caught(monkeypatch):
    monkeypatch.setattr(amc, "get_sub_project", lambda p, s: {"id": "s"})

    def boom(sub, path, **k):
        raise ValueError("bad csv")

    monkeypatch.setattr(amc, "attach_molecular_info", boom)
    body, disabled = amc.preview_add_molinfo(_CONTENTS, "f.csv", _TARGET)
    assert isinstance(body, dbc.Alert) and disabled is True


def test_confirm_writes_and_refreshes(monkeypatch):
    _patch(monkeypatch, {"status": "ok", "n_features": 100, "n_peaklist": 90,
                         "n_matched": 80, "sidecar_paths": ["/x/B_feature_annotations.parquet"],
                         "base": "B"})
    body, disabled, refresh = amc.confirm_add_molinfo(1, _CONTENTS, "f.csv", _TARGET, 3)
    assert isinstance(body, dbc.Alert)
    assert disabled is True
    assert refresh == 4                      # (3 or 0) + 1 でカード再描画


def test_confirm_error_is_caught(monkeypatch):
    monkeypatch.setattr(amc, "get_sub_project", lambda p, s: {"id": "s"})

    def boom(sub, path, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(amc, "attach_molecular_info", boom)
    body, disabled, refresh = amc.confirm_add_molinfo(1, _CONTENTS, "f.csv", _TARGET, 3)
    assert isinstance(body, dbc.Alert)
    assert refresh is no_update              # 失敗時はカード再描画しない
