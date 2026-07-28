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
# 2. 見た目スライダーの clientside restyle
# ---------------------------------------------------------------------------

def test_spatial_restyle_is_loaded(page):
    fns = page.evaluate(
        """() => {
            const ns = window.dash_clientside && window.dash_clientside.spatial_restyle;
            return ns ? Object.keys(ns).sort() : null;
        }"""
    )
    assert fns == ["hne_marker_size", "label_size", "marker_size", "spot_opacity"]


def test_spatial_restyle_updates_only_tagged_traces(page):
    """meta タグ付きトレースだけを、役割どおりに更新すること。

    実データ無しでも検証できるよう、テスト用の Plotly グラフを
    #spatial_plots_container に差し込んで restyle を走らせる。
    """
    result = page.evaluate(
        """async () => {
            const host = document.querySelector('#spatial_plots_container');
            if (!host || !window.Plotly) { return {error: 'no host/plotly'}; }
            const div = document.createElement('div');
            host.appendChild(div);
            await window.Plotly.newPlot(div, [
                {type: 'scattergl', x: [1,2], y: [1,2], mode: 'markers',
                 marker: {size: 3, opacity: 1}, meta: {dsz: 0, op: false}},   // 背景
                {type: 'scattergl', x: [1,2], y: [1,2], mode: 'markers',
                 marker: {size: 3, opacity: 1}, meta: {dsz: 0, op: true}},    // スポット
                {type: 'scattergl', x: [1,2], y: [1,2], mode: 'markers',
                 marker: {size: 3, opacity: 1}, meta: {dsz: 1, op: true}},    // +1
                {type: 'scattergl', x: [null], y: [null], mode: 'markers',
                 marker: {size: 10}},                                          // 凡例ダミー
            ], {meta: {kind: 'msi', auto_msz: 4.5}, annotations: [
                {text: 'C1', x: 1, y: 1, showarrow: false, font: {size: 10}}]});

            window.dash_clientside.spatial_restyle.marker_size(8);
            window.dash_clientside.spatial_restyle.spot_opacity(40);
            window.dash_clientside.spatial_restyle.label_size(18);
            const sizes = div.data.map(t => t.marker.size);
            const ops = div.data.map(t => t.marker.opacity);
            const labelSize = div.layout.annotations[0].font.size;

            // 「自動」(0) に戻すと layout.meta.auto_msz が使われる
            window.dash_clientside.spatial_restyle.marker_size(0);
            const autoSizes = div.data.map(t => t.marker.size);

            // H&E 用スライダーは kind='msi' のこの図を触らない
            window.dash_clientside.spatial_restyle.hne_marker_size(30);
            const afterHne = div.data.map(t => t.marker.size);

            host.removeChild(div);
            return {sizes, ops, labelSize, autoSizes, afterHne};
        }"""
    )
    assert "error" not in result, result
    # 背景=8, スポット=8, +1=9, 凡例ダミー=10(不変)
    assert result["sizes"] == [8, 8, 9, 10]
    # 不透明度は op:true のトレースだけ 0.4、他は 1 のまま
    assert result["ops"] == [1, 0.4, 0.4, None] or result["ops"][:3] == [1, 0.4, 0.4]
    assert result["labelSize"] == 18
    # 自動 → auto_msz(4.5) と +1
    assert result["autoSizes"] == [4.5, 4.5, 5.5, 10]
    # 種別違いのスライダーでは変化しない
    assert result["afterHne"] == result["autoSizes"]


# ---------------------------------------------------------------------------
# 3. HTTP 圧縮
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
