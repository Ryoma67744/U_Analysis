"""DESI でも「補正なし」を選べ、選んだら本当に補正しないこと (A-1・DESI 側)。

--------------------------------------------------------------------------
なぜ必要か
--------------------------------------------------------------------------
TIMS には解析シナリオの選択があり「補正なし」を選べる（実処理が伴っていなかった
のは A-1 の TIMS 側で直した）。ところが **DESI には補正の有無を選ぶ画面が無く**、
複数ファイルを読み込むと無条件に

    RunHarmony(object = seu_harmony, group.by.vars = "sample")

が走り、続けて RPCA 統合も走っていた。MSI では 1 ファイル＝1 切片＝多くの場合
1 個体・1 条件なので、sample 単位の補正は **除きたい機械差ではなく比較したい
生物差そのもの** を削ることに直結する（Nygaard 2016）。
それを回避する手段が DESI には一つも無かった。

--------------------------------------------------------------------------
DESI に合わせた選択肢にする
--------------------------------------------------------------------------
TIMS のシナリオ表は「切片(slice_id)」「連続切片」といった TIMS 側の概念を含むが、
**DESI の補正は `group.by.vars = "sample"` 固定**でそれらを実装していない。
そこで DESI には実際にできることだけを 2 択で出す:

  - 補正する（Harmony + RPCA）… 既定＝従来どおり
  - 補正しない（無補正 PCA）

既定を「補正する」にするのは、**設定を触っていない利用者の結果が黙って
変わらない**ようにするため。

--------------------------------------------------------------------------
出力先の整合（これを外すと画面が空になる）
--------------------------------------------------------------------------
補正しないときの結果を `Harmony/` に置くと名前と実体が食い違う。かといって
新しい名前のフォルダにすると、マーカー表を探す側（`deg_utils`）が
`Harmony / RPCA / PCA / pca_uncorrected` の 4 つしか知らないため、
**マーカー表・Volcano・ヒートマップ・GPT がすべて空になる**。
そこで補正なしの出力は `PCA/` に置き、RDS も `..._PCA.rds` にする。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESI_V16 = ROOT / "Script" / "DESI" / "260623_DESI-UMAP_Template_v16.R"

_SRC = DESI_V16.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 画面: 選べること
# ---------------------------------------------------------------------------

def test_the_screen_offers_a_no_correction_option():
    """★ DESI にも補正の有無を選ぶ欄があること。"""
    from app.layouts.settings_tab import create_settings_tab

    ids = set()

    def walk(node):
        cid = getattr(node, "id", None)
        if isinstance(cid, str):
            ids.add(cid)
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            for c in children:
                walk(c)
        elif children is not None:
            walk(children)

    walk(create_settings_tab())
    assert "desi_scenario" in ids, (
        "DESI に補正の有無を選ぶ欄が無い。複数ファイルでは必ず補正が走ってしまう")


def test_the_default_keeps_todays_behaviour():
    """★ 直しすぎの検出: 既定は従来どおり「補正する」こと。"""
    src = (ROOT / "app" / "layouts" / "settings_tab.py").read_text(encoding="utf-8")
    m = re.search(r'id="desi_scenario".*?value=(ls\.get\([^)]*\))', src, re.S)
    assert m, "desi_scenario の既定値が読み取れない"
    assert '"correct"' in m.group(1), (
        f"DESI の既定が {m.group(1).strip()}。既定を「補正なし」にすると、"
        "設定を触っていない利用者の結果まで黙って変わる")


def test_the_choice_reaches_the_analysis(monkeypatch, tmp_path):
    """★ 画面の選択が R へ渡る値になること。"""
    from app.callbacks.analysis_callbacks import _DESI_SCENARIO_MAP

    assert _DESI_SCENARIO_MAP["no_correct"] is False
    assert _DESI_SCENARIO_MAP["correct"] is True


# ---------------------------------------------------------------------------
# R 側: 旗で補正を止められること
# ---------------------------------------------------------------------------

def test_the_template_has_the_flag():
    """★ DESI テンプレートにも明示の旗があること。"""
    m = re.search(r"^BATCH_CORRECTION_ENABLE\s*<-\s*(\S+)", _SRC, re.M)
    assert m, "DESI に BATCH_CORRECTION_ENABLE が無い"
    assert m.group(1) == "TRUE", f"既定が {m.group(1)}（従来挙動は補正する）"


def test_the_guard_variable_comes_from_the_flag():
    """判定に使う変数が旗から導かれていること（別物にすり替わっていない）。"""
    assert re.search(r"\.correct_multi\s*<-\s*isTRUE\(BATCH_CORRECTION_ENABLE\)", _SRC), (
        "補正の判定に使う変数が BATCH_CORRECTION_ENABLE から導かれていない")


def test_harmony_is_guarded_by_the_flag():
    """★ 旗が偽なら Harmony を実行しないこと。"""
    i = _SRC.index("RunHarmony(object = seu_harmony")
    head = _SRC[max(0, i - 400):i]
    assert ".correct_multi" in head, (
        "RunHarmony が旗で囲われていない。「補正なし」を選んでも補正が走る")


def test_rpca_is_guarded_by_the_flag():
    """★ 旗が偽なら RPCA 統合も走らないこと（補正の一種なので）。"""
    i = _SRC.index('message("Multi-sample mode: RPCA...")')
    head = _SRC[max(0, i - 400):i]
    assert ".correct_multi" in head, (
        "RPCA 分岐が旗で囲われていない。Harmony だけ止めても"
        "RPCA 側で補正された結果が出てしまう")


def test_the_uncorrected_output_goes_to_the_pca_folder():
    """★ 補正なしの結果が `PCA/` に出ること（画面が拾える名前）。"""
    assert re.search(r'DESI_SeuratCombined_PCA\.rds', _SRC), (
        "補正なしのときの RDS 名が無い。`Harmony` の名前で出すと実体と食い違い、"
        "`PCA` 以外の新しい名前にするとマーカー表を探す側が見つけられない")
    assert re.search(r'file\.path\(od,\s*if\s*\(.*BATCH_CORRECTION_ENABLE', _SRC) or \
           re.search(r'\.dir_multi\s*<-', _SRC), (
        "出力フォルダが旗で切り替わっていない")


# ---------------------------------------------------------------------------
# 画面が結果を見つけられること
# ---------------------------------------------------------------------------

def test_the_viewer_finds_the_uncorrected_desi_result(tmp_path):
    """★ `DESI_SeuratCombined_PCA.rds` を「PCA」として拾うこと。"""
    from app.callbacks.interactive_callbacks import _detect_integration_methods

    (tmp_path / "DESI_SeuratCombined_PCA.rds").write_bytes(b"x")
    got = _detect_integration_methods(str(tmp_path))
    assert got.get("PCA"), (
        f"補正なしの DESI 結果を拾えていない: {got}。"
        "拾えないと結果フォルダを開いても手法が 1 つも出てこない")


def test_the_uncorrected_companion_is_not_confused_with_it(tmp_path):
    """★ 直しすぎの検出: 無補正 PCA コンパニオンと取り違えないこと。"""
    from app.callbacks.interactive_callbacks import _detect_integration_methods

    (tmp_path / "Step2_PCA_uncorrected.rds").write_bytes(b"x")
    got = _detect_integration_methods(str(tmp_path))
    assert got.get("PCA (uncorrected)"), got
    assert "PCA" not in got, (
        f"コンパニオン (Step2_PCA_uncorrected.rds) を「PCA」としても拾っている: {got}")


def test_a_corrected_run_is_unchanged(tmp_path):
    """★ 直しすぎの検出: 従来の Harmony/RPCA の検出を壊さないこと。"""
    from app.callbacks.interactive_callbacks import _detect_integration_methods

    (tmp_path / "DESI_SeuratCombined_harmony.rds").write_bytes(b"x")
    (tmp_path / "DESI_SeuratCombined_RPCA.rds").write_bytes(b"x")
    got = _detect_integration_methods(str(tmp_path))
    assert got.get("Harmony") and got.get("RPCA")
