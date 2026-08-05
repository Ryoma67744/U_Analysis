#!/usr/bin/env python3
# =============================================================================
# MSI Analysis Application - Parquet 再パック CLI
#
# 既に変換済みの .parquet を「全行 1 row group」へ作り直す。
# CSV からの再変換は不要で、値は 1 ビットも変わらない。
#
# Usage:
#   python -u App/tools/repack_parquet_rowgroups.py <target_folder> [options]
#
# Options:
#   --dry-run        判定と見積りのみ。書き込まない
#   --no-backup      <file>.parquet.bak を作らない（既定は作る）
#   --skip-verify    書き込み後の全列ビット比較を省略（非推奨）
#   --allow-split    1 row group に収まらないとき、予算内で最大の row group に分割する
#   --no-recursive   直下のみを対象にする（既定は再帰）
#   --include=<glob>[,<glob>...]   既定: *.parquet
#
# 終了コード: 0 = 正常 / 1 = 引数エラー等 / 2 = 1 件以上のファイルでエラー
#
# 注意: GUI から起動する場合は必ず `-u` を付けること。stdout がファイルだと
#       CPython はブロックバッファになり、進捗が終了まで 1 行も出ない。
# =============================================================================

import sys
from pathlib import Path

# App/ を import path に載せる（App/tools/x.py → parents[1] == App/）。
# 環境変数に依存せず、シェルからも GUI からも同じ挙動になる。
# slim_existing_rds.R が --file= から自身のディレクトリを解決するのと同じ考え方。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.parquet_repack import DEFAULT_INCLUDE, repack_folder  # noqa: E402


def parse_args(argv):
    """引数を解析する。slim_existing_rds.R と同じ体裁に揃えてある。"""
    target = None
    opts = {
        "dry_run": False, "backup": True, "verify": True,
        "allow_split": False, "recursive": True,
        "patterns": None,
    }
    for a in argv:
        if a == "--dry-run":
            opts["dry_run"] = True
        elif a == "--no-backup":
            opts["backup"] = False
        elif a == "--skip-verify":
            opts["verify"] = False
        elif a == "--allow-split":
            opts["allow_split"] = True
        elif a == "--no-recursive":
            opts["recursive"] = False
        elif a.startswith("--include="):
            vals = [s.strip() for s in a[len("--include="):].split(",")]
            vals = [s for s in vals if s]
            opts["patterns"] = vals or None
        elif a in ("-h", "--help"):
            print(__doc__ or "", flush=True)
            raise SystemExit(0)
        elif a.startswith("--"):
            raise SystemExit(f"[repack] 未知のオプション: {a}")
        else:
            if target is not None:
                raise SystemExit("[repack] target_folder が複数指定されています")
            target = a
    if target is None:
        raise SystemExit(
            "[repack] Usage: python -u repack_parquet_rowgroups.py "
            "<target_folder> [options]"
        )
    opts["patterns"] = opts["patterns"] or [DEFAULT_INCLUDE]
    return target, opts


def main(argv):
    target, opts = parse_args(argv)
    folder = Path(target).expanduser().resolve()
    if not folder.is_dir():
        print(f"[repack] target_folder が存在しません: {folder}", flush=True)
        return 1

    agg = repack_folder(
        folder,
        patterns=opts["patterns"],
        dry_run=opts["dry_run"],
        backup=opts["backup"],
        verify=opts["verify"],
        allow_split=opts["allow_split"],
        recursive=opts["recursive"],
    )
    # 1 件でも失敗していれば非ゼロで返す。
    # slim_existing_rds.R は全件失敗でも 0 を返し、UI が緑の成功表示になる欠陥がある。
    return 2 if agg.n_error else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
