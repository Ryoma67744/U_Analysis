"""「補正なし」を選んだら本当に補正しないこと (A-1・S1)。

--------------------------------------------------------------------------
症状
--------------------------------------------------------------------------
解析シナリオで「同一切片のクラスタ／群比較（Ctrl vs KO 等）：補正なし＝無補正PCA」
を選んでも、**Ctrl と KO を別ファイルで読み込むと必ず Harmony が実行される**。
画面の既定表示もその補正後の結果になる。にもかかわらず、論文用に自動生成される
Methods 文にはシナリオの文言がそのまま使われ「バッチ補正は行わなかった」と書かれる。

原因は「補正するかどうか」を**変数名から推測していた**こと:

    .bv_is_bio <- (.bv %in% c("condition","slice_id")) && !ALLOW_CONDITION_CORRECTION
    group_var  <- if (.bv_levels > 1 && !.bv_is_bio) .bv else NA

「補正なし」シナリオが渡すのは `BATCH_VAR = "sample"` なので `.bv_is_bio` は偽になり、
**ファイルが 2 つ以上あれば必ず補正が走る**。補正を回避できるのは実質
「ファイルが 1 つだけ」のときだけだった。

--------------------------------------------------------------------------
なぜ危険か
--------------------------------------------------------------------------
MSI では 1 ファイル＝1 切片＝多くの場合 1 個体・1 条件である。
「サンプル単位で補正する」ことは、除きたい機械差ではなく
**比較したい生物差そのものを削る**ことに直結する
（Nygaard 2016, doi:10.1093/biostatistics/kxv027）。

--------------------------------------------------------------------------
直し方
--------------------------------------------------------------------------
補正の要否を名前から推測せず、**明示の旗**にする。
シナリオ表に「補正するか」を持たせ、R 側の `BATCH_CORRECTION_ENABLE` へ渡す。
旗が偽なら既存の「補正をスキップ → 無補正 PCA を使用」経路がそのまま走る。
"""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "Script"
TIMS_V6 = SCRIPT / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"

_SRC = TIMS_V6.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# シナリオ表: 「補正するか」を持つこと
# ---------------------------------------------------------------------------

def test_the_scenario_table_says_whether_to_correct():
    """★ シナリオごとに補正の要否が決まっていること。"""
    from app.callbacks.analysis_callbacks import _SCENARIO_MAP

    # 「補正なし」は補正しない
    assert _SCENARIO_MAP["within_slice"][3] is False, (
        "「補正なし」シナリオが補正する設定になっている")
    assert _SCENARIO_MAP["condition_compare"][3] is False, (
        "旧シナリオ (condition_compare) も「補正なし」と同じ扱いにすること")
    # 連続切片は技術反復なのでサンプル間補正が妥当
    assert _SCENARIO_MAP["serial_section"][3] is True
    # 明示的に補正を求めるシナリオ
    assert _SCENARIO_MAP["batch_correct"][3] is True
    assert _SCENARIO_MAP["integrate_correct"][3] is True


def test_the_correction_target_is_unchanged():
    """★ 直しすぎの検出: 補正する側の基準は変えないこと。"""
    from app.callbacks.analysis_callbacks import _SCENARIO_MAP

    assert _SCENARIO_MAP["serial_section"][1] == "sample"
    assert _SCENARIO_MAP["batch_correct"][1] == "slice_id"
    assert _SCENARIO_MAP["integrate_correct"][1] == "slice_id"


# ---------------------------------------------------------------------------
# R 側: 旗を持ち、旗が偽なら補正しないこと
# ---------------------------------------------------------------------------

def test_the_script_has_an_explicit_flag():
    """★ 補正の要否が明示の定数になっていること。"""
    m = re.search(r"^BATCH_CORRECTION_ENABLE\s*<-\s*(\S+)", _SRC, re.M)
    assert m, (
        "BATCH_CORRECTION_ENABLE が無い。補正の要否を変数名から推測している限り、"
        "「補正なし」を選んでも sample 単位の補正が走る")
    assert m.group(1) == "TRUE", (
        f"既定が {m.group(1)}。既定は従来挙動 (補正する) でなければ、"
        "設定を触っていない利用者の結果まで変わる")


def test_the_decision_uses_the_flag():
    """★ 補正変数の決定が旗を見ていること。"""
    m = re.search(r"group_var\s*<-\s*if\s*\((.+?)\)\s*\.bv\s*else", _SRC)
    assert m, "補正変数の決定行が見つからない"
    assert "BATCH_CORRECTION_ENABLE" in m.group(1), (
        f"補正の判定が旗を見ていない: {m.group(1)}")


@pytest.mark.skipif(shutil.which("Rscript") is None, reason="R が無い環境")
@pytest.mark.parametrize("enable,levels,bv,want", [
    # 「補正なし」= 旗が偽 → ファイルが何個でも補正しない
    (False, 1, "sample", "NA"),
    (False, 2, "sample", "NA"),
    (False, 5, "sample", "NA"),
    # 従来どおり: 旗が真なら sample が複数のとき補正する
    (True, 2, "sample", "sample"),
    (True, 1, "sample", "NA"),
    # 生物差の列は許可がない限り補正しない（従来どおり）
    (True, 2, "slice_id", "NA"),
])
def test_the_flag_decides_whether_harmony_runs(enable, levels, bv, want):
    """★ 本丸: 旗が偽なら補正変数が NA（＝Harmony を実行しない）になること。"""
    m = re.search(r"(\s*\.bv_is_bio\s*<-.+?\n\s*group_var\s*<-.+?\n)", _SRC, re.S)
    assert m, "判定ロジックを取り出せない"
    logic = m.group(1)

    code = f"""
BATCH_CORRECTION_ENABLE <- {"TRUE" if enable else "FALSE"}
ALLOW_CONDITION_CORRECTION <- FALSE
.bv <- "{bv}"
.bv_levels <- {levels}
{logic}
cat(if (is.na(group_var)) "NA" else group_var)
"""
    out = subprocess.run(["Rscript", "-e", code], capture_output=True, text=True,
                         timeout=180)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == want, (
        f"旗={enable} / サンプル数={levels} / 列={bv} のとき "
        f"補正変数が {out.stdout.strip()!r}（期待 {want!r}）")


def test_the_skip_path_already_exists():
    """前提: 補正変数が NA のときの「無補正 PCA を使う」経路が残っていること。"""
    assert "補正をスキップ" in _SRC, (
        "補正をスキップする経路の説明が消えている。"
        "旗を偽にしても行き先が無ければ意味がない")


# ---------------------------------------------------------------------------
# 記録
# ---------------------------------------------------------------------------

def test_the_flag_reaches_the_script(tmp_path):
    """★ 画面の選択が実行スクリプトへ届くこと。"""
    from app.services.analysis_runner import generate_v8_config

    params = {
        "template_path": str(TIMS_V6),
        "data_folder": str(tmp_path),
        "output_dir": str(tmp_path),
        "sample_names": ["s1", "s2"],
        "batch_correction_enable": False,
    }
    out = Path(generate_v8_config(params, str(tmp_path)))
    text = out.read_text(encoding="utf-8")
    assert re.search(r"^BATCH_CORRECTION_ENABLE\s*<-\s*FALSE", text, re.M), (
        "「補正なし」を選んでもスクリプトに届いていない:\n"
        + "\n".join(l for l in text.splitlines() if "BATCH_CORRECTION" in l))


def test_the_default_still_corrects(tmp_path):
    """★ 直しすぎの検出: 指定が無ければ従来どおり補正すること。"""
    from app.services.analysis_runner import generate_v8_config

    params = {
        "template_path": str(TIMS_V6),
        "data_folder": str(tmp_path),
        "output_dir": str(tmp_path),
        "sample_names": ["s1", "s2"],
    }
    out = Path(generate_v8_config(params, str(tmp_path)))
    text = out.read_text(encoding="utf-8")
    assert re.search(r"^BATCH_CORRECTION_ENABLE\s*<-\s*TRUE", text, re.M)
