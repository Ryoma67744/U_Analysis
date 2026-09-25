#!/usr/bin/env python3
"""★ ver70.0: imzML 準備と R 解析を同じ親プロセスで管理する CLI。"""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.analysis_pipeline import run_pipeline


def main():
    if len(sys.argv) != 2:
        print("usage: run_analysis_pipeline.py REQUEST_JSON", file=sys.stderr)
        return 2
    with Path(sys.argv[1]).open(encoding="utf-8") as fh:
        payload = json.load(fh)
    return run_pipeline(payload)


if __name__ == "__main__":
    sys.exit(main())
