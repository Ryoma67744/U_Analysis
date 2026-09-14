"""分子名の優先順位と選択入力の出典を初回解析・閲覧で共通化する。"""
from __future__ import annotations
import hashlib
import json
import logging
from pathlib import Path
import re
import numpy as np
import pandas as pd
from app.utils.deg_utils import extract_mz_numeric, is_meaningful_annotation

logger = logging.getLogger("msi.naming_policy")
MANIFEST = "feature_annotation_manifest.json"
SOURCES_CSV = "feature_annotation_sources.csv"
POLICY_VERSION = 1


def db_annotation_enabled(settings: dict | None) -> bool:
    """CSV パスの存在は許可と見なさず、保存された明示値だけを使う。"""
    settings = settings or {}
    for key in ("annotation_enable", "db_annotation_enabled"):
        if key in settings:
            value = settings[key]
            return value is True or (isinstance(value, str) and value.lower() == "true")
    return "db" in (settings.get("use_annotation_check") or [])


def load_naming_settings(rds_path) -> dict:
    for base in list(Path(rds_path).resolve().parents)[:4]:
        candidate = base / "analysis_params.json"
        if candidate.is_file():
            try:
                saved = json.loads(candidate.read_text(encoding="utf-8"))
                runtime = saved.get("runtime_parameters") or {}
                settings = dict(saved)
                settings.update(runtime)
                if "annotation_csv_path" in runtime:
                    settings["annotation_csv"] = runtime["annotation_csv_path"]
                elif runtime.get("annotation_path"):
                    settings["annotation_csv"] = runtime["annotation_path"]
                if "adduct_patterns" in runtime:
                    settings["adduct_filter"] = runtime["adduct_patterns"]
                return settings
            except (OSError, ValueError):
                logger.warning("名称設定を読めません: %s", candidate)
                return {}
    return {}


def _normalize_annotation_table(table):
    # 旧サイドカーは raw だけを持つ場合がある。由来を保持して同じ構造へ読む。
    if "compound" not in table and "raw" in table:
        from app.services.peak_annotation import parse_scils_name
        parsed = [parse_scils_name(raw) for raw in table["raw"]]
        table = table.copy()
        table["compound"] = [row["compound"] for row in parsed]
        if "adduct" not in table:
            table["adduct"] = [row["adduct"] for row in parsed]
    return table


def copy_selected_feature_annotations(input_files, output_dir) -> Path:
    """実際の選択入力だけのサイドカーをコピーし、相対パス manifest と R 用表を保存。"""
    # ★ verNEXT: フォルダ全体のコピーでは未選択入力の名称混入と同名ファイル上書きが起きた。
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    records, frames, seen = [], [], set()
    for item in input_files or []:
        source = Path(item).resolve()
        if str(source) in seen:
            continue
        seen.add(str(source))
        sidecar = source.with_name(source.stem + "_feature_annotations.parquet")
        entry = {"input_file": str(source), "sidecar": None}
        if sidecar.is_file():
            digest = hashlib.sha256(str(source).encode()).hexdigest()[:12]
            destination = output / "feature_annotation_inputs" / (digest + "_feature_annotations.parquet")
            destination.parent.mkdir(parents=True, exist_ok=True)
            table = _normalize_annotation_table(pd.read_parquet(sidecar))
            table["source_file"] = str(source)
            table.to_parquet(destination, index=False)
            entry["sidecar"] = str(destination.relative_to(output))
            frames.append(table)
        records.append(entry)
    table = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["mz", "compound", "adduct", "source_file"])
    table.to_csv(output / SOURCES_CSV, index=False, encoding="utf-8")
    manifest = output / MANIFEST
    manifest.write_text(json.dumps({"version": POLICY_VERSION, "inputs": records}, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def find_annotation_sidecars(rds_path) -> list[Path]:
    """manifest がある解析はその選択集合だけを使い、旧結果は近傍探索で読む。"""
    bases = list(Path(rds_path).resolve().parents)[:3]
    for base in bases:
        manifest = base / MANIFEST
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                return [(base / row["sidecar"]).resolve() for row in data["inputs"] if row.get("sidecar")]
            except (OSError, ValueError, KeyError, TypeError):
                logger.warning("選択入力の名称 manifest を読めません: %s", manifest)
                return []
    for base in bases:
        for pattern in ("*_feature_annotations.parquet", "*/*_feature_annotations.parquet"):
            hits = sorted(base.glob(pattern))
            if hits:
                return hits
    return []


def _text(value) -> str:
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        return ""
    value = str(value).strip()
    return "" if value.lower() in {"", "nan", "none", "na", "no db hit"} else value


def embedded_compound(feature: str) -> str:
    """旧 feature ID は保持し、名称部分だけを表示用に取り出す。"""
    head = str(feature).split("|", 1)[0].strip()
    match = re.match(r"^(.+)_([0-9]+\.[0-9]+)$", head)
    if match and match.group(1).strip().lower() not in {"mz", "m/z", "no db hit"}:
        return match.group(1).strip() if is_meaningful_annotation(match.group(1), feature) else ""
    return ""


def resolve_feature_annotations(paths, features_list, tolerance=0.005) -> dict:
    """特徴量単位で SCiLS 名を合意し、競合した特徴量のみ名前を保留する。"""
    # ★ verNEXT: 一つの競合で他の全特徴量まで無名になる従来の全表比較を廃止。
    tables = []
    for path in paths:
        try:
            table = _normalize_annotation_table(pd.read_parquet(path))
            mass = pd.to_numeric(table["mz"], errors="coerce").to_numpy(dtype=float)
            valid = np.isfinite(mass)
            table = table.loc[valid].reset_index(drop=True)
            mass = mass[valid]
            order = np.argsort(mass, kind="mergesort")
            tables.append((str(path), table.iloc[order].reset_index(drop=True), mass[order]))
        except Exception:
            logger.warning("名称サイドカーを読めません: %s", path, exc_info=True)
    result = {}
    for feature in features_list:
        mz = extract_mz_numeric(feature)
        candidates = []
        if np.isfinite(mz):
            for path, table, masses in tables:
                if not len(masses):
                    continue
                position = int(np.searchsorted(masses, mz))
                neighbors = [j for j in (position - 1, position) if 0 <= j < len(masses)]
                nearest = min(abs(masses[j] - mz) for j in neighbors)
                if nearest > tolerance:
                    continue
                left = np.searchsorted(masses, mz - nearest - 1e-10)
                right = np.searchsorted(masses, mz + nearest + 1e-10, side="right")
                for position in range(int(left), int(right)):
                    if abs(abs(masses[position] - mz) - nearest) > 1e-10:
                        continue
                    row = table.iloc[position]
                    compound = _text(row.get("compound"))
                    if not is_meaningful_annotation(compound, feature):
                        continue
                    record = {key: _text(row.get(key)) or None for key in ("lipid_class", "database", "adduct", "formula", "smiles", "adduct_image", "adduct_family", "raw")}
                    record.update(compound=compound, mz=float(row["mz"]), source_file=_text(row.get("source_file")) or path)
                    ppm = pd.to_numeric(row.get("ppm"), errors="coerce")
                    record["ppm"] = float(ppm) if pd.notna(ppm) else None
                    candidates.append(record)
        if not candidates:
            compound = embedded_compound(feature)
            if compound:
                result[feature] = {"compound": compound, "display_name": str(feature).split("|", 1)[0].strip(), "status": "resolved", "source": "embedded", "candidates": []}
            continue
        names = {row["compound"] for row in candidates}
        adducts = {row["adduct"] for row in candidates if row.get("adduct")}
        conflict = len(names) > 1 or len(adducts) > 1
        record = dict(candidates[0])
        record.update(compound=None if conflict else candidates[0]["compound"],
                      display_name=None if conflict else f'{candidates[0]["compound"]}_{mz:.4f}',
                      status="conflict" if conflict else "resolved", source="SCiLS", candidates=candidates)
        result[feature] = record
    return result


def compose_annotation_map(features, scils_records, db_map=None) -> dict:
    """SCiLS 名 > 明示的に得た DB 候補。競合特徴量は DB でも補完しない。"""
    result = {}
    for feature in features:
        record = (scils_records or {}).get(feature) or {}
        if record.get("status") == "conflict":
            continue
        compound = record.get("compound") or embedded_compound(feature) or (db_map or {}).get(feature)
        if is_meaningful_annotation(compound, feature):
            result[feature] = compound
    return result


def apply_annotation_map(deg_data, annotation_map, *, preserve_existing=False):
    """全表示・CSV・PPTX が同じ名前を見るよう DEG の表示列のみ同期する。"""
    for row in deg_data or []:
        gene = row.get("gene", "")
        if gene in annotation_map:
            row["annotation"] = annotation_map[gene]
        elif not preserve_existing:
            row["annotation"] = gene
