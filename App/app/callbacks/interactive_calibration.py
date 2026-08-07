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

    # FUTURE(annot-provenance): 将来「由来表示」を足す場合、ここで CSV/DEG に加えて
    #   外部由来（LC-MS/MS・METASPACE・MS-DIAL）を app/services/annotation_sources で
    #   取り込み・対応づけし、由来付きラベル（例 "ATP (METASPACE, FDR=10%)"）に統合する想定。
    #   取込設計が未確定のため現状は変更なし。詳細: App/docs/MVP4_IMPLEMENTATION_STATUS.md
    return result


# ---------------------------------------------------------------------------
# m/z キャリブレーション
# ---------------------------------------------------------------------------

def _linear_fit(x, y):
    """最小二乗の直線当てはめ。(slope, intercept, r_squared) を返す (ver51.5)。

    ★ `scipy.stats.linregress` の置き換え。**scipy は requirements.txt にも
      Dockerfile にも無く、どの依存からも入らない**ため本番イメージに存在しない。
      linear 回帰を選んだ瞬間 ModuleNotFoundError になり、読み込み経路では
      `interactive_callbacks.py` の `except Exception` に拾われて
      「m/zキャリブレーションに失敗したため未適用」と出るだけだった
      (原因が分からない形で機能が無効化されていた)。

      poly2 / poly3 の分岐は元から `np.polyfit` を使っており、linear だけが
      scipy に依存していた。numpy に揃えれば依存を増やさずに機能が動く。

    単回帰では linregress の r_value**2 と 1 - ss_res/ss_tot が一致するので、
    poly 分岐と同じ式で R² を出す。
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    slope, intercept = np.polyfit(x, y, 1)
    fitted = slope * x + intercept
    ss_res = float(np.sum((y - fitted) ** 2))
    ss_tot = float(np.sum((y - np.mean(y)) ** 2))
    r_squared = (1.0 - ss_res / ss_tot) if ss_tot > 0 else 0.0
    return float(slope), float(intercept), float(r_squared)


def _features_within_windows(feature_names, reference_mz, search_window):
    """参照 m/z の ±search_window 内にある feature 名だけを返す (ver51.5)。

    キャリブレーションが実際に参照するのはこの窓の中だけ。全 feature の平均を
    作るために巨大な行列を読む必要は無い。

    Returns: (窓内の feature 名リスト, {feature名: m/z} 全件)
      2 つ目は呼び出し側が `_calibrate_mz` へ渡す features_list を作るのに使う
      (候補探索には全 feature の m/z が要るが、**強度が要るのは窓内だけ**)。
    """
    mz_values = {}
    for f in feature_names or []:
        mz = _extract_mz_numeric(f)
        if mz != float("inf"):
            mz_values[str(f)] = mz
    if not mz_values:
        return [], {}

    names = list(mz_values.keys())
    arr = np.array([mz_values[n] for n in names], dtype=float)
    order = np.argsort(arr)
    arr_sorted = arr[order]

    try:
        sw = float(search_window)
    except (TypeError, ValueError):
        sw = 0.5

    keep = set()
    for ref in (reference_mz or []):
        try:
            r = float(ref)
        except (TypeError, ValueError):
            continue
        lo = np.searchsorted(arr_sorted, r - sw, side="left")
        hi = np.searchsorted(arr_sorted, r + sw, side="right")
        for i in range(int(lo), int(hi)):
            keep.add(names[int(order[i])])
    return sorted(keep), mz_values


def window_avg_spectrum(expr_path, features_list, reference_mz, search_window):
    """参照窓内の列**だけ**読んで {feature名: 平均強度} を返す (ver51.5)。

    読めなければ None (呼び出し側は従来どおりエラー表示へ倒す)。
    """
    from app.callbacks.interactive_callbacks import _bridge

    wanted, _ = _features_within_windows(features_list, reference_mz, search_window)
    if not wanted:
        return {}
    return _bridge.get_feature_means(Path(expr_path).parent, wanted)


def _calibrate_mz(features_list, avg_spectrum, reference_mz,
                  search_window=0.5, min_peaks=2, regression_mode="linear"):
    """全ピクセル平均スペクトルから参照ピークのppmずれを計算し、回帰で補正値を返す。

    Args:
        features_list: Feature名リスト ("m/z 123.45678" 形式)
        avg_spectrum: {feature名: 平均強度}。ver51.5 で DataFrame から変更した。
            **参照窓の外は入っていなくてよい** (窓内しか読まないため)。
            DataFrame を渡していた頃は、この関数のためだけに 2.32GB を
            materialize していた。
        reference_mz: list[float] — 参照m/z理論値
        search_window: float — 検索ウィンドウ(Da)
        min_peaks: int — 最低マッチ数
        regression_mode: str — "linear", "poly2", "poly3"

    Returns:
        dict: calibrated, corrected_mz_map, report, regression_mode, r_squared, ...
    """

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

    # 平均強度は呼び出し側が窓内の列だけ読んで渡す (ver51.5)。
    avg_spectrum = avg_spectrum or {}

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
        # ★ ver51.8: n-1 は「ちょうど完全内挿できる」次数で自由度ゼロ。
        #   誤差を一切吸収せず、参照範囲の外へ外挿すると発散する
        #   (実測: 4 点 + 3 次で m/z 1000 の補正が -20,807 ppm)。
        #   パラメータ数の 2 倍以上の観測点を要求する。
        #   analysis_runner.compute_calibration_coefficients と同じ規則。
        degree = max(1, min(degree, len(obs_arr) // 2 - 1))  # 過学習防止
        coeffs = np.polyfit(obs_arr, ppm_arr, degree)
        predicted_ppm = np.polyval(coeffs, mz_array)
        # R² 算出
        fitted_ppm = np.polyval(coeffs, obs_arr)
        ss_res = np.sum((ppm_arr - fitted_ppm) ** 2)
        ss_tot = np.sum((ppm_arr - np.mean(ppm_arr)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    else:
        slope, intercept, r_squared = _linear_fit(obs_arr, ppm_arr)
        predicted_ppm = slope * mz_array + intercept
        coeffs = None

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

    if len(matched_pairs) < 2:
        return {"calibrated": False, "corrected_mz_map": {}, "report": matched_pairs}

    obs_arr = np.array([p["obs_mz"] for p in matched_pairs])
    ppm_arr = np.array([p["ppm_drift"] for p in matched_pairs])

    if regression_mode in ("poly2", "poly3"):
        degree = 2 if regression_mode == "poly2" else 3
        # ★ ver51.8: n-1 は「ちょうど完全内挿できる」次数で自由度ゼロ。
        #   誤差を一切吸収せず、参照範囲の外へ外挿すると発散する
        #   (実測: 4 点 + 3 次で m/z 1000 の補正が -20,807 ppm)。
        #   パラメータ数の 2 倍以上の観測点を要求する。
        #   analysis_runner.compute_calibration_coefficients と同じ規則。
        degree = max(1, min(degree, len(obs_arr) // 2 - 1))  # 過学習防止
        coeffs = np.polyfit(obs_arr, ppm_arr, degree)
        # R² 算出
        fitted_ppm = np.polyval(coeffs, obs_arr)
        ss_res = np.sum((ppm_arr - fitted_ppm) ** 2)
        ss_tot = np.sum((ppm_arr - np.mean(ppm_arr)) ** 2)
        r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    else:
        slope, intercept, r_squared = _linear_fit(obs_arr, ppm_arr)
        coeffs = None

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

    sw = float(search_window or 0.5)
    refs = []
    for row in table_data:
        try:
            v = row.get("ref_mz")
            if v not in (None, ""):
                refs.append(float(v))
        except (TypeError, ValueError):
            continue

    mz_values = {}
    avg_spectrum = None

    # ver51.5: parquet 経路は **参照窓内の列だけ** 読む。従来は列指定なしで
    # 全列を materialize していた (実データで 1 回 2.32GB)。使うのは窓内の
    # 最大強度ピークだけなので、窓の外は読む必要が無い。
    if cache_dir:
        expr_path = Path(cache_dir) / "expression_matrix.parquet"
        if expr_path.exists():
            from app.callbacks.interactive_callbacks import _bridge
            names = _bridge._parquet_column_names(expr_path)
            if names:
                wanted, mz_values = _features_within_windows(names, refs, sw)
                avg_spectrum = _bridge.get_feature_means(cache_dir, wanted)

    if avg_spectrum is None:
        # 生データ側は 1 行 (平均スペクトル) なので、そのまま全部読む。
        #
        # ★ ver51.8 の既知の限界: このコールバックには**サンプル選択の入力が無い**。
        #   そのため sample_name を渡せず、フォルダ内の先頭ファイルの平均スペクトルで
        #   参照ピークを探すことになる。複数サンプルのプロジェクトでは、
        #   「どのサンプルから求めた補正か」が利用者に見えない。
        #   設定画面側 (analysis_callbacks.auto_detect_observed_peaks) には
        #   cal_sample_selector があり、そちらは ver51.8 で厳密一致にした。
        #   ここに選択 UI を足すのは別途 (挙動変更を伴うため)。ログには残す。
        is_tims = bool(analysis_method_tims)
        expr_df = read_raw_mz_spectrum(data_folder, is_tims=is_tims)
        if expr_df is None:
            return no_update, "データが見つかりません。データフォルダを確認してください。"
        logger.info(
            "対話キャリブレーション: サンプル未指定のため %s 内の先頭ファイルの"
            "平均スペクトルで参照ピークを探索します", data_folder)
        # ver51.8: 独自の正規表現をやめ共通窓口へ統一（4 つ目の重複だった）。
        mz_values = {}
        for col in expr_df.columns:
            mz = _extract_mz_numeric(str(col))
            if mz != float("inf"):
                mz_values[col] = mz
        avg_spectrum = {f: float(expr_df[f].mean()) for f in mz_values}

    if not mz_values:
        return no_update, "m/z値を含むフィーチャーが見つかりません。"

    mz_array = np.array(list(mz_values.values()))
    feature_names = list(mz_values.keys())
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
    # ★ ver51.8: 保存先を決めるため rds_path を受け取る（元は無かった）
    State("seurat_rds_path_store", "data"),
    prevent_initial_call=True,
)
def auto_save_int_cal(enable, ion_mode, adduct_filter, matrix, table_data,
                      search_window, min_peaks, regression_mode,
                      mrm_path, rds_path):
    # ★ ver51.8: active key を立てないと別プロジェクトへ書くか黙って捨てられる
    if not rds_path:
        raise PreventUpdate
    from app.callbacks.interactive_callbacks import (
        _save_interactive_settings, _set_active_key)
    _set_active_key(rds_path)
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
     State("int_cal_annotation_path", "value"),
     # ★ ver51.8: 保存先を決めるため rds_path を受け取る（元は無かった）
     State("seurat_rds_path_store", "data")],
    prevent_initial_call=True,
)
def save_int_cal_list(n, enable, ion_mode, adduct_filter, matrix, table_data,
                      search_window, min_peaks, regression_mode,
                      mrm_path, rds_path):
    if not n:
        return no_update
    if not rds_path:
        raise PreventUpdate
    from app.callbacks.interactive_callbacks import (
        _save_interactive_settings, _set_active_key)
    _set_active_key(rds_path)
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
def _apply_int_calibration_inner(cal_enable, cal_table_data,
                                  cal_search_window, cal_min_peaks,
                                  cal_regression_mode, mrm_path_str,
                                  tolerance_mz, result_folder,
                                  integration_method, rds_map,
                                  annotation_csv, ion_mode, adduct_filter):
    """apply_int_calibration の内部ロジック。FileLock 内で実行する想定。

    本関数を直接 callback として呼ばないこと（active_key は呼出元で設定済みの前提）。
    """
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
            # 必要時に expression_matrix.parquet を on-demand 生成
            rds_path = _interactive_data.get("rds_path")
            cache_dir = _interactive_data.get("cache_dir")
            expr_path = None
            if rds_path:
                try:
                    from app.callbacks.interactive_callbacks import _bridge
                    expr_path = _bridge.ensure_expression_matrix(rds_path)
                except Exception:
                    expr_path = None
            if expr_path is None and cache_dir:
                fallback = Path(cache_dir) / "expression_matrix.parquet"
                if fallback.exists():
                    expr_path = fallback
            if expr_path and expr_path.exists():
                sw = float(cal_search_window or 0.5)
                # ver51.5: 参照窓内の列だけ読む (従来は列指定なしで 2.32GB)
                avg_spectrum = window_avg_spectrum(
                    expr_path, features_list, ref_only_mz, sw)
                if avg_spectrum is None:
                    return no_update, "expression_matrix.parquet を読めませんでした。"
                cal_result = _calibrate_mz(
                    features_list, avg_spectrum, ref_only_mz,
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
    """m/z キャリブレーション適用 callback。

    同一プロジェクトの同時実行は FileLock で順番待ちさせる
    （_calibration_result の上書き競合を防止）。
    """
    if not n_clicks:
        raise PreventUpdate

    # 現在の閲覧プロジェクトを ContextVar にスコープ + FileLock 準備
    lock_ctx = None
    if rds_map and integration_method and integration_method in rds_map:
        from app.callbacks.interactive_callbacks import _set_active_key
        from app.utils.file_locks import get_or_create_lock
        _set_active_key(rds_map[integration_method])
        rds_path_for_lock = Path(rds_map[integration_method])
        lock_ctx = get_or_create_lock(
            rds_path_for_lock.parent / ".calibration.lock", timeout=600
        )
        lock_ctx.acquire()

    try:
        return _apply_int_calibration_inner(
            cal_enable, cal_table_data, cal_search_window, cal_min_peaks,
            cal_regression_mode, mrm_path_str, tolerance_mz, result_folder,
            integration_method, rds_map, annotation_csv, ion_mode, adduct_filter,
        )
    finally:
        if lock_ctx is not None and lock_ctx.is_locked:
            try:
                lock_ctx.release()
            except Exception:
                pass


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
     State("reann_overwrite_csv", "value"),
     State("interactive_rds_map", "data")],
    prevent_initial_call=True,
)
def execute_reannotation(n_clicks,
                         annotation_path, ion_mode, adduct_filter,
                         tolerance, result_folder, integration_method,
                         overwrite_csv, rds_map=None):
    """再アノテーション実行: 既存DEGデータに対してPython側で化合物名を再マッピングする。"""
    if not n_clicks:
        raise PreventUpdate

    # 現在の閲覧プロジェクトを ContextVar にスコープ
    if rds_map and integration_method and integration_method in rds_map:
        from app.callbacks.interactive_callbacks import _set_active_key
        _set_active_key(rds_map[integration_method])

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
                    from app.utils.file_locks import atomic_write_csv
                    df_out = pd.DataFrame(updated)
                    atomic_write_csv(df_out, csv_path, index=False, encoding="utf-8")
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
