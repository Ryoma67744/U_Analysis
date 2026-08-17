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
//
// -----------------------------------------------------------------------------
// ver56.5 修正: 「発火元」と「渡す座標」がずれていた (C05-1)
// -----------------------------------------------------------------------------
// Plotly の relayoutData プロパティは**発火後も値が残る**。一度 UMAP のラベルを
// ドラッグすると `interactive_umap_plot.relayoutData` は `annotations[N].x` を
// 持ったままになる。その後 Spatial のラベルをドラッグすると、
//   - 発火元 (callback_context.triggered) = spatial_graph
//   - しかし従来の実装は Input を先頭から走査して「最初に見つかった
//     アノテーション移動」を返していたので、拾うのは **UMAP の古い座標**
// となり、UMAP のラベル座標が Spatial のラベル位置として保存されていた。
// UMAP の座標は概ね ±15、Spatial はピクセル単位で数千なので、保存後に
// Spatial のラベルが画面外へ飛ぶ。逆向き (Spatial → UMAP) も同様に起きる。
//
// 対策: **発火元のプロパティ値だけ**を見る。Dash の clientside 版
// `callback_context.triggered` は `{prop_id, value}` を持つ (dash-renderer が
// inputDict から解決して渡す) ので、発火した Input の値そのものを使える。
// 発火元がアノテーション移動でなければ (パン/ズーム) 他の Input に古い
// アノテーション座標が残っていても通さない。
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

    // prop_id ("<id>.relayoutData") から component id を取り出す。パターンマッチ
    // id ("{...}.relayoutData") は JSON 文字列なので dict に戻す。サーバ側は
    // この id で umap_per_sample_graph / spatial_graph / fs_spatial_graph を判別する。
    function idFromPropId(propId) {
        var idPart = String(propId || "");
        idPart = idPart.substring(0, idPart.lastIndexOf("."));
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

    function signal(rd, triggeredId) {
        return {
            relayout: rd,
            triggered_id: triggeredId,
            // 同じラベルを同じ座標へ戻した場合でも Store の変化として
            // 検知させるためのシーケンス番号。Date.now() ではなく単調増加
            // カウンタを使い、同一ミリ秒内の連続ドラッグも取りこぼさない。
            seq: (window.dash_clientside.__relayout_seq =
                (window.dash_clientside.__relayout_seq || 0) + 1),
        };
    }

    // 発火元 (callback_context.triggered) の値だけを見る。他の Input に残った
    // 古い relayoutData は、たとえアノテーション移動を含んでいても使わない。
    function filterAnnotationRelayout() {
        var NU = window.dash_clientside.no_update;
        var ctx = window.dash_clientside.callback_context;
        var triggered = (ctx && ctx.triggered) || [];
        var sawValue = false;
        for (var i = 0; i < triggered.length; i++) {
            var t = triggered[i] || {};
            if (!Object.prototype.hasOwnProperty.call(t, "value")) {
                continue;
            }
            sawValue = true;
            if (hasAnnotationMove(t.value)) {
                return signal(t.value, idFromPropId(t.prop_id));
            }
        }
        if (sawValue) {
            // 発火元は判明していて、そのいずれもアノテーション移動ではない
            // (パン/ズーム)。他の Input の残骸は見ない。
            return NU;
        }

        // --- 後方互換の退避経路 ---
        // callback_context が無い / triggered に value が入らない環境
        // (テストからの直接呼び出しや古い dash-renderer) では、従来どおり
        // 引数を平坦化して走査する。ALL パターンの Input は配列で来る。
        var flat = [];
        for (var j = 0; j < arguments.length; j++) {
            var a = arguments[j];
            if (Array.isArray(a)) {
                flat = flat.concat(a);
            } else {
                flat.push(a);
            }
        }
        for (var k = 0; k < flat.length; k++) {
            if (hasAnnotationMove(flat[k])) {
                return signal(flat[k],
                              triggered.length ? idFromPropId(triggered[0].prop_id)
                                               : null);
            }
        }
        return NU;
    }

    window.dash_clientside.relayout = {
        filter_annotations: filterAnnotationRelayout,
    };
})();
