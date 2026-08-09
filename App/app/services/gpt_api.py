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
import re
import threading
from pathlib import Path

logger = logging.getLogger("msi.gpt_api")

# 鍵不要でアクセスできるパス（ChatGPT が Action 設定時に取得する契約と死活）
_KEYFREE_PATHS = ("/api/gpt/openapi.json", "/api/gpt/health")

# 補正手法名 → RDS ファイル名のヒント（表示用の既定順）
_METHOD_ORDER = ("Harmony", "RPCA", "PCA", "PCA (uncorrected)")

# インタラクティブ Export のオンデマンド生成（重い＝R 抽出が走り得る）の同時実行上限。
# 1 ユーザーの ChatGPT からの利用を想定。過負荷を防ぐため小さめに固定する。
_GPT_EXPORT_MAX_CONCURRENCY = 2
_GPT_EXPORT_SEM = threading.BoundedSemaphore(_GPT_EXPORT_MAX_CONCURRENCY)

# 出力形式の許可値（TIMS のみ意味を持つ。DESI は常に xlsx）。
_EXPORT_FORMATS = ("parquet", "csv", "xlsx")

# ---------------------------------------------------------------------------
# 入力契約 (ver52.0)
# ---------------------------------------------------------------------------
# ★ 監査で出た Critical は全部「失敗せずに、もっともらしい間違った結果を返す」型。
#   GPT の Instructions ではなく **サーバー側**で直す。Instructions は
#   「お願い」であって契約ではなく、別クライアントやモデル挙動の揺れを防げない。
TOP_DEFAULT = 10
TOP_MIN = 1
TOP_MAX = 50

DIRECTIONS = ("up", "down", "both")
DIRECTION_DEFAULT = "both"

# マーカーの並び順。**符号を見ない**ので「高発現の上位」ではない。
# 応答にそのまま載せて、GPT が誤って「高発現」と書けないようにする。
MARKER_SORT_DESC = "p_val_adj asc, abs(avg_log2FC) desc"

# Custom GPT Actions はリクエスト/レスポンスとも 10 万文字未満。
# 余裕を見て手前で切る（Action 側で失敗すると原因が利用者に見えない）。
MAX_RESPONSE_CHARS = 90_000


class ApiError:
    """構造化エラー (ver52.0)。

    ★ 従来は「入力形式が不正」「クラスタが存在しない」「本当にマーカーが無い」
      「キャッシュ未生成」が **すべて `ok:true` + 空配列**で、利用者にも GPT にも
      区別できなかった。区別できないと、GPT は「該当なし」と要約してしまう。
    """

    __slots__ = ("code", "message", "status", "detail")

    def __init__(self, code: str, message: str, status: int = 422, detail=None):
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail or {}

    def to_payload(self) -> dict:
        d = {"ok": False, "code": self.code, "error": self.message}
        d.update(self.detail)
        return d

    def __repr__(self):  # デバッグ用
        return f"ApiError({self.code!r}, status={self.status})"


def parse_top(raw):
    """`top` を検証して (値, ApiError|None) を返す (ver52.0 / F-01)。

    ★ 従来は既定も上下限も無く、
        - 省略 → `shape_markers(top=None)` → **全件**
        - `top=0` → `if top:` が偽 → **全件**（「0 件」ではない）
      となり、Actions の応答上限に当たって `ResponseTooLargeError` になっていた。
      利用者に見えるのはそのエラーだけで、何を直せばよいか分からない。
    """
    if raw is None or raw == "":
        return TOP_DEFAULT, None
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, ApiError(
            "INVALID_TOP",
            f"top は整数で指定してください（{TOP_MIN}〜{TOP_MAX}）。受け取った値: {raw!r}")
    if val < TOP_MIN or val > TOP_MAX:
        return None, ApiError(
            "INVALID_TOP",
            f"top は {TOP_MIN} 以上 {TOP_MAX} 以下で指定してください。"
            f"受け取った値: {val}")
    return val, None


def parse_clusters(raw):
    """`cluster` を検証して (リスト|None, ApiError|None) を返す (ver52.0 / F-02)。

    ★ 従来は `str(r["cluster"]) == str(cluster)` の完全一致だったので、
      `"1,3,7"` は **どのレコードにも一致せず必ず 0 件**。しかも `ok:true` なので
      「マーカーが無い」と読める。実際には入力形式を処理できなかっただけで、
      **研究結果がそのまま欠落する**。

    422 で弾くこともできるが、正しく複数として処理すれば
    「1 質問あたり N 回呼ぶ」構造（監査 F-07）も同時に解消できる。
    戻り値の `markers[]` は平らなままなので既存の GPT 設定を壊さない。

    None は「全クラスタ」を意味する（従来と同じ）。
    """
    if raw is None or raw == "":
        return None, None
    parts = [p.strip() for p in str(raw).split(",")]
    if any(p == "" for p in parts):
        return None, ApiError(
            "INVALID_CLUSTER_FORMAT",
            f"cluster の書式が不正です（空の要素があります）: {raw!r}。"
            "例: '1' または '1,3,7'")
    seen, out = set(), []
    for p in parts:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out, None


LIMIT_DEFAULT = 50
LIMIT_MIN = 1
LIMIT_MAX = 200
TOL_DEFAULT = 0.01
TOL_MAX = 100.0


def parse_limit(raw):
    """`limit` を検証する (ver52.1 / API-03)。

    ★ ver52.0 で `top` に上下限を付けたのに、**まったく同じ穴を `limit` に
      残した**（`filter_compounds` の `if limit and limit > 0` により
      `limit=0` と負値が「無制限」になっていた）。
    """
    if raw is None or raw == "":
        return LIMIT_DEFAULT, None
    try:
        val = int(str(raw).strip())
    except (TypeError, ValueError):
        return None, ApiError(
            "INVALID_LIMIT",
            f"limit は整数で指定してください（{LIMIT_MIN}〜{LIMIT_MAX}）。"
            f"受け取った値: {raw!r}")
    if val < LIMIT_MIN or val > LIMIT_MAX:
        return None, ApiError(
            "INVALID_LIMIT",
            f"limit は {LIMIT_MIN} 以上 {LIMIT_MAX} 以下で指定してください。"
            f"受け取った値: {val}")
    return val, None


def _finite(raw):
    """有限の float として読めれば返す。`nan` / `inf` は None。"""
    import math
    try:
        v = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def parse_mz(raw):
    """`mz` を検証する (ver52.1 / API-04)。

    ★ 従来は例外時に **None にして m/z 絞り込み自体を消して**いた。
      利用者は m/z 検索を頼んだのに、名前検索でも全件検索でもない
      「m/z 指定なしの全件」がそれらしく返っていた。

    ★ さらに `float("nan")` は例外を出さないので、`mz=nan` は try を通過し、
      `abs(x - nan) > tol` → `nan > tol` → False となって
      **全件が絞り込みを素通り**していた（監査の記述より悪い）。
    """
    if raw is None or raw == "":
        return None, None
    v = _finite(raw)
    if v is None:
        return None, ApiError(
            "INVALID_MZ",
            f"mz は有限の数値で指定してください。受け取った値: {raw!r}")
    return v, None


def parse_tol(raw):
    """`tol` を検証する (ver52.1 / API-05)。

    ★ 負の tol は `abs(...) > tol` が常に真になり、**成功扱いの空結果**を返す。
      「入力が不正」と「本当に該当なし」が区別できなかった。
    """
    if raw is None or raw == "":
        return TOL_DEFAULT, None
    v = _finite(raw)
    if v is None or v <= 0 or v > TOL_MAX:
        return None, ApiError(
            "INVALID_TOL",
            f"tol は 0 より大きく {TOL_MAX} 以下の数値で指定してください。"
            f"受け取った値: {raw!r}")
    return v, None


def parse_export_format(raw):
    """`format` を検証する (ver52.1 / API-06)。

    ★ 従来は未知の値を**黙って parquet に差し替え**、応答に `fmt` を
      返しもしなかったので、利用者は別形式の成果物を受け取ったことに
      気づけなかった。
    """
    if raw is None or raw == "":
        return _EXPORT_FORMATS[0], None
    val = str(raw).strip().lower()
    if val not in _EXPORT_FORMATS:
        return None, ApiError(
            "INVALID_FORMAT",
            f"format は {' / '.join(_EXPORT_FORMATS)} のいずれかです。"
            f"受け取った値: {raw!r}")
    return val, None


def resolve_export_methods(raw, rds_map):
    """export の `methods` を検証する (ver52.1 / API-07)。

    ★ 従来は `interactive_data_export` 側の `if not sel: sel = 全手法` により、
      **指定が全部無効だと全手法へ膨らんで**いた。重い R 抽出を指定外の手法にも
      走らせ、成果物の由来を誤らせる。
    ★ さらに `resolve_method` は大小文字を吸収するのに export 経路だけ完全一致
      だったため、`methods=harmony` でも全手法に膨らんでいた（ver52.0 で
      私が作った不整合）。ここで同じ正規化を通す。

    Returns (選択リスト|None, ApiError|None)。None は「全手法」。
    """
    available = [m for m in _METHOD_ORDER if m in (rds_map or {})]
    available += [m for m in (rds_map or {}) if m not in _METHOD_ORDER]
    if raw is None or str(raw).strip() == "":
        return None, None

    wanted = [m.strip() for m in str(raw).split(",") if m.strip()]
    if not wanted:
        return None, ApiError(
            "INVALID_METHODS", f"methods の書式が不正です: {raw!r}")

    lookup = {m.lower(): m for m in available}
    sel, bad = [], []
    for m in wanted:
        canon = lookup.get(m.lower())
        if canon is None:
            bad.append(m)
        elif canon not in sel:
            sel.append(canon)
    if bad:
        return None, ApiError(
            "METHOD_NOT_AVAILABLE",
            f"この解析結果に無い手法が含まれています: {', '.join(bad)}。"
            "別の手法で代用はしません（成果物の由来が分からなくなるため）。",
            409, {"available_methods": available})
    return sel, None


def parse_direction(raw):
    """`direction` を検証して (値, ApiError|None) を返す (ver52.0 / 監査 17.1)。"""
    if raw is None or raw == "":
        return DIRECTION_DEFAULT, None
    val = str(raw).strip().lower()
    if val not in DIRECTIONS:
        return None, ApiError(
            "INVALID_DIRECTION",
            f"direction は {' / '.join(DIRECTIONS)} のいずれかです。受け取った値: {raw!r}")
    return val, None


def resolve_method(requested, rds_map):
    """要求手法を検証して (選択手法, ApiError|None) を返す (ver52.0 / F-03, F-04)。

    ★ 従来の `_pick_method` は、要求手法が `rds_map` に無いと
      `_METHOD_ORDER` の先頭（＝ふつう Harmony）へ**黙って落ちて**いた。
      実測で `PCA` / `PCA (uncorrected)` / `INVALID` がすべて `Harmony` になり、
      **応答の解析手法ラベルが信用できない**状態だった。
      PCA で比較したつもりが Harmony 同士の比較になる。

    規則:
      - 未知の手法名          → 422 INVALID_METHOD（そんな手法は無い）
      - 既知だがこの結果に無い → 409 METHOD_NOT_AVAILABLE ＋ available_methods
      - 省略                  → 既定順の先頭へフォールバックしてよい
                                （要求していないので「置換」ではない）
    表記ゆれは ver51.9 A-2 と同じく大文字小文字・前後空白を無視して吸収する。
    """
    available = [m for m in _METHOD_ORDER if m in (rds_map or {})]
    available += [m for m in (rds_map or {}) if m not in _METHOD_ORDER]
    if not available:
        return None, ApiError(
            "NO_RESULT", "この結果には解析済み RDS が見つかりません。", 404)

    if requested is None or str(requested).strip() == "":
        return available[0], None

    key = str(requested).strip().lower()
    known = {m.lower(): m for m in _METHOD_ORDER}
    known.update({m.lower(): m for m in (rds_map or {})})
    canonical = known.get(key)
    if canonical is None:
        return None, ApiError(
            "INVALID_METHOD",
            f"未知の解析手法です: {requested!r}。"
            f"指定できるのは {' / '.join(_METHOD_ORDER)} です。",
            422, {"available_methods": available})
    if canonical not in (rds_map or {}):
        return None, ApiError(
            "METHOD_NOT_AVAILABLE",
            f"{canonical} はこの解析結果にはありません。"
            "別の手法の結果で代用はしません（結果の由来が分からなくなるため）。",
            409, {"available_methods": available})
    return canonical, None


def _fc_or_none(rec):
    """`avg_log2FC` を float で返す。読めなければ None (ver52.3)。

    ★ 0.0 と「読めなかった」を区別することが要点。従来はどちらも 0.0 に
      落としていたので、読めない record が Up/Down の**両方から消えて**いた。
    """
    raw = rec.get("avg_log2FC", None)
    if raw is None or raw == "":
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return None if v != v else v          # NaN も「読めなかった」扱い


def count_unreadable_fc(records) -> int:
    """`avg_log2FC` を数値化できない record の件数を数える (ver52.3)。

    `marker_outcome` と同じ「dict を返して payload に merge する」形で
    応答へ載せるための純関数。
    """
    return sum(1 for r in (records or []) if _fc_or_none(r) is None)


def marker_outcome(shaped, requested_clusters, available_clusters):
    """マーカーの結果に添える注意書きを返す（無ければ None）(ver52.1 / F-09)。

    ★ ver52.0 では「要求したクラスタが 1 つも実在しないときだけ指定ミス」と
      判定していた。**この設計判断が誤り**で、`cluster=1,999` のように
      **一部だけ存在しない**場合に 999 について何も言わなかった:
        - データが返る   → `code` すら付かず完全な成功に見える
        - データが空     → 「クラスタは存在します」と**断言**する
      どちらでも GPT は「999 にはマーカーが無い」と要約するが、実際は存在しない。
      要求と実在の**差集合**を必ず返す形に改める。

    shaped: 整形後のマーカー
    requested_clusters: 要求されたクラスタのリスト（None = 全クラスタ）
    available_clusters: この結果に実在するクラスタの集合
    """
    avail = {str(c) for c in (available_clusters or ())}
    missing = []
    if requested_clusters and avail:
        missing = [str(c) for c in requested_clusters if str(c) not in avail]

    if shaped and not missing:
        return None                       # 何も問題なし
    out = {}
    if missing:
        out["missing_clusters"] = missing
        out["available_clusters"] = sorted(avail, key=str)
    if missing and len(missing) == len(requested_clusters or []):
        out["code"] = "CLUSTER_NOT_FOUND"
        out["message"] = ("指定したクラスタはこの結果に存在しません: "
                          + ", ".join(missing))
    elif missing:
        out["code"] = "PARTIAL_CLUSTERS"
        out["partial"] = True
        out["message"] = ("一部のクラスタはこの結果に存在しません: "
                          + ", ".join(missing)
                          + "。残りのクラスタの結果のみ返しています。")
    else:
        out["code"] = "NO_MARKERS"
        out["message"] = "条件に合うマーカーがありませんでした（クラスタは存在します）。"
    return out


def limit_response_size(payload: dict, list_key: str):
    """応答が Actions の上限を超えるなら件数を削る (ver52.0 / F-01)。

    ★ Action 側で失敗すると `ResponseTooLargeError` としか出ず、
      利用者には何を直せばよいか分からない。サーバー側で削って
      「削った」と明示するほうが情報量が多い。

    Returns (payload, truncated: bool)
    """
    items = payload.get(list_key)
    if not isinstance(items, list):
        return payload, False
    if len(json.dumps(payload, ensure_ascii=False, default=str)) <= MAX_RESPONSE_CHARS:
        return payload, False

    lo, hi = 0, len(items)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        trial = dict(payload)
        trial[list_key] = items[:mid]
        if len(json.dumps(trial, ensure_ascii=False,
                          default=str)) <= MAX_RESPONSE_CHARS:
            lo = mid
        else:
            hi = mid - 1
    out = dict(payload)
    out[list_key] = items[:lo]
    return out, True


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


def valid_job_id(job_id: str) -> bool:
    """ジョブ ID が `export_progress.new_job()`（uuid4.hex=32桁16進）の形式か（純関数）。

    ダウンロード/状態ルートの path 引数をグロブ/送出に使う前の防御（パストラバーサル対策）。
    """
    return bool(job_id) and bool(re.fullmatch(r"[0-9a-fA-F]{16,64}", str(job_id)))


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
    # ★ ver52.1: `if limit and limit > 0` だと **limit=0 と負値が無制限**になる
    #   （`top=0` と同じ穴を残していた）。境界は parse_limit が保証するが、
    #   純関数として直接呼ばれても破綻しないようにここでも素直に切る。
    if limit is not None:
        out = out[:max(0, int(limit))]
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


def shape_markers(records, cluster=None, top=None, direction=DIRECTION_DEFAULT):
    """DEG レコード列をクラスタ絞り込み＋有意度順に整形（純関数）。

    cluster: None（全クラスタ）/ 文字列 1 個 / **文字列のリスト**。
        ★ ver52.0: リストを受けられるようにした。従来は文字列の完全一致だけで、
          `"1,3,7"` のような入力が **どのレコードにも一致せず必ず 0 件**になり、
          しかも `ok:true` なので「マーカーが無い」と読めた（監査 F-02）。
    top: 各クラスタの上位 N。
    direction: "up" | "down" | "both"。
        ★ ver52.0: 並びは `p 昇順, |log2FC| 降順` で **符号を見ない**ため、
          「上位 N ＝ そのクラスタで高発現」ではない。実測で上位 10 件が全部
          負（＝相対的に低い）になる例があり、GPT が「高発現する上位マーカー」と
          説明していた（監査 17.1）。符号で絞れるようにする。
          既定 "both" は従来の挙動と同じ。

    並びは (調整p値 昇順, |log2FC| 降順) = `MARKER_SORT_DESC`。
    """
    recs = list(records or [])

    if cluster is None or cluster == "":
        wanted = None
    elif isinstance(cluster, (list, tuple, set)):
        wanted = {str(c) for c in cluster}
    else:
        wanted = {str(cluster)}
    if wanted is not None:
        recs = [r for r in recs if str(r.get("cluster", "")) in wanted]

    if direction in ("up", "down"):
        # ★ ver52.3: 以前は読めない `avg_log2FC` を 0.0 に落としていたため、
        #   その record は `> 0` にも `< 0` にも入らず **両方向から消えて**いた。
        #   件数の報告も無いので、切り詰められた一覧が「上位マーカーの全部」として
        #   GPT に渡っていた。入口 (`parse_top` 等) を 2 版続けて硬くした
        #   同じファイルの中で、絞り込み側だけ見ていなかった。
        #   0.0 は「変動なし」という正当な値なので、読めなかったものと区別する。
        keep = []
        for r in recs:
            v = _fc_or_none(r)
            if v is None:
                continue                  # 読めない → 件数は下で別途数える
            if (v > 0) if direction == "up" else (v < 0):
                keep.append(r)
        recs = keep

    recs.sort(key=_marker_sort_key)
    if top:
        top = int(top)
        # ★ 単一クラスタでも複数クラスタでも「クラスタごとに上位 N」で揃える。
        #   従来は単一指定のときだけ全体から N 件取っていたが、結果は同じ
        #   （すでに 1 クラスタに絞られているため）。分岐を無くして
        #   複数クラスタでも 1 クラスタでも同じ意味になるようにする。
        from collections import defaultdict
        by = defaultdict(list)
        for r in recs:
            by[str(r.get("cluster", ""))].append(r)
        recs = []
        for _cl, rs in by.items():
            recs.extend(rs[:top])          # by の各リストは既にソート済み
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

    `base_url` はハンドラが `_public_base_url()` から与える（実ホストを servers に反映）。
    ★ ver52.6: 以前は `request.url_root` を直に渡しており、リバースプロキシ配下で
      `http://` を名乗って ChatGPT の Action から使えなかった。共有リンクと
      同じ `SHARE_BASE_URL → external_base_url()` に寄せてある。
    鍵は仕様書には出さず、securityScheme（apiKey / header / X-API-Key）だけ記述する。
    """
    server_url = (base_url or "https://YOUR-DOMAIN").rstrip("/")

    def _p(name, where="query", typ="string", required=False, desc="",
           enum=None, default=None, minimum=None, maximum=None,
           exclusive_minimum=None):
        """パラメータ定義。

        ★ ver52.0: `enum` / `default` / `minimum` / `maximum` を書けるようにした。
          **モデルが実際に読むのはこの仕様書**なので、サーバー側だけ直しても
          ここが緩いままだとリクエストの作られ方は変わらない
          （`top` 省略で応答上限に当たり続ける、無効な method を送り続ける）。
        """
        schema = {"type": typ}
        if enum is not None:
            schema["enum"] = list(enum)
        if default is not None:
            schema["default"] = default
        if minimum is not None:
            schema["minimum"] = minimum
        if maximum is not None:
            schema["maximum"] = maximum
        if exclusive_minimum is not None:
            schema["exclusiveMinimum"] = exclusive_minimum
        return {"name": name, "in": where, "required": required,
                "schema": schema, "description": desc}

    _method_p = _p("method", enum=_METHOD_ORDER,
                   desc=("解析手法。指定した手法の結果が無い場合は 409 を返し、"
                         "**別手法で代用はしない**。省略時は既定順の先頭。"))

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
                               _method_p],
                "responses": {"200": {"description": "クラスタ統計",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects/{pid}/sub/{sid}/markers": {"get": {
                "operationId": "getMarkers",
                "summary": "クラスタ別マーカー（DEG）",
                "description": (
                    "並びは " + MARKER_SORT_DESC + "。**符号を見ないので"
                    "「上位 N ＝ そのクラスタで高発現」ではない**。"
                    "高発現だけが必要なら direction=up を指定すること。"),
                "parameters": [_p("pid", "path", required=True),
                               _p("sid", "path", required=True),
                               _method_p,
                               _p("cluster",
                                  desc=("クラスタ番号。カンマ区切りで複数指定できる"
                                        "（例: '1,3,7'）。省略すると全クラスタ。")),
                               _p("top", "query", "integer",
                                  default=TOP_DEFAULT, minimum=TOP_MIN,
                                  maximum=TOP_MAX,
                                  desc=(f"各クラスタの上位 N 件（{TOP_MIN}〜{TOP_MAX}、"
                                        f"既定 {TOP_DEFAULT}）。範囲外は 422。")),
                               _p("direction", enum=DIRECTIONS,
                                  default=DIRECTION_DEFAULT,
                                  desc=("up=そのクラスタで高い側のみ / "
                                        "down=低い側のみ / both=両方（既定）"))],
                "responses": {"200": {"description": "マーカー",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects/{pid}/sub/{sid}/compounds": {"get": {
                "operationId": "searchCompounds",
                "summary": "化合物アノテーション検索（名前 / m/z / 脂質クラス）",
                "parameters": [_p("pid", "path", required=True),
                               _p("sid", "path", required=True),
                               _method_p,
                               _p("query", desc="名前部分一致"),
                               _p("mz", "query", "number",
                                  desc="m/z 中心。有限の数値のみ（nan / inf は 422）。"),
                               _p("tol", "query", "number",
                                  default=TOL_DEFAULT, exclusive_minimum=0,
                                  maximum=TOL_MAX,
                                  desc=(f"m/z 許容差(Da)。0 より大きく {TOL_MAX} 以下"
                                        f"（既定 {TOL_DEFAULT}）。範囲外は 422。")),
                               _p("lipid_class", desc="脂質クラス部分一致"),
                               _p("limit", "query", "integer",
                                  default=LIMIT_DEFAULT, minimum=LIMIT_MIN,
                                  maximum=LIMIT_MAX,
                                  desc=(f"最大件数（{LIMIT_MIN}〜{LIMIT_MAX}、"
                                        f"既定 {LIMIT_DEFAULT}）。範囲外は 422。"))],
                "responses": {"200": {"description": "検索結果",
                                      "content": {"application/json": {"schema": obj}}}}}},
            # ★ ver52.0: ファイル本体を返す 2 つの operation
            #   (`download` / `downloadExportJob`) を **Action 仕様から外した**。
            #   Custom GPT Actions はバイナリ応答を扱えず、**必ず失敗する**
            #   （実測: 95KB の PNG でも ClientResponseError。容量の問題ではない）。
            #   仕様に載っていると GPT が繰り返し試みて、利用者には
            #   「ダウンロードできるはずなのにできない」ようにしか見えない。
            #   エンドポイント自体はブラウザからの直接取得のために残してある。
            "/api/gpt/projects/{pid}/sub/{sid}/outputs": {"get": {
                "operationId": "listOutputs",
                "summary": "出力画像の一覧（メタデータのみ。取得はアプリから）",
                "parameters": [_p("pid", "path", required=True),
                               _p("sid", "path", required=True)],
                "responses": {"200": {"description": "画像一覧",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects/{pid}/sub/{sid}/exports": {"get": {
                "operationId": "listExports",
                "summary": ("保存済み MetaboAnalyst エクスポートの一覧"
                            "（メタデータのみ。取得はアプリから）"),
                "parameters": [_p("pid", "path", required=True),
                               _p("sid", "path", required=True)],
                "responses": {"200": {"description": "エクスポート一覧",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/projects/{pid}/sub/{sid}/exports/interactive": {"post": {
                "operationId": "startInteractiveExport",
                "summary": ("インタラクティブ Export（UMAP_cluster）をその場で生成開始"
                            "（非同期。job_id を返す。重い処理）"),
                "parameters": [_p("pid", "path", required=True),
                               _p("sid", "path", required=True),
                               _p("format", enum=_EXPORT_FORMATS,
                                  default=_EXPORT_FORMATS[0],
                                  desc="TIMS のみ有効。範囲外は 422。"),
                               _p("methods",
                                  desc=("カンマ区切り手法名。省略で全手法。"
                                        "この結果に無い手法を含めると 409"
                                        "（別手法で代用はしない）。"))],
                "responses": {"200": {"description": "生成ジョブを開始（job_id/status_url）",
                                      "content": {"application/json": {"schema": obj}}}}}},
            "/api/gpt/exports/jobs/{job_id}": {"get": {
                "operationId": "getExportJob",
                "summary": ("生成ジョブの状態。done ならファイルの保存先を返すが、"
                            "Action からは取得できない（ブラウザ / アプリで開く）。"),
                "parameters": [_p("job_id", "path", required=True)],
                "responses": {"200": {"description": "状態",
                                      "content": {"application/json": {"schema": obj}}},
                              "404": {"description": "unknown job"}}}},
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
    """要求手法 method に対する (method_name, rds_path, ApiError|None) を返す。

    ★ ver52.0: 従来は要求手法が `rds_map` に無いと `_METHOD_ORDER` の先頭
      （ふつう Harmony）へ**黙って落ちて**いた。実測で `PCA` /
      `PCA (uncorrected)` / `INVALID` がすべて `Harmony` になり、
      **応答の解析手法ラベルが信用できない**状態だった。PCA で比較したつもりが
      Harmony 同士の比較になる。検証は `resolve_method` に集約した。
    """
    selected, err = resolve_method(method, rds_map)
    if err is not None:
        return None, None, err
    return selected, (rds_map or {}).get(selected), None


def _method_block(requested, selected, rds_map):
    """応答に載せる手法の由来 (ver52.0)。

    `requested_method` と `selected_method` を分けることで、
    「省略したので既定に落ちた」と「要求どおり」を利用者が区別できる。
    """
    available = [m for m in _METHOD_ORDER if m in (rds_map or {})]
    available += [m for m in (rds_map or {}) if m not in _METHOD_ORDER]
    return {
        "method": selected,                 # 後方互換（既存 GPT 設定が読む）
        "requested_method": requested or None,
        "selected_method": selected,
        "available_methods": available,
    }


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


def readiness(result_dir, rds_map, warm_cache_dirs) -> dict:
    """種別ごとの取得可否を返す (ver52.0 / 監査 F-05, F-06)。

    ★ `warm` 1 個では現状を表せない。実際には
        - クラスタ統計 / 化合物検索 … 抽出キャッシュが要る
        - マーカー(DEG)            … 結果フォルダのファイルを直接読むので不要
        - 出力画像                  … 同上
      という非対称があり、監査でも「cold なのに getMarkers は取れる」と
      観測されている。1 個の bool で返すと、GPT が「まだ何も取れない」と
      誤解して再試行するか、逆に諦める。

    R は起動しない（ファイルの存在確認だけ）。
    """
    has_cache = bool(warm_cache_dirs)
    markers = False
    outputs = False
    if result_dir:
        root = Path(result_dir)
        if root.is_dir():
            try:
                from app.utils.deg_utils import load_deg_results
                for m in ([m for m in _METHOD_ORDER if m in (rds_map or {})]
                          or [None]):
                    if load_deg_results(root, m):
                        markers = True
                        break
            except Exception as e:  # noqa: BLE001
                logger.debug("readiness: DEG 判定に失敗: %s", e)
            try:
                exts = {".png", ".jpg", ".jpeg"}
                outputs = any(f.suffix.lower() in exts
                              for f in root.rglob("*") if f.is_file())
            except OSError:
                outputs = False
    return {"clusters": has_cache, "compounds": has_cache,
            "markers": markers, "outputs": outputs}


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
# インタラクティブ Export のオンデマンド生成（フェーズ2・重い＝R 抽出が走り得る）
# ===========================================================================
def _find_export_job_file(job_id: str):
    """GPT_EXPORT_TMP_DIR 内の `<job_id>__*` を探して Path を返す（無ければ None）。

    ジョブレジストリが上限掃除で消えてもファイルから解決できるようにする。
    job_id は事前に valid_job_id で検証すること。
    """
    if not valid_job_id(job_id):
        return None
    try:
        from app.config import GPT_EXPORT_TMP_DIR
        d = Path(GPT_EXPORT_TMP_DIR)
        if not d.is_dir():
            return None
        matches = sorted(d.glob(f"{job_id}__*"))
        return matches[0] if matches else None
    except Exception as e:  # noqa: BLE001
        logger.warning("export job file 解決に失敗: %s", e)
        return None


def _run_interactive_export(job_id: str, resolved: dict, methods, fmt: str):
    """作業スレッド本体: セッション非依存ドライバで Export を生成し一時ファイルへ保存。

    生成バイト列は base64 でチャットに載せず、GPT_EXPORT_TMP_DIR に保存して
    `/api/gpt/exports/jobs/<job_id>/file`（send_file）でストリーム配信する。
    同時実行は _GPT_EXPORT_SEM で制限（過負荷防止）。
    """
    from app.services import export_progress as ep
    # ver52.1: 許可はハンドラが **thread 生成前** に取得済み。ここでは解放だけ担う。
    acquired = True
    try:
        from app.callbacks.interactive_data_export import (
            build_interactive_export_for_project,
        )
        from app.config import GPT_EXPORT_TMP_DIR
        file_bytes, filename, msg = build_interactive_export_for_project(
            resolved.get("data_folder"), resolved.get("ms_instrument"), fmt,
            resolved.get("rds_map"), resolved.get("result_dir"),
            (resolved.get("project") or {}).get("id"),
            (resolved.get("sub") or {}).get("id"),
            selected_methods=methods,
            progress_cb=lambda p, l="": ep.update_job(job_id, p, l),
        )
        if not file_bytes or not filename:
            ep.fail_job(job_id, msg or "出力に失敗しました")
            return
        GPT_EXPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        ep.sweep_old_files(GPT_EXPORT_TMP_DIR, max_age_sec=3600)  # 古い一時ファイル掃除
        safe = re.sub(r'[\\/:*?"<>|]+', "_", str(filename)) or "export.bin"
        path = Path(GPT_EXPORT_TMP_DIR) / f"{job_id}__{safe}"
        path.write_bytes(file_bytes)
        ep.finish_job(job_id, str(path), filename, msg)
    except Exception as e:  # noqa: BLE001
        logger.exception("[GPT] インタラクティブ Export ジョブ失敗")
        ep.fail_job(job_id, f"❌ エラー: {e}")
    finally:
        if acquired:
            _GPT_EXPORT_SEM.release()


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

    def _public_base_url() -> str:
        """外部から到達できるベース URL (scheme://host) を返す (ver52.6)。

        ★ 共有リンク (`share_manager.build_share_url`) と **同じ出どころ**にする。

        従来ここは `request.url_root` を直に使っていた。Flask の `request.scheme` は
        `X-Forwarded-Proto` を読まない (ProxyFix は本アプリに入れていない) ため、
        Caddy 等のリバースプロキシ配下では **`http://` を名乗る**仕様書ができる。
        ChatGPT の Action は https を要求するので取り込めず、仮に取り込めても
        呼び出しが 80→443 リダイレクトに当たって `X-API-Key` を落としうる。

        共有リンクは `SHARE_BASE_URL → external_base_url()` を通していたので
        正しく https になっており、**同じ 1 リクエストからアプリが 2 通りの
        公開 URL を作っている**状態だった (だから「ブラウザでは開けるのに
        ChatGPT からだけ繋がらない」になる)。出どころを 1 つに寄せて直す。
        """
        from app.config import SHARE_BASE_URL, APP_PORT
        from app.services.url_utils import external_base_url
        return (SHARE_BASE_URL.rstrip("/") if SHARE_BASE_URL
                else external_base_url(APP_PORT))

    def _json(data, status=200):
        resp = jsonify(to_jsonable(data))
        resp.status_code = status
        return resp

    def _ok(payload):
        d = {"ok": True}
        d.update(payload)
        return _json(d, 200)

    def _fail(msg, status=400, code=None):
        d = {"ok": False, "error": msg}
        if code:
            d["code"] = code
        return _json(d, status)

    def _fail_api(err):
        """`ApiError` をそのまま応答にする (ver52.0)。"""
        return _json(err.to_payload(), err.status)

    def _readiness(r):
        """解決済みサブプロジェクトの種別別 readiness (ver52.0)。"""
        warm = [rds for rds in (r["rds_map"] or {}).values()
                if _warm_cache_dir(rds)]
        return readiness(r["result_dir"], r["rds_map"], warm)

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
        # ★ ver52.7: 拒否した事実をサーバ側に残す。
        #   これが無いと「鍵が届いていない」のか「値が違う」のかを
        #   運用側から一切追えない (実際、リバースプロキシのログを
        #   掘るまで原因が分からなかった)。
        #   ★★ 鍵の値・長さ・先頭数文字は**出さない**。有無だけ。
        logger.warning(
            "GPT API 拒否: path=%s status=%d X-API-Key=%s",
            path, status,
            "あり" if request.headers.get("X-API-Key") else "なし")
        return _json({"ok": False, "error": err}, status)

    server.before_request(_gpt_before_request)

    # ---- 想定外例外を JSON にする (ver52.7) ---------------------------------
    def _gpt_json_errors(e):
        """`/api/gpt/*` の例外だけを JSON にする。**Dash 側の挙動は変えない**。

        従来 `/api/gpt/*` に errorhandler が無く、ハンドラ内の想定外例外は
        Werkzeug の **HTML 500** になっていた。`{"ok": false, ...}` という
        自分の契約を破るうえ、呼び出し側 (ChatGPT の Action) には
        `ClientResponseError` としか出ず、サーバ側にも何も残らない。

        ★ `/api/gpt/` 以外は **Flask の既定と同じ**ものを返す:
          - HTTPException → `return e` (ハンドラ未登録時に Flask がする動作)
          - それ以外       → 再送出 (未処理例外として handle_exception へ)
          ここで単純に `raise` すると **HTTPException まで 500 に化ける**ので
          分けている (Dash の 404 が 500 になる)。

        ★ 応答に出すのは例外の**型名だけ**。メッセージや traceback は
          パスや内部状態を含みうるので出さない (ログには出す)。
        """
        from werkzeug.exceptions import HTTPException
        if not request.path.startswith("/api/gpt/"):
            if isinstance(e, HTTPException):
                return e
            raise e
        if isinstance(e, HTTPException):
            return _json({"ok": False,
                          "error": e.description,
                          "code": (e.name or "HTTP_ERROR").upper().replace(" ", "_")},
                         e.code or 500)
        logger.exception("GPT API で想定外の例外: path=%s", request.path)
        return _json({"ok": False, "code": "INTERNAL",
                      "error": type(e).__name__}, 500)

    server.register_error_handler(Exception, _gpt_json_errors)

    # ---- 契約・死活 ----------------------------------------------------------
    def _health():
        """死活確認 + 公開 URL の自己申告 (ver52.6)。

        ★ 「OpenAPI の servers が http:// になっている」ことに気づけなかったのは、
          **仕様書が何を名乗っているか確認する手段が無かった**から。鍵不要の
          この窓口から見えるようにする (`https` が False なら Action は繋がらない)。

        ★ ver52.7: **認証が結線できているか**も答える。

          `key_decision` の 401 は「鍵が無い」と「鍵が違う」を区別せず
          `invalid **or** missing` の 1 文だけを返すため、利用者からは
          どちらか分からない。実際、ChatGPT の Action に古い値が入っていた
          事例で、リバースプロキシのログを掘るまで特定できなかった。

          この窓口は鍵不要なので、**認証を設定した Action なら header が届く**。
          そこを見れば 1 回の呼び出しで切り分けられる:

            key_header_received=false            → Action の認証が未設定
            true かつ authenticated=false        → 値が違う
            両方 true                            → 鍵は正しい

        ★ 出すのは「鍵が設定済みか」と**真偽値**だけ。**鍵そのものは出さない**
          (本モジュール冒頭の方針: 鍵はサーバ側のみに保持)。
          公開ドメインは秘密ではないので `public_base_url` は出してよい。

        ★ 新たなオラクルにはならない: 保護された窓口が 200/401 で返すのと
          同じ情報しか与えない。照合は `key_decision` と同じ定数時間比較。
        """
        from app.version import version_label
        base = _public_base_url()
        provided = request.headers.get("X-API-Key", "")
        configured = _cfg_key()
        return _ok({
            "app_version": version_label(),
            "gpt_api": "enabled" if configured else "disabled",
            "public_base_url": base,
            "openapi_url": f"{base}/api/gpt/openapi.json",
            "https": base.startswith("https://"),
            "key_header_received": bool(provided),
            "authenticated": bool(
                provided and configured
                and hmac.compare_digest(provided, configured)),
        })

    def _openapi():
        base = _public_base_url()
        if not base.startswith("https://"):
            logger.warning(
                "OpenAPI の servers が %s で https ではない。ChatGPT の Action は "
                "https を要求するため取り込めない。SHARE_BASE_URL を設定するか、"
                "リバースプロキシが X-Forwarded-Proto を送っているか確認すること。",
                base)
        return _json(build_openapi_spec(base))

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
            return _fail("project/sub not found", 404, "NOT_FOUND")
        requested = request.args.get("method")
        method, rds, err = _pick_method(r["rds_map"], requested)
        if err is not None:
            return _fail_api(err)
        mblock = _method_block(requested, method, r["rds_map"])
        cache_dir = _warm_cache_dir(rds)
        if not cache_dir:
            # ★ ver52.0: `warm` 1 個では状態を表せない。`getMarkers` は
            #   キャッシュが無くても取れる（DEG ファイルを直接読むため）。
            #   種別ごとの readiness を返して、GPT が「まだ何も取れない」と
            #   誤解しないようにする（監査 F-05 / F-06）。
            return _ok({"warm": False, "code": "CACHE_COLD", **mblock,
                        "ready": _readiness(r),
                        "message": ("クラスタ統計は抽出キャッシュが必要です"
                                    "（アプリで一度開くと生成されます）。"
                                    "マーカー(getMarkers)は今のままでも取得できます。")})
        recs, meta = _read_clusters(cache_dir)
        return _ok({"warm": True, **mblock, "ready": _readiness(r),
                    **shape_clusters(recs, meta)})

    # ---- マーカー（DEG。純ファイル読み） ------------------------------------
    def _markers(pid, sid):
        r = _resolve_sub(pid, sid)
        if not r:
            return _fail("project/sub not found", 404, "NOT_FOUND")
        if not r["result_dir"]:
            return _fail("結果フォルダが未設定です。", 404, "NO_RESULT")

        # ★ ver52.0: 入力を先に検証する。従来は
        #   top 省略/0 → 全件（応答上限に当たる）、`"1,3,7"` → 黙って 0 件、
        #   無効 method → そのまま通る、だった。
        requested = request.args.get("method")
        method, _rds, err = _pick_method(r["rds_map"], requested)
        if err is not None:
            return _fail_api(err)
        clusters, err = parse_clusters(request.args.get("cluster"))
        if err is not None:
            return _fail_api(err)
        top, err = parse_top(request.args.get("top"))
        if err is not None:
            return _fail_api(err)
        direction, err = parse_direction(request.args.get("direction"))
        if err is not None:
            return _fail_api(err)

        mblock = _method_block(requested, method, r["rds_map"])
        from app.utils.deg_utils import load_deg_results
        recs = load_deg_results(Path(r["result_dir"]), method)
        if recs is None:
            return _ok({**mblock, "markers": [], "code": "NO_MARKERS",
                        "message": "この手法の DEG 結果が見つかりません。"})

        available = {str(x.get("cluster", "")) for x in recs}
        shaped = shape_markers(recs, cluster=clusters, top=top,
                               direction=direction)
        payload = {
            **mblock,
            "cluster": clusters,
            "top": top,
            "direction": direction,
            # ★ 並び順を明示する。符号を見ないので「高発現の上位」ではない。
            #   これを書いておかないと GPT が「高発現する上位マーカー」と
            #   説明してしまう（監査 17.1）。
            "sort": MARKER_SORT_DESC,
            "markers": shaped,
        }
        notice = marker_outcome(shaped, clusters, available)
        if notice:
            # ★ ver52.1: 一部だけ存在しないクラスタも必ず伝える
            #   （従来は 999 について何も言わなかった）。
            payload.update(notice)
        # ★ ver52.3: `direction` 指定で読めない avg_log2FC の record が
        #   両方向から落ちていた。落とした件数を必ず伝える
        #   （黙って切り詰めた一覧を「上位マーカーの全部」として渡さない）。
        if direction in ("up", "down"):
            unreadable = count_unreadable_fc(recs)
            if unreadable:
                payload["dropped_unreadable_log2fc"] = unreadable
                payload["message"] = (
                    (payload.get("message", "") + " ").strip()
                    + f"avg_log2FC を数値化できない {unreadable} 件は "
                      "up/down のどちらにも分類できないため除外しました。").strip()
        payload, truncated = limit_response_size(payload, "markers")
        if truncated:
            payload["truncated"] = True
            payload["code"] = "RESPONSE_TRUNCATED"
            payload["message"] = (
                "応答が大きすぎるため件数を削りました。top を小さくするか、"
                "クラスタを絞ってください。")
        return _ok(payload)

    # ---- 化合物検索（ウォームのアノテーション） -----------------------------
    def _compounds(pid, sid):
        r = _resolve_sub(pid, sid)
        if not r:
            return _fail("project/sub not found", 404, "NOT_FOUND")
        requested = request.args.get("method")
        method, rds, err = _pick_method(r["rds_map"], requested)
        if err is not None:
            return _fail_api(err)
        mblock = _method_block(requested, method, r["rds_map"])
        cache_dir = _warm_cache_dir(rds)
        if not cache_dir:
            return _ok({"warm": False, "code": "CACHE_COLD", **mblock,
                        "compounds": [], "ready": _readiness(r),
                        "message": ("化合物検索は抽出キャッシュが必要です"
                                    "（アプリで一度開くと生成されます）。")})
        recs = _read_annotations(cache_dir)
        # ★ ver52.1: 従来は不正値を黙って既定へ差し替えていた。
        #   `mz` を None にすると **m/z 絞り込み自体が消えて**、
        #   「m/z 検索の結果」として全件が返っていた。
        mz, err = parse_mz(request.args.get("mz"))
        if err is not None:
            return _fail_api(err)
        tol, err = parse_tol(request.args.get("tol"))
        if err is not None:
            return _fail_api(err)
        limit, err = parse_limit(request.args.get("limit"))
        if err is not None:
            return _fail_api(err)
        result = filter_compounds(
            recs, query=request.args.get("query"), mz=mz, tol=tol,
            lipid_class=request.args.get("lipid_class"), limit=limit,
        )
        payload = {"warm": True, **mblock,
                   "n_total_annotated": len(recs), "compounds": result}
        payload, truncated = limit_response_size(payload, "compounds")
        if truncated:
            payload["truncated"] = True
            payload["code"] = "RESPONSE_TRUNCATED"
            payload["message"] = (
                "応答が大きすぎるため件数を削りました。limit を小さくするか、"
                "検索条件を絞ってください。")
        return _ok(payload)

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

    # ---- インタラクティブ Export オンデマンド生成（フェーズ2・非同期） --------
    def _export_interactive(pid, sid):
        # POST: 生成ジョブを起動し job_id と status_url を返す（重い＝R が走り得る）。
        r = _resolve_sub(pid, sid)
        if not r:
            return _fail("project/sub not found", 404)
        if not r["rds_map"]:
            return _fail("この結果には解析済み RDS が見つかりません。", 404)
        fmt, err = parse_export_format(request.args.get("format"))
        if err is not None:
            return _fail_api(err)
        methods, err = resolve_export_methods(
            request.args.get("methods"), r["rds_map"])
        if err is not None:
            return _fail_api(err)

        # ★ ver52.1: admission を **thread 生成前**に行う。従来は必ず thread を
        #   作ってから semaphore を最大 3600 秒待っており、待機中の job は
        #   `status:"running", pct:0` で実行中と区別が付かなかった。
        if not _GPT_EXPORT_SEM.acquire(blocking=False):
            return _json({
                "ok": False, "code": "BUSY",
                "error": ("生成中のエクスポートが上限に達しています"
                          f"（同時 {_GPT_EXPORT_MAX_CONCURRENCY} 件）。"
                          "時間をおいて再試行してください。"),
                "retry_after_sec": 60,
            }, 429)
        from app.services import export_progress as ep
        job_id = ep.new_job()
        threading.Thread(
            target=_run_interactive_export,
            args=(job_id, r, methods, fmt), daemon=True,
        ).start()
        return _ok({
            "job_id": job_id,
            "status_url": f"/api/gpt/exports/jobs/{job_id}",
            "message": ("エクスポート生成を開始しました。status_url を数秒おきに"
                        "ポーリングしてください。status=done で download_url が"
                        "返りますが、**この URL は Action からは取得できません**"
                        "（ブラウザ / アプリで開いてください）。"),
        })

    def _export_job_status(job_id):
        """GET: 非同期ジョブの状態（export / warmup 共通）。

        ★ ver53.0: 従来は「**ファイルが出来ていたら done**」だけで判定していた。
          成果物ファイルを作らない `warmup` を同じ窓口に載せると
          **永遠に running を返す**ので、ジョブ記録の `kind` で分ける。

        ★ ジョブ記録が無くてもファイルがあれば done を返す挙動は残す
          （ジョブは 32 件で打ち切られるので、記録だけ先に消えることがある）。
          `job is None` のときは種別が分からないので "export" とみなす
          ＝従来と同じ経路になる。
        """
        if not valid_job_id(job_id):
            return _fail("bad job id", 404)
        from app.services import export_progress as ep
        job = ep.get_job(job_id)
        kind = (job or {}).get("kind", "export")

        if kind == "export":
            f = _find_export_job_file(job_id)
            if f is not None:
                fname = (job or {}).get("filename") or f.name.split("__", 1)[-1]
                return _ok({
                    "status": "done", "pct": 100, "kind": kind, "filename": fname,
                    "download_url": f"/api/gpt/exports/jobs/{job_id}/file",
                    "message": (job or {}).get("msg", "完了"),
                })
        if job is None:
            return _fail("unknown or expired job", 404)
        if job.get("status") == "error":
            return _ok({"status": "error", "kind": kind,
                        "message": job.get("msg", "失敗")})
        if job.get("status") == "done":
            # ★ export でここに来るのは「完了したが一時ファイルが掃除された」
            #   場合（sweep_old_files が 1 時間で消す）。従来はこの経路が
            #   最後の return に落ちて **pct 100 のまま running** を返していた。
            out = {"status": "done", "pct": 100, "kind": kind,
                   "message": job.get("msg", "完了")}
            if kind == "export":
                out["message"] = (
                    "生成は完了しましたが一時ファイルの保持期限が切れています。"
                    "もう一度生成してください。")
                out["expired"] = True
            return _ok(out)
        return _ok({"status": "running", "kind": kind,
                    "pct": job.get("pct", 0), "label": job.get("label", "")})

    def _export_job_file(job_id):
        # GET: 生成済みファイルを send_file でストリーム配信。
        if not valid_job_id(job_id):
            abort(404)
        f = _find_export_job_file(job_id)
        if f is None or not f.exists():
            abort(404)
        from app.services import export_progress as ep
        job = ep.get_job(job_id)
        dl_name = (job or {}).get("filename") or f.name.split("__", 1)[-1]
        return send_file(str(f), as_attachment=True, download_name=dl_name)

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
        ("/api/gpt/exports/jobs/<job_id>", "gpt.export_job_status", _export_job_status),
        ("/api/gpt/exports/jobs/<job_id>/file", "gpt.export_job_file", _export_job_file),
    ]
    for rule, endpoint, view in routes:
        server.add_url_rule(rule, endpoint=endpoint, view_func=view, methods=["GET"])

    # インタラクティブ Export の生成開始のみ POST（副作用＝ジョブ起動があるため）。
    server.add_url_rule(
        "/api/gpt/projects/<pid>/sub/<sid>/exports/interactive",
        endpoint="gpt.export_interactive", view_func=_export_interactive,
        methods=["POST"],
    )

    logger.info("GPT API registered (/api/gpt/*); key=%s",
                "set" if _cfg_key() else "UNSET(closed)")
