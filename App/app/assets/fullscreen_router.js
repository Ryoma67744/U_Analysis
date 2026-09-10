/* ★ ver66.3: 全画面の種類別要求を軽量 Store へ渡し、古い生成応答を棄却する。
 * 従来は全種類のデータを毎回サーバへ送り、遅い応答が閉じたモーダルを
 * 再び開く可能性もあった。要求識別子はタブ内で管理し、閉鎖でも失効させる。
 */
(function () {
    "use strict";
    var active = null;
    var displayed = null;
    var sequence = 0;
    var pageId = String(Date.now()) + "-" + String(Math.random());
    var lanes = {umap: "light", spatial: "light", feature: "feature", deg: "deg"};

    function clearPreviousResponses(dc, results) {
        Object.keys(results).forEach(function (lane) {
            if (results[lane] != null) {
                dc.set_props("fullscreen_response_" + lane + "_store", {data: null});
            }
        });
    }

    function route(umap, feature, spatial, deg, isOpen, lightResult,
                   featureResult, degResult, rdsPath, loadToken, emptyBody) {
        var dc = window.dash_clientside;
        var nu = dc.no_update;
        var unchanged = [nu, nu, nu];
        var changed = (dc.callback_context.triggered || []).map(function (item) {
            return item.prop_id;
        });
        var results = {light: lightResult, feature: featureResult, deg: degResult};

        // 同時に閉鎖と生成応答が届いても、閉鎖を優先する。
        if (changed.indexOf("fullscreen_plot_modal.is_open") !== -1 && !isOpen) {
            active = null;
            displayed = null;
            clearPreviousResponses(dc, results);
            return unchanged;
        }
        // 読込先が変わった応答も画面へ反映しない。
        if (changed.indexOf("seurat_rds_path_store.data") !== -1 ||
                changed.indexOf("load_token_store.data") !== -1) {
            active = null;
            displayed = null;
            // データ A の図を表示したまま操作対象だけ B へ切り替えない。
            // 閉じている場合には閉鎖 callback を無用に発火させない。
            if (isOpen) return [false, "", emptyBody];
            clearPreviousResponses(dc, results);
            return unchanged;
        }

        var counts = {umap: umap, feature: feature, spatial: spatial, deg: deg};
        for (var i = 0; i < changed.length; i++) {
            var match = /^expand_(umap|feature|spatial|deg)_btn\.n_clicks$/.exec(changed[i]);
            if (!match || !counts[match[1]]) continue;
            var kind = match[1];
            active = {token: pageId + "-" + (++sequence), kind: kind,
                      rds_path: rdsPath == null ? null : rdsPath,
                      load_token: loadToken == null ? null : loadToken};
            // 前回の図を種類ごとに蓄積しない。応答を表示する実行中にその入力を
            // clear すると、再通知が現在の描画を追い越すため、新しい操作時に消す。
            clearPreviousResponses(dc, results);
            // set_props は応答→要求の静的依存循環を作らないために用いる。
            // 図を含む State はサーバの種類別 callback にだけ宣言する。
            dc.set_props("fullscreen_request_" + lanes[kind] + "_store", {data: active});
            return unchanged;
        }

        for (var j = 0; j < changed.length; j++) {
            var response = /^fullscreen_response_(light|feature|deg)_store\.data$/.exec(changed[j]);
            if (!response) continue;
            var result = results[response[1]];
            if (!result) continue;
            if (!active || result.token !== active.token || result.kind !== active.kind ||
                    result.rds_path !== active.rds_path ||
                    result.rds_path !== (rdsPath == null ? null : rdsPath) ||
                    result.load_token !== active.load_token ||
                    result.load_token !== (loadToken == null ? null : loadToken)) continue;
            if (!result.is_open) return unchanged;
            displayed = active;
            return [true, result.title, result.body];
        }
        return unchanged;
    }

    function filterAnnotations(umapRelayout, spatialRelayout, rdsPath, loadToken) {
        var dc = window.dash_clientside;
        if (!displayed || displayed.rds_path !== (rdsPath == null ? null : rdsPath) ||
                displayed.load_token !== (loadToken == null ? null : loadToken)) {
            return dc.no_update;
        }
        var result = dc.relayout.filter_annotations(umapRelayout, spatialRelayout);
        if (result === dc.no_update) return result;
        return Object.assign({}, result, {fullscreen_scope: displayed});
    }

    window.dash_clientside = Object.assign({}, window.dash_clientside, {
        fullscreen_router: {route: route, filter_annotations: filterAnnotations}
    });
}());
