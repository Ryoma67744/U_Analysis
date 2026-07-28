// =============================================================================
// relayoutData のクライアント側フィルタ (ver46.1)
// =============================================================================
// Plotly の `relayoutData` は、パン・ズーム・オートスケール・アノテーション移動の
// すべてで発火する。とくに scrollZoom 有効時はホイールを 1 回転させるだけで
// 10〜30 回発火する。
//
// アプリがこのイベントに用があるのは「クラスタラベル(アノテーション)をドラッグして
// 位置を保存する」ときだけ (interactive_fullscreen.accumulate_annotation_positions_*)。
// 従来はサーバ側コールバックの Input に relayoutData を直結していたため、
// **パン・ズームのたびに /_dash-update-component へ POST が飛び**、サーバは
// annotations[ キーが無いことを確認して PreventUpdate で捨てていた。
// 捨てるだけなのに往復のコストだけが乗る、という状態だった。
//
// ここで annotations[...] を含むイベントだけを通し、それ以外は no_update を返す。
// clientside コールバックはブラウザ内で完結するので、パン・ズームでは
// ネットワークが 1 バイトも動かない。
//
// `edits: {annotationPosition: true}` によるラベルのドラッグは必ず
// `annotations[N].x` / `annotations[N].y` を含むため、保存機能はそのまま動く。
// scrollZoom も従来どおり有効のままにできる。
// =============================================================================

window.dash_clientside = window.dash_clientside || {};

(function () {
    "use strict";

    function hasAnnotationMove(rd) {
        if (!rd || typeof rd !== "object") {
            return false;
        }
        for (var k in rd) {
            if (Object.prototype.hasOwnProperty.call(rd, k) &&
                k.indexOf("annotations[") === 0) {
                return true;
            }
        }
        return false;
    }

    // 発火元の component id を取り出す。パターンマッチ id ("{...}.relayoutData")
    // は JSON 文字列なので dict に戻す。サーバ側はこの id で
    // umap_per_sample_graph / spatial_graph / fs_spatial_graph を判別する。
    function triggeredId() {
        var ctx = window.dash_clientside.callback_context;
        if (!ctx || !ctx.triggered || !ctx.triggered.length) {
            return null;
        }
        var propId = ctx.triggered[0].prop_id || "";
        var idPart = propId.substring(0, propId.lastIndexOf("."));
        if (!idPart) {
            return null;
        }
        if (idPart.charAt(0) === "{") {
            try {
                return JSON.parse(idPart);
            } catch (e) {
                return idPart;
            }
        }
        return idPart;
    }

    // 引数は Input の並びどおりに届く。ALL パターンの Input は配列で来るため、
    // 平坦化してから「アノテーション移動を含む最初の 1 件」を探す。
    function filterAnnotationRelayout() {
        var NU = window.dash_clientside.no_update;
        var flat = [];
        for (var i = 0; i < arguments.length; i++) {
            var a = arguments[i];
            if (Array.isArray(a)) {
                flat = flat.concat(a);
            } else {
                flat.push(a);
            }
        }
        for (var j = 0; j < flat.length; j++) {
            if (hasAnnotationMove(flat[j])) {
                return {
                    relayout: flat[j],
                    triggered_id: triggeredId(),
                    // 同じラベルを同じ座標へ戻した場合でも Store の変化として
                    // 検知させるためのシーケンス番号。Date.now() ではなく単調増加
                    // カウンタを使い、同一ミリ秒内の連続ドラッグも取りこぼさない。
                    seq: (window.dash_clientside.__relayout_seq =
                        (window.dash_clientside.__relayout_seq || 0) + 1),
                };
            }
        }
        return NU;
    }

    window.dash_clientside.relayout = {
        filter_annotations: filterAnnotationRelayout,
    };
})();
