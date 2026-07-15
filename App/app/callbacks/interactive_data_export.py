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
from dash import (
    Input, Output, State, callback, clientside_callback, html, no_update,
)
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
from app.services.export_transform import (
    append_cluster_region_columns as _append_cluster_region_columns,
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
    sweep_old_files as _sweep_old_files,
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


def _read_source_parquet_metadata(input_paths: list) -> dict:
    """入力 parquet 群が共有するスキーマメタ (mz_sorted/annotation_files/peak_list) を返す。

    SCiLS 変換器が付与するこれらのメタは pandas 読込(`pd.read_parquet`)で失われるため、
    出力 parquet に再付与できるよう、ソース parquet から直接読み出す。

    - `.parquet/.pq` 入力のみ対象（CSV/TSV は元々メタを持たない）。
    - `mz_sorted` は「フル桁 m/z の正」なので、いずれかの入力で欠落 or 入力間で不一致の
      ときは誤った m/z 軸を書かないよう `{}` を返す（＝メタ非付与）。
    """
    pqs = [p for p in input_paths if Path(p).suffix.lower() in (".parquet", ".pq")]
    if not pqs:
        return {}
    import pyarrow.parquet as pq

    mds = []
    for p in pqs:
        try:
            md = pq.ParquetFile(p).schema_arrow.metadata or {}
        except Exception as e:  # 読めない入力があればメタ付与を諦める（データ出力は継続）
            logger.warning("[DataExport] スキーマメタ読取失敗 (%s): %s", p, e)
            return {}
        mds.append(md)

    mz_list = [md.get(b"mz_sorted") for md in mds]
    if any(v is None for v in mz_list):
        logger.warning(
            "[DataExport] 一部入力に mz_sorted メタが無いため、出力へメタを付与しません。")
        return {}
    if any(v != mz_list[0] for v in mz_list):
        logger.warning(
            "[DataExport] 入力間で mz_sorted が一致しないため、出力へメタを付与しません。")
        return {}

    md0 = mds[0]
    return {
        k: md0[k]
        for k in (b"mz_sorted", b"annotation_files", b"peak_list")
        if k in md0
    }


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
        # 右端に手法別クラスタ列・領域名列をベクトル付与（iterrows 撤廃＝軽い）。
        df = _append_cluster_region_columns(
            df, method_lookups, region_lookup, all_sample_list, is_multi, stem,
            _match_sample_name)
        dfs_out.append(df)

    df_all = (
        pd.concat(dfs_out, ignore_index=True) if len(dfs_out) > 1 else dfs_out[0]
    )

    # 出力形式に応じてバイト列を生成
    buf = io.BytesIO()
    if fmt == "xlsx" and df_all.shape[1] > 16384:
        # Excel の列上限。MSI は m/z 列が多く超過し得る → CSV/Parquet を案内。
        raise ValueError(
            f"xlsx は列数上限(16,384)を超えます（{df_all.shape[1]} 列）。"
            "出力形式で CSV または Parquet を選択してください。")
    if fmt == "xlsx":
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df_all.to_excel(writer, index=False, sheet_name="Data")
        filename = "UMAP_cluster_TIMS.xlsx"
    elif fmt == "csv":
        buf.write(df_all.to_csv(index=False).encode("utf-8"))
        filename = "UMAP_cluster_TIMS.csv"
    elif fmt == "parquet":
        # 入力(登録)parquet と同一の内部構造で書き出す:
        #  - ソースのスキーマメタ (mz_sorted/annotation_files/peak_list) を再付与
        #    （pandas 経由で失われるため入力から直接復元）
        #  - 圧縮を入力と同じ zstd に統一
        #  - 追加した解析列(UMAP cluster/手法名/領域名)を analysis_columns メタに記録し、
        #    再登録時に読取側が特徴量列と区別できるようにする
        import pyarrow as pa
        import pyarrow.parquet as pq

        analysis_cols = (
            list(method_lookups.keys()) if is_multi else ["UMAP cluster"]
        )
        if region_lookup is not None:
            analysis_cols.append("領域名")

        carried = _read_source_parquet_metadata(input_paths)  # {} なら N/A
        table = pa.Table.from_pandas(df_all, preserve_index=False)
        md = dict(table.schema.metadata or {})  # pandas 自身の b"pandas" を保持
        md.update(carried)                      # mz_sorted / annotation_files / peak_list
        md[b"analysis_columns"] = ",".join(analysis_cols).encode("utf-8")
        table = table.replace_schema_metadata(md)
        pq.write_table(table, buf, compression="zstd")
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
    Returns: (file_bytes|None, filename|None, status_message)。失敗時は (None, None, msg)。
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
            return None, None, (
                "❌ MSIデータフォルダが見つかりません。"
                "サブプロジェクト設定でMSIデータフォルダを指定してください。"
            )

        plot_data = _interactive_data.get("plot_data")
        if plot_data is None or plot_data.empty:
            return None, None, "データが読み込まれていません。先にデータを読み込んでください。"

        # SpatialX/SpatialY が必要
        if "SpatialX" not in plot_data.columns or "SpatialY" not in plot_data.columns:
            return None, None, "空間座標データ (SpatialX/SpatialY) がありません。"
        # 選択手法のクラスタールックアップを構築（未選択なら全手法）: 進捗 10→50%
        method_lookups = _build_all_method_lookups(
            rds_map, current_method, cluster_name_map, selected_methods,
            progress_cb=progress_cb, base=10, span=40)
        if not method_lookups:
            return None, None, "クラスターデータを構築できませんでした。"

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
        msg = f"✅ {filename} を生成しました"
        if n_methods > 1:
            msg += f" ({methods_str})"

        return file_bytes, filename, msg

    except Exception as e:
        logger.exception("データ出力エラー")
        return None, None, f"❌ エラー: {e}"


# ---------------------------------------------------------------------------
# セッション非依存ドライバ（API / バッチから駆動）
# ---------------------------------------------------------------------------

def _pick_primary_rds(rds_map: dict):
    """ROI(領域名) 割当の基準にする RDS を選ぶ。UI 既定の Harmony を優先。"""
    for m in ("Harmony", "RPCA", "PCA", "PCA (uncorrected)"):
        p = (rds_map or {}).get(m)
        if p and Path(p).exists():
            return p
    for p in (rds_map or {}).values():
        if p and Path(p).exists():
            return p
    return None


def build_interactive_export_for_project(
    data_folder, ms_instrument, export_format,
    rds_map, result_folder, project_id, sub_project_id,
    selected_methods=None, progress_cb=None,
):
    """ライブ session に依存せず UMAP_cluster エクスポートを生成する（API / バッチ用）。

    `_do_export` から `_interactive_data`（ブラウザ session のライブ状態）依存を取り除いた版。
    全手法のクラスタは `_build_all_method_lookups(current_method=None)` でディスクから読む
    （current_method=None のとき同関数は `_interactive_data` を一切参照しない）。
    ROI(領域名) は primary RDS の plot_data + `hne_overlay_state.json`（ディスク）から割り当てる。

    Returns: ``(file_bytes|None, filename|None, message)``。失敗時は ``(None, None, msg)``。
    抽出キャッシュが cold の場合は内部で R 抽出が走り得る（＝重い処理）。
    """
    def _p(pct, label=""):
        if progress_cb:
            try:
                progress_cb(int(pct), label)
            except Exception:  # noqa: BLE001
                pass

    try:
        ms_instrument = _resolve_instrument(ms_instrument, data_folder, result_folder)
        _p(5, "準備中…")

        if not data_folder:
            data_folder = _infer_data_folder(
                result_folder, project_id, sub_project_id, ms_instrument)
        if not data_folder:
            return None, None, (
                "❌ MSIデータフォルダが見つかりません。"
                "サブプロジェクト設定でMSIデータフォルダを指定してください。")

        rmap = rds_map if isinstance(rds_map, dict) else {}
        if not rmap:
            return None, None, "❌ 解析済み RDS が見つかりません。"

        # selected_methods を rds_map 内に正規化（空/不一致なら全手法）。
        # これにより _build_all_method_lookups の rmap が空にならず、session を参照する
        # フォールバック経路（rds_map 無し時のみ）に落ちない。
        if selected_methods:
            sel = [m for m in selected_methods if m in rmap]
        else:
            sel = list(rmap.keys())
        if not sel:
            sel = list(rmap.keys())

        # 全手法のクラスタルックアップをディスクから構築（current_method=None → session 非参照）
        method_lookups = _build_all_method_lookups(
            rmap, None, None, sel, progress_cb=progress_cb, base=10, span=40)
        if not method_lookups:
            return None, None, "クラスターデータを構築できませんでした。"

        # ROI(領域名) ルックアップ（primary RDS の plot_data + H&E オーバーレイ保存状態）
        _p(52, "ROI(領域名)を割当中…")
        region_lookup = {}
        primary_rds = _pick_primary_rds(rmap)
        if primary_rds:
            try:
                pdat = _bridge.extract_data(primary_rds).get("plot_data")
                region_lookup = _build_region_lookup(pdat, primary_rds)
            except Exception as e:  # noqa: BLE001
                logger.warning("[APIExport] ROI 割当をスキップ: %s", e)

        is_desi = (ms_instrument or "").upper() == "DESI"
        if is_desi:
            file_bytes, filename = _export_desi(
                data_folder, method_lookups, region_lookup,
                progress_cb=progress_cb, base=58, span=40)
        else:
            fmt = export_format or "parquet"
            file_bytes, filename = _export_tims(
                data_folder, method_lookups, fmt, region_lookup,
                progress_cb=progress_cb, base=58, span=40)
        _p(99, "仕上げ中…")

        msg = f"✅ {filename} を生成しました"
        if len(method_lookups) > 1:
            msg += " (" + " / ".join(method_lookups.keys()) + ")"
        return file_bytes, filename, msg

    except Exception as e:  # noqa: BLE001
        logger.exception("[APIExport] エクスポート生成エラー")
        return None, None, f"❌ エラー: {e}"


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
    """作業スレッド本体: _do_export を実行し、出力を一時ファイルへ保存して進捗を反映する。

    base64 でブラウザに載せる（＝タブ落ちの原因）代わりに、生成バイト列を
    DATA_EXPORT_TMP_DIR に保存し、Flask の send_file ルートでストリーム配信する。
    """
    try:
        file_bytes, filename, msg = _do_export(
            *args, progress_cb=lambda p, l="": _update_job(job_id, p, l))
        if not file_bytes or not filename:
            _fail_job(job_id, msg or "出力に失敗しました")
            return
        from app.config import DATA_EXPORT_TMP_DIR
        DATA_EXPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        _sweep_old_files(DATA_EXPORT_TMP_DIR, max_age_sec=3600)  # 古い一時ファイルを掃除
        safe = re.sub(r'[\\/:*?"<>|]+', "_", str(filename)) or "export.bin"
        path = DATA_EXPORT_TMP_DIR / f"{job_id}__{safe}"
        path.write_bytes(file_bytes)
        _finish_job(job_id, str(path), filename, msg)
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
    [Output("data_export_download_url", "data"),
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
    """Interval ごとにジョブ進捗を読み、バー/ラベル更新。完了で DL URL を配信して停止する。

    完了時はブラウザに base64 を載せず、`/api/data_export/<job_id>` を配信して
    clientside で自動DL＋ステータスに明示リンクを出す（send_file ストリーム）。
    ジョブは pop しない（ルートがファイル解決に使うため。掃除は TTL / 上限で行う）。
    """
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
        url = f"/api/data_export/{job_id}"
        link = html.A("⬇ ダウンロード", href=url,
                      className="fw-bold text-decoration-none")
        status_children = html.Span([f"{job['msg']} → ", link])
        # url を store に出して clientside が自動DL。リンクはフォールバック。
        return (url, status_children, _PROG_HIDE, "完了", 100,
                False, False, True)
    # error
    _pop_job(job_id)
    return ("", job["msg"], _PROG_HIDE, "失敗", no_update,
            False, False, True)


# 完了時、DL URL が入ったら clientside で自動ダウンロード（attachment のため画面遷移しない）。
clientside_callback(
    """
    function(url) {
        if (url) { window.location.href = url; }
        return '';
    }
    """,
    Output("data_export_download_sink", "data"),
    Input("data_export_download_url", "data"),
    prevent_initial_call=True,
)


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
