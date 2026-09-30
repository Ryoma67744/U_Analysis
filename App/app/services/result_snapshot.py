"""★ ver77.0: 画面の手法と出力手法を混ぜないための、ジョブ所有の結果スナップショット。"""
from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
import inspect
from pathlib import Path, PureWindowsPath, PurePosixPath

import pyarrow as pa


def file_signature(path):
    """mtime が同じ置換も検出する。元ファイルへ書き込まない。"""
    path = Path(path)
    before = path.stat()
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("解析結果が変更されています。読み込み完了後に出力してください。")
    return digest.hexdigest()


@dataclass(frozen=True)
class MethodSource:
    method: str
    path: str
    sha256: str
    sidecar_path: str | None = None
    sidecar_sha256: str | None = None
    records: tuple[tuple[str, str | None], ...] = ()


@dataclass(frozen=True)
class ExportRequest:
    sources: tuple[MethodSource, ...]
    scope: str


@dataclass(frozen=True)
class MethodResultSnapshot:
    source: MethodSource
    table: pa.Table
    meta: dict
    descriptor: dict
    cache_dir: str | None

    def frame(self):
        # ★ ver77.0: Arrow の固定バッファを caller が上書きできない DataFrame を返す。
        return self.table.to_pandas().copy(deep=True)

    def manifest(self):
        return {"requested_method": self.source.method,
                "source_file": Path(self.source.path).name,
                "source_sha256": self.source.sha256,
                "embedding_source_sha256": self.source.sidecar_sha256,
                "result_descriptor": public_metadata(self.descriptor)}


def capture_source_states(rds_map, methods):
    """★ ver77.0: 受付を塞がず、worker開始前の通常のファイル置換を検知する。"""
    from app.services.result_catalog import find_embedding_sidecar
    states = {}
    stamp = lambda st: (st.st_size, st.st_mtime_ns, st.st_ctime_ns, st.st_ino)
    for method in methods:
        path = str(Path(rds_map[method]).resolve())
        if not Path(path).is_file():
            continue
        sidecar = find_embedding_sidecar(path)
        sidecar = str(Path(sidecar).resolve()) if sidecar else None
        states[path] = (stamp(Path(path).stat()), sidecar,
                        stamp(Path(sidecar).stat()) if sidecar else None)
    return states


def build_export_request(rds_map, selected_methods=None, *, current_method=None, scope="", expected_states=None):
    """キー互換は呼出側で解決済み。無効指定を現在画面や全手法へ置換しない。"""
    rmap = dict(rds_map or {})
    methods = list(rmap) if selected_methods is None else list(dict.fromkeys(selected_methods))
    if not methods or any(method not in rmap for method in methods):
        raise ValueError("出力対象の手法がありません。手法を選択し直してください。")
    if expected_states:
        current = capture_source_states(rmap, methods)
        if any(current.get(path) != state for path, state in expected_states.items()):
            raise ValueError("出力受付後に解析結果が変更されました。再読込後に出力してください。")
    if current_method in methods:
        methods = [current_method, *[m for m in methods if m != current_method]]
    sources = []
    from app.services.result_catalog import find_embedding_sidecar, result_root
    for method in methods:
        path = Path(rmap[method])
        if not path.is_file():
            raise ValueError(f"選択した手法 {method} の解析結果がありません。")
        sidecar = find_embedding_sidecar(path)
        sidecar = str(Path(sidecar).resolve()) if sidecar else None
        root = result_root(path)
        records = tuple((str((root / name).resolve()), file_signature(root / name) if (root / name).is_file() else None)
                        for name in ("analysis_methods.json", "analysis_params.json", "runtime_params.json"))
        sources.append(MethodSource(str(method), str(path.resolve()), file_signature(path),
                                    sidecar, file_signature(sidecar) if sidecar else None, records))
    return ExportRequest(tuple(sources), str(scope))


def load_method_snapshots(request, bridge, *, purpose="spatial"):
    """各 RDS を検証して一度だけ抽出。live state は参照しない。"""
    snapshots = OrderedDict()
    for source in request.sources:
        _verify_source(source)
        # 旧 bridge adapter と小型 fixture は従来 signature のまま利用できる。
        parameters = inspect.signature(bridge.extract_data).parameters
        supports_verify = "force_verify" in parameters or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in parameters.values())
        result = bridge.extract_data(source.path, **({"force_verify": True} if supports_verify else {}))
        frame = result.get("plot_data")
        if frame is None or frame.empty:
            raise ValueError(f"選択した手法 {source.method} の解析済み画素を読み込めません。")
        descriptor = deepcopy(result.get("result_descriptor") or
                              (result.get("meta") or {}).get("result_descriptor") or {})
        recorded_hash = descriptor.get("source", {}).get("sha256")
        if recorded_hash and recorded_hash != source.sha256:
            raise ValueError("抽出キャッシュと選択した解析結果の内容が一致しません。")
        embedding = descriptor.get("embedding", {})
        if embedding.get("location") == "sidecar":
            if (not embedding.get("source_path") or
                    str(Path(embedding["source_path"]).resolve()) != source.sidecar_path or
                    embedding.get("source_sha256") != source.sidecar_sha256):
                raise ValueError("抽出キャッシュと選択したUMAP補助ファイルの内容が一致しません。")
        if descriptor.get("classification", {}).get("state") == "conflict":
            raise ValueError("解析結果の由来が矛盾しています。手法を確定するまで出力できません。")
        if descriptor and purpose and not descriptor.get("capabilities", {}).get(purpose, False):
            raise ValueError("クラスタ計算済みの空間結果がありません。下流解析を完了してください。")
        _verify_source(source)
        snapshots[source.method] = MethodResultSnapshot(
            source, pa.Table.from_pandas(frame.copy(deep=True), preserve_index=False),
            deepcopy(result.get("meta") or {}), descriptor, result.get("cache_dir"))
    return snapshots


def _verify_source(source):
    from app.services.result_catalog import find_embedding_sidecar
    sidecar = find_embedding_sidecar(source.path)
    sidecar = str(Path(sidecar).resolve()) if sidecar else None
    expected = ((source.path, source.sha256), *source.records)
    if source.sidecar_path:
        expected += ((source.sidecar_path, source.sidecar_sha256),)
    if sidecar != source.sidecar_path or any(
            (file_signature(path) if Path(path).is_file() else None) != digest
            for path, digest in expected):
        raise ValueError("出力中に解析結果またはUMAP補助ファイルが変更されました。出力を中止しました。")


def verify_request(request):
    if request is not None:
        for source in request.sources:
            _verify_source(source)


def export_method_name(method, descriptor):
    """誤った旧選択キーを科学的な手法名として公開しない。"""
    if not descriptor:
        return str(method)
    from app.services.result_contract import descriptor_method
    actual = descriptor_method(descriptor, fallback=None)
    if not actual:
        return "Unknown"
    if descriptor.get("clusters", {}).get("kind") == "inherited":
        return f"{actual}_projection"
    return str(actual)


def descriptor_caption(descriptor):
    if not descriptor:
        return ""
    from app.services.result_contract import descriptor_label
    return descriptor_label(descriptor)


def embedding_title(descriptor):
    return "PCA (PC1 / PC2)" if descriptor.get("embedding", {}).get("kind") == "pca2d" else "UMAP"


def public_metadata(value):
    """★ ver77.0: 持ち出す由来情報はbasename/hash/IDを残し、非公開の絶対パスを省く。"""
    if isinstance(value, dict):
        return {key: public_metadata(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [public_metadata(item) for item in value]
    if isinstance(value, (str, Path)):
        text = str(value)
        if PureWindowsPath(text).is_absolute():
            return PureWindowsPath(text).name
        if PurePosixPath(text).is_absolute():
            return PurePosixPath(text).name
    return value


def pin_input_files(paths, manifest=None):
    """入力の既知SHAを照合し、既知情報がなくても今回読んだ強度ファイルを固定する。"""
    records = []
    for path in paths:
        path = str(Path(path).resolve())
        digest = file_signature(path)
        known = None
        for entry in (manifest or {}).get("files", []):
            candidate = entry.get("runtime_path") or entry.get("path")
            if candidate and str(Path(candidate).resolve()) == path:
                known = (entry.get("validation", {}).get("parquet", {}).get("sha256") or
                         entry.get("input_fingerprint", {}).get("sha256"))
                source = entry.get("source_fingerprint") or {}
                if not known and isinstance(source, dict):
                    known = source.get("sha256")
                break
        if known and known != digest:
            raise ValueError(f"解析時の入力ファイルと内容が一致しません: {Path(path).name}")
        records.append({"path": path, "sha256": digest,
                        "verification": "analysis_record" if known else "export_snapshot"})
    return tuple(records)


def verify_input_files(records):
    for record in records or ():
        if not Path(record["path"]).is_file() or file_signature(record["path"]) != record["sha256"]:
            raise ValueError("出力中に強度の入力ファイルが変更されました。出力を中止しました。")


class ExportJoinAudit:
    """★ ver77.0: 解析済み画素の消失・二重結合だけを拒否し、手法別subsetの欠損を許す。"""

    def __init__(self, lookups, selection=None, reference_frame=None):
        self.expected, self.seen, self.source_methods = {}, {}, set()
        self.source_xy = {}
        if reference_frame is not None and {"source_file_id", "source_pixel_id", "SpatialX", "SpatialY"}.issubset(reference_frame.columns):
            from app.services.section_group_metadata import normalize_pixel_id
            for fid, pid, x, y in reference_frame[["source_file_id", "source_pixel_id", "SpatialX", "SpatialY"]].itertuples(index=False, name=None):
                self.source_xy[(str(fid), normalize_pixel_id(pid))] = (round(float(x), 4), round(float(y), 4))
        selected = selection._keys() if selection is not None else None
        for method, lookup in lookups.items():
            source = getattr(lookup, "by_source", None)
            kind = "source" if source else "legacy"
            keys = set(source or lookup)
            if selected is not None:
                keys &= {key[1:] for key in selected if key[0] == kind}
            self.expected[method], self.seen[method] = keys, set()
            if source:
                self.source_methods.add(method)

    def consume(self, legacy_keys, source_keys=None, spatial=None):
        if source_keys is not None and spatial is not None:
            for key, xy in zip(source_keys, spatial):
                if key in self.source_xy and xy != self.source_xy[key]:
                    raise ValueError("元画素IDに対応する空間座標が解析結果と一致しません。")
        for method, expected in self.expected.items():
            keys = source_keys if method in self.source_methods else legacy_keys
            if keys is None:
                continue
            for key in keys:
                if key not in expected:
                    continue
                if key in self.seen[method]:
                    raise ValueError(f"{method}: 同じ解析画素が複数の入力行に対応しています。")
                self.seen[method].add(key)

    def validate(self):
        for method, expected in self.expected.items():
            missing = expected - self.seen[method]
            if missing:
                raise ValueError(f"{method}: 解析済みの {len(missing)} 画素が入力と結合できません。出力を中止しました。")
