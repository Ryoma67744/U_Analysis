"""データ管理サブタブの「フォルダの移動」の検証（ver56.0）。

出力先の既定が長らく `/app`（コンテナの書き込み層）だったため、結果が
SFTP から見えず `docker compose down` で消える場所に残っている環境がある。
それを UI から永続化先へ退避するのが `move_entry` で、ここでは

- 永続/非永続の判定（`is_persistent_path`）
- 移動前のガード（`preview_move`）
- 移動と、移動後の projects.json パス貼り替え（`move_entry`）

を見る。ファイル操作は tmp_path、projects.json はメモリ上の dict に差し替える。
"""

import contextlib

import pytest

import app.services.data_browser as db
import app.services.project_manager as pm


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def locs(tmp_path, monkeypatch):
    """DATA_LOCATIONS を tmp_path 配下に差し替える。

    `stray` は「どのルートにも属さない場所」＝コンテナ書き込み層の見立て。
    """
    roots = {}
    for key, label in (("desi", "DESI生データ"), ("tims", "TIMS生データ"),
                       ("output", "解析出力"), ("internal", "アプリ内部データ")):
        root = tmp_path / key
        root.mkdir()
        roots[key] = root

    monkeypatch.setattr(db, "DATA_LOCATIONS", {
        key: db.DataLocation(key=key, label=label, root=roots[key],
                             env_var=None, description="")
        for key, label in (("desi", "DESI生データ"), ("tims", "TIMS生データ"),
                           ("output", "解析出力"), ("internal", "アプリ内部データ"))
    })

    stray = tmp_path / "stray"
    stray.mkdir()
    roots["stray"] = stray
    return roots


@pytest.fixture
def no_running_analysis(monkeypatch):
    """解析実行中ガードを「実行中なし」に固定する。"""
    import app.services.analysis_runner as ar
    monkeypatch.setattr(ar, "_find_running_job_for_guard", lambda: None)


@pytest.fixture
def projects_store(monkeypatch):
    """projects.json をメモリ上の dict に差し替え、その中身を返す。"""
    data = {"projects": []}
    monkeypatch.setattr(pm, "_projects_lock", contextlib.nullcontext())
    monkeypatch.setattr(pm, "_load_all", lambda: data)
    monkeypatch.setattr(pm, "_save_all", lambda d: data.update(d))
    monkeypatch.setattr(pm, "_safe_current_user", lambda: "tester")
    if hasattr(pm, "_write_meta_to_folder"):
        monkeypatch.setattr(pm, "_write_meta_to_folder", lambda *a, **k: None)
    return data


def _make_result_dir(parent, name="Analysis_20260812_054611"):
    d = parent / name
    (d / "RDS_Files").mkdir(parents=True)
    (d / "RDS_Files" / "step2.rds").write_bytes(b"x" * 2048)
    (d / "umap.png").write_bytes(b"y" * 1024)
    return d


def _write_meta(result_dir, proj_id="proj_1", sub_id="sub_1"):
    import json
    (result_dir / "_project_meta.json").write_text(json.dumps({
        "version": "1.0",
        "project": {"id": proj_id, "name": "P", "experiment_date": "2026-08-12"},
        "sub_project": {"id": sub_id, "name": "S",
                        "last_result_dir": str(result_dir)},
    }), encoding="utf-8")


# ---------------------------------------------------------------------------
# is_persistent_path — 「書けるか」ではなく「消えないか」
# ---------------------------------------------------------------------------

class TestIsPersistentPath:

    def test_location_root_and_subdir_are_persistent(self, locs):
        assert db.is_persistent_path(str(locs["output"]))
        assert db.is_persistent_path(str(locs["output"] / "2026" / "Analysis_x"))
        assert db.is_persistent_path(str(locs["desi"]))

    def test_container_write_layer_is_not_persistent(self, locs):
        """★ 本丸: 書き込みはできるが再ビルドで消える場所を弾けること。"""
        assert not db.is_persistent_path(str(locs["stray"] / "Analysis_x"))

    @pytest.mark.parametrize("value", ["", "   ", None])
    def test_empty_is_not_persistent(self, value, locs):
        assert not db.is_persistent_path(value)


# ---------------------------------------------------------------------------
# preview_move — 移動前のガード
# ---------------------------------------------------------------------------

class TestPreviewMoveGuards:

    def test_empty_source(self, locs):
        assert not db.preview_move("", str(locs["output"]))["ok"]

    def test_relative_source_is_rejected(self, locs):
        r = db.preview_move("relative/dir", str(locs["output"]))
        assert "絶対パス" in r["msg"]

    def test_missing_source_is_rejected(self, locs):
        r = db.preview_move(str(locs["stray"] / "nope"), str(locs["output"]))
        assert "見つかりません" in r["msg"]

    def test_empty_destination(self, locs):
        src = _make_result_dir(locs["stray"])
        assert "移動先が未入力" in db.preview_move(str(src), "")["msg"]

    def test_relative_destination_is_rejected(self, locs):
        src = _make_result_dir(locs["stray"])
        assert "絶対パス" in db.preview_move(str(src), "output/2026")["msg"]

    def test_location_root_cannot_be_moved(self, locs):
        """マウントポイント自体を動かすと復旧が面倒なので弾く。"""
        r = db.preview_move(str(locs["output"]), str(locs["desi"]))
        assert "ルートフォルダは移動できません" in r["msg"]

    def test_already_in_destination(self, locs):
        src = _make_result_dir(locs["output"])
        r = db.preview_move(str(src), str(locs["output"]))
        assert "既に移動先の直下" in r["msg"]

    def test_name_collision_is_not_overwritten(self, locs):
        """★ 上書きしないこと。既存の結果を黙って潰さない。"""
        src = _make_result_dir(locs["stray"])
        (locs["output"] / src.name).mkdir()
        r = db.preview_move(str(src), str(locs["output"]))
        assert "同名の項目" in r["msg"]

    def test_destination_inside_source_is_rejected(self, locs):
        """移動先が移動元の配下（＝自分の中に自分を入れる）を弾く。"""
        outer = locs["output"].parent          # 全ルートの親 = tmp_path
        r = db.preview_move(str(outer), str(locs["output"]))
        assert not r["ok"]
        assert "配下です" in r["msg"], r["msg"]


class TestDestinationMustBeAPersistentLocation:
    """★ 本丸: 参照で自由にパスを選べるようになった分、行き先を絞る。"""

    def test_outside_every_location_is_rejected(self, locs, tmp_path):
        src = _make_result_dir(locs["stray"])
        for bad in (str(locs["stray"]), str(tmp_path), "/tmp"):
            r = db.preview_move(str(src), bad)
            assert not r["ok"], bad
            assert "配下を指定してください" in r["msg"], (bad, r["msg"])

    def test_missing_destination_folder_is_rejected_not_created(self, locs):
        """存在しない移動先は作らない（作成は SFTP 等の責務）。"""
        src = _make_result_dir(locs["stray"])
        missing = locs["output"] / "2026"
        r = db.preview_move(str(src), str(missing))
        assert "移動先フォルダが見つかりません" in r["msg"]
        assert not missing.exists(), "検証で勝手に作ってしまっている"

    @pytest.mark.parametrize("key,label", [
        ("output", "解析出力"), ("desi", "DESI生データ"),
        ("tims", "TIMS生データ"), ("internal", "アプリ内部データ"),
    ])
    def test_destination_label_is_resolved(self, key, label, locs):
        src = _make_result_dir(locs["stray"])
        r = db.preview_move(str(src), str(locs[key]))
        assert r["ok"], r["msg"]
        assert r["dest_key"] == key
        assert r["dest_label"] == label


class TestPreviewMoveSuccess:

    def test_target_and_size(self, locs):
        src = _make_result_dir(locs["stray"])
        r = db.preview_move(str(src), str(locs["output"]))
        assert r["ok"], r["msg"]
        assert r["target"] == str(locs["output"] / src.name)
        assert r["file_count"] == 2          # ディレクトリは数えない
        assert r["used_bytes"] == 2048 + 1024

    def test_nested_destination_is_honoured(self, locs):
        """参照で深い階層を選べること。"""
        src = _make_result_dir(locs["stray"])
        nested = locs["output"] / "2026" / "batch1"
        nested.mkdir(parents=True)
        r = db.preview_move(str(src), str(nested))
        assert r["ok"], r["msg"]
        assert r["target"] == str(nested / src.name)


# ---------------------------------------------------------------------------
# move_entry — 実際に動かす
# ---------------------------------------------------------------------------

class TestMoveEntry:

    def test_moves_and_reports_no_relink_without_meta(
            self, locs, no_running_analysis, projects_store):
        src = _make_result_dir(locs["stray"])
        r = db.move_entry(str(src), str(locs["output"]))
        assert r["ok"], r["msg"]
        assert (locs["output"] / src.name / "umap.png").is_file()
        assert not src.exists()
        assert r["path_updates"] == []

    def test_refuses_while_an_analysis_is_running(
            self, locs, monkeypatch, projects_store):
        """★ 出力先を書いている最中に動かさない。"""
        import app.services.analysis_runner as ar
        monkeypatch.setattr(ar, "_find_running_job_for_guard",
                            lambda: {"analyst": "誰か"})
        src = _make_result_dir(locs["stray"])
        r = db.move_entry(str(src), str(locs["output"]))
        assert not r["ok"]
        assert "実行中" in r["msg"]
        assert src.is_dir(), "拒否したのに動かしてしまっている"

    def test_refuses_when_the_running_check_itself_failed(
            self, locs, monkeypatch, projects_store):
        """★ 探索に失敗したときは「実行中は無い」ではなく拒否側に倒れる。"""
        import app.services.analysis_runner as ar
        monkeypatch.setattr(ar, "_find_running_job_for_guard",
                            lambda: {"_scan_failed": True})
        src = _make_result_dir(locs["stray"])
        r = db.move_entry(str(src), str(locs["output"]))
        assert not r["ok"]
        assert src.is_dir()

    def test_unregistered_project_is_restored(
            self, locs, no_running_analysis, projects_store):
        src = _make_result_dir(locs["stray"], "Analysis_meta")
        _write_meta(src)
        r = db.move_entry(str(src), str(locs["output"]))
        assert r["ok"], r["msg"]
        assert any("復元" in m for m in r["path_updates"]), r["path_updates"]
        sub = pm.get_sub_project("proj_1", "sub_1")
        assert sub and sub["last_result_dir"] == str(locs["output"] / "Analysis_meta")

    def test_registered_project_gets_its_path_updated(
            self, locs, no_running_analysis, projects_store):
        """★ 本丸: 登録済みなら `update_paths` を通り、参照先が新パスになる。

        データ管理サブタブの「↩ 復元」ボタンは `restore` 固定で、既存の
        サブプロジェクトには「スキップ（既存）」を返しパスを更新しない。
        移動はそこを通さず自前で分岐する必要がある。
        """
        src = _make_result_dir(locs["stray"], "Analysis_meta")
        _write_meta(src)
        projects_store["projects"].append({
            "id": "proj_1", "name": "P",
            "sub_projects": [{"id": "sub_1", "name": "S",
                              "last_result_dir": str(src)}],
        })

        r = db.move_entry(str(src), str(locs["output"]))
        assert r["ok"], r["msg"]
        assert any("パス更新" in m for m in r["path_updates"]), r["path_updates"]
        sub = pm.get_sub_project("proj_1", "sub_1")
        assert sub["last_result_dir"] == str(locs["output"] / "Analysis_meta")

    def test_a_rejected_move_leaves_everything_in_place(
            self, locs, no_running_analysis, projects_store):
        src = _make_result_dir(locs["stray"])
        existing = locs["output"] / src.name
        existing.mkdir()
        (existing / "keep.txt").write_text("keep", encoding="utf-8")

        r = db.move_entry(str(src), str(locs["output"]))
        assert not r["ok"]
        assert src.is_dir()
        assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep"

    def test_reports_both_old_and_new_path(
            self, locs, no_running_analysis, projects_store):
        """呼び出し側が「開いているのは移動したフォルダか」を判定できること。"""
        src = _make_result_dir(locs["stray"])
        r = db.move_entry(str(src), str(locs["output"]))
        assert r["old_path"] == str(src)
        assert r["new_path"] == str(locs["output"] / src.name)


# ---------------------------------------------------------------------------
# 開いている結果フォルダの読み替え
# ---------------------------------------------------------------------------

class TestRemapOpenResultFolder:
    """移動後、インタラクティブ解析が開いているパスを差し替える判定。

    プロジェクト ID ではなくパスで見るので、`_project_meta.json` を持たない
    結果フォルダでも効く。
    """

    @staticmethod
    def _remap(*args):
        from app.callbacks.data_management_callbacks import (
            _remap_open_result_folder,
        )
        return _remap_open_result_folder(*args)

    def test_exact_match_is_remapped(self):
        assert self._remap("/app/A", "/app/A", "/app/Data/Other/output/A") == \
            "/app/Data/Other/output/A"

    def test_descendant_keeps_its_suffix(self):
        assert self._remap("/app/A/RDS_Files", "/app/A",
                           "/app/Data/Other/output/A") == \
            "/app/Data/Other/output/A/RDS_Files"

    def test_unrelated_folder_is_left_alone(self):
        """★ 無関係なフォルダを開いていたら触らない。"""
        assert self._remap("/app/B", "/app/A", "/app/Data/Other/output/A") == ""

    def test_prefix_lookalike_is_not_remapped(self):
        """/app/A2 は /app/A の配下ではない（文字列前方一致で誤爆しないこと）。"""
        assert self._remap("/app/A2", "/app/A", "/app/Data/Other/output/A") == ""

    @pytest.mark.parametrize("cur,old,new", [
        ("", "/app/A", "/app/out/A"),
        ("/app/A", "", "/app/out/A"),
        ("/app/A", "/app/A", ""),
    ])
    def test_missing_inputs_are_left_alone(self, cur, old, new):
        assert self._remap(cur, old, new) == ""


class TestAutoLoadIsSkippedRightAfterAMove:
    """★ 差替えたパスで自動読込まで走らせない。

    結果フォルダが変わると auto_scan_rds_files → auto_load_on_rds_ready と連鎖し、
    entry_mode が sub_project / shared なら読込まで自動で走る。移動直後は Seurat
    抽出キャッシュがミスして数分かかるので、設定タブにいる利用者の意図しない
    ところで始めない。
    """

    @staticmethod
    def _call(skip):
        from app.callbacks.interactive_callbacks import auto_load_on_rds_ready
        return auto_load_on_rds_ready(
            {"Harmony": "/x/step3.rds"}, "Harmony", "sub_project", 3, skip,
        )

    def test_flag_suppresses_autoload_and_is_consumed(self):
        from dash import no_update
        n_clicks, flag = self._call(True)
        assert n_clicks is no_update, "自動読込を止められていない"
        assert flag is False, "フラグが 1 回で消費されていない"

    def test_without_the_flag_autoload_still_runs(self):
        n_clicks, _flag = self._call(False)
        assert n_clicks == 4, "通常のサブプロジェクト遷移で自動読込が止まっている"
