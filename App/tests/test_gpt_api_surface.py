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
