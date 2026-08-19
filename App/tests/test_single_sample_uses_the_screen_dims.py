"""単一サンプルの解析でも、画面で指定した次元数が使われること (A-8)。

--------------------------------------------------------------------------
症状
--------------------------------------------------------------------------
ファイルを 1 つだけ指定して TIMS 解析を実行すると、画面の「dims」欄に 30 と
入っていても実際には **20 次元**で計算される。29 や 31 に変えるとちゃんと
反映されるのに、**既定値の 30 のときだけ無視される**。

理由は後方互換のためのガード:

    if (is.numeric(UMAP_DIMS_N) && UMAP_DIMS_N > 0L && UMAP_DIMS_N != 30L) {

「既定値 (30) のままなら従来のグリッドを触らない」という意図だが、
単一サンプルは補正を行わない経路に入り、その条件表 (`PCA_RETRY_GRID`) の
先頭は **1000 特徴量 / 20 主成分 / 20 次元**である。つまり
「30 と表示されているのに 20 で走る」ことになる。

なお「記録には 30 と書かれる」という問題は ver56.5 の実効値記録
(`.record_retry`) で既に解消済みで、受領書には実際の 20 が載る。
残っていたのは**挙動のほう**である。

--------------------------------------------------------------------------
直し方
--------------------------------------------------------------------------
ガードを外し、画面の値を常に反映する。既存の `.apply_ud` は
「先頭エントリの主成分数を次元数以上へ引き上げる」処理を持っているので、
dims > max_pcs で Seurat が落ちることは無い。

特徴量数 (1000 対 3000) は**画面に対応する設定が無いので変えない**。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "Script"
TIMS_V6 = SCRIPT / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"

_SRC = TIMS_V6.read_text(encoding="utf-8")


def _override_block() -> str:
    """`UMAP_DIMS_N` の反映ブロックを丸ごと切り出す。"""
    i = _SRC.index("if (is.numeric(UMAP_DIMS_N)")
    depth = 0
    j = _SRC.index("{", i)
    for k in range(j, len(_SRC)):
        if _SRC[k] == "{":
            depth += 1
        elif _SRC[k] == "}":
            depth -= 1
            if depth == 0:
                return _SRC[i:k + 1]
    raise AssertionError("反映ブロックを閉じられない")


# ---------------------------------------------------------------------------
# 前提（変えてはいけないもの）
# ---------------------------------------------------------------------------

def test_the_grids_are_unchanged():
    """条件表そのものは触らないこと（特徴量数を変えない）。"""
    assert re.search(r"list\(n_var_features = 1000, max_pcs = 20, umap_dims = 20\)", _SRC), (
        "PCA 側の条件表の先頭が変わっている。特徴量数は画面に対応する設定が"
        "無いので変えない方針")
    assert re.search(r"list\(n_var_features = 3000, max_pcs = 30, umap_dims = 30\)", _SRC)


def test_the_screen_default_is_still_thirty():
    """画面既定と R 側既定が揃っていること。"""
    m = re.search(r"^UMAP_DIMS_N\s*<-\s*(\d+)L?", _SRC, re.M)
    assert m and int(m.group(1)) == 30


# ---------------------------------------------------------------------------
# 本丸
# ---------------------------------------------------------------------------

def test_the_backward_compatibility_guard_is_gone():
    """★ 既定値 30 を除外するガードが残っていないこと。"""
    block = _override_block()
    assert "!= 30L" not in block and "!=30L" not in block, (
        "`UMAP_DIMS_N != 30L` のガードが残っている。"
        "既定値 30 のときだけ画面の指定が無視され、"
        "単一サンプルは 20 次元で走ってしまう")


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R が無い環境")
@pytest.mark.parametrize("dims,want_pca_dims,want_pca_pcs", [
    (30, 30, 30),   # ★ 既定値。従来は 20/20 のままだった
    (20, 20, 20),
    (35, 35, 35),
])
def test_the_screen_dims_reach_the_single_sample_grid(dims, want_pca_dims, want_pca_pcs):
    """★ 単一サンプルが使う条件表に画面の次元数が届くこと。

    単一サンプル（補正なし）は `PCA_RETRY_GRID` の先頭で走る。
    """
    code = f"""
MAX_PCS <- 30
UMAP_DIMS_MAX <- 30
UMAP_DIMS_N <- {dims}L
HARMONY_RETRY_GRID <- list(
  list(n_var_features = 3000, max_pcs = 30, umap_dims = 30),
  list(n_var_features = 1000, max_pcs = 20, umap_dims = 20),
  list(n_var_features = 500,  max_pcs = 15, umap_dims = 15)
)
PCA_RETRY_GRID <- list(
  list(n_var_features = 1000, max_pcs = 20, umap_dims = 20),
  list(n_var_features = 500,  max_pcs = 15, umap_dims = 15)
)
{_override_block()}
cat(PCA_RETRY_GRID[[1]]$umap_dims, PCA_RETRY_GRID[[1]]$max_pcs,
    PCA_RETRY_GRID[[1]]$n_var_features, UMAP_DIMS_MAX, MAX_PCS, sep = "\\n")
"""
    out = subprocess.run(["Rscript", "-e", code], capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, out.stderr
    got = [int(x) for x in out.stdout.split()]
    umap_dims, max_pcs, n_var, dims_max, global_pcs = got
    assert umap_dims == want_pca_dims, (
        f"単一サンプルの次元数が {umap_dims}（期待 {want_pca_dims}）。"
        "画面の指定が届いていない")
    assert max_pcs == want_pca_pcs, (
        f"主成分数が {max_pcs}。次元数 {want_pca_dims} を下回ると Seurat が落ちる")
    assert n_var == 1000, "特徴量数は変えない方針（画面に対応する設定が無い）"
    assert dims_max == dims and global_pcs >= dims


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R が無い環境")
def test_no_grid_entry_asks_for_more_dims_than_it_has_pcs():
    """★ 直しすぎの検出: どの段でも「次元数 > 主成分数」にならないこと。

    ここが崩れると `RunUMAP` が落ちて解析全体が止まる。
    """
    for dims in (5, 15, 20, 30, 50):
        code = f"""
MAX_PCS <- 30
UMAP_DIMS_MAX <- 30
UMAP_DIMS_N <- {dims}L
HARMONY_RETRY_GRID <- list(
  list(n_var_features = 3000, max_pcs = 30, umap_dims = 30),
  list(n_var_features = 1000, max_pcs = 20, umap_dims = 20),
  list(n_var_features = 500,  max_pcs = 15, umap_dims = 15)
)
PCA_RETRY_GRID <- list(
  list(n_var_features = 1000, max_pcs = 20, umap_dims = 20),
  list(n_var_features = 500,  max_pcs = 15, umap_dims = 15)
)
{_override_block()}
bad <- 0
for (g in list(HARMONY_RETRY_GRID, PCA_RETRY_GRID))
  for (cfg in g) if (cfg$umap_dims > cfg$max_pcs) bad <- bad + 1
cat(bad)
"""
        out = subprocess.run(["Rscript", "-e", code], capture_output=True, text=True,
                             timeout=180)
        assert out.returncode == 0, out.stderr
        assert out.stdout.strip() == "0", (
            f"dims={dims} で「次元数 > 主成分数」の段が "
            f"{out.stdout.strip()} 個ある。RunUMAP が落ちる")
