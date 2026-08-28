"""フルスクリーンの id 参照が「分岐をまたいでいない」ことの静的検査 (ver62.5)。

■ なぜ要るか

`interactive_fullscreen.py` の `accumulate_annotation_positions_fs` は、
UMAP 側と Spatial 側の除外ドロップダウンを **両方** State に取っていた。

    State("fs_umap_exclude_cluster", "value"),      # UMAP 分岐でしか作られない
    State("fs_spatial_exclude_cluster", "value"),   # Spatial 分岐でしか作られない

ところがフルスクリーンの中身は相互排他の分岐（UMAP / Feature / Spatial / DEG）
で組み立てられるため、**2 つが同時に存在することは無い**。フルスクリーンで
ラベルをドラッグするとこの callback が発火し、無い方の State で

    ReferenceError: A nonexistent object was used in an `State` of a Dash
    callback. The id of this object is `fs_umap_exclude_cluster`

になる。`suppress_callback_exceptions=True` なので登録時には弾かれず、
実行時に初めて出る。しかもレンダラ側のエラーなので **以降のコールバック配送が
巻き込まれ**、無関係なデータ出力の進捗が「準備中 0%」から動かなくなった。

ver46.1 で入ってから見つかるまで、単体テストも E2E も 1 件も検出していない。
「どの分岐で作られる id か」を見ているテストが無かったため。

■ 何を守るか

1 つの callback が要求する id は、**同時に存在し得る**ものだけであること。
言い換えると、相互排他の分岐 2 つ以上にまたがって id を参照していないこと。

AST で見るので Dash の起動もブラウザも要らない。
"""

import ast
from pathlib import Path

TARGET = (Path(__file__).resolve().parent.parent
          / "app" / "callbacks" / "interactive_fullscreen.py")

# 分岐に依存せず常に存在するもの（常設レイアウトの Store など）は対象外。
_ALWAYS_PRESENT = {
    "fs_annotation_relayout_signal",
    "fs_label_positions_snapshot",
}


def _tree():
    return ast.parse(TARGET.read_text(encoding="utf-8"))


def _string_ids(node):
    """ノード配下の Input(...) / State(...) の literal な id を集める。"""
    out = set()
    for sub in ast.walk(node):
        if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                and sub.func.id in ("Input", "State") and sub.args):
            a = sub.args[0]
            if isinstance(a, ast.Constant) and isinstance(a.value, str):
                out.add(a.value)
    return out


def _component_ids(node):
    """ノード配下で `id="..."` として**作られている** component の id を集める。"""
    out = set()
    for sub in ast.walk(node):
        if not isinstance(sub, ast.Call):
            continue
        for kw in sub.keywords:
            if (kw.arg == "id" and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)):
                out.add(kw.value.value)
    return out


def _body_builder():
    """`fullscreen_modal_body` の children を返す関数を見つける。"""
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if "fullscreen_modal_body" in _string_ids(dec) or any(
                isinstance(s, ast.Constant) and s.value == "fullscreen_modal_body"
                for s in ast.walk(dec)
                if isinstance(s, ast.Constant)
            ):
                return node
    return None


_PLACEHOLDER_FN = "_fs_exclude_placeholders"


def _calls_placeholder_helper(nodes):
    """そのノード群が `_fs_exclude_placeholders(...)` を呼んでいるか。"""
    for node in nodes:
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call)
                    and getattr(sub.func, "id", None) == _PLACEHOLDER_FN):
                return True
    return False


def _branch_id_sets(func):
    """`if trigger == "..."` の分岐ごとに `{ids, uses_placeholder}` を返す。"""
    branches = {}
    for stmt in func.body:
        if not isinstance(stmt, ast.If):
            continue
        keys = [c.value for c in ast.walk(stmt.test)
                if isinstance(c, ast.Constant) and isinstance(c.value, str)
                and c.value.startswith("expand_")]
        if not keys:
            continue
        ids = set()
        for s in stmt.body:
            ids |= _component_ids(s)
        branches[keys[0]] = {
            "ids": ids,
            "uses_placeholder": _calls_placeholder_helper(stmt.body),
        }
    return branches


def _shared_ids():
    """全分岐がプレースホルダで補っている id。補い漏れがあれば空を返す。

    ★ ver62.5: 「分岐をまたいで参照してよい」のは、**すべての分岐が
      補っている**ときだけ。1 つでも補い漏れがあれば、その id は
      「常に存在する」とは言えないので、下の検査で違反として扱われる。
    """
    import app.callbacks.interactive_fullscreen as fs
    branches = _branch_id_sets(_body_builder())
    if not branches or not all(b["uses_placeholder"] for b in branches.values()):
        return set()
    return set(fs._FS_EXCLUDE_IDS)


def test_the_body_builder_is_found():
    """検査の前提。見つからなければ以降の検査が空振りする。"""
    assert _body_builder() is not None, (
        "fullscreen_modal_body を組み立てる関数を特定できない。"
        "この検査が空振りしていないか確認すること")


def test_every_branch_is_detected():
    branches = _branch_id_sets(_body_builder())
    assert set(branches) == {
        "expand_umap_btn", "expand_feature_btn",
        "expand_spatial_btn", "expand_deg_btn",
    }, f"分岐の検出漏れ: {sorted(branches)}"


def test_every_branch_supplies_the_shared_exclude_dropdowns():
    """★ ver62.5: どの分岐で開いても 2 つの除外ドロップダウンが揃うこと。

    1 つでも欠けると、そこで開いたときに
    `accumulate_annotation_positions_fs` が存在しない id を参照する。
    """
    missing = [name for name, b in _branch_id_sets(_body_builder()).items()
               if not b["uses_placeholder"]]
    assert not missing, (
        f"{missing} の分岐が {_PLACEHOLDER_FN}() を呼んでいない。"
        "その分岐で開くと、無い方の除外ドロップダウンを参照して落ちる")


def test_no_callback_requires_ids_from_two_exclusive_branches():
    """1 つの callback が、相互排他の分岐 2 つ以上から id を要求していないこと。

    要求しているなら、その callback は**必ずどこかで**存在しない id を
    参照することになる。
    """
    tree = _tree()
    branches = _branch_id_sets(_body_builder())
    always = _ALWAYS_PRESENT | _shared_ids()
    # id → それを作る分岐名（全分岐で補われている id は対象外）
    owner = {}
    for name, b in branches.items():
        for i in b["ids"]:
            if i in always:
                continue
            owner.setdefault(i, set()).add(name)

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not (isinstance(dec, ast.Call)
                    and getattr(dec.func, "id", getattr(dec.func, "attr", None))
                    == "callback"):
                continue
            used = _string_ids(dec) - always
            spans = set()
            for i in used:
                spans |= owner.get(i, set())
            if len(spans) > 1:
                offenders.append((node.name, sorted(spans),
                                  sorted(i for i in used if i in owner)))

    assert not offenders, (
        "同時に存在し得ない id を要求している callback がある。"
        "フルスクリーンの中身は相互排他の分岐で組み立てられるので、"
        "実行時に ReferenceError になり、以降のコールバック配送ごと止まる:\n"
        + "\n".join(f"  {n}: 分岐 {b} の id {i} を同時に要求" for n, b, i in offenders))


def test_placeholders_cover_both_ids():
    """ヘルパーが 2 つの除外 id を対象にしていること。"""
    import app.callbacks.interactive_fullscreen as fs
    assert set(fs._FS_EXCLUDE_IDS) == {
        "fs_umap_exclude_cluster", "fs_spatial_exclude_cluster"}
    assert [c.id for c in fs._fs_exclude_placeholders()] == list(fs._FS_EXCLUDE_IDS)
    assert [c.id for c in fs._fs_exclude_placeholders("fs_umap_exclude_cluster")] == [
        "fs_spatial_exclude_cluster"]


def test_placeholder_value_means_no_exclusion():
    """補ったドロップダウンの値が挙動を変えないこと（除外なし）。"""
    import app.callbacks.interactive_fullscreen as fs
    for c in fs._fs_exclude_placeholders():
        assert c.value is None
    assert fs._excl_set(None) == set()
