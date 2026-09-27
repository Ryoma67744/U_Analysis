#!/usr/bin/env python3
"""Background CLI for imzML import/export. Emits an atomic status receipt."""
import argparse
import json
import os
import signal
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.imzml_io import export_imzml, inspect_imzml  # noqa: E402
from app.services.imzml_validation import checked_import  # noqa: E402
from app.services.input_preparation import PreparationCancelled, check_cancel, write_json  # noqa: E402


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["inspect", "import", "export"])
    parser.add_argument("source")
    parser.add_argument("output", nargs="?")
    parser.add_argument("--pixel-ids", help="Comma-separated internal pixel IDs")
    parser.add_argument("--alignment-ppm", type=float, default=0.0)
    parser.add_argument("--registration-spec", type=Path)
    parser.add_argument("--status", type=Path)
    args = parser.parse_args(argv)
    cancelled = False
    def stop(signum, frame):
        nonlocal cancelled
        cancelled = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def on_progress(done, total):
        check_cancel(lambda: cancelled)
        status({"state": "running", "done": done, "total": total})

    def status(value):
        if args.status:
            write_json(args.status, value)
        print(json.dumps(value, ensure_ascii=False), flush=True)

    try:
        if args.action == "inspect":
            result = inspect_imzml(args.source)
        elif args.action == "import":
            if not args.output:
                parser.error("import requires an output .parquet path")
            if not args.registration_spec or not args.registration_spec.is_file():
                parser.error("import requires --registration-spec for all sections")
            registration = json.loads(args.registration_spec.read_text(encoding="utf-8"))
            result = checked_import(
                args.source, args.output, registration=registration,
                alignment_ppm=args.alignment_ppm, progress=on_progress,
                cancel=lambda: cancelled,
                memory_budget_mb=int(os.environ.get("IMZML_BLOCK_MB", "64")),
            )
        else:
            if not args.output:
                parser.error("export requires an output .zip path")
            ids = [int(s) for s in args.pixel_ids.split(",")] if args.pixel_ids else None
            result = export_imzml(args.source, args.output, pixel_ids=ids,
                                  progress=on_progress)
        check_cancel(lambda: cancelled)
        status({"state": "done", "result": result})
        return 0
    except PreparationCancelled as exc:
        status({"state": "stopped", "error": str(exc)})
        return 130
    except Exception as exc:
        status({"state": "error", "error": str(exc)})
        traceback.print_exc()
        return 2


if __name__ == "__main__":
    sys.exit(main())
