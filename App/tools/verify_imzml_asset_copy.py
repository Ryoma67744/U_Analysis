"""Read-only full-tree copy comparison. This does NOT certify saved analyses.

Stop writers first. Compare all relative paths (including hidden files and empty
directories), sizes and SHA256. A missing asset on BOTH sides needs independent
saved-result diagnostics; equality alone is not a migration acceptance result.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import stat


def _identity(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def inventory(root):
    root = Path(root)
    if not stat.S_ISDIR(root.lstat().st_mode):
        raise ValueError(f"root must be a real directory: {root}")
    files, directories = {}, []
    def visit(directory):
        before = directory.stat()
        for path in sorted(directory.iterdir()):
            info = path.lstat()
            relative = path.relative_to(root).as_posix()
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
                visit(path)
            elif stat.S_ISREG(info.st_mode):
                if info.st_nlink != 1:
                    raise ValueError(f"hard link requires separate review: {relative}")
                flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                fd = os.open(path, flags)
                with os.fdopen(fd, "rb") as stream:
                    if _identity(os.fstat(stream.fileno())) != _identity(info):
                        raise ValueError(f"file changed before reading: {relative}")
                    digest = sha256()
                    while block := stream.read(4 * 1024 * 1024):
                        digest.update(block)
                    if _identity(os.fstat(stream.fileno())) != _identity(info):
                        raise ValueError(f"file changed while reading: {relative}")
                if _identity(path.lstat()) != _identity(info):
                    raise ValueError(f"file replaced while reading: {relative}")
                files[relative] = {"size": info.st_size, "sha256": digest.hexdigest()}
            else:
                raise ValueError(f"symlink/special file requires separate review: {relative}")
        if _identity(directory.stat()) != _identity(before):
            raise ValueError(f"directory changed while reading: {directory}")
    visit(root)
    if not files:
        raise ValueError("empty tree is not evidence of preserved conversion assets")
    return {"directories": directories, "files": files}


def compare(source, copy):
    source, copy = Path(source), Path(copy)
    a, b = source.resolve(), copy.resolve()
    if a == b or a in b.parents or b in a.parents:
        raise ValueError("source and copy must be separate, non-nested trees")
    original, duplicate = inventory(source), inventory(copy)
    left, right = original["files"], duplicate["files"]
    missing = sorted(set(left) - set(right))
    extra = sorted(set(right) - set(left))
    changed = sorted(name for name in left.keys() & right.keys() if left[name] != right[name])
    directories_equal = original["directories"] == duplicate["directories"]
    return {"schema": 1, "tree_equal": not (missing or extra or changed) and directories_equal,
            "saved_result_completeness_checked": False,
            "source": str(a), "copy": str(b), "file_count": len(left),
            "total_bytes": sum(item["size"] for item in left.values()),
            "missing": missing, "extra": extra, "changed": changed,
            "directories_equal": directories_equal, "source_inventory": original,
            "limitation": "Run saved-result asset diagnostics separately; assets absent on both sides are not detected by copy equality."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--copy", required=True)
    args = parser.parse_args(argv)
    try:
        report = compare(args.source, args.copy)
    except (OSError, ValueError) as exc:
        report = {"schema": 1, "tree_equal": False, "saved_result_completeness_checked": False,
                  "error": str(exc)}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["tree_equal"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
