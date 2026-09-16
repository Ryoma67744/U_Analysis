/* ★ ver68.0: Plotly の clickData は描画済みの点に吸着し、MSI の空白部分を
 * 頂点にできなかった。軸の現在の変換で実クリック位置を MSI 座標に戻す。
 */
(function () {
    "use strict";
    // Dash の動的タブ・再描画でもリスナーを重複登録しない。
    if (window.__hnePolygonClickInstalled) return;
    window.__hnePolygonClickInstalled = true;

    // 微小な手ぶれは許容し、5 CSS px を超えた移動はパンとして扱う。
    // 回帰試験では 3 px のクリックと 6 px の往復ドラッグを区別する。
    var MOVE_LIMIT = 5;
    var active = null;
    var sequence = 0;
    var pending = [];
    var pendingContext = null;

    function resetPending() { pending = []; pendingContext = null; }

    function consumePending(vertex) {
        if (pendingContext !== vertex.context) return;
        var last = pending.findIndex(function (item) { return item.nonce === vertex.nonce; });
        if (last >= 0) pending = pending.slice(last + 1);
    }

    // ★ ver68.0: サーバに下書きを往復させると、連続クリックが同じ古い State
    // を読み、先の頂点が消える。下書きの更新はブラウザ内で順に完了させる。
    var clients = window.dash_clientside = window.dash_clientside || {};
    clients.hnePolygon = clients.hnePolygon || {};
    clients.hnePolygon.updateDraft = function (imageClick, msiVertex, undo, clear,
            target, rotation, sample, rdsPath, mode, draft, ticFigure) {
        var dc = window.dash_clientside;
        var triggered = dc.callback_context && dc.callback_context.triggered || [];
        var ids = triggered.map(function (entry) { return entry.prop_id.split(".")[0]; });
        var id = ids[0];
        var nu = dc.no_update;
        var space = target || "hne";
        // 同じ処理回でクリックと画像切替が届いても、切替を優先する。
        if (ids.some(function (item) {
            return item === "hne_polygon_target" || item === "hne_sample_select" ||
                item === "seurat_rds_path_store" || item === "hne_polygon_clear_draft";
        })) { resetPending(); return []; }
        if (id === "hne_rotation_store") {
            if (space === "msi") { resetPending(); return []; }
            return nu;
        }

        var legacy = Array.isArray(draft);
        var old = legacy ? {coord_space: "hne", vertices: draft} : (draft || {});
        var compatible = old.coord_space === space &&
            (legacy || (old.sample === sample && old.rds_path === rdsPath));
        if (id === "hne_polygon_undo") {
            resetPending();
            if (!compatible || !Array.isArray(old.vertices)) return [];
            return legacy ? old.vertices.slice(0, -1) :
                Object.assign({}, old, {vertices: old.vertices.slice(0, -1)});
        }
        if (mode !== "polygon" || (space !== "hne" && space !== "msi")) {
            resetPending();
            return nu;
        }

        var x, y, context;
        if (id === "hne_msi_vertex_store" && space === "msi") {
            var meta = ticFigure && ticFigure.layout && ticFigure.layout.meta;
            if (!msiVertex || !meta || !meta.hne_polygon_draw ||
                    !msiVertex.context || msiVertex.context !== meta.hne_polygon_context ||
                    meta.hne_sample !== sample || meta.hne_rds_path !== rdsPath) return nu;
            x = msiVertex.x;
            y = msiVertex.y;
            context = msiVertex.context;
            compatible = compatible && old.context === context;
        } else if (id === "hne_image_graph" && space === "hne") {
            var point = imageClick && imageClick.points && imageClick.points[0];
            if (!point) return nu;
            x = point.x;
            y = point.y;
        } else return nu;
        if (typeof x !== "number" || typeof y !== "number" ||
                !Number.isFinite(x) || !Number.isFinite(y)) return nu;
        var vertices = compatible && Array.isArray(old.vertices) ? old.vertices.slice() : [];
        // Dash は短時間の Store 更新をまとめるため、実ブラウザでは clientside
        // callback でも単発イベントが間引かれた。未処理の全頂点をまとめて受け取る。
        var batch = space === "msi" && Array.isArray(msiVertex.batch) ? msiVertex.batch : null;
        if (batch) {
            var seen = compatible ? batch.findIndex(function (item) {
                return item.nonce === old.msi_last_nonce;
            }) : -1;
            batch.slice(seen + 1).forEach(function (item) {
                if (typeof item.x === "number" && typeof item.y === "number" &&
                        Number.isFinite(item.x) && Number.isFinite(item.y)) {
                    vertices.push([item.x, item.y]);
                }
            });
        } else vertices.push([x, y]);
        var next = {coord_space: space, vertices: vertices, sample: sample, rds_path: rdsPath};
        if (space === "msi") {
            next.context = context;
            next.msi_last_nonce = msiVertex.nonce;
            consumePending(msiVertex);
        }
        return next;
    };

    function metadata(gd) {
        return gd && gd.layout && gd.layout.meta;
    }

    function enabled(gd) {
        var meta = metadata(gd);
        return !!(meta && meta.hne_polygon_draw === true &&
            typeof meta.hne_polygon_context === "string" && meta.hne_polygon_context);
    }

    function graphFor(target) {
        if (!target || typeof target.closest !== "function") return null;
        // モードバー・凡例等がプロットの上に重なっていても頂点にはしない。
        if (target.closest(".modebar, .legend, .rangeslider-container, .rangeselector," +
                " button, input, select, textarea, a, [role='button']")) return null;
        var host = target.closest("#hne_tic_graph");
        if (!host) return null;
        var gd = host.classList.contains("js-plotly-plot") ? host :
            host.querySelector(".js-plotly-plot");
        return gd && gd.contains(target) ? gd : null;
    }

    function pointAt(gd, event) {
        var full = gd && gd._fullLayout;
        if (!full || !full.xaxis || !full.yaxis) return null;
        var xa = full.xaxis;
        var ya = full.yaxis;
        if (typeof xa.p2d !== "function" || typeof ya.p2d !== "function") return null;
        // Graph 外枠は親の幅まで伸びるが、固定幅の描画面は別サイズになり得る。
        // 実ブラウザで外枠 884 px / 描画面 600 px を確認したため内部面を基準にする。
        var canvas = typeof gd.querySelector === "function" && gd.querySelector(".svg-container");
        var rect = (canvas || gd).getBoundingClientRect();
        if (!(rect.width > 0 && rect.height > 0)) return null;
        // CSS 拡大縮小も考慮して Plotly 内部のピクセル単位へ戻す。
        var sx = Number.isFinite(full.width) ? full.width / rect.width : 1;
        var sy = Number.isFinite(full.height) ? full.height / rect.height : 1;
        var px = (event.clientX - rect.left) * sx - xa._offset;
        var py = (event.clientY - rect.top) * sy - ya._offset;
        if (!(px >= 0 && py >= 0 && px <= xa._length && py <= ya._length)) return null;
        var x = Number(xa.p2d(px));
        var y = Number(ya.p2d(py));
        return Number.isFinite(x) && Number.isFinite(y) ? {x: x, y: y} : null;
    }

    function samePointer(event) {
        return active && active.pointer === event.pointerId;
    }

    function moved(event) {
        if (!samePointer(event)) return;
        var dx = event.clientX - active.startX;
        var dy = event.clientY - active.startY;
        if (dx * dx + dy * dy > MOVE_LIMIT * MOVE_LIMIT) active.dragged = true;
    }

    function down(event) {
        // 複数指操作・右クリックはポリゴン入力として扱わない。
        if (event.button !== 0 || event.isPrimary === false) {
            active = null;
            return;
        }
        var gd = graphFor(event.target);
        if (!enabled(gd) || !pointAt(gd, event)) {
            active = null;
            return;
        }
        active = {
            gd: gd, pointer: event.pointerId,
            startX: event.clientX, startY: event.clientY,
            context: metadata(gd).hne_polygon_context, dragged: false
        };
    }

    function up(event) {
        if (!samePointer(event)) return;
        moved(event);
        var gesture = active;
        active = null;
        // Plotly 5.x は mousedown 後に body 直下の dragcover で mouseup を受ける。
        // この時だけ開始側の図を使い、別の図・ボタンへの mouseup は除外する。
        var cover = event.target && typeof event.target.closest === "function" &&
            event.target.closest(".dragcover");
        if (event.button !== 0 || gesture.dragged || !enabled(gesture.gd) ||
                (!cover && graphFor(event.target) !== gesture.gd) ||
                metadata(gesture.gd).hne_polygon_context !== gesture.context) return;
        var point = pointAt(gesture.gd, event);
        var dc = window.dash_clientside;
        if (!point || !dc || typeof dc.set_props !== "function") return;
        sequence += 1;
        var nonce = Date.now().toString(36) + "-" + sequence.toString(36);
        if (pendingContext !== gesture.context) {
            pending = [];
            pendingContext = gesture.context;
        }
        pending.push({x: point.x, y: point.y, nonce: nonce});
        dc.set_props("hne_msi_vertex_store", {data: {
            x: point.x, y: point.y, context: gesture.context,
            nonce: nonce, batch: pending.slice()
        }});
    }

    function cancel() { active = null; }

    // capture 段階で受信し、Plotly による stopPropagation に依存しない。
    // preventDefault は使わず、ドラッグ・ズームは通常どおり操作できる。
    var pointer = "PointerEvent" in window;
    document.addEventListener(pointer ? "pointerdown" : "mousedown", down, true);
    document.addEventListener(pointer ? "pointermove" : "mousemove", moved, true);
    document.addEventListener(pointer ? "pointerup" : "mouseup", up, true);
    if (pointer) document.addEventListener("pointercancel", cancel, true);
    window.addEventListener("blur", cancel);
}());
