// ★ ver66.3: 同じCookieの別タブを分離し、m/zの操作順をブラウザで確定する。
(function () {
    "use strict";
    window.dash_clientside = window.dash_clientside || {};
    var view = null;
    var sequence = 0;
    window.dash_clientside.feature_requests = {
        view_id: function () {
            if (!view) {
                view = (window.crypto && window.crypto.randomUUID) ? window.crypto.randomUUID() :
                    Date.now().toString(36) + "-" + Math.random().toString(36).slice(2);
            }
            return view;
        },
        intensity: function (feature, min, max, names, viewId, sample, nameMap, fullscreenClosed, rows, rotation, rdsPath, loaded) {
            if (!viewId) { return window.dash_clientside.no_update; }
            sequence += 1;
            var context = window.dash_clientside.callback_context || {};
            var changed = (context.triggered || []).map(function (item) {
                return (item.prop_id || "").split(".")[0];
            });
            var dataOnly = ["feature_select", "feature_intensity_min", "feature_intensity_max", "feature_show_compound_names"];
            // ★ ver66.3: 同時変更に構造変更が含まれれば、m/zだけの差分と誤認しない。
            var trigger = changed.find(function (id) { return dataOnly.indexOf(id) < 0; }) || changed[0] || "";
            return {view_id: viewId, sequence: sequence, feature: feature,
                    intensity_min: min, intensity_max: max, show_names: names,
                    sample: sample, name_map: nameMap, fullscreen_closed: fullscreenClosed,
                    rows: rows, rotation: rotation,
                    trigger: trigger, rds_path: rdsPath,
                    dataset_revision: loaded && loaded.rds_path === rdsPath ? loaded.dataset_revision : null};
        }
    };
})();
