"""コールバックの書き込み先が **実際のレイアウトに存在する**ことの全数番人。

★ ver56.4 / デバッグ総点検 §3.3 で確定した不具合:

  `auth_callbacks.py` のブラウザ側コールバックが、ver52.3 で画面から削除された
  `header_analyst_label_shared` に書き込もうとしていた。Dash は
  `suppress_callback_exceptions=True` のため起動時に落ちず、
  **実行時に処理全体が停止**する。その結果:

    header_analyst_label_landing  : 存在するのに中身は空文字 ""
    header_analyst_label_analysis : 存在するのに中身は空文字 ""
    header_analyst_label_shared   : そもそも存在しない

  つまり **1 か所の死んだ書き込み先のせいで、生きている 2 か所にも
  解析者名が入らない**。ログインしても「解析者: 〇〇」が常に空欄で、
  誰として作業しているのか画面から分からない状態が続いていた。

■ なぜ既存の番人で捕まらなかったか

`test_declared_targets_exist.py` は「宣言した対象が実在するか」を 4 層で見るが、
対象は R 注入・patch anchor・PARAM_BOUNDS・証跡 glob であり、
**Dash コールバックの Output そのもの**は対象外だった。
`test_callback_wiring.py` は引数の個数と返り値の個数を見るが、
id が実在するかは見ない。ここが穴だった。

同じ型は ver42.1 でも起きている（削除済み id が 1 つ登録表に残っていたため
「参照…」ボタン **35 個が一斉に無反応**になった）。再発コストが高い型なので、
**サーバ側・ブラウザ側の両方**を実レイアウトと突き合わせる。

■ 判定方法

`create_main_layout()` を実際に構築して全 id を収集し、
`app.callback` / `clientside_callback` が参照する文字列 id と突き合わせる。
コールバック内で動的に生成される部品（全画面モーダルの中身、Lite ビュー、
サンプル選択など）はレイアウトに現れないため、**コード中の `id=` 指定を
機械的に集めて実在しうる id とみなす**。手書きの除外リストは持たない
（参照されなくなった幽霊 id が残り、本当に消えた id を見逃す穴になるため）。
"""
import ast
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parent.parent / "app"

# --- 動的生成 id の扱い -------------------------------------------------------
# 全画面モーダル・Lite ビュー・サンプル選択などは callback が実行時に組み立てるため、
# 起動時のレイアウトには現れない。これらを手書きの除外リストで管理すると、
# 参照されなくなった id が残って「本当に消えた id」を見逃す穴になる
# (ver42.1 の 35 ボタン全滅は、まさに登録表に残った幽霊 id が原因だった)。
#
# そこで除外リストは持たず、**コード中で `id=` 指定されている文字列を機械的に集めて
# 「実在しうる id」とみなす**。どこにも生成コードが無い id だけが検出される。
_ID_KWARGS = ("id", "label_id", "panel_id", "browse_id", "input_id", "store_id")


def _collect_layout_ids(component, out):
    """レイアウト木を再帰的に辿り、文字列 id を集める。"""
    cid = getattr(component, "id", None)
    if isinstance(cid, str):
        out.add(cid)
    children = getattr(component, "children", None)
    if children is None:
        return
    if isinstance(children, (list, tuple)):
        for ch in children:
            _collect_layout_ids(ch, out)
    else:
        _collect_layout_ids(children, out)


@pytest.fixture(scope="module")
def layout_ids():
    """実際に組み立てたレイアウトの全 id。"""
    import os
    os.environ.setdefault("FLASK_SECRET_KEY", "test-secret-key")
    os.environ.setdefault("MASTER_PASSWORD", "test-master")
    os.environ.setdefault("INITIAL_PASSWORD_B", "test-b")
    from app.layouts.main_layout import create_main_layout
    ids = set()
    _collect_layout_ids(create_main_layout(), ids)
    assert len(ids) > 300, f"レイアウト id の収集に失敗している (only {len(ids)})"
    return ids


def _constructed_ids():
    """app/ 配下のコードで `id=...` として組み立てられる文字列 id をすべて集める。

    レイアウト定義だけでなく **callback 内で動的に生成される部品**も拾えるので、
    「起動時レイアウトには無いが、操作すると現れる」正当な id を自動で許容できる。
    位置引数でヘルパーへ渡される id (`_path_input_row("desi_v8_script_path", ...)`)
    は、そのヘルパーが最終的に `id=` へ渡すためここで捕捉される。
    """
    ids = set()
    for path in sorted(APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if kw.arg in _ID_KWARGS and isinstance(kw.value, ast.Constant) \
                        and isinstance(kw.value.value, str):
                    ids.add(kw.value.value)
    return ids


@pytest.fixture(scope="module")
def known_ids(layout_ids):
    """実在しうる id = 起動時レイアウト ∪ コード中で生成される id。"""
    return layout_ids | _constructed_ids()


def _iter_callback_targets():
    """(ファイル, 行, 種別, id, prop) を全 callback から列挙する。

    対象は `Output(...)` / `Input(...)` / `State(...)` の第 1 引数が
    文字列リテラルのもの（パターンマッチング dict は対象外）。
    """
    for path in sorted(APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if fname not in ("Output", "Input", "State"):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if not (isinstance(first, ast.Constant) and isinstance(first.value, str)):
                continue
            prop = None
            if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
                prop = node.args[1].value
            yield (path.relative_to(APP.parent), node.lineno, fname,
                   first.value, prop)


class TestCallbackTargetsExist:

    def test_every_output_id_exists_in_the_layout(self, known_ids):
        """★ 本丸: 書き込み先 (Output) が実在すること。

        存在しない Output が 1 つでもあると、その callback は実行時に
        丸ごと停止し、**同じ callback の生きている出力も更新されなくなる**。
        """
        missing = []
        for rel, lineno, kind, cid, prop in _iter_callback_targets():
            if kind != "Output":
                continue
            if cid in known_ids:
                continue
            missing.append(f"{rel}:{lineno}  Output({cid!r}, {prop!r})")

        assert not missing, (
            "レイアウトに存在しない部品へ書き込もうとしている。\n"
            "Dash は suppress_callback_exceptions=True のため起動時には落ちず、"
            "**実行時にその callback 全体が停止**して、同じ callback の\n"
            "生きている出力まで更新されなくなる (ver52.3 の解析者ラベルがこれ):\n  "
            + "\n  ".join(missing))

    def test_every_input_and_state_id_exists_in_the_layout(self, known_ids):
        """読み取り元 (Input/State) も実在すること。

        こちらは「まだ生成されていないだけ」の動的部品が正当に存在するため、
        コード中の `id=` 指定を集めた集合で許容する。
        どこにも生成コードが無い id だけを失敗させる。
        """
        missing = []
        for rel, lineno, kind, cid, prop in _iter_callback_targets():
            if kind == "Output":
                continue
            if cid in known_ids:
                continue
            missing.append(f"{rel}:{lineno}  {kind}({cid!r}, {prop!r})")

        assert not missing, (
            "どこにも生成されない部品から読み取ろうとしている"
            "(レイアウトにも、callback の動的生成にも id が無い):\n  "
            + "\n  ".join(missing))

    def test_dynamic_id_collection_is_working(self, layout_ids):
        """★ 動的 id の自動収集が機能していること。

        収集が壊れると「全部実在する」と誤判定して番人が空回りする。
        全画面モーダルの部品は起動時レイアウトには無く、callback 内で
        生成されるので、その差が取れていることを確認する。
        """
        constructed = _constructed_ids()
        assert "fs_umap_exclude_cluster" in constructed, (
            "callback 内で生成される id を収集できていない")
        assert "fs_umap_exclude_cluster" not in layout_ids, (
            "この id は起動時レイアウトには現れないはず"
            "(現れるなら本テストの前提が変わったので見直すこと)")
        assert len(constructed) > 500, f"収集数が少なすぎる ({len(constructed)})"

    def test_the_guard_actually_reports_something(self, layout_ids):
        """★ 番人が空回りしていないことの確認 (inertness guard)。"""
        outputs = [1 for _, _, k, _, _ in _iter_callback_targets() if k == "Output"]
        assert len(outputs) > 400, (
            f"Output の収集数が少なすぎる ({len(outputs)})。"
            "収集ロジックが壊れている可能性がある。")


class TestAnalystLabelRegression:
    """★ ver52.3 で消えた解析者ラベルの再発防止 (§3.3)。"""

    def test_analyst_label_callback_targets_live_components(self, layout_ids):
        """解析者名を配る callback の出力が全て実在すること。"""
        targets = [
            (rel, lineno, cid)
            for rel, lineno, kind, cid, _ in _iter_callback_targets()
            if kind == "Output" and cid.startswith("header_analyst_label")
        ]
        assert targets, "解析者ラベルへの Output が 1 つも見つからない"
        dead = [f"{rel}:{lineno} {cid}" for rel, lineno, cid in targets
                if cid not in layout_ids]
        assert not dead, (
            "解析者ラベルの書き込み先が実在しない。"
            "ログイン後もヘッダーが空欄になる:\n  " + "\n  ".join(dead))

    def test_clientside_js_returns_match_output_count(self):
        """ブラウザ側 JS の返り値の個数が Output 数と一致すること。

        Output を減らしたのに JS が 3 要素を返し続ける、という
        取り違えを防ぐ (今回の修正でまさに起こりうる)。
        """
        src = (APP / "callbacks" / "auth_callbacks.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        checked = 0
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "clientside_callback"):
                continue
            js = node.args[0].value if (node.args and isinstance(node.args[0], ast.Constant)) else ""
            if "header_analyst_label" not in src[node.lineno - 1: node.end_lineno + 200]:
                pass
            n_out = sum(
                1 for a in node.args[1:]
                if isinstance(a, ast.Call)
                and getattr(a.func, "id", None) == "Output"
            )
            if n_out == 0:
                continue
            # JS 内の `return [...]` の要素数を素朴に数える
            import re
            for m in re.finditer(r"return\s*\[([^\]]*)\]", js):
                items = [x for x in m.group(1).split(",") if x.strip()]
                assert len(items) == n_out, (
                    f"auth_callbacks.py:{node.lineno} の clientside は "
                    f"Output {n_out} 個に対し JS が {len(items)} 要素を返している: "
                    f"{m.group(0)[:80]}")
                checked += 1
        assert checked > 0, "clientside の返り値検査が 1 件も走っていない"
