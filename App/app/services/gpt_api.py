"""ChatGPT 連携用の読み取り専用 API（受付窓口 `/api/gpt/*`）。 ver41.0

このアプリを ChatGPT（Custom GPT の Action）から使い、データの検索・抽出・
保存済みエクスポート取得を行えるようにするための「受付窓口」。

設計方針:
- **読み取り専用**。書き換え・削除・管理系は一切公開しない。
- **合言葉 (X-API-Key)** で保護。鍵はサーバ側 (`config.GPT_API_KEY`) のみに保持し、
  照合は `hmac.compare_digest`（定数時間比較）。未設定なら 503 で窓口を閉じる (fail-closed)。
- **R を起動しない**。抽出は「ウォームキャッシュがある場合のみ」読む
  （`SeuratBridge.get_cache_dir` + `_is_cached` → CSV/JSON を直接読む）。
  キャッシュが無い（＝アプリで未オープン）場合は R を走らせず、その旨を返す。
- 大きいファイル（保存済み MetaboAnalyst エクスポート）は base64 で載せず、
  `send_file` でストリーム配信（ver40.1 のダウンロード方式を踏襲）。

テスト容易性のため、判定・整形ロジックは **flask / dash / pyarrow 非依存の純関数**
として module 直下に置き、flask や重い依存はハンドラ内で遅延 import する。
"""
from __future__ import annotations

import base64
import hmac
import json
import logging
from pathlib import Path

logger = logging.getLogger("msi.gpt_api")

# 鍵不要でアクセスできるパス（ChatGPT が Action 設定時に取得する契約と死活）
_KEYFREE_PATHS = ("/api/gpt/openapi.json", "/api/gpt/health")

# 補正手法名 → RDS ファイル名のヒント（表示用の既定順）
_METHOD_ORDER = ("Harmony", "RPCA", "PCA", "PCA (uncorrected)")


# ===========================================================================
# 認証判定（flask 非依存・純関数 → 単体テスト可能）
# ===========================================================================
def key_decision(path: str, provided_key: str, configured_key: str):
    """`/api/gpt/*` のアクセス可否を返す ``(allow: bool, status: int, error: str|None)``。

    - `openapi.json` / `health` は鍵不要（契約・死活のみ）。
    - 鍵未設定 (`configured_key` 空) なら 503（fail-closed。無防備公開を防ぐ）。
    - 提示鍵が一致すれば許可、それ以外は 401。定数時間比較を用いる。
    """
    if path in _KEYFREE_PATHS:
        return True, 200, None
    if not configured_key:
        return False, 503, "GPT API is not configured (set GPT_API_KEY)"
    provided = provided_key or ""
    if provided and hmac.compare_digest(provided, configured_key):
        return True, 200, None
    return False, 401, "invalid or missing API key (header: X-API-Key)"


# ===========================================================================
# ダウンロード参照トークン（flask 非依存・純関数）
# ===========================================================================
def encode_ref(pid: str, sid: str, kind: str, name: str) -> str:
    """ダウンロード対象を指す不透明トークン（URL-safe base64 の JSON）。

    パスそのものは載せず (pid, sid, kind, name) のみを載せる。ダウンロード時に
    サーバ側で列挙し直して name 一致を検証するため、パストラバーサルは起きない。
    """
    raw = json.dumps(
        {"p": pid, "s": sid, "k": kind, "n": name}, separators=(",", ":")
    ).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_ref(token: str):
    """`encode_ref` の逆。壊れていれば None。"""
    if not token or not isinstance(token, str):
        return None
    try:
        pad = "=" * (-len(token) % 4)
        raw = base64.urlsafe_b64decode(token + pad)
        d = json.loads(raw)
        if isinstance(d, dict) and all(k in d for k in ("p", "s", "k", "n")):
            return d
    except Exception:  # noqa: BLE001
        pass
    return None


# ===========================================================================
# レスポンス整形（flask 非依存・純関数）
# ===========================================================================
def sub_summary(s: dict) -> dict:
    """サブプロジェクト辞書から API 用の要約を作る。"""
    s = s or {}
    return {
        "id": s.get("id"),
        "name": s.get("name"),
        "experiment_date": s.get("experiment_date", ""),
        "ms_instrument": s.get("ms_instrument", ""),
        "polarity": s.get("polarity", []),
        "has_result": bool(s.get("last_result_dir")),
    }


def project_summary(p: dict) -> dict:
    """プロジェクト一覧用の要約。"""
    p = p or {}
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "experiment_date": p.get("experiment_date", ""),
        "last_modified": p.get("last_modified", ""),
        "n_sub_projects": len(p.get("sub_projects", []) or []),
    }


def project_detail(p: dict) -> dict:
    """プロジェクト詳細（サブプロジェクト要約付き）。"""
    p = p or {}
    return {
        "id": p.get("id"),
        "name": p.get("name"),
        "experiment_date": p.get("experiment_date", ""),
        "memo": p.get("memo", ""),
        "last_modified": p.get("last_modified", ""),
        "sub_projects": [sub_summary(s) for s in (p.get("sub_projects") or [])],
    }


def _rec_mz(rec: dict):
    """レコードから m/z を float で取り出す（無ければ feature 文字列から抽出）。"""
    v = rec.get("mz")
    try:
        if v is not None:
            return float(v)
    except (TypeError, ValueError):
        pass
    from app.utils.deg_utils import extract_mz_numeric
    mz = extract_mz_numeric(str(rec.get("feature", "")))
    return mz if mz != float("inf") else None


def filter_compounds(records, query=None, mz=None, tol=0.01,
                     lipid_class=None, limit=50):
    """化合物アノテーションのレコード列を名前 / m/z / 脂質クラスで絞り込む（純関数）。

    Parameters
    ----------
    records : list[dict]
        各 dict は ``feature`` と、``compound`` / ``display_name`` / ``lipid_class``
        / ``mz`` 等（`seurat_bridge` の feature_annotations レコード）を含む。
    query : str|None      名前部分一致（compound / display_name / lipid_class / feature）。
    mz : float|None       m/z 中心。指定時 ``|rec.mz - mz| <= tol`` のみ。
    tol : float           m/z 許容差（Da）。
    lipid_class : str|None 脂質クラス部分一致。
    limit : int           返す最大件数。
    """
    q = (query or "").strip().lower()
    lc = (lipid_class or "").strip().lower()
    out = []
    for rec in records or []:
        if q:
            hay = " ".join(str(rec.get(k, "")) for k in
                           ("compound", "display_name", "lipid_class", "feature")).lower()
            if q not in hay:
                continue
        if lc:
            if lc not in str(rec.get("lipid_class", "")).lower():
                continue
        rmz = _rec_mz(rec)
        if mz is not None:
            if rmz is None or abs(rmz - float(mz)) > float(tol):
                continue
        out.append({
            "feature": rec.get("feature"),
            "mz": rmz,
            "compound": rec.get("compound") or rec.get("display_name"),
            "display_name": rec.get("display_name"),
            "lipid_class": rec.get("lipid_class"),
            "adduct": rec.get("adduct"),
            "formula": rec.get("formula"),
            "database": rec.get("database"),
            "ppm": rec.get("ppm"),
        })
    # 並び順: m/z 指定時は近い順、そうでなければ m/z 昇順
    if mz is not None:
        out.sort(key=lambda r: abs((r["mz"] if r["mz"] is not None else 1e18) - float(mz)))
    else:
        out.sort(key=lambda r: (r["mz"] is None, r["mz"] if r["mz"] is not None else 0))
    if limit and limit > 0:
        out = out[:int(limit)]
    return out


def _marker_sort_key(r: dict):
    p = r.get("p_val_adj_raw")
    try:
        p = float(p)
        if p != p:  # NaN
            p = 1.0
    except (TypeError, ValueError):
        p = 1.0
    fc = r.get("avg_log2FC", 0) or 0
    try:
        fc = float(fc)
        if fc != fc:
            fc = 0.0
    except (TypeError, ValueError):
        fc = 0.0
    return (p, -abs(fc))


def _clean_marker(r: dict) -> dict:
    out = {
        "gene": r.get("gene"),
        "cluster": r.get("cluster"),
        "avg_log2FC": r.get("avg_log2FC"),
        "p_val_adj": r.get("p_val_adj"),
    }
    for k in ("annotation", "pct.1", "pct.2"):
        if k in r:
            out[k] = r[k]
    return out


def shape_markers(records, cluster=None, top=None):
    """DEG レコード列をクラスタ絞り込み＋有意度順に整形（純関数）。

    ``cluster`` 指定時はそのクラスタのみ。``top`` 指定時は（クラスタ指定なら上位N、
    未指定ならクラスタごとに上位N）。並びは (調整p値 昇順, |log2FC| 降順)。
    """
    recs = list(records or [])
    if cluster is not None and str(cluster) != "":
        recs = [r for r in recs if str(r.get("cluster", "")) == str(cluster)]
    recs.sort(key=_marker_sort_key)
    if top:
        top = int(top)
        if cluster is not None and str(cluster) != "":
            recs = recs[:top]
        else:
            from collections import defaultdict
            by = defaultdict(list)
            for r in recs:
                by[str(r.get("cluster", ""))].append(r)
            recs = []
            for _cl, rs in by.items():
                recs.extend(sorted(rs, key=_marker_sort_key)[:top])
    return [_clean_marker(r) for r in recs]


def shape_clusters(cluster_records, meta) -> dict:
    """クラスタ統計（cluster_stats.csv のレコード列）と meta を要約（純関数）。"""
    recs = list(cluster_records or [])
    return {
        "n_clusters": len(recs),
        "clusters": recs,
        "meta": meta or {},
    }


def to_jsonable(obj):
    """numpy / pandas 由来の型を JSON 化可能な素の型へ再帰変換する（純関数）。

    Flask の `jsonify` は numpy スカラ（int64 等）や NaN/NaT を扱えないため、
    pandas から読んだレコードを返す前にこれを通す。NaN/Inf/NA/NaT は None にする。
    """
    if obj is None:
        return None
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(v) for v in obj]
    # numpy スカラ / 配列
    try:
        import numpy as np
        if isinstance(obj, np.generic):
            obj = obj.item()
        elif isinstance(obj, np.ndarray):
            return [to_jsonable(v) for v in obj.tolist()]
    except Exception:  # noqa: BLE001
        pass
    # pandas NA / NaT （スカラのみ）
    try:
        import pandas as pd
        if pd.isna(obj):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return obj
    if isinstance(obj, float):
        import math
        return None if (math.isnan(obj) or math.isinf(obj)) else obj
    if isinstance(obj, str):
        return obj
    # Timestamp などは文字列化（最後の砦）
    try:
        return obj.isoformat()  # datetime / pandas.Timestamp
    except Exception:  # noqa: BLE001
        return str(obj)


# ===========================================================================
# OpenAPI 仕様（flask 非依存・純関数）
# ===========================================================================
def build_openapi_spec(base_url: str = "") -> dict:
    """Custom GPT の Action に登録する OpenAPI 3.1 仕様を返す。

    `base_url` はハンドラが `request.url_root` から与える（実ホストを servers に反映）。
    鍵は仕様書には出さず、securityScheme（apiKey / header / X-API-Key）だけ記述する。
    """
    server_url = (base_url or "https://YOUR-DOMAIN").rstrip("/")

    def _p(name, where="query", typ="string", required=False, desc=""):
        return {"name": name, "in": where, "required": required,
                "schema": {"type": typ}, "description": desc}

    obj = {"type": "object"}
    return {
        "openapi": "3.1.0",
        "info": {
            "title": "MSI Analysis 受付 API (read-only)",
            "version": "1.0.0",
            "description": (
                "MSI 解析アプリの読み取り専用 API。プロジェクト/化合物/クラスタ/"
                "マーカーの検索・抽出と、保存済み MetaboAnalyst エクスポートの取得。"
            ),
        },
        "servers": [{"url": server_url}],
        "security": [{"ApiKeyAuth": []}],
        "components": {
            "securitySchemes": {
                "ApiKeyAuth": {"type": "apiKey", "in": "header", "name": "X-API-Key"}
            }
        },
        "paths": {
            "/api/gpt/health": {"get": {
                "operationId": "health", "summary": "死活確認", "security": [],
                "responses": {"200": {"description": "OK",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects": {"get": {
                "operationId": "listProjects", "summary": "プロジェクト一覧",
                "responses": {"200": {"description": "一覧",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects/{pid}": {"get": {
                "operationId": "getProject", "summary": "プロジェクト詳細",
                "parameters": [_p("pid", "path", required=True)],
                "responses": {"200": {"description": "詳細",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects/{pid}/sub/{sid}/clusters": {"get": {
                "operationId": "getClusters",
                "summary": "クラスタ統計（ウォームキャッシュがある場合）",
                "parameters": [_p("pid", "path", required=True),
                               _p("sid", "path", required=True),
                               _p("method", desc="Harmony / RPCA / PCA")],
                "responses": {"200": {"description": "クラスタ統計",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects/{pid}/sub/{sid}/markers": {"get": {
                "operationId": "getMarkers", "summary": "クラスタ別マーカー（DEG）",
                "parameters": [_p("pid", "path", required=True),
                               _p("sid", "path", required=True),
                               _p("method", desc="Harmony / RPCA / PCA"),
                               _p("cluster", desc="クラスタ番号（省略で全クラスタ）"),
                               _p("top", "query", "integer", desc="各クラスタ上位N")],
                "responses": {"200": {"description": "マーカー",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects/{pid}/sub/{sid}/compounds": {"get": {
                "operationId": "searchCompounds",
                "summary": "化合物アノテーション検索（名前 / m/z / 脂質クラス）",
                "parameters": [_p("pid", "path", required=True),
                               _p("sid", "path", required=True),
                               _p("method", desc="Harmony / RPCA / PCA"),
                               _p("query", desc="名前部分一致"),
                               _p("mz", "query", "number", desc="m/z 中心"),
                               _p("tol", "query", "number", desc="m/z 許容差(Da) 既定0.01"),
                               _p("lipid_class", desc="脂質クラス部分一致"),
                               _p("limit", "query", "integer", desc="最大件数 既定50")],
                "responses": {"200": {"description": "検索結果",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects/{pid}/sub/{sid}/outputs": {"get": {
                "operationId": "listOutputs", "summary": "出力画像一覧（ダウンロードURL付き）",
                "parameters": [_p("pid", "path", required=True),
                               _p("sid", "path", required=True)],
                "responses": {"200": {"description": "画像一覧",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects/{pid}/sub/{sid}/exports": {"get": {
                "operationId": "listExports",
                "summary": "保存済み MetaboAnalyst エクスポート一覧（ダウンロードURL付き）",
                "parameters": [_p("pid", "path", required=True),
                               _p("sid", "path", required=True)],
                "responses": {"200": {"description": "エクスポート一覧",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/download/{token}": {"get": {
                "operationId": "download",
                "summary": "エクスポート/出力ファイルのダウンロード（一覧が返すトークン）",
                "parameters": [_p("token", "path", required=True)],
                "responses": {"200": {"description": "ファイル本体",
                                      "content": {"application/octet-stream":
                                                  {"schema": {"type": "string",
                                                              "format": "binary"}}}},
                              "404": {"description": "not found"}}}},
        },
    }


# ===========================================================================
# データアクセス（ハンドラ内で使う。重い依存は遅延 import。R は起動しない）
# ===========================================================================
def _resolve_sub(pid: str, sid: str):
    """(pid, sid) からプロジェクト/サブと結果フォルダ・rds_map を解決する。

    Returns dict or None:
        {project, sub, result_dir, data_folder, ms_instrument, rds_map}
    R は起動しない（rds_map はファイル名の走査のみ）。
    """
    from app.services.project_manager import get_project, get_sub_project
    project = get_project(pid)
    if not project:
        return None
    sub = get_sub_project(pid, sid)
    if not sub:
        return None

    # クロスマシンで壊れた絶対パスを現在の候補ディレクトリ配下で修復
    try:
        from app.services.path_resolver import resolve_project_paths
        fixed, _unresolved = resolve_project_paths(dict(sub))
    except Exception:  # noqa: BLE001
        fixed = dict(sub)

    result_dir = fixed.get("last_result_dir") or fixed.get("output_dir") or ""
    data_folder = fixed.get("data_folder") or ""
    ms_instrument = fixed.get("ms_instrument") or "TIMS"

    rds_map = {}
    if result_dir and Path(result_dir).is_dir():
        try:
            from app.callbacks.interactive_callbacks import _detect_integration_methods
            rds_map = _detect_integration_methods(result_dir, include_derived=False) or {}
        except Exception as e:  # noqa: BLE001
            logger.warning("rds_map 解決に失敗 (pid=%s sid=%s): %s", pid, sid, e)

    return {
        "project": project, "sub": sub, "result_dir": result_dir,
        "data_folder": data_folder, "ms_instrument": ms_instrument,
        "rds_map": rds_map,
    }


def _pick_method(rds_map: dict, method):
    """要求手法 method に対する (method_name, rds_path) を返す。無ければ既定順で先頭。"""
    if not rds_map:
        return None, None
    if method and method in rds_map:
        return method, rds_map[method]
    for m in _METHOD_ORDER:
        if m in rds_map:
            return m, rds_map[m]
    k = next(iter(rds_map))
    return k, rds_map[k]


def _warm_cache_dir(rds_path):
    """ウォーム（抽出済み）なら cache_dir を返す。未抽出なら None（R は起動しない）。"""
    if not rds_path:
        return None
    try:
        from app.services.seurat_bridge import SeuratBridge
        b = SeuratBridge()
        cd = b.get_cache_dir(rds_path)
        if cd and b._is_cached(cd):
            return Path(cd)
    except Exception as e:  # noqa: BLE001
        logger.warning("warm cache 判定に失敗: %s", e)
    return None


def _read_clusters(cache_dir):
    """cache_dir から cluster_stats.csv と extraction_meta.json を読む（R なし）。"""
    import pandas as pd
    recs, meta = [], {}
    stats_p = Path(cache_dir) / "cluster_stats.csv"
    meta_p = Path(cache_dir) / "extraction_meta.json"
    if stats_p.exists():
        try:
            recs = pd.read_csv(stats_p).to_dict("records")
        except Exception as e:  # noqa: BLE001
            logger.warning("cluster_stats.csv 読込失敗: %s", e)
    if meta_p.exists():
        try:
            meta = json.loads(meta_p.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.warning("extraction_meta.json 読込失敗: %s", e)
    return recs, meta


def _read_annotations(cache_dir):
    """cache_dir/feature_annotations.json を読み、レコード列に整形（R なし・pyarrow 不要）。"""
    p = Path(cache_dir) / "feature_annotations.json"
    if not p.exists():
        return []
    try:
        fmap = json.loads(p.read_text(encoding="utf-8")) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("feature_annotations.json 読込失敗: %s", e)
        return []
    out = []
    for feat, rec in fmap.items():
        if not isinstance(rec, dict):
            continue
        r = dict(rec)
        r["feature"] = feat
        out.append(r)
    return out


def _list_exports(rds_map: dict):
    """rds_map の各 RDS 隣の metaboanalyst_exports/*.zip|*.csv を列挙（重複パス除去）。"""
    seen_dirs = set()
    items = []
    for method, rds_path in (rds_map or {}).items():
        try:
            exp_dir = Path(rds_path).parent / "metaboanalyst_exports"
        except Exception:  # noqa: BLE001
            continue
        key = str(exp_dir.resolve()) if exp_dir else None
        if not key or key in seen_dirs:
            continue
        seen_dirs.add(key)
        if not exp_dir.is_dir():
            continue
        for f in sorted(exp_dir.iterdir()):
            if not f.is_file():
                continue
            if f.suffix.lower() not in (".zip", ".csv"):
                continue
            try:
                st = f.stat()
            except OSError:
                continue
            items.append({
                "id": f.name,          # このディレクトリ内では一意
                "filename": f.name,
                "path": str(f),
                "size_bytes": st.st_size,
                "modified": _iso(st.st_mtime),
            })
    return items


def _list_outputs(result_dir: str):
    """result_dir 配下の出力画像を列挙し、カテゴリ/クラスタ番号を付ける（R なし）。"""
    items = []
    root = Path(result_dir) if result_dir else None
    if not root or not root.is_dir():
        return items
    from app.services.results_viewer import categorize_image, extract_cluster_number
    exts = {".png", ".jpg", ".jpeg"}
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in exts:
            continue
        try:
            rel = str(f.relative_to(root))
            st = f.stat()
        except (ValueError, OSError):
            continue
        items.append({
            "id": rel,                 # result_dir 相対で一意
            "filename": f.name,
            "path": str(f),
            "category": categorize_image(str(f)),
            "cluster": extract_cluster_number(str(f)),
            "size_bytes": st.st_size,
            "modified": _iso(st.st_mtime),
        })
    items.sort(key=lambda d: (d["category"], d["id"]))
    return items


def _iso(ts: float) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:  # noqa: BLE001
        return ""


# ===========================================================================
# Flask への登録（flask はここで遅延 import）
# ===========================================================================
def register_gpt_api(server) -> None:
    """Flask server に `/api/gpt/*` の受付窓口を登録する。

    呼び出し順序: `auth_middleware.register(server)` の **後** に呼ぶこと
    （`_require_login` が `/api/gpt/` を bypass した後に、本 before_request が
    X-API-Key を照合する）。
    """
    from flask import request, jsonify, send_file, abort

    def _cfg_key() -> str:
        from app.config import GPT_API_KEY
        return GPT_API_KEY or ""

    def _json(data, status=200):
        resp = jsonify(to_jsonable(data))
        resp.status_code = status
        return resp

    def _ok(payload):
        d = {"ok": True}
        d.update(payload)
        return _json(d, 200)

    def _fail(msg, status=400):
        return _json({"ok": False, "error": msg}, status)

    # ---- 認証 (before_request): /api/gpt/* のみ X-API-Key を要求 --------------
    def _gpt_before_request():
        path = request.path
        if not path.startswith("/api/gpt/"):
            return None
        allow, status, err = key_decision(
            path, request.headers.get("X-API-Key", ""), _cfg_key()
        )
        if allow:
            return None
        return _json({"ok": False, "error": err}, status)

    server.before_request(_gpt_before_request)

    # ---- 契約・死活 ----------------------------------------------------------
    def _health():
        from app.version import version_label
        return _ok({
            "app_version": version_label(),
            "gpt_api": "enabled" if _cfg_key() else "disabled",
        })

    def _openapi():
        return _json(build_openapi_spec(request.url_root))

    # ---- プロジェクト --------------------------------------------------------
    def _projects():
        from app.services.project_manager import list_projects
        return _ok({"projects": [project_summary(p) for p in list_projects()]})

    def _project(pid):
        from app.services.project_manager import get_project
        p = get_project(pid)
        if not p:
            return _fail("project not found", 404)
        return _ok({"project": project_detail(p)})

    # ---- クラスタ統計（ウォームのみ） ---------------------------------------
    def _clusters(pid, sid):
        r = _resolve_sub(pid, sid)
        if not r:
            return _fail("project/sub not found", 404)
        method, rds = _pick_method(r["rds_map"], request.args.get("method"))
        if not rds:
            return _fail("この結果には解析済み RDS が見つかりません。", 404)
        cache_dir = _warm_cache_dir(rds)
        if not cache_dir:
            return _ok({"warm": False, "method": method,
                        "message": "抽出キャッシュ未生成です。アプリで一度開くと取得できます。"})
        recs, meta = _read_clusters(cache_dir)
        return _ok({"warm": True, "method": method, **shape_clusters(recs, meta)})

    # ---- マーカー（DEG。純ファイル読み） ------------------------------------
    def _markers(pid, sid):
        r = _resolve_sub(pid, sid)
        if not r:
            return _fail("project/sub not found", 404)
        if not r["result_dir"]:
            return _fail("結果フォルダが未設定です。", 404)
        method = request.args.get("method") or "Harmony"
        cluster = request.args.get("cluster")
        top = request.args.get("top")
        try:
            top = int(top) if top not in (None, "") else None
        except ValueError:
            top = None
        from app.utils.deg_utils import load_deg_results
        recs = load_deg_results(Path(r["result_dir"]), method)
        if recs is None:
            return _ok({"method": method, "markers": [],
                        "message": "この手法の DEG 結果が見つかりません。"})
        return _ok({"method": method,
                    "markers": shape_markers(recs, cluster=cluster, top=top)})

    # ---- 化合物検索（ウォームのアノテーション） -----------------------------
    def _compounds(pid, sid):
        r = _resolve_sub(pid, sid)
        if not r:
            return _fail("project/sub not found", 404)
        method, rds = _pick_method(r["rds_map"], request.args.get("method"))
        if not rds:
            return _fail("この結果には解析済み RDS が見つかりません。", 404)
        cache_dir = _warm_cache_dir(rds)
        if not cache_dir:
            return _ok({"warm": False, "method": method, "compounds": [],
                        "message": "抽出キャッシュ未生成です。アプリで一度開くと取得できます。"})
        recs = _read_annotations(cache_dir)
        mz = request.args.get("mz")
        try:
            mz = float(mz) if mz not in (None, "") else None
        except ValueError:
            mz = None
        try:
            tol = float(request.args.get("tol") or 0.01)
        except ValueError:
            tol = 0.01
        try:
            limit = int(request.args.get("limit") or 50)
        except ValueError:
            limit = 50
        result = filter_compounds(
            recs, query=request.args.get("query"), mz=mz, tol=tol,
            lipid_class=request.args.get("lipid_class"), limit=limit,
        )
        return _ok({"warm": True, "method": method,
                    "n_total_annotated": len(recs), "compounds": result})

    # ---- 出力画像一覧 --------------------------------------------------------
    def _outputs(pid, sid):
        r = _resolve_sub(pid, sid)
        if not r:
            return _fail("project/sub not found", 404)
        items = _list_outputs(r["result_dir"])
        out = []
        for it in items:
            tok = encode_ref(pid, sid, "output", it["id"])
            out.append({
                "filename": it["filename"], "category": it["category"],
                "cluster": it["cluster"], "size_bytes": it["size_bytes"],
                "modified": it["modified"],
                "download_token": tok,
                "download_url": "/api/gpt/download/" + tok,
            })
        return _ok({"outputs": out})

    # ---- 保存済み MetaboAnalyst エクスポート一覧 ----------------------------
    def _exports(pid, sid):
        r = _resolve_sub(pid, sid)
        if not r:
            return _fail("project/sub not found", 404)
        items = _list_exports(r["rds_map"])
        out = []
        for it in items:
            tok = encode_ref(pid, sid, "export", it["id"])
            out.append({
                "filename": it["filename"], "size_bytes": it["size_bytes"],
                "modified": it["modified"],
                "download_token": tok,
                "download_url": "/api/gpt/download/" + tok,
            })
        return _ok({"exports": out})

    # ---- ダウンロード（列挙し直して検証 → send_file ストリーム） -------------
    def _download(token):
        ref = decode_ref(token)
        if not ref:
            abort(404)
        r = _resolve_sub(ref["p"], ref["s"])
        if not r:
            abort(404)
        kind, name = ref["k"], ref["n"]
        if kind == "export":
            files = _list_exports(r["rds_map"])
        elif kind == "output":
            files = _list_outputs(r["result_dir"])
        else:
            abort(404)
        match = next((f for f in files if f["id"] == name), None)
        if not match or not Path(match["path"]).exists():
            abort(404)
        return send_file(match["path"], as_attachment=True,
                         download_name=match["filename"])

    # ---- ルート登録 ----------------------------------------------------------
    routes = [
        ("/api/gpt/health", "gpt.health", _health),
        ("/api/gpt/openapi.json", "gpt.openapi", _openapi),
        ("/api/gpt/projects", "gpt.projects", _projects),
        ("/api/gpt/projects/<pid>", "gpt.project", _project),
        ("/api/gpt/projects/<pid>/sub/<sid>/clusters", "gpt.clusters", _clusters),
        ("/api/gpt/projects/<pid>/sub/<sid>/markers", "gpt.markers", _markers),
        ("/api/gpt/projects/<pid>/sub/<sid>/compounds", "gpt.compounds", _compounds),
        ("/api/gpt/projects/<pid>/sub/<sid>/outputs", "gpt.outputs", _outputs),
        ("/api/gpt/projects/<pid>/sub/<sid>/exports", "gpt.exports", _exports),
        ("/api/gpt/download/<token>", "gpt.download", _download),
    ]
    for rule, endpoint, view in routes:
        server.add_url_rule(rule, endpoint=endpoint, view_func=view, methods=["GET"])

    logger.info("GPT API registered (/api/gpt/*); key=%s",
                "set" if _cfg_key() else "UNSET(closed)")
