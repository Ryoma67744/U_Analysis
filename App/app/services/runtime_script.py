# =============================================================================
# MSI Analysis Application - 実行スクリプト (log/v8_runtime_*.R) からの条件復元
# =============================================================================
# ver47.0 より前に回した結果フォルダには analysis_params.json の新キーも R サイド
# カーも無いため、UMAP パラメータ・正規化モード・クラスタリング設定が丸ごと
# 「未記録」になる。しかし <結果フォルダ>/log/v8_runtime_<日時>.R は
# 「UI の値を定数として焼き込んだ、実際に実行されたスクリプトそのもの」なので、
# ここから読み戻せば大半が復元できる。
#
# **これは推測ではない**。ただし出典が receipt.json より一段間接なので、
# 復元した値は `_sources[path] = "runtime_script"` と印を付け、Methods 本文では
# 別色（要確認）で表示する。既に記録がある値は決して上書きしない。
#
# 安全側に倒すための制約:
#   - whitelist に載せた定数名しか読まない。
#   - 単純リテラル（数値 / 30L / "文字列" / TRUE/FALSE / c(...) の列挙）だけ解釈し、
#     式や変数参照は「読めなかった」として捨てる（誤った値を書くくらいなら未記録のまま）。
#
# 依存は標準ライブラリのみ。
# =============================================================================

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("msi.runtime_script")

# R 定数名 → conditions のドットパス。
# 同じパスに複数候補がある場合は「先に載っている方を優先」する。
RUNTIME_VAR_MAP: list[tuple[str, str]] = [
    ("UMAP_N_NEIGHBORS", "analysis.umap.n_neighbors"),
    ("UMAP_MIN_DIST", "analysis.umap.min_dist"),
    ("UMAP_METRIC", "analysis.umap.metric"),
    ("UMAP_DIMS_N", "analysis.umap.dims"),
    ("GLOBAL_RANDOM_SEED", "analysis.umap.seed"),
    ("UMAP_SEED", "analysis.umap.seed"),
    ("RANDOM_SEED", "analysis.umap.seed"),
    ("CLUSTER_K_PARAM", "analysis.clustering.k_param"),
    # 近傍探索の距離尺度。UMAP の距離尺度 (cosine) とは別物なので混同しないこと。
    ("CLUSTER_METRIC", "analysis.clustering.neighbor_metric"),
    # バッチ DEG の検定前フィルタ（GUI に出ていない）
    ("DEG_MIN_PCT_VAL", "analysis.de.min_pct"),
    # ★ ver58.0 (A-4): 空間平滑化の有無。従来はどこにも記録されておらず、
    #   中間ファイル名が *_smoothed.rds なので「平滑化した」と読めてしまった。
    #   DESI は SPATIAL_SMOOTH、TIMS は SPATIAL_SMOOTH_ENABLE と名前が違う。
    ("SPATIAL_SMOOTH", "analysis.preprocessing.spatial_smoothing"),
    ("SPATIAL_SMOOTH_ENABLE", "analysis.preprocessing.spatial_smoothing"),
    ("NORM_MODE", "analysis.preprocessing.norm_mode"),
    ("INPUT_NORMALIZED", "analysis.preprocessing.input_normalized"),
    ("BATCH_VAR", "analysis.preprocessing.batch_correction"),
    ("ANALYSIS_METHOD", "analysis.preprocessing.batch_correction"),
    ("ION_MODE", "analysis.annotation.ion_mode"),
    ("DEFAULT_TOLERANCE_MZ", "analysis.annotation.tolerance_mz"),
    ("MZ_ALIGN_PPM", "analysis.mz_align_ppm"),
    ("DEG_P_THRESH_VAL", "analysis.thresholds.p"),
    ("DEG_LOGFC_TH_VAL", "analysis.thresholds.logfc"),
    ("sample_names", "analysis.sample_selection.sample_names"),
    ("ROI_FILTER", "analysis.sample_selection.roi_filter"),
    ("USE_ROI_AS_SAMPLE", "analysis.sample_selection.use_roi_as_sample"),
    ("FILTER_MODE", "analysis.filter_mode"),
    ("TARGET_CLUSTERS", "analysis.target_clusters"),
    ("PIPELINE_STAGE", "pipeline.pipeline_stage"),
]

# クラスタリング解像度は手法ごとに別定数のテンプレがある
# （DESI v16: _SINGLE=0.5 / _HARMONY=0.5 / _RPCA=0.8、TIMS ver6: 単一の CLUSTER_RESOLUTION）。
# 統合手法に対応する定数があればそれを優先しないと、DESI+RPCA を 0.5 と誤記する。
_RESOLUTION_BY_METHOD = {
    "HARMONY": "CLUSTER_RESOLUTION_HARMONY",
    "RPCA": "CLUSTER_RESOLUTION_RPCA",
    "PCA": "CLUSTER_RESOLUTION_SINGLE",
}
_RESOLUTION_FALLBACK = "CLUSTER_RESOLUTION"

# CLUSTER_ALGORITHM の数値 → 名称。rds_io.R:245-251 と一致させること。
_CLUSTER_ALGORITHM_NAMES = {
    1: "louvain",
    2: "louvain_multilevel",
    3: "slm",
    4: "leiden",
}

# 読み取り対象の定数名（上記マップ＋解像度＋アルゴリズム＋DBSCAN 判定用）
_WHITELIST = (
    {name for name, _ in RUNTIME_VAR_MAP}
    | set(_RESOLUTION_BY_METHOD.values())
    | {_RESOLUTION_FALLBACK, "CLUSTER_ALGORITHM", "DBSCAN_EPS", "DBSCAN_MINPTS"}
)

_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*<-\s*(.*)$")


# ---------------------------------------------------------------------------
# 字句
# ---------------------------------------------------------------------------

def _strip_comment(line: str) -> str:
    """引用符の外にある # 以降を落とす（"a#b" のような文字列は壊さない）。"""
    out = []
    quote = None
    prev = ""
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote and prev != "\\":
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
        prev = ch
    return "".join(out).rstrip()


def _split_top_level(body: str) -> list:
    """c(...) の中身をトップレベルのカンマで分割する。"""
    parts, buf, depth, quote, prev = [], [], 0, None, ""
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote and prev != "\\":
                quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "(":
            depth += 1
            buf.append(ch)
        elif ch == ")":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
        prev = ch
    if "".join(buf).strip():
        parts.append("".join(buf).strip())
    return parts


def _parse_scalar(tok: str) -> Any:
    """単純リテラルだけ解釈する。解釈できなければ _UNPARSED を返す。"""
    tok = tok.strip()
    if not tok:
        return _UNPARSED
    if tok in ("TRUE", "T"):
        return True
    if tok in ("FALSE", "F"):
        return False
    if tok in ("NULL", "NA", "NA_character_", "NA_integer_", "NA_real_"):
        return None
    if len(tok) >= 2 and tok[0] == tok[-1] and tok[0] in ("'", '"'):
        return tok[1:-1]
    m = re.fullmatch(r"[+-]?\d+L", tok)
    if m:
        return int(tok[:-1])
    try:
        if re.fullmatch(r"[+-]?\d+", tok):
            return int(tok)
        if re.fullmatch(r"[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?", tok):
            return float(tok)
    except ValueError:
        pass
    return _UNPARSED


class _Unparsed:
    """「読めなかった」を None（＝R の NULL）と区別するための番兵。"""

    def __repr__(self):  # pragma: no cover - デバッグ用
        return "<UNPARSED>"


_UNPARSED = _Unparsed()


def _parse_value(rhs: str) -> Any:
    """右辺を解釈する。c(...) はリストに、それ以外はスカラーに。"""
    rhs = rhs.strip()
    if rhs.startswith("c(") and rhs.endswith(")"):
        items = [_parse_scalar(t) for t in _split_top_level(rhs[2:-1])]
        if any(it is _UNPARSED for it in items):
            return _UNPARSED
        return items
    return _parse_scalar(rhs)


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------

def parse_runtime_script(path, whitelist=None) -> dict:
    """実行スクリプトから whitelist の定数だけを読み取って dict で返す。

    複数行にまたがる c(...)（sample_names など）に対応する。
    同じ定数が複数回代入されていれば最後の代入を採用する（後勝ち＝実際の値）。
    失敗しても例外は投げず、読めた分だけ返す。
    """
    names = set(whitelist) if whitelist is not None else _WHITELIST
    out: dict = {}
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("実行スクリプトを読めません: %s (%s)", path, e)
        return out

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = _strip_comment(lines[i])
        m = _ASSIGN_RE.match(line)
        if not m or m.group(1) not in names:
            i += 1
            continue
        name, rhs = m.group(1), m.group(2).strip()
        # 括弧が閉じるまで次行を連結する（複数行の c(...) 対応）
        guard = 0
        while rhs.count("(") > rhs.count(")") and i + 1 < len(lines) and guard < 500:
            i += 1
            guard += 1
            rhs += " " + _strip_comment(lines[i]).strip()
        value = _parse_value(rhs)
        if value is not _UNPARSED:
            out[name] = value
        i += 1
    return out


# ---------------------------------------------------------------------------
# conditions への反映
# ---------------------------------------------------------------------------

def _dig(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _set_by_path(d: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


def _is_empty(value) -> bool:
    """未記録とみなす値。False / 0 は「記録されている」ので空扱いしない。"""
    return value is None or value == "" or value == [] or value == {}


def recover_conditions(conditions: dict, script_path=None,
                       integration_method: Optional[str] = None) -> dict:
    """実行スクリプトの値で conditions の空欄だけを埋め、出典を記録する。

    conditions は破壊的に更新して返す。
    `conditions["_sources"][<path>] = "runtime_script"` が復元の印。

    既に値がある項目は**絶対に上書きしない**（receipt.json のほうが直接の記録なので）。
    """
    if script_path is None:
        script_path = _dig(conditions, "pipeline.runtime_script")
    sources = conditions.setdefault("_sources", {})
    if not script_path or not Path(str(script_path)).is_file():
        return conditions

    try:
        parsed = parse_runtime_script(script_path)
    except Exception as e:  # noqa: BLE001 - 復元失敗で本体を壊さない
        logger.warning("実行スクリプトの解析に失敗: %s (%s)", script_path, e)
        return conditions
    if not parsed:
        return conditions

    def _fill(path: str, value) -> None:
        if _is_empty(value):
            return
        if not _is_empty(_dig(conditions, path)):
            return  # 直接の記録が優先
        _set_by_path(conditions, path, value)
        sources[path] = "runtime_script"

    for name, path in RUNTIME_VAR_MAP:
        if name in parsed:
            _fill(path, parsed[name])

    # --- クラスタリング解像度: 統合手法に対応する定数を優先 ---
    method_key = str(integration_method or conditions.get("integration_method") or "").upper()
    res_names = []
    for prefix, var in _RESOLUTION_BY_METHOD.items():
        if method_key.startswith(prefix):
            res_names.append(var)
            break
    res_names.append(_RESOLUTION_FALLBACK)
    for var in res_names:
        if var in parsed:
            _fill("analysis.clustering.resolution", parsed[var])
            break

    # --- クラスタリング手法 ---
    alg = parsed.get("CLUSTER_ALGORITHM")
    if isinstance(alg, bool):
        alg = None  # TRUE/FALSE は手法番号ではない
    if isinstance(alg, int):
        _fill("analysis.clustering.algorithm",
              _CLUSTER_ALGORITHM_NAMES.get(alg, f"algorithm_{alg}"))
    elif "DBSCAN_EPS" in parsed or "DBSCAN_MINPTS" in parsed:
        _fill("analysis.clustering.algorithm", "dbscan")
    if "DBSCAN_EPS" in parsed:
        _fill("analysis.clustering.dbscan_eps", parsed["DBSCAN_EPS"])
    if "DBSCAN_MINPTS" in parsed:
        _fill("analysis.clustering.dbscan_min_pts", parsed["DBSCAN_MINPTS"])

    return conditions


def mark_recorded_sources(conditions: dict, paths) -> dict:
    """復元前に値が入っていたパスを "recorded" として記録する。

    色分け（黒＝記録済み / 青＝復元 / 赤＝未記録）の判定に使う。
    どのファイル由来か（receipt.json か analysis_params.json か）は区別しない。
    """
    sources = conditions.setdefault("_sources", {})
    for path in paths:
        if not _is_empty(_dig(conditions, path)):
            sources.setdefault(path, "recorded")
    return conditions
