"""ver53.0: 非同期ジョブと API の宣言面 (仕様書) を固定する。

■ ここで直している欠陥

(A) 状態問い合わせが「**ファイルが出来ていたら done**」だけで判定していた。
    成果物ファイルを作らない `warmup` を同じ窓口に載せると **永遠に running**
    を返す。ジョブ記録に `kind` を持たせて分ける。

    同じ判定の副作用として、export が完了したあと一時ファイルが掃除される
    (`sweep_old_files` が 1 時間で消す) と、**pct 100 のまま running** を
    返し続けていた。利用者は永久に待つことになる。

(B) 仕様書 (`build_openapi_spec`) が **10 operation** しか宣言していないのに
    実ルートは **13 本**。`download` / `downloadExportJob` が載っておらず、
    **`listOutputs` が `download_token` を返すのに、それを使う経路が
    仕様書に無い**という行き止まりになっていた。
    モデルが読むのは仕様書だけなので、宣言されていない経路は存在しない。

(C) `pid` / `sid` に説明が 1 文字も無く (12 箇所すべて `desc` 空)、
    モデルが連番と誤解して `sid=1` を送り 404 になった（実障害）。

(D) `_resolve_sub` は「プロジェクトが無い」と「サブが無い」の両方で None を
    返し、呼び出し側 6 箇所すべてが同じ 1 文を返していた。しかも 3 箇所は
    `NOT_FOUND` コードすら付いていない。同じファイルの `_pick_method` は
    409 で `available_methods` を返す**良い前例**があるのに、そこだけ揃って
    いなかった。
"""

import flask
import pytest

from app.services import export_progress as ep
from app.services import gpt_api as g

KEY = "k" * 64


def _client(monkeypatch):
    monkeypatch.setattr("app.config.SHARE_BASE_URL", "", raising=False)
    monkeypatch.setattr("app.config.GPT_API_KEY", KEY, raising=False)
    app = flask.Flask(__name__)
    g.register_gpt_api(app)
    return app.test_client()


def _get(client, path):
    return client.get(path, headers={"X-API-Key": KEY})


# ===========================================================================
# (A) ジョブ種別
# ===========================================================================
class TestJobKind:

    def test_default_kind_is_export(self):
        """既存の `new_job()` 呼び出しが挙動を変えないこと。"""
        jid = ep.new_job()
        try:
            assert ep.get_job(jid)["kind"] == "export"
        finally:
            ep.pop_job(jid)

    def test_warmup_kind_is_recorded(self):
        jid = ep.new_job("warmup")
        try:
            assert ep.get_job(jid)["kind"] == "warmup"
        finally:
            ep.pop_job(jid)

    def test_finish_without_a_file_keeps_paths_none(self):
        """★ ファイルを作らないジョブでも完了を記録できること。

        従来は `filepath` 必須で `str(filepath)` していたので、None を渡すと
        **"None" という文字列のパス**が入ってしまう形だった。
        """
        jid = ep.new_job("warmup")
        try:
            ep.finish_job(jid, msg="暖め完了")
            j = ep.get_job(jid)
            assert j["status"] == "done" and j["pct"] == 100
            assert j["filepath"] is None, j["filepath"]
            assert j["filename"] is None, j["filename"]
            assert j["msg"] == "暖め完了"
        finally:
            ep.pop_job(jid)

    def test_finish_with_a_file_still_works(self, tmp_path):
        """既存の 4 引数呼び出しがそのまま通ること（後方互換）。"""
        p = tmp_path / "x.csv"
        p.write_text("a", encoding="utf-8")
        jid = ep.new_job()
        try:
            ep.finish_job(jid, str(p), "x.csv", "完了")
            j = ep.get_job(jid)
            assert j["filepath"] == str(p) and j["filename"] == "x.csv"
        finally:
            ep.pop_job(jid)


class TestJobStatusEndpoint:
    """★ 本丸: `warmup` が done になること。"""

    def test_a_finished_warmup_reports_done(self, monkeypatch):
        """★★ 修正前はここが **永遠に running** だった。

        ファイルの有無だけで done を判定していたため、ファイルを作らない
        warmup は 100% 完了しても running のままになる。
        """
        jid = ep.new_job("warmup")
        try:
            ep.finish_job(jid, msg="暖め完了")
            body = _get(_client(monkeypatch),
                        f"/api/gpt/exports/jobs/{jid}").get_json()
            assert body["status"] == "done", body
            assert body["kind"] == "warmup"
            assert body["pct"] == 100
            assert "download_url" not in body, "ファイルの無いジョブに DL URL がある"
        finally:
            ep.pop_job(jid)

    def test_a_running_warmup_reports_running(self, monkeypatch):
        jid = ep.new_job("warmup")
        try:
            ep.update_job(jid, 40, "RDS 展開中…")
            body = _get(_client(monkeypatch),
                        f"/api/gpt/exports/jobs/{jid}").get_json()
            assert body["status"] == "running" and body["pct"] == 40
            assert body["label"] == "RDS 展開中…"
        finally:
            ep.pop_job(jid)

    def test_a_failed_warmup_reports_error(self, monkeypatch):
        jid = ep.new_job("warmup")
        try:
            ep.fail_job(jid, "RDS が読めません")
            body = _get(_client(monkeypatch),
                        f"/api/gpt/exports/jobs/{jid}").get_json()
            assert body["status"] == "error"
            assert "RDS が読めません" in body["message"]
        finally:
            ep.pop_job(jid)

    def test_an_export_whose_file_expired_is_not_reported_as_running(
            self, monkeypatch):
        """★ 完了したのにファイルが掃除された export。

        従来はこの経路が最後の return に落ち、**pct 100 のまま running** を
        返し続けていた。利用者は永久に待つ。
        """
        jid = ep.new_job("export")
        try:
            ep.finish_job(jid, "/nonexistent/gone.csv", "gone.csv", "完了")
            body = _get(_client(monkeypatch),
                        f"/api/gpt/exports/jobs/{jid}").get_json()
            assert body["status"] == "done", body
            assert body.get("expired") is True
            assert "保持期限" in body["message"], body["message"]
        finally:
            ep.pop_job(jid)

    def test_unknown_job_is_404(self, monkeypatch):
        r = _get(_client(monkeypatch), "/api/gpt/exports/jobs/" + "a" * 32)
        assert r.status_code == 404

    def test_bad_job_id_is_404(self, monkeypatch):
        r = _get(_client(monkeypatch), "/api/gpt/exports/jobs/not-hex")
        assert r.status_code == 404


# ===========================================================================
# warmup: 対象手法と冪等性（純関数）
# ===========================================================================
class TestResolveWarmupMethods:
    """★ export と違い、省略時は **主手法 1 つだけ**。

    全手法を暖めると単純に 3 倍かかる（1 手法で実測 233.7 秒）。
    """

    RDS = {"Harmony": "/h.rds", "RPCA": "/r.rds", "PCA": "/p.rds"}

    def test_omitted_picks_only_the_primary(self):
        sel, err = g.resolve_warmup_methods(None, self.RDS)
        assert err is None
        assert sel == ["Harmony"], f"省略時に {sel} を暖めようとしている"

    def test_explicit_single(self):
        sel, err = g.resolve_warmup_methods("RPCA", self.RDS)
        assert err is None and sel == ["RPCA"]

    def test_explicit_multiple(self):
        sel, err = g.resolve_warmup_methods("Harmony,PCA", self.RDS)
        assert err is None and set(sel) == {"Harmony", "PCA"}

    def test_case_is_absorbed(self):
        sel, err = g.resolve_warmup_methods("harmony", self.RDS)
        assert err is None and sel == ["Harmony"]

    def test_unknown_method_is_rejected(self):
        """★ 黙って全手法へ膨らませない（ver52.1 で export に入れた方針）。"""
        sel, err = g.resolve_warmup_methods("NOPE", self.RDS)
        assert sel is None and err is not None

    def test_no_result_is_409(self):
        sel, err = g.resolve_warmup_methods(None, {})
        assert sel is None and err is not None and err.status == 409


class TestWarmupTargets:
    """★ 冪等性の判定。既に暖まっているのに R を起動すると 4 分待たされる。"""

    def _cache(self, tmp_path, name, *, expression=False):
        d = tmp_path / name
        d.mkdir()
        (d / "extraction_meta.json").write_text("{}", encoding="utf-8")
        (d / "cluster_stats.csv").write_text("cluster\n0\n", encoding="utf-8")
        (d / "plot_data.parquet").write_bytes(b"x")
        if expression:
            (d / "expression_matrix.parquet").write_bytes(b"x")
        return d

    def test_cold_is_a_target(self, tmp_path, monkeypatch):
        monkeypatch.setattr(g, "_warm_cache_dir", lambda rds: None)
        assert g.warmup_targets({"Harmony": "/h.rds"}, ["Harmony"], False) == ["Harmony"]

    def test_warm_is_not_a_target(self, tmp_path, monkeypatch):
        d = self._cache(tmp_path, "h")
        monkeypatch.setattr(g, "_warm_cache_dir", lambda rds: d)
        assert g.warmup_targets({"Harmony": "/h.rds"}, ["Harmony"], False) == []

    def test_warm_without_expression_is_a_target_when_expression_requested(
            self, tmp_path, monkeypatch):
        """★★ 本丸。基本キャッシュが在っても発現行列は別。

        `extract_data` は `_is_cached`（plot_data + cluster_stats + meta）で
        早期 return するので、後から with_expression=True で呼び直しても
        **expression_matrix.parquet は作られない**。ここを同一視すると
        「暖めたつもりで暖まっていない」まま ver54.0 の数値取得へ進む。
        """
        d = self._cache(tmp_path, "h", expression=False)
        monkeypatch.setattr(g, "_warm_cache_dir", lambda rds: d)
        assert g.warmup_targets({"Harmony": "/h.rds"}, ["Harmony"], True) == ["Harmony"]

    def test_warm_with_expression_is_not_a_target(self, tmp_path, monkeypatch):
        d = self._cache(tmp_path, "h", expression=True)
        monkeypatch.setattr(g, "_warm_cache_dir", lambda rds: d)
        assert g.warmup_targets({"Harmony": "/h.rds"}, ["Harmony"], True) == []

    def test_only_the_cold_ones_are_returned(self, tmp_path, monkeypatch):
        warm = self._cache(tmp_path, "h")
        monkeypatch.setattr(
            g, "_warm_cache_dir", lambda rds: warm if rds == "/h.rds" else None)
        got = g.warmup_targets({"Harmony": "/h.rds", "RPCA": "/r.rds"},
                               ["Harmony", "RPCA"], False)
        assert got == ["RPCA"]

    def test_missing_rds_is_skipped(self, monkeypatch):
        monkeypatch.setattr(g, "_warm_cache_dir", lambda rds: None)
        assert g.warmup_targets({}, ["Harmony"], False) == []


class TestParseBoolFlag:
    """★ 綴り違いを黙って既定値に落とさない。

    `with_expression=yes` を False にすると、利用者は発現行列を暖めたつもりで
    暖まっていないまま次へ進み、原因の分からない失敗になる。
    """

    @pytest.mark.parametrize("raw,expect", [
        ("true", True), ("TRUE", True), ("1", True), ("yes", True),
        ("false", False), ("0", False), ("no", False),
    ])
    def test_accepted(self, raw, expect):
        val, err = g.parse_bool_flag(raw, "with_expression")
        assert err is None and val is expect

    @pytest.mark.parametrize("raw", ["y", "on", "maybe", "2", "-1"])
    def test_rejected_with_422(self, raw):
        val, err = g.parse_bool_flag(raw, "with_expression")
        assert val is None and err is not None and err.status == 422

    def test_omitted_uses_the_default(self):
        assert g.parse_bool_flag(None, "with_expression")[0] is False
        assert g.parse_bool_flag("", "with_expression", default=True)[0] is True


class TestWarmupEndpoint:

    def _post(self, client, path):
        return client.post(path, headers={"X-API-Key": KEY})

    def _resolved(self, monkeypatch, rds_map):
        monkeypatch.setattr(g, "_resolve_sub", lambda pid, sid: {
            "project": {"id": "p"}, "sub": {"id": "s"}, "result_dir": "/r",
            "data_folder": None, "ms_instrument": "TIMS", "rds_map": rds_map})

    def test_already_warm_returns_done_without_starting_r(
            self, tmp_path, monkeypatch):
        """★★ 冪等。R を起動せず即答すること。"""
        d = tmp_path / "h"
        d.mkdir()
        for f in ("extraction_meta.json", "cluster_stats.csv", "plot_data.parquet"):
            (d / f).write_bytes(b"{}")
        self._resolved(monkeypatch, {"Harmony": "/h.rds"})
        monkeypatch.setattr(g, "_warm_cache_dir", lambda rds: d)
        started = []
        monkeypatch.setattr(g.threading, "Thread",
                            lambda *a, **k: started.append(1))

        body = self._post(_client(monkeypatch),
                          "/api/gpt/projects/p/sub/s/warmup").get_json()
        assert body["status"] == "done" and body["already_warm"] is True
        assert not started, "既に暖まっているのにスレッドを起こしている"

    def test_cold_starts_a_warmup_job(self, monkeypatch):
        self._resolved(monkeypatch, {"Harmony": "/h.rds"})
        monkeypatch.setattr(g, "_warm_cache_dir", lambda rds: None)

        class _T:
            def __init__(self, *a, **k):
                pass

            def start(self):
                pass

        monkeypatch.setattr(g.threading, "Thread", _T)
        body = self._post(_client(monkeypatch),
                          "/api/gpt/projects/p/sub/s/warmup").get_json()
        assert body["status"] == "running"
        assert body["status_url"].startswith("/api/gpt/exports/jobs/")
        assert body["methods"] == ["Harmony"]
        from app.services import export_progress as ep
        try:
            assert ep.get_job(body["job_id"])["kind"] == "warmup"
        finally:
            ep.pop_job(body["job_id"])
            g._GPT_EXPORT_SEM.release()

    def test_unknown_sub_is_404(self, monkeypatch):
        monkeypatch.setattr(g, "_resolve_sub", lambda pid, sid: None)
        r = self._post(_client(monkeypatch), "/api/gpt/projects/p/sub/s/warmup")
        assert r.status_code == 404

    def test_bad_flag_is_422(self, monkeypatch):
        self._resolved(monkeypatch, {"Harmony": "/h.rds"})
        r = self._post(
            _client(monkeypatch),
            "/api/gpt/projects/p/sub/s/warmup?with_expression=maybe")
        assert r.status_code == 422, r.get_json()

    def test_get_is_not_allowed(self, monkeypatch):
        """★ 副作用のある操作を GET に載せない。"""
        self._resolved(monkeypatch, {"Harmony": "/h.rds"})
        r = _get(_client(monkeypatch), "/api/gpt/projects/p/sub/s/warmup")
        assert r.status_code == 405
