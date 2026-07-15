"""annotation_label.py - feature(m/z) → 表示ラベル解決の単一窓口。

化合物名は複数系統のソースに分散している:
  1. feature_annotations[feat]["display_name"] / ["compound"] … SCiLS/TIMS サイドカー由来
  2. annotation_map[feat] = compound … プロセス内スーパーセット（#1 or CSV/DEG から構築）
  3. deg_data[...]["annotation"] … R が markers_annotated.csv を出した時のみ存在

各表示コールバックが思い思いに 1 系統だけ参照していたため、化合物名が付いていても
箇所によって m/z のままになっていた。本モジュールは上記を一貫した優先順位で解決し、
各表示面の既存フォーマット(style)を保ったままラベルを返す。

構成:
  - feature_display_label(...)   … Dash 非依存の純関数（テスト容易）
  - label_from_active_state(...) … アクティブ state（_interactive_data）から
                                    annotation_map / feature_annotations を読む薄いラッパ
"""

from __future__ import annotations

import re
from typing import Optional

from app.utils.deg_utils import extract_mz_numeric, is_meaningful_annotation

# ファイル名に使えない/使いたくない文字（Windows/Unix 双方で安全側に）。
# TIMS の display_name / feature 名は `|`・`:`・空白を含むため、PNG/zip 名に生で使うと壊れる。
_FILENAME_UNSAFE = re.compile(r'[|/\\:*?"<>\s]+')


def _resolve_compound(feature_name: str,
                      annotation_map: Optional[dict],
                      feature_annotations: Optional[dict],
                      deg_annotation: Optional[str]) -> str:
    """意味のある化合物名を優先順位で 1 つ返す（無ければ ""）。

    優先: annotation_map(#2 スーパーセット) → feature_annotations.compound(#1) → deg_annotation(#3)。
    各候補は is_meaningful_annotation で「数値のみ / feature と同一」を排除する。
    """
    feat = feature_name
    if annotation_map:
        cand = annotation_map.get(feat)
        if isinstance(cand, str) and is_meaningful_annotation(cand, feat):
            return cand.strip()
    if feature_annotations:
        rec = feature_annotations.get(feat)
        if isinstance(rec, dict):
            cand = rec.get("compound")
            if isinstance(cand, str) and is_meaningful_annotation(cand, feat):
                return cand.strip()
    if isinstance(deg_annotation, str) and is_meaningful_annotation(deg_annotation, feat):
        return deg_annotation.strip()
    return ""


def _display_name(feature_name: str, feature_annotations: Optional[dict]) -> str:
    """feature_annotations の display_name（"化合物名_<m/z>"）を返す（無ければ ""）。"""
    if not feature_annotations:
        return ""
    rec = feature_annotations.get(feature_name)
    if isinstance(rec, dict):
        dn = rec.get("display_name")
        if isinstance(dn, str) and dn.strip():
            return dn.strip()
    return ""


def feature_display_label(feature_name, *,
                          annotation_map: Optional[dict] = None,
                          feature_annotations: Optional[dict] = None,
                          deg_annotation: Optional[str] = None,
                          show_compound: bool = True,
                          style: str = "auto") -> str:
    """feature 文字列を各表示面向けのラベルへ解決する。

    Args:
        feature_name: feature 文字列（多くは "m/z 760.58510" 等の安全キー、または "化合物名_m/z"）。
        annotation_map: {feat: compound}（スーパーセット）。
        feature_annotations: {feat: {"display_name","compound",...}}。
        deg_annotation: DEG レコードの annotation（単一化合物名文字列）。
        show_compound: False なら常に素の feature を返す（トグル OFF に直結）。
        style: "heading" | "paren" | "compound" | "filename" | "auto"。
    """
    feat = "" if feature_name is None else str(feature_name)
    if not show_compound:
        return feat

    compound = _resolve_compound(feat, annotation_map, feature_annotations, deg_annotation)
    display_name = _display_name(feat, feature_annotations)

    if style == "auto":
        style = "heading" if display_name else "paren"

    if style == "heading":
        # 例: "PI 38:4_760.5851"（display_name）、無ければ "760.5851  (Glucose)"、無ければ素の feat
        if display_name:
            return display_name
        if compound:
            return f"{feat}  ({compound})"
        return feat

    if style == "paren":
        # 例: "760.5851 (Glucose)" … ドロップダウン/タイトル向け（既存 _make_option と同形）
        return f"{feat} ({compound})" if compound else feat

    if style == "compound":
        # 表セル/専用列向け … 化合物名のみ（無ければ feat）
        return compound or feat

    if style == "filename":
        # ファイルシステム安全な識別子。display_name(化合物名_m/z) を最優先。
        if display_name:
            base = display_name
        elif compound:
            mz = extract_mz_numeric(feat)
            base = f"{compound}_{mz:.4f}" if mz != float("inf") else f"{compound}_{feat}"
        else:
            base = feat
        cleaned = _FILENAME_UNSAFE.sub("_", base).strip("_")
        return cleaned or _FILENAME_UNSAFE.sub("_", feat).strip("_") or feat

    # 未知 style は素の feat（安全側）
    return feat


def label_from_active_state(feature_name, *,
                            deg_annotation: Optional[str] = None,
                            show_compound: bool = True,
                            style: str = "auto") -> str:
    """アクティブ state から annotation_map / feature_annotations を読んで解決する薄いラッパ。

    循環 import 回避のため _interactive_data は関数内 lazy import（既存コールバックの慣習に合わせる）。
    アクティブキーは呼び出し側が _set_active_key(rds_path) 済みである前提。
    """
    annotation_map = None
    feature_annotations = None
    try:
        from app.callbacks.interactive_callbacks import _interactive_data
        annotation_map = _interactive_data.get("annotation_map")
        feature_annotations = _interactive_data.get("feature_annotations")
    except Exception:
        pass
    return feature_display_label(
        feature_name,
        annotation_map=annotation_map,
        feature_annotations=feature_annotations,
        deg_annotation=deg_annotation,
        show_compound=show_compound,
        style=style,
    )
