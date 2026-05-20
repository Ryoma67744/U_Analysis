// 認証 UI 用 clientside 関数
// パスワード変更モーダル「保存」クリック時に Flask API を呼ぶ。
if (!window.dash_clientside) {
    window.dash_clientside = {};
}
window.dash_clientside.auth = {
    submitChangePassword: function (n_clicks, master, newA, newB) {
        if (!n_clicks) {
            return [window.dash_clientside.no_update,
                    window.dash_clientside.no_update,
                    window.dash_clientside.no_update,
                    window.dash_clientside.no_update];
        }
        const trimmed_a = (newA || "").trim();
        const trimmed_b = (newB || "").trim();
        if (!master) {
            return ["Master Password を入力してください",
                    window.dash_clientside.no_update,
                    window.dash_clientside.no_update,
                    window.dash_clientside.no_update];
        }
        if (!trimmed_a && !trimmed_b) {
            return ["変更する Password A または B を入力してください",
                    window.dash_clientside.no_update,
                    window.dash_clientside.no_update,
                    window.dash_clientside.no_update];
        }

        const payload = { master_password: master };
        if (trimmed_a) payload.new_password_a = trimmed_a;
        if (trimmed_b) payload.new_password_b = trimmed_b;

        const result_div = "更新中...";

        // 同期的に返す必要があるので fetch は非同期、結果は次の callback で...
        // dash の clientside_callback は Promise を await できないため、
        // ここでは fetch を発火しつつローカル要素更新で結果を表示する。
        fetch("/api/admin/change-password", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
            credentials: "same-origin",
        }).then(function (resp) {
            return resp.json().then(function (data) {
                return { status: resp.status, body: data };
            });
        }).then(function (r) {
            const el = document.getElementById("cp_status");
            if (!el) return;
            if (r.status === 200 && r.body && r.body.ok) {
                const upd = (r.body.updated || []).join(", ");
                el.innerHTML =
                    '<div class="alert alert-success mb-0 py-2">' +
                    'Password ' + (upd || "?") + ' を更新しました。' +
                    '他のセッションは自動的にログアウトされます。' +
                    '</div>';
            } else {
                const msg = (r.body && r.body.error) || ("HTTP " + r.status);
                el.innerHTML =
                    '<div class="alert alert-danger mb-0 py-2">' +
                    msg + '</div>';
            }
        }).catch(function (e) {
            const el = document.getElementById("cp_status");
            if (el) {
                el.innerHTML =
                    '<div class="alert alert-danger mb-0 py-2">' +
                    '通信エラー: ' + e.message + '</div>';
            }
        });

        // 入力欄をクリア (Master と新パスは即時消去でセキュリティを担保)
        return [result_div, "", "", ""];
    },
};
