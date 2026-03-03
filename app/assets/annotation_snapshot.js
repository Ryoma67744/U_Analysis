/**
 * annotation_snapshot.js
 * ラベル位置保存ボタン押下時に、Plotly.js の DOM から
 * 全グラフのアノテーション位置を一括取得するclientside callback。
 *
 * relayoutData は最後のイベントのみ保持するため信頼できない。
 * 代わりに _fullLayout.annotations を直接読み取ることで、
 * 全アノテーションの最新位置を確実に取得する。
 *
 * ※ これはバックアップ機構。Primary はサーバーサイドの relayoutData 蓄積。
 *
 * v4: 通常モード / FS モードに分割（動的 Input のコールバックブロック対策）
 */
window.dash_clientside = window.dash_clientside || {};

// --- 共通ヘルパー ---

/**
 * DOM要素から Plotly グラフ div を探す（複数セレクタを試行）
 */
function _findPlotlyDiv(el) {
    if (!el) return null;
    if (el._fullLayout) return el;
    var pd = el.querySelector('.js-plotly-plot');
    if (pd && pd._fullLayout) return pd;
    pd = el.querySelector('.plotly');
    if (pd && pd._fullLayout) return pd;
    var children = el.querySelectorAll('div');
    for (var i = 0; i < children.length; i++) {
        if (children[i]._fullLayout) return children[i];
    }
    return null;
}

/**
 * DOM要素からPlotlyアノテーション情報を抽出
 */
function _getAnnotations(el, label) {
    var pd = _findPlotlyDiv(el);
    if (!pd || !pd._fullLayout) {
        if (el) console.log('[ANNOTATION] ' + label + ': _fullLayout not found');
        return [];
    }
    var anns = pd._fullLayout.annotations;
    if (!anns || !anns.length) return [];
    var out = [];
    for (var i = 0; i < anns.length; i++) {
        var a = anns[i];
        out.push({text: a.text || "", x: a.x, y: a.y});
    }
    console.log('[ANNOTATION] ' + label + ': captured ' + out.length + ' annotations');
    return out;
}

/**
 * triggered チェック（ボタンが実際にクリックされたか確認）
 */
function _checkTriggered() {
    try {
        var triggered = dash_clientside.callback_context.triggered;
        if (!triggered || !triggered.length || !triggered[0].value)
            return false;
        return true;
    } catch(e) {
        console.log('[ANNOTATION] callback_context error:', e);
        return false;
    }
}


window.dash_clientside.annotation_ns = {

    // 2a: 通常モード — 静的グラフのみキャプチャ
    capture_annotations_normal: function(n1, n2) {
        if (!_checkTriggered()) return window.dash_clientside.no_update;

        var result = {
            timestamp: new Date().toISOString(),
            umap_integrated: [],
            fs_umap_integrated: [],
            umap_per_sample: {},
            spatial: {},
            fs_spatial: {}
        };

        // UMAP統合（メインビュー）
        result.umap_integrated = _getAnnotations(
            document.getElementById('interactive_umap_plot'), 'umap_integrated');

        // パターンマッチ ID のグラフ（通常モード: umap_per_sample_graph, spatial_graph）
        document.querySelectorAll('[id^="{"]').forEach(function(el) {
            try {
                var p = JSON.parse(el.id);
                if (!p.type || !p.index) return;
                var anns = _getAnnotations(el, p.type + ':' + p.index);
                if (!anns.length) return;
                if (p.type === 'umap_per_sample_graph')
                    result.umap_per_sample[p.index] = anns;
                else if (p.type === 'spatial_graph')
                    result.spatial[p.index] = anns;
            } catch(e) {}
        });

        console.log('[ANNOTATION] normal snapshot:', JSON.stringify(result).substring(0, 200));
        return result;
    },

    // 2b: FS (UMAP/Spatial 共通) — パターンマッチング ALL で呼ばれる
    // n_clicks_list は ALL リストとして渡される
    capture_annotations_fs: function(n_clicks_list) {
        if (!_checkTriggered()) return window.dash_clientside.no_update;

        var result = {
            timestamp: new Date().toISOString(),
            umap_integrated: [],
            fs_umap_integrated: [],
            umap_per_sample: {},
            spatial: {},
            fs_spatial: {}
        };

        // FS UMAP 統合（存在すればキャプチャ）
        result.fs_umap_integrated = _getAnnotations(
            document.getElementById('fs_umap_integrated_graph'), 'fs_umap_integrated');

        // FS Spatial パターンマッチ ID（存在すればキャプチャ）
        document.querySelectorAll('[id^="{"]').forEach(function(el) {
            try {
                var p = JSON.parse(el.id);
                if (p.type === 'fs_spatial_graph' && p.index) {
                    var anns = _getAnnotations(el, 'fs_spatial:' + p.index);
                    if (anns.length) result.fs_spatial[p.index] = anns;
                }
            } catch(e) {}
        });

        console.log('[ANNOTATION] fs snapshot:', JSON.stringify(result).substring(0, 200));
        return result;
    }
};
