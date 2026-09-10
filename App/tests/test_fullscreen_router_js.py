"""★ ver66.3: 全画面ルータの逆順応答・閉鎖・ラベル保存先を実 JS で検証する。

Node VM は Dash renderer 自体を動かさないため、実ブラウザ試験は別途必要。
"""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ASSET = Path(__file__).resolve().parents[1] / "app/assets/fullscreen_router.js"
NODE = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const nu = {no_update: true};
const sent = [];
const dc = {no_update: nu, callback_context: {triggered: []},
    set_props: (id, props) => sent.push({id, props}),
    relayout: {filter_annotations: () => ({relayout: {'annotations[0].x': 2},
                                         triggered_id: 'fs_umap_integrated_graph'})}};
const context = {window: {dash_clientside: dc}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(require('path').join(require('path').dirname(process.argv[2]),
                 'relayout_filter.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context);
const router = context.window.dash_clientside.fullscreen_router;
const state = {isOpen: false, light: null, feature: null, deg: null,
               rds: '/A.rds', load: 'load-A', empty: [{placeholder: true}]};
function fire(changes, updates = {}) {
    Object.assign(state, updates);
    dc.callback_context.triggered = changes.map(prop_id => ({prop_id}));
    return router.route(1, 1, 1, 1, state.isOpen, state.light, state.feature,
                        state.deg, state.rds, state.load, state.empty);
}
function unchanged(out) { assert(out.every(value => value === nu)); }
function open(kind) {
    unchanged(fire(['expand_' + kind + '_btn.n_clicks']));
    const lane = ['umap', 'spatial'].includes(kind) ? 'light' : kind;
    return sent.filter(x => x.id === 'fullscreen_request_' + lane + '_store').at(-1).props.data;
}
function response(request, options = {}) {
    const lane = ['umap', 'spatial'].includes(request.kind) ? 'light' : request.kind;
    const value = {...request, is_open: true, title: request.kind, body: {token: request.token}, ...options};
    return fire(['fullscreen_response_' + lane + '_store.data'], {[lane]: value});
}
"""


@pytest.mark.skipif(not NODE, reason="Node が無い環境では別途実ブラウザ試験が必要")
@pytest.mark.parametrize("scenario", [
    r"""
    for (const kind of ['umap', 'spatial', 'feature', 'deg']) {
        const request = open(kind);
        const out = response(request);
        assert.strictEqual(out[0], true); assert.strictEqual(out[1], kind);
        assert.strictEqual(out[2].token, request.token);
        unchanged(fire(['fullscreen_plot_modal.is_open'], {isOpen: false}));
    }
    """,
    r"""
    const a = open('umap'); const b = open('spatial');
    assert.notStrictEqual(a.token, b.token);
    unchanged(response(a));
    assert.strictEqual(response(b)[1], 'spatial');
    unchanged(response(a));
    """,
    r"""
    const a = open('umap'); const b = open('feature');
    assert.strictEqual(response(b)[1], 'feature');
    unchanged(response(a));
    """,
    r"""
    const a = open('umap'); response(a);
    const b = open('spatial');
    unchanged(fire(['fullscreen_plot_modal.is_open'], {isOpen: false}));
    unchanged(response(b)); unchanged(response(a));
    """,
    r"""
    const a = open('umap'); response(a);
    const b = open('spatial');
    unchanged(fire(['fullscreen_response_light_store.data', 'fullscreen_plot_modal.is_open'],
                   {isOpen: false, light: {...b, is_open: true, title: 'spatial'}}));
    unchanged(response(b));
    """,
    r"""
    const a = open('umap'); response(a);
    const out = fire(['seurat_rds_path_store.data'], {rds: '/B.rds', isOpen: true});
    assert.strictEqual(out[0], false); assert.strictEqual(out[2], state.empty);
    unchanged(response(a));
    unchanged(fire(['seurat_rds_path_store.data'], {rds: '/C.rds', isOpen: false}));
    """,
    r"""
    const a = open('umap'); response(a);
    const out = fire(['load_token_store.data'], {load: 'load-A2', isOpen: true});
    assert.strictEqual(out[0], false); assert.strictEqual(out[2], state.empty);
    unchanged(response(a));
    """,
    r"""
    const a = open('umap'); unchanged(response(a, {is_open: false}));
    unchanged(fire(['fullscreen_response_light_store.data'], {light: null}));
    assert.strictEqual(sent.filter(x => x.id.includes('request')).length, 1);
    """,
    r"""
    const a = open('umap'); response(a); const before = sent.length;
    const b = open('feature');
    assert(sent.slice(before).some(x => x.id === 'fullscreen_response_light_store' && x.props.data === null));
    unchanged(fire(['fullscreen_response_light_store.data'], {light: null}));
    assert.strictEqual(response(b)[1], 'feature');
    """,
    r"""
    const a = open('umap'); response(a);
    dc.callback_context.triggered = [{prop_id: 'fs_umap_integrated_graph.relayoutData',
                                     value: {'annotations[0].font.size': 20}}];
    assert.strictEqual(router.filter_annotations({}, [], '/A.rds', 'load-A'), nu);
    dc.callback_context.triggered = [{prop_id: 'fs_umap_integrated_graph.relayoutData',
                                     value: {'annotations[0].x': 2}}];
    const signal = router.filter_annotations({}, [], '/A.rds', 'load-A');
    assert.strictEqual(signal.fullscreen_scope.token, a.token);
    assert.strictEqual(router.filter_annotations({}, [], '/B.rds', 'load-A'), nu);
    assert.strictEqual(router.filter_annotations({}, [], '/A.rds', 'load-A2'), nu);
    fire(['fullscreen_plot_modal.is_open'], {isOpen: false});
    assert.strictEqual(router.filter_annotations({}, [], '/A.rds', 'load-A'), nu);
    """,
])
def test_fullscreen_router_rejects_stale_updates(scenario):
    result = subprocess.run([NODE, "-", str(ASSET)], input=HARNESS + scenario,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
