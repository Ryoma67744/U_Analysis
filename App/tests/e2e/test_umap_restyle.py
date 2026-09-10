"""★ ver66.3: UMAP外観更新を実Plotlyで検証する（解析データは合成値）。

実行: E2E_STRICT=1 pytest tests/e2e/test_umap_restyle.py
ブラウザが無い場合は合格としない。数値・ID・ラベル位置・表示範囲を維持し、
遅れて届いた図にも最新外観を再適用することを確認する。
"""

import pytest

pytestmark = pytest.mark.e2e


def _prepare_graphs(page, scope):
    page.wait_for_function("() => window.Plotly && window.dash_clientside.umap_restyle")
    return page.evaluate(
        """async (scope) => {
            const ids = scope === 'normal'
                ? ['umap_per_sample_container', 'fs_umap_graph_container']
                : ['fs_umap_graph_container', 'umap_per_sample_container'];
            const roots = ids.map(id => {
                let root = document.getElementById(id);
                if (!root) {
                    root = document.createElement('div');
                    root.id = id;
                    document.body.appendChild(root);
                }
                return root;
            });
            const traces = [
                {type: 'scattergl', x: [-2,0], y: [-1,1], text: ['c1','c2'],
                 mode: 'markers', marker: {size: 2}, meta: 'background'},
                {type: 'scattergl', x: [2], y: [3], text: ['c3'],
                 mode: 'markers', marker: {size: 4}, meta: 'cluster1'},
                {type: 'scattergl', x: [null], y: [null], mode: 'markers',
                 marker: {size: 10}, meta: 'legend'},
                {type: 'scattergl', x: [0,2], y: [1,3], mode: 'lines+markers',
                 marker: {size: 7}, meta: 'draft'},
            ];
            const layout = {width: 350, height: 300, uirevision: 'same-data',
                xaxis: {range: [-3,4]}, yaxis: {range: [-2,5]},
                meta: {kind: 'umap', umap_style: {markers: [
                    {index: 0, delta: -1, role: 'background'},
                    {index: 1, delta: 1, role: 'highlight'},
                    {index: 2, delta: 0, role: 'legend'},
                    {index: 3, delta: 0, role: 'draft'},
                ]}},
                annotations: [
                    {name: 'umap_cluster_label', text: 'C1', x: 2.3, y: 3.4,
                     showarrow: false, font: {size: 12}},
                    {name: 'other_note', text: 'note', x: 0, y: 0,
                     showarrow: false, font: {size: 9}},
                ]};
            const div = document.createElement('div');
            const other = document.createElement('div');
            div.id = '__umap_target'; other.id = '__umap_other_scope';
            roots[0].appendChild(div); roots[1].appendChild(other);
            await window.Plotly.newPlot(div, structuredClone(traces), structuredClone(layout));
            await window.Plotly.newPlot(other, structuredClone(traces), structuredClone(layout));
            // 画面初期化の既定設定が別scopeの図へ遅れて反映される影響を分離する。
            const otherScope = scope === 'normal' ? 'fullscreen' : 'normal';
            window.dash_clientside.umap_restyle[scope](3, 12);
            window.dash_clientside.umap_restyle[otherScope](3, 12);
            for (let i = 0; i < 5; i++) await new Promise(requestAnimationFrame);
            window.__umapTest = {scope, traces, layout,
                before: JSON.stringify(div.data.map(t => ({x:t.x,y:t.y,text:t.text,meta:t.meta}))),
                otherBefore: JSON.stringify(other.data)};
            return true;
        }""", scope,
    )


def _snapshot(page):
    return page.evaluate(
        """() => {
            const gd = document.getElementById('__umap_target');
            const other = document.getElementById('__umap_other_scope');
            return {
                sizes: gd.data.map(t => t.marker.size),
                labels: gd.layout.annotations.map(a => ({size: a.font.size, x: a.x, y: a.y})),
                xrange: gd.layout.xaxis.range, yrange: gd.layout.yaxis.range,
                valuesUnchanged: window.__umapTest.before === JSON.stringify(
                    gd.data.map(t => ({x:t.x,y:t.y,text:t.text,meta:t.meta}))),
                otherUnchanged: window.__umapTest.otherBefore === JSON.stringify(other.data)
            };
        }"""
    )


@pytest.mark.parametrize("scope", ["normal", "fullscreen"])
def test_umap_style_preserves_roles_values_labels_and_zoom(page, scope):
    _prepare_graphs(page, scope)
    page.evaluate("scope => window.dash_clientside.umap_restyle[scope](8, 18)", scope)
    page.wait_for_function(
        "() => document.getElementById('__umap_target').layout.annotations[0].font.size === 18")
    result = _snapshot(page)
    assert result["sizes"] == [7, 9, 10, 7]
    assert result["labels"] == [
        {"size": 18, "x": 2.3, "y": 3.4}, {"size": 9, "x": 0, "y": 0}]
    assert result["xrange"] == [-3, 4]
    assert result["yrange"] == [-2, 5]
    assert result["valuesUnchanged"]
    assert result["otherUnchanged"]

    page.evaluate("scope => window.dash_clientside.umap_restyle[scope](1, 18)", scope)
    page.wait_for_function(
        "() => document.getElementById('__umap_target').data[1].marker.size === 2")
    assert _snapshot(page)["sizes"] == [1, 2, 10, 7]


@pytest.mark.parametrize("scope", ["normal", "fullscreen"])
def test_late_umap_figure_keeps_latest_style_without_redraw_loop(page, scope):
    _prepare_graphs(page, scope)
    page.evaluate("scope => window.dash_clientside.umap_restyle[scope](8, 18)", scope)
    page.wait_for_function(
        "() => document.getElementById('__umap_target').layout.annotations[0].font.size === 18")
    page.evaluate(
        """async () => {
            const gd = document.getElementById('__umap_target');
            window.__umapDraws = 0;
            gd.on('plotly_afterplot', () => { window.__umapDraws += 1; });
            // サーバで外観更新前に作った図が遅れて到着する状況を再現する。
            await window.Plotly.react(gd, structuredClone(window.__umapTest.traces),
                                       structuredClone(window.__umapTest.layout));
        }"""
    )
    page.wait_for_function(
        "() => document.getElementById('__umap_target').data[1].marker.size === 9 && "
        "document.getElementById('__umap_target').layout.annotations[0].font.size === 18")
    result = _snapshot(page)
    assert result["sizes"] == [7, 9, 10, 7]
    assert result["valuesUnchanged"] and result["otherUnchanged"]
    counts = page.evaluate(
        """async () => {
            for (let i = 0; i < 3; i++) await new Promise(requestAnimationFrame);
            const before = window.__umapDraws;
            for (let i = 0; i < 6; i++) await new Promise(requestAnimationFrame);
            return [before, window.__umapDraws];
        }"""
    )
    assert counts[0] == counts[1], f"再描画が停止していない: {counts}"
