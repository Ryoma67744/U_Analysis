"""クラスタ統合（マージ／ReUMAP 置換）がピクセルを対応付けられること (S2・3 件)。

「クラスタを絞り込んで再解析し、その結果を元の UMAP に貼り戻す」機能は、
元データ側と再解析側の**ピクセルを同じ鍵で照合**できて初めて成立する。
鍵は `sample|spot_index` で作る。ここが崩れると、

- **DESI**: 対応点が 1 つも取れず、整列 (3 点以上が必要) が `stop()` で落ちる。
  UMAP もクラスタも DEG も計算し終えた**最後の一歩**で赤いエラーになり、
  結果フォルダがプロジェクトに登録されない。
- **TIMS**: エラーを出さずに完走し「保存しました」と出るが、**中身は何も
  貼り戻されていない**。

--------------------------------------------------------------------------
原因 1: ReUMAP 置換の鍵ベクトルに名前が無い (全 NA 化)
--------------------------------------------------------------------------
`.get_umap_df` は鍵を**セル名で引く**:

    emb$key <- .make_cell_key(obj, sample_name_map)[emb$cell]

ところが ver18 側の `.make_cell_key` は名前を付けずに返す。R では
**名前の無いベクトルを文字列で引くと全て NA** になるので、`emb$key` は
全行 NA。NA 同士の `merge()` は既定で「一致」と見なされるため、
**総当り結合**（元 N 行 × 再解析 M 行）に化ける。データが大きいと
メモリを食い潰し、小さいと「何も変わらない成果物」が出る。

共通マージスクリプト `Common/UMAP_Merge_Clusters_ver1.R` の同名関数には
`names(key) <- rownames(md)` があり、**ver18 のコピーだけが欠けている**。

--------------------------------------------------------------------------
原因 2: 再解析側のサンプル名に付く接尾辞を吸収していない
--------------------------------------------------------------------------
再解析は入力ファイルを `<sample>_KEEP_Cl_8.parquet` のように書き出すので、
再解析側のサンプル名は `sampleA_KEEP_Cl_8`、元データ側は `sampleA` になる。
鍵は `sample|spot_index` なので、**接尾辞を外す対応表を渡さない限り
一致しない**。

ver18 は書き出しの際に `.merge_sample_map`（再解析名 → 元の名前）を
組み立てており、**マージスクリプト呼び出しには渡している**のに、
**ReUMAP 置換の呼び出しには渡していない**（生の `SAMPLE_NAME_MAP` を
渡しており、こちらは利用者が明示指定しない限り空）。

--------------------------------------------------------------------------
原因 3: DESI は対応表を `NULL` で固定している
--------------------------------------------------------------------------
DESI 再解析も同じ接尾辞を付けて書き出すのに、マージスクリプトへ
`SAMPLE_NAME_MAP <- NULL` を渡している。TIMS と違って対応表を組み立てても
いないため、DESI のマージは**構造的に必ず失敗する**。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "Script"
TIMS_REUMAP = SCRIPT / "TIMS" / "260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R"
DESI_FILTER = SCRIPT / "DESI" / "DESI_RDS_ClusterFilter_ver3.R"
MERGE = SCRIPT / "Common" / "UMAP_Merge_Clusters_ver1.R"


def _function_body(src: str, name: str) -> str:
    """`name <- function(...) { … }` の本体を波括弧の対応で切り出す。"""
    m = re.search(re.escape(name) + r"\s*<-\s*function\s*\([^)]*\)\s*\{", src)
    assert m, f"{name} の定義が見つからない"
    i = src.index("{", m.start())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"{name} の本体を閉じられない")


# ---------------------------------------------------------------------------
# 原因 1: 鍵ベクトルに名前が付くこと
# ---------------------------------------------------------------------------

def test_the_shared_merge_script_names_its_key_vector():
    """基準となる実装（共通マージスクリプト）は名前を付けている。"""
    body = _function_body(MERGE.read_text(encoding="utf-8"), ".make_cell_key")
    assert re.search(r"names\(\s*key\s*\)\s*<-", body), (
        "共通マージスクリプトの .make_cell_key が名前を付けなくなった。"
        "この関数の戻り値はセル名で引かれるので、名前が要る")


def test_the_reumap_copy_names_its_key_vector_too():
    """★ 本丸: ver18 のコピーも名前を付けること（現状は欠けている）。"""
    body = _function_body(TIMS_REUMAP.read_text(encoding="utf-8"), ".make_cell_key")
    assert re.search(r"names\(\s*key\s*\)\s*<-", body), (
        "ver18 の .make_cell_key が名前を付けずに返している。"
        "`.get_umap_df` が `[emb$cell]` で引くため **鍵が全て NA** になり、"
        "NA 同士の総当り結合になる（共通マージスクリプト側には名前付けがある）")


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R が無い環境")
def test_r_really_returns_na_for_an_unnamed_vector():
    """★ 前提の実証: 名前の無いベクトルを文字列で引くと NA になること。

    「R ではそうならないのでは」という反論を潰すために、実際に R で確かめる。
    """
    code = (
        'k <- paste0(c("s1","s2"), "|", c(1,2));'
        'cat(sum(is.na(k[c("cellA","cellB")])), "\n");'
        'names(k) <- c("cellA","cellB");'
        'cat(sum(is.na(k[c("cellA","cellB")])), "\n")'
    )
    out = subprocess.run(["Rscript", "-e", code], capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    unnamed, named = out.stdout.split()
    assert unnamed == "2", f"名前なしで NA にならない: {out.stdout!r}"
    assert named == "0", f"名前付きでも NA になる: {out.stdout!r}"


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R が無い環境")
def test_r_merge_on_all_na_keys_explodes():
    """★ 実害の実証: 全 NA の鍵で merge すると総当りになること。"""
    code = (
        'a <- data.frame(key=rep(NA_character_,3), x=1:3);'
        'b <- data.frame(key=rep(NA_character_,4), y=1:4);'
        'cat(nrow(merge(a,b,by="key")), "\n")'
    )
    out = subprocess.run(["Rscript", "-e", code], capture_output=True, text=True,
                         timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "12", (
        f"3 行 × 4 行が総当り 12 行にならない: {out.stdout!r}")


# ---------------------------------------------------------------------------
# 原因 2: ReUMAP 置換にも接尾辞の対応表を渡すこと
# ---------------------------------------------------------------------------

def _call_args(src: str, func: str) -> str:
    m = re.search(re.escape(func) + r"\s*\(", src)
    assert m, f"{func}( の呼び出しが見つからない"
    i = src.index("(", m.start())
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "(":
            depth += 1
        elif src[j] == ")":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
    raise AssertionError(f"{func}( を閉じられない")


def test_the_export_step_builds_a_suffix_map():
    """書き出し時に「再解析名 → 元の名前」の対応表を作っていること。"""
    src = TIMS_REUMAP.read_text(encoding="utf-8")
    assert ".merge_sample_map[" in src, (
        "接尾辞を吸収する対応表を組み立てていない")


def test_the_merge_script_call_uses_the_suffix_map():
    """マージスクリプト呼び出しは対応表を使っている（既に正しい側）。"""
    src = TIMS_REUMAP.read_text(encoding="utf-8")
    assert re.search(r"SAMPLE_NAME_MAP\s*<-\s*if\s*\(length\(\.merge_sample_map\)",
                     src), (
        "マージ呼び出しが対応表を使わなくなった")


def test_the_reumap_replace_call_uses_the_suffix_map():
    """★ 本丸: ReUMAP 置換にも同じ対応表を渡すこと（現状は生の変数）。"""
    src = TIMS_REUMAP.read_text(encoding="utf-8")
    args = _call_args(src, "apply_reumap_replace")
    m = re.search(r"sample_name_map\s*=\s*([^,\)]+)", args)
    assert m, f"apply_reumap_replace に sample_name_map 引数が無い: {args[:200]}"
    passed = m.group(1).strip()
    assert ".merge_sample_map" in passed, (
        f"ReUMAP 置換へ渡しているのは {passed!r}。"
        "書き出し時に作った `.merge_sample_map` を渡さないと、"
        "再解析側の `<sample>_KEEP_Cl_x` と元の `<sample>` が結び付かない"
        "（マージスクリプト側は既に対応表を渡している）")


# ---------------------------------------------------------------------------
# 原因 3: DESI も対応表を渡すこと
# ---------------------------------------------------------------------------

def test_desi_export_adds_the_same_suffix():
    """前提: DESI も同じ接尾辞を付けて書き出している。"""
    src = DESI_FILTER.read_text(encoding="utf-8")
    assert "_KEEP_Cl_" in src and "_EXCL_Cl_" in src


def test_desi_merge_does_not_hardcode_a_null_map():
    """★ 本丸: DESI が対応表を `NULL` で固定していないこと。"""
    src = DESI_FILTER.read_text(encoding="utf-8")
    assert not re.search(r"^\s*SAMPLE_NAME_MAP\s*<-\s*NULL\s*$", src, re.M), (
        "DESI がマージスクリプトへ `SAMPLE_NAME_MAP <- NULL` を渡している。"
        "書き出したファイル名には `_KEEP_Cl_x` が付くので、対応表が無いと"
        "ピクセルが 1 つも一致せず、整列が 3 点未満で `stop()` する"
        "（＝解析の最後で必ず赤いエラーになる）")


def test_desi_builds_a_suffix_map_when_exporting():
    """★ DESI も書き出し時に対応表を組み立てること。"""
    src = DESI_FILTER.read_text(encoding="utf-8")
    assert ".merge_sample_map[" in src, (
        "DESI が「再解析名 → 元の名前」の対応表を作っていない。"
        "TIMS 側 (ver18) と同じ形で作れる")
