# MSI Analysis Application - Interactive Analysis Module
# インタラクティブ解析モジュール

# 必要なパッケージの確認
if (!requireNamespace("plotly", quietly = TRUE)) {
  warning("plotlyパッケージがインストールされていません。install.packages('plotly')を実行してください。")
}

#' Seuratオブジェクトからインタラクティブ解析用のデータを抽出
#' @param seurat_obj Seuratオブジェクト
#' @return インタラクティブ解析用のデータリスト
extract_interactive_data <- function(seurat_obj) {
  if (is.null(seurat_obj)) {
    return(NULL)
  }

  tryCatch(
    {
      # UMAP座標を取得
      umap_coords <- NULL
      if ("umap" %in% names(seurat_obj@reductions)) {
        umap_coords <- as.data.frame(Seurat::Embeddings(seurat_obj, "umap"))
        colnames(umap_coords) <- c("UMAP_1", "UMAP_2")
      }

      if (is.null(umap_coords)) {
        return(list(error = "UMAPデータが見つかりません"))
      }

      # クラスタ情報を取得
      clusters <- Seurat::Idents(seurat_obj)

      # メタデータを取得
      metadata <- seurat_obj@meta.data

      # サンプル情報（orig.identがあれば使用）
      if ("orig.ident" %in% colnames(metadata)) {
        samples <- metadata$orig.ident
      } else {
        samples <- rep("Unknown", nrow(metadata))
      }

      # データフレームにまとめる
      plot_data <- data.frame(
        UMAP_1 = umap_coords$UMAP_1,
        UMAP_2 = umap_coords$UMAP_2,
        Cluster = as.factor(clusters),
        Sample = samples,
        CellID = rownames(umap_coords),
        stringsAsFactors = FALSE
      )

      # 追加のメタデータ列があれば追加
      if ("nCount_Spatial" %in% colnames(metadata)) {
        plot_data$TotalCount <- metadata$nCount_Spatial
      }
      if ("nFeature_Spatial" %in% colnames(metadata)) {
        plot_data$nFeature <- metadata$nFeature_Spatial
      }

      # X, Y座標（空間座標）があれば追加
      if ("x" %in% colnames(metadata)) {
        plot_data$SpatialX <- metadata$x
      }
      if ("y" %in% colnames(metadata)) {
        plot_data$SpatialY <- metadata$y
      }

      # クラスタ統計
      cluster_stats <- as.data.frame(table(plot_data$Cluster))
      colnames(cluster_stats) <- c("Cluster", "Count")

      # サンプル×クラスタ分布
      sample_cluster_dist <- as.data.frame(table(plot_data$Sample, plot_data$Cluster))
      colnames(sample_cluster_dist) <- c("Sample", "Cluster", "Count")

      return(list(
        plot_data = plot_data,
        cluster_stats = cluster_stats,
        sample_cluster_dist = sample_cluster_dist,
        n_clusters = length(unique(clusters)),
        n_cells = nrow(plot_data),
        samples = unique(samples),
        seurat_obj = seurat_obj # 後続の解析用に保持
      ))
    },
    error = function(e) {
      return(list(error = paste("データ抽出エラー:", e$message)))
    }
  )
}

#' インタラクティブUMAPプロットを作成（scatterglモード：WebGL高速レンダリング）
#' @param plot_data extract_interactive_dataで取得したplot_data
#' @param highlight_clusters ハイライトするクラスタ（NULLの場合は全て表示）
#' @param color_by 色分けの基準 ("Cluster" or "Sample")
#' @return plotlyオブジェクト
create_interactive_umap <- function(plot_data, highlight_clusters = NULL, color_by = "Cluster") {
  if (is.null(plot_data)) {
    return(NULL)
  }

  # ハイライト処理
  if (!is.null(highlight_clusters) && length(highlight_clusters) > 0) {
    plot_data$Highlight <- ifelse(plot_data$Cluster %in% highlight_clusters,
      as.character(plot_data$Cluster), "Other"
    )
    plot_data$Alpha <- ifelse(plot_data$Cluster %in% highlight_clusters, 0.8, 0.1)
  } else {
    plot_data$Highlight <- as.character(plot_data[[color_by]])
    plot_data$Alpha <- 0.7
  }

  # ホバーテキストを作成
  plot_data$HoverText <- paste0(
    "Cluster: ", plot_data$Cluster, "<br>",
    "Sample: ", plot_data$Sample, "<br>",
    "ID: ", plot_data$CellID
  )

  # 色パレットを生成
  unique_groups <- unique(plot_data$Highlight)
  n_groups <- length(unique_groups)

  if (!is.null(highlight_clusters) && length(highlight_clusters) > 0) {
    # ハイライトモード: Otherはグレー
    color_palette <- scales::hue_pal()(n_groups - 1)
    names(color_palette) <- unique_groups[unique_groups != "Other"]
    color_palette["Other"] <- "rgba(200,200,200,0.3)"
  } else {
    # 通常モード
    color_palette <- scales::hue_pal()(n_groups)
    names(color_palette) <- unique_groups
  }

  # scatterglを使用してplotlyプロットを直接作成（WebGL高速レンダリング）
  # これにより100万点以上のデータでも高速に描画可能
  fig <- plotly::plot_ly(
    data = plot_data,
    x = ~UMAP_1,
    y = ~UMAP_2,
    color = ~Highlight,
    colors = color_palette,
    text = ~HoverText,
    key = ~CellID,
    type = "scattergl", # WebGL モード（高速）
    mode = "markers",
    marker = list(
      size = 3,
      opacity = plot_data$Alpha[1]
    ),
    hoverinfo = "text",
    source = "umap_plot"
  )

  # レイアウト設定
  fig <- fig %>% plotly::layout(
    xaxis = list(title = "UMAP 1", zeroline = FALSE),
    yaxis = list(title = "UMAP 2", zeroline = FALSE),
    dragmode = "select",
    clickmode = "event+select",
    legend = list(
      title = list(text = color_by),
      itemsizing = "constant"
    ),
    hovermode = "closest"
  )

  # ツールバー設定
  fig <- fig %>% plotly::config(
    displayModeBar = TRUE,
    modeBarButtonsToRemove = c("lasso2d", "select2d"),
    displaylogo = FALSE
  )

  return(fig)
}

#' 空間マッピングプロットを作成
#' @param plot_data extract_interactive_dataで取得したplot_data
#' @param selected_clusters 表示するクラスタ
#' @param selected_sample 表示するサンプル（NULLの場合は全て）
#' @return plotlyオブジェクト
create_spatial_plot <- function(plot_data, selected_clusters = NULL, selected_sample = NULL) {
  if (is.null(plot_data)) {
    return(NULL)
  }

  # 空間座標があるか確認
  if (!("SpatialX" %in% colnames(plot_data)) || !("SpatialY" %in% colnames(plot_data))) {
    return(NULL)
  }

  # フィルタリング
  filtered_data <- plot_data
  if (!is.null(selected_sample) && selected_sample != "All") {
    filtered_data <- filtered_data[filtered_data$Sample == selected_sample, ]
  }

  # クラスタでハイライト
  if (!is.null(selected_clusters) && length(selected_clusters) > 0) {
    filtered_data$ShowCluster <- ifelse(filtered_data$Cluster %in% selected_clusters,
      as.character(filtered_data$Cluster), NA
    )
    filtered_data <- filtered_data[!is.na(filtered_data$ShowCluster), ]
  }

  if (nrow(filtered_data) == 0) {
    return(NULL)
  }

  # ホバーテキスト
  filtered_data$HoverText <- paste0(
    "Cluster: ", filtered_data$Cluster, "<br>",
    "X: ", round(filtered_data$SpatialX, 2), "<br>",
    "Y: ", round(filtered_data$SpatialY, 2)
  )

  # 色パレット
  unique_clusters <- unique(filtered_data$Cluster)
  color_palette <- scales::hue_pal()(length(unique_clusters))
  names(color_palette) <- unique_clusters

  # scatterglを使用（WebGL高速レンダリング）
  fig <- plotly::plot_ly(
    data = filtered_data,
    x = ~SpatialX,
    y = ~ -SpatialY, # Y軸反転（MSI座標系）
    color = ~Cluster,
    colors = color_palette,
    text = ~HoverText,
    type = "scattergl",
    mode = "markers",
    marker = list(size = 4, opacity = 0.8),
    hoverinfo = "text"
  ) %>%
    plotly::layout(
      xaxis = list(title = "X", scaleanchor = "y"),
      yaxis = list(title = "Y"),
      title = "Spatial Mapping"
    )

  return(fig)
}

#' クラスタ情報のサマリーを取得
#' @param interactive_data extract_interactive_dataの結果
#' @param selected_cluster 選択されたクラスタ
#' @return サマリーテキスト
get_cluster_summary <- function(interactive_data, selected_cluster) {
  if (is.null(interactive_data) || is.null(selected_cluster)) {
    return("クラスタを選択してください")
  }

  plot_data <- interactive_data$plot_data
  cluster_data <- plot_data[plot_data$Cluster == selected_cluster, ]

  if (nrow(cluster_data) == 0) {
    return("データがありません")
  }

  # サンプル分布
  sample_dist <- table(cluster_data$Sample)
  sample_text <- paste(names(sample_dist), ":", sample_dist, collapse = "\n")

  summary_text <- paste0(
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
    "【Cluster ", selected_cluster, "】\n",
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n",
    "ピクセル数: ", nrow(cluster_data), " / ", nrow(plot_data),
    " (", round(nrow(cluster_data) / nrow(plot_data) * 100, 1), "%)\n\n",
    "【サンプル分布】\n",
    sample_text
  )

  return(summary_text)
}

#' Feature Plot用のデータを準備
#' @param seurat_obj Seuratオブジェクト
#' @param feature_name フィーチャー名（m/z値など）
#' @param plot_data UMAPプロットデータ
#' @return フィーチャー発現量を追加したplot_data
prepare_feature_plot_data <- function(seurat_obj, feature_name, plot_data) {
  if (is.null(seurat_obj) || is.null(feature_name)) {
    return(NULL)
  }

  tryCatch(
    {
      # 発現量データを取得
      expr_data <- Seurat::GetAssayData(seurat_obj, slot = "data")

      if (!(feature_name %in% rownames(expr_data))) {
        return(NULL)
      }

      feature_expr <- as.numeric(expr_data[feature_name, ])
      plot_data$Expression <- feature_expr

      return(plot_data)
    },
    error = function(e) {
      return(NULL)
    }
  )
}

#' Feature Plotを作成（scatterglモード：WebGL高速レンダリング）
#' @param plot_data prepare_feature_plot_dataの結果
#' @param feature_name フィーチャー名
#' @return plotlyオブジェクト
create_feature_plot <- function(plot_data, feature_name) {
  if (is.null(plot_data) || !("Expression" %in% colnames(plot_data))) {
    return(NULL)
  }

  plot_data$HoverText <- paste0(
    "Cluster: ", plot_data$Cluster, "<br>",
    feature_name, ": ", round(plot_data$Expression, 3)
  )

  # scatterglを使用（WebGL高速レンダリング）
  fig <- plotly::plot_ly(
    data = plot_data,
    x = ~UMAP_1,
    y = ~UMAP_2,
    color = ~Expression,
    colors = "Plasma", # viridis系のカラースケール
    text = ~HoverText,
    type = "scattergl",
    mode = "markers",
    marker = list(
      size = 3,
      opacity = 0.8,
      colorbar = list(title = feature_name)
    ),
    hoverinfo = "text"
  ) %>%
    plotly::layout(
      xaxis = list(title = "UMAP 1", zeroline = FALSE),
      yaxis = list(title = "UMAP 2", zeroline = FALSE),
      title = paste("Feature Plot:", feature_name)
    )

  return(fig)
}

#' 利用可能なフィーチャー（m/z値）のリストを取得
#' @param seurat_obj Seuratオブジェクト
#' @return フィーチャー名のベクトル
get_available_features <- function(seurat_obj) {
  if (is.null(seurat_obj)) {
    return(character())
  }

  tryCatch(
    {
      rownames(Seurat::GetAssayData(seurat_obj, slot = "data"))
    },
    error = function(e) {
      character()
    }
  )
}
