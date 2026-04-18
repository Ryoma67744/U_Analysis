# =============================================================================
# MSI Analysis Application - Interactive m/z Calibration & Re-annotation
# インタラクティブ m/z キャリブレーション・再アノテーション
#
# interactive_callbacks.py から分離されたキャリブレーション関連の
# ヘルパー関数・コールバックをまとめたモジュール。
# =============================================================================

import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd
import dash_bootstrap_components as dbc
from dash import (Input, Output, State, callback, no_update)
from dash.exceptions import PreventUpdate

from app.config import DEFAULT_ADDUCT_POSITIVE
from app.utils.deg_utils import (
    is_meaningful_annotation as _is_meaningful_annotation,
    extract_mz_numeric as _extract_mz_numeric,
)

# 共有状態・ヘルパーは interactive_callbacks に定義されているが、
# 循環importを避けるため各コールバック関数内で遅延importする。
# ヘルパー関数（_build_annotation_csv_map 等）は循環に関わらないためそのまま定義。

logger = logging.getLogger("msi.interactive.calibration")


# ---------------------------------------------------------------------------
# アノテーション CSV / MRM マッピング ヘルパー
# ---------------------------------------------------------------------------

def _build_annotation_csv_map(csv_path, ion_mode="Positive", adduct_patterns=None, tolerance=0.01):
    """アノテーションCSV（TraceFinder/HMDB）→ {m/z: compound_name} マッピング。

    1行目でフォーマットを自動判定し、イオンモード・付加イオンでフィルタして返す。
    Returns:
        dict[float, str] — m/z数値 → 化合物名
    """
    if not csv_path or not Path(csv_path).exists():
        return {}

    try:
        with open(csv_path, encoding="utf-8", errors="replace") as f:
            first_line = f.readline()
    except Exception:
        return {}

    pol_need = "+" if ion_mode == "Positive" else "-"

    # --- TraceFinder format ---
    if "TraceFinder" in first_line:
        try:
            df = pd.read_csv(csv_path, skiprows=2, encoding="utf-8", on_bad_lines="skip")
        except Exception:
            return {}
        if "CompoundName" not in df.columns:
            return {}
        # ExtractedMass / Adduct / Polarity の繰返しブロックを検出
        # pandas は重複カラム名に .1, .2, .3 サフィックスを付与するため対応
        import re as _re2
        mass_cols = [i for i, c in enumerate(df.columns)
                     if c == "ExtractedMass" or _re2.match(r"ExtractedMass\.\d+$", c)]
        add_cols  = [i for i, c in enumerate(df.columns)
                     if c == "Adduct" or _re2.match(r"Adduct\.\d+$", c)]
        pol_cols  = [i for i, c in enumerate(df.columns)
                     if c == "Polarity" or _re2.match(r"Polarity\.\d+$", c)]
        if not mass_cols or not add_cols or not pol_cols:
            return {}
        n_blocks = min(len(mass_cols), len(add_cols), len(pol_cols))
        rows = []
        for k in range(n_blocks):
            for _, row in df.iterrows():
                name = str(row.iloc[0]).strip()  # CompoundName は常に0列目
                try:
                    mz = float(row.iloc[mass_cols[k]])
                except (ValueError, TypeError):
                    continue
                adduct = str(row.iloc[add_cols[k]]).strip()
                pol_raw = str(row.iloc[pol_cols[k]]).strip().upper()
                pol = "+" if pol_raw in ("+", "POS", "P", "POSITIVE") else (
                      "-" if pol_raw in ("-", "NEG", "N", "NEGATIVE") else "")
                if pol == pol_need and name and mz > 0:
                    rows.append((mz, name, adduct))
        # フィルタ: adduct_patterns
        if adduct_patterns:
            filtered = [(mz, nm, ad) for mz, nm, ad in rows
                        if any(p in ad for p in adduct_patterns)]
        else:
            filtered = rows
        return {mz: f"{nm} {ad}" for mz, nm, ad in filtered}

    # --- HMDB format ---
    adduct_col_map = {
        "[M+H]+":  ("M+H",  "+"),
        "[M+Na]+": ("M+Na", "+"),
        "[M+K]+":  ("M+K",  "+"),
        "[M+NH4]+":("M+NH4","+"),
        "[M]+":    ("M+",   "+"),
        "[M-H]-":  ("M-H",  "-"),
        "[M]-":    ("M-",   "-"),
    }
    try:
        df = pd.read_csv(csv_path, encoding="utf-8", on_bad_lines="skip")
    except Exception:
        return {}
    # 化合物名カラム検出
    name_col = None
    for c in ("name (accession)", "name", "Name", "CompoundName"):
        if c in df.columns:
            name_col = c
            break
    if name_col is None:
        return {}

    import re as _re
    result = {}
    for col_name, (adduct_str, pol) in adduct_col_map.items():
        if col_name not in df.columns or pol != pol_need:
            continue
        # adduct_patterns フィルタ
        if adduct_patterns:
            if not any(p in adduct_str for p in adduct_patterns):
                continue
        for _, row in df.iterrows():
            try:
                mz = float(row[col_name])
            except (ValueError, TypeError):
                continue
            if mz <= 0 or pd.isna(mz):
                continue
            name = str(row[name_col]).strip()
            # HMDB ID 除去
            name = _re.sub(r"\s*\(HMDB\d+\)\s*$", "", name)
            if name:
                result[mz] = f"{name} {adduct_str}"
    return result


def _build_mz_to_compound_map(mrm_path_str, tolerance=0.1):
    """MRMファイルから m/z値 → 化合物名 のマッピング辞書を構築する。

    Parent m/z と Daughter m/z の両方を対象にマッチングを行う。
    Returns:
        dict[float, str] — m/z数値 → 化合物名
    """
    if not mrm_path_str:
        return {}
    from app.services.data_manager import load_mrm_file
    mrm_df = load_mrm_file(mrm_path_str)
    if mrm_df is None or mrm_df.empty:
        return {}

    # カラム名の正規化（R側と同様のロジック）
    col_map = {}
    for col in mrm_df.columns:
        cl = col.lower().replace(" ", ".").replace("_", ".")
        if cl in ("compound", "name", "metabolite", "metabolite.name",
                  "analyte", "analyte.name"):
            col_map[col] = "Compound"
        elif cl in ("parent.m.z", "parent.mz", "parent", "precursor",
                    "q1", "q1.m.z", "precursor.m.z", "precursor.mz"):
            col_map[col] = "Parent_mz"
        elif cl in ("daughter.m.z", "daughter.mz", "daughter", "product",
                    "q3", "q3.m.z", "product.m.z", "product.mz"):
            col_map[col] = "Daughter_mz"
    mrm_df = mrm_df.rename(columns=col_map)

    if "Compound" not in mrm_df.columns:
        return {}

    # m/z → 化合物名 マッピング (Parent と Daughter 両方)
    mz_map = {}
    for _, row in mrm_df.iterrows():
        name = str(row.get("Compound", "")).strip()
        if not name:
            continue
        for mz_col in ("Parent_mz", "Daughter_mz"):
            if mz_col in mrm_df.columns:
                try:
                    mz_val = float(row[mz_col])
                    mz_map[mz_val] = name
                except (ValueError, TypeError):
                    continue
    return mz_map


def _annotate_gene_labels(gene_list, mz_to_compound, tolerance=0.1):
    """遺伝子/m/zラベルリストに化合物名を付与して返す。

    Returns:
        list[str] — アノテーション済みラベル（例: "mz_123.456 (Compound A)"）
    """
    if not mz_to_compound:
        return gene_list

    mrm_mz_values = np.array(sorted(mz_to_compound.keys()))
    annotated = []
    for g in gene_list:
        label = g
        # m/z数値を抽出（"mz_123.456" や "123.456" 形式に対応）
        match = re.search(r"(\d+\.\d+)", str(g))
        if match and len(mrm_mz_values) > 0:
            mz_val = float(match.group(1))
            # 最近傍マッチ
            idx = np.argmin(np.abs(mrm_mz_values - mz_val))
            if abs(mrm_mz_values[idx] - mz_val) <= tolerance:
                compound = mz_to_compound[mrm_mz_values[idx]]
                label = f"{g} ({compound})"
        annotated.append(label)
    return annotated


# ---------------------------------------------------------------------------
# Feature → 化合物名 マッピング構築
# ---------------------------------------------------------------------------

def _build_feature_annotation_map(
    features_list: list,
    annotation_csv_path: str = "",
    ion_mode: str = "Positive",
    adduct_patterns: list | None = None,
    tolerance: float = 0.01,
    deg_data: list | None = None,
) -> dict:
    """Feature文字列 → 化合物名 のマッピングを構築。

    アノテーションCSVとDEGデータの両方からマッピングを構築し統合する。
    DEGデータのアノテーションが優先される。

    Returns:
        dict[str, str] — feature文字列 → 化合物名
    """
    result = {}

    # 1) アノテーションCSVからの m/z(float)→化合物名 マッピング
    csv_map = _build_annotation_csv_map(
        annotation_csv_path,
        ion_mode=ion_mode,
        adduct_patterns=adduct_patterns,
        tolerance=tolerance,
    )
    if csv_map:
        csv_mz_values = np.array(sorted(csv_map.keys()))
        for f in features_list:
            mz_val = _extract_mz_numeric(f)
            if mz_val == float("inf"):
                continue
            idx = np.argmin(np.abs(csv_mz_values - mz_val))
            if abs(csv_mz_values[idx] - mz_val) <= tolerance:
                compound = csv_map[csv_mz_values[idx]]
                if _is_meaningful_annotation(compound, f):
                    result[f] = compound

    # 2) DEGデータからのアノテーション（優先的に上書き）
    if deg_data:
        for r in deg_data:
            gene = r.get("gene", "")
            ann = r.get("annotation", "")
            if gene and _is_meaningful_annotation(ann, gene):
                result[gene] = ann

    return result


# ---------------------------------------------------------------------------
# m/z キャリブレーション
# ---------------------------------------------------------------------------

def _calibrate_mz(features_list, expression_df, reference_mz,
                  search_window=0.5, min_peaks=2, regression_mode="linear"):
    """全ピクセル平均スペクトルから参照ピークのppmずれを計算し、回帰で補正値を返す。

    Args:
        features_list: Feature名リスト ("m/z 123.45678" 形式)
        expression_df: DataFrame (cells×features, 列名=feature名)
        reference_mz: list[float] — 参照m/z理論値
        search_window: float — 検索ウィンドウ(Da)
        min_peaks: int — 最低マッチ数
        regression_mode: str — "linear", "poly2", "poly3"

    Returns:
        dict: calibrated, corrected_mz_map, report, regression_mode, r_squared, ...
    """
    from scipy.stats import linregress

    # Feature名 → m/z数値マッピング
    mz_values = {}
    for f in features_list:
        mz = _extract_mz_numeric(f)
        if mz != float("inf"):
            mz_values[f] = mz

    if not mz_values:
        return {"calibrated": False, "corrected_mz_map": {}, "report": []}

    mz_array = np.array(list(mz_values.values()))
    feature_names = list(mz_values.keys())

    # 平均スペクトル算出
    avg_spectrum = {}
    for f in feature_names:
        if f in expression_df.columns:
            avg_spectrum[f] = expression_df[f].mean()
        else:
            avg_spectrum[f] = 0.0

    # 参照ピークとのマッチング
    matched = []
    for ref in reference_mz:
        within_window = np.where(np.abs(mz_array - ref) <= search_window)[0]
        if len(within_window) == 0:
            continue
        # ウィンドウ内で最大強度のピークを選択
        best_idx = None
        best_intensity = -1
        for idx in within_window:
            fname = feature_names[idx]
            intensity = avg_spectrum.get(fname, 0.0)
            if intensity > best_intensity:
                best_intensity = intensity
                best_idx = idx
        if best_idx is not None:
            obs = mz_array[best_idx]
            ppm = (obs - ref) / ref * 1e6
            matched.append({
                "ref_mz": ref, "obs_mz": float(obs),
                "ppm_drift": float(ppm), "avg_intensity": float(best_intensity),
            })

    if len(matched) < min_peaks:
        return {"calibrated": False, "corrected_mz_map": {}, "report": matched}

    # 回帰: ppm_drift を obs_mz の関数としてフィッティング
    obs_arr = np.array([m["obs_mz"] for m in matched])
    ppm_arr = np.array([m["ppm_drift"] for m in matched])

    if regression_mode in ("poly2", "poly3"):
        degree = 2 if regression_mode == "poly2" else 3
        degree = min(degree, len(obs_arr) - 1)  # 過学習防止
        coeffs = np.polyfit(obs_arr, ppm_arr, degree)
        predicted_ppm = np.polyval(coeffs, mz_array)
        # R² 算出
        fitted_ppm = np.polyval(coeffs, obs_arr)
        ss_res = np.sum((ppm_arr - fitted_ppm) ** 2)
        ss_tot = np.sum((ppm_arr - np.mean(ppm_arr)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    else:
        slope, intercept, r_value, _, _ = linregress(obs_arr, ppm_arr)
        predicted_ppm = slope * mz_array + intercept
        coeffs = None
        r_squared = float(r_value ** 2)

    # 全m/zに補正適用
    corrected_mz = mz_array / (1 + predicted_ppm / 1e6)
    corrected_mz_map = {f: float(c) for f, c in zip(feature_names, corrected_mz)}

    result = {
        "calibrated": True,
        "corrected_mz_map": corrected_mz_map,
        "report": matched,
        "regression_mode": regression_mode,
        "r_squared": r_squared,
    }
    if coeffs is not None:
        result["poly_coeffs"] = [float(c) for c in coeffs]
    else:
        result["slope"] = float(slope)
        result["intercept"] = float(intercept)
    return result


def _calibrate_mz_from_pairs(features_list, matched_pairs,
                             regression_mode="linear"):
    """ユーザーが明示的に対応付けたref/obsペアから直接キャリブレーション回帰を行う。

    Args:
        features_list: Feature名リスト ("m/z 123.45678" 形式)
        matched_pairs: list[dict] — {ref_mz, obs_mz, ppm_drift} のリスト
        regression_mode: str — "linear", "poly2", "poly3"

    Returns:
        dict: _calibrate_mz() と同一形式
    """
    from scipy.stats import linregress

    if len(matched_pairs) < 2:
        return {"calibrated": False, "corrected_mz_map": {}, "report": matched_pairs}

    obs_arr = np.array([p["obs_mz"] for p in matched_pairs])
    ppm_arr = np.array([p["ppm_drift"] for p in matched_pairs])

    if regression_mode in ("poly2", "poly3"):
        degree = 2 if regression_mode == "poly2" else 3
        degree = min(degree, len(obs_arr) - 1)  # 過学習防止
        coeffs = np.polyfit(obs_arr, ppm_arr, degree)
        # R² 算出
        fitted_ppm = np.polyval(coeffs, obs_arr)
        ss_res = np.sum((ppm_arr - fitted_ppm) ** 2)
        ss_tot = np.sum((ppm_arr - np.mean(ppm_arr)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    else:
        slope, intercept, r_value, _, _ = linregress(obs_arr, ppm_arr)
        coeffs = None
        r_squared = float(r_value ** 2)

    # Feature名 → m/z数値マッピング
    mz_values = {}
    for f in features_list:
        mz = _extract_mz_numeric(f)
        if mz != float("inf"):
            mz_values[f] = mz

    if not mz_values:
        return {"calibrated": False, "corrected_mz_map": {}, "report": matched_pairs}

    mz_array = np.array(list(mz_values.values()))
    feature_names = list(mz_values.keys())

    # 全m/zに補正適用
    if coeffs is not None:
        predicted_ppm = np.polyval(coeffs, mz_array)
    else:
        predicted_ppm = slope * mz_array + intercept
    corrected_mz = mz_array / (1 + predicted_ppm / 1e6)
    corrected_mz_map = {f: float(c) for f, c in zip(feature_names, corrected_mz)}

    result = {
        "calibrated": True,
        "corrected_mz_map": corrected_mz_map,
        "report": matched_pairs,
        "regression_mode": regression_mode,
        "r_squared": r_squared,
    }
    if coeffs is not None:
        result["poly_coeffs"] = [float(c) for c in coeffs]
    else:
        result["slope"] = float(slope)
        result["intercept"] = float(intercept)
    return result


def _reannotate_with_calibration(deg_data, corrected_mz_map, mrm_path, tolerance=0.1,
                                  annotation_csv_path=None, ion_mode="Positive",
                                  adduct_patterns=None):
    """補正済みm/zでアノテーションDBと照合し、DEGデータのannotation列を更新する。

    Args:
        deg_data: list[dict] — DEGレコードリスト
        corrected_mz_map: dict[str, float] — feature名 → 補正済みm/z
        mrm_path: str — MRM/TraceFinder DBファイルパス
        tolerance: float — マッチング許容誤差(Da)
        annotation_csv_path: str — アノテーションCSV（TraceFinder/HMDB形式）パス
        ion_mode: str — "Positive" or "Negative"
        adduct_patterns: list[str] — 付加イオンフィルター

    Returns:
        list[dict] — annotation更新済みDEGデータ
    """
    # アノテーションCSV（TraceFinder/HMDB）を優先、MRMをフォールバック
    mz_to_compound = _build_mz_to_compound_map(mrm_path, tolerance=tolerance)
    ann_map = _build_annotation_csv_map(
        annotation_csv_path, ion_mode=ion_mode,
        adduct_patterns=adduct_patterns, tolerance=tolerance,
    )
    mz_to_compound.update(ann_map)  # ann_map を優先
    if not mz_to_compound:
        return deg_data

    mrm_mz_values = np.array(sorted(mz_to_compound.keys()))
    if len(mrm_mz_values) == 0:
        return deg_data

    updated = []
    for row in deg_data:
        row = dict(row)  # コピー
        gene = row.get("gene", "")
        corrected = corrected_mz_map.get(gene)
        if corrected is not None and len(mrm_mz_values) > 0:
            idx = np.argmin(np.abs(mrm_mz_values - corrected))
            if abs(mrm_mz_values[idx] - corrected) <= tolerance:
                compound = mz_to_compound[mrm_mz_values[idx]]
                row["annotation"] = compound
        updated.append(row)
    return updated


# ===========================================================================
# インタラクティブ m/z キャリブレーション UI コールバック (INT-CB1〜CB11)
# ===========================================================================

# INT-CB1: キャリブレーション詳細パネルの表示切替
@callback(
    Output("int_cal_detail_panel", "style"),
    Input("int_cal_enable", "value"),
    prevent_initial_call=True,
)
def toggle_int_cal_panel(enabled):
    if enabled:
        return {"display": "block", "marginTop": "10px",
                "padding": "10px", "background": "#f8f9fa",
                "borderRadius": "5px"}
    return {"display": "none"}


# INT-CB2: マトリックス/イオンモード変更 → 参照m/zテーブル初期化
@callback(
    [Output("int_cal_table_data", "data", allow_duplicate=True),
     Output("int_cal_restore_pending", "data", allow_duplicate=True)],
    [Input("int_cal_matrix", "value"),
     Input("int_cal_ion_mode", "value")],
    State("int_cal_restore_pending", "data"),
    prevent_initial_call=True,
)
def update_int_cal_table_on_matrix(matrix_type, ion_mode, is_restoring):
    # データ読み込み直後の復元フェーズではテーブル上書きをスキップ
    if is_restoring:
        return no_update, False
    from app.config import MATRIX_REFERENCE_MZ
    if not matrix_type or matrix_type == "custom":
        return no_update, False
    polarity = ion_mode or "Positive"
    ref_list = MATRIX_REFERENCE_MZ.get(matrix_type, {}).get(polarity, [])
    if not ref_list:
        return [], False
    return [{"ref_mz": round(r, 4), "formula": "", "obs_mz": "",
             "ppm_drift": "--", "use": "Yes"} for r in ref_list], False


# INT-CB3: Store → DataTable 同期
@callback(
    [Output("int_cal_table", "data"),
     Output("int_cal_table", "selected_rows")],
    Input("int_cal_table_data", "data"),
    prevent_initial_call=True,
)
def sync_int_cal_store_to_table(store_data):
    data = store_data or []
    selected = [i for i, r in enumerate(data) if r.get("use") == "Yes"]
    return data, selected


# INT-CB4: チェックボックス → use フィールド同期
@callback(
    Output("int_cal_table_data", "data", allow_duplicate=True),
    Input("int_cal_table", "selected_rows"),
    State("int_cal_table", "data"),
    prevent_initial_call=True,
)
def sync_int_cal_selection_to_use(selected_rows, table_data):
    if table_data is None:
        return no_update
    selected_set = set(selected_rows or [])
    changed = False
    new_data = []
    for i, row in enumerate(table_data):
        new_use = "Yes" if i in selected_set else "No"
        if row.get("use") != new_use:
            changed = True
        new_data.append({**row, "use": new_use})
    if not changed:
        return no_update
    return new_data


# INT-CB5: 行追加
@callback(
    Output("int_cal_table_data", "data", allow_duplicate=True),
    Input("int_cal_add_row", "n_clicks"),
    State("int_cal_table", "data"),
    prevent_initial_call=True,
)
def add_int_cal_row(n, data):
    if not n:
        return no_update
    data = list(data or [])
    data.append({"ref_mz": "", "formula": "", "obs_mz": "", "ppm_drift": "--", "use": "Yes"})
    return data


# INT-CB6: 選択行削除
@callback(
    Output("int_cal_table_data", "data", allow_duplicate=True),
    Input("int_cal_delete_rows", "n_clicks"),
    [State("int_cal_table", "selected_rows"),
     State("int_cal_table", "data")],
    prevent_initial_call=True,
)
def delete_int_cal_rows(n, selected, data):
    if not n or not selected or not data:
        return no_update
    return [r for i, r in enumerate(data) if i not in set(selected)]


# INT-CB7: ピーク自動検出
@callback(
    [Output("int_cal_table_data", "data", allow_duplicate=True),
     Output("int_cal_status_text", "children")],
    Input("int_cal_auto_detect", "n_clicks"),
    [State("int_cal_table", "data"),
     State("int_cal_search_window", "value"),
     State("seurat_cache_dir_store", "data"),
     State("data_folder", "value"),
     State("analysis_method", "value"),
     State("analysis_method_tims", "value")],
    prevent_initial_call=True,
)
def auto_detect_int_cal_peaks(n, table_data, search_window, cache_dir,
                              data_folder, analysis_method, analysis_method_tims):
    if not n or not table_data:
        return no_update, no_update

    # --- データ読み込み: 優先順位 1) cache_dir  2) data_folder 生データ ---
    from app.services.data_manager import read_raw_mz_spectrum

    expr_df = None
    if cache_dir:
        expr_path = Path(cache_dir) / "expression_matrix.parquet"
        if expr_path.exists():
            try:
                expr_df = pd.read_parquet(expr_path)
            except Exception:
                expr_df = None

    if expr_df is None:
        is_tims = bool(analysis_method_tims)
        expr_df = read_raw_mz_spectrum(data_folder, is_tims=is_tims)

    if expr_df is None:
        return no_update, "データが見つかりません。データフォルダを確認してください。"

    mz_values = {}
    for col in expr_df.columns:
        match = re.search(r"(\d+\.?\d*)", col)
        if match:
            mz_values[col] = float(match.group(1))
    if not mz_values:
        return no_update, "m/z値を含むフィーチャーが見つかりません。"

    mz_array = np.array(list(mz_values.values()))
    feature_names = list(mz_values.keys())
    avg_spectrum = {f: float(expr_df[f].mean()) for f in feature_names}

    sw = float(search_window or 0.5)
    matched_count = 0
    updated_data = []
    for row in table_data:
        row = dict(row)
        ref = row.get("ref_mz")
        if not ref or ref == "":
            updated_data.append(row)
            continue
        try:
            ref_f = float(ref)
        except (ValueError, TypeError):
            updated_data.append(row)
            continue
        within = np.where(np.abs(mz_array - ref_f) <= sw)[0]
        if len(within) == 0:
            row["obs_mz"] = ""
            row["ppm_drift"] = "--"
            updated_data.append(row)
            continue
        best_idx = max(within, key=lambda i: avg_spectrum.get(feature_names[i], 0))
        obs = float(mz_array[best_idx])
        ppm = (obs - ref_f) / ref_f * 1e6
        row["obs_mz"] = round(obs, 5)
        row["ppm_drift"] = f"{ppm:+.1f}"
        matched_count += 1
        updated_data.append(row)

    status = f"検出完了: {matched_count}/{len(table_data)} ピークがマッチしました"
    return updated_data, status


# INT-CB8: テーブル編集時にΔppm再計算
@callback(
    Output("int_cal_table_data", "data", allow_duplicate=True),
    Input("int_cal_table", "data_timestamp"),
    State("int_cal_table", "data"),
    prevent_initial_call=True,
)
def recalculate_int_cal_ppm(ts, table_data):
    if not table_data:
        return no_update
    changed = False
    updated = []
    for row in table_data:
        row = dict(row)
        ref = row.get("ref_mz")
        obs = row.get("obs_mz")
        if ref and obs and str(ref).strip() and str(obs).strip():
            try:
                ref_f = float(ref)
                obs_f = float(obs)
                ppm = (obs_f - ref_f) / ref_f * 1e6
                new_drift = f"{ppm:+.1f}"
                if row.get("ppm_drift") != new_drift:
                    changed = True
                row["ppm_drift"] = new_drift
            except (ValueError, TypeError):
                pass
        else:
            if row.get("ppm_drift") != "--":
                changed = True
            row["ppm_drift"] = "--"
        updated.append(row)
    if not changed:
        return no_update
    return updated


# INT-CB9: 自動保存
@callback(
    Output("int_cal_save_trigger", "data"),
    [Input("int_cal_enable", "value"),
     Input("int_cal_ion_mode", "value"),
     Input("int_cal_adduct_filter", "value"),
     Input("int_cal_matrix", "value"),
     Input("int_cal_table_data", "data"),
     Input("int_cal_search_window", "value"),
     Input("int_cal_min_peaks", "value"),
     Input("int_cal_regression_mode", "value"),
     Input("int_cal_annotation_path", "value")],
    prevent_initial_call=True,
)
def auto_save_int_cal(enable, ion_mode, adduct_filter, matrix, table_data,
                      search_window, min_peaks, regression_mode,
                      mrm_path):
    from app.callbacks.interactive_callbacks import _save_interactive_settings
    _save_interactive_settings("int_calibration", {
        "enable": enable or False,
        "ion_mode": ion_mode or "Positive",
        "adduct_filter": adduct_filter or DEFAULT_ADDUCT_POSITIVE,
        "matrix": matrix or "DHB",
        "table_data": table_data or [],
        "search_window": search_window or 0.5,
        "min_peaks": min_peaks or 2,
        "regression_mode": regression_mode or "poly3",
        "mrm_path": mrm_path or "",
    })
    return None


# INT-CB10: List保存ボタン
@callback(
    Output("int_cal_status_text", "children", allow_duplicate=True),
    Input("int_cal_save_list", "n_clicks"),
    [State("int_cal_enable", "value"),
     State("int_cal_ion_mode", "value"),
     State("int_cal_adduct_filter", "value"),
     State("int_cal_matrix", "value"),
     State("int_cal_table_data", "data"),
     State("int_cal_search_window", "value"),
     State("int_cal_min_peaks", "value"),
     State("int_cal_regression_mode", "value"),
     State("int_cal_annotation_path", "value")],
    prevent_initial_call=True,
)
def save_int_cal_list(n, enable, ion_mode, adduct_filter, matrix, table_data,
                      search_window, min_peaks, regression_mode,
                      mrm_path):
    if not n:
        return no_update
    from app.callbacks.interactive_callbacks import _save_interactive_settings
    _save_interactive_settings("int_calibration", {
        "enable": enable or False,
        "ion_mode": ion_mode or "Positive",
        "adduct_filter": adduct_filter or DEFAULT_ADDUCT_POSITIVE,
        "matrix": matrix or "DHB",
        "table_data": table_data or [],
        "search_window": search_window or 0.5,
        "min_peaks": min_peaks or 2,
        "regression_mode": regression_mode or "poly3",
        "mrm_path": mrm_path or "",
    })
    return "設定を保存しました"


# INT-CB-ADDUCT: イオンモード変更時に付加イオン自動切替
@callback(
    Output("int_cal_adduct_filter", "value", allow_duplicate=True),
    Input("int_cal_ion_mode", "value"),
    prevent_initial_call=True,
)
def auto_switch_int_cal_adduct(ion_mode):
    from app.config import DEFAULT_ADDUCT_POSITIVE, DEFAULT_ADDUCT_NEGATIVE
    if ion_mode == "Positive":
        return DEFAULT_ADDUCT_POSITIVE
    return DEFAULT_ADDUCT_NEGATIVE


# INT-CB-MRM: アノテーションセクション表示制御（DESIのみ表示）
@callback(
    Output("int_cal_annotation_section", "style"),
    Input("int_cal_ms_instrument", "data"),
    prevent_initial_call=True,
)
def toggle_int_cal_annotation_section(ms_instrument):
    if ms_instrument and str(ms_instrument).upper() == "DESI":
        return {}
    return {"display": "none"}


# INT-CB11: キャリブレーション適用
@callback(
    [Output("deg_data_store", "data", allow_duplicate=True),
     Output("int_cal_apply_status", "children")],
    Input("int_cal_apply", "n_clicks"),
    [State("int_cal_enable", "value"),
     State("int_cal_table_data", "data"),
     State("int_cal_search_window", "value"),
     State("int_cal_min_peaks", "value"),
     State("int_cal_regression_mode", "value"),
     State("int_cal_annotation_path", "value"),
     State("tolerance_mz", "value"),
     State("interactive_result_folder", "value"),
     State("interactive_integration_method", "value"),
     State("interactive_rds_map", "data"),
     State("default_annotation_csv", "value"),
     State("int_cal_ion_mode", "value"),
     State("int_cal_adduct_filter", "value")],
    prevent_initial_call=True,
)
def apply_int_calibration(n_clicks, cal_enable, cal_table_data,
                          cal_search_window, cal_min_peaks,
                          cal_regression_mode, mrm_path_str,
                          tolerance_mz, result_folder,
                          integration_method, rds_map,
                          annotation_csv, ion_mode, adduct_filter):
    if not n_clicks:
        raise PreventUpdate

    from app.callbacks.interactive_callbacks import (
        _interactive_data, _save_interactive_settings, _load_deg_results,
    )

    # DEGデータをディスクから再読み込み
    deg_data = None
    if result_folder:
        result_base = Path(result_folder)
        deg_data = _load_deg_results(result_base, integration_method or "")
    elif _interactive_data.get("rds_path"):
        rds_dir = Path(_interactive_data["rds_path"]).parent
        result_base = rds_dir.parent if rds_dir.name == "RDS_Files" else rds_dir
        deg_data = _load_deg_results(result_base, integration_method or "")

    if not deg_data:
        return no_update, "DEGデータが見つかりません。データを先に読み込んでください。"

    if not cal_enable:
        # キャリブレーション無効 → 元のDEGデータをそのまま返す
        return deg_data, "キャリブレーションは無効です。有効にしてから適用してください。"

    if not cal_table_data:
        return no_update, "キャリブレーションテーブルにデータがありません。"

    # テーブルから use="Yes" のペアを抽出
    matched_pairs = []
    ref_only_mz = []
    for row in cal_table_data:
        if row.get("use") != "Yes":
            continue
        ref = row.get("ref_mz")
        obs = row.get("obs_mz")
        if ref and obs and str(ref).strip() and str(obs).strip():
            try:
                ref_f = float(ref)
                obs_f = float(obs)
                ppm = (obs_f - ref_f) / ref_f * 1e6
                matched_pairs.append({
                    "ref_mz": ref_f, "obs_mz": obs_f, "ppm_drift": ppm,
                })
            except (ValueError, TypeError):
                pass
        elif ref and str(ref).strip():
            try:
                ref_only_mz.append(float(ref))
            except (ValueError, TypeError):
                pass

    features_list = _interactive_data.get("features_list", [])
    if not features_list:
        return no_update, "フィーチャーリストが読み込まれていません。"

    mp = int(cal_min_peaks or 2)
    reg_mode = cal_regression_mode or "poly3"
    cal_result = None

    try:
        if len(matched_pairs) >= mp:
            cal_result = _calibrate_mz_from_pairs(
                features_list, matched_pairs, regression_mode=reg_mode,
            )
        elif ref_only_mz:
            cache_dir = _interactive_data.get("cache_dir")
            expr_path = (Path(cache_dir) / "expression_matrix.parquet"
                         if cache_dir else None)
            if expr_path and expr_path.exists():
                expr_df = pd.read_parquet(expr_path)
                sw = float(cal_search_window or 0.5)
                cal_result = _calibrate_mz(
                    features_list, expr_df, ref_only_mz,
                    search_window=sw, min_peaks=mp,
                    regression_mode=reg_mode,
                )
            else:
                return no_update, "expression_matrix.parquet が見つかりません。ピーク自動検出を先に実行してください。"
        else:
            return no_update, "有効なリファレンス/実測値ペアがありません。テーブルを確認してください。"

        if cal_result and cal_result.get("calibrated"):
            tol = float(tolerance_mz or 0.1)
            deg_data = _reannotate_with_calibration(
                deg_data, cal_result["corrected_mz_map"],
                mrm_path_str, tolerance=tol,
                annotation_csv_path=annotation_csv,
                ion_mode=ion_mode or "Positive",
                adduct_patterns=adduct_filter,
            )
            _interactive_data["_calibration_result"] = cal_result
            r2 = cal_result.get("r_squared", 0)
            mode = cal_result.get("regression_mode", "")
            status = f"キャリブレーション適用完了 (R²={r2:.4f}, {mode})"
            # 設定を永続化
            _save_interactive_settings("int_calibration", {
                "enable": cal_enable,
                "table_data": cal_table_data,
                "search_window": cal_search_window,
                "min_peaks": cal_min_peaks,
                "regression_mode": cal_regression_mode,
                "mrm_path": mrm_path_str or "",
            })
            return deg_data, status
        else:
            return no_update, "キャリブレーションに失敗しました。ピーク数が不足している可能性があります。"

    except Exception as e:
        return no_update, f"キャリブレーションエラー: {e}"


# =========================================================================
# 再アノテーション (Python側 m/z → 化合物名 再照合)
# =========================================================================

@callback(
    Output("reann_adduct_filter", "value"),
    Input("reann_ion_mode", "value"),
    prevent_initial_call=True,
)
def auto_switch_reann_adduct(ion_mode):
    """再アノテーション: イオンモード変更時にadductを自動切替"""
    from app.config import DEFAULT_ADDUCT_POSITIVE, DEFAULT_ADDUCT_NEGATIVE
    if ion_mode == "Positive":
        return DEFAULT_ADDUCT_POSITIVE
    return DEFAULT_ADDUCT_NEGATIVE


@callback(
    [Output("deg_data_store", "data", allow_duplicate=True),
     Output("reann_status_text", "children")],
    Input("reann_execute_btn", "n_clicks"),
    [State("reann_annotation_path", "value"),
     State("reann_ion_mode", "value"),
     State("reann_adduct_filter", "value"),
     State("reann_tolerance", "value"),
     State("interactive_result_folder", "value"),
     State("interactive_integration_method", "value"),
     State("reann_overwrite_csv", "value")],
    prevent_initial_call=True,
)
def execute_reannotation(n_clicks,
                         annotation_path, ion_mode, adduct_filter,
                         tolerance, result_folder, integration_method,
                         overwrite_csv):
    """再アノテーション実行: 既存DEGデータに対してPython側で化合物名を再マッピングする。"""
    if not n_clicks:
        raise PreventUpdate

    import dash_bootstrap_components as dbc
    from app.callbacks.interactive_callbacks import (
        _interactive_data, _load_deg_results,
    )

    # --- 1. result_base を解決 ---
    result_base = None
    if result_folder:
        result_base = Path(result_folder)
    elif _interactive_data.get("rds_path"):
        rds_dir = Path(_interactive_data["rds_path"]).parent
        result_base = rds_dir.parent if rds_dir.name == "RDS_Files" else rds_dir

    # --- 2. DEGデータをディスクから再読み込み ---
    deg_data = None
    if result_base:
        deg_data = _load_deg_results(result_base, integration_method or "")

    if not deg_data:
        return no_update, dbc.Alert(
            "DEGデータが見つかりません。先にデータを読み込んでください。",
            color="danger", className="small py-1 px-2 mb-0",
        )

    if not annotation_path:
        return no_update, dbc.Alert(
            "アノテーションファイルを指定してください。",
            color="warning", className="small py-1 px-2 mb-0",
        )

    tol = float(tolerance or 0.01)

    # --- 3. ファイル形式に応じてアノテーションマップを構築 ---
    try:
        ann_path = Path(annotation_path)
        is_excel = ann_path.suffix.lower() in (".xlsx", ".xls")

        if is_excel:
            mz_to_compound = _build_mz_to_compound_map(
                annotation_path, tolerance=tol)
        else:
            mz_to_compound = _build_annotation_csv_map(
                annotation_path,
                ion_mode=ion_mode or "Positive",
                adduct_patterns=adduct_filter,
                tolerance=tol,
            )

        if not mz_to_compound:
            return no_update, dbc.Alert(
                "アノテーションファイルから化合物情報を読み取れませんでした。"
                "ファイル形式・イオンモード・付加イオンを確認してください。",
                color="warning", className="small py-1 px-2 mb-0",
            )

        # --- 4. DEGデータに再アノテーション適用 ---
        mrm_mz_values = np.array(sorted(mz_to_compound.keys()))
        updated = []
        update_count = 0
        for row in deg_data:
            row = dict(row)
            gene = row.get("gene", "")
            mz_val = _extract_mz_numeric(gene)
            if mz_val != float("inf") and len(mrm_mz_values) > 0:
                idx = int(np.argmin(np.abs(mrm_mz_values - mz_val)))
                if abs(mrm_mz_values[idx] - mz_val) <= tol:
                    compound = mz_to_compound[mrm_mz_values[idx]]
                    if _is_meaningful_annotation(compound, gene):
                        old_ann = row.get("annotation", "")
                        row["annotation"] = compound
                        if old_ann != compound:
                            update_count += 1
            updated.append(row)

        # --- 5. CSV上書き保存（オプション） ---
        csv_saved_msg = ""
        if overwrite_csv and result_base:
            method_dir = integration_method or "harmony"
            csv_path = result_base / method_dir / "markers_annotated.csv"
            if not csv_path.parent.exists():
                csv_path_alt = result_base / method_dir.lower() / "markers_annotated.csv"
                if csv_path_alt.parent.exists():
                    csv_path = csv_path_alt
            if csv_path.parent.exists():
                try:
                    df_out = pd.DataFrame(updated)
                    df_out.to_csv(csv_path, index=False, encoding="utf-8")
                    csv_saved_msg = f" | CSV保存済み"
                except Exception as e:
                    csv_saved_msg = f" | CSV保存失敗: {e}"
            else:
                csv_saved_msg = " | 出力先フォルダが見つかりません"

        # --- 6. Feature検索用annotation_mapも更新 ---
        features_list = _interactive_data.get("features_list", [])
        if features_list:
            try:
                _interactive_data["annotation_map"] = _build_feature_annotation_map(
                    features_list,
                    annotation_csv_path=annotation_path if not is_excel else None,
                    ion_mode=ion_mode or "Positive",
                    adduct_patterns=adduct_filter,
                    tolerance=tol,
                    deg_data=updated,
                )
            except Exception:
                pass

        total = len(updated)
        status_msg = f"再アノテーション完了: {update_count}/{total} 件更新{csv_saved_msg}"
        return updated, dbc.Alert(
            status_msg, color="success", className="small py-1 px-2 mb-0",
        )

    except Exception as e:
        return no_update, dbc.Alert(
            f"再アノテーションエラー: {e}",
            color="danger", className="small py-1 px-2 mb-0",
        )
