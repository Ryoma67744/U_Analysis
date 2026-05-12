# =============================================================================
# MSI Analysis Application - Interactive PPTX Export
# インタラクティブ解析 PPTX エクスポート コールバック
#
# interactive_callbacks.py から分離された PPTX 関連の
# ヘルパー関数・コールバックをまとめたモジュール。
# =============================================================================

import json
import logging
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import (Input, Output, State, callback, dcc, no_update)
from dash.exceptions import PreventUpdate

from app.services.seurat_bridge import SeuratBridge
from app.utils.color_utils import (
    cluster_sort_key as _cluster_sort_key,
    get_cluster_color_map as _get_cluster_color_map,
    cluster_display_name as _cluster_display_name,
)
from app.utils.display_helpers import (
    display_name as _display_name,
)
from app.utils.deg_utils import (
    is_meaningful_annotation as _is_meaningful_annotation,
    get_top_n_features_for_cluster as _get_top_n_features_for_cluster,
)
from app.utils.label_persistence import (
    compute_annotation_offsets as _compute_annotation_offsets,
    load_label_positions as _load_label_positions_util,
)
from app.utils.pptx_helpers import (
    fig_to_png_bytes as _fig_to_png_bytes,
    pptx_add_title_bar as _pptx_add_title_bar,
    pptx_add_image as _pptx_add_image,
    pptx_add_image_preserve_ratio as _pptx_add_image_preserve_ratio,
    square_tile_dims as _square_tile_dims,
    build_cluster_legend_fig as _build_cluster_legend_fig,
    pptx_add_sections as _pptx_add_sections,
)

# 共有状態・ヘルパーを分離モジュールから参照
from app.callbacks.interactive_callbacks import (
    _interactive_data,
    _bridge,
    _load_deg_results,
    _load_label_positions,
)
from app.callbacks.interactive_umap import (
    _build_umap_integrated_fig,
    _get_merged_label_positions,
)
from app.callbacks.interactive_spatial import (
    _create_single_spatial_fig,
    _transform_coords,
    _calc_zero_gap_marker_size,
)
from app.callbacks.interactive_calibration import (
    _build_mz_to_compound_map,
    _annotate_gene_labels,
)

logger = logging.getLogger("msi.interactive.pptx")


# ---------------------------------------------------------------------------
# PPTX (Google Slides) エクスポート
# ---------------------------------------------------------------------------

def _build_feature_plot_fig(df, feature_name, cache_dir_path, rds_path,
                            rotation_store=None, name_map=None, marker_size=2,
                            colorbar_tickformat=None, show_colorbar_title=True,
                            auto_marker_size=False, show_colorbar=True):
    """単一 m/z Feature の Spatial Expression Plot figure を生成（PPTX 用）。

    Returns:
        go.Figure or None
    """
    from plotly.subplots import make_subplots

    if rotation_store is None:
        rotation_store = {}
    if name_map is None:
        name_map = {}

    # 発現データ取得
    expression = None
    if cache_dir_path:
        cache_dir = Path(cache_dir_path) if isinstance(cache_dir_path, str) else cache_dir_path
        expression = _bridge.get_feature_expression_fast(cache_dir, feature_name)
    if expression is None and rds_path:
        try:
            expression = _bridge.get_feature_expression(rds_path, feature_name)
        except Exception:
            return None
    if expression is None:
        return None

    df_plot = df.copy()
    df_plot["_expression"] = expression.values if hasattr(expression, "values") else expression

    if "SpatialX" not in df_plot.columns:
        return None

    samples = sorted(df_plot["Sample"].unique())
    n_samples = len(samples)
    subplot_titles = [_display_name(s, name_map) for s in samples]

    fig = make_subplots(
        rows=1, cols=n_samples,
        subplot_titles=subplot_titles,
        horizontal_spacing=0.03,
    )

    expr_vals = df_plot["_expression"].values
    global_min = float(np.nanmin(expr_vals))
    global_max = float(np.nanmax(expr_vals))

    for idx, s in enumerate(samples, 1):
        df_s = df_plot[df_plot["Sample"] == s]
        transform = rotation_store.get(
            s, rotation_store.get("__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
        if isinstance(transform, (int, float)):
            transform = {"angle": int(transform), "flip_h": False, "flip_v": False}

        raw_x = df_s["SpatialX"].values
        raw_y = -df_s["SpatialY"].values
        plot_x, plot_y = _transform_coords(
            raw_x, raw_y,
            transform.get("angle", 0),
            flip_h=transform.get("flip_h", False),
            flip_v=transform.get("flip_v", False),
        )

        # サンプル別マーカーサイズ自動計算
        ms = marker_size
        if auto_marker_size and len(plot_x) > 1:
            sorted_ux = np.sort(np.unique(plot_x))
            if len(sorted_ux) > 1:
                min_sp = float(np.min(np.diff(sorted_ux)))
                xr = float(plot_x.max() - plot_x.min())
                yr = float(plot_y.max() - plot_y.min()) if len(plot_y) > 1 else 1.0
                # Feature Plot は height=280 描画、margin 約40px → 有効240px
                eff_h = 240
                if yr > 0 and xr > 0:
                    eff_w = eff_h * (xr / yr)
                    ms = max(2, round(min_sp * eff_w / xr * 1.5))

        # TIC 背景
        if "TotalCount" in df_s.columns:
            fig.add_trace(go.Scatter(
                x=plot_x, y=plot_y, mode="markers",
                marker=dict(size=ms, symbol="square", color=df_s["TotalCount"].values,
                            colorscale="Greys", opacity=0.5, showscale=False),
                hoverinfo="skip", showlegend=False,
            ), row=1, col=idx)

        # 発現量オーバーレイ
        is_last = (idx == n_samples)
        _show_scale = is_last and show_colorbar
        marker_opts = dict(
            size=ms,
            symbol="square",
            color=df_s["_expression"].values,
            colorscale="Plasma",
            cmin=global_min, cmax=global_max,
            showscale=_show_scale,
            opacity=0.8,
        )
        if _show_scale:
            cb_opts = dict(
                len=0.8, thickness=15,
                tickvals=[global_min, global_max],
                ticktext=["0%", "100%"],
            )
            if show_colorbar_title:
                cb_opts["title"] = "Intensity"
            marker_opts["colorbar"] = cb_opts
        fig.add_trace(go.Scatter(
            x=plot_x, y=plot_y, mode="markers",
            marker=marker_opts, showlegend=False,
        ), row=1, col=idx)

    fig.update_layout(
        title=dict(text=feature_name, font=dict(size=20), x=0.5),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=10, r=50 if show_colorbar else 10, t=50, b=10),
    )
    # Subplot titles のフォントサイズ拡大
    fig.update_annotations(font_size=16)
    for i in range(1, n_samples + 1):
        fig.update_xaxes(visible=False, row=1, col=i)
        xanchor = f"x{i}" if i > 1 else "x"
        fig.update_yaxes(visible=False, scaleanchor=xanchor, row=1, col=i)

    return fig


def _build_volcano_fig_for_cluster(deg_data, cluster, fc_thresh=0.5, p_thresh=1.3,
                                   marker_size=8):
    """指定クラスタの Volcano Plot figure を生成（PPTX 用）。

    Returns:
        go.Figure or None
    """
    if not deg_data:
        return None
    df = pd.DataFrame(deg_data)
    if "p_val_adj_raw" in df.columns:
        df["p_num"] = pd.to_numeric(df["p_val_adj_raw"], errors="coerce")
    else:
        df["p_num"] = pd.to_numeric(df["p_val_adj"], errors="coerce")
    df["avg_log2FC"] = pd.to_numeric(df["avg_log2FC"], errors="coerce")
    min_nonzero_p = (
        df.loc[df["p_num"] > 0, "p_num"].min()
        if (df["p_num"] > 0).any()
        else 5e-324
    )
    df["neg_log10_p"] = -np.log10(df["p_num"].clip(lower=min_nonzero_p))

    if "annotation" in df.columns:
        df["display_text"] = df.apply(
            lambda r: f"{r['gene']}\n({r['annotation']})"
            if _is_meaningful_annotation(r.get("annotation", ""), r.get("gene", ""))
            else r["gene"],
            axis=1,
        )
    else:
        df["display_text"] = df["gene"]

    df = df[df["cluster"].astype(str) == str(cluster)]
    if df.empty:
        return None

    fig = go.Figure()
    for reg, color, label in [
        ("Up", "#FF2D2D", "Up-regulated"),
        ("Down", "#1E5BFF", "Down-regulated"),
        ("NS", "#7A7A7A", "Not significant"),
    ]:
        if reg == "Up":
            mask = (df["neg_log10_p"] >= p_thresh) & (df["avg_log2FC"] >= fc_thresh)
        elif reg == "Down":
            mask = (df["neg_log10_p"] >= p_thresh) & (df["avg_log2FC"] <= -fc_thresh)
        else:
            mask = ~(
                (df["neg_log10_p"] >= p_thresh) & (df["avg_log2FC"].abs() >= fc_thresh)
            )
        sub = df[mask]
        if len(sub) > 0:
            fig.add_trace(go.Scattergl(
                x=sub["avg_log2FC"], y=sub["neg_log10_p"],
                mode="markers",
                marker=dict(size=marker_size, color=color, opacity=0.7),
                name=label, text=sub["display_text"],
                hovertemplate=(
                    "<b>%{text}</b><br>"
                    "log2FC: %{x:.3f}<br>"
                    "-log10(p): %{y:.2f}<extra></extra>"
                ),
            ))

    fig.add_hline(y=p_thresh, line_dash="dash", line_color="gray", opacity=0.5)
    fig.add_vline(x=fc_thresh, line_dash="dash", line_color="gray", opacity=0.5)
    fig.add_vline(x=-fc_thresh, line_dash="dash", line_color="gray", opacity=0.5)

    # --- Top N アノテーション（PPTX用） ---
    sig_mask = (df["neg_log10_p"] >= p_thresh) & (df["avg_log2FC"].abs() >= fc_thresh)
    sig_df = df[sig_mask]
    if not sig_df.empty:
        _top_up = sig_df[sig_df["avg_log2FC"] > 0].nlargest(5, "avg_log2FC")
        _top_down = sig_df[sig_df["avg_log2FC"] < 0].nsmallest(5, "avg_log2FC")
        _auto_label = pd.concat([_top_up, _top_down])
        if len(_auto_label) > 0:
            _pts = list(zip(
                _auto_label["avg_log2FC"].values,
                _auto_label["neg_log10_p"].values,
                _auto_label["display_text"].values,
            ))
            _offsets = _compute_annotation_offsets(_pts)
            for (_x, _y, _txt), (_ax, _ay) in zip(_pts, _offsets):
                fig.add_annotation(
                    x=_x, y=_y, text=_txt,
                    showarrow=True, arrowhead=0, arrowwidth=1,
                    arrowcolor="#999", ax=_ax, ay=_ay,
                    font=dict(size=9, color="#333"),
                    bgcolor="rgba(255,255,255,0.8)", borderpad=2,
                )

    fig.update_layout(
        title=dict(text=f"Volcano Plot - Cluster {cluster}", font=dict(size=20), x=0.5),
        xaxis_title="avg_log2FC",
        yaxis_title="-log10(p_val_adj)",
        template="plotly_white",
        margin=dict(l=50, r=20, t=40, b=40),
    )
    return fig


def _build_heatmap_for_cluster(deg_data, cluster, df, cache_dir, top_n,
                                mrm_path=None):
    """指定クラスタの Top N マーカーを全クラスタで比較する Z-score ヒートマップを生成。"""
    if not deg_data or not cache_dir:
        return None
    try:
        df_deg = pd.DataFrame(deg_data)
        df_deg["p_num"] = pd.to_numeric(df_deg["p_val_adj"], errors="coerce")
        cl_deg = df_deg[df_deg["cluster"].astype(str) == str(cluster)]
        if cl_deg.empty:
            return None
        top_markers = cl_deg.sort_values("p_num").head(top_n)
        genes = top_markers["gene"].unique().tolist()
        if not genes:
            return None

        cache_dir_p = Path(cache_dir) if isinstance(cache_dir, str) else cache_dir
        expr_path = cache_dir_p / "expression_matrix.parquet"
        if not expr_path.exists():
            return None

        # 利用可能な遺伝子を Parquet schema から一括判定（I/O 1 回で完結）
        import pyarrow.parquet as pq
        try:
            schema_names = set(pq.read_schema(expr_path).names)
        except Exception:
            schema_names = set()
        available = [g for g in genes if g in schema_names]
        if not available:
            return None

        expr_df = pd.read_parquet(expr_path, columns=["CellID"] + available)
        plot_data = df if df is not None else _interactive_data.get("plot_data")
        if plot_data is None:
            return None
        merged = expr_df.merge(
            plot_data[["CellID", "Cluster"]], on="CellID", how="inner")
        cluster_means = merged.groupby("Cluster")[available].mean()
        cluster_means = cluster_means.reindex(
            sorted(cluster_means.index, key=_cluster_sort_key))

        z_data = cluster_means.values.copy()
        col_mean = z_data.mean(axis=0)
        col_std = z_data.std(axis=0)
        col_std[col_std == 0] = 1
        z_data = (z_data - col_mean) / col_std

        # Y軸ラベル（アノテーション付き）
        y_labels = available
        gene_to_annotation = {}
        for r in deg_data:
            gene = r.get("gene", "")
            ann = r.get("annotation", "")
            if gene and _is_meaningful_annotation(ann, gene):
                gene_to_annotation[gene] = ann
        if gene_to_annotation:
            y_labels = [
                f"{g} ({gene_to_annotation[g]})" if g in gene_to_annotation else g
                for g in available
            ]
        elif mrm_path:
            mz_to_compound = _build_mz_to_compound_map(str(mrm_path), tolerance=0.1)
            y_labels = _annotate_gene_labels(available, mz_to_compound, tolerance=0.1)

        fig = go.Figure(go.Heatmap(
            z=z_data.T,
            x=[str(c) for c in cluster_means.index],
            y=y_labels,
            colorscale="RdBu_r",
            zmid=0,
            ygap=2,  # Top N m/z行間の区切り線
            hovertemplate="Cluster: %{x}<br>Gene: %{y}<br>Z-score: %{z:.3f}<extra></extra>",
        ))
        fig.update_layout(
            margin=dict(l=20, r=20, t=30, b=20),
            xaxis=dict(side="bottom"),
            yaxis=dict(autorange="reversed"),
            paper_bgcolor="white",
            plot_bgcolor="white",
        )
        return fig.to_dict()
    except Exception as e:
        print(f"[PPTX] Per-cluster heatmap error (cluster {cluster}): {e}")
        return None


def _build_heatmap_for_pptx(heatmap_fig, deg_data, df, cache_dir, top_n,
                             mrm_path=None):
    """PPTX 用にZ-score + アノテーション付きヒートマップ figure を生成/調整する。

    アプリの heatmap_fig がすでに Z-score ならアノテーションだけ補完し、
    Raw なら expression_matrix から再計算して Z-score + アノテーション付きで返す。
    """
    # ---- deg_data / cache_dir がなければアプリ図をそのまま返す ----
    if not deg_data or not cache_dir:
        return heatmap_fig

    cache_dir_path = Path(cache_dir) if isinstance(cache_dir, str) else cache_dir
    expr_path = cache_dir_path / "expression_matrix.parquet" if cache_dir_path else None

    # ---- アノテーション辞書を構築 ----
    gene_to_annotation = {}
    for r in deg_data:
        gene = r.get("gene", "")
        ann = r.get("annotation", "")
        if gene and _is_meaningful_annotation(ann, gene):
            gene_to_annotation[gene] = ann

    # MRM fallback
    if not gene_to_annotation and mrm_path:
        try:
            mz_to_compound = _build_mz_to_compound_map(mrm_path, tolerance=0.1)
        except Exception:
            mz_to_compound = {}
    else:
        mz_to_compound = {}

    # ---- heatmap_fig が Z-score かどうかを判定 ----
    is_zscore = False
    if heatmap_fig:
        traces = heatmap_fig.get("data", [])
        for t in traces:
            if t.get("type") == "heatmap" and t.get("zmid") == 0:
                is_zscore = True
                break

    # ---- Z-score 済みの場合: アノテーション補完のみ ----
    if is_zscore and heatmap_fig:
        fig = go.Figure(heatmap_fig)
        # Y軸ラベルにアノテーションを補完
        for t in fig.data:
            if hasattr(t, "y") and t.y is not None:
                current_labels = list(t.y)
                needs_update = False
                new_labels = []
                for lbl in current_labels:
                    gene = str(lbl).split(" (")[0]  # 既存アノテーション除去
                    if gene in gene_to_annotation:
                        new_labels.append(f"{gene} ({gene_to_annotation[gene]})")
                        needs_update = True
                    elif mz_to_compound:
                        annotated = _annotate_gene_labels(
                            [gene], mz_to_compound, tolerance=0.1)
                        new_labels.append(annotated[0])
                        if annotated[0] != gene:
                            needs_update = True
                    else:
                        new_labels.append(lbl)
                if needs_update:
                    t.y = new_labels
        # 左マージンを再調整
        all_labels = []
        for t in fig.data:
            if hasattr(t, "y") and t.y is not None:
                all_labels.extend(str(l) for l in t.y)
        if all_labels:
            max_len = max(len(l) for l in all_labels)
            left_margin = min(max(max_len * 7, 120), 350)
            fig.update_layout(margin=dict(l=left_margin))
        return fig.to_dict()

    # ---- Raw / heatmap_fig がない場合: expression_matrix から新規生成 ----
    if not expr_path or not expr_path.exists():
        return heatmap_fig  # fallback: アプリ図をそのまま
    if df is None or df.empty:
        return heatmap_fig

    try:
        df_deg = pd.DataFrame(deg_data)
        df_deg["p_num"] = pd.to_numeric(df_deg.get("p_val_adj", ""), errors="coerce")
        top_markers = df_deg.sort_values("p_num").groupby("cluster").head(top_n)
        genes = top_markers["gene"].unique().tolist()
        if not genes:
            return heatmap_fig

        # 利用可能な遺伝子のみ読み込み
        available = []
        for g in genes:
            try:
                pd.read_parquet(expr_path, columns=[g])
                available.append(g)
            except Exception:
                continue
        if not available:
            return heatmap_fig

        expr_df = pd.read_parquet(expr_path, columns=["CellID"] + available)
        merged = expr_df.merge(
            df[["CellID", "Cluster"]], on="CellID", how="inner"
        )
        cluster_means = merged.groupby("Cluster")[available].mean()
        cluster_means = cluster_means.reindex(
            sorted(cluster_means.index, key=_cluster_sort_key)
        )

        # Z-score 変換
        z_data = cluster_means.values.copy()
        col_mean = z_data.mean(axis=0)
        col_std = z_data.std(axis=0)
        col_std[col_std == 0] = 1
        z_data = (z_data - col_mean) / col_std

        # Y軸ラベル（アノテーション付き）
        y_labels = []
        for g in available:
            if g in gene_to_annotation:
                y_labels.append(f"{g} ({gene_to_annotation[g]})")
            elif mz_to_compound:
                annotated = _annotate_gene_labels(
                    [g], mz_to_compound, tolerance=0.1)
                y_labels.append(annotated[0])
            else:
                y_labels.append(g)

        fig = go.Figure(go.Heatmap(
            z=z_data.T,
            x=[str(c) for c in cluster_means.index],
            y=y_labels,
            colorscale="RdBu_r",
            zmid=0,
            hovertemplate=(
                "Cluster: %{x}<br>Gene: %{y}<br>"
                "Value: %{z:.3f}<extra></extra>"
            ),
        ))
        max_label_len = max(len(str(l)) for l in y_labels) if y_labels else 10
        left_margin = min(max(max_label_len * 7, 120), 350)
        fig.update_layout(
            title=dict(
                text=f"Top {top_n} DEG Heatmap (Z-score)",
                font=dict(size=14), x=0.5),
            xaxis_title="Cluster",
            yaxis_title="Gene / m/z",
            template="plotly_white",
            margin=dict(l=left_margin, r=20, t=40, b=40),
            yaxis=dict(autorange="reversed"),
        )
        return fig.to_dict()

    except Exception:
        return heatmap_fig  # エラー時はアプリ図にフォールバック


def _build_cluster_slide_combined_fig(
    cl_str, cl_name, samples, df, color_map, custom_colors,
    cluster_name_map, name_map, rotation_store,
    umap_xrange, umap_yrange,
):
    """上段UMAP + 共有凡例 + 下段Spatial の結合図を生成（PPTスライド用）。

    添付画像レイアウト: 各サンプルを列、上段=UMAP highlight、下段=TIC overlay。
    """
    from plotly.subplots import make_subplots

    n = len(samples)
    if n == 0:
        return None

    cl_color = (custom_colors or {}).get(
        cl_str, color_map.get(cl_str, "#1f77b4"))

    umap_titles = [str(_display_name(s, name_map)) for s in samples]
    spatial_titles = [
        f"{_display_name(s, name_map)} (Cl {cl_str})" for s in samples
    ]

    fig = make_subplots(
        rows=2, cols=n,
        subplot_titles=umap_titles + spatial_titles,
        vertical_spacing=0.10,
        horizontal_spacing=0.03,
    )

    for idx, s in enumerate(samples):
        df_s = df[df["Sample"] == s].copy()
        col = idx + 1
        cl_mask = df_s["Cluster"].astype(str) == cl_str
        bg_df = df_s[~cl_mask]
        hl_df = df_s[cl_mask]

        # === Row 1: UMAP ===
        if len(bg_df) > 0:
            fig.add_trace(go.Scattergl(
                x=bg_df["UMAP_1"], y=bg_df["UMAP_2"],
                mode="markers",
                marker=dict(color="lightgray", size=2, opacity=0.8),
                name="Unselected", legendgroup="bg",
                showlegend=(idx == 0),
                hoverinfo="skip",
            ), row=1, col=col)
        if len(hl_df) > 0:
            fig.add_trace(go.Scattergl(
                x=hl_df["UMAP_1"], y=hl_df["UMAP_2"],
                mode="markers",
                marker=dict(color=cl_color, size=2),
                name=cl_name, legendgroup="hl",
                showlegend=(idx == 0),
                hoverinfo="skip",
            ), row=1, col=col)

        # === Row 2: Spatial ===
        if "SpatialX" not in df_s.columns:
            continue
        raw_x = df_s["SpatialX"].values.astype(float)
        raw_y = -df_s["SpatialY"].values.astype(float)  # Y軸反転
        transform = rotation_store.get(
            s, rotation_store.get(
                "__all__", {"angle": 0, "flip_h": False, "flip_v": False}))
        if isinstance(transform, (int, float)):
            transform = {"angle": int(transform),
                         "flip_h": False, "flip_v": False}
        tx, ty = _transform_coords(
            raw_x, raw_y,
            transform.get("angle", 0),
            flip_h=transform.get("flip_h", False),
            flip_v=transform.get("flip_v", False))
        msize = _calc_zero_gap_marker_size(tx, ty, render_height=300)
        bg_mask_arr = (~cl_mask).values
        hl_mask_arr = cl_mask.values

        # TIC background (Greys)
        if bg_mask_arr.any():
            if "TotalCount" in df_s.columns and df_s["TotalCount"].notna().any():
                fig.add_trace(go.Scattergl(
                    x=tx[bg_mask_arr], y=ty[bg_mask_arr],
                    mode="markers",
                    marker=dict(
                        color=df_s["TotalCount"].values[bg_mask_arr],
                        colorscale="Greys", size=msize,
                        symbol="square", opacity=0.5, showscale=False),
                    showlegend=False, hoverinfo="skip",
                ), row=2, col=col)
            else:
                fig.add_trace(go.Scattergl(
                    x=tx[bg_mask_arr], y=ty[bg_mask_arr],
                    mode="markers",
                    marker=dict(color="lightgray", size=msize,
                                symbol="square", opacity=0.2),
                    showlegend=False, hoverinfo="skip",
                ), row=2, col=col)
        # Highlighted cluster
        if hl_mask_arr.any():
            fig.add_trace(go.Scattergl(
                x=tx[hl_mask_arr], y=ty[hl_mask_arr],
                mode="markers",
                marker=dict(color=cl_color, size=msize,
                            symbol="square"),
                showlegend=False, hoverinfo="skip",
            ), row=2, col=col)

    # === Axes configuration ===
    for i in range(1, n + 1):
        xref_r1 = f"x{i}" if i > 1 else "x"
        fig.update_xaxes(showticklabels=False, showgrid=False,
                         zeroline=False,
                         range=umap_xrange if umap_xrange else None,
                         row=1, col=i)
        fig.update_yaxes(showticklabels=False, showgrid=False,
                         zeroline=False,
                         range=umap_yrange if umap_yrange else None,
                         scaleanchor=xref_r1,
                         row=1, col=i)
        xref_r2 = f"x{n + i}" if (n + i) > 1 else "x"
        fig.update_xaxes(showticklabels=False, showgrid=False,
                         zeroline=False, row=2, col=i)
        fig.update_yaxes(showticklabels=False, showgrid=False,
                         zeroline=False, autorange="reversed",
                         scaleanchor=xref_r2,
                         row=2, col=i)

    # Legend centered between rows
    fig_w = max(280 * n, 800)
    fig.update_layout(
        showlegend=True,
        legend=dict(orientation="h", xanchor="center", x=0.5, y=0.46,
                    font=dict(size=16)),
        plot_bgcolor="white",
        paper_bgcolor="white",
        width=fig_w, height=700,
        margin=dict(l=10, r=10, t=30, b=10),
    )
    # Subplot titles font size
    for ann in fig.layout.annotations:
        ann.font = dict(size=16)

    return fig


def _build_pptx(umap_fig, spatial_fig, meta, cluster_stats_data, rds_path,
                 sub_name="", volcano_fig=None, heatmap_fig=None,
                 deg_data=None, top_n=5, df=None, cache_dir=None,
                 custom_colors=None, rotation_store=None, name_map=None,
                 set_progress=None, mrm_path=None,
                 existing_prs=None, progress_offset=0, progress_total=None,
                 saved_positions=None, cluster_name_map=None):
    """グローバル概要 + クラスターごとの詳細スライドを含む PPTX を生成し bytes を返す。

    グローバルセクション:
        1. タイトル  2. UMAP+Spatial (統合)  3. クラスタ統計
    クラスターセクション (各クラスター × 3 スライド):
        A. UMAP (ハイライト) + Spatial (ハイライト)
        B. Volcano Plot + Top N Feature Plots
        C. Heatmap (Top N, Z-score)

    existing_prs: 既存のPresentationオブジェクト。指定時はそこにスライドを追加し、
                  bytes は返さず None を返す。
    progress_offset: 進捗計算のオフセット（複数手法ループ時に使用）
    progress_total: 進捗計算の全体ステップ数（複数手法ループ時に使用）
    """
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.enum.text import PP_ALIGN
    from pptx.enum.shapes import MSO_SHAPE
    from pptx.dml.color import RGBColor

    # 進捗計算用
    _clusters_for_progress = []
    if df is not None:
        try:
            _clusters_for_progress = sorted(
                set(str(c) for c in df["Cluster"].unique()),
                key=_cluster_sort_key)
        except Exception:
            pass
    _local_steps = 3 + len(_clusters_for_progress) * 3  # title, UMAP&Spatial, stats + 3 per cluster
    _total_steps = progress_total if progress_total else _local_steps
    _current_step = [progress_offset]  # mutable for nested function

    def _progress(label=""):
        _current_step[0] += 1
        if set_progress:
            pct = int(_current_step[0] / _total_steps * 100)
            set_progress((min(pct, 99), 100, label))

    if existing_prs is not None:
        prs = existing_prs
    else:
        prs = Presentation()
        # 16:9 ワイドスクリーン
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

    # =====================================================================
    # グローバルセクション
    # =====================================================================

    # --- スライド 1: タイトル ---
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    txBox = slide.shapes.add_textbox(Inches(1), Inches(1.5), Inches(11), Inches(3))
    tf = txBox.text_frame
    tf.word_wrap = True

    if sub_name:
        p_title = tf.paragraphs[0]
        p_title.text = sub_name
        p_title.font.size = Pt(40)
        p_title.font.bold = True
        p_title.alignment = PP_ALIGN.CENTER

        p_sub = tf.add_paragraph()
        p_sub.text = "MSI Interactive Analysis Report"
        p_sub.font.size = Pt(20)
        p_sub.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        p_sub.alignment = PP_ALIGN.CENTER
    else:
        p_title = tf.paragraphs[0]
        p_title.text = "MSI Interactive Analysis Report"
        p_title.font.size = Pt(36)
        p_title.font.bold = True
        p_title.alignment = PP_ALIGN.CENTER

    p2 = tf.add_paragraph()
    p2.text = f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    p2.font.size = Pt(16)
    p2.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    p2.alignment = PP_ALIGN.CENTER

    if rds_path:
        p3 = tf.add_paragraph()
        p3.text = f"Source: {Path(rds_path).name}"
        p3.font.size = Pt(14)
        p3.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        p3.alignment = PP_ALIGN.CENTER

    if meta:
        p4 = tf.add_paragraph()
        samples_str = ", ".join(meta.get("samples", []))
        p4.text = (
            f"Cells: {meta.get('n_cells', '?')} | "
            f"Clusters: {meta.get('n_clusters', '?')} | "
            f"Samples: {samples_str}"
        )
        p4.font.size = Pt(12)
        p4.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
        p4.alignment = PP_ALIGN.CENTER

    _progress("タイトルスライド")

    # --- スライド 2: UMAP + Spatial 統合 (サンプル別) ---
    if df is not None and not df.empty and "SpatialX" in df.columns:
        import math
        color_map_global = _get_cluster_color_map(df["Cluster"], custom_colors)
        all_samples = sorted(df["Sample"].unique())
        if not rotation_store:
            rotation_store = {}
        if not name_map:
            name_map = {}

        if all_samples:
            n_sp = len(all_samples)
            # レジェンド幅を確保しつつUMAP/Spatialの横幅を最大化
            _legend_w = 1.3
            avail_w = 13.33 - 0.3 - _legend_w - 0.1  # ≈ 11.93"

            # サンプル数が多い場合はスライドを分割
            _MIN_TILE_W = 1.5  # タイル最小幅(インチ)
            _max_per_slide = max(1, int(avail_w / _MIN_TILE_W))
            if n_sp > _max_per_slide:
                _mid = (n_sp + 1) // 2
                _sample_groups = [all_samples[:_mid], all_samples[_mid:]]
            else:
                _sample_groups = [all_samples]

            # UMAP の全データ軸範囲（サンプル間で統一）
            umap1_min = float(df["UMAP_1"].min())
            umap1_max = float(df["UMAP_1"].max())
            umap2_min = float(df["UMAP_2"].min())
            umap2_max = float(df["UMAP_2"].max())
            umap_pad = max(umap1_max - umap1_min, umap2_max - umap2_min) * 0.05
            umap_xrange = [umap1_min - umap_pad, umap1_max + umap_pad]
            umap_yrange = [umap2_min - umap_pad, umap2_max + umap_pad]

            # レジェンド画像を事前生成（各スライドで共有）
            _n_cl = len(df["Cluster"].unique())
            _legend_h_est = min(_n_cl * 0.35 + 0.2, 6.0)
            legend_fig = _build_cluster_legend_fig(
                df["Cluster"].unique(), color_map_global,
                cluster_name_map=cluster_name_map)
            legend_png = _fig_to_png_bytes(
                legend_fig.to_dict(), width=200, height=600, scale=2)

            for _grp_idx, _grp_samples in enumerate(_sample_groups):
                _grp_n = len(_grp_samples)
                _grp_suffix = f" ({_grp_idx + 1}/{len(_sample_groups)})" if len(_sample_groups) > 1 else ""
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                _pptx_add_title_bar(slide, f"UMAP & Spatial Mapping{_grp_suffix}")

                # 上段: サンプル別UMAP (y=0.9" 〜 y=4.0", 高さ3.1")
                tile_w_umap = avail_w / _grp_n
                for idx, s in enumerate(_grp_samples):
                    df_s = df[df["Sample"] == s]
                    _umap_pos = (saved_positions or {}).get("umap_integrated", {})
                    umap_s = _build_umap_integrated_fig(
                        df_s, color_by="Cluster", highlight_clusters=None,
                        show_legend=False, show_labels=True,
                        title=_display_name(s, name_map),
                        marker_size=2, custom_colors=custom_colors,
                        title_font_size=40, label_size=24,
                        saved_positions=_umap_pos,
                        cluster_name_map=cluster_name_map)
                    if umap_s is not None:
                        umap_s.update_xaxes(range=umap_xrange)
                        umap_s.update_yaxes(range=umap_yrange)
                        u_dict = (umap_s.to_dict()
                                  if hasattr(umap_s, "to_dict") else umap_s)
                        u_png = _fig_to_png_bytes(
                            u_dict, width=600, height=600, scale=2)
                        _uw, _uh, _uoff = _square_tile_dims(
                            tile_w_umap, 3.0)
                        u_left = Inches(0.3 + idx * tile_w_umap + _uoff)
                        _pptx_add_image(slide, u_png,
                                        int(u_left), Inches(0.9),
                                        Inches(_uw), Inches(_uh))

                # 下段: サンプル別Spatial (y=4.1" 〜 y=7.3", 高さ3.2")
                tile_w_sp = avail_w / _grp_n
                for idx, s in enumerate(_grp_samples):
                    df_s = df[df["Sample"] == s]
                    transform = rotation_store.get(
                        s, rotation_store.get(
                            "__all__",
                            {"angle": 0, "flip_h": False, "flip_v": False}))
                    if isinstance(transform, (int, float)):
                        transform = {"angle": int(transform),
                                     "flip_h": False, "flip_v": False}

                    _sp_pos = (saved_positions or {}).get("spatial", {}).get(s, {})
                    sp_fig = _create_single_spatial_fig(
                        df_s, color_map_global,
                        highlight_clusters=None,
                        selected_cell_ids=set(),
                        rotation_deg=transform.get("angle", 0),
                        show_labels=True,
                        flip_h=transform.get("flip_h", False),
                        flip_v=transform.get("flip_v", False),
                        title=_display_name(s, name_map),
                        marker_size=0,
                        render_height=560,
                        embed_legend=False,
                        title_font_size=40, label_size=24,
                        saved_positions=_sp_pos,
                        cluster_name_map=cluster_name_map)
                    if sp_fig is not None:
                        sp_dict = (sp_fig.to_dict()
                                   if hasattr(sp_fig, "to_dict") else sp_fig)
                        sp_png = _fig_to_png_bytes(
                            sp_dict, width=600, height=600, scale=2)
                        _sw, _sh, _soff = _square_tile_dims(
                            tile_w_sp, 3.1)
                        sp_left = Inches(0.3 + idx * tile_w_sp + _soff)
                        _pptx_add_image(slide, sp_png,
                                        int(sp_left), Inches(4.1),
                                        Inches(_sw), Inches(_sh))

                # クラスタレジェンド（右下角に配置、白余白最小化）
                _legend_x = Inches(0.3 + avail_w + 0.1)
                _legend_y = Inches(7.5 - _legend_h_est)
                _pptx_add_image_preserve_ratio(slide, legend_png,
                                               int(_legend_x), int(_legend_y),
                                               Inches(_legend_w), Inches(_legend_h_est),
                                               png_w=200, png_h=600)

            # --- UMAP (by Sample) スライド --- 削除済み（不要）

    elif spatial_fig:
        # SpatialX がない場合はアプリの図をフォールバック
        fig_check = go.Figure(spatial_fig)
        if fig_check.data:
            png_bytes = _fig_to_png_bytes(spatial_fig, width=1200, height=800)
            if png_bytes:
                slide = prs.slides.add_slide(prs.slide_layouts[6])
                _pptx_add_title_bar(slide, "Spatial Mapping")
                _pptx_add_image_preserve_ratio(
                    slide, png_bytes,
                    int((prs.slide_width - Inches(12)) / 2), Inches(0.9),
                    Inches(12), Inches(6.3),
                    png_w=1200, png_h=800)

    _progress("UMAP & Spatial")

    # --- スライド 5: クラスタ統計 ---
    if cluster_stats_data:
        slide = prs.slides.add_slide(prs.slide_layouts[6])
        _pptx_add_title_bar(slide, "Cluster Statistics")

        n_rows = len(cluster_stats_data) + 1
        n_cols = 3
        table_w = Inches(6)
        table_h = Inches(min(5.5, 0.4 * n_rows))
        left = int((prs.slide_width - table_w) / 2)
        table_shape = slide.shapes.add_table(
            n_rows, n_cols, left, Inches(1.0), table_w, table_h,
        )
        table = table_shape.table

        for j, h in enumerate(["Cluster", "Pixels", "%"]):
            cell = table.cell(0, j)
            cell.text = h
            cell.text_frame.paragraphs[0].font.size = Pt(12)
            cell.text_frame.paragraphs[0].font.bold = True

        for i, row_data in enumerate(cluster_stats_data):
            table.cell(i + 1, 0).text = str(row_data.get("Cluster", ""))
            table.cell(i + 1, 1).text = str(row_data.get("Pixels", ""))
            table.cell(i + 1, 2).text = str(row_data.get("Percent", ""))
            for j in range(n_cols):
                table.cell(i + 1, j).text_frame.paragraphs[0].font.size = Pt(11)

    _progress("クラスタ統計")

    # =====================================================================
    # クラスターセクション（各クラスター × 3 スライド）
    # A. UMAP & Spatial  B. DEG Analysis  C. Heatmap
    # =====================================================================
    if df is not None and not df.empty:
        clusters = sorted(df["Cluster"].unique(), key=_cluster_sort_key)
        color_map = _get_cluster_color_map(df["Cluster"], custom_colors)
        has_spatial = "SpatialX" in df.columns
        samples = sorted(df["Sample"].unique()) if has_spatial else []
        if not rotation_store:
            rotation_store = {}
        if not name_map:
            name_map = {}

        # UMAP 軸範囲（Per-cluster Slide B でサンプル間統一に使用）
        cl_umap1_min = float(df["UMAP_1"].min())
        cl_umap1_max = float(df["UMAP_1"].max())
        cl_umap2_min = float(df["UMAP_2"].min())
        cl_umap2_max = float(df["UMAP_2"].max())
        cl_umap_pad = max(cl_umap1_max - cl_umap1_min,
                          cl_umap2_max - cl_umap2_min) * 0.05
        cl_umap_xrange = [cl_umap1_min - cl_umap_pad,
                          cl_umap1_max + cl_umap_pad]
        cl_umap_yrange = [cl_umap2_min - cl_umap_pad,
                          cl_umap2_max + cl_umap_pad]

        rds_path_str = str(rds_path) if rds_path else None
        cache_dir_path = (
            Path(cache_dir) if isinstance(cache_dir, str) and cache_dir
            else cache_dir
        )

        for cl in clusters:
            cl_str = str(cl)
            _cl_name = _cluster_display_name(cl_str, cluster_name_map)

            # === Slide A: UMAP + Spatial (結合図) ===
            slide_a = prs.slides.add_slide(prs.slide_layouts[6])
            _pptx_add_title_bar(slide_a, f"{_cl_name} — UMAP & Spatial")

            n_sp_b = len(samples) if has_spatial and samples else 0

            if n_sp_b > 0:
                combined_fig = _build_cluster_slide_combined_fig(
                    cl_str, _cl_name, samples, df, color_map,
                    custom_colors, cluster_name_map, name_map,
                    rotation_store, cl_umap_xrange, cl_umap_yrange)
                if combined_fig is not None:
                    _cw = max(280 * n_sp_b, 800)
                    c_dict = (combined_fig.to_dict()
                              if hasattr(combined_fig, "to_dict")
                              else combined_fig)
                    c_png = _fig_to_png_bytes(
                        c_dict, width=_cw, height=700, scale=2)
                    if c_png:
                        _pptx_add_image_preserve_ratio(
                            slide_a, c_png,
                            Inches(0.3), Inches(0.7),
                            Inches(12.7), Inches(6.5),
                            png_w=_cw, png_h=700)
            else:
                # Spatialデータなし → 単一UMAP（従来互換）
                umap_hl = _build_umap_integrated_fig(
                    df, color_by="Cluster", highlight_clusters=[cl_str],
                    show_legend=True, show_labels=False,
                    marker_size=2, custom_colors=custom_colors,
                    bg_opacity=1.0,
                    cluster_name_map=cluster_name_map)
                if umap_hl is not None:
                    umap_dict = (umap_hl.to_dict()
                                 if hasattr(umap_hl, "to_dict") else umap_hl)
                    upng = _fig_to_png_bytes(
                        umap_dict, width=800, height=800, scale=2)
                    _pptx_add_image(slide_a, upng,
                                    Inches(0.3), Inches(0.7),
                                    Inches(4.5), Inches(4.5))

            _progress(f"{_cl_name} — UMAP/Spatial")

            # === Slide B: Volcano + Feature Plots ===
            slide_b = prs.slides.add_slide(prs.slide_layouts[6])
            _pptx_add_title_bar(slide_b, f"{_cl_name} — DEG Analysis")

            # Top N features (up / down)
            up_features, down_features = _get_top_n_features_for_cluster(
                deg_data, cl_str, n=top_n)

            # gene→annotation マッピング（Feature Plotタイトル用）
            _gene_ann_map = {}
            if deg_data:
                for _r in deg_data:
                    _g = _r.get("gene", "")
                    _a = _r.get("annotation", "")
                    if _g and _is_meaningful_annotation(_a, _g):
                        _gene_ann_map[_g] = _a

            # Volcano Plot を先に生成（レイアウト計算に必要）
            volcano_cl = _build_volcano_fig_for_cluster(deg_data, cl_str)

            # ---- 自動レイアウト計算 ----
            has_up = bool(up_features)
            has_down = bool(down_features)
            has_volcano = volcano_cl is not None

            _avail_top = 0.65      # Feature配置開始Y (タイトルバー下)
            _avail_bottom = 7.35   # スライド下端マージン
            _avail_h = _avail_bottom - _avail_top  # 6.7"
            _label_h = 0.25        # ラベル行の高さ
            _gap = 0.1             # セクション間隙間
            _volcano_h = 2.5       # Volcano固定高さ

            _n_feat_rows = (1 if has_up else 0) + (1 if has_down else 0)

            if _n_feat_rows > 0 and has_volcano:
                _non_feat = _n_feat_rows * (_label_h + _gap) + _gap + _volcano_h
                _feat_h_val = (_avail_h - _non_feat) / _n_feat_rows
            elif _n_feat_rows > 0:
                _non_feat = _n_feat_rows * (_label_h + _gap)
                _feat_h_val = (_avail_h - _non_feat) / _n_feat_rows
            else:
                _feat_h_val = 0

            _feat_h_val = max(1.5, min(_feat_h_val, 3.5))

            feat_w_val = min(2.4, 12.0 / max(top_n, 1))
            feat_w = Inches(feat_w_val)
            feat_h = Inches(_feat_h_val)

            # Y座標を順番に計算
            _cur_y = _avail_top
            _up_label_y = _up_plot_y = 0
            _down_label_y = _down_plot_y = 0
            _volcano_y = 0

            if has_up:
                _up_label_y = _cur_y
                _cur_y += _label_h + _gap
                _up_plot_y = _cur_y
                _cur_y += _feat_h_val + _gap
            if has_down:
                _down_label_y = _cur_y
                _cur_y += _label_h + _gap
                _down_plot_y = _cur_y
                _cur_y += _feat_h_val + _gap
            if has_volcano:
                _volcano_y = _cur_y

            # "▲ Up-regulated" ラベル
            if has_up:
                lbl = slide_b.shapes.add_textbox(
                    Inches(0.3), Inches(_up_label_y), Inches(3), Inches(0.3))
                lp = lbl.text_frame.paragraphs[0]
                lp.text = f"▲ Up-regulated (Top {len(up_features)})"
                lp.font.size = Pt(10)
                lp.font.color.rgb = RGBColor(0xFF, 0x2D, 0x2D)
                lp.font.bold = True

            # Feature Plot 画像配置 — Up
            for i, feat in enumerate(up_features):
                is_last_up = (i == len(up_features) - 1)
                feat_fig = _build_feature_plot_fig(
                    df, feat, cache_dir_path, rds_path_str,
                    rotation_store, name_map, marker_size=2,
                    show_colorbar_title=is_last_up,
                    show_colorbar=is_last_up,
                    auto_marker_size=True)
                if feat_fig:
                    # アノテーション付きタイトルに上書き
                    _feat_title = feat
                    if feat in _gene_ann_map:
                        _feat_title = f"{feat}\n({_gene_ann_map[feat]})"
                    feat_fig.update_layout(
                        title=dict(text=_feat_title, font=dict(size=14), x=0.5),
                        margin=dict(t=70))
                    png = _fig_to_png_bytes(
                        feat_fig, width=400, height=280, scale=2)
                    left = Inches(0.3 + i * (12.5 / max(top_n, 1)))
                    _pptx_add_image_preserve_ratio(
                        slide_b, png,
                        int(left), Inches(_up_plot_y),
                        feat_w, feat_h,
                        png_w=400, png_h=280)

            # Up features 間の縦区切り線
            _tile_w_feat = 12.5 / max(top_n, 1)
            for i in range(1, len(up_features)):
                _sep_x = Inches(0.3 + i * _tile_w_feat)
                _sep = slide_b.shapes.add_shape(
                    MSO_SHAPE.RECTANGLE,
                    int(_sep_x) - Emu(6350), Inches(_up_plot_y),
                    Emu(6350), feat_h)
                _sep.fill.solid()
                _sep.fill.fore_color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
                _sep.line.fill.background()

            # "▼ Down-regulated" ラベル
            if has_down:
                lbl = slide_b.shapes.add_textbox(
                    Inches(0.3), Inches(_down_label_y), Inches(3), Inches(0.3))
                lp = lbl.text_frame.paragraphs[0]
                lp.text = f"▼ Down-regulated (Top {len(down_features)})"
                lp.font.size = Pt(10)
                lp.font.color.rgb = RGBColor(0x1E, 0x5B, 0xFF)
                lp.font.bold = True

            # Feature Plot 画像配置 — Down
            for i, feat in enumerate(down_features):
                is_last_down = (i == len(down_features) - 1)
                feat_fig = _build_feature_plot_fig(
                    df, feat, cache_dir_path, rds_path_str,
                    rotation_store, name_map, marker_size=2,
                    show_colorbar_title=is_last_down,
                    show_colorbar=is_last_down,
                    auto_marker_size=True)
                if feat_fig:
                    # アノテーション付きタイトルに上書き
                    _feat_title = feat
                    if feat in _gene_ann_map:
                        _feat_title = f"{feat}\n({_gene_ann_map[feat]})"
                    feat_fig.update_layout(
                        title=dict(text=_feat_title, font=dict(size=14), x=0.5),
                        margin=dict(t=70))
                    png = _fig_to_png_bytes(
                        feat_fig, width=400, height=280, scale=2)
                    left = Inches(0.3 + i * (12.5 / max(top_n, 1)))
                    _pptx_add_image_preserve_ratio(
                        slide_b, png,
                        int(left), Inches(_down_plot_y),
                        feat_w, feat_h,
                        png_w=400, png_h=280)

            # Down features 間の縦区切り線
            for i in range(1, len(down_features)):
                _sep_x = Inches(0.3 + i * _tile_w_feat)
                _sep = slide_b.shapes.add_shape(
                    MSO_SHAPE.RECTANGLE,
                    int(_sep_x) - Emu(6350), Inches(_down_plot_y),
                    Emu(6350), feat_h)
                _sep.fill.solid()
                _sep.fill.fore_color.rgb = RGBColor(0xCC, 0xCC, 0xCC)
                _sep.line.fill.background()

            # Volcano Plot (下段) — XY比を保持して最大サイズで配置
            if has_volcano:
                vpng = _fig_to_png_bytes(volcano_cl, width=800, height=700, scale=2)
                v_aspect = 800 / 700  # ≈ 1.14
                v_height = Inches(_volcano_h)
                v_width = int(v_height * v_aspect)
                v_left = int((prs.slide_width - v_width) / 2)
                _pptx_add_image(slide_b, vpng,
                                v_left, Inches(_volcano_y), v_width, v_height)
            elif not up_features and not down_features:
                # DEG データなし → 注釈
                no_deg = slide_b.shapes.add_textbox(
                    Inches(3), Inches(3), Inches(7), Inches(1))
                np_ = no_deg.text_frame.paragraphs[0]
                np_.text = "No DEG data available for this cluster"
                np_.font.size = Pt(16)
                np_.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
                np_.alignment = PP_ALIGN.CENTER

            _progress(f"{_cl_name} — DEG")

            # === Slide C: Per-cluster Heatmap (Top N, Z-score) ===
            hm_fig = _build_heatmap_for_cluster(
                deg_data, cl_str, df, cache_dir, top_n, mrm_path=mrm_path)
            if hm_fig:
                hm_png = _fig_to_png_bytes(hm_fig, width=1200, height=800)
                if hm_png:
                    slide_c = prs.slides.add_slide(prs.slide_layouts[6])
                    _pptx_add_title_bar(
                        slide_c, f"{_cl_name} — Heatmap (Top {top_n})")
                    _pptx_add_image_preserve_ratio(
                        slide_c, hm_png,
                        int((prs.slide_width - Inches(12)) / 2), Inches(0.9),
                        Inches(12), Inches(6.3),
                        png_w=1200, png_h=800)

            _progress(f"{_cl_name} — Heatmap")

    # existing_prs が渡された場合は呼び出し元がまとめて保存するため
    # ここでは保存しない (現在のステップ数を返す)
    if existing_prs is not None:
        return _current_step[0]

    output = BytesIO()
    prs.save(output)
    output.seek(0)
    return output.getvalue()


# ---------------------------------------------------------------------------
# PPTX エクスポート コールバック
# ---------------------------------------------------------------------------

@callback(
    Output("export_top_n_store", "data"),
    Input("input_export_top_n", "value"),
    prevent_initial_call=True,
)
def sync_export_top_n(value):
    """dbc.Input → dcc.Store ブリッジ (Top N)"""
    return value or 5


@callback(
    [Output("export_method_selector", "options"),
     Output("export_method_selector", "value")],
    Input("interactive_rds_map", "data"),
    prevent_initial_call=True,
)
def update_export_method_options(rds_map):
    """rds_map の変更に応じてエクスポート対象手法セレクタを更新する。"""
    if not rds_map or not isinstance(rds_map, dict):
        return [{"label": "All", "value": "all"}], "all"

    methods = list(rds_map.keys())
    options = [{"label": m, "value": m} for m in methods]
    if len(methods) > 1:
        options.append({"label": "Both", "value": "all"})

    default_val = "all" if len(methods) > 1 else methods[0]
    return options, default_val


@callback(
    [Output("dl_report_pptx", "data"),
     Output("div_export_status", "children")],
    Input("btn_export_report", "n_clicks"),
    [State("interactive_umap_plot", "figure"),
     State("last_spatial_figure_store", "data"),
     State("seurat_rds_path_store", "data"),
     State("cluster_stats_table", "data"),
     State("interactive_sub_project_select", "options"),
     State("interactive_sub_project_select", "value"),
     State("volcano_plot", "figure"),
     State("heatmap_plot", "figure"),
     State("deg_data_store", "data"),
     State("custom_color_map_store", "data"),
     State("spatial_rotation_store", "data"),
     State("sample_name_map_store", "data"),
     State("export_top_n_store", "data"),
     State("seurat_cache_dir_store", "data"),
     State("annotation_path", "value"),
     State("interactive_rds_map", "data"),
     State("interactive_result_folder", "value"),
     State("interactive_integration_method", "value"),
     State("export_method_selector", "value"),
     State("cluster_name_map_store", "data"),
     State("accumulated_label_positions", "data")],
    background=True,
    running=[
        (Output("btn_export_report", "disabled"), True, False),
        (Output("export_progress_container", "style"),
         {"display": "block"}, {"display": "none"}),
    ],
    progress=[
        Output("export_progress_bar", "value"),
        Output("export_progress_bar", "max"),
        Output("export_progress_label", "children"),
    ],
    prevent_initial_call=True,
)
def cb_export_report(set_progress, n_clicks, umap_fig, spatial_fig, rds_path,
                     cluster_stats_data, sub_options, sub_value,
                     volcano_fig, heatmap_fig, deg_data, custom_colors,
                     rotation_store, name_map, top_n, cache_dir_str,
                     mrm_path_str, rds_map, result_folder, current_method,
                     export_method_selection, cluster_name_map,
                     accumulated_positions):
    """PPTX レポートをバックグラウンド生成してダウンロード。

    export_method_selection:
        "all" → 全手法（比較スライド付き）
        特定手法名 → その手法のみ
    """
    if not n_clicks:
        raise PreventUpdate

    set_progress((0, 100, "準備中..."))

    try:
        from pptx import Presentation
        from pptx.util import Inches, Pt
        from pptx.enum.text import PP_ALIGN
        from pptx.dml.color import RGBColor
    except ImportError:
        return no_update, (
            "python-pptx がインストールされていません。"
            "pip install python-pptx を実行してください。"
        )

    try:
        import kaleido  # noqa: F401
    except ImportError:
        return no_update, (
            "kaleido がインストールされていません。"
            "pip install kaleido を実行してください。"
        )

    if not umap_fig:
        return no_update, "UMAPプロットが見つかりません。データを読み込んでください。"

    try:
        # サブプロジェクト名を取得
        sub_name = ""
        if sub_options and sub_value:
            for opt in sub_options:
                if opt.get("value") == sub_value:
                    sub_name = opt.get("label", "")
                    break

        # ファイル名を決定
        if sub_name:
            safe_name = re.sub(r'[\\/*?:"<>|]', '_', sub_name)
            filename = f"{safe_name}.pptx"
        else:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"MSI_Report_{timestamp}.pptx"

        top_n = top_n or 5
        saved_positions = _get_merged_label_positions(accumulated_positions)

        # expression_matrix.parquet を必要時に on-demand 生成（feature plot / heatmap が利用）
        set_progress((1, 100, "発現データ準備中（初回は数十秒かかります）..."))
        try:
            if rds_path:
                _bridge.ensure_expression_matrix(rds_path)
        except Exception:
            pass  # PPT 内の feature 関連レンダリングは fallback or スキップ

        # ------------------------------------------------------------------
        # 出力対象手法リストの決定（export_method_selector に基づく）
        # ------------------------------------------------------------------
        methods_to_export = []
        if rds_map and isinstance(rds_map, dict):
            if export_method_selection and export_method_selection != "all":
                # 特定手法のみ選択
                if export_method_selection in rds_map:
                    methods_to_export = [export_method_selection]
                else:
                    methods_to_export = []
            elif len(rds_map) > 1:
                # "all" → 全手法
                if current_method and current_method in rds_map:
                    methods_to_export = [current_method] + [
                        m for m in rds_map if m != current_method
                    ]
                else:
                    methods_to_export = list(rds_map.keys())

        # ------------------------------------------------------------------
        # 単一手法の場合（従来の動作と完全互換）
        # ------------------------------------------------------------------
        if not methods_to_export:
            cache_dir_path = Path(cache_dir_str) if cache_dir_str else None
            df = None
            meta = {}
            if cache_dir_path:
                plot_parquet = cache_dir_path / "plot_data.parquet"
                plot_csv = cache_dir_path / "plot_data.csv"
                if plot_parquet.exists():
                    df = pd.read_parquet(plot_parquet)
                elif plot_csv.exists():
                    df = pd.read_csv(plot_csv)

                meta_file = cache_dir_path / "extraction_meta.json"
                if meta_file.exists():
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)

            pptx_bytes = _build_pptx(
                umap_fig, spatial_fig, meta, cluster_stats_data, rds_path,
                sub_name=sub_name, volcano_fig=volcano_fig,
                heatmap_fig=heatmap_fig,
                deg_data=deg_data, top_n=top_n, df=df,
                cache_dir=str(cache_dir_path) if cache_dir_path else None,
                custom_colors=custom_colors, rotation_store=rotation_store,
                name_map=name_map, set_progress=set_progress,
                mrm_path=mrm_path_str,
                saved_positions=saved_positions,
                cluster_name_map=cluster_name_map,
            )

            return (
                dcc.send_bytes(pptx_bytes, filename=filename),
                f"✓ PPTXファイルを出力しました: {filename}",
            )

        # ------------------------------------------------------------------
        # 複数手法 or セレクタで指定された手法 → 1つの PPTX に結合
        # ------------------------------------------------------------------
        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        is_multi = len(methods_to_export) > 1

        # 全手法のステップ数（Phase 1 後に再計算する）
        total_steps = len(methods_to_export) * 2  # 暫定: データ読込用
        progress_offset = 0
        exported_methods = []
        section_map = []  # (name, start_idx, end_idx) — ①-5

        # ==================================================================
        # Phase 1: 全手法のデータを事前抽出（キャッシュして二重抽出を防止）
        # ==================================================================
        extracted_data = {}  # method_name → dict

        for method_name in methods_to_export:
            method_rds = rds_map.get(method_name)
            if not method_rds or not Path(method_rds).exists():
                print(f"[Export] {method_name}: "
                      f"RDSファイルが見つかりません → スキップ")
                continue

            set_progress((
                min(int(progress_offset / total_steps * 100), 99), 100,
                f"{method_name} のデータを読み込み中..."
            ))

            try:
                result = _bridge.extract_data(method_rds)
            except Exception as e:
                print(f"[Export] {method_name}: データ抽出エラー: {e}")
                continue

            method_df = result["plot_data"]
            method_meta = result["meta"]
            method_cache_dir = result.get("cache_dir")

            # DEG 結果読み込み
            method_deg_data = None
            if result_folder:
                result_base = Path(result_folder)
                method_deg_data = _load_deg_results(
                    result_base, method_name)
            else:
                rds_dir = Path(method_rds).parent
                result_base = (rds_dir.parent
                               if rds_dir.name == "RDS_Files"
                               else rds_dir)
                method_deg_data = _load_deg_results(
                    result_base, method_name)

            extracted_data[method_name] = {
                "df": method_df,
                "meta": method_meta,
                "cache_dir": method_cache_dir,
                "deg_data": method_deg_data,
                "rds_path": method_rds,
            }
            progress_offset += 1

        if not extracted_data:
            return no_update, "エクスポート可能な手法がありません。"

        # Phase 1 完了: 実際のクラスタ数に基づいて total_steps を再計算
        total_steps = progress_offset                    # Phase 1 消費済み
        if is_multi:
            total_steps += len(extracted_data) * 2        # Phase 2: 比較 + sample UMAP
        for _ed in extracted_data.values():
            total_steps += 1                              # セパレータスライド
            _method_df = _ed["df"]
            if _method_df is not None:
                _n_clusters = len(_method_df["Cluster"].unique())
                total_steps += 3 + _n_clusters * 3       # _build_pptx 実ステップ (title, UMAP&Spatial, stats + 3/cluster)
            else:
                total_steps += 4

        # ==================================================================
        # Phase 2: 比較セクション — 全手法の UMAP & Spatial を先頭に配置
        #          (①-3: 複数手法の場合のみ)
        # ==================================================================
        if is_multi and len(extracted_data) > 1:
            comparison_start = len(prs.slides)

            for method_name, ed in extracted_data.items():
                m_df = ed["df"]
                if m_df is None or m_df.empty:
                    continue
                has_spatial_cmp = "SpatialX" in m_df.columns

                # 手法別ラベル位置を取得（現在のメソッドはメモリ蓄積をマージ）
                if method_name == current_method:
                    _method_positions = saved_positions
                else:
                    _method_positions = _load_label_positions_util(
                        ed["rds_path"], method_name)

                color_map_cmp = _get_cluster_color_map(
                    m_df["Cluster"], custom_colors)
                all_samples_cmp = sorted(m_df["Sample"].unique())
                n_sp_cmp = len(all_samples_cmp)
                _legend_w_cmp = 1.3
                avail_w_cmp = 13.33 - 0.3 - _legend_w_cmp - 0.1  # ≈ 11.93"

                # サンプル数が多い場合はスライドを分割
                _max_per_slide_cmp = max(1, int(avail_w_cmp / 1.5))
                if n_sp_cmp > _max_per_slide_cmp:
                    _mid_cmp = (n_sp_cmp + 1) // 2
                    _grp_cmp = [all_samples_cmp[:_mid_cmp], all_samples_cmp[_mid_cmp:]]
                else:
                    _grp_cmp = [all_samples_cmp]

                # UMAP axis ranges (全サンプル統一)
                u1_min = float(m_df["UMAP_1"].min())
                u1_max = float(m_df["UMAP_1"].max())
                u2_min = float(m_df["UMAP_2"].min())
                u2_max = float(m_df["UMAP_2"].max())
                u_pad = max(u1_max - u1_min,
                            u2_max - u2_min) * 0.05
                u_xrange = [u1_min - u_pad, u1_max + u_pad]
                u_yrange = [u2_min - u_pad, u2_max + u_pad]

                # レジェンド画像を事前生成
                _n_cl_cmp = len(m_df["Cluster"].unique())
                _lh_cmp = min(_n_cl_cmp * 0.35 + 0.2, 6.0)
                legend_fig_cmp = _build_cluster_legend_fig(
                    m_df["Cluster"].unique(), color_map_cmp,
                    cluster_name_map=cluster_name_map)
                legend_png_cmp = _fig_to_png_bytes(
                    legend_fig_cmp.to_dict(),
                    width=200, height=600, scale=2)

                for _gi, _gs in enumerate(_grp_cmp):
                    _gs_n = len(_gs)
                    _gs_sfx = f" ({_gi + 1}/{len(_grp_cmp)})" if len(_grp_cmp) > 1 else ""
                    slide = prs.slides.add_slide(prs.slide_layouts[6])
                    _pptx_add_title_bar(
                        slide,
                        f"UMAP & Spatial Mapping \u2014 {method_name}{_gs_sfx}")

                    # 上段: サンプル別 UMAP
                    tile_w_cmp = avail_w_cmp / max(_gs_n, 1)
                    _umap_pos_cmp = (_method_positions or {}).get("umap_integrated", {})
                    for idx_s, s in enumerate(_gs):
                        df_s = m_df[m_df["Sample"] == s]
                        umap_s = _build_umap_integrated_fig(
                            df_s, color_by="Cluster",
                            highlight_clusters=None,
                            show_legend=False, show_labels=True,
                            title=_display_name(s, name_map),
                            marker_size=3, custom_colors=custom_colors,
                            title_font_size=40, label_size=24,
                            saved_positions=_umap_pos_cmp,
                            cluster_name_map=cluster_name_map)
                        if umap_s is not None:
                            umap_s.update_xaxes(range=u_xrange)
                            umap_s.update_yaxes(range=u_yrange)
                            u_dict = (umap_s.to_dict()
                                      if hasattr(umap_s, "to_dict")
                                      else umap_s)
                            u_png = _fig_to_png_bytes(
                                u_dict, width=600, height=600, scale=2)
                            _cw, _ch, _coff = _square_tile_dims(
                                tile_w_cmp, 3.0)
                            u_left = Inches(
                                0.3 + idx_s * tile_w_cmp + _coff)
                            _pptx_add_image(slide, u_png,
                                            int(u_left), Inches(0.9),
                                            Inches(_cw), Inches(_ch))

                    # 下段: サンプル別 Spatial
                    if has_spatial_cmp:
                        _rot_store = rotation_store or {}
                        for idx_s, s in enumerate(_gs):
                            df_s = m_df[m_df["Sample"] == s]
                            transform = _rot_store.get(
                                s, _rot_store.get(
                                    "__all__",
                                    {"angle": 0, "flip_h": False,
                                     "flip_v": False}))
                            if isinstance(transform, (int, float)):
                                transform = {
                                    "angle": int(transform),
                                    "flip_h": False, "flip_v": False}
                            _sp_pos_cmp = (_method_positions or {}).get("spatial", {}).get(s, {})
                            sp_fig_cmp = _create_single_spatial_fig(
                                df_s, color_map_cmp,
                                highlight_clusters=None,
                                selected_cell_ids=set(),
                                rotation_deg=transform.get("angle", 0),
                                show_labels=True,
                                flip_h=transform.get("flip_h", False),
                                flip_v=transform.get("flip_v", False),
                                title=_display_name(s, name_map),
                                marker_size=0, render_height=560,
                                embed_legend=False,
                                title_font_size=40, label_size=24,
                                saved_positions=_sp_pos_cmp,
                                cluster_name_map=cluster_name_map)
                            if sp_fig_cmp is not None:
                                sp_dict = (sp_fig_cmp.to_dict()
                                           if hasattr(sp_fig_cmp, "to_dict")
                                           else sp_fig_cmp)
                                sp_png = _fig_to_png_bytes(
                                    sp_dict, width=600, height=600,
                                    scale=2)
                                _csw, _csh, _csoff = _square_tile_dims(
                                    tile_w_cmp, 3.1)
                                sp_left = Inches(
                                    0.3 + idx_s * tile_w_cmp + _csoff)
                                _pptx_add_image(
                                    slide, sp_png,
                                    int(sp_left), Inches(4.1),
                                    Inches(_csw), Inches(_csh))

                    # クラスタレジェンド（右下角に配置）
                    _lx_cmp = Inches(0.3 + avail_w_cmp + 0.1)
                    _ly_cmp = Inches(7.5 - _lh_cmp)
                    _pptx_add_image_preserve_ratio(slide, legend_png_cmp,
                                                   int(_lx_cmp), int(_ly_cmp),
                                                   Inches(_legend_w_cmp), Inches(_lh_cmp),
                                                   png_w=200, png_h=600)

                # --- UMAP (by Sample) comparison slide --- 削除済み（不要）

                progress_offset += 1

            comparison_end = len(prs.slides) - 1
            if comparison_end >= comparison_start:
                section_map.append(
                    ("Comparison", comparison_start, comparison_end))

        # ==================================================================
        # Phase 3: 各手法のフルセクション
        # ==================================================================
        for method_name in methods_to_export:
            if method_name not in extracted_data:
                continue

            ed = extracted_data[method_name]
            method_df = ed["df"]
            method_meta = ed["meta"]
            method_cache_dir = ed["cache_dir"]
            method_deg_data = ed["deg_data"]
            method_rds = ed["rds_path"]

            method_start_idx = len(prs.slides)

            # 手法別ラベル位置を取得
            if method_name == current_method:
                method_saved_positions = saved_positions
            else:
                method_saved_positions = _load_label_positions_util(
                    method_rds, method_name)

            set_progress((
                min(int(progress_offset / total_steps * 100), 99),
                100,
                f"{method_name} のスライドを生成中..."
            ))

            # --- セパレータスライド ---
            sep_slide = prs.slides.add_slide(prs.slide_layouts[6])
            _pptx_add_title_bar(sep_slide, f"═══ {method_name} ═══")
            txBox = sep_slide.shapes.add_textbox(
                Inches(1), Inches(2.5), Inches(11), Inches(2))
            tf = txBox.text_frame
            tf.word_wrap = True
            p = tf.paragraphs[0]
            p.text = f"Integration Method: {method_name}"
            p.font.size = Pt(28)
            p.font.bold = True
            p.alignment = PP_ALIGN.CENTER
            p2 = tf.add_paragraph()
            p2.text = (
                f"Cells: {method_meta.get('n_cells', '?')} | "
                f"Clusters: {method_meta.get('n_clusters', '?')}"
            )
            p2.font.size = Pt(18)
            p2.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
            p2.alignment = PP_ALIGN.CENTER
            progress_offset += 1

            # --- UMAP 図を生成 ---
            _umap_pos_m = (method_saved_positions or {}).get("umap_integrated", {})
            method_umap_fig = _build_umap_integrated_fig(
                method_df, color_by="Cluster",
                highlight_clusters=None,
                show_legend=True, show_labels=True,
                custom_colors=custom_colors,
                saved_positions=_umap_pos_m,
                cluster_name_map=cluster_name_map,
            )

            # --- クラスタ統計の生成 ---
            method_cluster_stats = []
            try:
                clusters_sorted = sorted(
                    method_df["Cluster"].unique(),
                    key=_cluster_sort_key)
                n_total = len(method_df)
                for c in clusters_sorted:
                    n_c = int((method_df["Cluster"] == c).sum())
                    pct = (f"{n_c / n_total * 100:.1f}"
                           if n_total else "0")
                    method_cluster_stats.append({
                        "Cluster": str(c),
                        "Pixels": n_c,
                        "Percent": pct,
                    })
            except Exception:
                method_cluster_stats = []

            # --- _build_pptx でフルセットを追加 ---
            method_sub_name = (
                f"{sub_name} [{method_name}]"
                if sub_name else method_name
            )
            returned = _build_pptx(
                method_umap_fig, None, method_meta,
                method_cluster_stats, method_rds,
                sub_name=method_sub_name,
                volcano_fig=None, heatmap_fig=None,
                deg_data=method_deg_data, top_n=top_n,
                df=method_df,
                cache_dir=(str(method_cache_dir)
                           if method_cache_dir else None),
                custom_colors=custom_colors,
                rotation_store=rotation_store,
                name_map=name_map,
                set_progress=set_progress,
                mrm_path=mrm_path_str,
                existing_prs=prs,
                progress_offset=progress_offset,
                progress_total=total_steps,
                saved_positions=method_saved_positions,
                cluster_name_map=cluster_name_map,
            )
            if isinstance(returned, int):
                progress_offset = returned

            method_end_idx = len(prs.slides) - 1
            section_map.append(
                (method_name, method_start_idx, method_end_idx))
            exported_methods.append(method_name)

        if not exported_methods:
            return no_update, "エクスポート可能な手法がありません。"

        # ==================================================================
        # Phase 4: セクション情報を PPTX XML に追加（①-5）
        # ==================================================================
        if section_map:
            try:
                _pptx_add_sections(prs, section_map)
            except Exception as e:
                print(f"[Export] セクション追加エラー（無視）: {e}")

        # --- 結合 PPTX をバイト列に変換 ---
        output = BytesIO()
        prs.save(output)
        output.seek(0)

        methods_str = " + ".join(exported_methods)
        return (
            dcc.send_bytes(output.getvalue(), filename=filename),
            f"✓ PPTXファイルを出力しました ({methods_str}): {filename}",
        )

    except Exception as e:
        return no_update, f"エクスポートエラー: {e}"
