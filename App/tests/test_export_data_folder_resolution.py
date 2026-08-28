"""データ出力が使う MSI データフォルダの確定ロジック（ver62.7）。

## 背景（実際に起きたこと）

サブプロジェクト台帳の `data_folder` が `/app/Data/DESI/Data`（＝既定値）に
化けたまま残っていた。この状態でも **UMAP もクラスタも Spatial も普通に表示される**。
画面の図は結果フォルダの解析結果 (RDS) だけで描いていて、生データフォルダを
一切見ないため。ところがデータ出力はクラスタ番号を生データの強度に結合し直すので、
そこだけが落ちる。利用者からは「図は見えているのに、なぜフォルダを直せと
言われるのか」が分からない。

`_infer_data_folder` の branch (a) には「登録済み data_folder に生データが
無ければ (b) の走査へ落とす」判断が ver62.2 の時点で書かれていた。しかし
呼び出し側（`_do_export` / `_do_export_api`）が `if not data_folder:` で
短絡するため**そこへ到達せず、書いてあるのに働いていなかった**。

ここで守るのは「**登録値は中身を見てから使う**」という性質。
"""
from pathlib import Path

import pytest

from app.callbacks import interactive_data_export as ide


@pytest.fixture
def tims_folder(tmp_path):
    """生データ（parquet）が直下にあるフォルダ。"""
    d = tmp_path / "dataset_A"
    d.mkdir()
    (d / "sample1.parquet").write_bytes(b"PAR1")
    return d


@pytest.fixture
def empty_folder(tmp_path):
    """サブフォルダはあるが生データが直下に無いフォルダ（壊れた登録値の形）。"""
    d = tmp_path / "DESI_Data_root"
    (d / "sub1").mkdir(parents=True)
    (d / "sub2").mkdir(parents=True)
    return d


def test_登録値に生データがあればそのまま使う(tims_folder):
    """挙動不変の確認。ここが変わると既存の正常系を壊す。"""
    folder, note = ide._resolve_data_folder(
        str(tims_folder), None, None, None, "TIMS")
    assert folder == str(tims_folder)
    assert note == "登録値"


def test_登録値に生データが無ければ結果フォルダの隣を探す(tmp_path, tims_folder,
                                                        empty_folder):
    """結果フォルダが生データの隣にあるプロジェクトは自力で復帰できること。

    実データの例: 結果フォルダ /app/Data/TIMS/Data/<dataset>/<解析名> に対し、
    その親 /app/Data/TIMS/Data/<dataset> が生データ。
    """
    result_dir = tims_folder / "P5_POS_1_nn10_md0p3_dim30"
    result_dir.mkdir()

    folder, note = ide._resolve_data_folder(
        str(empty_folder), str(result_dir), None, None, "TIMS")

    assert folder == str(tims_folder), (
        "登録値に生データが無いのに、隣にある生データへ乗り換えていない")
    assert "推定" in note


def test_推定も空振りなら登録値を残す(empty_folder, tmp_path):
    """None にしない。エラー文が「どのフォルダを見たか」を出せなくなるため。"""
    lonely = tmp_path / "output" / "Analysis_20260827_092631"
    lonely.mkdir(parents=True)

    folder, note = ide._resolve_data_folder(
        str(empty_folder), str(lonely), None, None, "TIMS")

    assert folder == str(empty_folder), (
        "登録値を捨てると、エラー文が一般論になって診断できなくなる")
    assert "生データ無し" in note

    # 実際にその登録値がエラー文へ出ること
    msg = ide._validate_data_folder(folder, "TIMS", "フォルダのパス")
    assert msg and str(empty_folder) in msg


def test_登録値が空なら従来どおり推定する(tims_folder):
    result_dir = tims_folder / "run1"
    result_dir.mkdir()
    folder, note = ide._resolve_data_folder(
        "", str(result_dir), None, None, "TIMS")
    assert folder == str(tims_folder)
    assert note == "推定"


def test_どこにも無ければNone(tmp_path):
    lonely = tmp_path / "nothing"
    lonely.mkdir()
    folder, note = ide._resolve_data_folder("", str(lonely), None, None, "TIMS")
    assert folder is None
    assert note == "見つからず"


def test_画面とAPIの両経路が同じヘルパーを通る():
    """判断を 2 箇所に書くと必ず片方だけ直る。1 箇所に集約されていることを固定する。"""
    import ast
    import inspect

    src = inspect.getsource(ide)
    tree = ast.parse(src)
    # 画面の出力と、ライブ session に依存しない API/バッチ用の生成関数。
    targets = {"_do_export", "build_interactive_export_for_project"}
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in targets:
            calls = {
                n.func.id for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            }
            found[node.name] = calls

    assert targets <= set(found), f"対象の関数が見つからない: {targets - set(found)}"
    for name, calls in found.items():
        assert "_resolve_data_folder" in calls, (
            f"{name} が _resolve_data_folder を通っていない")
        assert "_infer_data_folder" not in calls, (
            f"{name} が _infer_data_folder を直接呼んでいる"
            "（ヘルパーを迂回すると登録値の中身検査が抜ける）")


def test_エラー文が表示と出力の違いを説明する(empty_folder):
    """「図は出ているのに、なぜフォルダを直せと言われるのか」に答えること。"""
    msg = ide._no_input_message(str(empty_folder), "TIMS", "フォルダのパス")

    assert "RDS" in msg, "図が結果フォルダの RDS で描かれることが書かれていない"
    assert "この出力だけ失敗" in msg or "出力だけ失敗" in msg
    assert "MSIデータフォルダ" in msg, "直す場所が名指しされていない"
    assert "\n" not in msg, "改行は画面 (div) で潰れるので 1 行に収める"


# ---------------------------------------------------------------------------
# レイアウト: 直す欄が画面に出ていること
# ---------------------------------------------------------------------------
# ver62.3 の CHANGELOG に「記録: MSIデータフォルダ欄が見えない」として残していた件。
# 値は保持され下流（装置判定・生スペクトル読み出し）が使うのに `display: none` で
# 恒久的に隠されており、利用者は確認も修正もできなかった。エラー文が
# 「MSIデータフォルダの指定を確認してください」と言うのに、その欄が画面に無い。


def _walk_with_style(node, hidden=False):
    """コンポーネント木を辿り (node, 祖先も含めて非表示か) を返す。"""
    style = getattr(node, "style", None) or {}
    hidden = hidden or (str(style.get("display", "")).lower() == "none")
    yield node, hidden
    children = getattr(node, "children", None)
    if children is None:
        return
    if not isinstance(children, (list, tuple)):
        children = [children]
    for c in children:
        if hasattr(c, "children") or hasattr(c, "id"):
            yield from _walk_with_style(c, hidden)


def test_MSIデータフォルダ欄が画面に出ている():
    from app.layouts.interactive_tab import create_interactive_tab

    hits = [(n, h) for n, h in _walk_with_style(create_interactive_tab())
            if getattr(n, "id", None) == "interactive_msi_folder"]
    assert hits, "interactive_msi_folder がレイアウトに無い"
    assert len(hits) == 1, f"interactive_msi_folder が {len(hits)} 個ある"
    _node, hidden = hits[0]
    assert not hidden, (
        "MSIデータフォルダ欄が display:none の中にある。"
        "エラー文はこの欄を直すよう案内するので、見えないと利用者は詰む")


def test_参照ボタンが配線されている():
    """欄を出す以上、隣の「参照...」も動かないと画面として不完全。"""
    from app.callbacks.file_handlers import _BROWSE_BUTTONS

    assert _BROWSE_BUTTONS.get("browse_interactive_msi") == (
        "folder", "interactive_msi_folder")
