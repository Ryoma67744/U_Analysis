"""メモリ不足で軽い設定に落ちたことが、記録に残ること (R3-RETRY)。

--------------------------------------------------------------------------
症状: 実際とは違うハイパーパラメータが Methods に載る
--------------------------------------------------------------------------
大きなデータで 1 回目の計算が失敗すると、R スクリプトは黙って軽い設定へ
落として計算をやり直す。

    可変特徴量 3000 → 1000 → 500
    主成分数     30 →   20 →  15
    UMAP 次元    30 →   20 →  15

ところが「落とした」ことは画面にもログにも一切出ず、あとから受領書
(receipt.json) や Methods 下書きを見ても、最初に指定した構成値
(例: 次元数 30) が書かれている。

落ちた実行は 1000 または 500 個の可変特徴量・20 または 15 次元で UMAP と
クラスタリングを計算するので、通った実行とはクラスタ数・境界・マーカーが
系統的に異なる。にもかかわらず Methods には dims=30・HVG 3000 相当の
構成値が載るため、**論文の Methods に実際とは違う値が書かれ**、
同一設定での再現実験も成立しない。

失敗理由も `tryCatch(..., error = function(e) FALSE)` が丸ごと捨てていたので、
「なぜ落ちたのか」もログに残らなかった。

数値そのもの（そのとき使われた設定に対する計算結果）は正しいので、
記録を直しても解析結果は変わらない。
"""

import json
import re
from pathlib import Path

import pytest

from app.services.receipt import build_receipt

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "Script"
TIMS_V6 = SCRIPT_DIR / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"
RDS_IO = SCRIPT_DIR / "helpers" / "rds_io.R"


# ---------------------------------------------------------------------------
# R 側: リトライループが痕跡を残すこと
# ---------------------------------------------------------------------------

def test_retry_loop_reports_the_failure_reason():
    """★ 失敗理由を握り潰さないこと。

    `error = function(e) FALSE` は例外オブジェクトごと捨てるため、
    メモリ不足なのか別の不具合なのかも分からなくなっていた。
    """
    # コメント行は対象外（修正の経緯説明として当時の形を引用しているため）
    code = "\n".join(line for line in TIMS_V6.read_text(encoding="utf-8").splitlines()
                     if not line.lstrip().startswith("#"))
    silent = re.findall(r"error\s*=\s*function\(e\)\s*FALSE", code)
    assert not silent, (
        f"失敗理由を捨てる tryCatch が {len(silent)} 件残っている"
        "（リトライしたことがログに出ない）")


def test_retry_loop_logs_the_adopted_tier():
    """★ 採用した段と、その設定値をログに出すこと。"""
    src = TIMS_V6.read_text(encoding="utf-8")
    assert "[retry]" in src, "リトライの記録が 1 行も出ていない"
    for token in ("n_var_features", "max_pcs", "umap_dims"):
        assert re.search(r"\[retry\][^\n]*", src) and token in src, token


def test_effective_settings_are_exposed_to_the_sidecar():
    """★ 採用した設定を R のグローバルへ残し、サイドカーが拾えること。"""
    src = TIMS_V6.read_text(encoding="utf-8")
    for name in ("RETRY_N_VAR_FEATURES_EFFECTIVE",
                 "RETRY_MAX_PCS_EFFECTIVE",
                 "RETRY_UMAP_DIMS_EFFECTIVE",
                 "RETRY_TIER_USED"):
        assert re.search(name + r"\s*<<-", src), f"{name} をグローバルへ記録していない"

    sidecar = RDS_IO.read_text(encoding="utf-8")
    for name in ("n_var_features_effective", "max_pcs_effective",
                 "umap_dims_effective", "retry_tier_used"):
        assert name in sidecar, f"サイドカーが {name} を書いていない"


def test_r_script_still_parses():
    """R として構文が壊れていないこと。"""
    import shutil
    import subprocess
    if not shutil.which("Rscript"):
        pytest.skip("Rscript が無い環境")
    for path in (TIMS_V6, RDS_IO):
        proc = subprocess.run(
            ["Rscript", "-e", f'invisible(parse("{path}"))'],
            capture_output=True, text=True, timeout=180)
        assert proc.returncode == 0, f"{path.name}: {proc.stderr[-800:]}"


# ---------------------------------------------------------------------------
# Python 側: 実効値があればそちらを Methods / 受領書に載せること
# ---------------------------------------------------------------------------

def _receipt(sidecar_extra, params_extra=None):
    params = {"umap_dims_n": 30, "n_var_features": 3000, "max_pcs": 30}
    params.update(params_extra or {})
    sidecar = {"seed": 42}
    sidecar.update(sidecar_extra)
    return build_receipt(params, r_sidecar=sidecar)["object"]


def test_receipt_prefers_the_effective_dims():
    """★ 実際に使われた次元数を載せること（構成値ではなく）。"""
    r = _receipt({"umap_dims_effective": 20,
                  "n_var_features_effective": 1000,
                  "max_pcs_effective": 20,
                  "retry_tier_used": 2})
    assert r["umap"]["dims"] == 20, (
        "構成値 30 のまま載っている（実際は 20 次元で計算されている）")
    assert r["umap"]["dims_configured"] == 30
    assert r["umap"]["retry_tier_used"] == 2
    assert r["preprocessing"]["n_var_features"] == 1000
    assert r["preprocessing"]["max_pcs"] == 20


def test_receipt_flags_the_degradation():
    """劣化したことが一目で分かる印を立てること。"""
    r = _receipt({"umap_dims_effective": 20, "retry_tier_used": 2})
    assert r["umap"]["retry_degraded"] is True


def test_receipt_is_unchanged_when_tier1_succeeded():
    """1 段目で通ったときは従来どおりの内容であること。"""
    r = _receipt({"umap_dims_effective": 30,
                  "n_var_features_effective": 3000,
                  "max_pcs_effective": 30,
                  "retry_tier_used": 1})
    assert r["umap"]["dims"] == 30
    assert r["umap"]["retry_degraded"] is False


def test_receipt_falls_back_when_the_sidecar_is_old():
    """実効値の無い（古い）サイドカーでも従来どおり動くこと。"""
    r = _receipt({})
    assert r["umap"]["dims"] == 30
    assert r["umap"]["retry_degraded"] is False
    assert r["umap"]["retry_tier_used"] is None


# ---------------------------------------------------------------------------
# Methods 下書き（receipt.object.umap を経由して自動的に反映される経路）
# ---------------------------------------------------------------------------

def _methods_text(receipt_object):
    """受領書 → 条件 dict → Methods 下書き、と実経路を通して文字列にする。"""
    from app.services.provenance import _analysis_block
    from app.services.methods_text import render_methods
    conditions = {"analysis": _analysis_block({"object": receipt_object}, {}),
                  "integration_method": "Harmony"}
    return render_methods(conditions, lang="ja")


def _umap_dims_line(text):
    for line in text.splitlines():
        if line.startswith("| UMAP dims"):
            return line
    raise AssertionError("Methods 下書きに UMAP dims の行が無い")


def test_methods_text_uses_the_effective_dims():
    """★ Methods 下書きに、実際に使われた次元数が出ること。

    構成値 30 のまま書かれると、論文の Methods に実際とは違う値が載る。
    """
    text = _methods_text(_receipt({"umap_dims_effective": 15,
                                   "n_var_features_effective": 500,
                                   "max_pcs_effective": 15,
                                   "retry_tier_used": 3}))
    line = _umap_dims_line(text)
    assert "15" in line, line
    assert "30" not in line, f"構成値 30 のまま載っている: {line}"


def test_methods_text_survives_an_old_receipt():
    """実効値の無い（古い）レシートでも従来どおり構成値で書けること。"""
    text = _methods_text(_receipt({}))
    assert "30" in _umap_dims_line(text)


def test_receipt_json_is_serialisable():
    r = _receipt({"umap_dims_effective": 20, "retry_tier_used": 2})
    json.loads(json.dumps(r))
