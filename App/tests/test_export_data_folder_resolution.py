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


# ---------------------------------------------------------------------------
# ver62.8: 解析自身の記録から復旧する
# ---------------------------------------------------------------------------
# サブプロジェクト台帳の `data_folder` は壊れることがある（ver62.4 で塞いだ経路）。
# 一方、**同じ実行が結果フォルダには正しい値を残している**
# （`analysis_params.json` / `receipt.json`）。実機の監査では 10 プロジェクト・
# 15 サブプロジェクトで台帳の値が使えず（既定値に化けた 1 / 未設定 11 /
# 実体が消えた 3）、台帳を直して回るより記録から引く方が確実で範囲も広かった。
#
# ver62.7 の兄弟走査は「結果フォルダの隣に生データがある」レイアウトでしか効かない。
# 既定の出力先 `Data/Other/output/Analysis_*` の隣には他の解析結果しか無いので、
# **既定の運用ほど自力復帰できない**という逆立ちした状態だった。

import json


def _write_record(result_dir, *, receipt=None, params=None):
    """結果フォルダに解析記録を置く。"""
    result_dir.mkdir(parents=True, exist_ok=True)
    if receipt is not None:
        (result_dir / "receipt.json").write_text(
            json.dumps(receipt, ensure_ascii=False), encoding="utf-8")
    if params is not None:
        (result_dir / "analysis_params.json").write_text(
            json.dumps(params, ensure_ascii=False), encoding="utf-8")


@pytest.fixture
def isolated_result_dir(tmp_path):
    """生データの隣に無い結果フォルダ（本番の Data/Other/output/Analysis_* と同じ形）。"""
    d = tmp_path / "output" / "Analysis_20260827_092631"
    d.mkdir(parents=True)
    return d


def test_登録値が壊れていても解析記録から復旧する(tims_folder, empty_folder,
                                                  isolated_result_dir):
    """本番で起きていたそのもの。兄弟走査では救えない配置で記録から引く。"""
    _write_record(isolated_result_dir,
                  params={"data_folder": str(tims_folder)})

    folder, note = ide._resolve_data_folder(
        str(empty_folder), str(isolated_result_dir), None, None, "TIMS")

    assert folder == str(tims_folder)
    assert note.startswith("解析記録")


def test_登録値が空でも解析記録から補える(tims_folder, isolated_result_dir):
    """監査で 15 件中 11 件と最多だったケース。"""
    _write_record(isolated_result_dir,
                  params={"data_folder": str(tims_folder)})

    folder, note = ide._resolve_data_folder(
        "", str(isolated_result_dir), None, None, "TIMS")

    assert folder == str(tims_folder)
    assert note.startswith("解析記録")


def test_receipt_が_analysis_params_より優先される(tmp_path, empty_folder,
                                                    isolated_result_dir):
    """`provenance._analysis_block` と同じ優先順であること。"""
    good = tmp_path / "from_receipt"
    good.mkdir()
    (good / "a.parquet").write_bytes(b"PAR1")
    stale = tmp_path / "from_params"
    stale.mkdir()
    (stale / "b.parquet").write_bytes(b"PAR1")

    _write_record(isolated_result_dir,
                  receipt={"object": {"data_folder": str(good)}},
                  params={"data_folder": str(stale)})

    folder, _note = ide._resolve_data_folder(
        str(empty_folder), str(isolated_result_dir), None, None, "TIMS")
    assert folder == str(good)


def test_登録値に生データがあれば記録を見ない(tims_folder, tmp_path,
                                              isolated_result_dir):
    """利用者が明示的に入れた値を勝手に巻き戻さない（データを意図的に移した場合）。"""
    other = tmp_path / "recorded_but_old"
    other.mkdir()
    (other / "c.parquet").write_bytes(b"PAR1")
    _write_record(isolated_result_dir, params={"data_folder": str(other)})

    folder, note = ide._resolve_data_folder(
        str(tims_folder), str(isolated_result_dir), None, None, "TIMS")

    assert folder == str(tims_folder)
    assert note == "登録値"


def test_記録のパスも死んでいれば両方を名指しして止まる(tmp_path, empty_folder,
                                                        isolated_result_dir):
    """データを改名・移動した場合（監査の 3 件）。推測も空振りなら登録値を残す。"""
    dead = tmp_path / "moved_away"          # 作らない = 実体が無い
    _write_record(isolated_result_dir, params={"data_folder": str(dead)})

    folder, note = ide._resolve_data_folder(
        str(empty_folder), str(isolated_result_dir), None, None, "TIMS")

    assert folder == str(empty_folder), "登録値を残さないとエラー文が一般論になる"
    assert "生データ無し" in note
    msg = ide._validate_data_folder(folder, "TIMS", "フォルダのパス")
    assert msg and str(empty_folder) in msg


def test_記録が壊れたJSONでも例外にならない(empty_folder, isolated_result_dir):
    (isolated_result_dir / "receipt.json").write_text("{壊れ", encoding="utf-8")
    (isolated_result_dir / "analysis_params.json").write_text("", encoding="utf-8")

    folder, note = ide._resolve_data_folder(
        str(empty_folder), str(isolated_result_dir), None, None, "TIMS")
    assert folder == str(empty_folder)
    assert "生データ無し" in note


def test_再解析の記録からも復旧できる(tims_folder, empty_folder, isolated_result_dir):
    """今回の実データは tims_cluster_filter（クラスタフィルタ再解析）だった。

    `_params_to_save` は再解析時 `reanalysis_data_folder` を `data_folder` キーに
    入れて保存するので、経路が違っても同じキーで読める。
    """
    _write_record(isolated_result_dir,
                  receipt={"object": {"analysis_type": "tims_cluster_filter",
                                      "data_folder": str(tims_folder)}})

    folder, note = ide._resolve_data_folder(
        str(empty_folder), str(isolated_result_dir), None, None, "TIMS")
    assert folder == str(tims_folder)
    assert note.startswith("解析記録")


def test_台帳を書き換えない(tims_folder, empty_folder, isolated_result_dir,
                            monkeypatch):
    """復旧しても登録値は直さない。

    今回の一連の事故がそもそも「台帳への自動書き込み」発 (ver62.4) だったので、
    同じ台帳へ自動で書く経路をもう 1 本増やさない。
    """
    from app.services import project_manager

    called = []
    monkeypatch.setattr(project_manager, "update_sub_project",
                        lambda *a, **k: called.append(a))
    _write_record(isolated_result_dir, params={"data_folder": str(tims_folder)})

    ide._resolve_data_folder(str(empty_folder), str(isolated_result_dir),
                             None, None, "TIMS")
    assert not called, "台帳を書き換えている"


def test_復旧したときだけ使ったフォルダを述べる():
    """登録値をそのまま使ったときに毎回出ると、注意書きの意味が薄れる。"""
    assert ide._folder_note_message("/x/y", "登録値") == ""
    assert ide._folder_note_message("/x/y", "登録値(生データ無し)") == ""

    m = ide._folder_note_message("/x/y", "解析記録(登録値に生データ無し)")
    assert "解析記録" in m and "/x/y" in m
    m2 = ide._folder_note_message("/x/y", "推定(登録値に生データ無し)")
    assert "走査" in m2 and "/x/y" in m2


def test_recorded_data_folder_単体(tmp_path):
    from app.services.provenance import recorded_data_folder

    d = tmp_path / "res"
    d.mkdir()
    assert recorded_data_folder(d) is None          # 記録が無い
    assert recorded_data_folder(None) is None

    _write_record(d, params={"data_folder": "  /a/b  "})
    assert recorded_data_folder(d) == "/a/b"        # 前後の空白は落とす

    _write_record(d, receipt={"object": {"data_folder": "/c/d"}})
    assert recorded_data_folder(d) == "/c/d"        # receipt 優先

    _write_record(d, receipt={"object": {}})        # 空なら params へ落ちる
    assert recorded_data_folder(d) == "/a/b"
