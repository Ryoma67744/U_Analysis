"""display_helpers.py - UMAP 表示用ユーティリティ関数

interactive_callbacks.py から抽出した表示名変換・数値フォーマット・
UMAP 軸矢印画像生成のヘルパー関数群。
"""

import base64
import io
import logging

import math

import numpy as np
import plotly.graph_objects as go
from dash import html, dcc

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# サンプル別/分割表示: 共有クラスタ凡例 + タイル枠ヘルパー (ver28.0)
# 各図の個別凡例・枠を廃し、上部に共有凡例1つ＋各図を縦線で区切るための部品。
# ---------------------------------------------------------------------------

def cluster_legend_row(color_map, cluster_name_map=None):
    """サンプル別/分割表示の上部に置く「共有クラスタ凡例」(横一列・折返し)。

    color_map: {cluster_str: hex} 全クラスタ。cluster_sort_key 順に色四角＋表示名を並べる。
    """
    from app.utils.color_utils import cluster_sort_key, cluster_display_name
    items = [html.Span("クラスタ", className="legend-item",
                       style={"fontWeight": "600", "color": "#555",
                              "marginRight": "2px"})]
    for cl in sorted(color_map.keys(), key=cluster_sort_key):
        items.append(html.Span([
            html.Span(className="legend-swatch",
                      style={"backgroundColor": color_map.get(str(cl), "#999999")}),
            cluster_display_name(cl, cluster_name_map),
        ], className="legend-item"))
    return html.Div(items, className="facet-legend")


def cluster_legend_figure(color_map, cluster_name_map=None, hidden=None):
    """クリック可能な「凡例だけ」の Plotly figure を返す (ver29.0)。

    各クラスタを 1 ダミートレースとして横並び凡例にする。Plotly ネイティブ凡例の
    クリック=トグル非表示 / ダブルクリック=単独表示 をそのまま使う。トレースの
    ``meta`` にクラスタ id を持たせ、クリック後の visible からどのクラスタが
    非表示(legendonly)かを復元できるようにする。``hidden`` のクラスタは初期状態で
    legendonly(淡色)にして「灰色化ストア」と同期する (ver29.1)。
    """
    from app.utils.color_utils import cluster_sort_key, cluster_display_name
    hidden_set = {str(c) for c in (hidden or [])}
    fig = go.Figure()
    for cl in sorted(color_map.keys(), key=cluster_sort_key):
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=11, color=color_map.get(str(cl), "#999999")),
            name=cluster_display_name(cl, cluster_name_map),
            meta=str(cl), showlegend=True,
            visible=("legendonly" if str(cl) in hidden_set else True),
        ))
    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="h", itemsizing="constant",
                    itemclick="toggle", itemdoubleclick="toggleothers",
                    font=dict(size=11), x=0, y=0.5, xanchor="left",
                    yanchor="middle", tracegroupgap=0),
        margin=dict(l=2, r=2, t=2, b=2),
        xaxis=dict(visible=False, range=[0, 1]),
        yaxis=dict(visible=False, range=[0, 1]),
        plot_bgcolor="white", paper_bgcolor="white",
    )
    return fig


def _legend_graph_height(n_clusters):
    """横並び凡例の概算高さ(px)。1行に約10件、1行22px + 余白。"""
    rows = max(1, math.ceil((n_clusters or 1) / 10))
    return 24 + rows * 22


def facet_block(tiles, color_map, cluster_name_map=None, show_legend=True,
                outer_style=None, legend_id=None, hidden=None):
    """共有凡例(任意) + タイル群(縦線区切り) をまとめた html.Div を返す。

    tiles: className="facet-tile" を持つ図 Div のリスト。
    show_legend=True かつ color_map があるとき、上部に共有クラスタ凡例を1つ付ける。
    legend_id を渡すと、静的 HTML 凡例の代わりにクリック可能な凡例グラフ(dcc.Graph)を
    描画する (ver29.0)。hidden は初期 legendonly(灰色化)の同期に使う (ver29.1)。
    """
    children = []
    if show_legend and color_map:
        if legend_id:
            children.append(dcc.Graph(
                id=legend_id,
                figure=cluster_legend_figure(color_map, cluster_name_map, hidden),
                config={"displayModeBar": False},
                style={"height": f"{_legend_graph_height(len(color_map))}px"},
                className="facet-legend-graph",
            ))
        else:
            children.append(cluster_legend_row(color_map, cluster_name_map))
    children.append(html.Div(tiles, className="facet-tiles"))
    return html.Div(children, style=outer_style or {})


# ---------------------------------------------------------------------------
# uirevision ヘルパー (ver46.1)
# ---------------------------------------------------------------------------
# Plotly は figure を差し替えるたびにズーム/パンを既定へリセットする。layout.uirevision
# の値が前回と同じなら UI 状態(ズーム/パン/凡例のトグル)を保持するため、「見た目だけの
# 変更」ではユーザーの視野を壊さずに済む。
#
# 逆に、座標そのものが変わる操作(サンプル切替・回転・反転・座標系の切替)では視野を
# 保持すると「前のサンプルのズーム位置に固定されて何も見えない」状態になるため、
# 値を変えて明示的にリセットさせる。
# ---------------------------------------------------------------------------

def geom_uirevision(*parts) -> str:
    """図の「幾何」を一意に表す uirevision 文字列を返す。

    parts には座標そのものを変える要素だけを渡す(サンプル名・回転角・反転・座標系)。
    マーカーサイズ・色・ラベル・凡例・強度レンジのような見た目だけの要素は
    **渡さない**（渡すとその操作のたびにズームがリセットされてしまう）。
    """
    return "|".join("" if p is None else str(p) for p in parts)


def transform_uirevision(sample, transform, extra=None) -> str:
    """per-sample の回転/反転 dict から uirevision を作る共通ヘルパー。

    transform: {"angle": int, "flip_h": bool, "flip_v": bool}（旧形式の int も許容）。
    extra: 座標系を切り替える追加要素（例: H&E 射影座標 か MSI 空間座標か）。
    """
    if isinstance(transform, (int, float)):
        transform = {"angle": int(transform), "flip_h": False, "flip_v": False}
    transform = transform or {}
    return geom_uirevision(
        sample,
        transform.get("angle", 0),
        bool(transform.get("flip_h", False)),
        bool(transform.get("flip_v", False)),
        extra,
    )


# ---------------------------------------------------------------------------
# 表示名ヘルパー
# ---------------------------------------------------------------------------

def display_name(original: str, name_map: dict | None) -> str:
    """元のサンプル名をユーザー指定の表示名に変換する。マップが空なら元名をそのまま返す。"""
    if not name_map:
        return original
    return name_map.get(original, original)


def compact_sci(v):
    """数値をコンパクトな指数表記に変換: 280000 → '2.8e5'"""
    if v == 0:
        return "0"
    exp = int(np.floor(np.log10(abs(v))))
    coeff = v / (10 ** exp)
    return f"{coeff:.1f}e{exp}"


def format_plain_number(v):
    """数値を e 表記なしのプレーンな文字列で返す。
    0.00123 → '0.00123', 280000 → '280000', 3.5 → '3.5'"""
    if v == 0:
        return "0"
    if abs(v) >= 1:
        # 整数表示可能ならそうする
        if v == int(v):
            return str(int(v))
        return f"{v:.1f}"
    # 小数の場合、有効数字を維持しつつ e なし
    return f"{v:.6g}"


def generate_umap_arrow_image():
    """参照画像と同じスタイルのL字型UMAP軸画像をbase64 PNGで生成する（キャッシュ付き）"""
    if hasattr(generate_umap_arrow_image, "_cache"):
        return generate_umap_arrow_image._cache

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig_mpl, ax = plt.subplots(figsize=(1.6, 1.6), dpi=150)
    ax.set_xlim(-0.35, 1.15)
    ax.set_ylim(-0.35, 1.15)
    ax.set_aspect("equal")
    ax.axis("off")
    fig_mpl.patch.set_alpha(0.0)
    ax.patch.set_alpha(0.0)

    lw = 3.5
    hl = 0.08   # 矢印ヘッドの長さ
    hw = 0.06   # 矢印ヘッドの幅

    # 水平矢印（UMAP1）
    ax.annotate("", xy=(1.0, 0.0), xytext=(0.0, 0.0),
                arrowprops=dict(arrowstyle="->, head_length={}, head_width={}".format(hl * 3, hw * 3),
                                lw=lw, color="black"))
    # 垂直矢印（UMAP2）
    ax.annotate("", xy=(0.0, 1.0), xytext=(0.0, 0.0),
                arrowprops=dict(arrowstyle="->, head_length={}, head_width={}".format(hl * 3, hw * 3),
                                lw=lw, color="black"))

    # ラベル
    ax.text(1.0, -0.18, "UMAP1", fontsize=14, fontweight="bold",
            ha="center", va="top", color="black")
    ax.text(-0.18, 1.0, "UMAP2", fontsize=14, fontweight="bold",
            ha="center", va="center", rotation=90, color="black")

    buf = io.BytesIO()
    fig_mpl.savefig(buf, format="png", bbox_inches="tight",
                    transparent=True, pad_inches=0.05)
    plt.close(fig_mpl)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    data_uri = f"data:image/png;base64,{b64}"

    generate_umap_arrow_image._cache = data_uri
    return data_uri


def add_umap_arrows(fig):
    """UMAPプロットの左下にL字型 UMAP1/UMAP2 軸画像を埋め込む"""
    arrow_src = generate_umap_arrow_image()
    fig.add_layout_image(
        source=arrow_src,
        x=-0.02, y=-0.02,
        xref="paper", yref="paper",
        sizex=0.22, sizey=0.22,
        xanchor="left", yanchor="bottom",
        layer="above",
        opacity=1.0,
    )
