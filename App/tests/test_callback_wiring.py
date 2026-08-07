"""@callback の結線が壊れていないことの静的検査 (ver51.6)。

■ なぜ要るか

ver51.6 で `lite_view_callbacks.py` にヘルパー関数を足したとき、**既存の
`@callback(...)` デコレータと、その直下にあった `route_lite_url` の間**に
関数を挿し込んでしまった。結果:

  - デコレータはヘルパー (`_lite_cache_key`, 引数 4 個) に付いた
    → Dash は Input 1 個ぶんしか渡さないので URL 変更のたびに TypeError
  - 本来のルーティング関数 `route_lite_url` は **無登録**になった
    → /lite/<project>/<sub> が一切動かない

単体 880 件も E2E 19 件も、これを 1 件も検出しなかった。コールバックの
**登録**そのものを見ているテストが無かったため。外部監査の静的解析が拾った。

■ 何を守るか

「デコレータが宣言している Input/State の数」と「関数が受け取る引数の数」は
必ず一致する。ズレていれば、デコレータが別の関数に付いたか、引数を足し引き
したのに片方を直し忘れたかのどちらか。どちらも実行時まで発覚しない。

AST で見るので Dash の起動も実データも要らない。
"""

import ast
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent / "app"

# デコレータのキーワードのうち、**関数の引数にならない** もの。
# 例: cancel=[Input("cancel_btn", "n_clicks")] は Input を含むが引数ではない。
_NON_ARG_KEYWORDS = {
    "prevent_initial_call", "background", "manager", "cancel", "running",
    "progress", "progress_default", "interval", "cache_args_to_ignore",
    "on_error", "config_prevent_initial_callbacks",
}


def _is_callback_decorator(node):
    """`@callback(...)` / `@app.callback(...)` / `@dash.callback(...)` か。"""
    if not isinstance(node, ast.Call):
        return False
    f = node.func
    if isinstance(f, ast.Name):
        return f.id == "callback"
    if isinstance(f, ast.Attribute):
        return f.attr == "callback"
    return False


def _count_deps(nodes):
    """与えたノード群に含まれる Input(...) / State(...) の数を数える。"""
    n = 0
    for node in nodes:
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id in ("Input", "State")):
                n += 1
    return n


def _expected_arg_count(dec):
    """デコレータが関数へ渡す引数の数。数えられないときは None。"""
    considered = list(dec.args)
    for kw in dec.keywords:
        if kw.arg in ("inputs", "state"):
            considered.append(kw.value)
        elif kw.arg is None:          # **kwargs 展開。静的には追えない
            return None
        elif kw.arg not in _NON_ARG_KEYWORDS and kw.arg != "output":
            return None               # 未知のキーワード。黙って通さない
    n = _count_deps(considered)

    # background=True かつ progress= 指定なら set_progress が先頭に注入される
    has_progress = any(kw.arg == "progress" for kw in dec.keywords)
    background = any(
        kw.arg == "background"
        and isinstance(kw.value, ast.Constant) and kw.value.value is True
        for kw in dec.keywords)
    if has_progress and background:
        n += 1
    return n


def _callbacks():
    """(ファイル, 関数名, 行, 期待引数数, 実引数数) を全部集める。"""
    out = []
    for path in sorted(APP_DIR.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:                     # ここで落ちるのも異常
            pytest.fail(f"{path} が構文エラー: {e}")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not _is_callback_decorator(dec):
                    continue
                expected = _expected_arg_count(dec)
                if expected is None:
                    continue
                a = node.args
                if a.vararg or a.kwarg:              # *args は数えられない
                    continue
                # Dash は宣言した数だけ位置引数で呼ぶ。既定値つきの引数は
                # 渡されなくてよい (ループ変数を閉じ込める `_pid=input_id` の
                # 形が repo 内にある)。よって「ちょうど一致」ではなく
                # **その数で呼べるか** を見る。
                total = len(a.posonlyargs) + len(a.args)
                required = total - len(a.defaults)
                out.append((path, node.name, node.lineno,
                            expected, required, total))
    return out


def test_some_callbacks_were_found():
    """検査対象が実在すること (パスを間違えて 0 件を通さないための番人)。"""
    found = _callbacks()
    assert len(found) > 200, f"コールバックが {len(found)} 件しか見つからない"


def test_every_callback_takes_exactly_its_declared_inputs():
    """★ 宣言した Input/State の数と関数の引数の数が一致すること。

    ズレる典型は「デコレータと関数の間に別の関数を挿し込んだ」。このとき
    デコレータは挿し込んだ側に付き、本来の関数は無登録のまま残る
    (実測: ver51.6 で route_lite_url が丸ごと無効化された)。
    """
    bad = [row for row in _callbacks()
           if not (row[4] <= row[3] <= row[5])]
    assert not bad, "宣言した数で呼べないコールバック:\n" + "\n".join(
        f"  {p.name}:{ln} {fn}() 宣言={exp} 引数={req}〜{tot} 個"
        for p, fn, ln, exp, req, tot in bad)


def _count_outputs(dec):
    """デコレータが宣言している Output の数。running=/cancel= の中は数えない。"""
    considered = list(dec.args)
    for kw in dec.keywords:
        if kw.arg == "output":
            considered.append(kw.value)
    n = 0
    for node in considered:
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == "Output"):
                n += 1
    return n


def _tuple_returns(fn):
    """関数内の「明示的にタプルを返している」return を (行, 要素数) で返す。

    ★ タプル以外 (`return _finish(...)` のような委譲や単一値) は数えない。
      呼び先が何個返すかは静的には分からないので、ここで数えると
      委譲を使っている箇所が軒並み誤検出になる。

    ★ 入れ子の関数定義には降りない。コールバックの中に定義された小さな
      ヘルパー (`def _finish(...)` など) の return は、そのヘルパーの
      返り値であってコールバックの返り値ではない。
    """
    out = []
    stack = list(ast.iter_child_nodes(fn))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue                      # 入れ子の関数はその関数のもの
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Tuple):
            elts = node.value.elts
            # `return (x, *_NO_UPDATE_11[1:])` のような展開は静的に数えられない
            if not any(isinstance(e, ast.Starred) for e in elts):
                out.append((node.lineno, len(elts)))
        stack.extend(ast.iter_child_nodes(node))
    return out


def _delegation_targets(fn, module_funcs):
    """`return helper(...)` の形で委譲している同一モジュール内の関数を返す。

    実際に起きた取りこぼしがこれ。コールバック本体は
    `return _update_feature_plot_inner(...)` の 1 行で、Output の数と
    食い違う return は **委譲先**にあった。
    """
    targets = []
    for node in ast.walk(fn):
        if not (isinstance(node, ast.Return) and isinstance(node.value, ast.Call)):
            continue
        f = node.value.func
        if isinstance(f, ast.Name) and f.id in module_funcs:
            targets.append(module_funcs[f.id])
    return targets


def test_every_callback_returns_as_many_values_as_it_declares():
    """★ 明示タプルで返している return が Output の数と一致すること。

    ver51.6 で Feature の見出し用 Output を 1 つ足したとき、早期 return の
    1 分岐だけ 3 値のまま残っていた (Output は 4 個)。その分岐が実際に走る
    条件 (名前変更 / フルスクリーン閉鎖 かつ Feature 未選択) を踏むテストが
    無かったので、単体 880 件でも E2E でも素通りした。

    委譲先も 1 段だけ追う。取りこぼしはまさにそこにあった。
    """
    bad = []
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        module_funcs = {n.name: n for n in ast.walk(tree)
                        if isinstance(n, ast.FunctionDef)}
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for dec in node.decorator_list:
                if not _is_callback_decorator(dec):
                    continue
                n_out = _count_outputs(dec)
                if n_out <= 1:        # 単一 Output はタプルを返しても合法
                    continue
                checked = [node] + _delegation_targets(node, module_funcs)
                for fn in checked:
                    for lineno, cnt in _tuple_returns(fn):
                        if cnt != n_out:
                            bad.append(
                                f"  {path.name}:{lineno} {node.name}() "
                                f"Output={n_out} だが {cnt} 値を返している"
                                + ("" if fn is node else f" (委譲先 {fn.name})"))
    assert not bad, "返り値の数が Output と合わない:\n" + "\n".join(bad)


def test_helper_did_not_steal_the_lite_routing_callback():
    """★ 実際に起きた壊れ方そのものを固定する。

    `route_lite_url` が登録され、`_lite_cache_key` は素のヘルパーのままである
    こと。上の一般テストでも捕まるが、壊れたときに原因が一目で分かるように
    個別にも置く。
    """
    src = (APP_DIR / "callbacks" / "lite_view_callbacks.py").read_text(
        encoding="utf-8")
    tree = ast.parse(src)
    decorated = {
        n.name for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef)
        and any(_is_callback_decorator(d) for d in n.decorator_list)
    }
    assert "route_lite_url" in decorated, "URL ルーティングが無登録になっている"
    assert "_lite_cache_key" not in decorated, \
        "ヘルパーにコールバックが付いている"
