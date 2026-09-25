#!/usr/bin/env python3
"""Background CLI for imzML import/export. Emits an atomic status receipt."""
import argparse
import json
import os
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.imzml_io import import_imzml, export_imzml, inspect_imzml  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["inspect", "import", "export"])
    parser.add_argument("source")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--pixel-ids", help="Comma-separated internal pixel IDs")
    parser.add_argument("--status", type=Path)
    args = parser.parse_args(argv)

    def status(value):
        if args.status:
            args.status.parent.mkdir(parents=True, exist_ok=True)
            temporary = args.status.with_suffix(".tmp")
            temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, args.status)
        print(json.dumps(value, ensure_ascii=False), flush=True)

    try:
        if args.action == "inspect":
            result = inspect_imzml(args.source)
        elif args.action == "import":
            if not args.output:
                parser.error("import requires an output .parquet path")
            result = import_imzml(args.source, args.output,
                                  progress=lambda done, total: status({"state": "running", "done": done, "total": total}))
        else:
            if not args.output:
                parser.error("export requires an output .zip path")
            ids = [int(s) for s in args.pixel_ids.split(",")] if args.pixel_ids else None
            result = export_imzml(args.source, args.output, pixel_ids=ids,
                                  progress=lambda done, total: status({"state": "running", "done": done, "total": total}))
        status({"state": "done", "result": result})
        return 0
    except Exception as exc:
        status({"state": "error", "error": str(exc)})
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
