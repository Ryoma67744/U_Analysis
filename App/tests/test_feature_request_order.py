"""★ ver66.3: 実JSでタブ識別とm/z操作順を守る（Dash rendererの実試験は別）。"""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ASSET = Path(__file__).resolve().parents[1] / "app/assets/feature_requests.js"
NODE = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const crypto = require('crypto');
const source = fs.readFileSync(process.argv[2], 'utf8');
function makeWindow(useCrypto = true) {
    const no_update = {no_update: true};
    const window = {dash_clientside: {no_update}};
    if (useCrypto) window.crypto = {randomUUID: crypto.randomUUID};
    vm.runInNewContext(source, {window});
    return {api: window.dash_clientside.feature_requests, no_update, window};
}
"""


@pytest.mark.skipif(not NODE, reason="Node が無い環境では別途実ブラウザ試験が必要")
@pytest.mark.parametrize("scenario", [
    r"""
    const a = makeWindow();
    const id = a.api.view_id('same-cookie');
    assert.strictEqual(typeof id, 'string');
    assert(id.length > 0);
    assert.strictEqual(a.api.view_id('same-cookie'), id);
    assert.strictEqual(a.api.view_id('refreshed-cookie'), id);
    const b = makeWindow();
    assert.notStrictEqual(b.api.view_id('same-cookie'), id);
    """,
    r"""
    const a = makeWindow(false); const b = makeWindow(false);
    const id = a.api.view_id();
    assert.strictEqual(typeof id, 'string'); assert(id.length > 0);
    assert.strictEqual(a.api.view_id(), id);
    assert.notStrictEqual(b.api.view_id(), id);
    """,
    r"""
    const a = makeWindow();
    for (const missing of [null, undefined, '']) {
        assert.strictEqual(a.api.intensity('mz1', 0, 100, false, missing), a.no_update);
    }
    const id = a.api.view_id();
    assert.strictEqual(a.api.intensity('mz1', 0, 100, false, id).sequence, 1);
    """,
    r"""
    const a = makeWindow(); const id = a.api.view_id();
    const inputs = [
        ['mz_123.456789012345_化合物', 0, 99.8, false],
        ['mz2', null, null, true],
        ['mz1', '1.23456789012345', '100', false]
    ];
    inputs.forEach((args, index) => {
        const request = a.api.intensity(...args, id);
        assert.strictEqual(request.sequence, index + 1);
        assert.strictEqual(request.view_id, id);
        assert.strictEqual(request.feature, args[0]);
        assert.strictEqual(request.intensity_min, args[1]);
        assert.strictEqual(request.intensity_max, args[2]);
        assert.strictEqual(request.show_names, args[3]);
    });
    const b = makeWindow(); const other = b.api.view_id();
    assert.strictEqual(b.api.intensity('mz1', null, null, false, other).sequence, 1);
    assert.strictEqual(a.api.intensity('mz1', null, null, false, id).sequence, 4);
    """,
    r"""
    const a = makeWindow(); const id = a.api.view_id();
    a.window.dash_clientside.callback_context = {triggered: [
        {prop_id: 'feature_select.value'}, {prop_id: 'feature_rows_per_view.value'}]};
    const request = a.api.intensity('mz2', 0, 100, false, id, 'S1', {S1:'name'},
                                   3, 2, {S1:{angle:90}}, '/new.rds',
                                   {rds_path:'/new.rds',dataset_revision:'revision2'});
    assert.strictEqual(request.trigger, 'feature_rows_per_view');
    assert.strictEqual(request.dataset_revision, 'revision2');
    assert.strictEqual(request.rds_path, '/new.rds');
    assert.strictEqual(request.rows, 2);
    assert.strictEqual(request.name_map.S1, 'name');
    assert.strictEqual(request.rotation.S1.angle, 90);
    const pending = a.api.intensity('mz2', 0, 100, false, id, null, {}, 0, 0, {},
                                   '/old.rds', {rds_path:'/new.rds',dataset_revision:'revision2'});
    assert.strictEqual(pending.dataset_revision, null);
    """,
], ids=["view_stable_and_isolated", "view_without_crypto", "wait_for_view", "ordered_exact_payload",
        "structural_change_and_dataset_identity"])
def test_feature_request_order_and_view_identity(scenario):
    result = subprocess.run([NODE, "-", str(ASSET)], input=HARNESS + scenario,
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
