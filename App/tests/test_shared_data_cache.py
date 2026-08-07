"""共有データキャッシュの上限と、上限を入れたことで生じる落とし穴 (ver51.8)。

■ 背景

`share_callbacks._shared_data` は上限も TTL も無い**無制限**の辞書だった。
兄弟の `_project_states` / `_export_figures` には LRU + TTL があるのにここだけ無く、
しかも ver51.7 でキーへ RDS の mtime を足したため、**再解析のたびに新しいエントリが
増え続ける**状態だった（1 エントリは plot_data 全体で数十〜数百 MB）。

■ ★ 上限を入れたこと自体が新しいリスクを作る

無制限だった頃は「一度入れたものは消えない」ので
`if key not in _shared_data: ... ; data = _shared_data[key]` が安全だった。
LRU にすると **その隙間に evict されうる**（間に RDS 抽出という重い処理が入る）。
`_lite_bundle` を後から書き込む箇所も同じ。ここを添字アクセスのままにすると
KeyError になる。ローカル参照を持つ形に直した。
"""

import pytest

from app.callbacks import share_callbacks as SC


@pytest.fixture(autouse=True)
def _clean_cache():
    SC._shared_data.clear()
    SC._shared_data_atime.clear()
    yield
    SC._shared_data.clear()
    SC._shared_data_atime.clear()


class TestBounded:
    def test_entries_are_capped(self, monkeypatch):
        """★ 無制限に増えないこと（本来の目的）。"""
        monkeypatch.setattr(SC, "_SHARED_DATA_MAX", 3)
        for i in range(10):
            SC._shared_data_put(f"k{i}", {"n": i})
        assert len(SC._shared_data) == 3

    def test_oldest_is_dropped_first(self, monkeypatch):
        monkeypatch.setattr(SC, "_SHARED_DATA_MAX", 3)
        for i in range(4):
            SC._shared_data_put(f"k{i}", {"n": i})
        assert "k0" not in SC._shared_data
        assert set(SC._shared_data) == {"k1", "k2", "k3"}

    def test_reading_refreshes_lru_order(self, monkeypatch):
        """★ 使っているエントリが先に捨てられないこと。

        _shared_data_get を通さず `.get()` で読むと順序が更新されず、
        現に閲覧中のプロジェクトが evict されうる。
        """
        monkeypatch.setattr(SC, "_SHARED_DATA_MAX", 3)
        for i in range(3):
            SC._shared_data_put(f"k{i}", {"n": i})
        SC._shared_data_get("k0")          # k0 を使う
        SC._shared_data_put("k3", {"n": 3})
        assert "k0" in SC._shared_data, "使ったばかりの k0 が捨てられた"
        assert "k1" not in SC._shared_data

    def test_ttl_expires_idle_entries(self, monkeypatch):
        monkeypatch.setattr(SC, "_SHARED_DATA_TTL_SEC", 0.0)
        SC._shared_data_put("old", {"n": 0})
        SC._shared_data_put("new", {"n": 1})
        assert "old" not in SC._shared_data
        assert "new" in SC._shared_data

    def test_get_returns_none_for_missing(self):
        assert SC._shared_data_get("nope") is None


class TestNoDirectIndexing:
    """★ 添字アクセスが残っていないこと（KeyError の作り込み防止）。

    上限を入れた以上、`_shared_data[key]` は「まだ在る」前提を置くことになる。
    実コードでは必ずヘルパー経由か、put した dict のローカル参照を使う。
    """

    def test_lite_view_uses_helpers_only(self):
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent
               / "app" / "callbacks" / "lite_view_callbacks.py").read_text(
                   encoding="utf-8")
        tree = ast.parse(src)
        bad = [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Subscript)
            and isinstance(n.value, ast.Name) and n.value.id == "_shared_data"
        ]
        assert not bad, (
            f"_shared_data への添字アクセスが残っている (行 {bad})。"
            "LRU で evict されると KeyError になる")


class TestMutationThroughLocalReference:
    """put した dict をローカルで書き換えると、キャッシュ側にも反映されること。

    `_lite_bundle` の後付けはこの性質に依存している。
    """

    def test_local_mutation_is_visible_in_cache(self):
        d = {"plot_data": "X"}
        SC._shared_data_put("k", d)
        d["_lite_bundle"] = {"df_plot": "Y"}
        assert SC._shared_data_get("k")["_lite_bundle"] == {"df_plot": "Y"}

    def test_mutation_after_eviction_does_not_raise(self, monkeypatch):
        """★ evict された後にローカル参照へ書いても落ちないこと。"""
        monkeypatch.setattr(SC, "_SHARED_DATA_MAX", 1)
        d = {"plot_data": "X"}
        SC._shared_data_put("k", d)
        SC._shared_data_put("other", {"plot_data": "Z"})   # k を追い出す
        assert SC._shared_data_get("k") is None
        d["_lite_bundle"] = {"df_plot": "Y"}               # 例外にならない
        assert d["_lite_bundle"] == {"df_plot": "Y"}
