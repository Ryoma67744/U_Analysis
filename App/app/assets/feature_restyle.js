// =============================================================================
// Feature Plot の「見た目だけ」のコントロールを Plotly.restyle で反映する (ver51.3)
// =============================================================================
// マーカーサイズと配色は figure の **データ** を変えない。にもかかわらず、
// これらは Feature 描画コールバック (10 Input) の Input だったため、プルダウンを
// 1 回動かすたびに全サンプルのタイルがサーバで作り直され、全点の座標・強度・
// CellID が再送されていた（実測 4 サンプル × 50,000 点で gzip 後 4.5MB）。
//
// ここでは既に描画済みのグラフを直接 restyle するので、ネットワークは 1 バイトも
// 動かず、サーバ CPU も使わない。ver46.1 で Spatial に入れた仕組み
// (spatial_restyle.js) の Feature 版。
//
// **重要**: 同じ規則を Python 側にも実装している
// (app/utils/display_helpers.apply_feature_display_overrides)。一括保存は
// サーバが保持している figure を使うため、そちらにも同じ変換を掛けないと
// 「画面と保存した PNG が違う」ことになる。両者の一致は
// tests/test_render_payload.py で検証している。
//
// 対象トレースの見つけ方が Spatial と違う点に注意:
//   Spatial … トレースの meta = {dsz, op}
//   Feature … layout.meta = {kind:"feature", auto_msz, sz:[...], cs:[...]}
// Feature の発現トレースは meta を **hover ラベルの値** に使っている
// (ver46.3: 化合物名が "%{x}" 等を含みうるので値として渡す必要がある) ため、
// トレース meta を判定に使えない。
// =============================================================================

window.dash_clientside = window.dash_clientside || {};

(function () {
    "use strict";

    // Feature のフルスクリーンは専用コンテナを持たず、feature_plot_container の
    // children を fullscreen_modal_body に複製する作りなので後者も見る。
    // モーダルには UMAP / Spatial / DEG の図も入りうるが、applyFeature が
    // layout.meta.kind === "feature" で弾くので触らない。
    var CONTAINERS = ["#feature_plot_container", "#fullscreen_modal_body"];

    function plots() {
        var out = [];
        CONTAINERS.forEach(function (sel) {
            var root = document.querySelector(sel);
            if (!root) { return; }
            root.querySelectorAll(".js-plotly-plot").forEach(function (gd) {
                out.push(gd);
            });
        });
        return out;
    }

    // 1 つのグラフに対して、Python の apply_feature_display_overrides と
    // 同じ変換を行う。
    function applyFeature(gd, opts) {
        if (!window.Plotly || !gd || !gd.data || !gd.layout) { return; }
        var meta = gd.layout.meta || {};
        if (meta.kind !== "feature") { return; }

        // --- マーカーサイズ ---
        if (opts.markerSize !== null && opts.markerSize !== undefined) {
            var base = opts.markerSize > 0 ? opts.markerSize : meta.auto_msz;
            var szIdx = (meta.sz || []).filter(function (i) {
                return i >= 0 && i < gd.data.length;
            });
            if (base && szIdx.length) {
                try {
                    window.Plotly.restyle(gd, {"marker.size": base}, szIdx);
                } catch (e) { /* noop */ }
            }
        }

        // --- 配色 (発現トレースのみ。TIC 背景は常に Greys) ---
        if (opts.colorscale) {
            var csIdx = (meta.cs || []).filter(function (i) {
                return i >= 0 && i < gd.data.length;
            });
            if (csIdx.length) {
                try {
                    window.Plotly.restyle(
                        gd, {"marker.colorscale": opts.colorscale}, csIdx);
                } catch (e) { /* noop */ }
            }
        }
    }

    function run(opts) {
        plots().forEach(function (gd) { applyFeature(gd, opts); });
        return window.dash_clientside.no_update;
    }

    window.dash_clientside.feature_restyle = {
        // マーカーサイズ。0 = 自動（layout.meta.auto_msz を使う）
        marker_size: function (v) {
            return run({markerSize: v, colorscale: null});
        },
        // カラースケール名 (Plasma / Viridis / ...)
        colorscale: function (v) {
            return run({markerSize: null, colorscale: v});
        },
    };
})();
