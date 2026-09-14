"""解析時の切片IDを維持して群・独立試料情報だけを編集する。"""
from __future__ import annotations
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import tempfile
from filelock import FileLock

METADATA_COLUMNS = ("source_file_id", "source_pixel_id", "section_id", "subject_id", "group", "integration_unit_id")
UNASSIGNED_GROUP = "（群未設定）"


def _result_dir(rds_path):
    from app.services.provenance import results_dir_for_rds
    return results_dir_for_rds(rds_path)


def load_result_manifest(rds_path):
    """群編集sidecarを優先し、解析時の条件記録は保持する。"""
    root = _result_dir(rds_path)
    if root is None:
        return {}
    for name in ("section_manifest.json", "analysis_params.json", "receipt.json"):
        path = root / name
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            obj = json.load(fh)
        candidate = obj if name == "section_manifest.json" else (obj.get("section_manifest") if name == "analysis_params.json" else obj.get("object", {}).get("sample_selection", {}).get("section_manifest"))
        if isinstance(candidate, dict) and candidate.get("files"):
            return candidate
    return {}


def overlay_result_metadata(df, rds_path, manifest=None):
    """★ ver67.0: 群名だけを更新し、強度・座標・クラスタ・Sampleは変えない。"""
    if df is None or "section_id" not in df.columns:
        return df
    manifest = load_result_manifest(rds_path) if manifest is None else manifest
    rows = [s for f in (manifest or {}).get("files", []) for s in f.get("sections", [])]
    if not rows:
        return df
    out = df.copy(deep=False)
    for col in ("subject_id", "group"):
        mapping = {str(s["section_id"]): str(s.get(col) or "") for s in rows}
        mapped = out["section_id"].astype(str).map(mapping)
        out[col] = mapped.where(mapped.notna(), out[col] if col in out.columns else "").fillna("")
    return out


def group_rows(df):
    if df is None or "section_id" not in df.columns:
        return []
    cols = [c for c in ("section_id", "Sample", "subject_id", "group") if c in df.columns]
    result = []
    for record in df[cols].fillna("").astype(str).drop_duplicates("section_id").to_dict("records"):
        record["section"] = record.pop("Sample", record["section_id"])
        record.setdefault("subject_id", "")
        record.setdefault("group", "")
        result.append(record)
    return result


def group_summary(df):
    """画素数をNとせず、切片数と重複除外した独立試料数を返す。"""
    stats = {}
    for row in group_rows(df):
        item = stats.setdefault(row["group"] or UNASSIGNED_GROUP, {"sections": 0, "subjects": set(), "missing_subjects": 0})
        item["sections"] += 1
        subject = row.get("subject_id", "").strip()
        if subject:
            item["subjects"].add(subject)
        else:
            item["missing_subjects"] += 1
    return [{"group": key, "sections": v["sections"], "subjects": len(v["subjects"]), "missing_subjects": v["missing_subjects"]} for key, v in sorted(stats.items())]


def filter_groups(df, groups):
    """None=全群、[]=全解除を区別し、表示データのみに適用する。"""
    if df is None or groups is None or "group" not in df.columns:
        return df
    labels = df["group"].fillna("").astype(str).replace("", UNASSIGNED_GROUP)
    return df.loc[labels.isin(groups)]


def save_group_edits(rds_path, rows):
    root = _result_dir(rds_path)
    if root is None or not root.is_dir():
        raise ValueError("群情報を保存できる解析結果フォルダがありません。")
    target = root / "section_manifest.json"
    with FileLock(str(target) + ".lock", timeout=30):
        manifest = deepcopy(load_result_manifest(rds_path))
        sections = {str(s["section_id"]): s for f in manifest.get("files", []) for s in f.get("sections", [])}
        if not sections:
            raise ValueError("切片IDを持つ解析結果で群情報を編集できます。")
        seen = set()
        for row in rows or []:
            sid = str(row.get("section_id", ""))
            if sid not in sections or sid in seen:
                raise ValueError("切片の対応が変わりました。解析結果を読み込み直してください。")
            seen.add(sid)
            for key in ("subject_id", "group"):
                sections[sid][key] = str(row.get(key) or "").strip()
        from app.services.section_metadata import validate_section_manifest
        errors = validate_section_manifest(manifest)
        if errors:
            raise ValueError(" / ".join(errors))
        fd, tmp = tempfile.mkstemp(dir=root, prefix="section_groups_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(manifest, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return manifest


def export_metadata_frame(df):
    cols = [c for c in ("CellID", "Sample", *METADATA_COLUMNS, "SpatialX", "SpatialY", "UMAP_1", "UMAP_2", "Cluster") if c in df.columns]
    return df.loc[:, cols].copy()


def normalize_pixel_id(value):
    text = str(value).strip()
    # ★ ver67.0: 001と1は文字IDとして区別する。数値1.0と1の表現差だけを正規化する。
    if re.fullmatch(r"[+-]?0[0-9]+(?:\.0+)?", text):
        return text
    try:
        number = Decimal(text)
        return str(number.normalize()) if number.is_finite() else text
    except InvalidOperation:
        return text


def input_source_id(input_path, rds_path):
    if not rds_path:
        return None
    matches = [f for f in load_result_manifest(rds_path).get("files", []) if Path(f["path"]).resolve() == Path(input_path).resolve()]
    return str(matches[0]["file_id"]) if len(matches) == 1 else None


def selected_input_paths(rds_path, suffixes):
    """新形式では追加フォルダを含む選択済み入力を出力する。旧結果はNoneを返す。"""
    manifest = load_result_manifest(rds_path)
    if not manifest.get("files"):
        return None
    paths = [str(f["path"]) for f in manifest["files"] if f.get("selection_mode") != "none" and Path(f["path"]).suffix.lower() in suffixes]
    missing = [p for p in paths if not Path(p).is_file()]
    if missing:
        raise ValueError("解析に選択された入力が見つかりません: " + " / ".join(missing))
    return paths


def attach_input_metadata(df, input_path, rds_path, plot_data):
    """同名ROIを混同しないよう、元ファイルID・元画素IDで照合する。"""
    if plot_data is None or not {"source_file_id", "source_pixel_id"}.issubset(plot_data.columns) or "id" not in df.columns:
        return df
    fid = input_source_id(input_path, rds_path)
    if fid is None:
        return df
    pdat = overlay_result_metadata(plot_data, rds_path)
    selected = pdat.loc[pdat["source_file_id"].astype(str) == fid]
    cols = [c for c in METADATA_COLUMNS if c in selected.columns]
    keys = selected["source_pixel_id"].map(normalize_pixel_id)
    if keys.duplicated().any():
        raise ValueError("元画素IDが重複しているため、メタデータを一意に出力できません。")
    values = selected[cols].copy()
    values.index = keys
    out = df.copy(deep=False)
    for col in cols:
        out[col] = df["id"].map(normalize_pixel_id).map(values[col]).fillna("")
    return out


class SourceClusterLookup(dict):
    """旧Sample+座標辞書と、元ファイル+画素IDの辞書を同時に保持する。"""
    def __init__(self):
        super().__init__()
        self.by_source = {}


def append_source_clusters(df, input_path, rds_path, method_lookups):
    """★ ver67.0: 同名切片のクラスタを座標だけで混ぜない。"""
    fid = input_source_id(input_path, rds_path)
    if fid is None or "id" not in df.columns:
        return df
    keys = [(fid, normalize_pixel_id(x)) for x in df["id"]]
    out = df.copy(deep=False)
    for method, lookup in method_lookups.items():
        source = getattr(lookup, "by_source", None)
        if source:
            out[method if len(method_lookups) > 1 else "UMAP cluster"] = [source.get(key, "") for key in keys]
    return out


def append_source_values(df, input_path, rds_path, lookups, *, default=""):
    """★ ver67.0: 追加座標・品質値・H&E領域も元ファイル/画素IDで対応する。"""
    fid = input_source_id(input_path, rds_path)
    if fid is None or "id" not in df.columns:
        return df
    keys = [(fid, normalize_pixel_id(x)) for x in df["id"]]
    out = df.copy(deep=False)
    for column, lookup in (lookups or {}).items():
        source = getattr(lookup, "by_source", None)
        if source:
            out[column] = [source.get(key, default) for key in keys]
    return out


def source_match_mask(df, input_path, rds_path, method_lookups):
    """新形式の対象画素。Noneは旧結果、全Falseは対象画素なしを区別する。"""
    fid = input_source_id(input_path, rds_path)
    sources = [getattr(lookup, "by_source", None) for lookup in method_lookups.values()]
    sources = [source for source in sources if source]
    if fid is None or "id" not in df.columns or not sources:
        return None
    keys = [(fid, normalize_pixel_id(x)) for x in df["id"]]
    import pandas as pd
    return pd.Series([any(key in source for source in sources) for key in keys], index=df.index)
