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
        assert not db.preview_move("", "output")["ok"]

    def test_relative_source_is_rejected(self, locs):
        assert "絶対パス" in db.preview_move("relative/dir", "output")["msg"]

    def test_missing_source_is_rejected(self, locs):
        r = db.preview_move(str(locs["stray"] / "nope"), "output")
        assert "見つかりません" in r["msg"]

    def test_unknown_destination_key_is_rejected(self, locs):
        assert not db.preview_move(str(locs["stray"]), "bogus")["ok"]

    def test_location_root_cannot_be_moved(self, locs):
        """マウントポイント自体を動かすと復旧が面倒なので弾く。"""
        r = db.preview_move(str(locs["output"]), "desi")
        assert "ルートフォルダは移動できません" in r["msg"]

    def test_already_in_destination(self, locs):
        src = _make_result_dir(locs["output"])
        assert "既に移動先の直下" in db.preview_move(str(src), "output")["msg"]

    def test_name_collision_is_not_overwritten(self, locs):
        """★ 上書きしないこと。既存の結果を黙って潰さない。"""
        src = _make_result_dir(locs["stray"])
        (locs["output"] / src.name).mkdir()
        assert "同名の項目" in db.preview_move(str(src), "output")["msg"]

    def test_destination_inside_source_is_rejected(self, locs):
        """移動先が移動元の配下（＝自分の中に自分を入れる）を弾く。"""
        outer = locs["output"].parent          # 全ルートの親 = tmp_path
        r = db.preview_move(str(outer), "output")
        assert not r["ok"]
        assert "配下です" in r["msg"], r["msg"]


class TestPreviewMoveSuccess:

    def test_target_and_size(self, locs):
        src = _make_result_dir(locs["stray"])
        r = db.preview_move(str(src), "output")
        assert r["ok"], r["msg"]
        assert r["target"] == str(locs["output"] / src.name)
        assert r["file_count"] == 2          # ディレクトリは数えない
        assert r["used_bytes"] == 2048 + 1024

    def test_subfolder_is_honoured(self, locs):
        src = _make_result_dir(locs["stray"])
        (locs["output"] / "2026").mkdir()
        r = db.preview_move(str(src), "output", "2026")
        assert r["target"] == str(locs["output"] / "2026" / src.name)

    def test_absolute_subpath_cannot_escape_the_root(self, locs):
        """★ `_safe_resolve` によりルート外を指定してもルートへ丸められる。"""
        src = _make_result_dir(locs["stray"])
        r = db.preview_move(str(src), "output", "/etc")
        assert r["target"].startswith(str(locs["output"]))


# ---------------------------------------------------------------------------
# move_entry — 実際に動かす
# ---------------------------------------------------------------------------

class TestMoveEntry:

    def test_moves_and_reports_no_relink_without_meta(
            self, locs, no_running_analysis, projects_store):
        src = _make_result_dir(locs["stray"])
        r = db.move_entry(str(src), "output")
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
        r = db.move_entry(str(src), "output")
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
        r = db.move_entry(str(src), "output")
        assert not r["ok"]
        assert src.is_dir()

    def test_unregistered_project_is_restored(
            self, locs, no_running_analysis, projects_store):
        src = _make_result_dir(locs["stray"], "Analysis_meta")
        _write_meta(src)
        r = db.move_entry(str(src), "output")
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

        r = db.move_entry(str(src), "output")
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

        r = db.move_entry(str(src), "output")
        assert not r["ok"]
        assert src.is_dir()
        assert (existing / "keep.txt").read_text(encoding="utf-8") == "keep"
