"""見出し（アコーディオン）を畳んでいる間の変更が、開き直したときに反映されることの番人。

★ ver56.5 / デバッグ総点検 §4.3 で確定した不具合 (C09-1 / C04-1):

  UMAP・Spatial の見出しを**畳んだ状態**でクラスタ名の変更・色の変更・マージ表示の
  切替などを行い、そのあと開き直しても **図が変更前のまま**表示される。
  設定欄は新しい値なのに図だけ古い、という食い違いが起きる。そのまま
  「一括保存」や PowerPoint 出力を行うと、**古い図が成果物として保存される**。

■ なぜ起きるか

`accordion_toggle_is_noop()` は「前回この描画が見た開閉状態」を覚えておき、
アコーディオン単独の発火で状態が変わっていなければ再描画を省く。ところが
呼び出し元は

    if "acc_umap" not in active_list:
        return no_update          # ← ここで抜けるので記録が更新されない
    ...
    if accordion_toggle_is_noop(...):   # ← 閉じている間は到達しない

という順序になっており、**閉じている間ずっと記録が「開」のまま**になる。
開き直すと prev=True / is_open=True で「変化なし」と判定され、再描画がスキップされる。

■ ver51.9 の修正が効いていなかったこと

この症状は ver51.9 (C-3) で一度「修正済み」とされている。しかし当時直したのは
節 id の綴り (`acc_umap_integrated` → `acc_umap`) だけで、記録が更新されない
上記の順序は手つかずだった。**綴りを直した結果、prev が常に False から
常に True に変わっただけで、開き直しの再描画は相変わらずスキップされていた**
(挙動不変の空修正)。本テストはその再発も同時に防ぐ。
"""
import pytest


@pytest.fixture
def ic():
    import app.callbacks.interactive_callbacks as module
    module._accordion_seen.clear()
    yield module
    module._accordion_seen.clear()


class TestReopenRedraws:
    """★ 本丸: 閉 → (変更) → 開 で必ず再描画されること。"""

    def test_reopen_after_close_is_not_treated_as_noop(self, ic):
        """開 → 閉 → 開 のシーケンスで、開き直しが再描画になること。"""
        rds = "/results/run1"

        # 1) 開いている状態で描画された
        assert ic.accordion_toggle_is_noop(
            "acc_umap", "sess1", rds, ["acc_umap"], "cluster_name_map") is False

        # 2) 畳む（呼び出し元は早期 return するが、閉状態は記録されねばならない）
        ic.accordion_record_closed("acc_umap", "sess1", rds)

        # 3) 開き直す → 再描画が必要
        assert ic.accordion_toggle_is_noop(
            "acc_umap", "sess1", rds, ["acc_umap"], "interactive_accordion") is False, (
            "閉じている間の変更が反映されない。開き直しても図が古いまま残る")

    def test_other_section_toggle_is_still_skipped(self, ic):
        """別セクションの開閉だけでは再描画しないこと（本来の目的を壊さない）。"""
        rds = "/results/run1"
        ic.accordion_toggle_is_noop(
            "acc_umap", "sess1", rds, ["acc_umap"], "cluster_name_map")
        # acc_umap は開いたまま、別セクションを開閉した
        assert ic.accordion_toggle_is_noop(
            "acc_umap", "sess1", rds, ["acc_umap", "acc_deg"],
            "interactive_accordion") is True, (
            "別セクションの開閉で UMAP 全体を作り直している（ver46.1 の趣旨に反する）")

    def test_non_accordion_trigger_always_redraws(self, ic):
        """アコーディオン以外が発火元なら常に再描画すること。"""
        rds = "/results/run1"
        ic.accordion_toggle_is_noop(
            "acc_umap", "sess1", rds, ["acc_umap"], "interactive_accordion")
        assert ic.accordion_toggle_is_noop(
            "acc_umap", "sess1", rds, ["acc_umap"], "custom_color_map_store") is False

    def test_first_call_always_redraws(self, ic):
        """記録が無い初回は必ず描画すること（安全側）。"""
        assert ic.accordion_toggle_is_noop(
            "acc_umap", "sess1", "/r", ["acc_umap"], "interactive_accordion") is False


class TestSessionIsolation:
    """★ C09-2: 利用者ごとに記録が分かれること。"""

    def test_sessions_do_not_share_state(self, ic):
        """別セッションの開閉が互いの判定を汚さないこと。

        `session_id=None` を渡すと全利用者が `__nosession__` という同じ鍵を
        共有し、他人の操作で自分の再描画がスキップされうる。
        """
        rds = "/results/run1"
        ic.accordion_toggle_is_noop("acc_umap", "sessA", rds, ["acc_umap"], "x")
        # 別セッションの初回は記録が無いので必ず描画される
        assert ic.accordion_toggle_is_noop(
            "acc_umap", "sessB", rds, ["acc_umap"], "interactive_accordion") is False

    def test_umap_callback_receives_a_real_session_id(self):
        """★ 統合 UMAP の描画が `session_id=None` を渡していないこと。

        隣の facet 版 (:662) と Spatial 版は正しく渡しており、
        統合 UMAP だけが None だった。
        """
        import ast
        import inspect
        import app.callbacks.interactive_umap as um

        tree = ast.parse(inspect.getsource(um))
        bad = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and getattr(node.func, "id", None) == "accordion_toggle_is_noop"):
                continue
            if len(node.args) >= 2:
                second = node.args[1]
                if isinstance(second, ast.Constant) and second.value is None:
                    bad.append(node.lineno)
        assert not bad, (
            "accordion_toggle_is_noop に session_id=None を渡している箇所がある"
            f" (interactive_umap.py:{bad})。全利用者で記録が共有され、"
            "他人の操作で自分の再描画がスキップされる")


class TestAllCallSitesRecordClosedState:
    """★ 早期 return の前に閉状態を記録していること（全呼び出し元）。

    1 か所でも記録を忘れると、その画面だけ「畳んだ間の変更が反映されない」に戻る。
    """

    @pytest.mark.parametrize("module_name,section", [
        ("app.callbacks.interactive_umap", "acc_umap"),
        ("app.callbacks.interactive_spatial", "acc_spatial"),
    ])
    def test_early_return_is_preceded_by_record(self, module_name, section):
        import ast
        import importlib
        import inspect

        module = importlib.import_module(module_name)
        src = inspect.getsource(module)
        assert "accordion_record_closed" in src, (
            f"{module_name} が閉状態を記録していない。"
            f"'{section}' を畳んでいる間の変更が開き直しても反映されない")

        # 記録呼び出しが、その関数内の早期 return より前にあること
        tree = ast.parse(src)
        found = False
        for fn in ast.walk(tree):
            if not isinstance(fn, ast.FunctionDef):
                continue
            calls = [n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.Call)
                     and getattr(n.func, "id", None) == "accordion_record_closed"]
            noops = [n.lineno for n in ast.walk(fn)
                     if isinstance(n, ast.Call)
                     and getattr(n.func, "id", None) == "accordion_toggle_is_noop"]
            if calls and noops:
                assert min(calls) < min(noops), (
                    f"{module_name}:{fn.name} で記録が noop 判定より後にある")
                found = True
        assert found, f"{module_name} に記録と判定の両方を持つ関数が無い"
