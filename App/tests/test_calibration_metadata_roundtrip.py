"""キャリブレーションのメタ情報が保存→復元を通ること (ver51.9)。

■ 何が起きていたか

ver51.8 で `compute_calibration_coefficients` に

    requested_degree / ref_mz_min / ref_mz_max

を足し、コミットメッセージには「外挿を UI で警告する」と書いた。
**実際には一度も警告が出ない。**

  1. 保存側 (`analysis_callbacks._params_to_save`) が 3 キーを **書いていない**
  2. 読み出し側 (`load_calibration_from_first_analysis`) は
     `analysis_params.json` から **固定 5 キーの dict を組み直してから**
     `cal_data.get("requested_degree")` / `.get("ref_mz_min")` を引く

つまり参照するキーが dict に存在し得ず、警告分岐は **到達不能**。
同梱リファレンスは m/z 136-379 しか無いのに補正対象は 1000 超まであるため、
「外挿しています」は出さなければならない情報のはずだった。

★ ここで固定するのは「保存した情報が復元経路まで通っていること」。
  UI 文言ではなく **データが欠落しないこと** を見る。
"""

import json

import pytest


# ---------------------------------------------------------------------------
# 1. 保存側 — analysis_params.json に 3 キーが載ること
# ---------------------------------------------------------------------------

class TestParamsAreWritten:
    """★ 保存されないものは復元しようがない。

    コールバック本体は巨大で単体起動できないため、`analysis_params.json` を
    組み立てている箇所をソースから確認する。行数に依存しないよう
    「保存 dict にキー名が現れるか」で見る。
    """

    @staticmethod
    def _source():
        from pathlib import Path
        return (Path(__file__).resolve().parent.parent
                / "app" / "callbacks" / "analysis_callbacks.py").read_text(
                    encoding="utf-8")

    @pytest.mark.parametrize("key", [
        "calibration_requested_degree",
        "calibration_ref_mz_min",
        "calibration_ref_mz_max",
    ])
    def test_key_is_persisted(self, key):
        assert f'"{key}"' in self._source(), (
            f"{key} を analysis_params.json に保存していない。"
            "外挿警告・次数クランプの表示が復元経路で失われる")


# ---------------------------------------------------------------------------
# 2. 読み出し側 — 保存した値が cal_data に載ること
# ---------------------------------------------------------------------------

def _params_json(tmp_path, **extra):
    """外挿になる典型 (参照 m/z 136-379、指定 poly3 → 実効 1 次)。"""
    payload = {
        "calibration_enable": True,
        "calibration_coefficients": [1e-6, -2e-4],
        "calibration_degree": 1,
        "calibration_r_squared": 0.98,
        "calibration_n_points": 4,
        "calibration_regression_mode": "poly3",
        "calibration_requested_degree": 3,
        "calibration_ref_mz_min": 136.0619,
        "calibration_ref_mz_max": 379.0925,
        "calibration_table": [],
    }
    payload.update(extra)
    (tmp_path / "analysis_params.json").write_text(
        json.dumps(payload), encoding="utf-8")
    return payload


class TestRestoredPayload:
    """★ 復元された `reanalysis_calibration_data` に 3 キーが載ること。

    ここが本丸。ver51.8 では 5 キー固定の dict を組んでいたので、
    `analysis_params.json` に書いてあっても **落ちていた**。
    """

    @staticmethod
    def _load(rds_folder):
        pytest.importorskip("dash")
        from app.callbacks.analysis_callbacks import (
            load_calibration_from_first_analysis)
        return load_calibration_from_first_analysis(str(rds_folder))

    def test_requested_degree_survives(self, tmp_path):
        _params_json(tmp_path)
        cal_data = self._load(tmp_path)[0]
        assert cal_data.get("requested_degree") == 3, (
            "指定次数が復元されない。利用者は poly3 を選んだのに 1 次で"
            "当てられていることを知る手段が無い")

    def test_reference_range_survives(self, tmp_path):
        _params_json(tmp_path)
        cal_data = self._load(tmp_path)[0]
        assert cal_data.get("ref_mz_min") == pytest.approx(136.0619)
        assert cal_data.get("ref_mz_max") == pytest.approx(379.0925)

    def test_extrapolation_warning_is_rendered(self, tmp_path):
        """★ 警告が実際に画面へ出ること（到達不能だった分岐）。"""
        _params_json(tmp_path)
        children = self._load(tmp_path)[1]
        text = _flatten_text(children)
        assert "外挿" in text, f"外挿警告が出ていない: {text!r}"
        assert "136" in text and "379" in text, \
            f"参照ピークの範囲が示されていない: {text!r}"

    def test_degree_downgrade_is_disclosed(self, tmp_path):
        """指定次数と実効次数が食い違うなら、その旨を書くこと。"""
        _params_json(tmp_path)
        text = _flatten_text(self._load(tmp_path)[1])
        assert "3" in text and "1" in text and "下げた" in text, \
            f"次数を下げたことが書かれていない: {text!r}"

    def test_no_warning_when_range_unknown(self, tmp_path):
        """★ 過剰修正の番人: 範囲が分からない旧データで嘘を書かない。

        ver51.8 以前に作られた analysis_params.json には 3 キーが無い。
        そこで既定値をでっち上げると、今度は **無い情報を出す** 側の間違いになる。
        """
        _params_json(tmp_path)
        payload = json.loads(
            (tmp_path / "analysis_params.json").read_text(encoding="utf-8"))
        for k in ("calibration_requested_degree",
                  "calibration_ref_mz_min", "calibration_ref_mz_max"):
            payload.pop(k)
        (tmp_path / "analysis_params.json").write_text(
            json.dumps(payload), encoding="utf-8")

        text = _flatten_text(self._load(tmp_path)[1])
        assert "外挿" not in text, f"範囲不明なのに外挿警告を出している: {text!r}"
        assert "下げた" not in text, f"次数不明なのに断定している: {text!r}"


def _flatten_text(node) -> str:
    """Dash コンポーネント木から表示テキストを集める。"""
    if node is None:
        return ""
    if isinstance(node, (str, int, float)):
        return str(node)
    if isinstance(node, (list, tuple)):
        return " ".join(_flatten_text(n) for n in node)
    children = getattr(node, "children", None)
    return _flatten_text(children)
