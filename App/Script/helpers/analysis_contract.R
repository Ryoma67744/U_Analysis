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

  # ★ ver71.0: imzMLの物理切片はROI名ではなく固定component列から割り当てる。
  if (!is.null(entry) && identical(role, "spatial")) {
    if (!component_col %in% names(md)) stop("imzML座標切片に必要な component 列がありません")
    component <- as.character(md[[component_col]]); component[is.na(component)] <- ""
    if (any(!nzchar(component))) stop("imzML座標componentが欠損しています")
    all_rows <- if (!is.null(entry$spatial_sections) && length(entry$spatial_sections)) entry$spatial_sections else entry$sections
    registered_components <- unique(as.character(unlist(lapply(all_rows, function(section)
      section$component_ids), use.names = FALSE)))
    unknown_components <- setdiff(unique(component[nzchar(component)]), registered_components)
    if (length(unknown_components))
      stop("変換入力にmanifest未登録の座標componentがあります: ", paste(unknown_components, collapse=", "))
    owners <- list()
    for (section in entry$sections) {
      ids <- as.character(unlist(section$component_ids, use.names = FALSE))
      if (!length(ids)) stop("座標切片に component が登録されていません")
      for (cid in ids) {
        if (!is.null(owners[[cid]])) stop("同じ座標componentが複数切片へ登録されています: ", cid)
        owners[[cid]] <- list(
          section_id = ua_value(section$section_id),
          section_display_name = ua_value(section$section_display_name, ua_value(section$section_id)),
          subject_id = ua_value(section$subject_id),
          group = ua_value(section$group),
          integration_unit_id = ua_value(section$integration_unit_id, ua_value(section$section_id))
        )
      }
    }
    keep <- if (identical(mode, "none")) rep(FALSE, nrow(md)) else component %in% names(owners)
    md <- md[keep, , drop = FALSE]; component <- component[keep]
    if (!nrow(md)) return(md)
    if (any(!component %in% names(owners))) stop("選択画素に対応する座標切片がありません")
    if (!"source_file_id" %in% names(md)) md$source_file_id <- file_id
    if (!"source_pixel_id" %in% names(md))
      md$source_pixel_id <- if (pixel_col %in% names(md)) as.character(md[[pixel_col]]) else rownames(md)
    values <- lapply(c("section_id", "section_display_name", "subject_id", "group", "integration_unit_id"),
      function(key) vapply(component, function(cid) ua_value(owners[[cid]][[key]]), character(1)))
    names(values) <- c("section_id", "section_display_name", "subject_id", "group", "integration_unit_id")
    for (key in names(values)) md[[key]] <- values[[key]]
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
ua_stamp_checkpoint <- function(obj,signature) {
  if(inherits(obj,"Seurat")) {
    obj@misc$analysis_signature <- signature
    obj@misc$source_identity_policy <- "file_pixel_v1"
  }
  else if(is.list(obj)) obj <- lapply(obj,ua_stamp_checkpoint,signature=signature)
  obj
}
ua_checkpoint_matches <- function(obj,signature) {
  if(is.null(signature) || !nzchar(signature)) return(TRUE)
  if(inherits(obj,"Seurat")) return(identical(obj@misc$analysis_signature,signature))
  if(is.list(obj)) {
    items <- Filter(function(x) inherits(x,"Seurat") || is.list(x),obj)
    return(length(items)>0L && all(vapply(items,ua_checkpoint_matches,logical(1),signature)))
  }
  FALSE
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
