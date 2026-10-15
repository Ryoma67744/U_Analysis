"""Translate configured host paths at UI boundaries without relocating assets.

These aliases are a presentation contract, not a filesystem permission boundary.
No existence checks or basename searches are performed: output files may not exist
yet, and Windows container/local roots can be tested from another operating system.
"""
from __future__ import annotations

import ntpath
import os
import posixpath
from pathlib import PurePosixPath, PureWindowsPath


_ROOT_SETTINGS = (
    ("TIMS_DATA_DIR", "MSI_HOST_TIMS_DATA_DIR"),
    ("DESI_DATA_DIR", "MSI_HOST_DESI_DATA_DIR"),
    ("OUTPUT_DATA_DIR", "MSI_HOST_OUTPUT_DATA_DIR"),
    ("IMZML_CACHE_DIR", "MSI_HOST_IMZML_ASSETS_DIR"),
)


def _absolute(value):
    """Return (flavour, normalized path, parts), or None for relative paths."""
    value = os.fspath(value)
    if not isinstance(value, str) or not value or "\x00" in value:
        return None
    if PureWindowsPath(value).is_absolute() and not value.startswith("/"):
        normalized = ntpath.normpath(value)
        return "windows", normalized, PureWindowsPath(normalized).parts
    if value.startswith("/"):
        normalized = posixpath.normpath("/" + value.lstrip("/"))
        return "posix", normalized, PurePosixPath(normalized).parts
    return None


def _relative(value, root):
    current, base = _absolute(value), _absolute(root)
    if current is None or base is None or current[0] != base[0]:
        return None
    current_parts, base_parts = current[2], base[2]
    if current[0] == "windows":
        matches = tuple(p.casefold() for p in current_parts[:len(base_parts)]) == tuple(
            p.casefold() for p in base_parts)
    else:
        matches = current_parts[:len(base_parts)] == base_parts
    return current_parts[len(base_parts):] if matches else None


def _join(root, tail):
    parsed = _absolute(root)
    kind = PureWindowsPath if parsed[0] == "windows" else PurePosixPath
    return str(kind(parsed[1]).joinpath(*tail))


def _same_path(first, second):
    return _relative(first, second) == () and _relative(second, first) == ()


def get_path_mappings() -> list[tuple[str, str]]:
    """Return unambiguous (internal root, public root) pairs from configuration."""
    from app import config
    candidates = []
    for setting, environment in _ROOT_SETTINGS:
        public = os.environ.get(environment, "").strip()
        internal = str(getattr(config, setting))
        inside, outside = _absolute(internal), _absolute(public)
        if inside is not None and outside is not None:
            candidates.append((inside[1], outside[1]))
    # An identical root cannot name different locations in either direction.
    # Ignore conflicting definitions rather than silently choosing one of them.
    result = []
    for internal, public in candidates:
        conflict = any(
            (_same_path(internal, other_internal) and not _same_path(public, other_public))
            or (_same_path(public, other_public) and not _same_path(internal, other_internal))
            for other_internal, other_public in candidates)
        duplicate = any(_same_path(internal, i) and _same_path(public, p) for i, p in result)
        if not conflict and not duplicate:
            result.append((internal, public))
    return result


def has_mappings() -> bool:
    return bool(get_path_mappings())


def _translate(value, *, outward):
    if value is None or value == "":
        return value
    raw = os.fspath(value)
    parsed = _absolute(raw)
    if parsed is None:
        return raw
    pairs = [(internal, public) if outward else (public, internal)
             for internal, public in get_path_mappings()]
    for source, destination in sorted(pairs, key=lambda pair: len(_absolute(pair[0])[2]), reverse=True):
        tail = _relative(parsed[1], source)
        if tail is not None:
            return _join(destination, tail)
    return raw


def to_display_path(value):
    return _translate(value, outward=True)


def to_internal_path(value):
    return _translate(value, outward=False)


def virtual_children(public_path):
    """List only configured branches of a public ancestor; never list host disk.

    Mapped roots and their descendants are real directories in the internal
    namespace, so their callers should use normal filesystem listing (None).
    """
    current = _absolute(public_path or "")
    if current is None:
        return None
    mappings = get_path_mappings()
    if any(_relative(current[1], public) is not None for _, public in mappings):
        return None
    children = {}
    for _, public in mappings:
        tail = _relative(public, current[1])
        if tail:
            path = _join(current[1], tail[:1])
            children[path] = {"name": tail[0], "path": path, "is_dir": True, "icon": "📁"}
    return sorted(children.values(), key=lambda row: row["name"].casefold()) if children else None
