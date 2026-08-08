"""GPT API の入力契約 (ver52.0)。

■ 何が起きていたか

外部監査が報告した Critical を現行コード (ver51.9) で再現して確認した。
どれも ver51.8/51.9 で潰してきたのと同じ型 —— **失敗せずに、
もっともらしい間違った結果を返す**。

  F-01  `top` に既定値も上下限も無い
        省略 → **全件**、`top=0` → **全件**（`if top:` が偽なので無制限）、
        `top=51` → 51 件。Actions は応答 10 万文字未満なので
        `ResponseTooLargeError` になり、**利用者には原因が分からない**

  F-02  `cluster="1,3,7"` が `ok:true` の空配列
        `str(r["cluster"]) == str(cluster)` の完全一致なので**必ず 0 件**。
        「マーカーが無い」と読めるが、実際は入力形式を処理できなかっただけ。
        研究結果の欠落を引き起こす

  F-03  `method` の検証が無い
        `INVALID` を渡しても通り、ラベルは要求値をそのまま返す

  F-04  `_pick_method` が黙って別手法へ落ちる
        `PCA` / `PCA (uncorrected)` / `INVALID` → すべて `Harmony`。
        **応答の解析手法ラベルが信用できない**

  17.1  `top` は「高発現の上位」ではない
        `_marker_sort_key` は `(p 昇順, |log2FC| 降順)` で**符号を見ない**。
        実測で上位 10 件が全部 `−`（＝そのクラスタで相対的に低い）なのに、
        GPT は「クラスタ N で高発現する上位マーカー」と説明していた

★ サーバー側で直す。GPT の Instructions だけでは、別クライアントや
  モデル挙動の揺れを防げない（Instructions は「お願い」であって契約ではない）。
"""

import json

import pytest

from app.services import gpt_api as g


# ---------------------------------------------------------------------------
# F-01 top
# ---------------------------------------------------------------------------

class TestParseTop:
    """★ 既定 10 / 1..50。範囲外は 422。"""

    def test_omitted_defaults_to_10(self):
        val, err = g.parse_top(None)
        assert err is None
        assert val == 10, f"省略時の既定が 10 でない: {val}"

    def test_empty_string_defaults_to_10(self):
        val, err = g.parse_top("")
        assert err is None and val == 10

    def test_zero_is_rejected(self):
        """★ `top=0` を「無制限」から切り離すのが要点。

        従来は `if top:` が偽になり **全件返していた**。
        「0 件が欲しい」という要求はあり得ないので 422 が正しい。
        """
        val, err = g.parse_top("0")
        assert err is not None, "top=0 が通っている（従来は全件返していた）"
        assert err.status == 422 and err.code == "INVALID_TOP"

    def test_above_max_is_rejected(self):
        val, err = g.parse_top("51")
        assert err is not None and err.status == 422, "top=51 が通っている"

    def test_max_is_accepted(self):
        val, err = g.parse_top("50")
        assert err is None and val == 50

    def test_min_is_accepted(self):
        val, err = g.parse_top("1")
        assert err is None and val == 1

    @pytest.mark.parametrize("raw", ["abc", "1.5", "-3", "10.0", " "])
    def test_non_integer_is_rejected(self, raw):
        val, err = g.parse_top(raw)
        assert err is not None and err.status == 422, f"{raw!r} が通っている"

    def test_full_width_digits_are_accepted(self):
        """全角数字は Python の int() が受け付ける（10 は 10 なので害はない）。

        当初「弾くべき」と書いたが、`int('１０') == 10` を実測して前提が
        誤りだと分かったので、受け付ける側を固定する。
        """
        val, err = g.parse_top("１０")
        assert err is None and val == 10

    def test_error_message_says_the_range(self):
        """利用者が直せるだけの情報を返すこと。"""
        _v, err = g.parse_top("999")
        assert "50" in err.message and "1" in err.message, err.message


# ---------------------------------------------------------------------------
# F-02 複数クラスタ
# ---------------------------------------------------------------------------

class TestParseClusters:
    """★ カンマ区切りを 422 にせず、複数クラスタとして正しく処理する。

    422 でも「黙って空」よりは良いが、正しく動かせば
    F-07（1 質問あたり N 回の呼び出し）も同時に解消できる。
    """

    def test_comma_separated_becomes_a_list(self):
        val, err = g.parse_clusters("1,3,7")
        assert err is None
        assert val == ["1", "3", "7"], val

    def test_single_value_still_works(self):
        val, err = g.parse_clusters("1")
        assert err is None and val == ["1"]

    def test_omitted_means_all_clusters(self):
        for raw in (None, ""):
            val, err = g.parse_clusters(raw)
            assert err is None and val is None, raw

    def test_whitespace_is_tolerated(self):
        val, err = g.parse_clusters(" 1 , 3 ")
        assert err is None and val == ["1", "3"]

    @pytest.mark.parametrize("raw", ["1,,3", ",", "1,", ","])
    def test_malformed_is_rejected(self, raw):
        """空要素は入力ミスなので黙って捨てない。"""
        val, err = g.parse_clusters(raw)
        assert err is not None, f"{raw!r} が通っている"
        assert err.status == 422 and err.code == "INVALID_CLUSTER_FORMAT"

    def test_duplicates_are_collapsed_keeping_order(self):
        val, err = g.parse_clusters("3,1,3")
        assert err is None and val == ["3", "1"]


class TestShapeMarkersMultiCluster:
    """★ 複数クラスタで、各クラスタの上位 N が返ること。"""

    @staticmethod
    def _recs():
        out = []
        for cl in ("1", "3", "7"):
            for i in range(20):
                out.append({"gene": f"g{cl}_{i}", "cluster": cl,
                            "avg_log2FC": (1.0 if i % 2 else -1.0) * (20 - i),
                            "p_val_adj": "1e-5", "p_val_adj_raw": 1e-5 * (i + 1)})
        return out

    def test_three_clusters_return_three_groups(self):
        got = g.shape_markers(self._recs(), cluster=["1", "3", "7"], top=10)
        by = {}
        for r in got:
            by.setdefault(r["cluster"], []).append(r)
        assert sorted(by) == ["1", "3", "7"], sorted(by)
        assert all(len(v) == 10 for v in by.values()), {k: len(v) for k, v in by.items()}

    def test_flat_shape_is_preserved(self):
        """★ 過剰修正の番人: 応答の形を変えない。

        `markers[]` をクラスタ別の入れ子にすると **既存の GPT 設定が壊れる**。
        各レコードが `cluster` を持つので、平らなままで十分。
        """
        got = g.shape_markers(self._recs(), cluster=["1", "3"], top=2)
        assert isinstance(got, list)
        assert all(isinstance(r, dict) and "cluster" in r for r in got)

    def test_single_cluster_string_still_works(self):
        """従来の呼び方（文字列 1 個）を壊さない。"""
        got = g.shape_markers(self._recs(), cluster="1", top=5)
        assert len(got) == 5 and {r["cluster"] for r in got} == {"1"}

    def test_unknown_cluster_yields_nothing_for_that_one_only(self):
        got = g.shape_markers(self._recs(), cluster=["1", "999"], top=3)
        assert {r["cluster"] for r in got} == {"1"}


# ---------------------------------------------------------------------------
# F-03 / F-04 method
# ---------------------------------------------------------------------------

RDS_MAP = {"Harmony": "/h.rds", "RPCA": "/r.rds"}


class TestResolveMethod:
    """★ 明示要求した手法は絶対に置換しない。"""

    def test_available_method_is_returned(self):
        sel, err = g.resolve_method("RPCA", RDS_MAP)
        assert err is None and sel == "RPCA"

    def test_unknown_name_is_rejected(self):
        """★ 従来は `INVALID` が通り、Harmony の結果を `INVALID` として返していた。"""
        sel, err = g.resolve_method("INVALID", RDS_MAP)
        assert err is not None, "未知の手法名が通っている"
        assert err.status == 422 and err.code == "INVALID_METHOD"

    def test_known_but_unavailable_is_not_substituted(self):
        """★ 本丸。`PCA` を要求して `Harmony` が返らないこと。

        従来は `_pick_method` が `_METHOD_ORDER` の先頭へ黙って落ちるため、
        PCA を指定した比較が実は Harmony 同士の比較になっていた。
        """
        sel, err = g.resolve_method("PCA", RDS_MAP)
        assert sel != "Harmony", "PCA 要求が Harmony に置換されている"
        assert err is not None and err.code == "METHOD_NOT_AVAILABLE"
        assert err.status == 409

    def test_unavailable_error_lists_what_is_available(self):
        """利用者が次に何を指定すればよいか分かること。"""
        _sel, err = g.resolve_method("PCA", RDS_MAP)
        assert err.detail.get("available_methods") == ["Harmony", "RPCA"], err.detail

    @pytest.mark.parametrize("given", ["harmony", "RPCA ", " rpca"])
    def test_case_and_space_are_normalised(self, given):
        """★ 表記ゆれで「未知の手法」にしない（ver51.9 A-2 と同じ方針）。"""
        sel, err = g.resolve_method(given, RDS_MAP)
        assert err is None, f"{given!r} が弾かれた"
        assert sel in RDS_MAP

    def test_omitted_falls_back_and_says_so(self):
        """★ 省略時のみ既定へ落ちてよい（要求していないので置換ではない）。"""
        sel, err = g.resolve_method(None, RDS_MAP)
        assert err is None and sel == "Harmony"

    def test_no_rds_at_all_is_an_error(self):
        sel, err = g.resolve_method(None, {})
        assert err is not None and err.code == "NO_RESULT"


# ---------------------------------------------------------------------------
# 17.1 direction
# ---------------------------------------------------------------------------

class TestDirection:
    """★ 「上位 N」が「高発現の上位」ではないことへの対処。"""

    @staticmethod
    def _mixed():
        return [
            {"gene": "up1", "cluster": "1", "avg_log2FC": 3.0,
             "p_val_adj": "1e-9", "p_val_adj_raw": 1e-9},
            {"gene": "dn1", "cluster": "1", "avg_log2FC": -4.0,
             "p_val_adj": "1e-8", "p_val_adj_raw": 1e-8},
            {"gene": "up2", "cluster": "1", "avg_log2FC": 1.0,
             "p_val_adj": "1e-7", "p_val_adj_raw": 1e-7},
            {"gene": "zero", "cluster": "1", "avg_log2FC": 0.0,
             "p_val_adj": "1e-6", "p_val_adj_raw": 1e-6},
        ]

    def test_up_returns_only_positive(self):
        got = g.shape_markers(self._mixed(), cluster="1", top=10, direction="up")
        assert [r["gene"] for r in got] == ["up1", "up2"], got

    def test_down_returns_only_negative(self):
        got = g.shape_markers(self._mixed(), cluster="1", top=10, direction="down")
        assert [r["gene"] for r in got] == ["dn1"], got

    def test_both_is_the_default_and_keeps_old_behaviour(self):
        """★ 過剰修正の番人: 既定は従来どおり（符号で絞らない）。"""
        a = g.shape_markers(self._mixed(), cluster="1", top=10)
        b = g.shape_markers(self._mixed(), cluster="1", top=10, direction="both")
        assert a == b
        assert len(a) == 4, a

    def test_invalid_direction_is_rejected(self):
        val, err = g.parse_direction("sideways")
        assert err is not None and err.status == 422


# ---------------------------------------------------------------------------
# F-09 エラー契約
# ---------------------------------------------------------------------------

class TestErrorContract:
    """★ 「本当に無い」「存在しない」「入力が不正」を区別できること。

    従来はどれも `ok:true` + 空配列で、利用者にも GPT にも区別できなかった。
    """

    def test_codes_are_distinct(self):
        codes = {
            g.parse_top("0")[1].code,
            g.parse_clusters("1,,3")[1].code,
            g.resolve_method("INVALID", RDS_MAP)[1].code,
            g.resolve_method("PCA", RDS_MAP)[1].code,
        }
        assert len(codes) == 4, f"コードが重複している: {codes}"

    def test_error_serialises_with_ok_false(self):
        _v, err = g.parse_top("0")
        payload = err.to_payload()
        assert payload["ok"] is False
        assert payload["code"] == "INVALID_TOP"
        assert payload["error"]

    def test_marker_outcome_distinguishes_missing_cluster_from_no_markers(self):
        """★ クラスタ不在とマーカー 0 件を区別する。

        利用者にとって意味が全く違う。前者は指定ミス（直せる）、
        後者は生物学的な結果（直しようがない）。

        ★ ver52.1: このテストは元々 **私の誤った設計判断を固定していた**。
          「一部だけ存在しない場合は指定ミスを優先しない」と書いて
          `["1","999"]` → NO_MARKERS を正解にしていたが、それでは
          999 が存在しないことを誰にも伝えられない。判断ごと改めた。
        """
        available = {"0", "1", "2"}
        # 結果があるなら注意書きは要らない
        assert g.marker_outcome([{"gene": "a"}], ["1"], available) is None
        # 全部存在しない → 指定ミス
        assert g.marker_outcome([], ["999"], available)["code"] == "CLUSTER_NOT_FOUND"
        # クラスタは在るが 0 件（direction で絞った等）→ 本当に無い
        assert g.marker_outcome([], ["1"], available)["code"] == "NO_MARKERS"
        # ★ 一部だけ存在しない → 部分的であることを必ず伝える
        part = g.marker_outcome([], ["1", "999"], available)
        assert part["code"] == "PARTIAL_CLUSTERS" and part["missing_clusters"] == ["999"]
        # クラスタ未指定で 0 件 → DEG 自体が空
        assert g.marker_outcome([], None, available)["code"] == "NO_MARKERS"


# ---------------------------------------------------------------------------
# レスポンスサイズガード
# ---------------------------------------------------------------------------

class TestResponseSizeGuard:
    """★ Actions の上限に当たる前にサーバー側で削る。

    Action 側で失敗すると `ResponseTooLargeError` としか出ず、
    利用者は何を直せばよいか分からない。
    """

    def test_large_payload_is_truncated(self):
        recs = [{"gene": "m/z 123.45678 (very long annotation text)" * 3,
                 "cluster": "1", "avg_log2FC": 1.0, "p_val_adj": "1e-9"}
                for _ in range(5000)]
        out, truncated = g.limit_response_size({"markers": recs}, "markers")
        assert truncated is True
        assert len(out["markers"]) < len(recs)
        assert len(json.dumps(out, ensure_ascii=False)) <= g.MAX_RESPONSE_CHARS

    def test_small_payload_is_untouched(self):
        """★ 過剰修正の番人: 収まるものは削らない。"""
        payload = {"markers": [{"gene": "a", "cluster": "1"}]}
        out, truncated = g.limit_response_size(payload, "markers")
        assert truncated is False
        assert out == payload


# ---------------------------------------------------------------------------
# F-01/F-03 OpenAPI 仕様（★ GPT が実際に読むのはこれ）
# ---------------------------------------------------------------------------

class TestOpenApiContract:
    """★ サーバーを直しても仕様書が緩いままだと、モデルの挙動は変わらない。"""

    @staticmethod
    def _param(spec, path, name):
        op = spec["paths"][path]["get"]
        return next((p for p in op["parameters"] if p["name"] == name), None)

    def test_top_declares_default_and_bounds(self):
        spec = g.build_openapi_spec("https://x")
        p = self._param(
            spec, "/api/gpt/projects/{pid}/sub/{sid}/markers", "top")
        assert p is not None
        s = p["schema"]
        assert s.get("default") == 10, s
        assert s.get("minimum") == 1, s
        assert s.get("maximum") == 50, s

    def test_method_is_an_enum(self):
        spec = g.build_openapi_spec("https://x")
        for path in ("/api/gpt/projects/{pid}/sub/{sid}/markers",
                     "/api/gpt/projects/{pid}/sub/{sid}/clusters",
                     "/api/gpt/projects/{pid}/sub/{sid}/compounds"):
            p = self._param(spec, path, "method")
            assert p and "enum" in p["schema"], f"{path} の method に enum が無い"
            assert set(p["schema"]["enum"]) == set(g._METHOD_ORDER), p

    def test_direction_is_declared(self):
        spec = g.build_openapi_spec("https://x")
        p = self._param(
            spec, "/api/gpt/projects/{pid}/sub/{sid}/markers", "direction")
        assert p and p["schema"].get("enum") == ["up", "down", "both"], p
        assert p["schema"].get("default") == "both", p

    def test_cluster_documents_multi_value(self):
        spec = g.build_openapi_spec("https://x")
        p = self._param(
            spec, "/api/gpt/projects/{pid}/sub/{sid}/markers", "cluster")
        assert "," in p["description"], p["description"]

    def test_binary_download_is_not_an_action(self):
        """★ Actions はバイナリ応答を扱えないので **必ず失敗する**。

        仕様に載せておくと GPT が繰り返し試みるだけなので外す。
        エンドポイント自体はブラウザ直接利用のために残す。
        """
        spec = g.build_openapi_spec("https://x")
        ops = {op.get("operationId")
               for p in spec["paths"].values() for op in p.values()}
        assert "download" not in ops, (
            "バイナリを返す download が Action 仕様に残っている。"
            f"現在の operationId: {sorted(o for o in ops if o)}")
        assert "downloadExportJob" not in ops, ops

    def test_the_rest_of_the_operations_survive(self):
        """★ 過剰修正の番人: 必要な operation を消していないこと。"""
        spec = g.build_openapi_spec("https://x")
        ops = {op.get("operationId")
               for p in spec["paths"].values() for op in p.values()}
        for required in ("health", "listProjects", "getProject", "getClusters",
                         "getMarkers", "searchCompounds", "listOutputs",
                         "listExports", "startInteractiveExport", "getExportJob"):
            assert required in ops, f"{required} が消えた"


# ---------------------------------------------------------------------------
# ハンドラ結線（実際の HTTP 応答で見る）
# ---------------------------------------------------------------------------

class TestHandlersAreWired:
    """★ 純関数を直しても、ハンドラが呼ばなければ意味が無い。

    ver51.8 の A-7（ヘルパを作ったのに 4 箇所で呼び替え漏れ）と同じ形の
    取りこぼしを防ぐため、**実際の HTTP 応答**で確認する。
    """

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        flask = pytest.importorskip("flask")
        import pandas as pd

        # 結果フォルダ: Harmony / RPCA の DEG（PCA は無い）
        result_dir = tmp_path / "result"
        rds_dir = result_dir / "RDS_Files"
        rds_dir.mkdir(parents=True)
        rds_map = {}
        for m in ("Harmony", "RPCA"):
            p = rds_dir / f"seu_{m.lower()}.rds"
            p.write_bytes(b"x")
            rds_map[m] = str(p)
            d = result_dir / m
            d.mkdir()
            rows = []
            for cl in ("1", "3", "7"):
                for i in range(30):
                    rows.append({"gene": f"{m}_c{cl}_{i}", "cluster": cl,
                                 "avg_log2FC": (1.0 if i % 2 else -1.0) * (30 - i),
                                 "p_val_adj": 10.0 ** -(9 - i % 9)})
            pd.DataFrame(rows).to_csv(d / "deg_markers.csv", index=False)

        monkeypatch.setattr(
            g, "_resolve_sub",
            lambda pid, sid: {"project": {}, "sub": {},
                              "result_dir": str(result_dir),
                              "data_folder": None, "ms_instrument": "TIMS",
                              "rds_map": rds_map})
        monkeypatch.setattr(g, "_warm_cache_dir", lambda rds: None)

        app = flask.Flask(__name__)
        monkeypatch.setattr("app.config.GPT_API_KEY", "k", raising=False)
        g.register_gpt_api(app)
        c = app.test_client()
        c.environ_base["HTTP_X_API_KEY"] = "k"
        return c

    @staticmethod
    def _get(client, **params):
        from urllib.parse import urlencode
        q = urlencode({k: v for k, v in params.items() if v is not None})
        return client.get(f"/api/gpt/projects/p/sub/s/markers?{q}",
                          headers={"X-API-Key": "k"})

    def test_top_omitted_is_capped_at_the_default(self, client):
        """★ 従来は全件（90 件）返って応答上限に当たっていた。"""
        r = self._get(client)
        assert r.status_code == 200
        body = r.get_json()
        assert body["top"] == 10
        by = {}
        for m in body["markers"]:
            by.setdefault(str(m["cluster"]), 0)
            by[str(m["cluster"])] += 1
        assert all(v == 10 for v in by.values()), by

    def test_top_zero_is_422(self, client):
        r = self._get(client, top=0)
        assert r.status_code == 422, r.get_json()
        assert r.get_json()["code"] == "INVALID_TOP"

    def test_top_51_is_422(self, client):
        r = self._get(client, top=51)
        assert r.status_code == 422

    def test_multi_cluster_returns_all_three(self, client):
        """★ 従来は ok:true の空配列だった（研究結果がそのまま欠落）。"""
        r = self._get(client, cluster="1,3,7", top=5)
        assert r.status_code == 200
        body = r.get_json()
        # cluster は CSV 由来の型のまま返る（数値 CSV なら int）。
        # 絞り込みは文字列比較なので機能する。型を変えると既存の
        # GPT 設定の読み方を変えてしまうので、ここでは str() で比べる。
        assert {str(m["cluster"]) for m in body["markers"]} == {"1", "3", "7"}, body
        assert len(body["markers"]) == 15

    def test_invalid_method_is_422(self, client):
        """★ 従来は通って Harmony の結果を INVALID として返していた。"""
        r = self._get(client, method="INVALID")
        assert r.status_code == 422, r.get_json()
        assert r.get_json()["code"] == "INVALID_METHOD"

    def test_pca_is_not_silently_swapped_for_harmony(self, client):
        """★ 本丸。PCA を要求して Harmony の結果が返らないこと。"""
        r = self._get(client, method="PCA")
        assert r.status_code == 409, r.get_json()
        body = r.get_json()
        assert body["code"] == "METHOD_NOT_AVAILABLE"
        assert body["available_methods"] == ["Harmony", "RPCA"]

    def test_requested_and_selected_are_reported(self, client):
        r = self._get(client, method="RPCA", top=1)
        body = r.get_json()
        assert body["requested_method"] == "RPCA"
        assert body["selected_method"] == "RPCA"
        assert body["method"] == "RPCA"          # 後方互換キー

    def test_omitted_method_says_it_fell_back(self, client):
        r = self._get(client, top=1)
        body = r.get_json()
        assert body["requested_method"] is None
        assert body["selected_method"] == "Harmony"

    def test_each_method_returns_its_own_table(self, client):
        """要求どおりの手法の表が返ること（ラベルだけ違う、にならない）。"""
        h = self._get(client, method="Harmony", cluster="1", top=1).get_json()
        rp = self._get(client, method="RPCA", cluster="1", top=1).get_json()
        assert h["markers"][0]["gene"].startswith("Harmony_")
        assert rp["markers"][0]["gene"].startswith("RPCA_")

    def test_sort_order_is_disclosed(self, client):
        """★ 「上位＝高発現」と誤解させないための明示。"""
        body = self._get(client, cluster="1", top=1).get_json()
        assert body["sort"] == g.MARKER_SORT_DESC

    def test_direction_up_returns_only_positive(self, client):
        body = self._get(client, cluster="1", top=50,
                         direction="up").get_json()
        assert body["markers"]
        assert all(m["avg_log2FC"] > 0 for m in body["markers"])

    def test_unknown_cluster_is_distinguished(self, client):
        """★ 「存在しない」と「マーカーが無い」を区別する。"""
        body = self._get(client, cluster="999").get_json()
        assert body["code"] == "CLUSTER_NOT_FOUND", body
        assert {str(c) for c in body["available_clusters"]} == {"1", "3", "7"}

    def test_malformed_cluster_is_422(self, client):
        r = self._get(client, cluster="1,,3")
        assert r.status_code == 422
        assert r.get_json()["code"] == "INVALID_CLUSTER_FORMAT"

    def test_cold_clusters_report_readiness(self, client):
        """★ cold でもマーカーは取れることを応答で伝える。"""
        r = client.get("/api/gpt/projects/p/sub/s/clusters",
                       headers={"X-API-Key": "k"})
        body = r.get_json()
        assert body["code"] == "CACHE_COLD"
        assert body["ready"]["clusters"] is False
        assert body["ready"]["markers"] is True, body["ready"]

    def test_response_stays_within_the_action_limit(self, client):
        """★ 上限に当たる前にサーバー側で削ること。"""
        import json as _json
        r = self._get(client, cluster="1,3,7", top=50)
        assert r.status_code == 200
        assert len(_json.dumps(r.get_json(), ensure_ascii=False)) \
            <= g.MAX_RESPONSE_CHARS


# ===========================================================================
# ver52.1 — 外部監査が ver52.0 に見つけた境界の残り
# ===========================================================================
# ★ どれも ver52.0 で私が **半端に直した**結果。`top` は直したのに `limit` を
#   直していない、`_markers` は検証するのに `_compounds` /
#   `_export_interactive` は素通り、という形になっていた。
#   監査の gpt_api_boundary_reproduction.csv の 9 行をそのままケースにする。


class TestMissingClustersAreReported:
    """★ API-01/02: 一部だけ存在しないクラスタを黙って無視しない。

    `cluster=1,999` で 1 が実在すれば、999 は
      - shaped が空でない → `code` すら付かず成功扱い
      - shaped が空       → NO_MARKERS（「クラスタは存在します」と断言）
    となり、**利用者が問い合わせた 999 について何も言わない**。
    GPT は「999 にはマーカーが無い」と要約するが、実際には存在しない。

    ★ ver52.0 で私は「一部でも実在するなら NO_MARKERS が正しい」と
      コメントに書き、**その誤った設計判断ごとテストに固定した**。
      判断が誤りだったのでテストも直す。
    """

    AVAIL = {"0", "1", "2"}

    def test_partial_miss_is_reported_even_when_data_exists(self):
        out = g.marker_outcome([{"gene": "a"}], ["1", "999"], self.AVAIL)
        assert out is not None, (
            "データが返るときも欠けたクラスタを通知していない。"
            "利用者は 999 も確認済みだと誤解する")
        assert out["missing_clusters"] == ["999"], out
        assert out["partial"] is True, out

    def test_partial_miss_is_reported_when_empty(self):
        out = g.marker_outcome([], ["1", "999"], self.AVAIL)
        assert out["missing_clusters"] == ["999"], out

    def test_all_missing_is_cluster_not_found(self):
        out = g.marker_outcome([], ["998", "999"], self.AVAIL)
        assert out["code"] == "CLUSTER_NOT_FOUND", out
        assert out["missing_clusters"] == ["998", "999"], out

    def test_no_markers_when_all_requested_exist(self):
        """★ 過剰修正の番人: 全部実在して 0 件なら「本当に無い」。"""
        out = g.marker_outcome([], ["1"], self.AVAIL)
        assert out["code"] == "NO_MARKERS", out
        assert not out.get("missing_clusters"), out

    def test_nothing_reported_on_the_clean_path(self):
        """★ 過剰修正の番人: 何も問題が無ければ余計なキーを足さない。"""
        assert g.marker_outcome([{"gene": "a"}], ["1"], self.AVAIL) is None

    def test_all_clusters_requested_is_untouched(self):
        assert g.marker_outcome([{"gene": "a"}], None, self.AVAIL) is None
        assert g.marker_outcome([], None, self.AVAIL)["code"] == "NO_MARKERS"


class TestNumericQueryValidation:
    """★ API-03/04/05: 数値クエリを黙って既定へ差し替えない。"""

    def test_limit_zero_is_rejected(self):
        """★ `top=0` と同じ穴を `limit` に残していた。"""
        val, err = g.parse_limit("0")
        assert err is not None, "limit=0 が通っている（従来は無制限だった）"
        assert err.status == 422 and err.code == "INVALID_LIMIT"

    def test_limit_defaults_and_bounds(self):
        assert g.parse_limit(None)[0] == 50
        assert g.parse_limit("200")[0] == 200
        assert g.parse_limit("201")[1] is not None
        assert g.parse_limit("-5")[1] is not None
        assert g.parse_limit("abc")[1] is not None

    def test_mz_must_be_finite(self):
        """★ 監査より悪い: `float('nan')` は例外を出さない。

        実測では `mz=nan` が `abs(x-nan) > tol` → `nan > tol` → False で
        **全件を絞り込み素通りさせ、「m/z 検索の結果」として返っていた**。
        """
        for bad in ("nan", "inf", "-inf", "NaN", "Infinity"):
            val, err = g.parse_mz(bad)
            assert err is not None, f"mz={bad!r} が通っている"
            assert err.code == "INVALID_MZ"

    def test_mz_rejects_non_numeric_instead_of_dropping_the_filter(self):
        """★ 従来は None にして **m/z 絞り込み自体を消して**いた。"""
        val, err = g.parse_mz("abc")
        assert err is not None and err.status == 422

    def test_mz_omitted_means_no_filter(self):
        """過剰修正の番人: 省略は従来どおり「m/z 指定なし」。"""
        assert g.parse_mz(None) == (None, None)
        assert g.parse_mz("") == (None, None)

    def test_tol_must_be_positive(self):
        """★ 負の tol は「成功した空結果」になり、真の該当なしと区別できない。"""
        for bad in ("-0.1", "0", "nan", "inf"):
            val, err = g.parse_tol(bad)
            assert err is not None, f"tol={bad!r} が通っている"
            assert err.code == "INVALID_TOL"

    def test_tol_default_and_valid(self):
        assert g.parse_tol(None)[0] == 0.01
        assert g.parse_tol("0.5")[0] == 0.5

    def test_filter_compounds_no_longer_treats_zero_as_unlimited(self):
        recs = [{"feature": str(m), "compound": f"C{i}", "mz": float(m)}
                for i, m in enumerate((100, 200, 300, 400, 500))]
        assert len(g.filter_compounds(recs, limit=0)) == 0, \
            "limit=0 がまだ無制限になっている"
        assert len(g.filter_compounds(recs, limit=2)) == 2
        assert len(g.filter_compounds(recs)) == 5     # 既定 50


class TestExportContract:
    """★ API-06/07: 指定と違うものを黙って作らない。"""

    def test_invalid_format_is_rejected(self):
        val, err = g.parse_export_format("tsv")
        assert err is not None and err.code == "INVALID_FORMAT"
        assert err.status == 422

    def test_valid_formats_pass_and_default_is_parquet(self):
        assert g.parse_export_format(None)[0] == "parquet"
        for fmt in g._EXPORT_FORMATS:
            assert g.parse_export_format(fmt)[0] == fmt
        assert g.parse_export_format(" CSV ")[0] == "csv"

    def test_unavailable_methods_are_rejected_not_expanded(self):
        """★ 従来は指定が全部無効だと **全手法** に膨らんでいた。"""
        sel, err = g.resolve_export_methods("PCA", {"Harmony": "/h", "RPCA": "/r"})
        assert err is not None, "無効な methods が通っている"
        assert err.code == "METHOD_NOT_AVAILABLE"
        assert err.detail["available_methods"] == ["Harmony", "RPCA"]

    def test_case_is_folded_like_resolve_method(self):
        """★ ver52.0 で私が作った不整合。

        `resolve_method` は大小文字を吸収するのに、export 経路だけ完全一致
        だったため `methods=harmony` が「無効」→ 全手法に膨らんでいた。
        """
        sel, err = g.resolve_export_methods("harmony", {"Harmony": "/h"})
        assert err is None and sel == ["Harmony"], (sel, err)

    def test_omitted_means_all_methods(self):
        """過剰修正の番人: 省略は従来どおり全手法。"""
        sel, err = g.resolve_export_methods(None, {"Harmony": "/h", "RPCA": "/r"})
        assert err is None and sel is None

    def test_partial_valid_is_rejected_too(self):
        """一部だけ有効でも、指定外を混ぜて出さない。"""
        sel, err = g.resolve_export_methods("Harmony,PCA", {"Harmony": "/h"})
        assert err is not None and err.code == "METHOD_NOT_AVAILABLE"


class TestDownloadUrlIsHonest:
    """★ API-08: Action が取得できない URL を「取得してください」と言わない。"""

    def test_spec_summary_does_not_promise_download(self):
        spec = g.build_openapi_spec("https://x")
        blob = json.dumps(spec, ensure_ascii=False)
        assert "download_url を返す" not in blob, (
            "Action が呼べない download_url の取得を仕様書が示唆している")

    def test_openapi_declares_the_tightened_params(self):
        spec = g.build_openapi_spec("https://x")

        def _param(path, name, method="get"):
            op = spec["paths"][path][method]
            return next((p for p in op["parameters"] if p["name"] == name), None)

        cpath = "/api/gpt/projects/{pid}/sub/{sid}/compounds"
        lim = _param(cpath, "limit")
        assert lim["schema"].get("default") == 50, lim
        assert lim["schema"].get("minimum") == 1, lim
        assert lim["schema"].get("maximum") == 200, lim
        tol = _param(cpath, "tol")
        assert tol["schema"].get("default") == 0.01, tol
        assert tol["schema"].get("exclusiveMinimum") == 0, tol

        epath = "/api/gpt/projects/{pid}/sub/{sid}/exports/interactive"
        fmt = _param(epath, "format", "post")
        assert set(fmt["schema"]["enum"]) == set(g._EXPORT_FORMATS), fmt


# ===========================================================================
# ver52.3 ④: direction 指定で読めない avg_log2FC が両方向から消える
# ===========================================================================
# `_fc` は読めない値も NaN も 0.0 に落としていたため、その record は
# `> 0` にも `< 0` にも入らず **up でも down でも返らなかった**。
# 件数の報告も無いので、切り詰められた一覧が「上位マーカーの全部」として
# GPT に渡っていた。入口 (parse_top / parse_limit / parse_direction) を
# 2 版続けて硬くした同じファイルの中で、絞り込み側だけ見ていなかった。

def _recs_with_unreadable():
    return [
        {"cluster": "1", "gene": "mz_100.0", "avg_log2FC": 2.0,
         "p_val_adj": 1e-5},
        {"cluster": "1", "gene": "mz_200.0", "avg_log2FC": "n.d.",
         "p_val_adj": 1e-5},
        {"cluster": "1", "gene": "mz_300.0", "avg_log2FC": float("nan"),
         "p_val_adj": 1e-5},
        {"cluster": "1", "gene": "mz_400.0", "avg_log2FC": -1.5,
         "p_val_adj": 1e-4},
    ]


class TestUnreadableFoldChangeIsCounted:

    def test_counts_both_unparseable_and_nan(self):
        from app.services.gpt_api import count_unreadable_fc
        assert count_unreadable_fc(_recs_with_unreadable()) == 2

    def test_zero_is_readable(self):
        """★ 0.0 は「変動なし」という正当な値。読めなかったのとは違う。"""
        from app.services.gpt_api import count_unreadable_fc
        assert count_unreadable_fc([{"avg_log2FC": 0.0}]) == 0

    # ▼ 以下 2 件は **修正前後で結果が変わらない**（症状そのものの記録）。
    #   除外する挙動自体は従来どおり正しい。変わったのは「件数を伝えるか」なので、
    #   直ったことを突くのは上の count_unreadable_fc と下の応答テスト。
    #   弱いテストを「効いている」と誤解しないよう、ここに明記しておく。
    def test_symptom_direction_filter_excludes_unreadable(self):
        from app.services.gpt_api import shape_markers
        up = shape_markers(_recs_with_unreadable(), cluster="1",
                           direction="up")
        down = shape_markers(_recs_with_unreadable(), cluster="1",
                             direction="down")
        assert [r["gene"] for r in up] == ["mz_100.0"]
        assert [r["gene"] for r in down] == ["mz_400.0"]

    def test_symptom_both_directions_together_miss_them(self):
        """up と down を足しても読めない 2 件は出てこない（＝黙って消える）。"""
        from app.services.gpt_api import shape_markers
        recs = _recs_with_unreadable()
        genes = {r["gene"] for r in shape_markers(recs, cluster="1", direction="up")}
        genes |= {r["gene"] for r in shape_markers(recs, cluster="1", direction="down")}
        assert genes == {"mz_100.0", "mz_400.0"}

    def test_direction_both_is_unaffected(self):
        """★ 過剰修正の番人: direction 未指定は従来どおり全件返すこと。"""
        from app.services.gpt_api import shape_markers
        got = shape_markers(_recs_with_unreadable(), cluster="1", direction="both")
        assert len(got) == 4, f"direction=both で件数が変わっている: {len(got)}"


class TestResponseReportsDroppedRecords:
    """★ 本命: 落とした件数が **実際の HTTP 応答** に載ること。

    純関数だけ直しても応答に出なければ利用者（GPT）には届かない。
    ver51.8 A-7（ヘルパを作ったのに呼び替え漏れ）と同じ形を避けるため、
    このファイルの既存の client fixture と同じやり方で応答を見る。
    """

    @pytest.fixture
    def client(self, tmp_path, monkeypatch):
        flask = pytest.importorskip("flask")
        import pandas as pd

        result_dir = tmp_path / "result"
        rds_dir = result_dir / "RDS_Files"
        rds_dir.mkdir(parents=True)
        p = rds_dir / "seu_harmony.rds"
        p.write_bytes(b"x")
        rds_map = {"Harmony": str(p)}
        d = result_dir / "Harmony"
        d.mkdir()
        pd.DataFrame([
            {"gene": "mz_100.0", "cluster": "1", "avg_log2FC": 2.0,
             "p_val_adj": 1e-5},
            {"gene": "mz_200.0", "cluster": "1", "avg_log2FC": "n.d.",
             "p_val_adj": 1e-5},
            {"gene": "mz_300.0", "cluster": "1", "avg_log2FC": -1.5,
             "p_val_adj": 1e-4},
        ]).to_csv(d / "deg_markers.csv", index=False)

        monkeypatch.setattr(
            g, "_resolve_sub",
            lambda pid, sid: {"project": {}, "sub": {},
                              "result_dir": str(result_dir),
                              "data_folder": None, "ms_instrument": "TIMS",
                              "rds_map": rds_map})
        monkeypatch.setattr(g, "_warm_cache_dir", lambda rds: None)

        app = flask.Flask(__name__)
        monkeypatch.setattr("app.config.GPT_API_KEY", "k", raising=False)
        g.register_gpt_api(app)
        c = app.test_client()
        c.environ_base["HTTP_X_API_KEY"] = "k"
        return c

    @staticmethod
    def _markers(client, **params):
        from urllib.parse import urlencode
        r = client.get("/api/gpt/projects/p/sub/s/markers?" + urlencode(params),
                       headers={"X-API-Key": "k"})
        assert r.status_code == 200, f"{r.status_code}: {r.data[:200]}"
        return r.get_json()

    def test_dropped_count_is_in_the_response(self, client):
        body = self._markers(client, cluster="1", direction="up")
        assert body.get("dropped_unreadable_log2fc") == 1, (
            "avg_log2FC を読めない record を除外したのに、応答が件数を伝えていない。"
            "GPT には切り詰められた一覧が『上位マーカーの全部』として渡る: "
            f"{ {k: v for k, v in body.items() if k != 'markers'} }")

    def test_message_explains_it(self, client):
        body = self._markers(client, cluster="1", direction="up")
        assert "avg_log2FC" in (body.get("message") or ""), \
            f"説明文が無い: {body.get('message')!r}"

    def test_no_noise_when_everything_is_readable(self, client):
        """★ 過剰修正の番人: 落とすものが無ければ余計なキーを足さないこと。"""
        body = self._markers(client, cluster="1", direction="both")
        assert "dropped_unreadable_log2fc" not in body
