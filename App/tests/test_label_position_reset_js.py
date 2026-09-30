"""★ ver76.0: 実JSで部分ドラッグ座標補完と表示中の図だけの自動復帰を検証する。"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest


NODE = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")
ASSETS = Path(__file__).resolve().parents[1] / "app" / "assets"


@pytest.mark.skipif(not NODE, reason="Nodeが必要です")
def test_actual_javascript_preserves_scope_and_limits_reset_targets(tmp_path):
    script = tmp_path / "label-reset.cjs"
    script.write_text(r"""
const assert = require('assert');
const fs = require('fs');
const vm = require('vm');
const path = require('path');
const nu = {};
const scope = {rds_path: 'A.rds', method: 'Harmony', load_token: 'load-1', revision: 3,
    kind: 'umap', section: 'umap_integrated', labels: [{index: 0, cluster: '7'}]};
const gd = {layout: {meta: {label_scope: scope}, annotations: [{text: 'renamed', x: 1, y: 2}]},
    getClientRects: () => [1]};
const host = {classList: {contains: () => false}, querySelector: () => gd,
    querySelectorAll: () => [gd], getClientRects: () => [1]};
const hidden = {getClientRects: () => [], querySelectorAll: () => {throw Error('hidden plot read');}};
const nodes = {interactive_umap_plot: host, umap_per_sample_container: hidden};
const dc = {no_update: nu, callback_context: {triggered: [{prop_id: 'interactive_umap_plot.relayoutData',
    value: {'annotations[0].x': 9}}]}};
const context = {window: {dash_clientside: dc}, document: {getElementById: id => nodes[id]}};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(process.argv[2], 'relayout_filter.js'), 'utf8'), context);
vm.runInContext(fs.readFileSync(path.join(process.argv[2], 'label_positions.js'), 'utf8'), context);
let got = dc.relayout.filter_annotations();
assert.deepStrictEqual(JSON.parse(JSON.stringify(got.positions)), {'7': {x: 9, y: 2}});
assert.strictEqual(got.label_scope.revision, 3);
assert.strictEqual(got.label_scope.section, 'umap_integrated');
scope.kind = 'hne';
assert.strictEqual(dc.relayout.filter_annotations(), nu);
scope.kind = 'umap';
dc.callback_context.triggered = [{prop_id: '{"type":"reset_cluster_labels","view":"umap"}.n_clicks', value: 1}];
got = dc.label_positions.request_reset([1], 'A.rds', 'load-1');
assert.strictEqual(got.targets.length, 1);
assert.deepStrictEqual(JSON.parse(JSON.stringify(got.targets[0].clusters)), ['7']);
assert.strictEqual(got.targets[0].revision, 3);
assert.strictEqual(dc.label_positions.request_reset([1], 'B.rds', 'load-1').targets.length, 0);
assert.strictEqual(dc.label_positions.request_reset([1], 'A.rds', 'load-2').targets.length, 0);
scope.kind = 'hne';
assert.strictEqual(dc.label_positions.request_reset([1], 'A.rds', 'load-1').targets.length, 0);
""", encoding="utf-8")
    result = subprocess.run([NODE, str(script), str(ASSETS)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
