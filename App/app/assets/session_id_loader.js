// session_id を Cookie から読み取り、Dash の dcc.Store (session_id_store) に転送する。
//
// Cookie は Flask before_request (App/app/services/session_id.py の get_or_create_session_id)
// で発行される。clientside_callback (edit_lock_callbacks.py) からこの関数を呼出。
window.dash_clientside = window.dash_clientside || {};
window.dash_clientside.session = {
    get_session_id: function() {
        const match = document.cookie.match(/(?:^|;\s*)msi_session_id=([^;]+)/);
        return match ? match[1] : null;
    }
};
