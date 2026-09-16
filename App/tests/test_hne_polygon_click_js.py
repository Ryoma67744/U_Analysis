"""★ ver68.0: MSI の空白・ズーム・ドラッグを実 JS で検証する。"""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


ASSET = Path(__file__).resolve().parents[1] / "app/assets/hne_polygon_click.js"
NODE = os.environ.get("CODEX_PRIMARY_RUNTIME_NODE") or shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const listeners = {};
const sent = [];
const winListeners = {};
const gd = {
    layout: {meta: {hne_polygon_draw: true, hne_polygon_context: 'sample-A'}},
    _fullLayout: {
        width: 500, height: 400,
        xaxis: {_offset: 50, _length: 400, p2d: px => 10 + px / 4},
        yaxis: {_offset: 40, _length: 320, p2d: py => 80 - py / 4}
    },
    getBoundingClientRect: () => ({left: 100, top: 200, width: 500, height: 400}),
    contains: target => target.inside === true
};
const host = {classList: {contains: () => false}, querySelector: () => gd};
function target(options = {}) {
    return {inside: true, closest: selector => selector === '#hne_tic_graph' ? host : null,
        ...options};
}
const blank = target();
const context = {window: {PointerEvent: function () {},
    dash_clientside: {set_props: (id, props) => sent.push({id, props})},
    addEventListener: (name, handler) => {winListeners[name] = handler;}},
    document: {addEventListener: (name, handler) => {
        if (listeners[name]) throw Error('duplicate listener'); listeners[name] = handler;}}
};
vm.createContext(context);
function load() { vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), context); }
load();
function event(name, x = 350, y = 400, options = {}) {
    listeners[name]({target: blank, pointerId: 1, button: 0, isPrimary: true,
        clientX: x, clientY: y, ...options});
}
function click(x = 350, y = 400, options = {}) {
    event('pointerdown', x, y, options); event('pointerup', x, y, options);
}
function last() { return sent.at(-1).props.data; }
function xy(x, y) {
    assert.strictEqual(sent.at(-1).id, 'hne_msi_vertex_store');
    assert(Math.abs(last().x - x) < 1e-10, JSON.stringify(last()));
    assert(Math.abs(last().y - y) < 1e-10, JSON.stringify(last()));
    assert.strictEqual(last().context, 'sample-A');
}
"""


@pytest.mark.skipif(not NODE, reason="Node が無い環境では実ブラウザ試験が必要")
@pytest.mark.parametrize("scenario", [
    # データ点も clickData も無い空白から連続座標を取得する。
    "click(351, 401); xy(60.25, 39.75);",
    # Plotly が body 直下に挿入する dragcover で pointerup を受けても入力できる。
    """
    const cover = {closest: selector => selector === '.dragcover' ? {} : null};
    event('pointerdown'); event('pointerup', 350, 400, {target: cover}); xy(60, 40);
    event('pointerdown'); event('pointermove', 360, 400);
    event('pointerup', 350, 400, {target: cover});
    assert.strictEqual(sent.length, 1);
    """,
    # 軸の反転・ズーム後の p2d を毎回使う。
    """
    click(); xy(60, 40);
    gd._fullLayout.xaxis.p2d = px => 95 - px / 20;
    gd._fullLayout.yaxis.p2d = py => 12 + py / 40;
    click(350, 400); xy(85, 16);
    """,
    # CSS 縮小されても内部の Plotly 座標へ変換する。
    """
    gd.getBoundingClientRect = () => ({left: 100, top: 200, width: 250, height: 200});
    click(225, 300); xy(60, 40);
    """,
    # Graph 外枠が描画面より大きい場合、外枠幅で座標を縮めない。
    """
    gd.getBoundingClientRect = () => ({left: 100, top: 200, width: 884, height: 400});
    gd.querySelector = () => ({getBoundingClientRect:
        () => ({left: 100, top: 200, width: 500, height: 400})});
    click(350, 400); xy(60, 40);
    """,
    # 長い往復ドラッグが最初の位置に戻ってもクリックにならない。
    """
    event('pointerdown'); event('pointermove', 356, 400); event('pointerup');
    assert.strictEqual(sent.length, 0);
    event('pointerdown'); event('pointerup', 350, 406);
    assert.strictEqual(sent.length, 0);
    event('pointerdown'); event('pointermove', 353, 400); event('pointerup', 353, 400);
    xy(60.75, 40);
    """,
    # 通常モード・不明な座標系・別画像への切替途中は入力しない。
    """
    gd.layout.meta.hne_polygon_draw = false; click();
    gd.layout.meta.hne_polygon_draw = true;
    gd.layout.meta.hne_polygon_context = null; click();
    gd.layout.meta.hne_polygon_context = 'sample-A';
    event('pointerdown'); gd.layout.meta.hne_polygon_context = 'sample-B'; event('pointerup');
    assert.strictEqual(sent.length, 0);
    """,
    # 凡例、モードバー、右クリック、軸余白・図外は除外する。
    """
    const button = target({closest: selector => selector === '#hne_tic_graph' ? host : {}});
    click(350, 400, {target: button}); click(350, 400, {button: 2});
    click(350, 400, {isPrimary: false});
    click(120, 400); click(350, 220); click(580, 400); click(350, 580);
    click(350, 400, {target: target({inside: false})});
    assert.strictEqual(sent.length, 0);
    """,
    # 他の指、cancel、blur を経た操作から頂点を生成しない。
    """
    event('pointerdown'); event('pointerup', 350, 400, {pointerId: 2});
    assert.strictEqual(sent.length, 0);
    event('pointercancel'); event('pointerup');
    event('pointerdown'); winListeners.blur(); event('pointerup');
    assert.strictEqual(sent.length, 0);
    """,
    # 再読込でも同一クリックを二重送信しない。同座標の再クリックにも別 nonce。
    """
    load(); click(); click(); click();
    assert.strictEqual(sent.length, 3);
    assert.strictEqual(new Set(sent.map(row => row.props.data.nonce)).size, 3);
    """,
    # 描画未完了、非数値、非表示の図から不正な座標を送信しない。
    """
    const full = gd._fullLayout; gd._fullLayout = null; click();
    gd._fullLayout = full; full.xaxis.p2d = () => NaN; click();
    full.xaxis.p2d = px => px;
    gd.getBoundingClientRect = () => ({left: 0, top: 0, width: 0, height: 0});
    click(); assert.strictEqual(sent.length, 0);
    """,
])
def test_msi_polygon_native_coordinates(scenario):
    result = subprocess.run(
        [NODE, "-", str(ASSET)], input=HARNESS + scenario,
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


DRAFT_HARNESS = HARNESS + r"""
const dc = context.window.dash_clientside;
const nu = dc.no_update = {no_update: true};
const fig = {layout: {meta: {
    hne_polygon_draw: true, hne_polygon_context: 'sample-A-rotation-0',
    hne_sample: 'A', hne_rds_path: '/A.rds'
}}};
const state = {image: null, vertex: null, target: 'msi', rotation: {},
    sample: 'A', path: '/A.rds', mode: 'polygon', draft: [], figure: fig};
function update(id, changes = {}) {
    Object.assign(state, changes);
    dc.callback_context = {triggered: (Array.isArray(id) ? id : [id]).map(
        name => ({prop_id: name + '.data'}))};
    const result = dc.hnePolygon.updateDraft(state.image, state.vertex, 0, 0,
        state.target, state.rotation, state.sample, state.path, state.mode,
        state.draft, state.figure);
    if (result !== nu) state.draft = result;
    return result;
}
function vertex(x, y = 2, extra = {}) {
    return update('hne_msi_vertex_store', {
        vertex: {x, y, context: 'sample-A-rotation-0', nonce: x.toString(), ...extra}});
}
function equal(actual, expected) {
    assert.strictEqual(JSON.stringify(actual), JSON.stringify(expected));
}
"""


@pytest.mark.skipif(not NODE, reason="Node が無い環境では実ブラウザ試験が必要")
@pytest.mark.parametrize("scenario", [
    # Dash に最終イベントしか届かなくても、途中の頂点を全て保持する。
    # 続けて undo・clear・追加入力を行い、処理済み頂点を再生しない。
    """
    gd.layout.meta.hne_polygon_context = 'sample-A-rotation-0';
    for (let i = 0; i < 10; i++) click(300 + i, 400);
    update('hne_msi_vertex_store', {vertex: last()});
    assert.strictEqual(state.draft.vertices.length, 10);
    click(350, 400); assert.strictEqual(last().batch.length, 1);
    update('hne_msi_vertex_store', {vertex: last()});
    assert.strictEqual(state.draft.vertices.length, 11);
    update('hne_polygon_undo');
    click(351, 400); update('hne_msi_vertex_store', {vertex: last()});
    assert.strictEqual(state.draft.vertices.length, 11);
    update('hne_polygon_clear_draft');
    click(352, 400); update('hne_msi_vertex_store', {vertex: last()});
    assert.strictEqual(state.draft.vertices.length, 1);
    """,
    # ブラウザ内で頂点を順番に追加し、クリック間にサーバ応答を待たない。
    """
    for (let i = 0; i < 20; i++) vertex(i);
    equal(state.draft.vertices, Array.from({length: 20}, (_, i) => [i, 2]));
    assert.strictEqual(state.draft.coord_space, 'msi');
    assert.strictEqual(state.draft.context, 'sample-A-rotation-0');
    """,
    # Undo は座標系と画像コンテキストを保持し、クリア後は再開できる。
    """
    vertex(1); vertex(2); update('hne_polygon_undo');
    equal(state.draft.vertices, [[1, 2]]);
    assert.strictEqual(state.draft.context, 'sample-A-rotation-0');
    update('hne_polygon_clear_draft'); equal(state.draft, []);
    vertex(3); equal(state.draft.vertices, [[3, 2]]);
    """,
    # 対象を跨いだクリックや旧図から遅延して届いた入力を混ぜない。
    """
    vertex(1);
    assert.strictEqual(update('hne_image_graph', {image: {points: [{x: 9, y: 9}]}}), nu);
    assert.strictEqual(vertex(2, 2, {context: 'stale'}), nu);
    assert.strictEqual(vertex(2, 2, {context: null}), nu);
    state.figure.layout.meta.hne_sample = 'B'; assert.strictEqual(vertex(3), nu);
    state.figure.layout.meta.hne_sample = 'A';
    state.figure.layout.meta.hne_rds_path = '/B.rds'; assert.strictEqual(vertex(4), nu);
    equal(state.draft.vertices, [[1, 2]]);
    """,
    # モード変更や不正なクリック座標は下書きを変えない。
    """
    vertex(1); state.mode = 'pan'; assert.strictEqual(vertex(2), nu);
    state.mode = 'polygon'; assert.strictEqual(vertex(NaN), nu);
    assert.strictEqual(vertex(2, Infinity), nu);
    equal(state.draft.vertices, [[1, 2]]);
    """,
    # 回転・切片・解析ファイル・描画対象の変更で MSI 下書きを破棄する。
    """
    for (const id of ['hne_rotation_store', 'hne_sample_select',
                     'seurat_rds_path_store', 'hne_polygon_target']) {
        vertex(1); update(id); equal(state.draft, []);
    }
    vertex(1); update(['hne_msi_vertex_store', 'hne_sample_select']); equal(state.draft, []);
    """,
    # 古い座標系の頂点を新しい図に引き継がない。
    """
    vertex(1); state.draft.context = 'old-rotation';
    vertex(2); equal(state.draft.vertices, [[2, 2]]);
    state.draft.sample = 'B'; vertex(3); equal(state.draft.vertices, [[3, 2]]);
    state.draft.rds_path = '/B.rds'; vertex(4); equal(state.draft.vertices, [[4, 2]]);
    """,
    # 従来の H&E リスト形式の下書きは継続し、MSI 回転で消さない。
    """
    state.target = 'hne'; state.draft = [[1, 1]];
    assert.strictEqual(update('hne_rotation_store'), nu);
    update('hne_image_graph', {image: {points: [{x: 3, y: 4}]}});
    equal(state.draft.vertices, [[1, 1], [3, 4]]);
    assert.strictEqual(state.draft.coord_space, 'hne');
    assert.strictEqual(vertex(2), nu);
    update('hne_polygon_undo'); equal(state.draft.vertices, [[1, 1]]);
    """,
    # MSI を選んだ時点で残っていた H&E 下書きを流用しない。
    """
    state.draft = [[99, 99]]; vertex(1); equal(state.draft.vertices, [[1, 2]]);
    state.draft = [[99, 99]]; update('hne_polygon_undo'); equal(state.draft, []);
    """,
])
def test_polygon_draft_client_updates(scenario):
    result = subprocess.run(
        [NODE, "-", str(ASSET)], input=DRAFT_HARNESS + scenario,
        text=True, capture_output=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
