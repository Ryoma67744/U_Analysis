"""molinfo_attach.py - 登録済みサブプロジェクトへ「分子情報（化合物名）」を後から付与する。

分子情報は通常 SCiLS→Parquet 変換時に peak-list CSV から付与される（本体 parquet の列名に埋め込み
＋サイドカー `<BASE>_feature_annotations.parquet` 生成）。peak-list 無しで登録すると「分子情報なし」に
なるが、本体は数 GB で再登録・UMAP 再計算は非現実的。

本モジュールは SCiLS「Static feature list」CSV（通常登録で読む peak-list と同一形式）を後から与え、
**本体 parquet を書き換えずにサイドカーだけを生成**して分子情報を反映させる。変換時と同じ既存関数
（`scils_converter._read_peaklist` / `peak_annotation.build_feature_annotation_table`）を再利用するため、
生成物は通常登録と同一。対象は TIMS/SCiLS（MALDI 含む）。Dash 非依存の純ロジック。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def _read_feature_mz(data_folder) -> np.ndarray:
    """登録済みデータの特徴量 m/z 一覧（昇順）を返す。

    footer メタ `mz_sorted`（変換時に常に書かれる・分子情報なし登録でも存在）を最優先。
    無ければ列名（`mz_<値>` / 裸の数値 / 埋め込み `化合物名_<m/z> | …`）から復元する。
    """
    import pyarrow.parquet as pq

    from app.services.data_manager import (
        build_tims_input_paths,
        _read_mz_sorted_metadata,
        _mz_from_embedded_name,
    )

    paths = build_tims_input_paths(str(data_folder))
    if not paths:
        return np.asarray([], dtype=float)

    pf = pq.ParquetFile(paths[0])
    mz = _read_mz_sorted_metadata(pf)
    if mz:
        return np.asarray(sorted(mz), dtype=float)

    # フォールバック: 列名から m/z を復元
    non_meta = {"id", "x", "y", "annotation"}
    vals: list[float] = []
    for n in pf.schema.names:
        if n in non_meta:
            continue
        m: Optional[float] = None
        if n.startswith("mz_"):
            try:
                m = float(n[3:])
            except ValueError:
                m = None
        if m is None:
            try:
                m = float(n)
            except ValueError:
                m = None
        if m is None:
            m = _mz_from_embedded_name(n)
        if m is not None:
            vals.append(m)
    return np.asarray(sorted(vals), dtype=float)


def _main_parquet_base(data_folder) -> Optional[str]:
    """本体 parquet の stem（サイドカー名 `<BASE>_feature_annotations.parquet` の BASE）。"""
    from app.services.data_manager import build_tims_input_paths

    paths = build_tims_input_paths(str(data_folder))
    return Path(paths[0]).stem if paths else None


def _target_dirs(sub: dict, also_result_dirs: bool) -> list:
    """サイドカーを書き出すディレクトリ群（data_folder ＋ 現行の結果ディレクトリ）。"""
    data_folder = (sub or {}).get("data_folder")
    dirs = [Path(data_folder)]
    if also_result_dirs:
        for key in ("last_result_dir", "output_dir"):
            v = (sub or {}).get(key)
            try:
                if v and Path(v).is_dir() and Path(v).resolve() != Path(data_folder).resolve():
                    dirs.append(Path(v))
            except OSError:
                continue
    return dirs


def _invalidate_caches(result_dirs: list) -> None:
    """後付け反映のためのキャッシュ無効化。

    - annotation_inspect のプロセス内キャッシュを一掃（folder mtime でも自動無効化されるが念のため）。
    - seurat_bridge の `feature_annotations.json`（結果ディレクトリ配下）を削除（次回読込で再構築）。
      ※ seurat_bridge 側も mtime で自己修復するが、確実に反映させるため best-effort で削除。
    """
    try:
        from app.services import annotation_inspect as ai
        ai._INSPECT_CACHE.clear()
    except Exception:
        pass
    for d in result_dirs:
        try:
            for cache_json in Path(d).rglob("feature_annotations.json"):
                try:
                    cache_json.unlink()
                    logger.info("stale feature_annotations.json を削除: %s", cache_json)
                except OSError:
                    pass
        except Exception:
            pass


def attach_molecular_info(
    sub: Optional[dict],
    csv_path,
    *,
    tol_da: float = 0.01,
    dry_run: bool = False,
    also_result_dirs: bool = True,
) -> dict:
    """SCiLS feature-list CSV を既存サブプロジェクトへ後付けし、サイドカーを生成する。

    Args:
        sub: サブプロジェクト dict（data_folder / last_result_dir / output_dir を参照）
        csv_path: アップロードされた SCiLS「Static feature list」CSV のパス
        tol_da: peak-list m/z と特徴量 m/z の最近傍マッチ許容（Da）
        dry_run: True なら書き込み・無効化をせず件数のみ返す（プレビュー用）
        also_result_dirs: data_folder に加え結果ディレクトリにもサイドカーを置く

    Returns:
        dict: status / n_features / n_peaklist / n_matched / sidecar_paths / base
    """
    from app.services.scils_converter import _read_peaklist
    from app.services.peak_annotation import build_feature_annotation_table
    from app.services.annotation_inspect import _is_real_compound

    data_folder = (sub or {}).get("data_folder")
    if not data_folder or not Path(data_folder).is_dir():
        raise ValueError("data_folder が未設定、またはフォルダが存在しません。")

    mz_sorted = _read_feature_mz(data_folder)
    if mz_sorted.size == 0:
        raise ValueError(
            "本体 parquet から特徴量 m/z を取得できませんでした"
            "（mz_sorted メタ・列名とも不明）。TIMS/SCiLS データか確認してください。"
        )

    base = _main_parquet_base(data_folder)
    if not base:
        raise ValueError("本体 parquet が data_folder に見つかりません。")

    pk_mz, pk_names = _read_peaklist(Path(csv_path))
    if pk_mz.size == 0:
        raise ValueError("peak-list CSV から m/z を読み取れませんでした（形式を確認してください）。")

    feat_df = build_feature_annotation_table(mz_sorted, pk_mz, pk_names, tol_da=tol_da)
    n_matched = int(feat_df["compound"].apply(_is_real_compound).sum())

    result = {
        "status": "ok",
        "n_features": int(mz_sorted.size),
        "n_peaklist": int(pk_mz.size),
        "n_matched": n_matched,
        "sidecar_paths": [],
        "base": base,
    }
    if dry_run:
        result["status"] = "preview"
        return result

    dirs = _target_dirs(sub, also_result_dirs)
    written: list[str] = []
    for d in dirs:
        sidecar = Path(d) / f"{base}_feature_annotations.parquet"
        try:
            feat_df.to_parquet(str(sidecar), index=False)
            written.append(str(sidecar))
            logger.info("サイドカー出力: %s", sidecar)
        except Exception as e:
            logger.warning("サイドカー出力に失敗 (%s): %s", sidecar, e)
    if not written:
        raise RuntimeError("サイドカーの書き出しに失敗しました。")

    # 結果ディレクトリ（data_folder を除く）のキャッシュを無効化
    result_dirs = [d for d in dirs if Path(d).resolve() != Path(data_folder).resolve()]
    _invalidate_caches(result_dirs)

    result["sidecar_paths"] = written
    return result
