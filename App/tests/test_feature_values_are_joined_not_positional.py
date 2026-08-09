"""ver54.0: m/z の数値を GPT から取れるようにした。**位置ではなく CellID で突合する**。

■ なぜこの番人が最優先か

ver52.5 で見つけた欠陥がここに直結する。発現行列 (`expression_matrix.parquet`) と
座標・クラスタ (`plot_data.parquet`) を **位置で対応づけて長さだけ検査する**
実装は、行順がずれると「もっともらしい別の場所の値」を返す。

画面 (Feature plot) なら「なんか変だ」と気づけた。しかし **API 経由では
利用者が元データを見ないので絶対に気づけない**。GPT は返ってきた数値を
そのまま「クラスタ 1 の平均は 8.2」と要約する。

だから ver54.0 は最初から `merge(on="CellID")` で組む。Heatmap 側
(`interactive_deg.py:1415`) が既にそうしているので、それに揃えた形。
CellID が無い古い抽出では **数値を返さず失敗する**（位置に落ちない）。

■ もう 1 つの型: 黙って削らない

全ピクセル (実測 203,078) は JSON で 5〜6 MB になり、Actions の上限
(90,000 文字) の約 60 倍。削るのは避けられないので、**削った事実を必ず言う**。
黙って先頭 N 件だけ返すと「全部でこれだけ」と読まれる。
"""

import json

import flask
import pandas as pd
import pytest

from app.services import gpt_api as g

KEY = "k" * 64
CELLS = [f"c{i}" for i in range(10)]
# クラスタ 1 が高く、クラスタ 5 が低い。突合がずれれば必ず壊れる並びにする。
CLUSTERS = ["1"] * 5 + ["5"] * 5
VALUES = [10.0, 12.0, 11.0, 13.0, 14.0, 0.0, 1.0, 0.0, 2.0, 0.0]
FEATURE = "mz_800.5000"


def _write_cache(d, *, cell_ids=CELLS, expr_cell_ids=None, expr_order=None,
                 with_cellid=True, with_expression=True):
    """抽出キャッシュ一式を作る。

    `expr_order`    … 発現行列側の**行順だけ**を変える（位置対応の検出用）
    `expr_cell_ids` … 発現行列側の **ID を変える**（突合できない行の検出用）
    """
    d.mkdir(parents=True, exist_ok=True)
    (d / "extraction_meta.json").write_text("{}", encoding="utf-8")
    (d / "cluster_stats.csv").write_text("cluster\n1\n5\n", encoding="utf-8")
    pd.DataFrame({
        "CellID": list(cell_ids), "Cluster": CLUSTERS, "Sample": ["S1"] * 10,
        "SpatialX": list(range(10)), "SpatialY": list(range(10)),
    }).to_parquet(d / "plot_data.parquet", index=False)
    if with_expression:
        ids = list(expr_cell_ids if expr_cell_ids is not None else cell_ids)
        order = expr_order if expr_order is not None else list(range(len(ids)))
        cols = {FEATURE: [VALUES[i] for i in order],
                "mz_900.0000": [0.0] * len(order)}
        if with_cellid:
            cols = {"CellID": [ids[i] for i in order], **cols}
        pd.DataFrame(cols).to_parquet(d / "expression_matrix.parquet", index=False)
    return d


def _client(monkeypatch, cache_dir):
    monkeypatch.setattr("app.config.SHARE_BASE_URL", "", raising=False)
    monkeypatch.setattr("app.config.GPT_API_KEY", KEY, raising=False)
    monkeypatch.setattr(g, "_resolve_sub", lambda pid, sid: {
        "project": {"id": "p"}, "sub": {"id": "s"}, "result_dir": "/r",
        "data_folder": None, "ms_instrument": "TIMS",
        "rds_map": {"Harmony": "/h.rds"}})
    monkeypatch.setattr(g, "_warm_cache_dir", lambda rds: cache_dir)
    app = flask.Flask(__name__)
    g.register_gpt_api(app)
    return app.test_client()


def _get(client, path):
    return client.get(path, headers={"X-API-Key": KEY})


# ===========================================================================
# ★★ 本丸: 位置ではなく CellID で突合する
# ===========================================================================
class TestValuesAreJoinedByCellId:

    def _stats(self, body):
        return {r["cluster"]: r for r in body["clusters"]}

    def test_aligned_gives_the_right_means(self, tmp_path, monkeypatch):
        d = _write_cache(tmp_path / "c")
        body = _get(_client(monkeypatch, d),
                    "/api/gpt/projects/p/sub/s/features/stats?mz=800.5").get_json()
        s = self._stats(body)
        assert s["1"]["n"] == 5 and s["5"]["n"] == 5
        assert s["1"]["mean"] == pytest.approx(12.0)
        assert s["5"]["mean"] == pytest.approx(0.6)

    def test_shuffled_expression_still_gives_the_right_means(
            self, tmp_path, monkeypatch):
        """★★ ここが本丸。

        発現行列の行順だけを逆にする。**位置で対応づける実装なら、
        クラスタ 1 と 5 の平均が入れ替わる**（12.0 と 0.6 が逆になる）。
        CellID で突合していれば、行順が何であろうと答えは変わらない。
        """
        d = _write_cache(tmp_path / "c", expr_order=list(range(9, -1, -1)))
        body = _get(_client(monkeypatch, d),
                    "/api/gpt/projects/p/sub/s/features/stats?mz=800.5").get_json()
        s = self._stats(body)
        assert s["1"]["mean"] == pytest.approx(12.0), (
            "行順を変えたら平均が変わった＝位置で対応づけている")
        assert s["5"]["mean"] == pytest.approx(0.6)

    def test_shuffled_expression_does_not_change_raw_values_either(
            self, tmp_path, monkeypatch):
        """生値のほうも同じ。x/y と value の組が崩れないこと。"""
        out = []
        for order in (None, list(range(9, -1, -1))):
            d = _write_cache(tmp_path / f"c{order is None}", expr_order=order)
            body = _get(
                _client(monkeypatch, d),
                "/api/gpt/projects/p/sub/s/features/values?mz=800.5&limit=50"
            ).get_json()
            out.append({(r["x"], r["y"]): r["value"] for r in body["values"]})
        assert out[0] == out[1], "行順を変えたら座標と値の組が変わった"

    def test_no_cellid_fails_instead_of_guessing(self, tmp_path, monkeypatch):
        """★★ 突合材料が無いなら**数値を返さない**。

        位置で対応づけて返すと、利用者は元データを見ないので
        間違いに気づけない。黙って別の場所の値を返すより失敗させる。
        """
        d = _write_cache(tmp_path / "c", with_cellid=False)
        r = _get(_client(monkeypatch, d),
                 "/api/gpt/projects/p/sub/s/features/stats?mz=800.5")
        assert r.status_code == 409, r.get_json()
        body = r.get_json()
        assert body["code"] == "NO_CELL_ID"
        assert "clusters" not in body or not body.get("clusters")

    def test_partial_match_is_reported(self, tmp_path, monkeypatch):
        """★ 内側結合で落ちた行を黙らない。

        一部しか突合できていないのに「クラスタの統計」として返すと、
        利用者は全ピクセルの統計だと読む。
        """
        d = _write_cache(
            tmp_path / "c",
            expr_cell_ids=[f"c{i}" for i in range(5)] + [f"x{i}" for i in range(5)])
        body = _get(_client(monkeypatch, d),
                    "/api/gpt/projects/p/sub/s/features/stats?mz=800.5").get_json()
        # plot_data 側は c0..c9、発現行列側は c0..c4 + x0..x4 → 5 行だけ一致
        assert body["n_matched"] == 5, body
        assert "unmatched_note" in body, body


# ===========================================================================
# m/z の解決（純関数）
# ===========================================================================
class TestResolveFeatureColumn:

    NAMES = {"CellID", "mz_800.5000", "mz_800.5100", "mz_900.0000", "junk"}

    def test_picks_the_nearest(self):
        col, n = g.resolve_feature_column(self.NAMES, 800.50, 0.005)
        assert col == "mz_800.5000" and n == 1

    def test_reports_multiple_candidates(self):
        """★ tol が広すぎて複数を巻き込んだことを言う。1 本選んで黙らない。"""
        col, n = g.resolve_feature_column(self.NAMES, 800.505, 0.02)
        assert col in ("mz_800.5000", "mz_800.5100")
        assert n == 2, "複数該当を報告していない"

    def test_none_when_out_of_tol(self):
        col, n = g.resolve_feature_column(self.NAMES, 500.0, 0.01)
        assert col is None and n == 0

    def test_cellid_is_never_selected(self):
        col, _ = g.resolve_feature_column({"CellID"}, 0.0, 1e9)
        assert col is None

    def test_unparsable_names_are_skipped(self):
        col, _ = g.resolve_feature_column({"junk", "総イオン"}, 100.0, 1e9)
        assert col is None

    def test_is_deterministic_on_ties(self):
        """同距離のとき毎回同じものを選ぶこと（結果が呼ぶたびに変わらない）。"""
        names = {"mz_100.0000", "mz_100.0200"}
        picks = {g.resolve_feature_column(names, 100.01, 0.02)[0] for _ in range(5)}
        assert len(picks) == 1, picks


class TestClusterStatsMath:
    """★ 統計値が手計算と一致すること。"""

    def _frames(self):
        expr = pd.DataFrame({"CellID": CELLS, FEATURE: VALUES})
        plot = pd.DataFrame({"CellID": CELLS, "Cluster": CLUSTERS})
        return expr, plot

    def test_values_match_hand_calculation(self):
        recs, meta = g.cluster_stats_from_frames(*self._frames(), FEATURE)
        s = {r["cluster"]: r for r in recs}
        assert s["1"]["n"] == 5
        assert s["1"]["mean"] == pytest.approx(12.0)
        assert s["1"]["median"] == pytest.approx(12.0)
        assert s["1"]["detected_pct"] == pytest.approx(100.0)
        assert s["5"]["mean"] == pytest.approx(0.6)
        assert s["5"]["median"] == pytest.approx(0.0)
        # 5 件中 0 でないのは 2 件
        assert s["5"]["detected_pct"] == pytest.approx(40.0)
        assert meta["n_matched"] == 10

    def test_sd_is_none_for_a_single_cell(self):
        """★ 標本標準偏差は n=1 で NaN。0.0 に潰すと「ばらつきが無い」と読める。"""
        expr = pd.DataFrame({"CellID": ["a"], FEATURE: [3.0]})
        plot = pd.DataFrame({"CellID": ["a"], "Cluster": ["1"]})
        recs, _ = g.cluster_stats_from_frames(expr, plot, FEATURE)
        assert recs[0]["sd"] is None, recs[0]

    def test_clusters_are_sorted_like_the_screen(self):
        expr = pd.DataFrame({"CellID": list("abcd"), FEATURE: [1.0] * 4})
        plot = pd.DataFrame({"CellID": list("abcd"),
                             "Cluster": ["10", "2", "1", "other"]})
        recs, _ = g.cluster_stats_from_frames(expr, plot, FEATURE)
        assert [r["cluster"] for r in recs] == ["1", "2", "10", "other"]

    def test_nan_never_reaches_the_response(self):
        """NaN は JSON にできない。None にしていること。"""
        expr = pd.DataFrame({"CellID": ["a", "b"], FEATURE: [float("nan")] * 2})
        plot = pd.DataFrame({"CellID": ["a", "b"], "Cluster": ["1", "1"]})
        recs, _ = g.cluster_stats_from_frames(expr, plot, FEATURE)
        blob = json.dumps(recs)          # NaN が残っていれば ValueError にはならないが
        assert "NaN" not in blob, blob


# ===========================================================================
# 削ったら削ったと言う / 入力検証
# ===========================================================================
class TestTruncationIsAnnounced:

    def test_limit_truncation_is_reported(self, tmp_path, monkeypatch):
        """★ 黙って先頭 N 件だけ返すと「全部でこれだけ」と読まれる。"""
        d = _write_cache(tmp_path / "c")
        body = _get(
            _client(monkeypatch, d),
            "/api/gpt/projects/p/sub/s/features/values?mz=800.5&limit=3").get_json()
        assert len(body["values"]) == 3
        assert body["truncated"] is True
        assert body["n_selected"] == 10
        assert "10" in body["message"] and "3" in body["message"]

    def test_no_truncation_flag_when_everything_fits(self, tmp_path, monkeypatch):
        """★ 過剰報告の番人: 全部返せたときに truncated を立てない。"""
        d = _write_cache(tmp_path / "c")
        body = _get(
            _client(monkeypatch, d),
            "/api/gpt/projects/p/sub/s/features/values?mz=800.5&limit=50").get_json()
        assert len(body["values"]) == 10
        assert "truncated" not in body, body

    def test_cluster_filter_narrows_the_selection(self, tmp_path, monkeypatch):
        d = _write_cache(tmp_path / "c")
        body = _get(
            _client(monkeypatch, d),
            "/api/gpt/projects/p/sub/s/features/values?mz=800.5&cluster=5"
        ).get_json()
        assert body["n_selected"] == 5
        assert {r["cluster"] for r in body["values"]} == {"5"}
        assert body["n_total"] == 10

    def test_response_stays_under_the_action_limit(self, tmp_path, monkeypatch):
        """★ 全 endpoint 共通の上限。ここを超えると Action 側で読めない。"""
        d = _write_cache(tmp_path / "c")
        c = _client(monkeypatch, d)
        for path in ("/api/gpt/projects/p/sub/s/features/stats?mz=800.5",
                     "/api/gpt/projects/p/sub/s/features/values?mz=800.5&limit=200"):
            raw = _get(c, path).get_data(as_text=True)
            assert len(raw) <= g.MAX_RESPONSE_CHARS, (path, len(raw))


class TestInputsAreValidated:

    def test_mz_is_required(self, tmp_path, monkeypatch):
        """★ 省略を「全 feature」に落とさない（m/z 検索の意味が消える）。"""
        d = _write_cache(tmp_path / "c")
        r = _get(_client(monkeypatch, d), "/api/gpt/projects/p/sub/s/features/stats")
        assert r.status_code == 422 and r.get_json()["code"] == "MZ_REQUIRED"

    @pytest.mark.parametrize("q", ["mz=nan", "mz=inf", "mz=abc"])
    def test_bad_mz_is_422(self, tmp_path, monkeypatch, q):
        d = _write_cache(tmp_path / "c")
        r = _get(_client(monkeypatch, d),
                 f"/api/gpt/projects/p/sub/s/features/stats?{q}")
        assert r.status_code == 422, r.get_json()

    @pytest.mark.parametrize("q", ["tol=0", "tol=-1", "tol=1000"])
    def test_bad_tol_is_422(self, tmp_path, monkeypatch, q):
        d = _write_cache(tmp_path / "c")
        r = _get(_client(monkeypatch, d),
                 f"/api/gpt/projects/p/sub/s/features/stats?mz=800.5&{q}")
        assert r.status_code == 422, r.get_json()

    def test_missing_feature_is_404_not_an_empty_success(self, tmp_path, monkeypatch):
        """★ 「該当なし」を ok:true + 空配列にしない（区別できなくなる）。"""
        d = _write_cache(tmp_path / "c")
        r = _get(_client(monkeypatch, d),
                 "/api/gpt/projects/p/sub/s/features/stats?mz=500.0")
        assert r.status_code == 404 and r.get_json()["code"] == "FEATURE_NOT_FOUND"

    def test_no_expression_matrix_says_how_to_fix_it(self, tmp_path, monkeypatch):
        """★ 「warmup?with_expression=true を実行」と次の一手を言うこと。"""
        d = _write_cache(tmp_path / "c", with_expression=False)
        r = _get(_client(monkeypatch, d),
                 "/api/gpt/projects/p/sub/s/features/stats?mz=800.5")
        assert r.status_code == 409
        body = r.get_json()
        assert body["code"] == "NO_EXPRESSION_MATRIX"
        assert "with_expression" in body["error"], body["error"]

    def test_cold_cache_is_reported_not_faked(self, tmp_path, monkeypatch):
        """キャッシュが無いとき、空の統計を成功として返さないこと。

        ★ `_client` が `_warm_cache_dir` を設定するので、cold を作るには
          **`_client` に None を渡す**。先に monkeypatch しても後から
          上書きされる（最初そう書いて空振りした）。
        """
        body = _get(_client(monkeypatch, None),
                    "/api/gpt/projects/p/sub/s/features/stats?mz=800.5").get_json()
        assert body["warm"] is False and body["code"] == "CACHE_COLD"
        assert body["clusters"] == []

    def test_stats_explains_it_is_not_avg_log2fc(self, tmp_path, monkeypatch):
        """★ 実障害の予防。avg_log2FC と混同されると結論が逆になる。"""
        d = _write_cache(tmp_path / "c")
        body = _get(_client(monkeypatch, d),
                    "/api/gpt/projects/p/sub/s/features/stats?mz=800.5").get_json()
        assert "avg_log2FC" in body.get("stat_note", ""), body
