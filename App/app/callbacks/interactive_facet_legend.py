# =============================================================================
# MSI Analysis Application - クリック可能な共有クラスタ凡例 (ver29.0 / 修正 ver29.1)
# サンプル別/分割表示の上部に置く「凡例だけの Plotly グラフ」(display_helpers.
# cluster_legend_figure) の凡例クリックを、当該ビューの「灰色化ストア」へ反映する。
# 単一クリック=トグル非表示 / ダブルクリック=単独表示 (Plotly ネイティブ)。
#
# ver29.1: 以前は exclude ドロップダウンに連動していたが、exclude は df からセルを
# 完全除去するため背景の灰色スポットも消えていた。凡例は色トレースだけを隠して
# 灰色背景を残す挙動が正しいので、専用の legend_hidden ストアに切り替えた。
# exclude ドロップダウン(「完全に除去」)は別操作として独立を維持する。
#
# 仕組み: 凡例クリックで restyleData が発火 → figure State に反映された各トレースの
# visible を読み、legendonly のもの(=非表示)の meta(クラスタ id)を集めてストアへ。
# ビルダは legend_hidden のクラスタの「色トレースのみ」を描かず灰色背景は残す。
# プログラム的な figure 差し替えでは restyleData は発火しないためループしない。
# =============================================================================

import logging

from dash import Input, Output, State, callback
from dash.exceptions import PreventUpdate

logger = logging.getLogger("msi.interactive.facet_legend")


def _register(legend_id, hidden_store_id):
    """legend_id の凡例クリック → hidden_store_id(灰色化クラスタ list) を更新するコールバックを登録。"""

    @callback(
        Output(hidden_store_id, "data", allow_duplicate=True),
        Input(legend_id, "restyleData"),
        State(legend_id, "figure"),
        prevent_initial_call=True,
    )
    def _legend_to_hidden(restyle, fig):
        if not restyle or not fig:
            raise PreventUpdate
        data = fig.get("data", []) or []
        hidden = [t.get("meta") for t in data
                  if t.get("visible") == "legendonly" and t.get("meta") is not None]
        return hidden

    return _legend_to_hidden


# メイン + フルスクリーン × UMAP + Spatial の 4 ビュー。
# メインと FS は同一モダリティで灰色化状態を共有する。
_register("umap_shared_legend", "umap_legend_hidden_store")
_register("fs_umap_shared_legend", "umap_legend_hidden_store")
_register("spatial_shared_legend", "spatial_legend_hidden_store")
_register("fs_spatial_shared_legend", "spatial_legend_hidden_store")
