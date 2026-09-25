"""imzML の座標だけから物理切片候補と軽量プレビューを作る。

強度配列を持つ ibd は開かない。ここで得る component は最小の幾何学単位で、
ユーザーが複数 component を 1 section に結合できる。ROI annotation とは別契約。
"""
from __future__ import annotations

from collections import Counter, deque
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


SCHEMA_VERSION = 1
CONNECTIVITY = 8
MAX_PREVIEW_POINTS = 3000
_X = "IMS:1000050"
_Y = "IMS:1000051"
_Z = "IMS:1000052"


class SpatialLayoutError(ValueError):
    """座標から物理切片候補を安全に構築できない。"""


def _tag(elem) -> str:
    return elem.tag.rsplit("}", 1)[-1]


def _collect_cv(node, refs: dict[str, dict[str, tuple[str, str]]]):
    values: dict[str, tuple[str, str]] = {}

    def visit(elem):
        yield elem
        for child in elem:
            # 外部配列の offset/length などは座標契約へ混ぜない。
            if _tag(child) == "binaryDataArray" and _tag(node) != "binaryDataArray":
                continue
            yield from visit(child)

    for elem in visit(node):
        kind = _tag(elem)
        if kind == "referenceableParamGroupRef":
            ref = elem.get("ref") or ""
            if ref not in refs:
                raise SpatialLayoutError(f"未定義の parameter group: {ref}")
            values.update(refs[ref])
        elif kind == "cvParam":
            accession = elem.get("accession") or ""
            value = (elem.get("value", ""), elem.get("name", ""))
            if accession in values and values[accession][0] != value[0]:
                raise SpatialLayoutError(f"矛盾する座標属性: {accession}")
            values[accession] = value
    return values


def read_imzml_coordinates(path: str | Path) -> list[tuple[int, int, int]]:
    """XML 内の spectrum 順で (x, y, z) を返す。ibd は開かない。"""
    xml = Path(path).expanduser().resolve()
    if xml.suffix.lower() != ".imzml" or not xml.is_file():
        raise SpatialLayoutError(f".imzML が見つかりません: {xml}")
    refs: dict[str, dict[str, tuple[str, str]]] = {}
    coordinates: list[tuple[int, int, int]] = []
    seen: set[tuple[int, int, int]] = set()
    try:
        for _, elem in ET.iterparse(xml, events=("end",)):
            kind = _tag(elem)
            if kind == "referenceableParamGroup":
                refs[elem.get("id") or ""] = _collect_cv(elem, refs)
                elem.clear()
            elif kind == "spectrum":
                attrs = _collect_cv(elem, refs)
                xyz = []
                for term, default in ((_X, None), (_Y, None), (_Z, "1")):
                    raw = attrs.get(term, (default, ""))[0]
                    if raw is None:
                        raise SpatialLayoutError("imzML spectrum に x/y 座標がありません。")
                    text = str(raw).strip()
                    if not re.fullmatch(r"\+?\d+", text):
                        raise SpatialLayoutError("画素座標は非負の整数である必要があります。")
                    xyz.append(int(text))
                coord = tuple(xyz)
                if coord in seen:
                    raise SpatialLayoutError(f"画素座標が重複しています: {coord}")
                seen.add(coord)
                coordinates.append(coord)
                elem.clear()
    except ET.ParseError as exc:
        raise SpatialLayoutError(f"imzML XML が不正です: {exc}") from exc
    if not coordinates:
        raise SpatialLayoutError("imzML に spectrum 座標がありません。")
    if len({z for _, _, z in coordinates}) != 1:
        raise SpatialLayoutError("複数の z 面は別試料として登録してください。")
    return coordinates


def _dominant_pitch(coordinates, axis: str) -> int:
    """同じ走査行／列で反復する隣接差を格子間隔として採用する。

    全座標の最小差だけを使うと、複数切片の開始位置が1だけずれた場合に
    本来20刻みの格子を1刻みと誤認する。行（x）または列（y）内で繰り返す
    差を優先し、情報が無い場合だけ全体差へフォールバックする。
    """
    if axis not in {"x", "y"}:
        raise SpatialLayoutError("axis は x / y で指定してください。")
    groups = {}
    value_index, group_index = (0, 1) if axis == "x" else (1, 0)
    for coord in coordinates:
        groups.setdefault(int(coord[group_index]), set()).add(int(coord[value_index]))
    diffs = []
    for values in groups.values():
        ordered = sorted(values)
        diffs.extend(b - a for a, b in zip(ordered, ordered[1:]) if b > a)
    if not diffs:
        ordered = sorted({int(coord[value_index]) for coord in coordinates})
        diffs = [b - a for a, b in zip(ordered, ordered[1:]) if b > a]
    if not diffs:
        return 1
    counts = Counter(diffs)
    frequency = max(counts.values())
    # 同頻度なら細かい格子を採る。大きな切片間gapを優先しない。
    return max(1, min(step for step, count in counts.items() if count == frequency))


def _coordinate_hash(coordinates) -> str:
    payload = json.dumps(sorted([list(map(int, c)) for c in coordinates]),
                         ensure_ascii=False, separators=(",", ":"))
    return sha256(payload.encode("utf-8")).hexdigest()


def _component_id(coordinates) -> str:
    return "component_" + _coordinate_hash(coordinates)[:20]


def _sample_preview(records, limit: int):
    if limit <= 0 or not records:
        return []
    ordered = sorted(records, key=lambda r: (r[1], r[0], r[3]))
    if len(ordered) <= limit:
        chosen = ordered
    else:
        step = len(ordered) / limit
        chosen = [ordered[min(len(ordered) - 1, int(i * step))] for i in range(limit)]
    return [{"x": int(x), "y": int(y), "source_index": int(source_index)}
            for x, y, _z, source_index in chosen]


def build_spatial_layout(coordinates, *, include_preview=True,
                         max_preview_points=MAX_PREVIEW_POINTS,
                         connectivity=CONNECTIVITY) -> dict:
    """座標の8近傍連結成分を切片候補として返す。"""
    if connectivity not in (4, 8):
        raise SpatialLayoutError("connectivity は 4 または 8 で指定してください。")
    coords = [tuple(int(v) for v in row[:3]) for row in coordinates]
    if not coords:
        raise SpatialLayoutError("座標がありません。")
    if len(coords) != len(set(coords)):
        raise SpatialLayoutError("画素座標が重複しています。")
    if len({z for _, _, z in coords}) != 1:
        raise SpatialLayoutError("複数の z 面は別試料として登録してください。")
    if any(v < 0 for row in coords for v in row):
        raise SpatialLayoutError("画素座標は非負である必要があります。")

    pitch_x = _dominant_pitch(coords, "x")
    pitch_y = _dominant_pitch(coords, "y")
    by_xy = {(x, y): (x, y, z, i) for i, (x, y, z) in enumerate(coords)}
    if connectivity == 4:
        offsets = ((-pitch_x, 0), (pitch_x, 0), (0, -pitch_y), (0, pitch_y))
    else:
        offsets = tuple((dx, dy) for dx in (-pitch_x, 0, pitch_x)
                        for dy in (-pitch_y, 0, pitch_y) if dx or dy)

    remaining = set(by_xy)
    # 成分数が多い場合も毎回min(set)を取ってO(n^2)にしない。
    seeds = sorted(remaining, key=lambda point: (point[1], point[0]))
    seed_index = 0
    raw_components = []
    while remaining:
        while seed_index < len(seeds) and seeds[seed_index] not in remaining:
            seed_index += 1
        if seed_index >= len(seeds):
            raise SpatialLayoutError("座標component探索の内部整合性が失われました。")
        start = seeds[seed_index]
        seed_index += 1
        queue = deque([start])
        remaining.remove(start)
        records = []
        while queue:
            xy = queue.popleft()
            records.append(by_xy[xy])
            for dx, dy in offsets:
                neighbour = (xy[0] + dx, xy[1] + dy)
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    queue.append(neighbour)
        raw_components.append(records)

    raw_components.sort(key=lambda rows: (min(r[1] for r in rows), min(r[0] for r in rows), -len(rows)))
    total = len(coords)
    allocations = []
    if include_preview:
        for rows in raw_components:
            allocations.append(max(1, round(max_preview_points * len(rows) / total)))
        while sum(allocations) > max_preview_points and max(allocations, default=0) > 1:
            idx = max(range(len(allocations)), key=allocations.__getitem__)
            allocations[idx] -= 1
    else:
        allocations = [0] * len(raw_components)

    components = []
    coordinate_components = {}
    source_components = [None] * total
    warnings = []
    for order, (rows, allocation) in enumerate(zip(raw_components, allocations), 1):
        component_coords = [(x, y, z) for x, y, z, _ in rows]
        cid = _component_id(component_coords)
        xs, ys = [r[0] for r in rows], [r[1] for r in rows]
        component = {
            "component_id": cid,
            "order": order,
            "default_name": f"Section {order:02d}",
            "pixel_count": len(rows),
            "bbox": {"min_x": min(xs), "max_x": max(xs),
                     "min_y": min(ys), "max_y": max(ys)},
            "centroid": {"x": sum(xs) / len(xs), "y": sum(ys) / len(ys)},
        }
        if include_preview:
            component["preview"] = _sample_preview(rows, allocation)
        components.append(component)
        for x, y, z, source_index in rows:
            coordinate_components[(x, y, z)] = cid
            source_components[source_index] = cid
        if len(rows) <= 4:
            warnings.append(f"{component['default_name']} は {len(rows)} pixels の小領域です。")

    return {
        "schema_version": SCHEMA_VERSION,
        "coordinate_hash": _coordinate_hash(coords),
        "connectivity": connectivity,
        "grid_pitch": {"x": pitch_x, "y": pitch_y},
        "z": coords[0][2],
        "pixel_count": total,
        "component_count": len(components),
        "components": components,
        "warnings": warnings,
        # Python内部で変換時に使う。JSON保存前は strip_runtime_layout で除去する。
        "_coordinate_components": coordinate_components,
        "_source_components": source_components,
    }


def strip_runtime_layout(layout: dict, *, strip_preview=False) -> dict:
    result = deepcopy(layout)
    result.pop("_coordinate_components", None)
    result.pop("_source_components", None)
    if strip_preview:
        for component in result.get("components", []):
            component.pop("preview", None)
    return result


def inspect_imzml_spatial_layout(path: str | Path, *, max_preview_points=MAX_PREVIEW_POINTS) -> dict:
    p = Path(path).expanduser().resolve()
    st = p.stat()
    return deepcopy(_inspect_cached(str(p), st.st_size, st.st_mtime_ns, int(max_preview_points)))


@lru_cache(maxsize=64)
def _inspect_cached(path: str, _size: int, _mtime_ns: int, max_preview_points: int) -> dict:
    coords = read_imzml_coordinates(path)
    layout = build_spatial_layout(coords, include_preview=True, max_preview_points=max_preview_points)
    return strip_runtime_layout(layout)


def default_spatial_sections(file_id: str, layout: dict) -> list[dict]:
    result = []
    for component in layout.get("components", []):
        cid = str(component["component_id"])
        result.append({
            "section_id": stable_spatial_section_id(file_id, [cid]),
            "section_display_name": str(component.get("default_name") or f"Section {len(result)+1:02d}"),
            "component_ids": [cid],
            "pixel_count": int(component.get("pixel_count", 0)),
            "selected": True,
            "subject_id": "",
            "group": "",
        })
    return result


def stable_spatial_section_id(file_id: str, component_ids) -> str:
    ids = sorted(str(x) for x in component_ids)
    token = str(file_id) + "\x00spatial\x00" + "\x00".join(ids)
    return "section_" + sha256(token.encode("utf-8")).hexdigest()[:20]


def normalize_spatial_sections(file_id: str, layout: dict, sections=None) -> list[dict]:
    """componentを重複・欠落させず、結合構成と名称を正規化する。"""
    components = {str(c["component_id"]): c for c in layout.get("components", [])}
    if not components:
        raise SpatialLayoutError("座標componentがありません。")
    rows = deepcopy(sections) if sections else default_spatial_sections(file_id, layout)
    normalized = []
    assigned = set()
    for row in rows:
        ids = sorted(dict.fromkeys(str(x) for x in row.get("component_ids", [])))
        if not ids:
            continue
        unknown = [x for x in ids if x not in components]
        if unknown:
            raise SpatialLayoutError("未知の座標componentが指定されています: " + ", ".join(unknown))
        overlap = assigned.intersection(ids)
        if overlap:
            raise SpatialLayoutError("座標componentが複数切片へ重複所属しています: " + ", ".join(sorted(overlap)))
        assigned.update(ids)
        sid = stable_spatial_section_id(file_id, ids)
        default_order = min(int(components[x].get("order", 10**9)) for x in ids)
        normalized.append({
            "section_id": sid,
            "section_display_name": str(row.get("section_display_name") or f"Section {default_order:02d}").strip(),
            "component_ids": ids,
            "pixel_count": sum(int(components[x].get("pixel_count", 0)) for x in ids),
            "selected": bool(row.get("selected", True)),
            "subject_id": str(row.get("subject_id") or "").strip(),
            "group": str(row.get("group") or "").strip(),
            "integration_unit_id": sid,
            "_order": default_order,
        })
    missing = set(components) - assigned
    for cid in sorted(missing, key=lambda x: int(components[x].get("order", 10**9))):
        component = components[cid]
        sid = stable_spatial_section_id(file_id, [cid])
        normalized.append({
            "section_id": sid,
            "section_display_name": str(component.get("default_name") or f"Section {component.get('order', 1):02d}"),
            "component_ids": [cid],
            "pixel_count": int(component.get("pixel_count", 0)),
            "selected": True,
            "subject_id": "",
            "group": "",
            "integration_unit_id": sid,
            "_order": int(component.get("order", 10**9)),
        })
    normalized.sort(key=lambda row: (row.pop("_order"), row["section_id"]))
    names = [row["section_display_name"] for row in normalized]
    if any(not name for name in names):
        raise SpatialLayoutError("切片名は空にできません。")
    if len(names) != len(set(names)):
        raise SpatialLayoutError("同じファイル内の切片名は重複できません。")
    return normalized



def reset_spatial_sections(file_id: str, layout: dict, sections) -> list[dict]:
    """自動component分割へ戻し、既存の対象状態・群情報を可能な範囲で保持する。"""
    current = normalize_spatial_sections(file_id, layout, sections)
    owners = {str(cid): row for row in current for cid in row.get("component_ids", [])}
    result = default_spatial_sections(file_id, layout)
    for row in result:
        cid = row["component_ids"][0]
        old = owners.get(cid)
        if not old:
            continue
        row["selected"] = bool(old.get("selected", True))
        row["subject_id"] = str(old.get("subject_id") or "")
        row["group"] = str(old.get("group") or "")
        if len(old.get("component_ids", [])) == 1:
            row["section_display_name"] = str(old.get("section_display_name") or row["section_display_name"])
    return normalize_spatial_sections(file_id, layout, result)

def merge_spatial_sections(file_id: str, layout: dict, sections, row_indices) -> list[dict]:
    rows = normalize_spatial_sections(file_id, layout, sections)
    indices = sorted(set(int(i) for i in row_indices if isinstance(i, int) or str(i).isdigit()))
    indices = [i for i in indices if 0 <= i < len(rows)]
    if len(indices) < 2:
        raise SpatialLayoutError("結合する切片を2つ以上選択してください。")
    first = rows[indices[0]]
    selected_rows = [rows[i] for i in indices]
    component_ids = sorted({cid for row in selected_rows for cid in row["component_ids"]})
    merged = {
        "section_id": stable_spatial_section_id(file_id, component_ids),
        "section_display_name": first["section_display_name"],
        "component_ids": component_ids,
        "selected": any(row.get("selected", True) for row in selected_rows),
        "subject_id": first.get("subject_id", "") if len({row.get("subject_id", "") for row in selected_rows}) == 1 else "",
        "group": first.get("group", "") if len({row.get("group", "") for row in selected_rows}) == 1 else "",
    }
    remaining = [row for i, row in enumerate(rows) if i not in indices]
    remaining.append(merged)
    return normalize_spatial_sections(file_id, layout, remaining)
