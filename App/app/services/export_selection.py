"""数値出力だけの対象選択。解析結果・表示用DataFrameは更新しない。"""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import math
from pathlib import Path

import pandas as pd

from app.services.section_group_metadata import (
    UNASSIGNED_GROUP, input_source_id, normalize_pixel_id,
)


def _text(value):
    return "" if value is None or pd.isna(value) else str(value)


def _digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _xy(row, x="SpatialX", y="SpatialY"):
    try:
        result = (float(row.get(x)), float(row.get(y)))
    except (ValueError, TypeError):
        raise ValueError("出力対象の画素に有効な空間座標がありません。") from None
    if not all(math.isfinite(value) for value in result):
        raise ValueError("出力対象の画素に有効な空間座標がありません。")
    # ★ ver75.1: 既存のクラスタ突合と同じ精度を使い、丸め後の衝突も別途拒否する。
    return tuple(round(value, 4) for value in result)


def _collect(frames, scope):
    if not isinstance(scope, str) or not scope:
        raise ValueError("出力対象の解析結果スコープがありません。")
    if not isinstance(frames, dict) or not frames:
        raise ValueError("出力対象の解析済みデータがありません。")
    pixels, units, kinds = {}, {}, set()
    if not all(isinstance(method, str) and method for method in frames):
        raise ValueError("出力手法名が不正です。")
    method_names = sorted(frames)
    for method in method_names:
        frame = frames[method]
        if not isinstance(method, str) or not method or not isinstance(frame, pd.DataFrame):
            raise ValueError("出力手法の解析済みデータが不正です。")
        duplicate_rows = {}
        for row in frame.to_dict("records"):
            fid, pid, sid = (_text(row.get(column)) for column in
                             ("source_file_id", "source_pixel_id", "section_id"))
            sample = _text(row.get("Sample"))
            group, subject = _text(row.get("group")), _text(row.get("subject_id"))
            label = _text(row.get("section_display_name")) or sample or sid
            if fid or pid:
                if not (fid and pid and sid):
                    raise ValueError("元ファイル・画素・切片IDが不完全なため対象を選択できません。")
                kind = "source"
                normalized_pid = normalize_pixel_id(pid)
                if not normalized_pid:
                    raise ValueError("元画素IDが空欄のため対象を選択できません。")
                key = (kind, fid, normalized_pid)
                unit_key = (kind, fid, sid)
                coordinates = _xy(row) if {"SpatialX", "SpatialY"}.issubset(row) else None
            else:
                if not sample:
                    raise ValueError("旧解析結果にSample名がないため対象を選択できません。")
                kind = "legacy"
                coordinates = _xy(row)
                key = (kind, sample, *coordinates)
                unit_key = (kind, sample)
            kinds.add(kind)
            metadata = (unit_key, group, subject, sid, coordinates)
            # ★ ver75.1: 同じ元画素を別手法の値で上書きせず、由来情報の矛盾を止める。
            old = pixels.get(key)
            if old is not None and old["metadata"] != metadata:
                raise ValueError("同じ解析画素の群・個体・切片または座標が手法間で矛盾しています。")
            local = (_text(row.get("CellID")), _text(row.get("Cluster")),
                     _text(row.get("UMAP_1")), _text(row.get("UMAP_2")), coordinates)
            if kind == "legacy" and key in duplicate_rows:
                raise ValueError("旧解析結果のSampleと座標に複数の画素が対応しています。")
            if key in duplicate_rows and duplicate_rows[key] != local:
                raise ValueError("重複した解析画素に異なる値があり、対象を一意に決定できません。")
            if kind == "legacy" and old is not None:
                cell = _text(row.get("CellID"))
                if cell and old["cell"] and cell != old["cell"]:
                    raise ValueError("旧解析結果のSampleと座標に複数の画素が対応しています。")
                if cell and not old["cell"]:
                    old["cell"] = cell
            duplicate_rows[key] = local
            unit_id = kind + ":" + _digest(unit_key)
            definition = (group, subject, sid, label)
            if unit_id in units and units[unit_id]["definition"] != definition:
                raise ValueError("同じ切片の群・個体・表示名が矛盾しています。")
            unit = units.setdefault(unit_id, {
                "id": unit_id, "label": label, "group": group or UNASSIGNED_GROUP,
                "subject_id": subject, "section_id": sid, "sample": sample,
                "source_file_id": fid, "definition": definition, "keys": set(),
                "methods": set(),
            })
            unit["keys"].add(key)
            unit["methods"].add(method)
            pixel = pixels.setdefault(key, {"metadata": metadata, "unit": unit_id,
                                           "methods": set(), "cell": _text(row.get("CellID"))})
            pixel["methods"].add(method)
    if len(kinds) > 1:
        raise ValueError("元画素IDのある結果と旧形式の結果を混在して選択できません。")
    public_units = [{key: value for key, value in unit.items()
                     if key not in {"definition", "keys", "methods"}} |
                    {"pixels": len(unit["keys"]), "methods": sorted(unit["methods"])}
                    for unit in units.values()]
    public_units.sort(key=lambda unit: (unit["group"], unit["label"], unit["id"]))
    signature = _digest({"scope": scope, "methods": method_names, "units": public_units,
                         "pixels": [(key, pixels[key]["metadata"], sorted(pixels[key]["methods"]))
                                    for key in sorted(pixels)]})
    catalog = {"scope": scope, "signature": signature, "units": public_units,
               "pixels": len(pixels), "total_pixels": len(pixels),
               "method_counts": {method: sum(method in pixel["methods"] for pixel in pixels.values())
                                 for method in method_names}}
    return catalog, pixels, next(iter(kinds), "source")


def build_catalog(frames: dict[str, pd.DataFrame], scope: str) -> dict:
    """全選択手法の画素和集合から、JSONへ保存できる選択候補を作る。"""
    return _collect(frames, scope)[0]


@dataclass
class ResolvedSelection:
    """確定した出力対象。元データの行順・値を保った行選択だけを行う。"""
    catalog: dict
    pixels: dict
    selected_ids: frozenset
    kind: str
    _selected_keys: frozenset = field(init=False, repr=False)
    _legacy_paths: dict = field(default_factory=dict, init=False, repr=False)
    _matched_keys: set = field(default_factory=set, init=False, repr=False)
    _matched_paths: dict = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        self._selected_keys = frozenset(key for key, pixel in self.pixels.items()
                                       if pixel["unit"] in self.selected_ids)

    def _keys(self):
        return self._selected_keys

    def includes_path(self, input_path, rds_path) -> bool:
        if self.kind == "legacy":
            # ★ ver75.1: 旧入力はファイル名だけで切片が判定できないので、読んで検査する。
            return True
        fid = input_source_id(input_path, rds_path)
        if fid is None:
            raise ValueError("入力ファイルを解析結果の元ファイルIDへ対応付けできません。")
        return any(key[1] == fid for key in self._keys())

    def filter_frame(self, df, input_path, rds_path) -> pd.DataFrame:
        if not isinstance(df, pd.DataFrame):
            raise ValueError("出力する画素表が不正です。")
        if df.empty:
            return df.copy()
        chosen = self._keys()
        if self.kind == "source":
            fid = input_source_id(input_path, rds_path)
            if fid is None or "id" not in df.columns:
                raise ValueError("元ファイルID・画素IDで出力対象を照合できません。")
            ids = df["id"].map(normalize_pixel_id)
            if df["id"].isna().any() or ids.eq("").any() or ids.duplicated().any():
                raise ValueError("入力の画素IDが欠落・重複しています。")
            keys = [("source", fid, pid) for pid in ids]
            mask = [key in chosen for key in keys]
            matched = chosen.intersection(keys)
        else:
            mask, matched = self._legacy_mask(df, input_path, chosen)
        path = str(Path(input_path).resolve())
        if any(self._matched_paths.get(key, path) != path for key in matched):
            raise ValueError("同じ選択画素が複数の入力ファイルに存在します。")
        self._matched_paths.update((key, path) for key in matched)
        self._matched_keys.update(matched)
        # ★ ver75.1: 選択をクラスタ辞書の削除に置き換えると、除外OFF時に全行が残る。
        # 明示した画素集合だけを選び、強度・座標・indexには手を加えない。
        return df.loc[mask].copy()

    def _legacy_mask(self, df, input_path, chosen):
        if not {"x", "y"}.issubset(df.columns):
            raise ValueError("旧入力に画素選択用の空間座標がありません。")
        samples = {key[1] for key in self.pixels}
        stem = Path(input_path).stem
        labels = None
        for column in ("Sample", "annotation"):
            if column in df.columns:
                candidate = df[column].map(_text)
                if candidate.isin(samples).any():
                    labels = candidate
                    break
        if labels is None:
            if stem not in samples:
                raise ValueError("旧入力のSample名を完全一致で対応付けできません。")
            labels = pd.Series([stem] * len(df), index=df.index)
        seen, mask = set(), []
        path = str(Path(input_path).resolve())
        for label, row in zip(labels, df.to_dict("records")):
            key = ("legacy", label, *_xy(row, "x", "y"))
            if key in seen and key in self.pixels:
                raise ValueError("旧入力のSampleと座標に複数の画素が対応しています。")
            if key in self.pixels:
                previous = self._legacy_paths.get(key)
                if previous is not None and previous != path:
                    raise ValueError("同じ旧Sampleと座標が複数の入力ファイルに存在します。")
                self._legacy_paths[key] = path
            seen.add(key)
            mask.append(key in chosen)
        return mask, chosen.intersection(seen)

    def validate_complete(self) -> None:
        """画素表を全入力から読んだ後、選んだ画素の欠落を公開前に検査する。"""
        # ★ ver75.1: 一部だけ読めた結果を成功にすると、選択件数と実出力が食い違う。
        # m/z一覧だけは画素表ではないため、呼び手が対象ファイルの存在を検査する。
        missing = self._selected_keys - self._matched_keys
        if missing:
            raise ValueError(f"選択した解析画素のうち{len(missing)}画素が入力に見つかりません。")

    def summary(self) -> dict:
        keys = self._keys()
        units = [unit for unit in self.catalog["units"] if unit["id"] in self.selected_ids]
        return {"mode": "selected", "scope": self.catalog["scope"],
                "signature": self.catalog["signature"], "ids": sorted(self.selected_ids),
                "units": units, "groups": sorted({unit["group"] for unit in units}),
                "pixels": len(keys),
                "method_counts": {method: sum(method in self.pixels[key]["methods"] for key in keys)
                                  for method in self.catalog["method_counts"]}}


def resolve_selection(frames, selection, scope) -> ResolvedSelection | None:
    """既定出力と空選択を区別し、結果切替・メタデータ編集後の古い指定を拒否する。"""
    if selection is None:
        return None
    if not isinstance(selection, dict) or selection.get("mode") not in ("all", "selected"):
        raise ValueError("出力対象の指定が不正です。")
    if selection["mode"] == "all":
        return None
    catalog, pixels, kind = _collect(frames, scope)
    if selection.get("scope") != scope or selection.get("signature") != catalog["signature"]:
        raise ValueError("解析結果または群情報が変更されました。出力対象を選び直してください。")
    ids = selection.get("ids")
    if (not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids)
            or len(set(ids)) != len(ids)):
        raise ValueError("出力対象を1つ以上、重複なく選択してください。")
    if set(ids) - {unit["id"] for unit in catalog["units"]}:
        raise ValueError("この解析結果にない出力対象が指定されています。")
    return ResolvedSelection(catalog, pixels, frozenset(ids), kind)
