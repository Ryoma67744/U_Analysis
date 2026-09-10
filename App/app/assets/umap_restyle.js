// ★ ver66.3: UMAPの外観だけを更新し、座標やCellIDの再送を省く。
// Pythonのapply_umap_display_overridesと同じ役割表を読む。hover用meta、
// 凡例・下書きの固定サイズ、ドラッグ済みラベルの位置は変更しない。
window.dash_clientside = window.dash_clientside || {};
(function () {
    "use strict";
    var scopes = {
        normal: {selectors: ["#interactive_umap_plot", "#umap_per_sample_container"]},
        fullscreen: {selectors: ["#fs_umap_graph_container"]}
    };
    var graphs = new WeakMap();

    function finite(value) {
        return value !== null && value !== undefined && Number.isFinite(Number(value));
    }

    function apply(gd, scope) {
        if (!gd || !gd.isConnected || !gd.data || !gd.layout || !window.Plotly) { return; }
        var state = graphs.get(gd);
        if (!state) { return; }
        if (state.busy) { state.pending = true; return; }
        var meta = gd.layout.meta || {};
        if (meta.kind !== "umap") { return; }
        var rules = (meta.umap_style || {}).markers || [];
        var indices = [], sizes = [], labels = {};
        if (finite(scope.marker)) {
            rules.forEach(function (rule) {
                var i = rule.index;
                if (["point", "highlight", "background"].indexOf(rule.role) < 0 ||
                    !Number.isInteger(i) || !gd.data[i] || !finite(rule.delta)) { return; }
                var size = Math.max(1, Number(scope.marker) + Number(rule.delta));
                if (!gd.data[i].marker || gd.data[i].marker.size !== size) {
                    indices.push(i); sizes.push(size);
                }
            });
        }
        if (finite(scope.label)) {
            (gd.layout.annotations || []).forEach(function (ann, i) {
                if (ann.name === "umap_cluster_label" &&
                    (!ann.font || ann.font.size !== Number(scope.label))) {
                    // font.sizeだけなら、Plotlyで移動済みのx/yを上書きしない。
                    labels["annotations[" + i + "].font.size"] = Number(scope.label);
                }
            });
        }
        if (!indices.length && !Object.keys(labels).length) { return; }
        state.busy = true;
        state.pending = false;
        var work;
        try {
            // 1図に複数のPlotly操作を同時発行しない。連打中は最新設定へ追従する。
            work = indices.length ? window.Plotly.restyle(gd, {"marker.size": sizes}, indices) : null;
        } catch (error) {
            state.busy = false;
            console.error("UMAPサイズ更新に失敗しました", error);
            return;
        }
        Promise.resolve(work).then(function () {
            // restyleの完了待ち中に別世代の図がreactされた場合、古い添字の
            // 注釈は書かない。afterplotから現在の役割表で再適用する。
            if (gd.isConnected && gd.layout.meta === meta && Object.keys(labels).length) {
                return window.Plotly.relayout(gd, labels);
            }
        }).catch(function (error) {
            console.error("UMAP文字サイズ更新に失敗しました", error);
        }).finally(function () {
            state.busy = false;
            // 自分のafterplotも1回だけ照合する。値が一致すればPlotlyは呼ばない。
            if (state.pending && gd.isConnected) { queue(scope); }
        });
    }

    function scan(scope) {
        var unfinished = false;
        scope.selectors.forEach(function (selector) {
            var root = document.querySelector(selector);
            if (!root) { return; }
            if (!scope.observers) { scope.observers = new Map(); }
            if (!scope.observers.has(root)) {
                var observer = new MutationObserver(function (mutations) {
                    // 新しい図のmountだけを見る。SVGの再描画を監視の起点にしない。
                    var addedPlot = mutations.some(function (mutation) {
                        // Dashは先に空divをmountし、後からPlotlyがclassを付ける。
                        // その順序でも初回のafterplot監視を取り付ける。
                        if (mutation.type === "attributes") {
                            return mutation.target.matches(".js-plotly-plot");
                        }
                        return Array.from(mutation.addedNodes).some(function (node) {
                            return node.nodeType === 1 && (node.matches(".js-plotly-plot") ||
                                node.querySelector(".js-plotly-plot"));
                        });
                    });
                    if (addedPlot) { scope.retries = 0; queue(scope); }
                });
                observer.observe(root, {childList: true, subtree: true,
                    attributes: true, attributeFilter: ["class"]});
                scope.observers.set(root, observer);
            }
            var plots = Array.from(root.querySelectorAll(".js-plotly-plot"));
            if (root.matches(".js-plotly-plot")) { plots.unshift(root); }
            plots.forEach(function (gd) {
                if (!gd.data || !gd._fullLayout || typeof gd.on !== "function") {
                    unfinished = true; return;
                }
                if (!graphs.has(gd)) {
                    graphs.set(gd, {busy: false, pending: false});
                    gd.on("plotly_afterplot", function () { apply(gd, scope); });
                }
                apply(gd, scope);
            });
        });
        // モーダル/ページを閉じたら古いDOMへの強参照を解放する。
        if (scope.observers) {
            scope.observers.forEach(function (observer, root) {
                if (!root.isConnected) { observer.disconnect(); scope.observers.delete(root); }
            });
        }
        if (unfinished && (scope.retries || 0) < 10) {
            scope.retries = (scope.retries || 0) + 1;
            queue(scope);
        }
    }

    function queue(scope) {
        if (scope.queued) { return; }
        scope.queued = true;
        window.requestAnimationFrame(function () {
            scope.queued = false;
            scan(scope);
        });
    }

    function run(scope, marker, label) {
        scope.marker = marker;
        scope.label = label;
        scope.retries = 0;
        queue(scope);
        return window.dash_clientside.no_update;
    }

    window.dash_clientside.umap_restyle = {
        normal: function (marker, label) { return run(scopes.normal, marker, label); },
        fullscreen: function (marker, label) { return run(scopes.fullscreen, marker, label); }
    };
})();
