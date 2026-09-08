"""複数フォルダから複数の Parquet を登録できること (ver64.0)。

--------------------------------------------------------------------------
症状: 追加したフォルダのファイルが解析に入っていないように見える
--------------------------------------------------------------------------
「追加データフォルダ」を足しても、

  1. 件数バッジ「N ファイル検出」は **基準フォルダしか数えない**ので、
     何個足しても数字が動かない。画面上で件数を示すのはここだけなので、
     利用者から見ると「追加できていない」。
  2. サンプル一覧は `list_tims_files_multi` が **stem で重複排除**した
     名前だったため、別フォルダの同名ファイルは画面に 1 個しか出ない。
     ところが解析対象 (INPUT_PATHS) は `build_tims_input_paths_multi` +
     `Path(p).stem in 選択サンプル名` で作られており **両方が入る**。
     画面のチェック数と実際に読むファイル数が食い違う。
  3. その 1 個のチェックを外すと同名ファイルが両方消える。片方だけを
     選ぶ手段が無い。
  4. 切片(annotation)の候補は `find_tims_file_path_multi` が **先に
     見つけた 1 本**からしか作られない。ANNOTATION_FILTER は R 側で
     全ファイルに一律で適用される (ver6 テンプレ 967-981 行) ので、
     候補に出なかったファイルは 0 件一致となり
     `ANNOTATION_FILTER に一致する spot がありません` で **解析ごと停止**する。
  5. 追加フォルダは `dcc.Store` の既定 (memory) のまま保存経路が無く、
     ブラウザを再読込しただけで消える。消えたことは画面に出ない。

--------------------------------------------------------------------------
直し方
--------------------------------------------------------------------------
サンプル一覧をフォルダごとに分け、チェックの値を **ファイルのフルパス**に
する。画面で見えているものと INPUT_PATHS が 1 対 1 で対応するので、上の
1〜4 が同時に消える。5 は基準フォルダ (data_folder) と同じ保存・復元経路に
乗せる。

このファイルは「修正を戻すと落ちる」ことを確認済み。
"""

from pathlib import Path

import pandas as pd
import pytest

import app.callbacks.analysis_callbacks as ac
import app.callbacks.file_handlers as fh
import app.callbacks.project_callbacks as pc
from app.services import data_manager as dm


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mkpq(path: Path, annotation: list[str] | None = None) -> Path:
    """annotation 列を持てるダミー TIMS Parquet を作る。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({
        "spot_index": [1, 2], "x": [0.0, 1.0], "y": [0.0, 0.0],
        "mz_100.0000": [1.0, 2.0], "mz_101.0000": [1.0, 2.0],
    })
    if annotation is not None:
        df["annotation"] = annotation
    df.to_parquet(path, index=False)
    return path


def _checklists(children):
    """`update_sample_selector` の戻り値から (フォルダ, options, value) を取り出す。"""
    out = []
    for block in (children if isinstance(children, list) else [children]):
        for sub in (getattr(block, "children", None) or []):
            if type(sub).__name__ == "Checklist":
                out.append((sub.id["index"],
                            [o["value"] for o in sub.options],
                            list(sub.value)))
    return out


@pytest.fixture
def two_folders_same_stem(tmp_path):
    """別フォルダに同名 (s1) の Parquet がある構成。切片ラベルは別物。"""
    a, c = tmp_path / "FolderA", tmp_path / "FolderC"
    _mkpq(a / "s1.parquet", annotation=["P7_DHB1", "P7_DHB2"])
    _mkpq(c / "s1.parquet", annotation=["Other_1", "Other_2"])
    _mkpq(c / "s3.parquet", annotation=["Other_1", "Other_2"])
    return str(a), str(c)


# ---------------------------------------------------------------------------
# list_tims_folder_groups: 画面と解析の共通の出典
# ---------------------------------------------------------------------------

class TestFolderGroups:
    def test_folders_stay_separate_even_with_the_same_stem(self, two_folders_same_stem):
        """★ 同名 stem がフォルダをまたいでも消えないこと。

        `list_tims_files_multi` に戻すと "s1" が 1 個に畳まれてこのテストが落ちる。
        """
        a, c = two_folders_same_stem
        groups = dm.list_tims_folder_groups([a, c])
        assert [g["folder"] for g in groups] == [a, c]
        assert [Path(p).name for p in groups[0]["paths"]] == ["s1.parquet"]
        assert [Path(p).name for p in groups[1]["paths"]] == ["s1.parquet", "s3.parquet"]

    def test_blank_rows_are_skipped(self, two_folders_same_stem):
        """入力途中の空行は「フォルダ 0 個」として扱う（エラーにしない）。"""
        a, _ = two_folders_same_stem
        groups = dm.list_tims_folder_groups([a, "", None, "   "])
        assert [g["folder"] for g in groups] == [a]

    def test_the_same_folder_is_listed_once(self, two_folders_same_stem):
        """基準フォルダと同じフォルダを足しても二重に数えない。"""
        a, _ = two_folders_same_stem
        groups = dm.list_tims_folder_groups([a, a + "/", a])
        assert len(groups) == 1


# ---------------------------------------------------------------------------
# サンプル一覧 → Store
# ---------------------------------------------------------------------------

class TestSampleSelector:
    def test_tims_lists_every_file_of_every_folder(self, two_folders_same_stem):
        """★ 画面のチェック数 = 全フォルダの実ファイル数。

        従来は stem 重複排除で 3 ファイル中 2 個しか出なかった。
        """
        a, c = two_folders_same_stem
        lists = _checklists(fh.update_sample_selector(a, None, "tims_v8", [c]))
        assert [f for f, _, _ in lists] == [a, c]
        # 値はフルパス（サンプル名だと同名ファイルを区別できない）
        assert lists[0][1] == [str(Path(a) / "s1.parquet")]
        assert lists[1][1] == [str(Path(c) / "s1.parquet"), str(Path(c) / "s3.parquet")]
        # 既定は全選択（従来どおり）
        assert all(opts == val for _, opts, val in lists)

    def test_the_total_count_is_shown_for_multiple_folders(self, two_folders_same_stem):
        """★ 追加フォルダを含む合計をどこかに出すこと。

        上の「N ファイル検出」バッジは基準フォルダしか数えないため、
        これが無いと画面から追加フォルダの反映を確認できない。
        """
        a, c = two_folders_same_stem
        children = fh.update_sample_selector(a, None, "tims_v8", [c])
        texts = [str(x.children) for x in children if type(x).__name__ == "Small"]
        assert any("合計 3 ファイル" in t for t in texts), texts

    def test_sync_reports_both_names_and_paths(self, two_folders_same_stem):
        a, c = two_folders_same_stem
        lists = _checklists(fh.update_sample_selector(a, None, "tims_v8", [c]))
        names, paths = fh.sync_selected_samples([v for _, _, v in lists])
        assert names == ["s1", "s3"]          # 名前は畳む (R のサンプル名は basename 由来)
        assert paths == [str(Path(a) / "s1.parquet"),
                         str(Path(c) / "s1.parquet"),
                         str(Path(c) / "s3.parquet")]

    def test_one_of_two_same_named_files_can_be_deselected(self, two_folders_same_stem):
        """★ 同名ファイルの片方だけを外せること。

        従来はチェックが 1 個しか無く、外すと両方が同時に解析から消えた。
        """
        a, c = two_folders_same_stem
        lists = _checklists(fh.update_sample_selector(a, None, "tims_v8", [c]))
        # FolderC 側の s1 だけ外す
        values = []
        for folder, _, val in lists:
            values.append([v for v in val
                           if not (folder == c and Path(v).stem == "s1")])
        _, paths = fh.sync_selected_samples(values)
        assert str(Path(a) / "s1.parquet") in paths
        assert str(Path(c) / "s1.parquet") not in paths

    def test_desi_keeps_sample_names_with_dots(self, tmp_path):
        """DESI は従来どおり stem 単位。`d2.v2` のようなドット入りも壊さない。

        値をパスとサンプル名で取り違えると `Path("d2.v2").stem` が "d2" に
        化けて、R が読むファイル名と一致しなくなる。
        """
        (tmp_path / "d1.txt").write_text("x\ty\tmz_1\n0\t0\t1\n")
        (tmp_path / "d2.v2.txt").write_text("x\ty\tmz_1\n0\t0\t1\n")
        lists = _checklists(fh.update_sample_selector(str(tmp_path), "desi_v8", None, []))
        assert lists[0][1] == ["d1", "d2.v2"]
        names, paths = fh.sync_selected_samples([lists[0][2]])
        assert names == ["d1", "d2.v2"]
        assert paths == []          # DESI はパスを持たない


# ---------------------------------------------------------------------------
# 切片 (annotation) 選択
# ---------------------------------------------------------------------------

class TestAnnotationSelector:
    def test_every_selected_file_gets_its_own_checkbox(self, two_folders_same_stem):
        """★ 同名ファイルでも 1 ファイル 1 チェックリストになること。

        id が stem のままだと Dash のパターンマッチング ID が重複し、
        2 つのチェックが 1 つとして扱われる。
        """
        a, c = two_folders_same_stem
        paths = [str(Path(a) / "s1.parquet"), str(Path(c) / "s1.parquet")]
        ui, _ = fh.update_annotation_selector(paths, None, "tims_v8")
        ids = [sub.id["index"] for blk in ui if type(blk).__name__ == "Div"
               for sub in blk.children if type(sub).__name__ == "Checklist"]
        assert ids == paths
        assert len(set(ids)) == len(ids)

    def test_labels_of_the_second_same_named_file_are_not_lost(self, two_folders_same_stem):
        """★ 2 本目の切片ラベルが ANNOTATION_FILTER に入ること。

        入らないと R 側 (ver6:971-977) が 0 件一致で
        `ANNOTATION_FILTER に一致する spot がありません` を投げ、解析が止まる。
        """
        a, c = two_folders_same_stem
        paths = [str(Path(a) / "s1.parquet"), str(Path(c) / "s1.parquet")]
        _, store = fh.update_annotation_selector(paths, None, "tims_v8")
        assert store == ["Other_1", "Other_2", "P7_DHB1", "P7_DHB2"]

    def test_hidden_for_non_tims_umap(self, two_folders_same_stem):
        a, _ = two_folders_same_stem
        assert fh.update_annotation_selector(
            [str(Path(a) / "s1.parquet")], "desi_v8", None) == ([], None)


# ---------------------------------------------------------------------------
# 追加データフォルダの行 UI（基準フォルダの欄と同じ形）
# ---------------------------------------------------------------------------

class TestExtraFolderRows:
    def test_each_row_has_input_browse_remove_and_badge(self, two_folders_same_stem):
        """★ 1 行 = パス入力欄 + 参照... + × + 件数バッジ。

        従来はフォルダ名を出すだけの読み取り専用リストで、件数バッジも
        パスの手直しもできなかった。
        """
        a, _ = two_folders_same_stem
        rows = fh.render_extra_folders([a, ""])
        assert len(rows) == 2
        line, badge = rows[0].children
        inp, browse, remove = line.children
        assert inp.id == {"type": "extra_folder_path", "index": 0}
        assert inp.value == a
        assert browse.id == {"type": "btn_browse_extra_folder", "index": 0}
        assert remove.id == {"type": "btn_remove_extra_folder", "index": 0}
        assert "1 ファイル検出" in badge.children
        # 空行は「これから入力する行」として案内だけ出す
        assert "参照" in rows[1].children[1].children

    def test_a_missing_folder_is_marked_on_its_row(self, tmp_path):
        badge = fh._extra_folder_badge(str(tmp_path / "does_not_exist"))
        assert "フォルダが見つかりません" in badge.children

    def test_typed_path_reaches_the_store(self, two_folders_same_stem):
        a, c = two_folders_same_stem
        assert fh.edit_extra_folder_path([c], [a]) == [c]

    def test_no_write_while_the_row_count_disagrees(self, two_folders_same_stem):
        """行の増減直後に古い値で上書きしないこと（描き直し前の値が届く）。"""
        a, c = two_folders_same_stem
        assert fh.edit_extra_folder_path([c], [a, ""]) is fh.no_update
        assert fh.edit_extra_folder_path([a], [a]) is fh.no_update

    def test_browse_selection_lands_on_the_row_that_asked(self, two_folders_same_stem):
        """★ 行の「参照...」で選んだフォルダはその行にだけ入ること。"""
        a, c = two_folders_same_stem
        state = {"caller_id": fh._EXTRA_ROW_CALLER, "extra_index": 1,
                 "selected_path": c, "mode": "folder"}
        assert fh.apply_extra_folder_selection(1, state, [a, ""]) == [a, c]

    def test_other_browse_buttons_are_untouched(self, two_folders_same_stem):
        """基準フォルダなど従来の参照ボタン経由では何も書き換えないこと。"""
        a, c = two_folders_same_stem
        state = {"caller_id": "data_folder", "selected_path": c}
        assert fh.apply_extra_folder_selection(1, state, [a, ""]) is fh.no_update


# ---------------------------------------------------------------------------
# 解析へ渡るところ / 記録 / 保存
# ---------------------------------------------------------------------------

class TestReachesTheAnalysis:
    def test_input_paths_come_from_the_checked_paths(self):
        """★ INPUT_PATHS は選択パスをそのまま使うこと。

        stem 一致で絞る実装に戻すと、同名ファイルが両方入って
        「チェック 1 個なのに 2 本読む」に戻る。
        """
        import inspect
        src = inspect.getsource(ac.run_analysis)
        assert "selected_sample_paths" in src, (
            "run_analysis が選択パスを使っていない")
        assert 'State("selected_sample_paths_store", "data")' in \
            inspect.getsource(ac).split("def run_analysis")[0]

    def test_the_analysis_records_what_it_actually_read(self):
        """★ どのファイル・どのフォルダを読んだかを analysis_params.json に残す。

        残らないと、複数フォルダの解析を後から検証する手段が
        log/v8_runtime_*.R しか無い。
        """
        import inspect
        src = inspect.getsource(ac.run_analysis)
        assert '"input_paths": params.get("input_paths")' in src
        assert src.count('"extra_data_folders"') >= 3, (
            "last_settings / サブプロジェクト設定 / analysis_params のどれかで"
            "追加データフォルダを保存していない")

    def test_extra_folders_are_validated_before_running(self, tmp_path):
        """★ 存在しない追加フォルダは実行前に止めること。

        止めないと「そのフォルダの分だけ黙って抜けた」結果になる。
        """
        data = tmp_path / "main"
        _mkpq(data / "s1.parquet")
        out = tmp_path / "out"
        out.mkdir()
        blocking, _ = ac._collect_preflight_errors(
            None, "tims_v8", str(data), "", str(out),
            0.05, 0.25, 0.01, False, "", "",
            extra_data_folders=[str(tmp_path / "nope"), ""])
        assert any("追加データフォルダ" in b for b in blocking), blocking

    def test_blank_extra_rows_do_not_block(self, tmp_path):
        """空行は「これから入力する行」なので実行を止めない。"""
        data = tmp_path / "main"
        _mkpq(data / "s1.parquet")
        out = tmp_path / "out"
        out.mkdir()
        blocking, _ = ac._collect_preflight_errors(
            None, "tims_v8", str(data), "", str(out),
            0.05, 0.25, 0.01, False, "", "",
            extra_data_folders=["", "  "])
        assert not any("追加データフォルダ" in b for b in blocking), blocking


class TestPersistence:
    def test_the_layout_restores_the_saved_extra_folders(self):
        """★ 基準フォルダと同じく前回値から復元すること。

        既定の memory Store のままだと、ブラウザを再読込しただけで
        追加フォルダが消える（消えたことは画面に出ない）。
        """
        import inspect
        from app.layouts import settings_tab
        src = inspect.getsource(settings_tab)
        assert 'ls.get("extra_data_folders"' in src

    def test_sub_project_restore_returns_the_extra_folders(self, monkeypatch):
        """★ サブプロジェクトを切り替えたら追加フォルダも入れ替わること。

        入れ替わらないと、前のサブプロジェクトの追加フォルダが残ったまま
        実行され、記録上は今のサブプロジェクトなのに別のデータが混ざる。
        """
        import inspect
        outputs = inspect.getsource(pc).split("def sub_action_new_analysis")[0]
        assert 'Output("extra_data_folders_store", "data", allow_duplicate=True)' \
            in outputs
        src = inspect.getsource(pc.sub_action_new_analysis)
        assert "_n_outputs = 24" in src, "Output を足したのに戻り値の個数が合っていない"
        assert 'settings.get("extra_data_folders")' in src
