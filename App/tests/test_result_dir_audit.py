"""結果フォルダの健全性チェックと、全ロケーション横断スキャン（ver56.2）。

■ なぜ要るか

出力先が `/app` 直下（コンテナの書き込み層）のまま解析した結果が、
`docker compose up -d --build` でコンテナが作り直された時点で消えた。

厄介だったのは**画面上どこにも異常が出なかった**こと。実体が消えても
projects.json の登録は残り、インタラクティブ解析の結果フォルダ欄も古いパスを
表示し続けるので、次に開こうとするまで誰も気づけない。

`audit_result_dirs()` は再ビルド前に気づくための表で、
`find_meta_projects_everywhere()` は「解析出力しか見ていなかった」復元スキャンの穴を塞ぐ。
実運用では結果は生データフォルダの隣に置かれており、登録済み 33 件のうち
32 件が従来のスキャン対象外だった。
"""

import contextlib
import json

import pytest

import app.services.data_browser as db
import app.services.project_manager as pm


def _locations(monkeypatch, roots: dict):
    """DATA_LOCATIONS を差し替える（辞書の順序がそのまま探索順になる）。"""
    labels = {"desi": "DESI生データ", "tims": "TIMS生データ",
              "output": "解析出力", "internal": "アプリ内部データ"}
    monkeypatch.setattr(db, "DATA_LOCATIONS", {
        key: db.DataLocation(key=key, label=labels[key], root=root,
                             env_var=None, description="")
        for key, root in roots.items()
    })


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """本番と同じ入れ子（内部データ ⊃ 解析出力）を再現する。

    `/app/Data/Other` が `/app/Data/Other/output` を含むため、素直に 4 か所を
    走査すると同じフォルダが 2 回拾われる。そこを畳めているかを見たい。
    """
    desi = tmp_path / "Data" / "DESI" / "Data"
    tims = tmp_path / "Data" / "TIMS" / "Data"
    internal = tmp_path / "Data" / "Other"
    output = internal / "output"
    for p in (desi, tims, output):
        p.mkdir(parents=True)
    stray = tmp_path / "app_layer"          # 書き込み層の見立て
    stray.mkdir()

    _locations(monkeypatch, {
        "desi": desi, "tims": tims, "output": output, "internal": internal,
    })
    return {"desi": desi, "tims": tims, "output": output,
            "internal": internal, "stray": stray}


@pytest.fixture
def projects(monkeypatch):
    data = {"projects": []}
    monkeypatch.setattr(pm, "_projects_lock", contextlib.nullcontext())
    monkeypatch.setattr(pm, "_load_all", lambda: data)
    monkeypatch.setattr(pm, "_save_all", lambda d: data.update(d))
    return data


def _add_sub(projects, proj_name, sub_name, **fields):
    for p in projects["projects"]:
        if p["name"] == proj_name:
            p["sub_projects"].append(dict(id=sub_name, name=sub_name, **fields))
            return
    projects["projects"].append({
        "id": proj_name, "name": proj_name,
        "sub_projects": [dict(id=sub_name, name=sub_name, **fields)],
    })


def _write_meta(result_dir, proj_id, sub_id):
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "_project_meta.json").write_text(json.dumps({
        "version": "1.0",
        "project": {"id": proj_id, "name": proj_id},
        "sub_project": {"id": sub_id, "name": sub_id},
    }), encoding="utf-8")


# ---------------------------------------------------------------------------
# audit_result_dirs
# ---------------------------------------------------------------------------

class TestAuditResultDirs:

    def test_persistent_and_existing_is_not_reported(self, layout, projects):
        good = layout["tims"] / "260802_Clinical_HCC" / "UMAP1"
        good.mkdir(parents=True)
        _add_sub(projects, "P", "S", last_result_dir=str(good))
        assert db.audit_result_dirs() == []

    def test_missing_folder_is_reported(self, layout, projects):
        """★ 今回の事故。登録は残っているが実体が無い。"""
        gone = layout["stray"] / "Analysis_20260812_054611_nn10_md0p3_dim30"
        _add_sub(projects, "HCC", "260811_Partial", last_result_dir=str(gone))

        rows = db.audit_result_dirs()
        assert len(rows) == 1
        assert rows[0]["state"] == "missing"
        assert rows[0]["project_name"] == "HCC"
        assert rows[0]["sub_name"] == "260811_Partial"
        assert rows[0]["path"] == str(gone)

    def test_existing_but_volatile_is_reported(self, layout, projects):
        """★ 再ビルド前に気づきたいのはこれ。まだ間に合う状態。"""
        risky = layout["stray"] / "Analysis_20260812_054611"
        risky.mkdir()
        _add_sub(projects, "P", "S", last_result_dir=str(risky))

        rows = db.audit_result_dirs()
        assert [r["state"] for r in rows] == ["volatile"]

    def test_falls_back_to_output_dir(self, layout, projects):
        risky = layout["stray"] / "out"
        risky.mkdir()
        _add_sub(projects, "P", "S", output_dir=str(risky))
        assert [r["state"] for r in db.audit_result_dirs()] == ["volatile"]

    @pytest.mark.parametrize("fields", [
        {},                                   # まだ解析していない
        {"last_result_dir": ""},
        {"last_result_dir": "   "},
    ])
    def test_unanalysed_sub_projects_are_quiet(self, fields, layout, projects):
        """結果がまだ無いだけのサブプロジェクトを警告に混ぜない。"""
        _add_sub(projects, "P", "S", **fields)
        assert db.audit_result_dirs() == []

    def test_reports_every_offender(self, layout, projects):
        gone = layout["stray"] / "gone"
        risky = layout["stray"] / "risky"
        risky.mkdir()
        safe = layout["output"] / "safe"
        safe.mkdir()
        _add_sub(projects, "P1", "S1", last_result_dir=str(gone))
        _add_sub(projects, "P1", "S2", last_result_dir=str(risky))
        _add_sub(projects, "P2", "S3", last_result_dir=str(safe))

        rows = db.audit_result_dirs()
        assert sorted(r["sub_name"] for r in rows) == ["S1", "S2"]


# ---------------------------------------------------------------------------
# find_meta_projects_everywhere
# ---------------------------------------------------------------------------

class TestFindMetaProjectsEverywhere:

    def test_finds_results_next_to_raw_data(self, layout):
        """★ 本丸。実運用の置き場所（生データの隣）を拾えること。"""
        _write_meta(layout["tims"] / "260802_Clinical_HCC" / "UMAP1", "P", "S1")
        _write_meta(layout["desi"] / "250621_Ohashi" / "UMAP1", "P", "S2")

        found = db.find_meta_projects_everywhere()
        assert sorted(m["sub_project"]["id"] for m in found) == ["S1", "S2"]

    def test_labels_where_it_was_found(self, layout):
        _write_meta(layout["tims"] / "x" / "UMAP1", "P", "S1")
        found = db.find_meta_projects_everywhere()
        assert found[0]["_found_location"] == "TIMS生データ"

    def test_overlapping_roots_do_not_duplicate(self, layout):
        """解析出力は内部データの配下。2 回拾って重複させない。"""
        _write_meta(layout["output"] / "UMAP1", "P", "S1")

        found = db.find_meta_projects_everywhere()
        assert len(found) == 1, [m["_found_dir"] for m in found]
        # 先に走査される「解析出力」のラベルが付く
        assert found[0]["_found_location"] == "解析出力"

    def test_nothing_registered_returns_empty(self, layout):
        assert db.find_meta_projects_everywhere() == []
