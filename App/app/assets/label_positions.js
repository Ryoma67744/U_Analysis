// ★ ver76.0: 自動配置へ戻す対象は、画面にある図の来歴/クラスタIDから決める。
// 全Storeや全注釈座標を送らず、別サンプルの手動位置を解除しない。
(function () {
    "use strict";
    window.dash_clientside = window.dash_clientside || {};
    var sequence = 0;
    function requestReset(clicks, rdsPath, loadToken) {
        var dc = window.dash_clientside;
        var changed = (dc.callback_context.triggered || [])[0];
        if (!changed || !changed.value) return dc.no_update;
        var id;
        try { id = JSON.parse(changed.prop_id.slice(0, changed.prop_id.lastIndexOf("."))); }
        catch (_) { return dc.no_update; }
        var roots = {
            umap: ["interactive_umap_plot", "umap_per_sample_container"],
            spatial: ["spatial_plots_container"],
            fs_umap: ["fs_umap_graph_container"],
            fs_spatial: ["fs_spatial_graph_container"]
        };
        var targets = [];
        (roots[id.view] || []).forEach(function (rootId) {
            var root = document.getElementById(rootId);
            if (!root || root.getClientRects().length === 0) return;
            root.querySelectorAll(".js-plotly-plot").forEach(function (gd) {
                if (gd.getClientRects().length === 0) return;
                var scope = gd.layout && (gd.layout.meta || {}).label_scope;
                if (!scope || scope.kind === "hne" || scope.rds_path !== rdsPath ||
                        scope.load_token !== loadToken || !(scope.labels || []).length) return;
                targets.push(Object.assign({}, scope, {clusters: scope.labels.map(function (a) {
                    return String(a.cluster);
                })}));
            });
        });
        return {targets: targets, rds_path: rdsPath, load_token: loadToken, seq: ++sequence};
    }
    dcInstall();
    function dcInstall() {
        window.dash_clientside.label_positions = {request_reset: requestReset};
    }
}());
