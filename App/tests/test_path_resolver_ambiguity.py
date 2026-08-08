"""パス補正が別プロジェクトを指さないこと (ver51.9 / C-5)。

■ 何が起きていたか

`resolve_data_path` は壊れた絶対パスの**末尾フォルダ名だけ**を取り、
候補ルート配下を `rglob` して **最初に見つかったもの**へ貼り替えていた。

  - `rglob` の順序は環境依存（同じ入力でもマシンが変われば別の答え）
  - `Data` / `output` / `RDS_Files` のような一般名は
    **どのプロジェクトにもある**
  - `_MAX_SCAN_DEPTH` は宣言だけで使われていない

結果、別プロジェクトのフォルダを指したまま解析やエクスポートが走る。
パスは補正済みとして表示されるので、利用者は気づけない。

★ 直し方は「一意に定まるときだけ採る」。壊れたパスの**末尾から何段一致するか**
  で採点し、最良が 1 つに決まらなければ補正しない（＝未解決として警告する）。
  DEG の手法スコープ (ver51.9 A-2) と同じ方針。
"""

import pytest

from app.services import path_resolver as PR


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """同名フォルダを持つ 2 つのプロジェクトを作る。"""
    root = tmp_path / "candidates"
    (root / "ProjectA" / "Data").mkdir(parents=True)
    (root / "ProjectB" / "Data").mkdir(parents=True)
    (root / "ProjectA" / "Data" / "marker_a.txt").write_text("a", encoding="utf-8")
    (root / "ProjectB" / "Data" / "marker_b.txt").write_text("b", encoding="utf-8")

    monkeypatch.setattr(PR, "DESI_DATA_CANDIDATES", [root])
    monkeypatch.setattr(PR, "TIMS_DATA_CANDIDATES", [root])
    monkeypatch.setattr(PR, "OUTPUT_DATA_CANDIDATES", [root])
    return root


class TestAmbiguousTailIsRefused:
    def test_generic_name_with_two_matches_is_not_resolved(self, roots):
        """★ どちらか 1 つを黙って選ばないこと。

        末尾が `Data` だけでは A と B の区別が付かない。
        """
        got = PR.resolve_data_path(r"D:\Dropbox\Whatever\Data", modality="desi")
        assert got is None, (
            f"曖昧なのに 1 つ選んでいる: {got}。別プロジェクトのデータを"
            "指したまま解析やエクスポートが走る")

    def test_two_level_tail_disambiguates(self, roots):
        """★ 2 段まで一致すれば一意に決まる → 補正してよい。"""
        got = PR.resolve_data_path(
            r"D:\Dropbox\Old\ProjectB\Data", modality="desi")
        assert got is not None, "一意に決まるのに補正できていない"
        assert (got / "marker_b.txt").exists(), got

    def test_unique_name_still_resolves(self, tmp_path, monkeypatch):
        """★ 過剰修正の番人: 候補が 1 つなら従来どおり補正する。"""
        root = tmp_path / "c"
        (root / "OnlyHere" / "UniqueFolder").mkdir(parents=True)
        monkeypatch.setattr(PR, "DESI_DATA_CANDIDATES", [root])
        monkeypatch.setattr(PR, "TIMS_DATA_CANDIDATES", [root])
        monkeypatch.setattr(PR, "OUTPUT_DATA_CANDIDATES", [root])

        got = PR.resolve_data_path(
            r"D:\Dropbox\X\UniqueFolder", modality="desi")
        assert got is not None and got.name == "UniqueFolder", got

    def test_existing_path_is_returned_untouched(self, tmp_path):
        """過剰修正の番人: 生きているパスは触らない。"""
        p = tmp_path / "alive"
        p.mkdir()
        assert PR.resolve_data_path(str(p)) == p

    def test_missing_everywhere_returns_none(self, roots):
        assert PR.resolve_data_path(
            r"D:\Nope\DoesNotExistAnywhere", modality="desi") is None


class TestFileResolution:
    def test_ambiguous_file_is_refused(self, tmp_path, monkeypatch):
        root = tmp_path / "c"
        for proj in ("A", "B"):
            d = root / proj
            d.mkdir(parents=True)
            (d / "annotation.csv").write_text("x", encoding="utf-8")
        monkeypatch.setattr(PR, "DESI_DATA_CANDIDATES", [root])
        monkeypatch.setattr(PR, "TIMS_DATA_CANDIDATES", [root])
        monkeypatch.setattr(PR, "OUTPUT_DATA_CANDIDATES", [root])

        got = PR.resolve_data_path(
            r"D:\X\annotation.csv", modality="desi", is_file=True)
        assert got is None, f"曖昧なファイルを 1 つ選んでいる: {got}"

    def test_unique_file_resolves(self, tmp_path, monkeypatch):
        root = tmp_path / "c"
        (root / "A").mkdir(parents=True)
        (root / "A" / "unique_db.csv").write_text("x", encoding="utf-8")
        monkeypatch.setattr(PR, "DESI_DATA_CANDIDATES", [root])
        monkeypatch.setattr(PR, "TIMS_DATA_CANDIDATES", [root])
        monkeypatch.setattr(PR, "OUTPUT_DATA_CANDIDATES", [root])

        got = PR.resolve_data_path(
            r"D:\X\unique_db.csv", modality="desi", is_file=True)
        assert got is not None and got.name == "unique_db.csv", got


class TestScanDepthIsEnforced:
    """★ `_MAX_SCAN_DEPTH` が宣言だけで使われていなかった。

    宣言された上限が効いていないと、巨大な共有ドライブで
    「補正のたびに全走査」になる。
    """

    def test_deeper_than_the_limit_is_not_matched(self, tmp_path, monkeypatch):
        root = tmp_path / "c"
        deep = root
        for i in range(PR._MAX_SCAN_DEPTH + 3):
            deep = deep / f"lvl{i}"
        (deep / "TooDeep").mkdir(parents=True)
        monkeypatch.setattr(PR, "DESI_DATA_CANDIDATES", [root])
        monkeypatch.setattr(PR, "TIMS_DATA_CANDIDATES", [root])
        monkeypatch.setattr(PR, "OUTPUT_DATA_CANDIDATES", [root])

        assert PR.resolve_data_path(
            r"D:\X\TooDeep", modality="desi") is None

    def test_within_the_limit_is_matched(self, tmp_path, monkeypatch):
        root = tmp_path / "c"
        (root / "a" / "b" / "ShallowEnough").mkdir(parents=True)
        monkeypatch.setattr(PR, "DESI_DATA_CANDIDATES", [root])
        monkeypatch.setattr(PR, "TIMS_DATA_CANDIDATES", [root])
        monkeypatch.setattr(PR, "OUTPUT_DATA_CANDIDATES", [root])

        got = PR.resolve_data_path(r"D:\X\ShallowEnough", modality="desi")
        assert got is not None, got
