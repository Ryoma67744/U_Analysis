"""「無補正 PCA」の図は、無補正の空間でクラスタを取り直すこと (A-2・S1)。

--------------------------------------------------------------------------
症状
--------------------------------------------------------------------------
結果画面で「PCA (uncorrected)」を選ぶと、補正とは独立した解析結果のように
見える。しかし実際に表示されるのは

    座標  … 無補正 PCA から作り直したもの
    クラスタ … **補正 (Harmony) で決めたものをそのまま流用**

という混成で、クラスタ番号・各クラスタのマーカー一覧・空間マップ・
AI による解釈まで、すべて補正後の定義で作られていた。
「補正の有無でクラスタがどう変わるか」を見る目的には使えない。

--------------------------------------------------------------------------
なぜ今直すか
--------------------------------------------------------------------------
A-1 で「補正なし」を選べるようにしたため、補正なしを選んだ解析では
**この図が主結果になる**。無補正ビューだけ補正後のクラスタを借りていると、
同じ「無補正」でも経路によって中身が変わり一貫しない。

--------------------------------------------------------------------------
どう直すか
--------------------------------------------------------------------------
TIMS には「クラスタが無ければ作り直す」経路が既にあるので、
コンパニオンを作るときにクラスタを引き継がず、その経路を通す。
**取り直した結果を保存し直す処理がこの図だけ対象外**になっているので、
そこも直す（直さないと画面は旧クラスタ・表は新クラスタで食い違う）。

DESI には同等の仕組みが無いので、本体側にコンパニオン出力を新設する。

なおクラスタ番号が補正後と対応しなくなるのは**当然のこと**（別々に決めた
のだから対応する必要が無い）。取り直したことは記録に残す。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TIMS_V6 = ROOT / "Script" / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"
DESI_V16 = ROOT / "Script" / "DESI" / "260623_DESI-UMAP_Template_v16.R"
HELPER = ROOT / "Script" / "helpers" / "derive_uncorrected_pca.R"

_TIMS = TIMS_V6.read_text(encoding="utf-8")
_DESI = DESI_V16.read_text(encoding="utf-8")


def _fn_body(src: str, header: str) -> str:
    i = src.index(header)
    j = src.index("{", i)
    depth = 0
    for k in range(j, len(src)):
        if src[k] == "{":
            depth += 1
        elif src[k] == "}":
            depth -= 1
            if depth == 0:
                return src[i:k + 1]
    raise AssertionError("関数本体を閉じられない")


# ---------------------------------------------------------------------------
# TIMS: クラスタを引き継がず取り直すこと
# ---------------------------------------------------------------------------

def test_the_companion_does_not_inherit_the_corrected_clusters():
    """★ 本丸: コンパニオンを作るときにクラスタを引き継がないこと。"""
    i = _TIMS.index("ALWAYS_OUTPUT_UNCORRECTED_PCA) && !identical(REDUCTION_USED")
    block = _TIMS[i:i + 1500]
    assert "seurat_clusters" in block and "NULL" in block, (
        "補正後のクラスタを落としていない。落とさないと"
        "『無補正の座標＋補正後のクラスタ』という混成のままになる")


def test_the_downstream_can_be_told_to_recluster():
    """★ 取り直しを明示的に指示できること。"""
    assert re.search(r"run_downstream_analysis <- function\([^)]*force_recluster",
                     _TIMS), (
        "run_downstream_analysis に取り直しの指示が無い。"
        "クラスタ列の有無だけに頼ると、引き継ぎを消し忘れた瞬間に黙って戻る")
    body = _fn_body(_TIMS, "run_downstream_analysis <- function")
    assert re.search(r"if\s*\(\s*isTRUE\(force_recluster\)\s*\|\|", body), (
        "取り直しの指示がクラスタリングの条件に効いていない")


def test_the_companion_call_asks_for_a_recluster():
    """★ コンパニオンの下流処理が取り直しを要求すること。"""
    i = _TIMS.index('run_downstream_analysis(.unc_obj, "pca_uncorrected"')
    call = _TIMS[i:i + 300]
    assert "force_recluster = TRUE" in call, (
        f"コンパニオンが取り直しを要求していない: {call[:160]}")


def test_the_new_clusters_are_written_back():
    """★ 取り直したクラスタが保存し直されること。

    ここを忘れると、画面 (RDS の Idents を読む) は旧クラスタ、
    マーカー表 (CSV) は新クラスタになり、ヒートマップが別クラスタ同士を
    突き合わせる。
    """
    body = _fn_body(_TIMS, "run_downstream_analysis <- function")
    i = body.index("Idents(obj) <- obj$seurat_clusters")
    tail = body[i:i + 1500]
    assert "pca_uncorrected" in tail, (
        "取り直したクラスタを保存し直す分岐が pca_uncorrected を対象にしていない。"
        "画面と表で別のクラスタが使われる")


# ---------------------------------------------------------------------------
# DESI: コンパニオンを本体側で作ること
# ---------------------------------------------------------------------------

def test_desi_produces_an_uncorrected_companion():
    """★ DESI も無補正 PCA のコンパニオンを出すこと。"""
    assert "ALWAYS_OUTPUT_UNCORRECTED_PCA" in _DESI, (
        "DESI にコンパニオン出力が無い。画面から都度作る補助スクリプトに頼ると、"
        "クラスタリングの条件 (近傍数・解像度) を渡す経路が無く、"
        "本解析と違う条件でクラスタが決まってしまう")


def test_desi_companion_reclusters_on_the_uncorrected_space():
    """★ DESI のコンパニオンが無補正空間でクラスタを決めること。"""
    # 宣言行ではなく **使っている側** を見る
    i = _DESI.index("isTRUE(ALWAYS_OUTPUT_UNCORRECTED_PCA)")
    block = _DESI[i:i + 3000]
    assert "FindClusters" in block and "FindNeighbors" in block, (
        "DESI のコンパニオンがクラスタを取り直していない")
    assert 'reduction = "pca"' in block, (
        "無補正 (pca) の空間でクラスタを決めていない")


def test_desi_companion_only_when_correction_ran():
    """★ 直しすぎの検出: 補正していないときは二重に出さないこと。

    「補正なし」を選んだ実行では主結果が既に無補正なので、
    同じものをもう一度出す意味が無い。
    """
    i = _DESI.index("isTRUE(ALWAYS_OUTPUT_UNCORRECTED_PCA)")
    block = _DESI[i:i + 600]
    assert ".correct_multi" in block, (
        "補正の有無を見ずにコンパニオンを出している")


# ---------------------------------------------------------------------------
# 画面が見つけられること
# ---------------------------------------------------------------------------

def test_the_viewer_finds_the_desi_companion(tmp_path):
    """★ DESI のコンパニオンを「PCA (uncorrected)」として拾うこと。"""
    from app.callbacks.interactive_callbacks import _detect_integration_methods

    (tmp_path / "DESI_SeuratCombined_harmony.rds").write_bytes(b"x")
    (tmp_path / "DESI_SeuratCombined_PCA_uncorrected.rds").write_bytes(b"x")
    got = _detect_integration_methods(str(tmp_path))
    assert got.get("PCA (uncorrected)"), (
        f"DESI のコンパニオンを拾えていない: {got}")
    assert got.get("Harmony"), "従来の Harmony 検出を壊している"


def test_the_derived_cache_is_invalidated_when_the_helper_changes(tmp_path):
    """★ 補助スクリプトを直しても古い派生結果が返り続けないこと。

    派生結果は「元ファイルのパス」だけを鍵にして取り置きされるため、
    スクリプトを直しても以前の結果がそのまま返っていた。
    """
    src = (ROOT / "app" / "callbacks" / "interactive_callbacks.py").read_text(encoding="utf-8")
    i = src.index("derived_pca")
    head = src[max(0, i - 800):i]
    assert "_DERIVE_PCA_VERSION" in head or "_DERIVE_PCA_VERSION" in src[i:i + 400], (
        "派生結果の鍵に補助スクリプトの版が入っていない。"
        "直しても古い結果が返り続ける")


# ---------------------------------------------------------------------------
# 記録
# ---------------------------------------------------------------------------

def test_the_recomputed_clusters_are_recorded():
    """★ 「クラスタを取り直した」ことが利用者に伝わること。

    クラスタ番号は補正後と対応しない。対応しないこと自体は当然だが、
    **それを知らないと番号で突き合わせてしまう**ので記録に残す。
    """
    from app.services.methods_text import _WARNING_TEXTS

    assert "uncorrected_clusters_recomputed" in _WARNING_TEXTS, (
        "「無補正側はクラスタを取り直した」という説明が用意されていない")
    ja, _en = _WARNING_TEXTS["uncorrected_clusters_recomputed"]
    assert "対応" in ja, f"番号が対応しないことに触れていない: {ja}"
