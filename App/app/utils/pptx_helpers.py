"""PPTX エクスポート用ヘルパー関数群。

interactive_callbacks.py から抽出した PPTX 生成ユーティリティ。
"""

import logging
import uuid
from concurrent.futures import Future, ProcessPoolExecutor
from io import BytesIO
from typing import Optional

import plotly.graph_objects as go
import plotly.io as pio
from lxml import etree
from pptx.dml.color import RGBColor
from pptx.util import Inches, Pt

from app.utils.color_utils import cluster_display_name, cluster_sort_key

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# PNG conversion
# ---------------------------------------------------------------------------

def fig_to_png_bytes(fig_dict, width=1200, height=800, scale=2):
    """Plotly figure dict を PNG バイト列に変換する。
    kaleido が未インストールの場合は None を返す。"""
    try:
        fig = go.Figure(fig_dict)
        fig.update_layout(paper_bgcolor="white", plot_bgcolor="white")
        return pio.to_image(fig, format="png", width=width, height=height, scale=scale)
    except Exception:
        return None


class RenderQueue:
    """kaleido PNG レンダリングを ProcessPool で並列化するキュー。

    kaleido は Chromium プロセスを介したレンダリング (1 回 200-500ms) で、
    内部状態がプロセス単位のため ThreadPool では並列化できない。
    ProcessPoolExecutor で各 worker が独立した Chromium を持つことで真の並列化を実現する。

    使い方:
        with RenderQueue(max_workers=4) as q:
            fut1 = q.submit(fig_dict1, 800, 600, 2)
            fut2 = q.submit(fig_dict2, 1200, 800, 2)
            # ... 他の処理を進める間にバックグラウンドでレンダリング
            png1 = fut1.result()  # ここで block (すでに並列で進んでいる)
            png2 = fut2.result()

    注意:
        - 各 worker は独立した kaleido / Chromium を起動するため初期化 1-2 秒のオーバーヘッド
          (worker あたり 50-100MB の RAM を消費)
        - fig_dict は pickle 可能な dict であること (plotly Figure を .to_dict() したもの)
        - context manager 外で submit() を呼ぶと RuntimeError
    """

    def __init__(self, max_workers: int = 4):
        self._pool: Optional[ProcessPoolExecutor] = None
        self._max_workers = max_workers

    def __enter__(self) -> "RenderQueue":
        self._pool = ProcessPoolExecutor(max_workers=self._max_workers)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=True)
            self._pool = None

    def submit(self, fig_dict, width: int = 1200, height: int = 800,
               scale: int = 2) -> Future:
        """fig_dict のレンダリングをバックグラウンドで開始。Future を返す。"""
        if self._pool is None:
            raise RuntimeError(
                "RenderQueue は context manager (with) として使ってください"
            )
        return self._pool.submit(fig_to_png_bytes, fig_dict, width, height, scale)


# ---------------------------------------------------------------------------
# Slide helpers
# ---------------------------------------------------------------------------

def pptx_add_title_bar(slide, title_text):
    """PPTX スライドにタイトルバーを追加する。"""
    txBox = slide.shapes.add_textbox(Inches(0.3), Inches(0.15), Inches(8), Inches(0.5))
    p = txBox.text_frame.paragraphs[0]
    p.text = title_text
    p.font.size = Pt(22)
    p.font.bold = True
    p.font.color.rgb = RGBColor(0x33, 0x33, 0x33)


def pptx_add_image(slide, png_bytes, left, top, width, height):
    """PNG バイト列を PPTX スライドの指定位置に配置する。"""
    if png_bytes:
        img_stream = BytesIO(png_bytes)
        slide.shapes.add_picture(img_stream, left, top, width, height)


def pptx_add_image_preserve_ratio(slide, png_bytes, left, top,
                                   max_width, max_height,
                                   png_w=None, png_h=None):
    """PNG 元比率を保持して max_width × max_height 内に収まるよう配置する。

    png_w, png_h: PNG 生成時のピクセル寸法（比率計算に使用）。
    未指定時は max_width × max_height をそのまま使用（従来互換）。
    """
    if not png_bytes:
        return
    if png_w and png_h:
        aspect = png_w / png_h
        max_aspect = max_width / max_height if max_height else 1.0
        if max_aspect > aspect:
            # 高さ制約: height = max_height, width = height * aspect
            h = max_height
            w = int(h * aspect)
        else:
            # 幅制約: width = max_width, height = width / aspect
            w = max_width
            h = int(w / aspect)
        # 枠内中央寄せ
        left = left + int((max_width - w) / 2)
        top = top + int((max_height - h) / 2)
    else:
        w, h = max_width, max_height
    img_stream = BytesIO(png_bytes)
    slide.shapes.add_picture(img_stream, left, top, w, h)


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def square_tile_dims(tile_w, row_h, margin=0.95):
    """正方形画像のタイル配置寸法を計算する。

    Returns:
        (side, side, offset): 正方形の辺長と、タイル内の左オフセット
    """
    avail_w = tile_w * margin
    side = min(avail_w, row_h)
    offset = (tile_w - side) / 2
    return side, side, offset


# ---------------------------------------------------------------------------
# Legend figures
# ---------------------------------------------------------------------------

def build_cluster_legend_fig(cluster_list, color_map, font_size=18,
                             marker_size=14, cluster_name_map=None):
    """クラスタレジェンド専用のPlotly Figureを生成"""
    fig = go.Figure()
    for cl in sorted(cluster_list, key=cluster_sort_key):
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=marker_size,
                        color=color_map.get(str(cl), "#999")),
            name=cluster_display_name(cl, cluster_name_map), showlegend=True,
        ))
    fig.update_layout(
        showlegend=True,
        legend=dict(
            font=dict(size=font_size),
            itemsizing="constant",
            yanchor="middle", y=0.5,
            xanchor="center", x=0.5,
            tracegroupgap=4,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        plot_bgcolor="white", paper_bgcolor="white",
        width=200, height=600,
    )
    return fig


def build_sample_legend_fig(sample_list, sample_color_map, name_map=None,
                             font_size=18, marker_size=14):
    """サンプルレジェンド専用のPlotly Figureを生成"""
    fig = go.Figure()
    for s in sorted(sample_list):
        display = (name_map or {}).get(s, s) if name_map else s
        fig.add_trace(go.Scatter(
            x=[None], y=[None], mode="markers",
            marker=dict(size=marker_size,
                        color=sample_color_map.get(str(s), "#999")),
            name=str(display), showlegend=True,
        ))
    fig.update_layout(
        showlegend=True,
        legend=dict(
            font=dict(size=font_size),
            itemsizing="constant",
            yanchor="middle", y=0.5,
            xanchor="center", x=0.5,
            tracegroupgap=4,
        ),
        margin=dict(l=0, r=0, t=0, b=0),
        xaxis=dict(visible=False), yaxis=dict(visible=False),
        plot_bgcolor="white", paper_bgcolor="white",
        width=200, height=600,
    )
    return fig


# ---------------------------------------------------------------------------
# Presentation sections
# ---------------------------------------------------------------------------

def pptx_add_sections(prs, section_map):
    """PPTX にセクション情報を追加する（PowerPoint のセクションパネルに表示される）。

    Args:
        prs: python-pptx Presentation オブジェクト
        section_map: list of (section_name, start_slide_idx, end_slide_idx)
            slide indices は 0-based、両端を含む。
    """
    p_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    p14_ns = "http://schemas.microsoft.com/office/powerpoint/2010/main"
    ext_uri = "{521415D9-36F7-43E2-AB2F-B90AF26B5E84}"

    prs_elem = prs.element  # CT_Presentation lxml element

    # p14 名前空間プレフィックスを登録（PowerPoint互換性のため）
    etree.register_namespace("p14", p14_ns)

    # sldIdLst から全スライド ID を取得
    sldIdLst = prs_elem.find(f"{{{p_ns}}}sldIdLst")
    if sldIdLst is None:
        return

    sld_ids = []
    for sldId in sldIdLst:
        sid = sldId.get("id")
        if sid:
            sld_ids.append(sid)
    if not sld_ids:
        return

    # extLst を検索または作成
    extLst = prs_elem.find(f"{{{p_ns}}}extLst")
    if extLst is None:
        extLst = etree.SubElement(prs_elem, f"{{{p_ns}}}extLst")

    # ext 要素を作成（セクション用 URI）
    ext = etree.SubElement(extLst, f"{{{p_ns}}}ext")
    ext.set("uri", ext_uri)

    # sectionLst を作成
    sectionLst = etree.SubElement(ext, f"{{{p14_ns}}}sectionLst")

    for sec_name, start_idx, end_idx in section_map:
        section = etree.SubElement(sectionLst, f"{{{p14_ns}}}section")
        section.set("name", sec_name)
        section.set("id", "{" + str(uuid.uuid4()).upper() + "}")

        sldIdLst_sec = etree.SubElement(section, f"{{{p14_ns}}}sldIdLst")

        for i in range(start_idx, min(end_idx + 1, len(sld_ids))):
            sldId_ref = etree.SubElement(sldIdLst_sec, f"{{{p14_ns}}}sldId")
            sldId_ref.set("id", sld_ids[i])
