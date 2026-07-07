"""データ出力コールバック — 元データに UMAP cluster 列を追加してエクスポート。

DESI: .txt → Excel（サンプル別シート）
TIMS: Parquet/CSV → 選択形式（Excel / CSV / Parquet）

複数手法（Harmony / RPCA 等）が存在する場合は、全手法のクラスター情報を
1つのファイルにまとめて出力する（Method 列 + UMAP cluster 列）。
"""

import contextvars
import io
import logging
import re
import threading
from collections import OrderedDict
from pathlib import Path

import pandas as pd
from dash import Input, Output, State, callback, dcc, no_update
from dash.exceptions import PreventUpdate

from app.callbacks.interactive_callbacks import _bridge, _interactive_data
from app.utils.color_utils import cluster_display_name
from app.utils.label_persistence import load_cluster_name_map
from app.services import hne_overlay as hn
from app.services import hne_persistence as hp
from app.services.data_manager import (
    build_tims_input_paths,
    list_msi_files,
)

logger = logging.getLogger(__name__)
logger.info("[DataExport] モジュール読み込み完了 (v2)")


# 進捗ジョブレジストリ（Dash 非依存の services モジュールへ分離＝単体テスト可）。
from app.services.export_progress import (  # noqa: E402
    new_job as _new_job,
    update_job as _update_job,
    finish_job as _finish_job,
    fail_job as _fail_job,
    get_job as _get_job,
    pop_job as _pop_job,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_instrument(ms_instrument, *paths) -> str:
    """エクスポート経路を決める ms_instrument を確定する。

    サブプロジェクト metadata の ms_instrument は未設定時に "TIMS" へフォールバック
    するため、DESIプロジェクトが誤って TIMS 経路に入ることがある。そこで明示 "DESI" を
    優先しつつ、未指定/曖昧な場合はパス規約 (Data/DESI/Data・Data/TIMS/Data) から判定する。

    Returns: "DESI" または "TIMS"
    """
    mi = (ms_instrument or "").strip().upper()
    if mi == "DESI":
        return "DESI"
    joined = "/".join(str(p) for p in paths if p).replace("\\", "/")
    if "/DESI/" in joined and "/TIMS/" not in joined:
        return "DESI"
    if "/TIMS/" in joined:
        return "TIMS"
    return mi or "TIMS"


def _build_cluster_lookup(plot_data: pd.DataFrame, cluster_name_map: dict | None = None) -> dict:
    """plot_data 全体から {(sample, round(x,4), round(y,4)): クラスタ表示名} dict を構築。

    cluster_name_map（クラスタ名変更）があれば、番号ではなく変更名を値にする。
    """
    if plot_data is None or plot_data.empty:
        return {}
    lookup = {}
    for _, row in plot_data.iterrows():
        sx = row.get("SpatialX")
        sy = row.get("SpatialY")
        sample = str(row.get("Sample", ""))
        cluster = row.get("Cluster", "")
        if pd.notna(sx) and pd.notna(sy):
            key = (sample, round(float(sx), 4), round(float(sy), 4))
            lookup[key] = cluster_display_name(cluster, cluster_name_map)
    return lookup


def _build_region_lookup(plot_data: pd.DataFrame, rds_path) -> dict:
    """plot_data 全体から {(sample, round(x,4), round(y,4)): 領域名(ROI)} を構築。

    各切片(sample)の H&E オーバーレイ保存状態（hne_overlay_state.json）から ROI を
    割当てる（`hn.regions_from_overlay`）。overlay 未設定／ROI 未割当の spot は
    キーを作らない（出力では空欄になる）。キーは `_build_cluster_lookup` と同方式
    （元 SpatialX/Y を 4 桁丸め）で、クラスタ列と同じ行に突合される。
    """
    lookup: dict = {}
    if (plot_data is None or not rds_path
            or "SpatialX" not in plot_data.columns
            or "SpatialY" not in plot_data.columns
            or "Sample" not in plot_data.columns):
        return lookup
    for sample in plot_data["Sample"].dropna().astype(str).unique():
        sub = plot_data[plot_data["Sample"].astype(str) == sample]
        if sub.empty:
            continue
        try:
            entry = hp.load_hne_sample(rds_path, sample)
            region = hn.regions_from_overlay(sub, entry)
        except Exception as e:  # noqa: BLE001
            logger.warning("[DataExport] %s: 領域割当に失敗: %s", sample, e)
            continue
        sx = pd.to_numeric(sub["SpatialX"], errors="coerce").to_numpy(float)
        sy = pd.to_numeric(sub["SpatialY"], errors="coerce").to_numpy(float)
        for x, y, r in zip(sx, sy, region.to_numpy()):
            if r is None or pd.isna(x) or pd.isna(y):
                continue
            lookup[(sample, round(float(x), 4), round(float(y), 4))] = str(r)
    return lookup


def _safe_prefix(name: str) -> str:
    """R の safe_prefix 変換を再現: [^A-Za-z0-9_-] → '_'"""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", name)


def _match_sample_name(file_stem: str, sample_names: list[str]) -> str | None:
    """ファイル名ステムと plot_data の Sample 名をマッチング。

    1. 完全一致
    2. safe_prefix 変換後一致
    3. 部分一致（file_stem が Sample に含まれる or 逆）
    """
    # 完全一致
    if file_stem in sample_names:
        return file_stem
    # safe_prefix 変換後一致
    safe = _safe_prefix(file_stem)
    for sn in sample_names:
        if _safe_prefix(sn) == safe:
            return sn
    # 部分一致
    for sn in sample_names:
        if file_stem in sn or sn in file_stem:
            return sn
    return None


def _has_msi_files(folder: Path, ms_instrument: str | None) -> bool:
    """指定フォルダに MSI データファイルが存在するか判定。"""
    if not folder.is_dir():
        return False
    is_desi = (ms_instrument or "").upper() == "DESI"
    if is_desi:
        return bool(list(folder.glob("*.txt")))
    exts = {".parquet", ".pq", ".csv", ".tsv"}
    return any(
        f.suffix.lower() in exts for f in folder.iterdir() if f.is_file()
    )


def _is_within(path: Path, root: Path) -> bool:
    """path が root と等しい、または root の配下にあるか。"""
    try:
        p = path.resolve()
        r = root.resolve()
    except Exception:
        p, r = path, root
    return p == r or r in p.parents


def _project_root_for(result_path: Path):
    """result_path が属する『プロジェクトルート』(データ/出力ルート直下のディレクトリ)を返す。

    別プロジェクト混入を防ぐため、データフォルダ推定の走査をこのルート配下に限定する用途。
    既知のデータ/出力ルート配下でない場合は None。
    """
    try:
        from app.config import (
            DESI_DATA_CANDIDATES, TIMS_DATA_CANDIDATES, OUTPUT_DATA_CANDIDATES,
        )
        roots = (list(DESI_DATA_CANDIDATES) + list(TIMS_DATA_CANDIDATES)
                 + list(OUTPUT_DATA_CANDIDATES))
    except Exception:
        roots = []
    try:
        rp = result_path.resolve()
    except Exception:
        rp = result_path
    for root in roots:
        try:
            root_r = Path(root).resolve()
        except Exception:
            continue
        if root_r in rp.parents:
            rel = rp.relative_to(root_r)
            if rel.parts:
                return root_r / rel.parts[0]
    return None


def _infer_data_folder(
    result_folder: str | None,
    project_id: str | None,
    sub_project_id: str | None,
    ms_instrument: str | None,
) -> str | None:
    """MSI データフォルダを自動推定する。

    推定順: (a) サブプロジェクトメタデータ → (b) 結果フォルダ兄弟ディレクトリスキャン
    """
    # (a) サブプロジェクトメタデータから取得
    if project_id and sub_project_id:
        try:
            from app.services.project_manager import get_sub_project

            sub = get_sub_project(project_id, sub_project_id)
            if sub and sub.get("data_folder"):
                candidate = Path(sub["data_folder"])
                if candidate.is_dir():
                    return str(candidate)
        except Exception:
            pass

    # (b) 結果フォルダ配下のスキャン（当該プロジェクト内に限定）。
    # 別プロジェクト混入を防ぐため、全プロジェクト共通のデータルート
    # (例: Data/DESI/Data) は走査せず、プロジェクトルート配下のみを探索する。
    if not result_folder:
        return None

    result_path = Path(result_folder)
    if not result_path.is_dir():
        return None

    _skip = {"RDS_Files", "log", "__pycache__", ".git"}
    project_root = _project_root_for(result_path)

    search_roots = [result_path.parent]
    if project_root and project_root.is_dir() and project_root != result_path.parent:
        search_roots.append(project_root)

    for root in search_roots:
        # プロジェクトルートが判明している場合、その配下以外は走査しない
        if project_root is not None and not _is_within(root, project_root):
            continue
        # 生データが「データセットフォルダ直下」にあるケース（結果フォルダの親に .txt 等を
        # 直接置く運用）。サブフォルダだけでなくルート自身も MSI データの有無を確認する。
        if root != result_path and _has_msi_files(root, ms_instrument):
            return str(root)
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child == result_path or child.name in _skip:
                continue
            # 別プロジェクトのディレクトリは除外
            if project_root is not None and not _is_within(child, project_root):
                continue
            if _has_msi_files(child, ms_instrument):
                return str(child)

    return None


def ensure_sub_project_data_folder(project_id, sub_id, result_folder, ms_instrument):
    """サブプロジェクトの data_folder が空なら、プロジェクト内限定推定で解決して保存する。

    生データ登録パスが空欄のサブプロジェクト（推定フォールバックに落ちる）を自己修復し、
    以後の出力が推定に頼らず確実にそのフォルダを使えるようにする。
    Returns: 解決/既存の data_folder (str) または None。
    """
    if not project_id or not sub_id:
        return None
    try:
        from app.services.project_manager import get_sub_project, update_sub_project
        sub = get_sub_project(project_id, sub_id)
        if not sub:
            return None
        existing = sub.get("data_folder")
        if existing:
            return existing  # 既に設定済みなら触らない
        inst = _resolve_instrument(ms_instrument, result_folder)
        resolved = _infer_data_folder(result_folder, project_id, sub_id, inst)
        if resolved:
            update_sub_project(project_id, sub_id, {"data_folder": resolved})
        return resolved
    except Exception:
        logger.exception("[DataExport] data_folder バックフィルに失敗")
        return None


def _build_all_method_lookups(
    rds_map: dict | None,
    current_method: str | None,
    cluster_name_map: dict | None = None,
    selected_methods: list | None = None,
    progress_cb=None,
    base: int = 0,
    span: int = 0,
) -> OrderedDict:
    """選択手法のクラスタールックアップを構築。

    selected_methods: 出力対象の手法名リスト（None/空 → rds_map の全手法）。
    現在の手法は ``_interactive_data["plot_data"]`` を再利用し、それ以外は
    ``_bridge.extract_data()`` で動的ロードする。派生 PCA は Harmony から遅延生成。
    Returns:
        OrderedDict {method_name: cluster_lookup_dict}
    """
    method_lookups: OrderedDict[str, dict] = OrderedDict()
    full_map = rds_map if isinstance(rds_map, dict) else {}

    # 選択手法でフィルタ（None/空 → 全手法）
    sel = set(str(m) for m in (selected_methods or []))
    rmap = {m: p for m, p in full_map.items() if (not sel or m in sel)}

    if not rmap:
        # rds_map 無し → 現在の plot_data のみ
        plot_data = _interactive_data.get("plot_data")
        if plot_data is not None:
            method_name = _interactive_data.get("method") or "Unknown"
            method_lookups[method_name] = _build_cluster_lookup(plot_data, cluster_name_map)
        return method_lookups

    # 現在の手法を先頭に配置
    ordered_methods = []
    if current_method and current_method in rmap:
        ordered_methods.append(current_method)
    for m in rmap:
        if m not in ordered_methods:
            ordered_methods.append(m)

    n_methods = max(1, len(ordered_methods))
    for i_m, method_name in enumerate(ordered_methods):
        if progress_cb:
            progress_cb(int(base + span * i_m / n_methods),
                        f"手法クラスタを準備中… ({method_name})")
        if method_name == current_method and _interactive_data.get("plot_data") is not None:
            # 現在の手法は再読込不要
            method_lookups[method_name] = _build_cluster_lookup(
                _interactive_data.get("plot_data"), cluster_name_map)
            continue
        rds_path = rmap[method_name]
        # 派生 PCA（未補正）はディスク未生成のことがある → Harmony から遅延生成
        if method_name == "PCA" and rds_path and not Path(rds_path).exists():
            harmony_rds = full_map.get("Harmony")
            if harmony_rds and Path(harmony_rds).exists():
                try:
                    _bridge.derive_uncorrected_pca(harmony_rds, rds_path)
                except Exception as e:
                    logger.warning("[DataExport] PCA 派生生成失敗: %s", e)
        if not rds_path or not Path(rds_path).exists():
            logger.warning("[DataExport] %s: RDS が見つかりません → スキップ", method_name)
            continue
        try:
            result = _bridge.extract_data(rds_path)
            # 手法ごとにクラスタ名変更マップは独立。他手法はその手法の保存分を読む。
            other_map = load_cluster_name_map(rds_path, method_name)
            method_lookups[method_name] = _build_cluster_lookup(
                result["plot_data"], other_map)
        except Exception as e:
            logger.warning("[DataExport] %s: データ抽出エラー: %s", method_name, e)

    return method_lookups


# ---------------------------------------------------------------------------
# DESI エクスポート
# ---------------------------------------------------------------------------

def _export_desi(
    data_folder: str, method_lookups: OrderedDict, region_lookup: dict | None = None,
    progress_cb=None, base: int = 0, span: int = 0,
) -> tuple[bytes, str]:
    """DESI .txt → Excel バイト列（サンプル別シート + 手法別クラスター列）。

    複数手法の場合は手法名を列ヘッダーにして横並びで配置する。
    単一手法の場合は従来通り「UMAP cluster」列1つ。
    region_lookup を渡すと最終列に「領域名」(ROI) を付与する（未割当は空欄）。

    Returns (excel_bytes, filename)
    """
    add_region = region_lookup is not None
    file_stems = list_msi_files(data_folder)
    if not file_stems:
        raise ValueError("DESI .txt ファイルが見つかりません")

    is_multi = len(method_lookups) > 1
    method_names = list(method_lookups.keys())

    # 全手法から Sample 名を収集
    all_sample_names: set[str] = set()
    for lookup in method_lookups.values():
        all_sample_names.update(k[0] for k in lookup.keys())
    sample_names = sorted(all_sample_names)

    n_files = max(1, len(file_stems))
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for i_f, stem in enumerate(file_stems):
            if progress_cb:
                progress_cb(int(base + span * i_f / n_files),
                            f"書き込み中… {i_f + 1}/{n_files} ({stem})")
            txt_path = Path(data_folder) / f"{stem}.txt"
            if not txt_path.exists():
                continue

            # 行単位で読み込み（ヘッダー20列/データ21列の不一致に対応）
            with open(txt_path, "r", encoding="utf-8", errors="replace") as fh:
                raw_lines = fh.readlines()

            if not raw_lines:
                continue

            rows = [line.rstrip("\r\n").split("\t") for line in raw_lines]
            max_cols = max(len(r) for r in rows)
            matched_sample = _match_sample_name(stem, sample_names)

            output_rows: list[list[str]] = []

            # ヘッダー行（先頭5行）
            for i in range(min(5, len(rows))):
                padded = rows[i] + [""] * (max_cols - len(rows[i]))
                if i == 0:
                    # 行1: ラベル行 → 最右に手法名を列ヘッダーとして追加
                    if is_multi:
                        padded.extend(method_names)
                    else:
                        padded.append("UMAP cluster")
                    if add_region:
                        padded.append("領域名")  # 最終列に ROI
                else:
                    # 行2〜5: ヘッダー行 → 空セル
                    padded.extend([""] * len(method_names) if is_multi else [""])
                    if add_region:
                        padded.append("")
                output_rows.append(padded)

            # データ行（行5以降）— 各行に全手法のクラスター値を横並びで追加
            data_rows = rows[5:] if len(rows) > 5 else []

            for row in data_rows:
                padded = row + [""] * (max_cols - len(row))

                # 座標を一度だけ取得
                x_val, y_val = None, None
                if matched_sample and len(row) >= 3:
                    try:
                        x_val = round(float(row[1]), 4)
                        y_val = round(float(row[2]), 4)
                    except (ValueError, IndexError):
                        pass

                # 各手法のクラスター値を列として追加
                for method_name in method_names:
                    cluster_val = ""
                    if x_val is not None and y_val is not None:
                        key = (matched_sample, x_val, y_val)
                        cluster_val = method_lookups[method_name].get(key, "")
                    padded.append(cluster_val)

                # 最終列に領域名(ROI)
                if add_region:
                    region_val = ""
                    if x_val is not None and y_val is not None:
                        region_val = region_lookup.get((matched_sample, x_val, y_val), "")
                    padded.append(region_val)

                output_rows.append(padded)

            df_out = pd.DataFrame(output_rows)

            # シート名（Excel の31文字制限対応）
            sheet_name = stem[:31]
            df_out.to_excel(
                writer, sheet_name=sheet_name, header=False, index=False
            )

    buf.seek(0)
    return buf.getvalue(), "UMAP_cluster_DESI.xlsx"


# ---------------------------------------------------------------------------
# TIMS エクスポート
# ---------------------------------------------------------------------------

def _read_tims_file(file_path: str) -> pd.DataFrame:
    """TIMS 入力ファイルを読み込む（Parquet/CSV/TSV 自動判定）。"""
    p = Path(file_path)
    ext = p.suffix.lower()
    if ext in (".parquet", ".pq"):
        return pd.read_parquet(file_path)
    elif ext == ".tsv":
        return pd.read_csv(file_path, sep="\t")
    else:
        return pd.read_csv(file_path)


def _export_tims(
    data_folder: str, method_lookups: OrderedDict, fmt: str,
    region_lookup: dict | None = None,
    progress_cb=None, base: int = 0, span: int = 0,
) -> tuple[bytes, str]:
    """TIMS 入力ファイルに手法別クラスター列を追加してエクスポート。

    複数手法の場合は手法名を列名にして横並びで配置する。
    単一手法の場合は従来通り「UMAP cluster」列1つ。
    region_lookup を渡すと最終列に「領域名」(ROI) を付与する（未割当は空欄）。

    Returns (file_bytes, filename)
    """
    input_paths = build_tims_input_paths(data_folder)
    if not input_paths:
        raise ValueError("TIMS 入力ファイルが見つかりません")

    is_multi = len(method_lookups) > 1
    method_names = list(method_lookups.keys())

    # 全手法の Sample 名を統合
    all_sample_names: set[str] = set()
    for lookup in method_lookups.values():
        all_sample_names.update(k[0] for k in lookup.keys())
    all_sample_list = sorted(all_sample_names)

    n_files = max(1, len(input_paths))
    dfs_out = []
    for i_f, fp in enumerate(input_paths):
        if progress_cb:
            progress_cb(int(base + span * i_f / n_files),
                        f"書き込み中… {i_f + 1}/{n_files} ({Path(fp).stem})")
        df = _read_tims_file(fp)
        stem = Path(fp).stem
        has_annotation = "annotation" in df.columns

        # 各行のサンプルマッチングと座標キーを一度だけ計算
        row_keys: list[tuple[str | None, float | None, float | None]] = []
        for _, row in df.iterrows():
            x_val = row.get("x")
            y_val = row.get("y")

            if has_annotation:
                sample_id = str(row["annotation"])
            else:
                sample_id = stem

            matched = None
            if sample_id in all_sample_list:
                matched = sample_id
            else:
                matched = _match_sample_name(sample_id, all_sample_list)

            if matched and pd.notna(x_val) and pd.notna(y_val):
                row_keys.append(
                    (matched, round(float(x_val), 4), round(float(y_val), 4))
                )
            else:
                row_keys.append((None, None, None))

        # 各手法のクラスター列を追加
        for method_name in method_names:
            cluster_lookup = method_lookups[method_name]
            col_name = method_name if is_multi else "UMAP cluster"
            df[col_name] = [
                cluster_lookup.get((m, x, y), "") if m else ""
                for m, x, y in row_keys
            ]

        # 最終列に領域名(ROI)
        if region_lookup is not None:
            df["領域名"] = [
                region_lookup.get((m, x, y), "") if m else ""
                for m, x, y in row_keys
            ]

        dfs_out.append(df)

    df_all = (
        pd.concat(dfs_out, ignore_index=True) if len(dfs_out) > 1 else dfs_out[0]
    )

    # 出力形式に応じてバイト列を生成
    buf = io.BytesIO()
    if fmt == "xlsx":
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df_all.to_excel(writer, index=False, sheet_name="Data")
        filename = "UMAP_cluster_TIMS.xlsx"
    elif fmt == "csv":
        buf.write(df_all.to_csv(index=False).encode("utf-8"))
        filename = "UMAP_cluster_TIMS.csv"
    elif fmt == "parquet":
        df_all.to_parquet(buf, index=False)
        filename = "UMAP_cluster_TIMS.parquet"
    else:
        buf.write(df_all.to_csv(index=False).encode("utf-8"))
        filename = "UMAP_cluster_TIMS.csv"

    buf.seek(0)
    return buf.getvalue(), filename


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def _do_export(
    data_folder, ms_instrument, export_format,
    rds_map, current_method, result_folder, project_id, sub_project_id,
    loaded_rds, cluster_name_map=None, selected_methods=None, progress_cb=None,
):
    """データ出力の本体。開いている(読み込み済みの)プロジェクトにスコープを固定して、
    元データに UMAP cluster 列を付与したファイルを生成する。

    progress_cb: progress_cb(pct:int, label:str) を渡すと 0-100 の進捗を報告する（任意）。
    Returns: (dcc.send_bytes(...) または no_update, status_message)
    """
    def _p(pct, label=""):
        if progress_cb:
            try:
                progress_cb(int(pct), label)
            except Exception:  # noqa: BLE001
                pass

    from app.callbacks.interactive_callbacks import _set_active_key
    # 開いているプロジェクト(= 実際に読み込んだ RDS)にアクティブキーを固定する。
    # 別プロジェクトの plot_data / クラスタを読まないよう loaded_rds を最優先・無条件に設定。
    if loaded_rds:
        _set_active_key(loaded_rds)
    elif rds_map and current_method and current_method in rds_map:
        _set_active_key(rds_map[current_method])

    logger.info(
        "[DataExport] _do_export: loaded_rds=%s data_folder=%s", loaded_rds, data_folder
    )

    try:
        # ms_instrument を確定（DESIプロジェクトが既定の "TIMS" に落ち、誤って TIMS 経路に
        # 入る事象への対策）。metadata が "DESI" でなくてもパス規約から DESI を判定する。
        ms_instrument = _resolve_instrument(ms_instrument, data_folder, result_folder)
        logger.info("[DataExport] instrument 確定: %s", ms_instrument)
        _p(5, "準備中…")

        # MSI データフォルダの自動推定（当該プロジェクト内に限定して推定する）
        if not data_folder:
            data_folder = _infer_data_folder(
                result_folder, project_id, sub_project_id, ms_instrument
            )
            logger.info("[DataExport] 自動推定結果: %s", data_folder)
        if not data_folder:
            return no_update, (
                "❌ MSIデータフォルダが見つかりません。"
                "サブプロジェクト設定でMSIデータフォルダを指定してください。"
            )

        plot_data = _interactive_data.get("plot_data")
        if plot_data is None or plot_data.empty:
            return no_update, "データが読み込まれていません。先にデータを読み込んでください。"

        # SpatialX/SpatialY が必要
        if "SpatialX" not in plot_data.columns or "SpatialY" not in plot_data.columns:
            return no_update, "空間座標データ (SpatialX/SpatialY) がありません。"
        # 選択手法のクラスタールックアップを構築（未選択なら全手法）: 進捗 10→50%
        method_lookups = _build_all_method_lookups(
            rds_map, current_method, cluster_name_map, selected_methods,
            progress_cb=progress_cb, base=10, span=40)
        if not method_lookups:
            return no_update, "クラスターデータを構築できませんでした。"

        # 領域名(ROI) ルックアップ（読込中 RDS の H&E オーバーレイ保存状態から）。
        # 設定が無ければ空 dict（最終列は空欄）。
        _p(52, "ROI(領域名)を割当中…")
        region_lookup = _build_region_lookup(plot_data, loaded_rds)

        is_desi = (ms_instrument or "").upper() == "DESI"

        # ファイル書き込み: 進捗 58→98%
        if is_desi:
            file_bytes, filename = _export_desi(
                data_folder, method_lookups, region_lookup,
                progress_cb=progress_cb, base=58, span=40)
        else:
            fmt = export_format or "xlsx"
            file_bytes, filename = _export_tims(
                data_folder, method_lookups, fmt, region_lookup,
                progress_cb=progress_cb, base=58, span=40)
        _p(99, "仕上げ中…")

        # ステータスメッセージ
        n_methods = len(method_lookups)
        methods_str = " / ".join(method_lookups.keys())
        msg = f"✅ {filename} をダウンロードしました"
        if n_methods > 1:
            msg += f" ({methods_str})"

        return dcc.send_bytes(file_bytes, filename), msg

    except Exception as e:
        logger.exception("データ出力エラー")
        return no_update, f"❌ エラー: {e}"


# ---------------------------------------------------------------------------
# 進捗 % 表示（インプロセス作業スレッド + dcc.Interval ポーリング）。
#  start : ボタン押下で作業スレッドを起動し、進捗UI(0%)表示・ボタン無効・Interval 有効化。
#  poll  : Interval ごとにジョブレジストリを読み、バー%/ラベル更新。完了でダウンロード配信。
# background=True(set_progress) は使わない（_do_export が _interactive_data のインプロセス
# 状態＝セッションの plot_data 等を参照するため、DiskcacheManager の fork worker では共有されない）。
# サーバは単一プロセス・マルチスレッドなので、作業スレッドと poll は同一プロセス＝レジストリ共有可。
# ---------------------------------------------------------------------------

_PROG_SHOW = {"display": "block", "marginTop": "8px"}
_PROG_HIDE = {"display": "none"}


@callback(
    [Output("data_export_method_selector", "options"),
     Output("data_export_method_selector", "value")],
    Input("interactive_rds_map", "data"),
    prevent_initial_call=True,
)
def update_data_export_method_options(rds_map):
    """rds_map から出力手法チェックリストを更新（既定で全手法チェック）。"""
    if not rds_map or not isinstance(rds_map, dict):
        return [], []
    methods = list(rds_map.keys())
    return [{"label": m, "value": m} for m in methods], methods


def _run_export_job(job_id, args):
    """作業スレッド本体: _do_export を実行し進捗をレジストリへ反映する。"""
    try:
        download, msg = _do_export(
            *args, progress_cb=lambda p, l="": _update_job(job_id, p, l))
        if download is no_update or not download:
            _fail_job(job_id, msg or "出力に失敗しました")
        else:
            _finish_job(job_id, download, msg)
    except Exception as e:  # noqa: BLE001
        logger.exception("[DataExport] ジョブ実行エラー")
        _fail_job(job_id, f"❌ エラー: {e}")


@callback(
    [Output("data_export_progress_container", "style"),
     Output("data_export_progress_label", "children"),
     Output("data_export_progress_bar", "value"),
     Output("data_export_progress_bar", "animated"),
     Output("btn_export_data", "disabled"),
     Output("data_export_job", "data"),
     Output("data_export_poll", "disabled")],
    Input("btn_export_data", "n_clicks"),
    [State("interactive_msi_folder", "value"),
     State("int_cal_ms_instrument", "data"),
     State("data_export_format", "value"),
     State("interactive_rds_map", "data"),
     State("interactive_integration_method", "value"),
     State("interactive_result_folder", "value"),
     State("interactive_project_select", "value"),
     State("interactive_sub_project_select", "value"),
     State("seurat_rds_path_store", "data"),
     State("cluster_name_map_store", "data"),
     State("data_export_method_selector", "value")],
    prevent_initial_call=True,
)
def data_export_start(n_clicks, data_folder, ms_instrument, export_format,
                      rds_map, current_method, result_folder,
                      project_id, sub_project_id, loaded_rds, cluster_name_map,
                      selected_methods):
    """出力開始: 作業スレッドを起動し、進捗UI(0%)表示・ボタン無効・Interval 有効化。"""
    if not n_clicks:
        raise PreventUpdate
    job_id = _new_job()
    args = (data_folder, ms_instrument, export_format, rds_map, current_method,
            result_folder, project_id, sub_project_id, loaded_rds, cluster_name_map,
            selected_methods)
    # 親コンテキスト(ContextVar の active key 等)を引き継いでスレッド実行。
    ctx = contextvars.copy_context()
    threading.Thread(
        target=ctx.run, args=(_run_export_job, job_id, args), daemon=True
    ).start()
    return (_PROG_SHOW, "準備中…  0%", 0, False, True, {"job": job_id}, False)


@callback(
    [Output("dl_data_export", "data"),
     Output("div_data_export_status", "children"),
     Output("data_export_progress_container", "style", allow_duplicate=True),
     Output("data_export_progress_label", "children", allow_duplicate=True),
     Output("data_export_progress_bar", "value", allow_duplicate=True),
     Output("data_export_progress_bar", "animated", allow_duplicate=True),
     Output("btn_export_data", "disabled", allow_duplicate=True),
     Output("data_export_poll", "disabled", allow_duplicate=True)],
    Input("data_export_poll", "n_intervals"),
    State("data_export_job", "data"),
    prevent_initial_call=True,
)
def data_export_poll(n_intervals, job_store):
    """Interval ごとにジョブ進捗を読み、バー/ラベル更新。完了でダウンロード配信＆停止。"""
    job_id = (job_store or {}).get("job")
    if not job_id:
        raise PreventUpdate
    job = _get_job(job_id)
    if job is None:
        # 既に配信済み or 不明 → ポーリング停止のみ
        return (no_update,) * 7 + (True,)
    status = job["status"]
    if status == "running":
        pct = job["pct"]
        label = f"{job['label']}  {pct}%"
        return (no_update, no_update, no_update, label, pct,
                False, no_update, no_update)
    if status == "done":
        _pop_job(job_id)
        return (job["download"], job["msg"], _PROG_HIDE, "完了", 100,
                False, False, True)
    # error
    _pop_job(job_id)
    return (no_update, job["msg"], _PROG_HIDE, "失敗", no_update,
            False, False, True)


@callback(
    Output("data_export_format_wrapper", "style"),
    Input("int_cal_ms_instrument", "data"),
    prevent_initial_call=True,
)
def toggle_format_selector(ms_instrument):
    """DESI → フォーマットセレクタ非表示 / TIMS → 表示。"""
    if (ms_instrument or "").upper() == "DESI":
        return {"display": "none"}
    return {"display": "block"}
