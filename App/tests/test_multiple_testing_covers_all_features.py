"""多重比較の補正を「検定した全体」に対して行うこと (A-3)。

--------------------------------------------------------------------------
症状
--------------------------------------------------------------------------
補正は「ふるいを通り抜けた分子だけ」を対象にかけられていた。分母が小さいので
補正後の値が本来より甘く出る。既定の p 値閾値 0.05 のままだと、書き出される
分子は全部が自動的に「有意」と判定され、**この設定は実質まったく効いていない**。

対象は 5 か所。DESI の 3 分岐だけでなく、**TIMS 本解析と、対話画面の範囲選択に
よる比較**にも同じ書き方がある（報告書は DESI 3 か所と書いていたが過小記載だった）。

--------------------------------------------------------------------------
落とし穴: 見えない足切りがもう 1 つある
--------------------------------------------------------------------------
ふるいの指定 (min.pct / logfc.threshold) を 0 にするだけでは足りない。
解析エンジンには `return.thresh` という**既定 0.01 の足切り**があり、
これを外さない限り「弱い結果は最初から返らない」ため、補正の分母は
今までと変わらない。5 か所とも指定が無いので、明示的に 1 にする必要がある。

--------------------------------------------------------------------------
書き出しは従来どおり絞る（ご指定）
--------------------------------------------------------------------------
検定と補正は全体に対して行い、CSV と図に出すのは閾値を通ったものだけにする。
従来は「エンジンが勝手に絞った結果」をそのまま書いていたので、
**絞り込みの工程自体が存在しない**。書き出し直前に新設する。
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DESI_V16 = ROOT / "Script" / "DESI" / "260623_DESI-UMAP_Template_v16.R"
TIMS_V6 = ROOT / "Script" / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"
FINDMARKERS = ROOT / "Script" / "helpers" / "run_findmarkers.R"

_SOURCES = {
    "DESI 本解析": DESI_V16,
    "TIMS 本解析": TIMS_V6,
    "対話画面の範囲選択": FINDMARKERS,
}


def _marker_calls(path: Path):
    """`FindAllMarkers(` / `FindMarkers(` の呼び出しを、引数まで含めて返す。"""
    src = path.read_text(encoding="utf-8")
    out = []
    for m in re.finditer(r"Find(?:All)?Markers\(", src):
        i = src.index("(", m.start())
        depth = 0
        for j in range(i, len(src)):
            if src[j] == "(":
                depth += 1
            elif src[j] == ")":
                depth -= 1
                if depth == 0:
                    line = src[:m.start()].count("\n") + 1
                    out.append((line, src[m.start():j + 1]))
                    break
    return out


def test_every_testing_site_is_found():
    """前提: 5 か所すべてを見ていること（見落とすと片手落ちになる）。"""
    counts = {k: len(_marker_calls(v)) for k, v in _SOURCES.items()}
    assert counts["DESI 本解析"] == 3, counts
    assert counts["TIMS 本解析"] == 1, counts
    assert counts["対話画面の範囲選択"] == 2, counts   # local / global


# ---------------------------------------------------------------------------
# 本丸: 足切りを外して全体を検定すること
# ---------------------------------------------------------------------------

# 対話画面の比較は呼び出し側から値を受け取る作りなので、リテラル 0 ではなく
# 「呼び出し側の変数」を渡す。その変数の既定が 0 であることは
# test_the_interactive_defaults_match / test_the_python_side_defaults_match が見る。
_CALLER_SUPPLIED = {"対話画面の範囲選択": {"min_pct", "logfc_th"}}


@pytest.mark.parametrize("label", sorted(_SOURCES))
def test_no_pre_filter_before_testing(label):
    """★ 検定前のふるいを 0 にすること。"""
    allowed = _CALLER_SUPPLIED.get(label, set())
    offenders = []
    for line, call in _marker_calls(_SOURCES[label]):
        for arg in ("min.pct", "logfc.threshold"):
            m = re.search(re.escape(arg) + r"\s*=\s*([^,)]+)", call)
            if not m:
                offenders.append(f"{label}:{line}  {arg} の指定が無い")
                continue
            val = m.group(1).strip()
            if val in allowed:
                continue                       # 呼び出し側が決める（既定は別テストで固定）
            if val not in ("0", "0L", "0.0"):
                offenders.append(f"{label}:{line}  {arg} = {val}")
    assert not offenders, (
        "検定前にふるいをかけている。補正の分母が小さくなり、"
        "補正後の値が本来より甘く出る:\n  " + "\n  ".join(offenders))


@pytest.mark.parametrize("label", sorted(_SOURCES))
def test_the_hidden_return_threshold_is_lifted(label):
    """★ 見えない足切り (return.thresh) を明示的に外すこと。

    これを忘れると、ふるいを 0 にしても弱い結果が返らないため
    **補正の分母は今までと変わらない**。
    """
    offenders = []
    for line, call in _marker_calls(_SOURCES[label]):
        m = re.search(r"return\.thresh\s*=\s*([^,)]+)", call)
        if not m:
            offenders.append(f"{label}:{line}  return.thresh の指定が無い（既定 0.01 が効く）")
        elif m.group(1).strip() not in ("1", "1L", "1.0"):
            offenders.append(f"{label}:{line}  return.thresh = {m.group(1).strip()}")
    assert not offenders, (
        "解析エンジンの既定の足切りが残っている:\n  " + "\n  ".join(offenders))


# ---------------------------------------------------------------------------
# 書き出しは絞ること（ご指定）
# ---------------------------------------------------------------------------

def test_the_export_is_filtered():
    """★ CSV に出すのは閾値を通ったものだけにすること。"""
    src = DESI_V16.read_text(encoding="utf-8")
    assert ".deg_for_export" in src, (
        "書き出し用の絞り込みが無い。全特徴量をそのまま書くと"
        "行数が 1〜2 桁増える（ご指定は『CSV は従来どおり絞る』）")
    for name in ("analysis_deg_all_markers_", "analysis_top5_markers_per_cluster_"):
        i = src.index(f'paste0("{name}"')
        head = src[max(0, i - 700):i]
        assert ".deg_for_export" in head, (
            f"{name}… の書き出しが絞り込みを通っていない")


def test_the_export_filter_keeps_the_screen_thresholds():
    """★ 絞り込みが画面の閾値を使うこと（別の基準を勝手に作らない）。"""
    src = DESI_V16.read_text(encoding="utf-8")
    i = src.index(".deg_for_export <- function")
    body = src[i:i + 900]
    assert "DEG_LOGFC_TH_VAL" in body and "DEG_P_THRESH_VAL" in body, (
        "書き出しの絞り込みが画面の閾値を見ていない")


def test_tims_export_is_filtered_too():
    """★ TIMS 側も書き出しを絞ること。"""
    src = TIMS_V6.read_text(encoding="utf-8")
    assert ".deg_for_export" in src, "TIMS 側に書き出しの絞り込みが無い"


# ---------------------------------------------------------------------------
# 併せて直す副作用
# ---------------------------------------------------------------------------

def test_every_branch_floors_zero_pvalues():
    """★ 3 分岐すべてで p 値ゼロの床置換を行うこと。

    従来は Harmony 分岐だけ抜けていた。行数が増えるとゼロが出やすくなり、
    そのまま CSV に混ざる。
    """
    src = DESI_V16.read_text(encoding="utf-8")
    n = len(re.findall(r"\.floor_zero_padj\(", src))
    assert n >= 3, (
        f"床置換の呼び出しが {n} 箇所しかない。3 分岐すべてに要る")


def test_the_deg_cache_key_includes_the_test_conditions():
    """★ 検定条件を変えたら古い計算結果を再利用しないこと。"""
    src = TIMS_V6.read_text(encoding="utf-8")
    i = src.index("deg_FindAllMarkers_raw_")
    around = src[max(0, i - 1500):i + 1500]
    assert "DEG_MIN_PCT_VAL" in around or ".deg_cache_key" in around, (
        "DEG の取り置きが検定条件を見ていない。条件を変えても"
        "古いテーブルがそのまま使われる")


def test_the_interactive_defaults_match():
    """★ 対話画面の既定も揃えること（片方だけだと母集団が混在する）。"""
    src = FINDMARKERS.read_text(encoding="utf-8")
    for opt, want in (("--min-pct", "0"), ("--logfc", "0")):
        m = re.search(re.escape(opt) + r'"\s*,\s*"([^"]+)"', src)
        assert m, f"{opt} の既定が読み取れない"
        assert m.group(1) == want, (
            f"{opt} の既定が {m.group(1)}。バッチ側と揃えないと"
            "同じ画面に 2 つの補正母集団が混在する")


def test_the_python_side_defaults_match():
    """★ Python 側の既定も 0 にすること。

    R の既定だけ直しても、呼び出し側が 0.05 / 0.25 を渡していれば上書きされる。
    """
    src = (ROOT / "app" / "services" / "seurat_bridge.py").read_text(encoding="utf-8")
    m = re.search(r"def run_differential_expression\(.*?min_pct=([\d.]+),\s*logfc=([\d.]+)",
                  src, re.S)
    assert m, "on-the-fly DE の既定が読み取れない"
    assert float(m.group(1)) == 0.0 and float(m.group(2)) == 0.0, (
        f"Python 側の既定が min_pct={m.group(1)} / logfc={m.group(2)}。"
        "R 側だけ直しても上書きされる")


def test_the_recorded_filter_matches_reality():
    """★ 受領書に記録される足切りも実態に合わせること。"""
    from app.services.provenance import BATCH_DE_FIXED_PARAMS, ONTHEFLY_DE_FIXED_PARAMS

    assert BATCH_DE_FIXED_PARAMS["min_pct"] == 0.0
    assert ONTHEFLY_DE_FIXED_PARAMS["min_pct"] == 0.0


def test_the_methods_text_describes_the_new_scope():
    """★ Methods 文が「全体を検定して補正した」と書けること。"""
    from app.services.methods_text import render_methods

    conditions = {"analysis": {"de": {"min_pct": 0}, "thresholds": {"logfc": 0.25}}}
    text = render_methods(conditions, lang="ja")
    assert "限定した" not in text, (
        "「検定に先立ち…に限定した」という記述が残っている。"
        "全特徴量を検定するようになったので事実と違う")
