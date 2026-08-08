"""ChatGPT 連携 API（受付窓口 /api/gpt/*）の純ロジックのテスト（Dash/Flask 非依存）。

対象: app.services.gpt_api の純関数
- X-API-Key の可否判定 (key_decision): 鍵不要 / 未設定=503 / 誤=401 / 一致=200
- ダウンロード参照トークン encode/decode の往復と不正拒否
- 化合物検索 filter_compounds（名前 / m/z±tol / 脂質クラス / 並び / limit）
- マーカー整形 shape_markers（クラスタ絞り込み / top / 有意度順）
- 一覧整形 project_summary / project_detail / sub_summary / shape_clusters
- OpenAPI 仕様 build_openapi_spec の形（servers 反映 / 鍵スキーム / 各 operationId）
"""
from app.services import gpt_api as g


# ---------------------------------------------------------------------------
# key_decision
# ---------------------------------------------------------------------------
def test_key_decision_keyfree_paths_need_no_key():
    for path in ("/api/gpt/openapi.json", "/api/gpt/health"):
        assert g.key_decision(path, "", "") == (True, 200, None)
        # 鍵設定済みでも鍵不要パスは素通り
        assert g.key_decision(path, "", "secret")[0] is True


def test_key_decision_unset_key_is_fail_closed_503():
    allow, status, err = g.key_decision("/api/gpt/projects", "anything", "")
    assert allow is False and status == 503 and "GPT_API_KEY" in err


def test_key_decision_bad_key_401_good_key_200():
    assert g.key_decision("/api/gpt/projects", "wrong", "secret")[:2] == (False, 401)
    assert g.key_decision("/api/gpt/projects", "", "secret")[:2] == (False, 401)
    assert g.key_decision("/api/gpt/projects", "secret", "secret") == (True, 200, None)


# ---------------------------------------------------------------------------
# トークン
# ---------------------------------------------------------------------------
def test_ref_token_roundtrip():
    t = g.encode_ref("p1", "s1", "export", "metaboanalyst_data_compound.zip")
    d = g.decode_ref(t)
    assert d == {"p": "p1", "s": "s1", "k": "export",
                 "n": "metaboanalyst_data_compound.zip"}
    # URL-safe（+ や / を含まない、パディング = を除去）
    assert "/" not in t and "+" not in t and "=" not in t


def test_ref_token_rejects_garbage():
    assert g.decode_ref("") is None
    assert g.decode_ref("@@@not-base64@@@") is None
    # 構造不足（キー欠落）は None
    import base64 as _b64
    import json as _json
    bad = _b64.urlsafe_b64encode(_json.dumps({"p": "x"}).encode()).decode().rstrip("=")
    assert g.decode_ref(bad) is None


# ---------------------------------------------------------------------------
# filter_compounds
# ---------------------------------------------------------------------------
def _compound_fixture():
    return [
        {"feature": "611.1439", "compound": "PC 34:1", "display_name": "PC 34:1",
         "lipid_class": "PC", "mz": 611.1439, "adduct": "+H"},
        {"feature": "700.5", "compound": "TG 50:2", "display_name": "TG 50:2",
         "lipid_class": "TG", "mz": 700.5},
        {"feature": "611.20", "compound": "PE 30:0", "display_name": "PE 30:0",
         "lipid_class": "PE", "mz": 611.20},
    ]


def test_filter_compounds_by_name_case_insensitive():
    recs = _compound_fixture()
    got = g.filter_compounds(recs, query="pc")
    assert [r["compound"] for r in got] == ["PC 34:1"]


def test_filter_compounds_by_mz_tolerance_and_sorted_by_closeness():
    recs = _compound_fixture()
    # 611.14 ± 0.1 → 611.1439 のみ（611.20 は 0.06 差だが 0.1 内 → 両方一致）
    got = g.filter_compounds(recs, mz=611.14, tol=0.1)
    names = [r["compound"] for r in got]
    assert set(names) == {"PC 34:1", "PE 30:0"}
    # 近い順（611.1439 が先）
    assert names[0] == "PC 34:1"
    # 狭い許容差では PC のみ
    got2 = g.filter_compounds(recs, mz=611.14, tol=0.01)
    assert [r["compound"] for r in got2] == ["PC 34:1"]


def test_filter_compounds_by_lipid_class_and_limit():
    recs = _compound_fixture()
    assert [r["compound"] for r in g.filter_compounds(recs, lipid_class="tg")] == ["TG 50:2"]
    # limit
    assert len(g.filter_compounds(recs, limit=2)) == 2


def test_filter_compounds_mz_from_feature_when_missing():
    # mz キーが無くても feature 文字列から m/z を抽出できる
    recs = [{"feature": "500.25", "compound": "X", "lipid_class": "PC"}]
    got = g.filter_compounds(recs, mz=500.25, tol=0.01)
    assert len(got) == 1 and abs(got[0]["mz"] - 500.25) < 1e-6


# ---------------------------------------------------------------------------
# shape_markers
# ---------------------------------------------------------------------------
def _marker_fixture():
    return [
        {"gene": "a", "cluster": "0", "avg_log2FC": 2.0, "p_val_adj": "1e-9",
         "p_val_adj_raw": 1e-9},
        {"gene": "b", "cluster": "0", "avg_log2FC": -3.0, "p_val_adj": "1e-3",
         "p_val_adj_raw": 1e-3},
        {"gene": "c", "cluster": "1", "avg_log2FC": 1.0, "p_val_adj": "1e-5",
         "p_val_adj_raw": 1e-5},
    ]


def test_shape_markers_cluster_filter_and_significance_order():
    recs = _marker_fixture()
    got = g.shape_markers(recs, cluster="0")
    assert [r["gene"] for r in got] == ["a", "b"]  # 1e-9 が 1e-3 より先
    # p_val_adj_raw は出力に残さない
    assert "p_val_adj_raw" not in got[0]


def test_shape_markers_top_per_cluster_when_no_cluster():
    recs = _marker_fixture()
    got = g.shape_markers(recs, top=1)
    # クラスタごとに上位1件（0→a, 1→c）
    assert {(r["gene"], r["cluster"]) for r in got} == {("a", "0"), ("c", "1")}


def test_shape_markers_top_within_cluster():
    recs = _marker_fixture()
    got = g.shape_markers(recs, cluster="0", top=1)
    assert [r["gene"] for r in got] == ["a"]


# ---------------------------------------------------------------------------
# 一覧整形
# ---------------------------------------------------------------------------
def test_project_and_sub_summaries():
    proj = {
        "id": "p1", "name": "実験A", "experiment_date": "2026-01-01",
        "last_modified": "2026-07-01T10:00:00", "memo": "m",
        "sub_projects": [
            {"id": "s1", "name": "sub1", "ms_instrument": "TIMS",
             "polarity": ["Positive"], "last_result_dir": "/x/y"},
            {"id": "s2", "name": "sub2", "ms_instrument": "DESI"},
        ],
    }
    summ = g.project_summary(proj)
    assert summ["id"] == "p1" and summ["n_sub_projects"] == 2
    det = g.project_detail(proj)
    assert det["memo"] == "m" and len(det["sub_projects"]) == 2
    assert det["sub_projects"][0]["has_result"] is True
    assert det["sub_projects"][1]["has_result"] is False


def test_shape_clusters():
    recs = [{"cluster": 0, "n": 100}, {"cluster": 1, "n": 50}]
    out = g.shape_clusters(recs, {"foo": "bar"})
    assert out["n_clusters"] == 2 and out["clusters"] == recs
    assert out["meta"] == {"foo": "bar"}
    # None 安全
    assert g.shape_clusters(None, None)["n_clusters"] == 0


# ---------------------------------------------------------------------------
# OpenAPI
# ---------------------------------------------------------------------------
def test_openapi_spec_shape_and_security():
    spec = g.build_openapi_spec("https://msi.example.com/")
    assert spec["openapi"].startswith("3.")
    # servers は実ホストを反映（末尾スラッシュ除去）
    assert spec["servers"] == [{"url": "https://msi.example.com"}]
    # apiKey / header / X-API-Key
    scheme = spec["components"]["securitySchemes"]["ApiKeyAuth"]
    assert scheme == {"type": "apiKey", "in": "header", "name": "X-API-Key"}
    # グローバル security は ApiKeyAuth
    assert spec["security"] == [{"ApiKeyAuth": []}]
    # 期待する operationId が揃っている
    op_ids = {m["operationId"]
              for path in spec["paths"].values()
              for m in path.values()}
    # ★ ver52.0: `download` は Action 仕様から外した。Custom GPT Actions は
    #   バイナリ応答を扱えず **必ず失敗する**（実測 95KB の PNG でも失敗）ため、
    #   載せておくと GPT が繰り返し試みるだけだった。
    #   エンドポイント自体はブラウザ直接取得のために残っている。
    assert {"health", "listProjects", "getProject", "getClusters", "getMarkers",
            "searchCompounds", "listOutputs", "listExports"} <= op_ids
    assert "download" not in op_ids
    # health は鍵不要（security:[]）
    assert spec["paths"]["/api/gpt/health"]["get"]["security"] == []


def test_openapi_spec_default_server_placeholder():
    spec = g.build_openapi_spec("")
    assert spec["servers"][0]["url"] == "https://YOUR-DOMAIN"


# ---------------------------------------------------------------------------
# to_jsonable（numpy/pandas → 素の型。jsonify で 500 にならないための保険）
# ---------------------------------------------------------------------------
def test_to_jsonable_numpy_and_nan():
    import json
    import math
    import numpy as np
    import pandas as pd

    payload = {
        "i": np.int64(5),
        "f": np.float64(1.5),
        "b": np.bool_(True),
        "arr": np.array([1, 2, 3]),
        "nan": float("nan"),
        "inf": math.inf,
        "na": pd.NA,
        "nat": pd.NaT,
        "nested": [{"x": np.int32(7)}],
        "s": "ok",
    }
    out = g.to_jsonable(payload)
    # json.dumps が例外なく通る（= jsonify が 500 にならない）
    json.dumps(out)
    assert out["i"] == 5 and isinstance(out["i"], int)
    assert out["f"] == 1.5 and isinstance(out["f"], float)
    assert out["b"] is True
    assert out["arr"] == [1, 2, 3]
    assert out["nan"] is None and out["inf"] is None
    assert out["na"] is None and out["nat"] is None
    assert out["nested"][0]["x"] == 7
    assert out["s"] == "ok"


def test_to_jsonable_records_from_dataframe():
    import json
    import pandas as pd
    df = pd.DataFrame({"cluster": [0, 1], "n_cells": [100, 50], "mean": [1.2, 3.4]})
    recs = df.to_dict("records")  # numpy int64/float64 混在
    out = g.to_jsonable(recs)
    json.dumps(out)  # 例外が出ないこと
    assert out[0]["cluster"] == 0 and out[1]["n_cells"] == 50


# ---------------------------------------------------------------------------
# フェーズ2: インタラクティブ Export のオンデマンド生成
# ---------------------------------------------------------------------------
def test_valid_job_id():
    assert g.valid_job_id("a" * 32) is True          # uuid4.hex
    assert g.valid_job_id("0123456789abcdef") is True  # 16桁
    # パストラバーサル/不正はすべて False（グロブ・送出前の防御）
    assert g.valid_job_id("") is False
    assert g.valid_job_id("../etc/passwd") is False
    assert g.valid_job_id("abc") is False             # 短すぎ
    assert g.valid_job_id("g" * 32) is False          # 16進以外
    assert g.valid_job_id("a" * 65) is False          # 長すぎ
    assert g.valid_job_id(None) is False


def test_openapi_has_phase2_export_ops():
    spec = g.build_openapi_spec("https://msi.example.com")
    paths = spec["paths"]
    # 生成開始は POST（副作用あり）
    ip = paths["/api/gpt/projects/{pid}/sub/{sid}/exports/interactive"]
    assert "post" in ip and ip["post"]["operationId"] == "startInteractiveExport"
    # 状態は GET
    assert paths["/api/gpt/exports/jobs/{job_id}"]["get"]["operationId"] == "getExportJob"
    # ★ ver52.0: ファイル本体を返す operation は Action 仕様から外した
    #   （バイナリ応答は Actions が扱えない）。ルート自体は残っている。
    assert "/api/gpt/exports/jobs/{job_id}/file" not in paths
    # これらは鍵不要ではない（health のように security:[] を持たない → グローバル鍵が効く）
    assert "security" not in ip["post"]
