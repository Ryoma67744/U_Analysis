"""DESI の差次発現検定が「正しい値」で行われること (S1 1 件 + S2 1 件)。

--------------------------------------------------------------------------
① RPCA 分岐だけ、位置合わせ用に作り直した値で検定している (S1)
--------------------------------------------------------------------------
複数サンプルを RPCA で統合する分岐は、統合のために作り直した
`integrated` アッセイを既定にしたまま `FindAllMarkers` を呼ぶ。
この値は再構成されたもの（負値も取る）で、Seurat の公式見解でも
統合後の補正値での検定は推奨されない。

同じ v16 の **Harmony 分岐は正しく測定値へ戻してから**検定しており
（`assay_hm_harmony <- if ("Spatial" %in% Assays(...)) "Spatial" else …`）、
TIMS 本解析も検定直前に `DefaultAssay(obj) <- "Spatial"` を置いている。
**DESI の RPCA 分岐だけが例外**で、同じアプリの中で手法間の一貫性が崩れている。

→ 直すとマーカー一覧・p 値・fold change が変わる（TIMS と同じ土台になる）。

--------------------------------------------------------------------------
② 画面の log2FC 閾値が検定に届かない (S2)
--------------------------------------------------------------------------
DESI は 3 か所すべての `FindAllMarkers` で `logfc.threshold = 0.25` を
**直書き**している。画面で設定した値 (`DEG_LOGFC_TH_VAL`) は volcano の
色分けと線の位置にしか使われないため、**閾値を変えても出てくる特徴分子の
一覧がまったく変わらない**。TIMS は同じ場所で `DEG_LOGFC_TH_VAL` を渡している。

なお画面の既定値は 0.25 で直書きの値と同じなので、**既定のまま使っている
限りこの修正で数値は変わらない**。変わるのは「利用者が値を変えたとき」だけで、
それはまさに設定が効くようになったということである。

`min.pct` は DESI が 0.25、TIMS が 0.05 と食い違っているが、**どちらも
画面から設定できない**ため本テストの対象外とする（統一するかは別途判断）。
"""

import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "Script"
DESI_V16 = SCRIPT / "DESI" / "260623_DESI-UMAP_Template_v16.R"
TIMS_V6 = SCRIPT / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"


def _lines(path: Path):
    return path.read_text(encoding="utf-8").splitlines()


def _find_calls(lines, pattern):
    return [(i + 1, ln) for i, ln in enumerate(lines) if re.search(pattern, ln)]


# ---------------------------------------------------------------------------
# ① 検定するアッセイ
# ---------------------------------------------------------------------------

def test_tims_restores_the_measurement_assay_before_testing():
    """基準となる実装（TIMS）は検定前に測定値へ戻している。"""
    lines = _lines(TIMS_V6)
    restore = [n for n, _ in _find_calls(lines, r'DefaultAssay\(obj\)\s*<-\s*"Spatial"')]
    marker = [n for n, _ in _find_calls(lines, r"deg\s*<-\s*FindAllMarkers\(obj")
              ]
    assert restore and marker, "TIMS 側の基準実装が見つからない"
    assert min(restore) < min(marker), (
        "TIMS が検定前にアッセイを戻さなくなった（基準実装が壊れている）")


def test_desi_harmony_branch_restores_the_measurement_assay():
    """v16 の Harmony 分岐も正しい（こちらは既に測定値へ戻している）。"""
    src = DESI_V16.read_text(encoding="utf-8")
    assert re.search(
        r'assay_hm_harmony\s*<-\s*if\s*\(\s*"Spatial"\s*%in%\s*Seurat::Assays\(seu_harmony\)',
        src), "Harmony 分岐の測定値復帰が見当たらない"


def test_desi_rpca_branch_does_not_test_on_the_integrated_assay():
    """★ 本丸: RPCA 分岐も検定前に測定値へ戻すこと。

    `DefaultAssay(seu_rpca) <- "integrated"` のあと、`FindAllMarkers(seu_rpca…)`
    に到達するまでに測定値アッセイへ戻す代入が無いことを検出する。
    """
    lines = _lines(DESI_V16)
    to_integrated = [n for n, _ in _find_calls(
        lines, r'DefaultAssay\(seu_rpca\)\s*<-\s*"integrated"')]
    marker = [n for n, _ in _find_calls(lines, r"FindAllMarkers\(seu_rpca")]
    assert to_integrated, "RPCA 分岐の integrated 設定が見つからない（前提が変わった）"
    assert marker, "RPCA 分岐の FindAllMarkers が見つからない（前提が変わった）"

    start, end = min(to_integrated), min(marker)
    between = "\n".join(lines[start:end - 1])
    restored = re.search(
        r'DefaultAssay\(seu_rpca\)\s*<-\s*(?!"integrated")', between)
    assert restored, (
        f"v16:{start} で integrated にしたまま v16:{end} で検定している。"
        "統合のために作り直した値（負値も取る）での検定は推奨されない。"
        "Harmony 分岐と TIMS は測定値へ戻してから検定しており、"
        "**RPCA 分岐だけが例外**")


# ---------------------------------------------------------------------------
# ② 画面の log2FC 閾値
# ---------------------------------------------------------------------------

_FINDALL = r"FindAllMarkers\("


# ★ ver58.0 (デバッグ総点検 A-3) による契約の変更:
#   ver57.5 では「画面の log2FC 閾値が **検定** に届くこと」を求めていた。
#   しかしその形だと、閾値を通った分子だけに多重比較補正がかかり、
#   **補正の分母が画面の設定で動く**（統計的に妥当でない）。
#   A-3 で「検定と補正は全特徴量に対して行い、画面の閾値は **書き出しの絞り込み**
#   に効かせる」形へ変えた。したがってここで求めるものも変わる:
#     旧: FindAllMarkers(logfc.threshold = DEG_LOGFC_TH_VAL)
#     新: FindAllMarkers(logfc.threshold = 0) かつ 書き出しが DEG_LOGFC_TH_VAL を見る
#   「設定を変えても一覧が変わらない」という元の症状が再発しないことは、
#   下の test_the_screen_threshold_still_changes_the_output が引き続き見張る。

def test_no_branch_pre_filters_before_testing():
    """★ どの分岐も検定前に足切りしないこと（補正の分母を守る）。"""
    offenders = []
    for path, label in ((DESI_V16, "DESI"), (TIMS_V6, "TIMS")):
        for n, ln in _find_calls(_lines(path), _FINDALL):
            m = re.search(r"logfc\.threshold\s*=\s*([^,\)]+)", ln)
            if m and m.group(1).strip() not in ("0", "0L", "0.0"):
                offenders.append(f"{label}:{n}  logfc.threshold = {m.group(1).strip()}")
    assert not offenders, (
        "検定前に足切りしている。補正の分母が小さくなる:\n  " + "\n  ".join(offenders))


def test_the_screen_threshold_still_changes_the_output():
    """★ 元の症状の再発防止: 画面の閾値が出力に効くこと。

    検定には届かなくなったが、**書き出しの絞り込み**には効かなければならない。
    効かないと「設定を変えても一覧が変わらない」という元の症状に戻る。
    """
    for path, label in ((DESI_V16, "DESI"), (TIMS_V6, "TIMS")):
        src = path.read_text(encoding="utf-8")
        i = src.index(".deg_for_export <- function")
        body = src[i:i + 900]
        assert "DEG_LOGFC_TH_VAL" in body, (
            f"{label}: 書き出しの絞り込みが画面の log2FC 閾値を見ていない")


def test_the_threshold_constant_still_exists():
    """注入先の定数が消えていないこと（Python 側が書き換える先）。"""
    src = DESI_V16.read_text(encoding="utf-8")
    assert re.search(r"^DEG_LOGFC_TH_VAL\s*<-", src, re.M), (
        "DEG_LOGFC_TH_VAL の定義が無い。analysis_runner が書き換える先が失われる")


def test_every_default_matches_the_screen_default():
    """★ 直しすぎの検出: R 側の既定値が画面の既定値 (0.25) と一致すること。

    検定がこの定数を見るようになったので、**アプリから値が注入されない
    使い方**（R を直接実行する / 再解析の欄を空にする）で既定値が違うと、
    設定を触っていないのに閾値が変わってしまう。
    """
    targets = {
        "DESI 本解析": (DESI_V16, r"^DEG_LOGFC_TH_VAL\s*<-\s*([0-9.]+)"),
        "DESI 再解析": (SCRIPT / "DESI" / "DESI_RDS_ClusterFilter_ver3.R",
                     r"^V8_DEG_LOGFC_TH_VAL\s*<-\s*([0-9.]+)"),
        "TIMS 再解析": (SCRIPT / "TIMS" / "260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R",
                     r"^V13_DEG_LOGFC_TH_VAL\s*<-\s*([0-9.]+)"),
    }
    for label, (path, pat) in targets.items():
        m = re.search(pat, path.read_text(encoding="utf-8"), re.M)
        assert m, f"{label}: 既定値が読み取れない"
        assert float(m.group(1)) == 0.25, (
            f"{label} の既定値が {m.group(1)}。画面の既定値 0.25 と揃っていないと、"
            "値を注入しない経路で閾値が黙って変わる")


def test_the_methods_text_can_recover_the_real_min_pct():
    """★ Methods 文に**実際に使った**検出率が載ること（数値は変えない）。

    DESI は `min.pct = 0.25` で検定しているのに、実行スクリプトから
    復元できる定数が無かったため、受領書と Methods 文には
    `provenance.BATCH_DE_FIXED_PARAMS` のフォールバック値 0.05
    （TIMS の既定）が書かれていた。**実際と違う値が論文の方法に載る**。

    定数化は記録のためだけで、値は従来の直書きと同じ 0.25 なので
    解析結果は 1 つも変わらない。
    """
    from app.services.runtime_script import recover_conditions

    conditions = {}
    recover_conditions(conditions, script_path=str(DESI_V16))
    got = conditions.get("analysis", {}).get("de", {}).get("min_pct")
    assert got == 0.25, (
        f"DESI の実行スクリプトから min.pct を復元できない (得られた値: {got})。"
        "復元できないと provenance のフォールバック 0.05 (TIMS の値) が"
        "Methods 文に書かれる")


def test_the_min_pct_constant_matches_what_the_test_uses():
    """定数と実際の呼び出しが食い違わないこと（二重管理の防止）。"""
    src = DESI_V16.read_text(encoding="utf-8")
    m = re.search(r"^DEG_MIN_PCT_VAL\s*<-\s*([0-9.]+)", src, re.M)
    assert m, "DEG_MIN_PCT_VAL の定義が無い"
    assert float(m.group(1)) == 0.25, (
        f"既定が {m.group(1)}。従来の直書き値 0.25 と違うと**結果が変わる**")
    # ★ ver58.0 (A-3): 検定は足切りなし (0) に変えたので、呼び出し側は 0 でよい。
    #   定数は「記録のため」に残す（受領書・Methods が実効値を書けるように）。
    for n, ln in _find_calls(_lines(DESI_V16), _FINDALL):
        mm = re.search(r"min\.pct\s*=\s*([^,\)]+)", ln)
        if mm:
            assert mm.group(1).strip() in ("0", "0L", "0.0"), (
                f"v16:{n} が検定前に足切りしている: {mm.group(1).strip()}")


def test_the_default_keeps_todays_numbers():
    """★ 直しすぎの検出: 画面の既定値 (0.25) では従来と同じ計算になること。

    既定のまま使っている解析の数値が変わらないことを固定する。
    値が変わるのは「利用者が明示的に変えたとき」だけでなければならない。
    """
    from app.layouts import settings_tab  # noqa: F401  (import 可能性の確認)
    src = (Path(__file__).resolve().parents[1]
           / "app" / "layouts" / "settings_tab.py").read_text(encoding="utf-8")
    m = re.search(r'id="logfc_thresh".*?value=ls\.get\("logfc_thresh",\s*([0-9.]+)\)',
                  src, re.S)
    assert m, "画面の log2FC 閾値の既定値が読み取れない"
    assert float(m.group(1)) == 0.25, (
        f"画面の既定値が {m.group(1)} に変わっている。"
        "従来の直書き値 0.25 と違う既定にすると、"
        "**設定を触っていない利用者の結果まで変わる**")
