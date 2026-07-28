// =============================================================================
// 見た目だけのコントロールを Plotly.restyle で反映する (ver46.1)
// =============================================================================
// マーカーサイズ / ラベルサイズ / スポット不透明度は figure の **データ** を変えない。
// にもかかわらず、これらは Spatial 描画コールバック (22 Input) の Input だったため、
// スライダーを 1 目盛り動かすたびに全タイルがサーバで作り直され、
// 全点の座標が再送されていた（10 万 spot で gzip 後でも 1.5MB 前後）。
//
// ここでは既に描画済みのグラフを直接 restyle するので、ネットワークは 1 バイトも
// 動かず、サーバ CPU も使わない。
//
// **重要**: 同じ規則を Python 側にも実装している
// (app/utils/display_helpers.apply_display_overrides)。一括保存 / サムネ登録は
// サーバ側が保持している figure を使うため、そちらにも同じ変換を掛けないと
// 「画面と保存した PNG が違う」ことになる。両者の一致は
// tests/test_render_payload.py::test_display_overrides_match_fresh_build で担保。
//
// 判定はトレースの meta で行う:
//   meta = {dsz: 0|1, op: bool}   dsz=基準サイズからの差分 / op=不透明度の対象か
// meta が無いトレース（凡例ダミー・H&E 画像）は触らない。
// layout.meta.kind ("msi" | "hne") でタイル種別を見分け、対象外の図は無視する。
// =============================================================================

window.dash_clientside = window.dash_clientside || {};

(function () {
    "use strict";

    var CONTAINERS = ["#spatial_plots_container", "#fs_spatial_graph_container"];

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

    // 1 つのグラフに対して、Python の apply_display_overrides と同じ変換を行う。
    function applyOverrides(gd, opts) {
        if (!window.Plotly || !gd || !gd.data || !gd.layout) { return; }
        var meta = gd.layout.meta || {};
        if (opts.kinds.indexOf(meta.kind) < 0) { return; }

        // --- マーカーサイズ / 不透明度（トレース単位の restyle）---
        var idx = [], sizes = [], opacities = [];
        var base = null;
        if (opts.markerSize !== null && opts.markerSize !== undefined) {
            base = opts.markerSize > 0 ? opts.markerSize : meta.auto_msz;
        }
        for (var i = 0; i < gd.data.length; i++) {
            var tm = gd.data[i].meta;
            if (!tm || typeof tm !== "object" || tm.dsz === undefined) { continue; }
            var touched = false;
            var size = undefined, opacity = undefined;
            if (base) { size = base + (tm.dsz || 0); touched = true; }
            if (opts.spotOpacity !== null && opts.spotOpacity !== undefined && tm.op) {
                opacity = opts.spotOpacity;
                touched = true;
            }
            if (!touched) { continue; }
            idx.push(i);
            sizes.push(size);
            opacities.push(opacity);
        }
        if (idx.length) {
            var update = {};
            if (sizes.some(function (v) { return v !== undefined; })) {
                update["marker.size"] = sizes;
            }
            if (opacities.some(function (v) { return v !== undefined; })) {
                update["marker.opacity"] = opacities;
            }
            if (Object.keys(update).length) {
                try { window.Plotly.restyle(gd, update, idx); } catch (e) { /* noop */ }
            }
        }

        // --- ラベルサイズ（layout.annotations の relayout）---
        if (opts.labelSize !== null && opts.labelSize !== undefined) {
            var anns = gd.layout.annotations || [];
            if (anns.length) {
                var rel = {};
                for (var j = 0; j < anns.length; j++) {
                    rel["annotations[" + j + "].font.size"] = opts.labelSize;
                }
                try { window.Plotly.relayout(gd, rel); } catch (e) { /* noop */ }
            }
        }
    }

    function run(opts) {
        plots().forEach(function (gd) { applyOverrides(gd, opts); });
        return window.dash_clientside.no_update;
    }

    window.dash_clientside.spatial_restyle = {
        // 通常 (MSI) タイルのマーカーサイズ。0 = 自動（layout.meta.auto_msz を使う）
        marker_size: function (v) {
            return run({markerSize: v, labelSize: null, spotOpacity: null,
                        kinds: ["msi"]});
        },
        // クラスタ番号ラベルの文字サイズ
        label_size: function (v) {
            return run({markerSize: null, labelSize: v, spotOpacity: null,
                        kinds: ["msi"]});
        },
        // スポット不透明度 (0-100)。通常タイル・H&E タイルの両方に効く
        spot_opacity: function (v) {
            var op = (v === null || v === undefined) ? null : v / 100.0;
            return run({markerSize: null, labelSize: null, spotOpacity: op,
                        kinds: ["msi", "hne"]});
        },
        // H&E タイルのスポットサイズ
        hne_marker_size: function (v) {
            return run({markerSize: v, labelSize: null, spotOpacity: null,
                        kinds: ["hne"]});
        },
    };
})();
