"""データ出力コールバック — 元データに UMAP cluster 列を追加してエクスポート。

DESI: .txt → Excel（サンプル別シート）
TIMS: Parquet/CSV → 選択形式（Excel / CSV / Parquet）

複数手法（Harmony / RPCA 等）が存在する場合は、全手法のクラスター情報を
1つのファイルにまとめて出力する（Method 列 + UMAP cluster 列）。
"""

import io
import logging
import re
from collections import OrderedDict
from pathlib import Path

import pandas as pd
from dash import Input, Output, State, callback, dcc, no_update
from dash.exceptions import PreventUpdate

from app.callbacks.interactive_callbacks import _bridge, _interactive_data
from app.services.data_manager import (
    build_tims_input_paths,
    list_msi_files,
)

logger = logging.getLogger(__name__)
logger.info("[DataExport] モジュール読み込み完了 (v2)")


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


def _build_cluster_lookup(plot_data: pd.DataFrame) -> dict:
    """plot_data 全体から {(sample, round(x,4), round(y,4)): cluster} dict を構築。"""
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
            lookup[key] = str(cluster)
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


def _build_all_method_lookups(
    rds_map: dict | None,
    current_method: str | None,
) -> OrderedDict:
    """全手法のクラスタールックアップを構築。

    Returns:
        OrderedDict {method_name: cluster_lookup_dict}
        現在の手法は ``_interactive_data["plot_data"]`` を再利用し、
        それ以外は ``_bridge.extract_data()`` で動的ロードする。
    """
    method_lookups: OrderedDict[str, dict] = OrderedDict()

    if not rds_map or not isinstance(rds_map, dict) or len(rds_map) <= 1:
        # rds_map がない or 単一手法 → 現在の plot_data のみ
        plot_data = _interactive_data.get("plot_data")
        if plot_data is not None:
            method_name = _interactive_data.get("method") or "Unknown"
            method_lookups[method_name] = _build_cluster_lookup(plot_data)
        return method_lookups

    # 複数手法: 現在の手法を先頭に配置
    ordered_methods = []
    if current_method and current_method in rds_map:
        ordered_methods.append(current_method)
    for m in rds_map:
        if m not in ordered_methods:
            ordered_methods.append(m)

    for method_name in ordered_methods:
        if method_name == current_method:
            # 現在の手法は再読込不要
            plot_data = _interactive_data.get("plot_data")
            if plot_data is not None:
                method_lookups[method_name] = _build_cluster_lookup(plot_data)
        else:
            rds_path = rds_map[method_name]
            if rds_path and Path(rds_path).exists():
                try:
                    result = _bridge.extract_data(rds_path)
                    method_lookups[method_name] = _build_cluster_lookup(
                        result["plot_data"]
                    )
                except Exception as e:
                    logger.warning(
                        "[DataExport] %s: データ抽出エラー: %s", method_name, e
                    )

    return method_lookups


# ---------------------------------------------------------------------------
# DESI エクスポート
# ---------------------------------------------------------------------------

def _export_desi(
    data_folder: str, method_lookups: OrderedDict
) -> tuple[bytes, str]:
    """DESI .txt → Excel バイト列（サンプル別シート + 手法別クラスター列）。

    複数手法の場合は手法名を列ヘッダーにして横並びで配置する。
    単一手法の場合は従来通り「UMAP cluster」列1つ。

    Returns (excel_bytes, filename)
    """
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

    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        for stem in file_stems:
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
                else:
                    # 行2〜5: ヘッダー行 → 空セル
                    padded.extend([""] * len(method_names) if is_multi else [""])
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
    data_folder: str, method_lookups: OrderedDict, fmt: str
) -> tuple[bytes, str]:
    """TIMS 入力ファイルに手法別クラスター列を追加してエクスポート。

    複数手法の場合は手法名を列名にして横並びで配置する。
    単一手法の場合は従来通り「UMAP cluster」列1つ。

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

    dfs_out = []
    for fp in input_paths:
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
    loaded_rds,
):
    """データ出力の本体。開いている(読み込み済みの)プロジェクトにスコープを固定して、
    元データに UMAP cluster 列を付与したファイルを生成する。

    Returns: (dcc.send_bytes(...) または no_update, status_message)
    """
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
        # 全手法のクラスタールックアップを構築
        method_lookups = _build_all_method_lookups(rds_map, current_method)
        if not method_lookups:
            return no_update, "クラスターデータを構築できませんでした。"

        is_desi = (ms_instrument or "").upper() == "DESI"

        if is_desi:
            file_bytes, filename = _export_desi(data_folder, method_lookups)
        else:
            fmt = export_format or "xlsx"
            file_bytes, filename = _export_tims(data_folder, method_lookups, fmt)

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
# 進捗表示付き 2 段チェーン（前景）。
#  Stage A: 進捗UI表示 + ボタン無効化 + Stage B をトリガ（ここで一度描画される）
#  Stage B: 出力本体を実行 → ダウンロード返却 + 進捗「完了」→ 非表示
# background=True は使わない（_do_export が _interactive_data のインプロセス状態を
# 参照するため、DiskcacheManager の fork worker では共有されない）。
# ---------------------------------------------------------------------------

_PROG_SHOW = {"display": "block", "marginTop": "8px"}
_PROG_HIDE = {"display": "none"}


@callback(
    [Output("data_export_progress_container", "style"),
     Output("data_export_progress_label", "children"),
     Output("data_export_progress_bar", "value"),
     Output("data_export_progress_bar", "animated"),
     Output("btn_export_data", "disabled"),
     Output("data_export_trigger", "data")],
    Input("btn_export_data", "n_clicks"),
    prevent_initial_call=True,
)
def data_export_stage_a(n_clicks):
    """出力開始: 進捗UIを表示しボタンを無効化、Stage B をトリガする。"""
    if not n_clicks:
        raise PreventUpdate
    return (_PROG_SHOW, "出力中… ファイルを生成しています", 100, True, True, {"n": n_clicks})


@callback(
    [Output("dl_data_export", "data"),
     Output("div_data_export_status", "children"),
     Output("data_export_progress_container", "style", allow_duplicate=True),
     Output("data_export_progress_label", "children", allow_duplicate=True),
     Output("data_export_progress_bar", "value", allow_duplicate=True),
     Output("data_export_progress_bar", "animated", allow_duplicate=True),
     Output("btn_export_data", "disabled", allow_duplicate=True)],
    Input("data_export_trigger", "data"),
    [State("interactive_msi_folder", "value"),
     State("int_cal_ms_instrument", "data"),
     State("data_export_format", "value"),
     State("interactive_rds_map", "data"),
     State("interactive_integration_method", "value"),
     State("interactive_result_folder", "value"),
     State("interactive_project_select", "value"),
     State("interactive_sub_project_select", "value"),
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def data_export_stage_b(trigger, data_folder, ms_instrument, export_format,
                        rds_map, current_method, result_folder,
                        project_id, sub_project_id, loaded_rds):
    """出力本体を実行し、ダウンロードと完了表示を返す。"""
    if not trigger:
        raise PreventUpdate
    download, msg = _do_export(
        data_folder, ms_instrument, export_format, rds_map, current_method,
        result_folder, project_id, sub_project_id, loaded_rds,
    )
    return (download, msg, _PROG_HIDE, "完了", 100, False, False)


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
