# =============================================================================
# MSI Analysis Application - Share Callbacks
# 共有リンク (/share/<token> ・ /view/<token>) のルーティング。
#
# ★ ver52.3: 旧 read-only 共有ページ (page_shared / sv_* 9 本 / shared_view.py)
#   を削除した。ver4.0 で「共有時はインタラクティブタブ全機能を共有モードで
#   開く」方式に置き換わって以降、`current_page` に "shared" を書く箇所も
#   `share_token` に書き込むコールバックも **0 件**で、到達不能だった
#   （"shared" は `current_page` ではなく `interactive_entry_mode` に入る別物）。
#   共有機能そのものは本ファイルの `route_share_url` で生きている。
# =============================================================================

import logging
import os
import threading
import time
from collections import OrderedDict

from dash import Input, Output, callback, no_update

logger = logging.getLogger(__name__)
from app.services.share_manager import get_share
from app.services.persistent_share_manager import (
    get_persistent_share, increment_view_count as _persistent_increment_view,
)
from app.services.seurat_bridge import SeuratBridge

# Seuratブリッジ。
# ★ ver52.3: 旧 read-only 共有ページを削除した後も残す。
#   `lite_view_callbacks` が本モジュールから `_sv_bridge` と
#   `_shared_data*` を import して使っている（Lite ビューは生きている）。
_sv_bridge = SeuratBridge()

# 共有データキャッシュ: { token: { plot_data, cluster_stats, features_list, meta, ... } }
#
# ★ ver51.8: 上限も TTL も無い**無制限**の辞書だった。兄弟の
#   `interactive_callbacks._project_states` / `_export_figures` には LRU + TTL が
#   入っているのに、ここだけ無い。しかも ver51.7 でキーへ RDS の mtime を足した
#   結果、**再解析のたびに新しいエントリが増え続ける**（古い版は誰も消さない）。
#   1 エントリは plot_data (全 spot × 全列) + cluster_stats + features_list なので
#   数十〜数百 MB になり、run_app.py が明記しているとおり本番は 1 プロセスなので
#   再起動するまで解放されない。
#
#   LRU + TTL に変える。実装は _project_states と同じ方針
#   （OrderedDict + 最終アクセス時刻）。
_SHARED_DATA_MAX = int(os.environ.get("MSI_SHARED_DATA_MAX", "8"))
_SHARED_DATA_TTL_SEC = float(os.environ.get("MSI_SHARED_DATA_TTL_SEC", "1800"))
_shared_data: "OrderedDict[str, dict]" = OrderedDict()
_shared_data_atime: dict[str, float] = {}
_shared_data_lock = threading.RLock()


def _shared_data_put(key: str, value: dict) -> None:
    """共有データを登録し、上限/TTL を超えた古いエントリを捨てる (ver51.8)。"""
    with _shared_data_lock:
        now = time.monotonic()
        _shared_data[key] = value
        _shared_data.move_to_end(key)
        _shared_data_atime[key] = now

        # TTL 超過を先に落とす
        for k in [k for k, t in _shared_data_atime.items()
                  if k != key and now - t > _SHARED_DATA_TTL_SEC]:
            _shared_data.pop(k, None)
            _shared_data_atime.pop(k, None)
        # まだ多ければ LRU で落とす
        while len(_shared_data) > _SHARED_DATA_MAX:
            old, _ = _shared_data.popitem(last=False)
            _shared_data_atime.pop(old, None)
            logger.info("共有データキャッシュを LRU で解放: %s", old)


def _shared_data_get(key: str):
    """共有データを取り出す（取得時に LRU の順序を更新する）。"""
    with _shared_data_lock:
        v = _shared_data.get(key)
        if v is not None:
            _shared_data.move_to_end(key)
            _shared_data_atime[key] = time.monotonic()
        return v


# =========================================================================
# URL ルーティング
# =========================================================================

@callback(
    [Output("current_page", "data", allow_duplicate=True),
     Output("interactive_result_folder", "value", allow_duplicate=True),
     Output("interactive_msi_folder", "value", allow_duplicate=True),
     Output("interactive_project_select", "value", allow_duplicate=True),
     Output("interactive_sub_project_select", "value", allow_duplicate=True),
     Output("interactive_entry_mode", "data", allow_duplicate=True),
     Output("current_sub_project_id", "data", allow_duplicate=True),
     Output("shared_session", "data")],
    Input("url_bar", "pathname"),
    prevent_initial_call=True,
)
def route_share_url(pathname):
    """ver4.0: /share/<token> または /view/<token> でインタラクティブ解析
    (全機能) を共有モードで開く。

    - /share/<token>: 期間付き共有 (Tier B 認証)
    - /view/<token>: 無期限共有 (認証不要)
    旧 read-only shared_view ではなく page_analysis の interactive タブを
    共有モードで表示し、操作 + 保存を可能にする (① 操作可・保存あり)。
    共有先での操作は元プロジェクトに保存される。

    main_tabs.active_tab は url_bar.pathname を Input に取る
    _sync_tab_from_url (tab_url_routing.py) と衝突するため、ここでは
    設定しない。代わりに shared_session を Input に取る
    _shared_activate_interactive_tab が "interactive" を立てる
    (二段パターン: Input が違うので Dash の "already in use" を回避)。
    """
    _n = 8
    if not pathname:
        return (no_update,) * _n

    token, kind = None, None
    if pathname.startswith("/share/"):
        token = pathname.split("/share/", 1)[1].split("/")[0].split("?")[0]
        kind = "expiring"
    elif pathname.startswith("/view/"):
        token = pathname.split("/view/", 1)[1].split("/")[0].split("?")[0]
        kind = "persistent"
        if token:
            _persistent_increment_view(token)
    if not token:
        return (no_update,) * _n

    # トークン解決
    share = (get_persistent_share(token) if kind == "persistent"
             else get_share(token))
    if not share:
        # 無効/期限切れトークン → landing にフォールバック (エラーは画面で)
        logger.warning("share token invalid/expired: kind=%s", kind)
        return (no_update,) * _n

    result_dir = share.get("result_dir", "")
    project_id = share.get("project_id", "")
    sub_project_id = share.get("sub_project_id", "")

    # MSI データフォルダはサブプロジェクトから取得。
    #
    # ★ ver52.3: ここは「空」と「解決できなかった」を区別せず、下の return で
    #   `data_folder or no_update` としていた。`no_update` は **直前に開いて
    #   いた別プロジェクトの値を store に残す**ので、
    #     - 見出しとプロジェクト ID は新しい共有のもの
    #     - MSI データフォルダは前のプロジェクトのもの
    #   という食い違いが起きる。以後の生スペクトル読み出し
    #   （キャリブレーション自動検出・DESI エクスポート）が
    #   **別データセットに当たる**（スコープ漏れ）。
    #
    #   解決できなければ空文字を**明示的に**入れる。空なら下流は
    #   「データフォルダ未設定」として扱うので安全側に倒れる。
    #   黙って前の値を使い続けるより、開けないほうがましという判断。
    data_folder = ""
    try:
        from app.services.project_manager import get_sub_project
        sub = get_sub_project(project_id, sub_project_id)
        if sub:
            data_folder = sub.get("data_folder", "") or ""
        else:
            logger.warning(
                "share: サブプロジェクトが見つからない project=%s sub=%s。"
                "データフォルダは未設定にする（前の値を残さない）",
                project_id, sub_project_id)
    except Exception:
        logger.warning(
            "share: サブプロジェクトの解決に失敗 project=%s sub=%s。"
            "データフォルダは未設定にする（前の値を残さない）",
            project_id, sub_project_id, exc_info=True)
        data_folder = ""

    shared_session = {
        "active": True,
        "token": token,
        "kind": kind,
        "project_id": project_id,
        "sub_project_id": sub_project_id,
        # ver4.1: 共有元が指定した統合手法 ("all" なら受け手は全手法を切替可、
        # 特定手法なら受け手にはその手法のみ表示)
        "integration_method": share.get("integration_method", "all"),
    }
    logger.info("shared interactive open: kind=%s project=%s sub=%s",
                kind, project_id, sub_project_id)
    # ★ ver52.3: `x or no_update` をやめ、常に明示的な値を入れる。
    #   `no_update` は「変更しない」なので、**直前に開いていた別プロジェクトの
    #   結果フォルダ / データフォルダがそのまま残る**。project_id と
    #   sub_project_id だけが新しくなるため、画面は新しい共有を名乗りながら
    #   中身は前のプロジェクトを指す、という状態になっていた。
    return (
        "analysis",
        result_dir,
        data_folder,
        project_id,
        sub_project_id,
        "shared",            # interactive_entry_mode → auto_load 対象
        sub_project_id,      # current_sub_project_id
        shared_session,
    )


# ---------------------------------------------------------------------------
# 共有セッション確定後に interactive タブを active 化 (ver4.0)
# ---------------------------------------------------------------------------
# main_tabs.active_tab を url_bar.pathname から直接書くと _sync_tab_from_url
# (tab_url_routing.py) と同一 (Input, Output) になり Dash 実行時に
# "Output ... is already in use" となる。route_share_url が立てた
# shared_session を Input に取ることで Input を分離し衝突を避ける。

@callback(
    Output("main_tabs", "active_tab", allow_duplicate=True),
    Input("shared_session", "data"),
    prevent_initial_call=True,
)
def _shared_activate_interactive_tab(shared):
    if shared and shared.get("active"):
        return "interactive"
    return no_update
