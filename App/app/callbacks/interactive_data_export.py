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

    # (b) 結果フォルダの兄弟ディレクトリをスキャン
    if not result_folder:
        return None

    result_path = Path(result_folder)
    if not result_path.is_dir():
        return None

    _skip = {"RDS_Files", "log", "__pycache__", ".git"}

    search_roots = [result_path.parent]
    # 結果フォルダの親が output_dir 的な階層の場合、もう一段上も探索
    if result_path.parent.parent.is_dir():
        search_roots.append(result_path.parent.parent)

    for root in search_roots:
        for child in sorted(root.iterdir()):
            if not child.is_dir():
                continue
            if child == result_path or child.name in _skip:
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

@callback(
    [Output("dl_data_export", "data"),
     Output("div_data_export_status", "children")],
    Input("btn_export_data", "n_clicks"),
    [State("interactive_msi_folder", "value"),
     State("int_cal_ms_instrument", "data"),
     State("data_export_format", "value"),
     State("interactive_rds_map", "data"),
     State("interactive_integration_method", "value"),
     State("interactive_result_folder", "value"),
     State("interactive_project_select", "value"),
     State("interactive_sub_project_select", "value")],
    prevent_initial_call=True,
)
def cb_export_data(
    n_clicks, data_folder, ms_instrument, export_format,
    rds_map, current_method, result_folder, project_id, sub_project_id,
):
    """データ出力ボタンのコールバック。

    ``interactive_rds_map`` に複数手法が登録されている場合は、
    全手法のクラスター情報を1つのファイルにまとめて出力する。
    ``data_folder`` が未設定の場合はサブプロジェクトメタデータや
    結果フォルダ構造から自動推定を試みる。
    """
    if not n_clicks:
        return no_update, no_update
    # 現在の閲覧プロジェクトを ContextVar にスコープ（他ユーザーと干渉しないように）
    if rds_map and current_method and current_method in rds_map:
        from app.callbacks.interactive_callbacks import _set_active_key
        _set_active_key(rds_map[current_method])

    logger.info("[DataExport] cb_export_data 発火: data_folder=%s", data_folder)

    try:
        # MSI データフォルダの自動推定
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
