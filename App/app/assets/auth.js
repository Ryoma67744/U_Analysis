// 認証 UI 用 clientside 関数
// パスワード変更モーダル「保存」クリック時に Flask API を呼ぶ。
if (!window.dash_clientside) {
    window.dash_clientside = {};
}
window.dash_clientside.auth = {
    // ver4.0: Password A 廃止。Master + 共有 (B) の 2 本のみ。
    // ログイン済 (Tier A) なら master 再入力は任意 (③)。
    submitChangePassword: function (n_clicks, master, newMaster, newB) {
        const NU = window.dash_clientside.no_update;
        if (!n_clicks) {
            return [NU, NU, NU, NU];
        }
        const trimmed_master = (newMaster || "").trim();
        const trimmed_b = (newB || "").trim();
        if (!trimmed_master && !trimmed_b) {
            return [
                "変更する Master / 共有パスワードのいずれかを入力してください",
                NU, NU, NU,
            ];
        }

        // master は任意 (空でも可)。入力された場合のみ照合用に送る。
        const payload = {};
        if (master) payload.master_password = master;
        if (trimmed_master) payload.new_master_password = trimmed_master;
        if (trimmed_b) payload.new_password_b = trimmed_b;

        const result_div = "更新中...";

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
