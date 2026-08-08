"""アクティブプロジェクトの分離 (ver51.8)。

■ 何が起きていたか

`interactive_callbacks._active_key_var` は ContextVar で、コメントには
「ContextVar で per-request isolation を実現する」と書いてあった。**これは事実と違う。**

本番は waitress の **スレッドプール (既定 8 本)** で動き、リクエストを
`contextvars.Context.run()` で包まない。ContextVar はスレッドごとに 1 つの
コンテキストを持つだけなので、`set()` した値は **同じスレッドが次に処理する
無関係なリクエストへ残る**。

その結果:
  - `_set_active_key` を呼び忘れたコールバックが「前のリクエストが見ていた
    プロジェクト」のデータを読む
  - 解析条件・UMAP 表示設定が **別プロジェクトの interactive_settings.json** へ書かれる

★ 直し方は「リクエスト境界で捨てる」。呼び忘れは
  **別プロジェクトを読む → 何も読めない** に変わる。静かに間違うより安全。
"""

import concurrent.futures as cf

import pytest

from app.callbacks.interactive_callbacks import (
    _active_key_var,
    _set_active_key,
    reset_active_key,
)


class TestContextVarLeak:
    """ContextVar は「勝手に」リクエスト単位で切れたりしない、という前提の固定。"""

    def test_value_leaks_across_tasks_on_a_pooled_thread(self):
        """★ リセットが無ければ値が漏れることを示す（修正の前提）。

        これが漏れないなら reset は不要ということになる。waitress の
        ワーカースレッド 1 本を ThreadPoolExecutor(max_workers=1) で模す。
        """
        pool = cf.ThreadPoolExecutor(max_workers=1)
        try:
            pool.submit(lambda: _set_active_key("/rds/projectA.rds")).result()
            leaked = pool.submit(lambda: _active_key_var.get()).result()
        finally:
            pool.shutdown(wait=True)
        assert leaked is not None, (
            "ContextVar が勝手にリクエスト単位で切れている。"
            "この前提が変わったなら reset_active_key の要否を再検討すること")

    def test_reset_clears_it(self):
        """★ リクエスト境界で捨てられること。"""
        pool = cf.ThreadPoolExecutor(max_workers=1)
        try:
            pool.submit(lambda: _set_active_key("/rds/projectA.rds")).result()

            def next_request():
                reset_active_key()          # before_request 相当
                return _active_key_var.get()

            assert pool.submit(next_request).result() is None
        finally:
            pool.shutdown(wait=True)

    def test_reset_does_not_affect_a_key_set_after_it(self):
        """リセット後に正しく設定した値は残ること（過剰リセットの番人）。"""
        reset_active_key()
        _set_active_key("/rds/projectB.rds")
        assert _active_key_var.get() is not None


class TestBeforeRequestIsRegistered:
    """★ Flask に実際に登録されていること。

    reset_active_key があっても呼ばれなければ意味が無い。
    """

    def test_flask_before_request_hook_exists(self, monkeypatch):
        pytest.importorskip("dash")
        import secrets
        # app.main は import 時に SECRET_KEY を要求する（フェイルファースト設計）
        monkeypatch.setenv("FLASK_SECRET_KEY", secrets.token_hex(32))
        from app.main import server
        names = [f.__name__ for f in server.before_request_funcs.get(None, [])]
        assert "_reset_active_project_key" in names, names


class TestSettingsWritersScopeTheirProject:
    """設定を書くコールバックが rds_path から active key を立てること。

    ★ 正しい実装は interactive_spatial.save_spatial_display_settings にあり、
      対になる UMAP 版だけが呼び忘れていた。AST で「呼んでいるか」を見る。
    """

    @pytest.mark.parametrize("module,func", [
        ("app/callbacks/provenance_callbacks.py", "_save"),
        ("app/callbacks/interactive_umap.py", "save_umap_display_settings"),
        ("app/callbacks/interactive_spatial.py", "save_spatial_display_settings"),
        ("app/callbacks/interactive_calibration.py", "auto_save_int_cal"),
        ("app/callbacks/interactive_calibration.py", "save_int_cal_list"),
        ("app/callbacks/interactive_deg.py", "add_feature_bookmark"),
        ("app/callbacks/interactive_deg.py", "remove_feature_bookmark"),
    ])
    def test_writer_sets_the_active_key(self, module, func):
        import ast
        from pathlib import Path

        src = (Path(__file__).resolve().parent.parent / module).read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        fn = next((n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == func), None)
        assert fn is not None, f"{func} が見つからない"

        calls = {
            n.func.id for n in ast.walk(fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
        }
        assert "_set_active_key" in calls, (
            f"{module}:{func} が _set_active_key を呼んでいない。"
            "別プロジェクトへ書くか、記録が黙って捨てられる")


class TestReadersAlsoScopeTheirProject:
    """★ ver51.9: **読み出し側**も active key を立てること。

    ver51.8 でリクエスト境界に active key のリセットを入れたとき、
    「書き込み側 7 つ」だけを直して **読み出し側を見ていなかった**。
    その結果、`_set_active_key` を呼ばずに `_interactive_data` を読む
    コールバックが 2 つ、**常に None を読む**ようになった:

      - `interactive_cluster.update_cluster_dropdown_labels`
        → クラスタを改名してもドロップダウンのラベルが更新されない
      - `interactive_spatial.update_swatch_disabled_state`
        → 使用済み色がグレーアウトされない

    リセット前は「前のリクエストが見ていたプロジェクト」が残っていたので
    たまたま動いていただけで、元から正しくなかった。

    ここでは repo 全体を走査し、**@callback の中で `_interactive_data` /
    `_get_state()` を読むのに `_set_active_key` を呼ばないものが無いこと**を見る。
    ホワイトリスト方式（既知の 7 つを列挙）だと今回と同じ取りこぼしが起きる。
    """

    @staticmethod
    def _offenders():
        import ast
        from pathlib import Path

        cb_dir = Path(__file__).resolve().parent.parent / "app" / "callbacks"
        out = []
        for path in sorted(cb_dir.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.FunctionDef):
                    continue
                decorated = any(
                    (isinstance(d, ast.Call)
                     and ((isinstance(d.func, ast.Name) and d.func.id == "callback")
                          or (isinstance(d.func, ast.Attribute) and d.func.attr == "callback")))
                    for d in node.decorator_list)
                if not decorated:
                    continue

                names, attrs, calls = set(), set(), set()
                for n in ast.walk(node):
                    if isinstance(n, ast.Name):
                        names.add(n.id)
                    elif isinstance(n, ast.Attribute):
                        attrs.add(n.attr)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
                        calls.add(n.func.id)

                reads_state = ("_interactive_data" in names) or ("_get_state" in calls)
                if not reads_state:
                    continue
                # _get_state(rds_path) のように明示的にキーを渡している形は安全
                explicit = any(
                    isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "_get_state" and n.args
                    for n in ast.walk(node))
                if "_set_active_key" in calls or explicit:
                    continue
                out.append(f"{path.name}:{node.lineno} {node.name}()")
        return out

    def test_no_callback_reads_state_without_setting_the_key(self):
        offenders = self._offenders()
        assert not offenders, (
            "active key を立てずに _interactive_data を読むコールバックがある。"
            "リクエスト境界でリセットされるため **常に空** を読む:\n  "
            + "\n  ".join(offenders))
