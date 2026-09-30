"""実R helperの小型S4回帰。R jobで実行する。"""
from pathlib import Path
import pytest

def test_actual_r_fact_extraction_uses_cluster_graph_and_not_last_neighbors(tmp_path):
    """Seurat 不要の小型 S4 fixture で、実際の R facts ブロックを実行する。"""
    import shutil
    import subprocess
    executable = shutil.which("Rscript")
    if executable is None:
        candidate = Path("C:/Program Files/R/R-4.4.2/bin/Rscript.exe")
        executable = str(candidate) if candidate.exists() else None
    if executable is None:
        pytest.skip("Rscript 未導入")
    text = (Path(__file__).parents[1] / "Script/helpers/extract_seurat_data.R").read_text(encoding="utf-8")
    block = text[text.index("command_facts <- lapply"):text.index("meta_info <- list(")]
    setup = '''
setClass("AuditCommand",slots=c(params="list",time.stamp="POSIXct",assay.used="character"))
setClass("AuditReduction",slots=c(cell.embeddings="matrix",assay.used="character"))
setClass("AuditObject",slots=c(commands="list",reductions="list",misc="list"))
cmd <- function(params, t) new("AuditCommand",params=params,time.stamp=as.POSIXct(t,origin="1970-01-01"),assay.used="Spatial")
obj <- new("AuditObject",commands=list(
  FindNeighbors.harmony=cmd(list(reduction="harmony",graph.name=c("old_nn","old_snn")),1),
  FindClusters=cmd(list(graph.name="old_snn",resolution=.3,random.seed=91),2),
  FindNeighbors.pca=cmd(list(reduction="pca",graph.name=c("new_nn","new_snn")),3),
  RunUMAP.pca=cmd(list(reduction="pca",dims=1:3),4)),
  reductions=list(pca=new("AuditReduction",cell.embeddings=matrix(0,3,3),assay.used="Spatial")),misc=list())
wrapper_reduction <- "pca"
embedding_kind <- "umap"
embedding_location <- "primary"
plot_data <- data.frame(SpatialX=1:3)
meta <- data.frame(seurat_clusters=c("0","1","1"))
clusters <- c("0","1","1")
cell_ids <- c("a","b","c")
features <- "mz"
'''
    script = tmp_path / "facts.R"
    script.write_text(setup + block + '\nstopifnot(facts$clusters$space == "harmony", facts$embedding$space == "pca", facts$cluster_commands_verified, facts$clusters$parameters$random.seed == 91)\n', encoding="utf-8")
    result = subprocess.run([executable, "--vanilla", str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("wrapped", [False, True])
def test_actual_r_reads_legacy_and_v2_deg_table(tmp_path, monkeypatch, wrapped):
    import shutil
    import subprocess
    import app.config as config
    from app.utils.deg_utils import read_deg_rds

    executable = shutil.which("Rscript")
    if executable is None:
        candidate = Path("C:/Program Files/R/R-4.4.2/bin/Rscript.exe")
        executable = str(candidate) if candidate.exists() else None
    if executable is None:
        pytest.skip("Rscript 未導入")
    monkeypatch.setattr(config, "RSCRIPT_PATH", Path(executable))
    source = tmp_path / "deg.rds"
    command = ('d <- data.frame(gene="mz_100",cluster="0",avg_log2FC=2,p_val_adj=.001);'
               + ('d <- list(signature="key",data=d,assignment_hash="assignment");' if wrapped else '')
               + f'saveRDS(d,"{source.as_posix()}")')
    result = subprocess.run([executable, "--vanilla", "-e", command], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    records = read_deg_rds(source)
    assert records and records[0]["gene"] == "mz_100"
