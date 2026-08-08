"""ラベル位置キャッシュが呼び出し側に汚されないこと (ver51.9 / C-1)。

■ 何が起きていたか

`_read_positions_json` は `(path, mtime, size)` をキーに JSON をキャッシュし、
**その dict をそのまま返す**。ところが描画側の
`interactive_umap._get_merged_label_positions` は

    saved_section = all_pos.get(section, {})
    _merge_label_positions(saved_section, acc_section)   # ← in-place
    all_pos[section] = saved_section

と **返ってきた dict と入れ子を破壊的に書き換える**。

本番は 1 プロセスなのでキャッシュはプロセス共有。結果:

  - 利用者 B が「まだドラッグ中／保存前」の位置をキャッシュへ焼き込む
  - 利用者 A の再描画と PPTX にその位置が混ざる
  - **ファイルは変わらないので mtime キーは有効なまま** → 汚染が消えない
    (再起動するまで残る)

ラベルは注釈テキストの位置なので、図が壊れて見えるわけではない。
「なんとなく位置がおかしい」で終わり、原因に辿り着く手段が無い。

★ キャッシュ境界で複製を返す。呼び出し側の in-place merge は残してよい
  （そちらを直すと 4 箇所の描画経路すべてを揃える必要があり、
  1 箇所でも漏らすと同じことが起きる）。
"""

import json

import pytest

import app.utils.label_persistence as LP


@pytest.fixture(autouse=True)
def _clear_cache():
    with LP._POSITIONS_CACHE_LOCK:
        LP._POSITIONS_CACHE.clear()
    yield
    with LP._POSITIONS_CACHE_LOCK:
        LP._POSITIONS_CACHE.clear()


PAYLOAD = {
    "umap_integrated": {"0": {"ax": 10, "ay": 20}},
    "spatial": {"S1": {"1": {"ax": 5, "ay": 5}}},
}


def _write(tmp_path):
    p = tmp_path / "label_positions.json"
    p.write_text(json.dumps(PAYLOAD), encoding="utf-8")
    return p


class TestCacheIsNotSharedByReference:
    def test_top_level_mutation_does_not_leak(self, tmp_path):
        """★ 返り値を書き換えても次の読み出しに影響しないこと。"""
        p = _write(tmp_path)
        first = LP._read_positions_json(p)
        first["umap_integrated"] = {"999": {"ax": -1, "ay": -1}}

        second = LP._read_positions_json(p)
        assert second["umap_integrated"] == {"0": {"ax": 10, "ay": 20}}, (
            f"キャッシュが汚染された: {second['umap_integrated']}")

    def test_nested_mutation_does_not_leak(self, tmp_path):
        """★ 本命。実際の汚染は**入れ子**の in-place merge で起きる。

        浅いコピーだけだと、ここが通らない。
        """
        p = _write(tmp_path)
        first = LP._read_positions_json(p)
        first["spatial"]["S1"]["1"]["ax"] = 12345
        first["spatial"]["S1"]["NEW"] = {"ax": 0, "ay": 0}

        second = LP._read_positions_json(p)
        assert second["spatial"]["S1"]["1"]["ax"] == 5, second["spatial"]
        assert "NEW" not in second["spatial"]["S1"], second["spatial"]

    def test_each_reader_gets_an_independent_object(self, tmp_path):
        p = _write(tmp_path)
        a = LP._read_positions_json(p)
        b = LP._read_positions_json(p)
        assert a is not b
        assert a["spatial"] is not b["spatial"]
        assert a == b


class TestRealDrawingPathDoesNotPollute:
    """★ 実際の描画経路 (`_get_merged_label_positions`) で再現する。

    ここが通らなければ、キャッシュだけ直しても意味が無い。
    """

    def test_second_user_does_not_see_the_first_users_drag(self, tmp_path):
        pytest.importorskip("dash")
        from app.callbacks.interactive_umap import _get_merged_label_positions

        rds = tmp_path / "seu.rds"
        rds.write_bytes(b"x")
        (tmp_path / "label_positions.json").write_text(
            json.dumps(PAYLOAD), encoding="utf-8")

        # 利用者 B: ドラッグ途中の位置（まだ保存していない）
        _get_merged_label_positions(
            {"umap_integrated": {"0": {"ax": -777, "ay": -777}}},
            rds_path=str(rds))

        # 利用者 A: 蓄積なしで描画 → ファイルの値が出るはず
        a_pos = _get_merged_label_positions({}, rds_path=str(rds))
        assert a_pos["umap_integrated"]["0"] == {"ax": 10, "ay": 20}, (
            "他セッションのドラッグ途中の位置が混ざっている: "
            f"{a_pos['umap_integrated']['0']}")

    def test_merging_still_works(self, tmp_path):
        """過剰修正の番人: 自分の蓄積はちゃんと反映されること。"""
        pytest.importorskip("dash")
        from app.callbacks.interactive_umap import _get_merged_label_positions

        rds = tmp_path / "seu.rds"
        rds.write_bytes(b"x")
        (tmp_path / "label_positions.json").write_text(
            json.dumps(PAYLOAD), encoding="utf-8")

        got = _get_merged_label_positions(
            {"umap_integrated": {"0": {"ax": -777, "ay": -777}}},
            rds_path=str(rds))
        assert got["umap_integrated"]["0"] == {"ax": -777, "ay": -777}, got


class TestCacheStillCaches:
    """過剰修正の番人: 毎回ディスクを読み直すようになっていないこと。

    このキャッシュは ver46.1 で「1 回の再描画ごとに複数回呼ばれる」ために
    入れたもの。複製のコストで台無しにしては本末転倒。
    """

    def test_file_is_read_once(self, tmp_path, monkeypatch):
        p = _write(tmp_path)
        reads = []
        real = type(p).read_text

        def _counting(self, *a, **k):
            reads.append(str(self))
            return real(self, *a, **k)

        monkeypatch.setattr(type(p), "read_text", _counting)
        for _ in range(5):
            LP._read_positions_json(p)
        assert len(reads) == 1, f"キャッシュが効いていない (読込 {len(reads)} 回)"
