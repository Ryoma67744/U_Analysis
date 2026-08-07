"""描画パフォーマンス対策のブラウザ側検証 (ver46.1 / ver46.2)。

単体テストでは確認できないことをブラウザで確かめる。

1. `assets/relayout_filter.js` がパン/ズーム相当の relayoutData を握りつぶす
   （＝サーバ往復が発生しない）こと。アノテーション移動は通ること。
2. `assets/spatial_restyle.js` が meta タグ付きトレースだけを役割どおりに更新すること。
3. Dash のコールバック応答が gzip 圧縮されて返ること。
4. **実際に描画されるホバー文字列**が正しいこと。

4 は ver46.2 で追加した。ver46.1 で `text=<スカラー>` + `hovertemplate="%{text}"`
にした結果、ツールチップに文字列 "%{text}" がそのまま出る回帰が起きたため。
plotly.py の直列化は通る（＝Python 側のテストでは検出できない）ので、
ホバー系は必ず**描画結果**で検証する。

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
# 2b. Feature Plot の clientside restyle (ver51.3)
# ---------------------------------------------------------------------------
# Spatial と同じ「サーバ側に戻ると無音で元の重さに逆戻りする」性質があるので、
# 同じ番人を置く。対象トレースの探し方だけが違う (Spatial はトレースの meta、
# Feature は layout.meta の添字。発現トレースの meta は hover ラベルの値に
# 使っているため上書きできない)。

def test_feature_restyle_is_loaded(page):
    fns = page.evaluate(
        """() => {
            const ns = window.dash_clientside && window.dash_clientside.feature_restyle;
            return ns ? Object.keys(ns).sort() : null;
        }"""
    )
    assert fns == ["colorscale", "marker_size"]


def test_feature_restyle_updates_only_indexed_traces(page):
    """layout.meta の添字どおりに、対象トレースだけを更新すること。

    ★ TIC 背景は常に Greys。配色プルダウンで塗り替わってはいけない
      (cs に入れていないこと) を実ブラウザで固定する。
    """
    result = page.evaluate(
        """async () => {
            const host = document.querySelector('#feature_plot_container');
            if (!host || !window.Plotly) { return {error: 'no host/plotly'}; }
            const div = document.createElement('div');
            host.appendChild(div);
            await window.Plotly.newPlot(div, [
                {type: 'scattergl', x: [1,2], y: [1,2], mode: 'markers',
                 marker: {size: 3, colorscale: 'Greys'}},      // 0: TIC 背景
                {type: 'scattergl', x: [1,2], y: [1,2], mode: 'markers',
                 marker: {size: 3, colorscale: 'Plasma'}},     // 1: 発現量
            ], {meta: {kind: 'feature', auto_msz: 4.5, sz: [0, 1], cs: [1]}});

            // 別種別の図。Feature 用の操作で触ってはいけない。
            const other = document.createElement('div');
            host.appendChild(other);
            await window.Plotly.newPlot(other, [
                {type: 'scattergl', x: [1,2], y: [1,2], mode: 'markers',
                 marker: {size: 3, colorscale: 'Greys'}, meta: {dsz: 0, op: false}},
            ], {meta: {kind: 'msi', auto_msz: 9}});

            window.dash_clientside.feature_restyle.marker_size(8);
            window.dash_clientside.feature_restyle.colorscale('Viridis');
            const sizes = div.data.map(t => t.marker.size);
            // ★ plotly.py は組み立て時に名前を停止点配列へ展開するが、
            //   plotly.js は gd.data に名前のまま保持する (展開は _fullData 側)。
            //   両方の形を受けられるように正規化する。
            const first = div.data.map(t => {
                const cs = t.marker.colorscale;
                return Array.isArray(cs) ? String(cs[0][1]).toLowerCase()
                                         : String(cs).toLowerCase();
            });

            // 「自動」(0) に戻すと layout.meta.auto_msz が使われる
            window.dash_clientside.feature_restyle.marker_size(0);
            const autoSizes = div.data.map(t => t.marker.size);

            const otherSize = other.data.map(t => t.marker.size);

            host.removeChild(div); host.removeChild(other);
            return {sizes, first, autoSizes, otherSize};
        }"""
    )
    assert "error" not in result, result
    # sz:[0,1] なので両方 8
    assert result["sizes"] == [8, 8]
    # cs:[1] だけ Viridis 化。TIC 背景 (index 0) は Greys のまま
    # (名前のままなら "viridis"、展開済みなら Viridis の先頭色 #440154)
    assert result["first"][1] in ("viridis", "#440154"), result["first"]
    assert result["first"][0] not in ("viridis", "#440154"), \
        f"TIC 背景まで配色が変わっている: {result['first']}"
    # 自動 → auto_msz(4.5)
    assert result["autoSizes"] == [4.5, 4.5]
    # kind='msi' の図は Feature 用の操作では変わらない
    assert result["otherSize"] == [3]


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


# ---------------------------------------------------------------------------
# 4. ホバー表示（実際に描画される文字列を検証する）
# ---------------------------------------------------------------------------
# ver46.2: `text=<スカラー>` + `hovertemplate="%{text}"` はツールチップに
# 文字列 "%{text}" をそのまま出してしまう（plotly.py の直列化は通るので
# Python 側のテストでは検出できない）。実際に描画された文字列で検証する。

def _hover_text_of(page, trace):
    """1 トレースだけの図を描いて 1 点目にホバーし、表示された文字列を返す。"""
    pos = page.evaluate(
        """async (t) => {
            let host = document.getElementById('__hovertest');
            if (!host) {
                host = document.createElement('div');
                host.id = '__hovertest';
                host.style.position = 'fixed';
                host.style.left = '0px'; host.style.top = '0px';
                host.style.zIndex = '99999'; host.style.background = '#fff';
                document.body.appendChild(host);
            }
            await window.Plotly.newPlot(host, [t],
                {width: 400, height: 300, margin: {l: 0, r: 0, t: 0, b: 0}});
            const bb = host.getBoundingClientRect();
            return {px: bb.left + host._fullLayout._size.l + host._fullLayout.xaxis.c2p(1),
                    py: bb.top + host._fullLayout._size.t + host._fullLayout.yaxis.c2p(1)};
        }""", trace)
    page.mouse.move(0, 0)
    page.wait_for_timeout(60)
    page.mouse.move(pos["px"], pos["py"])
    page.wait_for_timeout(350)
    return page.evaluate(
        """() => {
            const b = document.querySelectorAll('.hoverlayer .hovertext');
            return b.length ? b[0].textContent : null;
        }""")


def test_hover_shows_cluster_name_not_template_literal(page):
    """Spatial のクラスタ別トレースのホバーがクラスタ名を表示すること。

    ver46.1 で `text=<スカラー>` + `%{text}` にした結果、ツールチップに
    "%{text}" がそのまま出る回帰が発生した。その形を再現し、NG であることと、
    採用した形 (hovertext + hoverinfo) が正しく出ることの両方を固定する。
    """
    base = {"type": "scattergl", "x": [1, 2], "y": [1, 2], "mode": "markers",
            "marker": {"size": 20, "symbol": "square"}}

    # 旧実装（回帰した形）: テンプレート文字列がそのまま出てしまう
    broken = dict(base, text="Cluster 7", hovertemplate="%{text}<extra></extra>")
    assert _hover_text_of(page, broken) == "%{text}"

    # 現行実装: クラスタ名がそのまま出る
    fixed = dict(base, hovertext="Cluster 7", hoverinfo="text")
    assert _hover_text_of(page, fixed) == "Cluster 7"

    # クラスタ名はユーザーが変更できる。テンプレート記法を含んでも解釈されない
    tricky = dict(base, hovertext="%{x} 領域", hoverinfo="text")
    assert _hover_text_of(page, tricky) == "%{x} 領域"


def test_hover_feature_plot_shows_intensity(page):
    """Feature Plot は WebGL 化しても強度と CellID を表示すること。"""
    trace = {"type": "scattergl", "x": [1, 2], "y": [1, 2], "mode": "markers",
             "marker": {"size": 20, "symbol": "square", "color": [0.25, 0.75],
                        "colorscale": "Plasma"},
             "text": ["cell1", "cell2"],
             "hovertemplate": "mz_100: %{marker.color:.4f}<br>%{text}<extra></extra>"}
    txt = _hover_text_of(page, trace)
    assert "0.2500" in txt and "cell1" in txt, txt
    assert "%{" not in txt, txt


def test_hover_umap_shows_cluster_via_meta(page):
    """UMAP はスカラー meta で %{meta} を解決できること（配列を作らずに済む）。"""
    trace = {"type": "scattergl", "x": [1, 2], "y": [1, 2], "mode": "markers",
             "marker": {"size": 20}, "meta": "7", "text": ["cell1", "cell2"],
             "hovertemplate": "Cluster: %{meta}<br>%{text}<extra></extra>"}
    txt = _hover_text_of(page, trace)
    assert "Cluster: 7" in txt and "cell1" in txt, txt
    assert "%{" not in txt, txt


def test_hover_hne_cluster_name_is_not_template_interpolated(page):
    """H&E オーバーレイのクラスタ名がテンプレートとして解釈されないこと。

    クラスタ名はユーザーが変更できるため、"%{x}" のような記法を含むと
    hovertemplate に直接埋めた場合は展開されてしまう。meta 経由なら値として扱われる。
    """
    tricky = "腫瘍%{x}"
    trace = {"type": "scattergl", "x": [1, 2], "y": [1, 2], "mode": "markers",
             "marker": {"size": 20}, "text": ["cell1", "cell2"],
             "meta": {"dsz": 0, "op": True, "nm": tricky},
             "hovertemplate": "Cluster: %{meta.nm}<br>%{text}<extra></extra>"}
    txt = _hover_text_of(page, trace)
    assert tricky in txt, txt

    # 直接埋め込むと展開されてしまう（＝この形は使わない、という固定）
    bad = {"type": "scattergl", "x": [1, 2], "y": [1, 2], "mode": "markers",
           "marker": {"size": 20}, "text": ["cell1", "cell2"],
           "hovertemplate": "Cluster: " + tricky + "<br>%{text}<extra></extra>"}
    assert tricky not in _hover_text_of(page, bad)


# ---------------------------------------------------------------------------
# 5. Plotly の前提の固定 (ver51.4 / A-0 調査)
# ---------------------------------------------------------------------------

def test_opacity_zero_points_still_respond_to_hover(page):
    """★ marker.opacity=0 の点でも hover は発生する。

    Feature Plot を「全点を静的に保持し、閾値未満は opacity=0」へ変える設計
    (幾何を固定して転送量を 1/6 にする案) が、**そのままでは hover 挙動を
    変えてしまう**根拠。現在は visible_mask で閾値未満の点をトレースから
    除外しているため、その位置に tooltip は出ない。

    Plotly には「トレース内の一部の点だけ hover 対象外にする」機能が無い
    (hoverinfo はトレース単位)。したがって幾何の完全固定と hover 挙動の
    厳密な維持は両立しない。

    この前提が将来 Plotly 側で変われば、このテストが落ちて設計を簡略化できる。
    """
    pos = page.evaluate(
        """async () => {
            let host = document.getElementById('__op0');
            if (!host) {
                host = document.createElement('div');
                host.id = '__op0';
                host.style.position = 'fixed'; host.style.left = '0px';
                host.style.top = '0px'; host.style.zIndex = '99999';
                host.style.background = '#fff';
                document.body.appendChild(host);
            }
            await window.Plotly.newPlot(host, [{
                type: 'scattergl', x: [1, 2], y: [1, 1], mode: 'markers',
                marker: {size: 30, opacity: [1, 0], color: ['#f00', '#00f']},
                text: ['visible_pt', 'hidden_pt'],
                hovertemplate: 'v=%{text}<extra></extra>'
            }], {width: 400, height: 200, margin: {l: 0, r: 0, t: 0, b: 0}});
            const bb = host.getBoundingClientRect();
            const L = host._fullLayout;
            return {
                vis: {px: bb.left + L._size.l + L.xaxis.c2p(1),
                      py: bb.top + L._size.t + L.yaxis.c2p(1)},
                hid: {px: bb.left + L._size.l + L.xaxis.c2p(2),
                      py: bb.top + L._size.t + L.yaxis.c2p(1)}
            };
        }"""
    )

    def _hover_at(p):
        page.mouse.move(0, 0)
        page.wait_for_timeout(80)
        page.mouse.move(p["px"], p["py"])
        page.wait_for_timeout(350)
        return page.evaluate(
            """() => {
                const b = document.querySelectorAll('.hoverlayer .hovertext');
                return b.length ? b[0].textContent : null;
            }"""
        )

    assert "visible_pt" in (_hover_at(pos["vis"]) or ""), "可視点の hover が出ない"
    hidden = _hover_at(pos["hid"]) or ""
    assert "hidden_pt" in hidden, (
        "opacity=0 の点が hover に反応しなくなった。"
        "Feature の幾何を固定しても hover 挙動を維持できるので、"
        "hovertext による「閾値未満」表示は不要にできる")


# ---------------------------------------------------------------------------
# 5. Violin の等質量ダウンサンプル (ver51.6)
# ---------------------------------------------------------------------------
# 全 spot 値ではなく等質量ビン平均 256 点を送るようにした。単体テストでは
# 「元データの統計」と「送る配列の統計」を比べているが、実際に描かれる曲線を
# 決めるのは **plotly.js 側の KDE 計算**なので、そこは実ブラウザでしか確認できない。
#
# ★ とくに帯域幅は plotly の既定が点数に依存する (n^-0.2)。渡し忘れても
#   Python 側は何も言わないが、ブラウザでは分布が目に見えてなまる。
#   ここでは plotly.js が実際に算出した密度曲線と箱ひげ統計を取り出して比べる。

def _violin_calc(page, trace):
    """violin を 1 つ描き、plotly.js が計算した密度と箱ひげ統計を返す。"""
    return page.evaluate(
        """async (t) => {
            let host = document.getElementById('__violintest');
            if (!host) {
                host = document.createElement('div');
                host.id = '__violintest';
                host.style.position = 'fixed';
                host.style.left = '0px'; host.style.top = '0px';
                host.style.zIndex = '99999'; host.style.background = '#fff';
                document.body.appendChild(host);
            }
            await window.Plotly.newPlot(host, [t], {width: 400, height: 300});
            const cd = host.calcdata[0][0];
            // lf/uf は実際に描かれるひげの端、span は KDE を評価する範囲
            // (= violin の縦の広がり)。min/max は送った配列の端であって
            // 描画位置ではないので比べない。
            return {
                mean: cd.mean, med: cd.med, q1: cd.q1, q3: cd.q3,
                lf: cd.lf, uf: cd.uf, bandwidth: cd.bandwidth,
                span: Array.from(cd.span || []),
                density: (cd.density || []).map(p => [p.t, p.v]),
            };
        }""", trace)


def _violin_fixture():
    """MSI 風 (大量のゼロ + 重い右裾) の 1 群ぶんと、その縮約版を作る。"""
    import numpy as np
    import sys
    sys.path.insert(0, "app") if "app" not in sys.path else None
    from app.utils.display_helpers import violin_equal_mass_sample

    rng = np.random.default_rng(0)
    v = np.concatenate([np.zeros(12000), rng.lognormal(3, 1.6, 8000)])
    v = np.round(v, 4)
    ys, bw, span = violin_equal_mass_sample(v)
    base = dict(type="violin", box_visible=True, meanline_visible=True,
                points=False)
    full = dict(base, y=v.tolist())
    small = dict(base, y=[float(x) for x in ys], bandwidth=float(bw),
                 span=[float(span[0]), float(span[1])], spanmode="manual")
    return full, small, v


def test_violin_downsample_keeps_the_rendered_distribution(page):
    """★ 実ブラウザで、縮約前後の KDE 曲線と箱ひげ統計が一致すること。"""
    full_tr, small_tr, v = _violin_fixture()
    axis = float(v.max() - v.min())

    full = _violin_calc(page, full_tr)
    small = _violin_calc(page, small_tr)

    assert len(small_tr["y"]) == 256, "縮約されていない"

    # 帯域幅が引き継がれていること（既定に任せると約 1.7 倍に広がる）
    assert abs(small["bandwidth"] - full["bandwidth"]) / full["bandwidth"] < 1e-6, \
        f"帯域幅が違う: {full['bandwidth']} -> {small['bandwidth']}"

    # 実際に描かれる線の位置（平均線・中央値・箱・ひげ）を軸の高さ比で見る
    for k in ("mean", "med", "q1", "q3", "lf", "uf"):
        d = abs(small[k] - full[k]) / axis
        assert d < 0.005, f"{k} が軸の {d:.2%} ずれた ({full[k]} -> {small[k]})"

    # violin の縦の広がり。span を渡し忘れると縮約後の配列の端から作られて縮む。
    for i, edge in enumerate(("下端", "上端")):
        d = abs(small["span"][i] - full["span"][i]) / axis
        assert d < 0.005, f"span の{edge}が軸の {d:.2%} ずれた"

    # 密度曲線そのもの。plotly が返す (座標, 密度) を共通格子へ寄せて比べる。
    import numpy as np
    fa, sa = np.asarray(full["density"]), np.asarray(small["density"])
    assert fa.size and sa.size, "密度が取れていない"
    grid = np.linspace(max(fa[:, 0].min(), sa[:, 0].min()),
                       min(fa[:, 0].max(), sa[:, 0].max()), 400)
    fd = np.interp(grid, fa[:, 0], fa[:, 1])
    sd = np.interp(grid, sa[:, 0], sa[:, 1])
    worst = float(np.max(np.abs(fd - sd)) / fd.max())
    assert worst < 0.02, f"密度曲線が最大 {worst:.2%} ずれた"


def test_violin_downsample_without_bandwidth_visibly_differs(page):
    """★ 上のテストが空振りでないことの番人。

    bandwidth / span を渡さずに 256 点だけ送ると、plotly の既定は点数から
    帯域幅を決める (n^-0.2) ため KDE がなまり、縦の広がりも縮む。
    「同じになる」テストは、こうして **壊した版がちゃんと落ちる**ことまで
    示さないと、実は何も検証していない可能性が残る。
    """
    import numpy as np
    full_tr, small_tr, v = _violin_fixture()
    axis = float(v.max() - v.min())

    naive = {k: val for k, val in small_tr.items()
             if k not in ("bandwidth", "span", "spanmode")}
    full = _violin_calc(page, full_tr)
    bad = _violin_calc(page, naive)

    # 帯域幅がはっきり変わる。
    # ★ 向きは分布による。plotly の既定は
    #     max(1.059*min(sd, IQR/1.349)*n^-0.2,  span/100)
    #   で、裾の重い分布では **span/100 の下限側が効く**。縮約すると配列の
    #   span が縮むので、この場合は逆に帯域幅が狭くなる (実測 36.8 -> 13.9)。
    #   どちらへ転んでも「既定任せでは別物になる」ことが示せればよい。
    rel = abs(bad["bandwidth"] - full["bandwidth"]) / full["bandwidth"]
    assert rel > 0.5, \
        f"既定の帯域幅が変わらない: {full['bandwidth']} -> {bad['bandwidth']}"

    # 縦の広がりも縮む
    assert abs(bad["span"][1] - full["span"][1]) / axis > 0.005, \
        "span を渡さなくても縦の広がりが変わらない"

    # 密度曲線もはっきりずれる
    fa, ba = np.asarray(full["density"]), np.asarray(bad["density"])
    grid = np.linspace(max(fa[:, 0].min(), ba[:, 0].min()),
                       min(fa[:, 0].max(), ba[:, 0].max()), 400)
    worst = float(np.max(np.abs(np.interp(grid, fa[:, 0], fa[:, 1])
                                - np.interp(grid, ba[:, 0], ba[:, 1]))) / fa[:, 1].max())
    assert worst > 0.02, f"壊した版が許容内に収まってしまった ({worst:.2%})"
