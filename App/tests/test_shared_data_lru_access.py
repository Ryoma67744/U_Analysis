"""共有データ LRU が「閲覧中」を認識すること (ver51.9)。

■ 何が起きていたか

ver51.8 で `_shared_data` を無制限 dict から LRU + TTL に変えた
(1 エントリが数十〜数百 MB、本番は 1 プロセスなので再起動まで解放されない)。
そのとき `_shared_data_put` / `_shared_data_get` を用意したが、
**共有ビューの読み出しは素の `_shared_data.get(token)` のまま**だった。

`OrderedDict.get()` は順序を動かさないので、**閲覧中の共有セッションが
「ずっと未使用」に見える**。新しい共有リンクが 8 個開かれた時点で、
今まさに見ている人のデータが真っ先に捨てられ、画面が空になる。

★ 「LRU の順序を更新しない読み出しが 1 つでも残っていないか」を AST で見る。
  ver51.8 の取りこぼしは「ヘルパを作ったが呼び替えを全部やらなかった」形なので、
  既知の箇所を列挙するホワイトリストでは同じ取りこぼしが再発する。
"""

import ast
from pathlib import Path

import pytest

# ヘルパ自身は `_shared_data` を直接触ってよい（そこが実装だから）
_HELPER_FUNCS = {"_shared_data_put", "_shared_data_get"}

_CB_DIR = Path(__file__).resolve().parent.parent / "app" / "callbacks"
_MODULES = ["share_callbacks.py", "lite_view_callbacks.py"]


def _direct_accesses(path: Path):
    """`_shared_data` への「順序を動かさない」直接アクセスを拾う。

    見るのは
      - `_shared_data.get(...)` / `.pop(...)` などの読み出しメソッド
      - `_shared_data[...]` の添字アクセス
    `len()` や `in` は順序に影響しないので対象外。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))

    # ヘルパ関数の本体は除外する
    helper_nodes = [n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name in _HELPER_FUNCS]
    helper_lines = set()
    for h in helper_nodes:
        helper_lines.update(range(h.lineno, (h.end_lineno or h.lineno) + 1))

    out = []
    for node in ast.walk(tree):
        if getattr(node, "lineno", None) in helper_lines:
            continue
        # _shared_data.get(...) など
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_shared_data"
                and node.func.attr in ("get", "pop", "setdefault")):
            out.append(f"{path.name}:{node.lineno} _shared_data.{node.func.attr}()")
        # _shared_data[token]
        elif (isinstance(node, ast.Subscript)
              and isinstance(node.value, ast.Name)
              and node.value.id == "_shared_data"):
            out.append(f"{path.name}:{node.lineno} _shared_data[...]")
    return out


class TestNoUnorderedReads:
    def test_every_read_goes_through_the_helper(self):
        """★ 素の `.get()` / 添字読みが残っていないこと。"""
        offenders = []
        for name in _MODULES:
            offenders += _direct_accesses(_CB_DIR / name)
        assert not offenders, (
            "LRU の順序を更新しない `_shared_data` アクセスが残っている。"
            "閲覧中の共有セッションが『未使用』扱いで真っ先に捨てられる:\n  "
            + "\n  ".join(offenders))


class TestGetActuallyRefreshes:
    """ヘルパ側の実装が本当に順序を動かすこと（番人の前提）。"""

    @pytest.fixture(autouse=True)
    def _clean(self):
        from app.callbacks import share_callbacks as SC
        SC._shared_data.clear()
        SC._shared_data_atime.clear()
        yield
        SC._shared_data.clear()
        SC._shared_data_atime.clear()

    def test_reading_moves_the_entry_to_the_end(self):
        from app.callbacks import share_callbacks as SC

        SC._shared_data_put("a", {"v": 1})
        SC._shared_data_put("b", {"v": 2})
        assert list(SC._shared_data) == ["a", "b"]

        SC._shared_data_get("a")
        assert list(SC._shared_data) == ["b", "a"], \
            "読み出しで LRU の順序が動いていない"

    def test_viewed_session_survives_pressure(self, monkeypatch):
        """★ 本番で起きる形: 閲覧中のトークンが押し出されないこと。"""
        from app.callbacks import share_callbacks as SC
        monkeypatch.setattr(SC, "_SHARED_DATA_MAX", 3)

        SC._shared_data_put("viewing", {"v": 0})
        for i in range(6):
            SC._shared_data_get("viewing")      # 閲覧し続けている
            SC._shared_data_put(f"other{i}", {"v": i})

        assert SC._shared_data_get("viewing") is not None, \
            "閲覧中の共有セッションが LRU で捨てられた"

    def test_missing_key_returns_none(self):
        from app.callbacks import share_callbacks as SC
        assert SC._shared_data_get("nope") is None
