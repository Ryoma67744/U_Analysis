# =============================================================================
# MSI Analysis Application - 解析条件の記録（論文 Methods 用）
# =============================================================================
# 目的は 1 つ。「論文を書くときに条件が抜け落ちない」こと。
#
# 既存の receipt.py はバッチ解析（設定タブ→実行）の条件を結果フォルダに残すが、
# 論文の図が実際に出てくる Interactive タブの設定（Volcano/Heatmap/Feature/
# on-the-fly DE/H&E エクスポート等）はどこにも残っていなかった。本モジュールは
#
#   receipt.json（無ければ analysis_params.json）
#   + interactive_settings.json（各パネルの collector が書く）
#   + selection_groups / feature_lists / H&E の要約
#
# を 1 つの conditions dict にまとめ、エクスポートのたびに
# <result-dir>/provenance/export_<ts>_<kind>.json として必ず書き出す。
# ダウンロード形式に依存しないので、生 CSV でもクライアント側 PNG でも記録は残る。
#
# 重要な原則: **欠損値を捏造しない**。取得できなかった項目は None のままにし、
# `_missing` に列挙する。Methods 生成側はそれを「未記録」と明示して、
# 人が手で埋めるべき箇所が分かるようにする。
#
# 依存は標準ライブラリ + app.utils/app.services のみ（Dash 非依存・単体テスト可）。
# =============================================================================

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from app.services import runtime_script as _rs
from app.utils.file_locks import atomic_write_json

logger = logging.getLogger("msi.provenance")

CONDITIONS_VERSION = "1"
PROVENANCE_DIRNAME = "provenance"
CONDITIONS_JSON = "analysis_conditions.json"

# on-the-fly DE は GUI に出ていない固定値で走る。
# seurat_bridge.SeuratBridge.run_differential_expression のシグネチャ既定値と
# 一致させること（interactive_de.py は上書きしていない）。
ONTHEFLY_DE_FIXED_PARAMS = {
    "test": "wilcox",
    "min_pct": 0.05,
    "logfc_threshold": 0.25,
    "p_adjust_method": "BH",
}

# バッチ解析側の DEG も GUI に出ていない固定条件で走っている。
# R テンプレ (260623_DBSCAN_With_cluster_ver6_no-png_slim.R:1860, :1867):
#   FindAllMarkers(only.pos=FALSE, min.pct=DEG_MIN_PCT_VAL, logfc.threshold=DEG_LOGFC_TH_VAL,
#                  test.use="wilcox")
#   deg$p_val_adj <- p.adjust(deg$p_val, method="BH")   ← Seurat 既定の Bonferroni を置換
# min_pct は実行スクリプトから復元できれば上書きされる（DEG_MIN_PCT_VAL）。
BATCH_DE_FIXED_PARAMS = {
    "test": "wilcox",
    "min_pct": 0.05,
    "p_adjust_method": "BH",
    "only_positive": False,
}

# conditions に必ず入っていてほしいキー（欠けていれば _missing に載せる）。
# ドット区切りでネストを辿る。
_REQUIRED_PATHS = (
    "analysis.umap.n_neighbors",
    "analysis.umap.min_dist",
    "analysis.umap.metric",
    "analysis.umap.dims",
    "analysis.umap.seed",
    "analysis.clustering.algorithm",
    "analysis.clustering.resolution",
    "analysis.preprocessing.norm_mode",
    "analysis.preprocessing.input_normalized",
    "analysis.preprocessing.batch_correction",
    "analysis.thresholds.p",
    "analysis.thresholds.logfc",
    "analysis.annotation.ion_mode",
    "analysis.annotation.tolerance_mz",
    "software.r_version",
    "software.app_version",
    "pipeline.template_path",
)

# 必須ではないが実行スクリプトから復元しうる項目。出典の印を付ける対象に含める。
_RECOVERABLE_EXTRA_PATHS = (
    "analysis.clustering.k_param",
    "analysis.mz_align_ppm",
    "analysis.filter_mode",
    "analysis.target_clusters",
    "analysis.sample_selection.sample_names",
    "analysis.sample_selection.roi_filter",
    "analysis.sample_selection.use_roi_as_sample",
    "analysis.sample_selection.tims_scenario",
    "analysis.annotation.adduct_filter",
    "analysis.annotation.annotation_csv",
    "analysis.preprocessing.calibration_enable",
    "analysis.preprocessing.calibration_regression_mode",
    "analysis.analysis_type",
)


# ---------------------------------------------------------------------------
# パス解決
# ---------------------------------------------------------------------------

def results_dir_for_rds(rds_path, explicit_result_folder=None) -> Optional[Path]:
    """RDS パスから結果フォルダ（receipt.json のある場所）を返す。

    これまで interactive_callbacks / interactive_calibration / interactive_pptx に
    同じ導出が 4 箇所コピペされていたのを 1 本化したもの。

    - explicit_result_folder（UI の「結果フォルダ」入力）があればそれを優先する。
    - RDS が RDS_Files/ 配下ならその親、そうでなければ RDS のあるディレクトリ。
    - **SEURAT_CACHE_DIR 配下（"PCA (uncorrected)" の derived_pca 等）は
      結果フォルダではないので None を返す。** 呼び出し側は no-op にすること。
    """
    if explicit_result_folder:
        p = Path(explicit_result_folder)
        if p.is_dir():
            return p
    if not rds_path:
        return None
    try:
        rds_dir = Path(rds_path).parent
    except (TypeError, ValueError):
        return None

    # キャッシュ配下は結果フォルダではない
    try:
        from app.config import SEURAT_CACHE_DIR
        cache_root = Path(SEURAT_CACHE_DIR).resolve()
        if cache_root in rds_dir.resolve().parents or rds_dir.resolve() == cache_root:
            return None
    except Exception:  # config 未読込などでも本体は壊さない
        logger.debug("SEURAT_CACHE_DIR 判定をスキップしました", exc_info=True)

    return rds_dir.parent if rds_dir.name == "RDS_Files" else rds_dir


def provenance_dir(result_dir) -> Optional[Path]:
    """<result-dir>/provenance を返す（result_dir が None なら None）。"""
    if not result_dir:
        return None
    return Path(result_dir) / PROVENANCE_DIRNAME


# ---------------------------------------------------------------------------
# 読み取りヘルパー（どれも失敗しても解析/出力本体を壊さない）
# ---------------------------------------------------------------------------

def _read_json(path) -> dict:
    try:
        p = Path(path)
        if p.is_file():
            return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        logger.debug("JSON 読込に失敗: %s (%s)", path, e)
    return {}


def _dig(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _current_operator() -> Optional[str]:
    """Flask session の解析者名（request context 外では None）。"""
    try:
        from flask import session
        return session.get("analyst_name") or None
    except Exception:
        return None


def latest_runtime_script(result_dir) -> Optional[Path]:
    """<result-dir>/log/v8_runtime_*.R のうち最新のものを返す。

    UI の値がすべて定数として焼き込まれた「実際に走ったスクリプト」で、
    サンプル絞り込みや ROI フィルタなど analysis_params.json に無い条件も
    ここには残っている。Methods の裏付けとして最も強い。
    """
    if not result_dir:
        return None
    log_dir = Path(result_dir) / "log"
    if not log_dir.is_dir():
        return None
    cands = sorted(log_dir.glob("v8_runtime_*.R"))
    return cands[-1] if cands else None


# ---------------------------------------------------------------------------
# 収集
# ---------------------------------------------------------------------------

def _merge_prefer_first(primary: Optional[dict], fallback: dict) -> dict:
    """primary の値を優先しつつ、空いているキーだけ fallback で埋める。"""
    out = dict(fallback or {})
    for k, v in (primary or {}).items():
        if v not in (None, "", [], {}):
            out[k] = v
    return out


def _analysis_block(receipt: dict, params: dict) -> dict:
    """receipt.json（優先）と analysis_params.json からバッチ解析側の条件を組む。"""
    obj = receipt.get("object") or {}
    instr = receipt.get("instrument") or {}

    return {
        "analysis_type": obj.get("analysis_type") or params.get("analysis_type"),
        "data_folder": obj.get("data_folder") or params.get("data_folder"),
        "output_dir": (receipt.get("result") or {}).get("output_dir")
                      or params.get("output_dir"),
        "started_at": receipt.get("startTime") or params.get("timestamp"),
        "ended_at": receipt.get("endTime") or params.get("execution_end_time"),
        "operator": (receipt.get("agent") or {}).get("operator") or params.get("operator"),
        "preprocessing": obj.get("preprocessing") or {
            "input_normalized": params.get("input_normalized"),
            "norm_mode": params.get("norm_mode"),
            "batch_correction": params.get("batch_correction"),
            "calibration_enable": params.get("calibration_enable"),
            "calibration_regression_mode": params.get("calibration_regression_mode"),
        },
        "umap": obj.get("umap") or {
            "n_neighbors": params.get("umap_n_neighbors"),
            "min_dist": params.get("umap_min_dist"),
            "metric": params.get("umap_metric"),
            "dims": params.get("umap_dims_n"),
            "seed": params.get("umap_seed"),
        },
        # ver48.0: receipt.json が無い（＝R サイドカーが出る前の古い解析）ときに
        # クラスタリングが丸ごと空になっていたので params 側のフォールバックを足した。
        "clustering": obj.get("clustering") or {
            "algorithm": params.get("clustering_algorithm"),
            "resolution": params.get("clustering_resolution"),
            "k_param": params.get("clustering_k"),
        },
        "annotation": obj.get("annotation") or {
            "ion_mode": params.get("ion_mode"),
            "tolerance_mz": params.get("tolerance_mz"),
            "adduct_filter": params.get("adduct_filter"),
            "annotation_csv": params.get("annotation_csv"),
            "sources": params.get("annotation_sources"),
        },
        "thresholds": obj.get("thresholds") or {
            "p": params.get("p_thresh"),
            "logfc": params.get("logfc_thresh"),
        },
        # ver48.0: receipt.py:190 が object.sample_selection を書いているのに
        # params しか見ておらず、レシート側の記録を捨てていた。両方を統合する
        # （レシート優先。tims_scenario はレシートでは pipeline 側にある）。
        "sample_selection": _merge_prefer_first(
            obj.get("sample_selection"),
            {
                "sample_names": params.get("sample_names"),
                "roi_filter": params.get("roi_filter"),
                "annotation_filter": params.get("annotation_filter"),
                "use_roi_as_sample": params.get("use_roi_as_sample"),
                "tims_scenario": (_dig(receipt, "object.pipeline.tims_scenario")
                                  or params.get("tims_scenario")),
            },
        ),
        "filter_mode": obj.get("filter_mode") or params.get("filter_mode"),
        "target_clusters": obj.get("target_clusters") or params.get("target_clusters"),
        "mz_align_ppm": params.get("mz_align_ppm"),
        "_instrument": instr,
    }


def collect_conditions(rds_path=None, result_folder=None, integration_method=None,
                       extra=None, app_version=None) -> dict:
    """現時点の解析条件を 1 つの dict にまとめる。

    Args:
        rds_path: Interactive タブが読んでいる RDS のパス
        result_folder: UI の「結果フォルダ」（あれば優先）
        integration_method: Harmony / RPCA / PCA など、いま表示している統合手法
        extra: エクスポート callback がクリック時点で握っている値（並び替え・
            絞り込みなど、ディスクに永続化されていないもの）
        app_version: 省略時は version_label() を使う

    Returns:
        conditions dict。取得できなかった必須項目は `_missing` に列挙される。
    """
    result_dir = results_dir_for_rds(rds_path, result_folder)
    receipt = _read_json(Path(result_dir) / "receipt.json") if result_dir else {}
    params = _read_json(Path(result_dir) / "analysis_params.json") if result_dir else {}

    analysis = _analysis_block(receipt, params)
    instr = analysis.pop("_instrument", {}) or {}

    if app_version is None:
        try:
            from app.version import version_label
            app_version = version_label()
        except Exception:
            app_version = None

    runtime_script = latest_runtime_script(result_dir)

    interactive = {}
    try:
        from app.utils.label_persistence import load_interactive_settings
        interactive = load_interactive_settings(rds_path) or {}
    except Exception as e:
        logger.debug("interactive_settings の読込に失敗: %s", e)

    conditions = {
        "conditions_version": CONDITIONS_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "generated_by": _current_operator(),
        "integration_method": integration_method,
        "rds_path": str(rds_path) if rds_path else None,
        "result_dir": str(result_dir) if result_dir else None,
        "analysis": analysis,
        "software": {
            "app_version": app_version or instr.get("app_version"),
            "r_version": instr.get("r_version"),
            "packages": instr.get("packages") or {},
        },
        "pipeline": {
            "template_path": params.get("template_path")
                             or _dig(receipt, "object.pipeline.template_path"),
            "template_sha256": params.get("template_sha256")
                               or _dig(receipt, "object.pipeline.template_sha256"),
            "runtime_script": str(runtime_script) if runtime_script else None,
            "runtime_script_sha256": _sha256(runtime_script) if runtime_script else None,
            "pipeline_stage": params.get("pipeline_stage"),
        },
        "interactive": _interactive_block(interactive, rds_path),
        "onthefly_de_fixed_params": dict(ONTHEFLY_DE_FIXED_PARAMS),
        "batch_de_fixed_params": dict(BATCH_DE_FIXED_PARAMS),
        "extra": dict(extra or {}),
    }

    # ver48.0: 復元より前に「直接記録されていた」項目に印を付ける。
    # 以後に埋まったものは実行スクリプト由来と判別でき、Methods 本文で色を分けられる。
    _rs.mark_recorded_sources(conditions, _REQUIRED_PATHS + _RECOVERABLE_EXTRA_PATHS)

    # ver48.0: 古い結果フォルダは analysis_params.json の新キーも R サイドカーも
    # 無く大半が未記録になる。実行スクリプト（UI 値が定数として焼き込まれている）
    # から空欄だけを埋める。既存の記録は上書きしない。
    try:
        _rs.recover_conditions(conditions, runtime_script, integration_method)
    except Exception as e:  # noqa: BLE001 - 復元失敗で収集全体を壊さない
        logger.warning("実行スクリプトからの復元に失敗: %s", e)

    # derived_pca（キャッシュ上の埋め込み）は結果フォルダを持たない。黙って
    # 「条件不明」にならないよう、警告として明示する。
    # ver48.0: 英語の Methods に日本語が出ないよう、コード＋パラメータで持つ。
    warnings = []
    if rds_path and result_dir is None:
        warnings.append({"code": "cache_only_embedding", "params": {}})
    if (integration_method and str(integration_method).upper().startswith("PCA")
            and result_dir is None):
        # 名前が PCA というだけでは派生生成とは限らない（結果フォルダに
        # 永続化された PCA の RDS があることもある）。結果フォルダを持たない
        # ときだけ「キャッシュ上の派生埋め込み」と断定する。
        warnings.append({"code": "derived_pca_not_persisted", "params": {}})
    conditions["warnings"] = warnings
    conditions["_missing"] = _missing_paths(conditions)
    return conditions


def _interactive_block(settings: dict, rds_path) -> dict:
    """interactive_settings.json ＋ 各サイドカーの要約。"""
    block = {k: v for k, v in (settings or {}).items() if not k.startswith("_")}

    # 選択グループ / 特徴量リストは件数と名前だけ（cell_ids 全体は巨大なので入れない）
    try:
        from app.services import selection_groups as sg
        groups = (sg.load_groups(rds_path) or {}).get("groups", [])
        block["selection_groups"] = [
            {"name": g.get("name"), "n_cells": len(g.get("cell_ids") or [])}
            for g in groups
        ]
    except Exception as e:
        logger.debug("selection_groups の読込に失敗: %s", e)

    try:
        from app.services import feature_lists as fl
        lists = (fl.load_lists(rds_path) or {}).get("lists", [])
        block["feature_lists"] = [
            {"name": ls.get("name"), "n_features": len(ls.get("features") or [])}
            for ls in lists
        ]
    except Exception as e:
        logger.debug("feature_lists の読込に失敗: %s", e)

    return block


def _sha256(path) -> Optional[str]:
    try:
        from app.services.receipt import sha256_file
        return sha256_file(path)
    except Exception:
        return None


def _missing_paths(conditions: dict) -> list:
    """必須項目のうち値が取れていないものを列挙する（捏造しないための土台）。"""
    missing = []
    for dotted in _REQUIRED_PATHS:
        if _dig(conditions, dotted) in (None, "", [], {}):
            missing.append(dotted)
    return missing


# ---------------------------------------------------------------------------
# 書き出し
# ---------------------------------------------------------------------------

def write_export_record(result_dir, kind: str, conditions: dict) -> Optional[Path]:
    """エクスポート 1 回分の条件を <result-dir>/provenance/ に記録する。

    ダウンロードの形式に依存しないため、生 CSV やクライアント側 PNG のように
    manifest を同梱できない経路でもこれだけは残る。**「抜け落ちない」の本体。**

    result_dir が None（derived_pca 等）のときは何もせず None を返す。
    失敗しても例外は投げない（エクスポート本体を壊さないため）。
    """
    pdir = provenance_dir(result_dir)
    if pdir is None:
        logger.info("結果フォルダが特定できないため条件記録をスキップ: kind=%s", kind)
        return None
    try:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_kind = "".join(c if (c.isalnum() or c in "-_") else "_" for c in str(kind))
        path = pdir / f"export_{ts}_{safe_kind}.json"
        payload = dict(conditions or {})
        payload["export_kind"] = kind
        payload["exported_at"] = datetime.now().isoformat(timespec="seconds")
        atomic_write_json(payload, path)
        return path
    except Exception as e:
        logger.warning("条件記録の書き出しに失敗 (kind=%s): %s", kind, e)
        return None


def record_export(kind: str, rds_path=None, result_folder=None,
                  integration_method=None, extra=None) -> Optional[Path]:
    """収集 → 記録をまとめて行う入口（エクスポート callback から 1 行で呼ぶ）。

    生 CSV のように manifest を同梱できない出力でも、これだけは残る。
    例外は投げない（エクスポート本体を止めないため）。
    """
    try:
        conditions = collect_conditions(rds_path=rds_path,
                                        result_folder=result_folder,
                                        integration_method=integration_method,
                                        extra=extra)
        return write_export_record(results_dir_for_rds(rds_path, result_folder),
                                   kind, conditions)
    except Exception as e:
        logger.warning("エクスポート条件の記録に失敗 (kind=%s): %s", kind, e)
        return None


def record_csv_export(filename: str, rds_path=None, result_folder=None,
                      integration_method=None, extra=None) -> Optional[Path]:
    """CSV エクスポート 1 回分の条件を残す。

    表のエクスポートは derived_virtual_data（画面上の並び替え・絞り込み後）を
    書き出すため、sort_by / filter_query を残さないと同じ表を再現できない。
    呼び出し側は extra にそれらを入れること。

    CSV 本体の列は変更しない（Supplementary Table としてそのまま使えるように）。
    """
    payload = {"exported_file": filename}
    payload.update(extra or {})
    stem = Path(filename).stem
    return record_export(f"csv_{stem}", rds_path=rds_path,
                         result_folder=result_folder,
                         integration_method=integration_method, extra=payload)


def conditions_json_bytes(conditions: dict) -> bytes:
    """ZIP へ同梱する analysis_conditions.json のバイト列。"""
    return json.dumps(conditions or {}, indent=2,
                      ensure_ascii=False, default=str).encode("utf-8")


def write_conditions_bundle(result_dir, conditions: dict) -> dict:
    """まとめ出力: provenance/ に conditions + 日英 Methods を書き、パスを返す。

    Returns: {"conditions": Path|None, "methods_ja": Path|None, "methods_en": Path|None}
    """
    out = {"conditions": None, "methods_ja": None, "methods_en": None,
           "prose_ja_md": None, "prose_ja_html": None,
           "prose_en_md": None, "prose_en_html": None}
    pdir = provenance_dir(result_dir)
    if pdir is None:
        return out
    from app.services.methods_text import (render_methods, render_methods_prose,
                                           render_methods_prose_html)
    from app.services.receipt import _atomic_write
    try:
        cpath = pdir / CONDITIONS_JSON
        atomic_write_json(conditions, cpath)
        out["conditions"] = cpath
    except Exception as e:
        logger.warning("analysis_conditions.json の書き出しに失敗: %s", e)
    for lang, key in (("ja", "methods_ja"), ("en", "methods_en")):
        try:
            mpath = pdir / f"METHODS_{lang}.md"
            _atomic_write(mpath, render_methods(conditions, lang=lang))
            out[key] = mpath
        except Exception as e:
            logger.warning("METHODS_%s.md の書き出しに失敗: %s", lang, e)
        # 論文用の平文。HTML 版は未記録（赤）と復元値（青）の色が残る。
        for suffix, render in ((".md", render_methods_prose),
                               (".html", render_methods_prose_html)):
            try:
                ppath = pdir / f"METHODS_prose_{lang}{suffix}"
                _atomic_write(ppath, render(conditions, lang=lang))
                out[f"prose_{lang}{suffix.replace('.', '_')}"] = ppath
            except Exception as e:
                logger.warning("METHODS_prose_%s%s の書き出しに失敗: %s",
                               lang, suffix, e)
    return out
