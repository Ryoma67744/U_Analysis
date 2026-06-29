# =============================================================================
# MSI Analysis Application - クリック可能な共有クラスタ凡例 (ver29.0)
# サンプル別/分割表示の上部に置く「凡例だけの Plotly グラフ」(display_helpers.
# cluster_legend_figure) の凡例クリックを、当該ビューの exclude ドロップダウン値へ
# 反映する。単一クリック=トグル非表示 / ダブルクリック=単独表示 (Plotly ネイティブ)。
#
# 仕組み: 凡例クリックで restyleData が発火 → figure State に反映された各トレースの
# visible を読み、legendonly のもの(=非表示)の meta(クラスタ id)を集めて exclude へ。
# 既存 exclude パイプラインが全タイル+凡例を再構築し双方向同期する。
# プログラム的な figure 差し替えでは restyleData は発火しないためループしない。
# =============================================================================

import logging

from dash import Input, Output, State, callback
from dash.exceptions import PreventUpdate

logger = logging.getLogger("msi.interactive.facet_legend")


def _register(legend_id, exclude_id):
    """legend_id の凡例クリック → exclude_id ドロップダウン値 を更新するコールバックを登録。"""

    @callback(
        Output(exclude_id, "value", allow_duplicate=True),
        Input(legend_id, "restyleData"),
        State(legend_id, "figure"),
        prevent_initial_call=True,
    )
    def _legend_to_exclude(restyle, fig):
        if not restyle or not fig:
            raise PreventUpdate
        data = fig.get("data", []) or []
        hidden = [t.get("meta") for t in data
                  if t.get("visible") == "legendonly" and t.get("meta") is not None]
        return hidden

    return _legend_to_exclude


# メイン + フルスクリーン × UMAP + Spatial の 4 ビューを登録。
_register("umap_shared_legend", "umap_exclude_cluster")
_register("fs_umap_shared_legend", "fs_umap_exclude_cluster")
_register("spatial_shared_legend", "spatial_exclude_cluster")
_register("fs_spatial_shared_legend", "fs_spatial_exclude_cluster")
