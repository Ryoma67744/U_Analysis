"""DEG の統合手法スコープ (ver51.8)。

■ 何が起きていたか

`load_deg_results(result_base, "RPCA")` の探索パターンに `Harmony/*` `RPCA/*`
`PCA/*` が **無条件で** 並んでいた。要求した手法の DEG ファイルが無いと
別手法の結果へ黙ってフォールバックし、しかも `_write_deg_index` で

    {"deg_results": {"RPCA": {"path": "Harmony/deg_markers.csv"}}}

と **ディスクに記録** していた。以後は高速パスがそれを先に読むので、
**間違いが固着し再起動しても消えない**。

Volcano / クラスタ Top5 / マーカー表 / PPTX / 共有・lite ビュー / GPT API が
すべてこの関数を通るため、「手法を比較しているつもりで同じ表を 3 つ見る」
状態になっていた。利用者が気づく手段は無い。

★ ここで固定するのは「要求した手法の結果以外を返さない」ことと
  「間違った対応をディスクに焼き付けない」ことの 2 点。
"""

import json

import pandas as pd
import pytest

from app.utils.deg_utils import load_deg_results


def _markers(tag):
    return pd.DataFrame({
        "gene": [f"{tag}_FEATURE"],
        "cluster": ["0"],
        "avg_log2FC": [2.0],
        "p_val_adj": [0.001],
    })


def _write(base, rel, tag):
    p = base / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    _markers(tag).to_csv(p, index=False)
    return p


def test_does_not_fall_back_to_another_method(tmp_path):
    """★ 要求した手法に DEG が無くても、別手法の結果を返さない。"""
    _write(tmp_path, "Harmony/deg_markers.csv", "HARMONY")
    (tmp_path / "RPCA").mkdir()          # フォルダはあるが DEG ファイルは無い

    assert load_deg_results(tmp_path, "RPCA") is None, \
        "別手法 (Harmony) の結果を返している"


def test_does_not_persist_a_cross_method_mapping(tmp_path):
    """★ 間違った対応を deg_index.json に焼き付けない。

    ここが本当に怖い部分。一度書かれると高速パスが先に読むため、
    以後は探索を直しても間違いが残り続ける。
    """
    _write(tmp_path, "Harmony/deg_markers.csv", "HARMONY")
    (tmp_path / "RPCA").mkdir()

    load_deg_results(tmp_path, "RPCA")

    idx = tmp_path / "deg_index.json"
    if idx.exists():
        meta = json.loads(idx.read_text(encoding="utf-8"))
        entry = (meta.get("deg_results") or {}).get("RPCA")
        assert entry is None or "Harmony" not in str(entry.get("path", "")), \
            f"別手法のパスが記録された: {entry}"


def test_returns_its_own_method_when_present(tmp_path):
    """正常系: 自分の手法のファイルはちゃんと読む（過剰な締め付けの検出）。"""
    _write(tmp_path, "Harmony/deg_markers.csv", "HARMONY")
    _write(tmp_path, "RPCA/deg_markers.csv", "RPCA")

    got = load_deg_results(tmp_path, "RPCA")
    assert got and got[0]["gene"] == "RPCA_FEATURE"


def test_each_method_gets_its_own_table(tmp_path):
    """★ 手法ごとに違う表が返ること（PPTX が 3 手法を並べる経路）。"""
    _write(tmp_path, "Harmony/deg_markers.csv", "HARMONY")
    _write(tmp_path, "RPCA/deg_markers.csv", "RPCA")
    _write(tmp_path, "PCA/deg_markers.csv", "PCA")

    seen = {}
    for m in ("Harmony", "RPCA", "PCA"):
        res = load_deg_results(tmp_path, m)
        assert res, f"{m} が読めていない"
        seen[m] = res[0]["gene"]
    assert len(set(seen.values())) == 3, f"手法間で同じ表が返っている: {seen}"


def test_root_level_output_is_still_accepted(tmp_path):
    """手法フォルダを作らない単一手法の出力は従来どおり読める。

    ここまで締めると「手法フォルダが無い正常な結果」まで読めなくなるので、
    直下のファイルは許容し続ける（過剰修正の番人）。
    """
    _write(tmp_path, "markers_annotated.csv", "ROOT")
    got = load_deg_results(tmp_path, "RPCA")
    assert got and got[0]["gene"] == "ROOT_FEATURE"


def test_rglob_stage_also_scoped(tmp_path):
    """再帰探索段でも別手法フォルダを掴まないこと。

    TIMS は日付サブフォルダへ出すため rglob 段がある。ここが素通りだと
    第 1 段を締めても意味が無い。
    """
    _write(tmp_path, "20260807_run/Harmony/markers_annotated.csv", "HARMONY")
    (tmp_path / "20260807_run" / "RPCA").mkdir(parents=True)

    assert load_deg_results(tmp_path, "RPCA") is None, \
        "rglob 段で別手法を掴んでいる"


@pytest.mark.parametrize("method", ["Harmony", "RPCA", "PCA", "PCA (uncorrected)"])
def test_no_method_ever_sees_another_methods_file(tmp_path, method):
    """全手法について、自分のファイルが無ければ None（総当たり）。"""
    _write(tmp_path, "Harmony/deg_markers.csv", "HARMONY")
    _write(tmp_path, "RPCA/deg_markers.csv", "RPCA")
    _write(tmp_path, "PCA/deg_markers.csv", "PCA")
    _write(tmp_path, "pca_uncorrected/deg_markers.csv", "UNC")

    expected = {"Harmony": "HARMONY_FEATURE", "RPCA": "RPCA_FEATURE",
                "PCA": "PCA_FEATURE", "PCA (uncorrected)": "UNC_FEATURE"}[method]
    got = load_deg_results(tmp_path, method)
    assert got and got[0]["gene"] == expected, f"{method} -> {got and got[0]['gene']}"


# ---------------------------------------------------------------------------
# ver51.9: 手法名の表記ゆれと「手法不明」の扱い
# ---------------------------------------------------------------------------
# ★ ver51.8 で「別手法フォルダを候補から外す」ようにしたとき、**表記が一致しない
#   呼び出し側を壊した**。_METHOD_DIR_MAP は完全一致のみで、
#     - gpt_api は URL クエリをそのまま渡す (`?method=rpca` は小文字)
#     - interactive_calibration は `integration_method or ""` を渡す
#   どちらも method_dir="Harmony" に落ち、そのうえで RPCA フォルダが除外され、
#   **RPCA しか無いプロジェクトで DEG が見つからなくなった**。
#   (従来は無条件パターンで拾えていた = 別の意味で間違っていた)

class TestMethodNameNormalisation:
    @pytest.mark.parametrize("given", ["RPCA", "rpca", "RPca", " rpca "])
    def test_case_and_space_insensitive(self, tmp_path, given):
        """★ 表記ゆれがあっても正しい手法の表を返すこと。"""
        _write(tmp_path, "Harmony/deg_markers.csv", "HARMONY")
        _write(tmp_path, "RPCA/deg_markers.csv", "RPCA")
        got = load_deg_results(tmp_path, given)
        assert got and got[0]["gene"] == "RPCA_FEATURE", f"{given!r} -> {got}"

    def test_pca_uncorrected_alias_still_works(self, tmp_path):
        _write(tmp_path, "pca_uncorrected/deg_markers.csv", "UNC")
        got = load_deg_results(tmp_path, "pca (uncorrected)")
        assert got and got[0]["gene"] == "UNC_FEATURE"


class TestUnknownMethod:
    """手法を特定できないときの振る舞い。

    ★ 「黙って Harmony 扱い」にすると ver51.8 で潰した不具合に逆戻りする。
      かといって常に None だと、単一手法のプロジェクトで読めなくなる
      (これが ver51.8 の作り込み)。**一意に定まるときだけ**採る。
    """

    @pytest.mark.parametrize("given", ["", None, "unknown-method"])
    def test_unique_method_folder_is_accepted(self, tmp_path, given):
        """★ DEG を持つ手法フォルダが 1 つだけなら採用する。"""
        _write(tmp_path, "RPCA/deg_markers.csv", "RPCA")
        got = load_deg_results(tmp_path, given)
        assert got and got[0]["gene"] == "RPCA_FEATURE", f"{given!r} -> {got}"

    @pytest.mark.parametrize("given", ["", None, "unknown-method"])
    def test_ambiguous_method_folders_return_none(self, tmp_path, given):
        """★ 複数あるならどれか 1 つを黙って選ばない。"""
        _write(tmp_path, "Harmony/deg_markers.csv", "HARMONY")
        _write(tmp_path, "RPCA/deg_markers.csv", "RPCA")
        assert load_deg_results(tmp_path, given) is None, \
            "曖昧なのに 1 つ選んでいる"

    def test_unknown_method_does_not_poison_deg_index(self, tmp_path):
        """★ 手法名が分からない以上、対応をディスクに記録してはいけない。"""
        _write(tmp_path, "RPCA/deg_markers.csv", "RPCA")
        load_deg_results(tmp_path, "")
        idx = tmp_path / "deg_index.json"
        if idx.exists():
            meta = json.loads(idx.read_text(encoding="utf-8"))
            assert not (meta.get("deg_results") or {}), meta

    def test_root_level_still_preferred(self, tmp_path):
        """直下に結果があるなら手法フォルダを探しに行かない。"""
        _write(tmp_path, "markers_annotated.csv", "ROOT")
        _write(tmp_path, "RPCA/deg_markers.csv", "RPCA")
        got = load_deg_results(tmp_path, "")
        assert got and got[0]["gene"] == "ROOT_FEATURE"


class TestRdsRecursiveStageIsScoped:
    """★ 最後の RDS 再帰探索段にもスコープが効くこと。

    ver51.8 では CSV rglob 段と RDS glob 段だけを塞ぎ、**この経路が素通り**だった。
    RDS しか無いプロジェクト (TIMS ver13 系) では今も別手法の表が返っていた。
    """

    def test_rds_rglob_does_not_cross_methods(self, tmp_path, monkeypatch):
        import app.utils.deg_utils as DU

        (tmp_path / "Harmony").mkdir()
        (tmp_path / "Harmony" / "deg_FindAllMarkers_raw_1.rds").write_bytes(b"x")
        (tmp_path / "RPCA").mkdir()      # RPCA には RDS 無し

        monkeypatch.setattr(
            DU, "read_deg_rds",
            lambda p: [{"gene": "HARMONY_RDS", "cluster": "0",
                        "avg_log2FC": 1.0, "p_val_adj": 0.01}])

        assert load_deg_results(tmp_path, "RPCA") is None, \
            "RDS の再帰探索で別手法を掴んでいる"
