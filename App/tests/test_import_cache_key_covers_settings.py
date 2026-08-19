"""取り込みキャッシュが「設定違い」を別物として扱うこと (S2)。

--------------------------------------------------------------------------
症状: 切片の選択を変えて実行し直しても、変更前のデータで解析が走る
--------------------------------------------------------------------------
入力ファイルの読み込み結果は `_csv_rds_cache` に取り置きされる。
再利用してよいかの判定に使っているのは **元ファイルの大きさと更新時刻だけ**:

    ok <- all.equal(obj$meta$size,  fi$size) && all.equal(obj$meta$mtime, fi$mtime)

ところが取り置きされる中身は、読み込み時に

  - `ANNOTATION_FILTER`（切片の選択）で **行を間引いた後**のもの
  - `USE_EMBEDDED_COMPOUND_NAMES`（化合物名を使うか m/z 表記か）で
    **特徴量の名前を決めた後**のもの

である。元ファイルは変わっていないので、**切片の選択だけを変えて
「解析実行」を押し直すと、変更前の選択のデータがそのまま使われる**。
画面に警告は出ず、結果に付く記録には新しく選んだ切片名が書かれるため、
見た目には正常に見える（出力フォルダ名を変えると正しくなる —
キャッシュの置き場所が出力フォルダ配下だから）。

--------------------------------------------------------------------------
直し方
--------------------------------------------------------------------------
「取り置きした中身を左右する設定」を指紋にして鍵へ加える。
指紋が違えば読み直す。設定を変えない限り従来どおりキャッシュが効く。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "Script"
TIMS_V6 = SCRIPT / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"

_SRC = TIMS_V6.read_text(encoding="utf-8")


def _cached_reader_body() -> str:
    i = _SRC.index("read_desi_data_cached <- function")
    j = _SRC.index("\n# ---- ヘルパー", i)
    return _SRC[i:j]


# ---------------------------------------------------------------------------
# 前提: 取り置きされる中身が設定に左右されること
# ---------------------------------------------------------------------------

def test_the_reader_really_depends_on_those_settings():
    """読み込み処理が切片フィルタと化合物名の設定を見ていること。"""
    i = _SRC.index("read_desi_data <- function")
    body = _SRC[i:i + 20000]
    assert "ANNOTATION_FILTER" in body, "切片フィルタを見ていない（前提が変わった）"
    assert "USE_EMBEDDED_COMPOUND_NAMES" in body, "化合物名の設定を見ていない"


# ---------------------------------------------------------------------------
# 本丸: 鍵が設定を含むこと
# ---------------------------------------------------------------------------

def test_the_cache_stores_a_settings_fingerprint():
    """★ 取り置きの際に設定の指紋も一緒に保存すること。"""
    body = _cached_reader_body()
    save = re.search(r"saveRDS\(list\(meta\s*=\s*list\((.*?)\)\s*,\s*data", body, re.S)
    assert save, "キャッシュの書き出しが見つからない"
    assert "settings" in save.group(1), (
        f"キャッシュの meta に設定の指紋が入っていない: {save.group(1)[:200]}。"
        "大きさと更新時刻だけでは、**設定を変えた再実行が旧データを掴む**")


def test_the_cache_validates_the_settings_fingerprint():
    """★ 再利用の判定で指紋を突き合わせること。"""
    body = _cached_reader_body()
    # `ok <- ...` は複数行に続く（次の文が始まるまで）
    m = re.search(r"\n\s*ok\s*<-(.*?)\n\s*if\s*\(ok\)", body, re.S)
    assert m, "再利用判定 (`ok <- ...` → `if (ok)`) が見つからない"
    assert "settings" in m.group(1), (
        f"再利用判定が指紋を見ていない: {m.group(1).strip()[:250]}")


def test_the_fingerprint_helper_exists():
    """指紋の作り方が 1 か所に定義されていること。"""
    assert ".cache_settings_key <- function" in _SRC, (
        "指紋を作るヘルパーが無い。保存側と検証側で式が二重管理になると、"
        "片方だけ直したときに黙って食い違う")


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R が無い環境")
def test_the_fingerprint_changes_with_each_setting():
    """★ 指紋が実際に設定ごとに変わること（R で実行して確かめる）。

    ヘルパーだけを取り出して評価するので、Seurat は要らない。
    """
    m = re.search(r"\.cache_settings_key\s*<-\s*function.*?\n\}", _SRC, re.S)
    assert m, "ヘルパーの定義を取り出せない"
    helper = m.group(0)

    code = helper + """
key <- function(af, emb) {
  ANNOTATION_FILTER <<- af
  USE_EMBEDDED_COMPOUND_NAMES <<- emb
  .cache_settings_key()
}
a <- key(NULL, TRUE)
b <- key(c("slice_1"), TRUE)
c1 <- key(c("slice_1","slice_2"), TRUE)
d <- key(NULL, FALSE)
e <- key(c("slice_2","slice_1"), TRUE)
cat(a != b, b != c1, a != d, e == c1, sep="\\n")
"""
    out = subprocess.run(["Rscript", "-e", code], capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, out.stderr
    got = out.stdout.split()
    assert got[0] == "TRUE", "切片を選ぶと指紋が変わること"
    assert got[1] == "TRUE", "選ぶ切片を増やすと指紋が変わること"
    assert got[2] == "TRUE", "化合物名の設定で指紋が変わること"
    assert got[3] == "TRUE", (
        "同じ切片を順番違いで選んだだけで指紋が変わってはいけない"
        "（無意味な読み直しになる）")


def test_the_cache_still_works_when_nothing_changed():
    """★ 直しすぎの検出: 設定が同じなら従来どおり再利用すること。

    毎回読み直す実装にしてしまうと、大きなデータで所要時間が跳ね上がる。
    「大きさ・更新時刻が同じ」の判定を残していることを見張る。
    """
    body = _cached_reader_body()
    assert "obj$meta$size" in body and "obj$meta$mtime" in body, (
        "元ファイルの大きさ・更新時刻の判定が消えている。"
        "指紋だけにすると、ファイルが差し替わっても気づけない")
