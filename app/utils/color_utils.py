# =============================================================================
# MSI Analysis Application - Color Utility Functions
# クラスタ・サンプルの色マップ生成ユーティリティ
# =============================================================================

import logging
import re

logger = logging.getLogger("msi.color_utils")

from app.config import CLUSTER_PRESET_COLORS, DESI_COLORS_50, HIGHLIGHT_GRAY


def cluster_sort_key(x):
    """クラスタIDの統一ソートキー
    "3-a" → (3, "a"), "3" → (3, ""), "abc" → (inf, "abc")
    """
    s = str(x)
    # "3-a" 形式の分解
    m = re.match(r'^(\d+)-([a-z]+)$', s)
    if m:
        return (int(m.group(1)), m.group(2))
    if s.isdigit():
        return (int(s), "")
    return (float("inf"), s)


def get_cluster_color_map(clusters, custom_colors=None):
    """クラスタ値のリストから、ソート済みの色マップ dict を返す。
    custom_colors が指定された場合、デフォルト色をカスタム色で上書きする。"""
    str_cls = list(set(str(c) for c in clusters))
    str_cls.sort(key=cluster_sort_key)
    cmap = {cl: CLUSTER_PRESET_COLORS[i % len(CLUSTER_PRESET_COLORS)] for i, cl in enumerate(str_cls)}
    if custom_colors:
        cmap.update(custom_colors)
    return cmap


def adjust_color_lightness(hex_color, factor):
    """HEX カラーの明度を調整する。factor > 1 で明るく、< 1 で暗く。"""
    hex_color = hex_color.lstrip('#')
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    r = min(255, max(0, int(r + (255 - r) * (factor - 1) * 0.5)))
    g = min(255, max(0, int(g + (255 - g) * (factor - 1) * 0.5)))
    b = min(255, max(0, int(b + (255 - b) * (factor - 1) * 0.5)))
    return f'#{r:02x}{g:02x}{b:02x}'


def get_merged_cluster_color_map(clusters, mode="shade", custom_colors=None):
    """マージクラスタ用カラーマップ

    mode="shade": 親クラスタの色を濃淡バリエーションで展開
      例: 親 3 = #1f77b4 → 3-a=#1f77b4, 3-b=明るい, 3-c=さらに明るい
    mode="independent": 全サブクラスタに独立した新色を割り当て
    """
    str_cls = list(set(str(c) for c in clusters))
    str_cls.sort(key=cluster_sort_key)

    if mode == "independent":
        # 全クラスタに独立色を割り当て
        cmap = {cl: CLUSTER_PRESET_COLORS[i % len(CLUSTER_PRESET_COLORS)]
                for i, cl in enumerate(str_cls)}
    else:
        # shade モード: 親クラスタごとに色を展開
        # まず親クラスタ（サブクラスタを持たないものも含む）の色を決定
        parent_set = set()
        for cl in str_cls:
            m = re.match(r'^(\d+)-[a-z]+$', cl)
            if m:
                parent_set.add(m.group(1))
            else:
                parent_set.add(cl)
        parent_sorted = sorted(parent_set, key=cluster_sort_key)
        parent_colors = {p: CLUSTER_PRESET_COLORS[i % len(CLUSTER_PRESET_COLORS)]
                         for i, p in enumerate(parent_sorted)}

        cmap = {}
        # 親クラスタごとのサブクラスタ数を計算
        parent_subclusters = {}
        for cl in str_cls:
            m = re.match(r'^(\d+)-([a-z]+)$', cl)
            if m:
                p = m.group(1)
                parent_subclusters.setdefault(p, []).append(cl)

        for cl in str_cls:
            m = re.match(r'^(\d+)-([a-z]+)$', cl)
            if m:
                parent = m.group(1)
                base_color = parent_colors.get(parent, "#999999")
                subs = parent_subclusters.get(parent, [cl])
                idx = subs.index(cl) if cl in subs else 0
                n_subs = len(subs)
                if n_subs <= 1:
                    factor = 1.0
                else:
                    # 0番目=元の色, 最後=最も明るい
                    factor = 1.0 + (idx / (n_subs - 1)) * 1.2
                cmap[cl] = adjust_color_lightness(base_color, factor)
            else:
                # 通常クラスタはそのまま親の色
                cmap[cl] = parent_colors.get(cl, "#999999")

    if custom_colors:
        cmap.update(custom_colors)
    return cmap


def cluster_display_name(cl_id, name_map):
    """クラスタ表示名を返す。name_map に定義があればそれを使い、なければ 'Cluster {id}'"""
    if name_map and str(cl_id) in name_map:
        dn = name_map[str(cl_id)].strip()
        if dn:
            return dn
    return str(cl_id)


def get_sample_color_map(samples):
    """サンプル名のリストからサンプル色マップを生成（DESI_COLORS_50 使用）"""
    sorted_samples = sorted(set(str(s) for s in samples))
    return {s: DESI_COLORS_50[i % len(DESI_COLORS_50)]
            for i, s in enumerate(sorted_samples)}


def get_cluster_colorscale(clusters, custom_colors=None):
    """Scattergl用: 数値インデックスベースのcolorscale情報を返す。

    HEX文字列配列をmarker.colorに渡すとWebGL内部処理で色ミスマッチが
    生じるため、数値+colorscaleで確実に色を指定する。

    Returns:
        cluster_to_idx: dict[str, int] — クラスタ文字列→0-based数値インデックス
        discrete_colorscale: list — Plotly colorscale形式
    """
    str_cls = list(set(str(c) for c in clusters))
    str_cls.sort(key=cluster_sort_key)
    n = max(len(str_cls), 1)
    cluster_to_idx = {cl: i for i, cl in enumerate(str_cls)}

    # discrete colorscale: 各色が均等な範囲を占める
    colorscale = []
    for i, cl in enumerate(str_cls):
        low = i / n
        high = (i + 1) / n
        color = CLUSTER_PRESET_COLORS[i % len(CLUSTER_PRESET_COLORS)]
        if custom_colors and cl in custom_colors:
            color = custom_colors[cl]
        colorscale.append([low, color])
        colorscale.append([high, color])

    return cluster_to_idx, colorscale
