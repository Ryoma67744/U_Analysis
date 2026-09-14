# ★ verNEXT: ファイル別選択、元画素の由来、手法別完了状態を共通化する。
ua_value <- function(x, default = "") {
  if (is.null(x) || !length(x) || is.na(x[[1]])) default else as.character(x[[1]])
}
ua_path <- function(path) gsub("\\\\", "/", normalizePath(path, winslash = "/", mustWork = FALSE))
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
                                pixel_col = "spot_index", fallback_role = "region") {
  entry <- ua_manifest_entry(manifest, input_path)
  mode <- ua_value(entry$selection_mode, "all")
  if (!mode %in% c("all", "selected", "none")) stop("selection_mode が不正です")
  roi <- if (roi_col %in% names(md)) as.character(md[[roi_col]]) else rep("", nrow(md))
  roi[is.na(roi)] <- ""
  keep <- if (mode == "none") rep(FALSE, nrow(md)) else if (mode == "selected")
    roi %in% unlist(entry$rois, use.names = FALSE) else rep(TRUE, nrow(md))
  md <- md[keep, , drop = FALSE]; roi <- roi[keep]
  if (!nrow(md)) return(md)
  file_id <- ua_value(entry$file_id, ua_path(input_path))
  if (!"source_file_id" %in% names(md)) md$source_file_id <- file_id
  if (!"source_pixel_id" %in% names(md))
    md$source_pixel_id <- if (pixel_col %in% names(md)) as.character(md[[pixel_col]]) else rownames(md)
  role <- ua_value(entry$roi_role, fallback_role)
  if (!role %in% c("section", "region")) stop("ROIの意味は section / region で指定してください")
  if (!is.null(entry) && role == "section") {
    registered <- vapply(entry$sections, function(s) ua_value(s$roi), character(1))
    if (any(!roi %in% registered)) stop("選択画素に対応する切片の登録がありません")
  }
  section <- if (role == "section") paste0(file_id, "::", roi) else rep(file_id, nrow(md))
  values <- list(section_id=section, subject_id=rep("",nrow(md)), group=rep("",nrow(md)), integration_unit_id=section)
  for (s in entry$sections) {
    idx <- if (role == "section") roi == ua_value(s$roi) else rep(TRUE,nrow(md))
    sid <- ua_value(s$section_id, if (role == "section") paste0(file_id,"::",ua_value(s$roi)) else file_id)
    values$section_id[idx] <- sid
    values$integration_unit_id[idx] <- ua_value(s$integration_unit_id,sid)
    values$subject_id[idx] <- ua_value(s$subject_id)
    values$group[idx] <- ua_value(s$group)
  }
  for (key in names(values)) if (!is.null(entry) || !key %in% names(md)) md[[key]] <- values[[key]]
  if (anyNA(md$integration_unit_id) || any(!nzchar(md$integration_unit_id))) stop("統合単位IDが欠損しています")
  md
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
  if(inherits(obj,"Seurat")) obj@misc$analysis_signature <- signature
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
