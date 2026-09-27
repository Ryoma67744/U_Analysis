# ★ ver67.0: ファイル別選択、元画素の由来、手法別完了状態を共通化する。
ua_value <- function(x, default = "") {
  if (is.null(x) || !length(x) || is.na(x[[1]])) default else as.character(x[[1]])
}
ua_path <- function(path) gsub("\\\\", "/", normalizePath(path, winslash = "/", mustWork = FALSE))
# ★ ver67.0: 同じ表示名が複数の元ファイルを指す場合だけ安定した識別子を添える。
ua_disambiguate_samples <- function(labels, source_ids) {
  labels <- as.character(labels); source_ids <- as.character(source_ids)
  if (length(labels) != length(source_ids)) stop("表示名と元ファイルIDの長さが一致しません")
  valid <- !is.na(labels) & nzchar(labels) & !is.na(source_ids) & nzchar(source_ids)
  sources <- split(source_ids[valid], labels[valid])
  collisions <- names(Filter(function(x) length(unique(x)) > 1L, sources))
  change <- valid & labels %in% collisions
  if (any(change)) {
    ids <- unique(source_ids[change])
    tags <- setNames(vapply(ids, function(x) substr(digest::digest(x, algo = "xxhash64", serialize = FALSE), 1L, 12L),
                            character(1)), ids)
    labels[change] <- paste0(labels[change], "__", unname(tags[source_ids[change]]))
  }
  labels
}
ua_read_manifest <- function(path = "") {
  if (is.null(path) || !length(path) || !nzchar(path)) return(NULL)
  if (!file.exists(path)) stop("切片対応表が見つかりません: ", path)
  x <- jsonlite::fromJSON(path, simplifyVector = FALSE)
  if (!is.list(x$files)) stop("切片対応表の files が不正です")
  x
}
ua_manifest_entry <- function(manifest, path) {
  if (is.null(manifest)) return(NULL)
  key <- ua_path(path)
  hits <- Filter(function(x) {
    paths <- c(ua_value(x$path), ua_value(x$runtime_path))
    any(vapply(paths[nzchar(paths)], function(p) identical(ua_path(p), key), logical(1)))
  }, manifest$files)
  if (length(hits) != 1L) stop("入力と切片対応表が一意に対応しません: ", path)
  hits[[1]]
}
ua_section_metadata <- function(md, input_path, manifest = NULL, roi_col = "annotation",
                                pixel_col = "spot_index", fallback_role = "region",
                                component_col = "ua_coordinate_component") {
  entry <- ua_manifest_entry(manifest, input_path)
  mode <- ua_value(entry$selection_mode, "all")
  if (!mode %in% c("all", "selected", "none")) stop("selection_mode が不正です")
  file_id <- ua_value(entry$file_id, ua_path(input_path))
  role <- ua_value(entry$roi_role, fallback_role)

  # ★ ver74.0: 新標準Parquetはannotation=切片名、旧形式はcomponent列で割り当てる。
  if (!is.null(entry) && identical(role, "spatial")) {
    all_rows <- if (!is.null(entry$registered_sections) && length(entry$registered_sections))
      entry$registered_sections else if (!is.null(entry$spatial_sections) && length(entry$spatial_sections))
      entry$spatial_sections else entry$sections
    if (!length(all_rows)) stop("全切片の登録情報がありません")
    # ★ ver74.0: Parquetの固定annotationと後から変更する表示/群を分離する。
    # registered_sectionsは不変の原登録。effective値はsection_idでのみ重ねる。
    effective_rows <- if (length(entry$spatial_sections)) entry$spatial_sections else entry$sections
    effective_ids <- vapply(effective_rows, function(section) ua_value(section$section_id), character(1))
    if (anyDuplicated(effective_ids)) stop("有効metadataのsection_idが重複しています")
    all_rows <- lapply(all_rows, function(section) {
      fixed_annotation <- ua_value(section$annotation_label, ua_value(section$section_display_name))
      i <- match(ua_value(section$section_id), effective_ids)
      if (!is.na(i)) {
        effective <- effective_rows[[i]]
        for (field in c("section_display_name", "subject_id", "group", "metadata_confirmed"))
          if (!is.null(effective[[field]])) section[[field]] <- effective[[field]]
      }
      section$annotation_label <- fixed_annotation
      section
    })

    # 解析に使用しない切片も含め、全切片の必須情報を確認する。
    names_all <- vapply(all_rows, function(section) ua_value(section$section_display_name), character(1))
    ids_all <- vapply(all_rows, function(section) ua_value(section$section_id), character(1))
    subjects_all <- vapply(all_rows, function(section) ua_value(section$subject_id), character(1))
    groups_all <- vapply(all_rows, function(section) ua_value(section$group), character(1))
    confirmed_all <- vapply(all_rows, function(section)
      isTRUE(section$metadata_confirmed) || identical(tolower(ua_value(section$metadata_confirmed)), "true"), logical(1))
    if (any(!nzchar(ids_all)) || anyDuplicated(ids_all)) stop("全切片のsection_idが欠損または重複しています")
    if (any(!nzchar(names_all)) || anyDuplicated(names_all)) stop("全切片の切片名が欠損または重複しています")
    if (any(!nzchar(subjects_all))) stop("解析対象外を含む全切片に個体／独立試料IDが必要です")
    if (any(!nzchar(groups_all))) stop("解析対象外を含む全切片に群が必要です")
    if (any(!confirmed_all)) stop("解析対象外を含む全切片の登録確認が必要です")
    # ★ ver74.0: 同一個体の群矛盾を拒否し、「該当なし」を架空の共通個体にしない。
    subject_valid <- !subjects_all %in% c("該当なし")
    for (subject in unique(subjects_all[subject_valid]))
      if (length(unique(groups_all[subjects_all == subject])) > 1L)
        stop("同一個体IDに複数の群が登録されています: ", subject)

    selected_ids <- if (!is.null(entry$selected_section_ids))
      as.character(unlist(entry$selected_section_ids, use.names = FALSE)) else
      vapply(entry$sections, function(section) ua_value(section$section_id), character(1))
    if (!length(selected_ids)) return(md[FALSE, , drop = FALSE])
    if (anyDuplicated(selected_ids)) stop("解析対象切片IDが重複しています")
    if (any(!selected_ids %in% ids_all)) stop("解析対象切片が全切片登録情報に存在しません")
    # ★ ver74.0: 保存RDS再解析では元RDSに無い切片を後から追加できない。
    if (!is.null(entry$source_selected_section_ids)) {
      source_ids <- as.character(unlist(entry$source_selected_section_ids, use.names = FALSE))
      if (any(!selected_ids %in% source_ids))
        stop("元RDSに含まれない切片が解析対象に追加されています")
    }

    owners <- list()
    key_values <- NULL
    if (component_col %in% names(md)) {
      # legacy ver71-73
      for (section in all_rows) {
        for (cid in as.character(unlist(section$component_ids, use.names = FALSE))) {
          if (!is.null(owners[[cid]])) stop("同じ座標componentが複数切片へ登録されています: ", cid)
          owners[[cid]] <- section
        }
      }
      key_values <- as.character(md[[component_col]])
    } else {
      # ver74標準: annotation列に全pixelの切片名を固定済み。
      if (!roi_col %in% names(md)) stop("標準Parquetのannotation列がありません")
      fixed_labels <- vapply(all_rows, function(section) ua_value(section$annotation_label), character(1))
      if (any(!nzchar(fixed_labels)) || anyDuplicated(fixed_labels))
        stop("固定annotationが欠損または重複しています")
      owners <- setNames(all_rows, fixed_labels)
      key_values <- as.character(md[[roi_col]])
    }
    key_values[is.na(key_values)] <- ""
    if (any(!nzchar(key_values))) stop("切片割当てが空のpixelがあります")
    unknown <- setdiff(unique(key_values), names(owners))
    if (length(unknown)) stop("Parquetに未登録の切片があります: ", paste(unknown, collapse=", "))

    selected_keys <- names(Filter(function(section)
      ua_value(section$section_id) %in% selected_ids, owners))
    keep <- if (identical(mode, "none")) rep(FALSE, nrow(md)) else key_values %in% selected_keys
    md <- md[keep, , drop = FALSE]; key_values <- key_values[keep]
    if (!nrow(md)) return(md)
    if (!"source_file_id" %in% names(md)) md$source_file_id <- file_id
    if (!"source_pixel_id" %in% names(md))
      md$source_pixel_id <- if (pixel_col %in% names(md)) as.character(md[[pixel_col]]) else rownames(md)
    values <- lapply(c("section_id", "section_display_name", "subject_id", "group"),
      function(field) vapply(key_values, function(key) ua_value(owners[[key]][[field]]), character(1)))
    names(values) <- c("section_id", "section_display_name", "subject_id", "group")
    values$integration_unit_id <- values$section_id
    for (field in names(values)) md[[field]] <- values[[field]]
    if (anyNA(md$integration_unit_id) || any(!nzchar(md$integration_unit_id)))
      stop("統合単位IDが欠損しています")
    return(md)
  }

  roi <- if (roi_col %in% names(md)) as.character(md[[roi_col]]) else rep("", nrow(md))
  roi[is.na(roi)] <- ""
  keep <- if (mode == "none") rep(FALSE, nrow(md)) else if (mode == "selected")
    roi %in% unlist(entry$rois, use.names = FALSE) else rep(TRUE, nrow(md))
  md <- md[keep, , drop = FALSE]; roi <- roi[keep]
  if (!nrow(md)) return(md)
  if (!"source_file_id" %in% names(md)) md$source_file_id <- file_id
  if (!"source_pixel_id" %in% names(md))
    md$source_pixel_id <- if (pixel_col %in% names(md)) as.character(md[[pixel_col]]) else rownames(md)
  if (!role %in% c("section", "region")) stop("ROIの意味は section / region で指定してください")
  if (!is.null(entry) && role == "section") {
    registered <- vapply(entry$sections, function(section) ua_value(section$roi), character(1))
    if (any(!roi %in% registered)) stop("選択画素に対応する切片の登録がありません")
  }
  section <- if (role == "section") paste0(file_id, "::", roi) else rep(file_id, nrow(md))
  values <- list(section_id=section, section_display_name=section,
                 subject_id=rep("",nrow(md)), group=rep("",nrow(md)), integration_unit_id=section)
  for (section_row in entry$sections) {
    idx <- if (role == "section") roi == ua_value(section_row$roi) else rep(TRUE,nrow(md))
    sid <- ua_value(section_row$section_id, if (role == "section") paste0(file_id,"::",ua_value(section_row$roi)) else file_id)
    values$section_id[idx] <- sid
    values$section_display_name[idx] <- ua_value(section_row$section_display_name, sid)
    values$integration_unit_id[idx] <- ua_value(section_row$integration_unit_id,sid)
    values$subject_id[idx] <- ua_value(section_row$subject_id)
    values$group[idx] <- ua_value(section_row$group)
  }
  for (key in names(values)) if (!is.null(entry) || !key %in% names(md)) md[[key]] <- values[[key]]
  if (anyNA(md$integration_unit_id) || any(!nzchar(md$integration_unit_id))) stop("統合単位IDが欠損しています")
  md
}
# ★ ver67.0: 表示名が同じ入力の出力も、選択時と同じ元ファイルIDで分ける。
ua_input_metadata <- function(md, input_path, manifest = NULL) {
  entry <- ua_manifest_entry(manifest, input_path)
  file_id <- ua_value(entry$file_id, ua_path(input_path))
  has_source <- "source_file_id" %in% names(md)
  if (has_source && (!is.null(entry) || any(as.character(md$source_file_id) == file_id))) {
    md <- md[!is.na(md$source_file_id) & as.character(md$source_file_id) == file_id, , drop = FALSE]
  } else {
    sn <- tools::file_path_sans_ext(basename(input_path))
    md <- md[!is.na(md$sample) & as.character(md$sample) == sn, , drop = FALSE]
  }
  if (nrow(md) && (anyNA(md$spot_index) || anyDuplicated(md$spot_index)))
    stop("出力の元ファイル/画素対応が一意ではありません: ", input_path)
  md
}
# ★ ver67.0: 再出力で画素番号が変わっても、座標対応と元画素の由来を別に保持する。
ua_match_xy <- function(px, py, rx, ry, tolerance = 0) {
  if (length(px) != length(py) || length(rx) != length(ry) ||
      any(!is.finite(rx)) || any(!is.finite(ry))) stop("座標対応に必要なx/yが不正です")
  if (!is.finite(tolerance) || tolerance < 0) stop("座標許容誤差が不正です")
  if (tolerance == 0) {
    source_key <- paste(rx, ry, sep = "|")
    if (anyDuplicated(source_key)) stop("元画素の座標が重複しており一意に対応できません")
    index <- match(paste(px, py, sep = "|"), source_key)
  } else {
    # 隣接ビンも探索する。丸めた同一ビンだけでは境界近傍を取りこぼす。
    bins <- split(seq_along(rx), paste(floor(rx/tolerance), floor(ry/tolerance), sep = "|"))
    offsets <- expand.grid(x = -1:1, y = -1:1)
    index <- vapply(seq_along(px), function(i) {
      if (!is.finite(px[i]) || !is.finite(py[i])) return(NA_integer_)
      keys <- paste(floor(px[i]/tolerance) + offsets$x,
                    floor(py[i]/tolerance) + offsets$y, sep = "|")
      candidates <- unlist(bins[keys], use.names = FALSE)
      candidates <- candidates[abs(rx[candidates]-px[i]) <= tolerance &
                               abs(ry[candidates]-py[i]) <= tolerance]
      if (length(candidates) > 1L) stop("座標許容誤差内に複数の元画素があります")
      if (length(candidates)) as.integer(candidates) else NA_integer_
    }, integer(1))
  }
  if (anyDuplicated(index[!is.na(index)])) stop("複数の入力画素が同じ元画素へ対応しています")
  index
}
ua_apply_sections <- function(obj,input_path,manifest=NULL,roi_col="annotation",pixel_col="spot_index",fallback_role="region") {
  md <- ua_section_metadata(obj@meta.data,input_path,manifest,roi_col,pixel_col,fallback_role)
  if (!nrow(md)) return(NULL)
  if (nrow(md) != ncol(obj)) obj <- subset(obj,cells=rownames(md))
  obj@meta.data <- md[colnames(obj),,drop=FALSE]
  obj
}
ua_record_method <- function(outdir,method,status,reason="",stage="",rds_path="") {
  dir.create(outdir,recursive=TRUE,showWarnings=FALSE)
  path <- file.path(outdir,"analysis_methods.json")
  state <- if(file.exists(path)) tryCatch(jsonlite::fromJSON(path,simplifyVector=FALSE),error=function(e)list()) else list()
  state$schema_version <- 1L
  if(is.null(state$methods)) state$methods <- list()
  state$methods[[tolower(method)]] <- list(status=status,stage=stage,reason=reason,rds_path=rds_path,
    updated_at=format(Sys.time(),"%Y-%m-%dT%H:%M:%SZ",tz="UTC"))
  tmp <- tempfile(".analysis_methods-",tmpdir=outdir)
  jsonlite::write_json(state,tmp,auto_unbox=TRUE,pretty=TRUE,null="null")
  if(!file.rename(tmp,path)) {
    if(!file.copy(tmp,path,overwrite=TRUE)) stop("手法別状態を保存できません: ",path)
    unlink(tmp)
  }
  invisible(state)
}
# ★ ver74.0: 数値処理の署名とmetadata来歴を別々に保存する。旧RDSを書換えず、
# 新署名が無い旧checkpointは従来の完全署名が一致する場合だけ再利用する。
ua_signature_setting <- function(name) ua_value(get0(name, envir = .GlobalEnv, inherits = TRUE))
ua_stamp_checkpoint <- function(obj, signature,
    reduction_signature = ua_signature_setting("REDUCTION_SIGNATURE"),
    metadata_signature = ua_signature_setting("METADATA_SIGNATURE")) {
  if (inherits(obj, "Seurat")) {
    obj@misc$analysis_signature <- signature
    obj@misc$reduction_signature <- reduction_signature
    obj@misc$metadata_signature <- metadata_signature
    obj@misc$source_identity_policy <- "file_pixel_v1"
  } else if (is.list(obj) && !is.data.frame(obj)) {
    obj <- lapply(obj, ua_stamp_checkpoint, signature = signature,
      reduction_signature = reduction_signature, metadata_signature = metadata_signature)
  }
  obj
}
ua_checkpoint_matches <- function(obj, signature,
    reduction_signature = ua_signature_setting("REDUCTION_SIGNATURE")) {
  if (inherits(obj, "Seurat")) {
    if (nzchar(reduction_signature) && nzchar(ua_value(obj@misc$reduction_signature)))
      return(identical(obj@misc$reduction_signature, reduction_signature))
    if (is.null(signature) || !nzchar(signature)) return(!nzchar(reduction_signature))
    return(identical(obj@misc$analysis_signature, signature))
  }
  if (is.list(obj) && !is.data.frame(obj)) {
    items <- Filter(function(x) inherits(x, "Seurat") || is.list(x), obj)
    return(length(items) > 0L && all(vapply(items, ua_checkpoint_matches, logical(1),
      signature = signature, reduction_signature = reduction_signature)))
  }
  FALSE
}
ua_refresh_checkpoint_metadata <- function(obj, manifest, signature,
    metadata_signature = ua_signature_setting("METADATA_SIGNATURE")) {
  if (is.list(obj) && !inherits(obj, "Seurat") && !is.data.frame(obj))
    return(lapply(obj, ua_refresh_checkpoint_metadata, manifest = manifest,
      signature = signature, metadata_signature = metadata_signature))
  if (!inherits(obj, "Seurat")) return(obj)
  changed <- nzchar(metadata_signature) && !identical(obj@misc$metadata_signature, metadata_signature)
  if (changed) {
    if (is.null(manifest) || !length(manifest$files))
      stop("metadata変更のあるcheckpoint再利用には切片対応表が必要です")
    md <- obj@meta.data
    if (!all(c("source_file_id", "section_id") %in% names(md)))
      stop("checkpointの由来IDが無いためmetadata変更を安全に反映できません")
    pieces <- lapply(unique(as.character(md$source_file_id)), function(file_id) {
      hits <- Filter(function(entry) identical(ua_value(entry$file_id), file_id), manifest$files)
      if (length(hits) != 1L) stop("checkpointの元ファイルが切片対応表と一意に対応しません: ", file_id)
      entry <- hits[[1L]]
      path <- ua_value(entry$runtime_path)
      if (!nzchar(path)) path <- ua_value(entry$path)
      part <- md[as.character(md$source_file_id) == file_id, , drop = FALSE]
      # ★ ver74.0: 追加要求の切片に画素が無くても、metadataだけの更新とは認めない。
      if (identical(ua_value(entry$roi_role), "spatial")) {
        requested_ids <- if (!is.null(entry$selected_section_ids))
          as.character(unlist(entry$selected_section_ids, use.names = FALSE)) else
          vapply(entry$sections, function(section) ua_value(section$section_id), character(1))
        if (!setequal(unique(as.character(part$section_id)), requested_ids))
          stop("metadata更新でcheckpointの解析切片を追加/削除することはできません")
      }
      roi_col <- if ("annotation" %in% names(part)) "annotation" else if ("ROI" %in% names(part)) "ROI" else "slice_id"
      updated <- ua_section_metadata(part, path, manifest, roi_col = roi_col)
      if (!setequal(rownames(part), rownames(updated)))
        stop("metadata更新によってcheckpointの解析画素が変化しました")
      updated
    })
    updated <- do.call(rbind, pieces)
    obj@meta.data <- updated[colnames(obj), , drop = FALSE]
  }
  # PCA/UMAPの座標は不変。下流集計のみ再実行するためのフラグを残す。
  obj@misc$metadata_changed_on_resume <- changed
  ua_stamp_checkpoint(obj, signature, metadata_signature = metadata_signature)
}

# ★ ver74.0: 選択された画素だけで全0 featureを除外する共通処理。
# 元Parquetは変更せず、フィルタの件数を結果RDSへ記録する。
ua_drop_all_zero_features <- function(obj, assay = "Spatial") {
  counts <- tryCatch(Seurat::GetAssayData(obj, assay = assay, layer = "counts"),
    error = function(e) Seurat::GetAssayData(obj, assay = assay, slot = "counts"))
  keep <- Matrix::rowSums(counts != 0) > 0
  if (!any(keep)) stop("選択した切片に非ゼロfeatureがありません")
  dropped <- rownames(counts)[!keep]
  if (length(dropped)) obj <- subset(obj, features = rownames(counts)[keep])
  obj@misc$zero_feature_filter <- list(input_features = length(keep),
    retained_features = sum(keep), removed_features = dropped)
  obj
}

ua_prepare_reduction <- function(obj,reduction) {
  if(!reduction %in% names(obj@reductions)) stop("Reduction がありません: ",reduction)
  # 補正結果は共通の補正前PCAも保存する。PCAでは補正系をすべて除去する。
  keep <- unique(c(reduction,intersect("pca",names(obj@reductions))))
  for(name in setdiff(names(obj@reductions),keep)) obj[[name]] <- NULL
  for (name in names(obj@graphs)) obj[[name]] <- NULL
  for(name in names(obj@neighbors)) obj[[name]] <- NULL
  obj@meta.data$seurat_clusters <- NULL
  for(name in grep("_snn_res",names(obj@meta.data),value=TRUE)) obj@meta.data[[name]] <- NULL
  Seurat::Idents(obj) <- factor(rep("unclustered",ncol(obj)))
  obj@misc$cluster_reduction <- reduction
  obj
}
ua_cluster_reduction <- function(obj,reduction,umap_dims,cluster_dims,n_neighbors,min_dist,umap_metric,seed,k_param,cluster_metric,resolution,algorithm) {
  obj <- ua_prepare_reduction(obj, reduction)
  available <- ncol(Seurat::Embeddings(obj,reduction))
  if(ncol(obj)<4L || available<2L) stop("UMAP/クラスタリングに必要な画素/主成分が不足しています")
  obj <- Seurat::RunUMAP(obj,reduction=reduction,dims=seq_len(min(umap_dims,available)),
    n.neighbors=min(n_neighbors,ncol(obj)-1L),min.dist=min_dist,metric=umap_metric,seed.use=seed)
  obj <- Seurat::FindNeighbors(obj, reduction = reduction,dims=seq_len(min(cluster_dims,available)),
    k.param=min(k_param,ncol(obj)-1L),annoy.metric=cluster_metric)
  obj <- Seurat::FindClusters(obj, resolution = resolution,algorithm=algorithm,random.seed=seed)
  Seurat::Idents(obj) <- obj$seurat_clusters
  obj@misc$cluster_reduction <- reduction
  obj
}
