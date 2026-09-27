#!/usr/bin/env python3
"""対象解析1件のクラスタ出力を、アプリと同じ照合関数で読み取り診断する。

例:
  python App/tools/diag_umap_cluster_export.py --rds result/RDS_Files/Step2.rds \
      --plot-data cache/key/plot_data.parquet --instrument TIMS --data-dir data
  docker compose exec -T msi-app python3 - --rds /data/result/Step2.rds \
      --plot-data /data/cache/key/plot_data.parquet --instrument TIMS < App/tools/diag_umap_cluster_export.py

終了コード: 0=解析画素を全て照合、3=全て未一致、4=一部未一致、1=診断不能。
★ ver74.0: 全cacheのSample名を合併すると別解析で誤って成功し、由来IDを
無視すると同名切片や意図的な選択外画素を異常扱いする。対象を必須にした。
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path
import sys

# stdin起動とリポジトリ直実行の両方でアプリの実装を利用する。
for _root in (Path(__file__).resolve().parent.parent, Path.cwd() / "App", Path("/app/App")):
    if (_root / "app/services").is_dir():
        sys.path.insert(0, str(_root))
        break

import pandas as pd
import pyarrow.parquet as pq


def read_input_keys(path, instrument):
    """強度行列を展開せず、照合に必要な列だけを読む。"""
    path = Path(path)
    if instrument == "DESI":
        from app.services.desi_header import read_desi_header
        header = read_desi_header(path)
        if header is None:
            raise ValueError(f"DESIヘッダを判定できません: {path}")
        with path.open(encoding="utf-8") as handle:
            rows = csv.reader(handle, delimiter="\t")
            for _ in range(header.n_header):
                next(rows)
            data = [row[:3] for row in rows if row and any(row)]
        return pd.DataFrame(data, columns=["id", "x", "y"])
    if path.suffix.lower() in {".parquet", ".pq"}:
        columns = pq.ParquetFile(path).schema_arrow.names
        return pd.read_parquet(path, columns=[c for c in ("id", "x", "y", "annotation") if c in columns])
    # 見出し付きCSV/TSVも旧SCiLS Transform形式も実際のexport readerへ委譲する。
    from app.callbacks.interactive_data_export import _read_tims_file
    return _read_tims_file(str(path))


def diagnose(plot_data, rds_path, paths, instrument):
    """解析に存在するキーだけを期待集合とし、入力側の選択外画素を別計数する。"""
    from app.callbacks.interactive_data_export import _build_cluster_lookup, _match_sample_name
    from app.services.export_transform import append_cluster_region_columns
    from app.services.section_group_metadata import (
        append_source_clusters, input_source_id, source_match_mask,
    )
    if plot_data.empty or "Cluster" not in plot_data or plot_data["Cluster"].isna().any():
        raise ValueError("対象plot_dataが空、またはCluster列に欠損があります")
    if plot_data["Cluster"].astype(str).str.strip().eq("").any():
        raise ValueError("対象plot_dataのCluster列に空欄があります")
    lookup = _build_cluster_lookup(plot_data)
    by_source = bool(getattr(lookup, "by_source", None))
    if by_source and (plot_data[["source_file_id", "source_pixel_id"]].isna().any().any() or
                      plot_data[["source_file_id", "source_pixel_id"]].astype(str).apply(lambda c: c.str.strip().eq("")).any().any()):
        raise ValueError("対象plot_dataの由来IDが一部の画素で欠けています")
    # 実際のクラスタ値の代わりにキーごとの識別子を付与し、どの解析画素が
    # 照合できたかを追う。照合処理そのものはexportと同じ関数を使う。
    mapping = lookup.by_source if by_source else lookup
    tokens = {key: f"key_{i}" for i, key in enumerate(mapping)}
    if not tokens:
        raise ValueError("対象plot_dataに照合可能な由来IDまたは座標がありません")
    mapping.update(tokens)
    samples = sorted({key[0] for key in lookup})
    observed = set()
    report = []
    for path in paths:
        df = read_input_keys(path, instrument)
        if by_source:
            if input_source_id(path, rds_path) is None or "id" not in df:
                raise ValueError(f"入力と対象結果の元ファイルID/画素IDを対応できません: {path}")
            out = append_source_clusters(df, path, rds_path, {"target": lookup})
            mask = source_match_mask(df, path, rds_path, {"target": lookup})
            resolver = "source-id"
        else:
            if instrument == "DESI" and _match_sample_name(Path(path).stem, samples) is None:
                raise ValueError("旧DESIのROI分割結果は由来IDが無く、この診断では一意に対応できません")
            stats = {}
            out = append_cluster_region_columns(df, {"target": lookup}, None, samples,
                                                False, Path(path).stem, _match_sample_name, stats=stats)
            mask = out["UMAP cluster"].ne("")
            resolver = stats.get("resolver", "legacy")
        matched = set(out.loc[mask, "UMAP cluster"].astype(str))
        observed.update(matched)
        report.append({"path": str(path), "rows": len(df), "matched": int(mask.sum()),
                       "outside_analysis": int((~mask).sum()), "resolver": resolver})
    missing = set(tokens.values()) - observed
    code = (3 if not observed else 4) if missing else 0
    return code, {"expected": len(tokens), "matched": len(set(tokens.values()) & observed),
                  "missing": len(missing), "files": report}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plot-data", required=True, type=Path, help="診断する解析1件のplot_data.parquet")
    parser.add_argument("--rds", required=True, type=Path, help="同じ解析結果の元RDSパス")
    parser.add_argument("--instrument", required=True, choices=("TIMS", "DESI"))
    parser.add_argument("--data-dir", type=Path, help="manifestの無い旧結果の生データフォルダ")
    args = parser.parse_args(argv)
    try:
        from app.services.section_group_metadata import selected_input_paths
        suffixes = {".txt"} if args.instrument == "DESI" else {".parquet", ".pq", ".csv", ".tsv"}
        paths = selected_input_paths(str(args.rds), suffixes)
        if paths is None:
            if args.data_dir is None:
                raise ValueError("旧結果の診断には --data-dir が必要です")
            if args.instrument == "TIMS":
                from app.services.data_manager import build_tims_input_paths
                paths = build_tims_input_paths(str(args.data_dir))
            else:
                paths = sorted(args.data_dir.glob("*.txt"))
        if not paths:
            raise ValueError("対象解析の入力ファイルがありません")
        code, result = diagnose(pd.read_parquet(args.plot_data), str(args.rds), paths, args.instrument)
        print(f"対象結果: {args.rds}\n対象cache: {args.plot_data}")
        for item in result["files"]:
            print(f"  {item['path']}: 照合 {item['matched']}/{item['rows']}画素 "
                  f"({item['resolver']}), 解析対象外 {item['outside_analysis']}画素")
        print(f"解析画素: {result['matched']}/{result['expected']} 一致、未一致 {result['missing']}")
        return code
    except Exception as exc:
        print(f"診断不能: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
