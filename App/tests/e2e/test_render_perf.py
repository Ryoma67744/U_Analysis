"""描画パフォーマンス対策のブラウザ側検証 (ver46.1)。

単体テストでは確認できない 2 点をブラウザで確かめる。

1. `assets/relayout_filter.js` が読み込まれ、パン/ズーム相当の relayoutData を
   握りつぶす（＝サーバ往復が発生しない）こと。アノテーション移動は通ること。
2. Dash のコールバック応答が gzip 圧縮されて返ること。

実行: `pytest tests/e2e/test_render_perf.py`（playwright + Chromium が必要）。
"""

import gzip
import json
import urllib.request

import pytest

pytestmark = pytest.mark.e2e


# ---------------------------------------------------------------------------
# 1. clientside relayout フィルタ
# ---------------------------------------------------------------------------

def test_relayout_filter_is_loaded(page):
    """assets/ の JS がロードされ、関数が公開されていること。"""
    assert page.evaluate(
        "() => typeof (window.dash_clientside"
        " && window.dash_clientside.relayout"
        " && window.dash_clientside.relayout.filter_annotations)") == "function"


def test_relayout_filter_suppresses_pan_and_zoom(page):
    """パン/ズーム由来の relayoutData は no_update になる（サーバへ行かない）。"""
    suppressed = page.evaluate(
        """() => {
            const f = window.dash_clientside.relayout.filter_annotations;
            const NU = window.dash_clientside.no_update;
            const samples = [
                // ドラッグによるパン
                {"xaxis.range[0]": 1, "xaxis.range[1]": 9,
                 "yaxis.range[0]": 2, "yaxis.range[1]": 8},
                // ホイールズーム
                {"xaxis.range[0]": 0.5, "xaxis.range[1]": 4.5},
                // ダブルクリックによるオートスケール
                {"xaxis.autorange": true, "yaxis.autorange": true},
                // 何も無い/壊れた入力
                {}, null, undefined,
            ];
            return samples.every(rd => f(rd) === NU);
        }"""
    )
    assert suppressed, "パン/ズームがサーバへ転送されてしまう"


def test_relayout_filter_passes_annotation_drag(page):
    """アノテーション（クラスタラベル）移動は通す = 位置保存が壊れない。"""
    result = page.evaluate(
        """() => {
            const f = window.dash_clientside.relayout.filter_annotations;
            const NU = window.dash_clientside.no_update;
            // ラベルドラッグ時に Plotly が出す形。パン成分が同時に入ることもある。
            const rd = {"annotations[2].x": 12.5, "annotations[2].y": -3.25,
                        "xaxis.range[0]": 1};
            const out = f(rd);
            if (out === NU) { return {passed: false}; }
            return {passed: true, relayout: out.relayout, seq: out.seq};
        }"""
    )
    assert result["passed"], "アノテーション移動が握りつぶされている"
    assert result["relayout"]["annotations[2].x"] == 12.5
    assert result["relayout"]["annotations[2].y"] == -3.25


def test_relayout_filter_handles_pattern_matching_lists(page):
    """ALL パターンの Input は配列で届く。配列内のアノテーション移動を拾えること。"""
    ok = page.evaluate(
        """() => {
            const f = window.dash_clientside.relayout.filter_annotations;
            const NU = window.dash_clientside.no_update;
            // タイル 3 枚のうち 2 枚目だけがラベル移動
            const list = [{"xaxis.range[0]": 1}, {"annotations[0].y": 7}, null];
            const out = f(null, list, []);
            return out !== NU && out.relayout["annotations[0].y"] === 7;
        }"""
    )
    assert ok


def test_relayout_filter_seq_is_monotonic(page):
    """同じ座標へ戻しても Store の変化として検知できるよう seq が単調増加すること。"""
    a, b = page.evaluate(
        """() => {
            const f = window.dash_clientside.relayout.filter_annotations;
            const rd = {"annotations[0].x": 1};
            return [f(rd).seq, f(rd).seq];
        }"""
    )
    assert b > a


# ---------------------------------------------------------------------------
# 2. HTTP 圧縮
# ---------------------------------------------------------------------------

def test_dash_callback_response_is_gzipped(app_server):
    """コールバック応答 (figure JSON) が gzip で返ること。

    figure JSON は数値の羅列で 8〜12 倍に縮む。ver46.1 以前は無圧縮だった。
    """
    base, _ = app_server
    # 認証不要かつ必ず存在する軽量コールバック経路として dash-layout を使う。
    # （/_dash-layout は Dash が常に提供する GET エンドポイント）
    req = urllib.request.Request(
        base + "/_dash-layout",
        headers={"Accept-Encoding": "gzip", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            encoding = r.headers.get("Content-Encoding", "")
            body = r.read()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"/_dash-layout を取得できない（認証設定依存）: {e}")

    assert encoding == "gzip", (
        f"Content-Encoding={encoding!r}。flask-compress / compress=True の設定を確認")
    # 実際に展開でき、圧縮が効いていること
    raw = gzip.decompress(body)
    json.loads(raw)
    assert len(body) < len(raw), f"compressed={len(body)} raw={len(raw)}"
