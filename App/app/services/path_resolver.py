# =============================================================================
# MSI Analysis Application - Path Resolver
# 生データパスの自動補正ユーティリティ
# =============================================================================
#
# projects.json に絶対パスで保存された生データ参照を、
# 別マシン/アップデート後の環境でも解決可能にするための補正ロジック。
#
# 動作:
#   1. 破損パス (例: Windows Dropbox パス) の末尾フォルダ名を抽出
#   2. DESI_DATA_CANDIDATES / TIMS_DATA_CANDIDATES を順に検索
#   3. 同名フォルダを発見したら新しい絶対パスを返す
# =============================================================================

from __future__ import annotations

import logging
from pathlib import Path
from typing import Literal, Optional

from app.config import (
    DESI_DATA_CANDIDATES,
    TIMS_DATA_CANDIDATES,
    OUTPUT_DATA_CANDIDATES,
)

logger = logging.getLogger("msi.path_resolver")

Modality = Literal["desi", "tims", "auto"]

# projects.json 内で生データを参照しているフィールド
DATA_PATH_FIELDS = ("data_folder", "output_dir", "last_result_dir")
FILE_PATH_FIELDS = ("annotation_path", "annotation_csv_path")

# 探索深度 (性能と汎用性のトレードオフ)
_MAX_SCAN_DEPTH = 5


def _split_path(raw: str) -> list[str]:
    """Windows / POSIX 両対応でパス末尾の要素列を抽出"""
    if not raw:
        return []
    # Windows 区切りと POSIX 区切りの両方を扱う
    normalized = raw.replace("\\", "/")
    parts = [p for p in normalized.split("/") if p and ":" not in p]
    return parts


def _candidates_for(modality: Modality, field: Optional[str] = None) -> list[Path]:
    """探索候補リストを返す。

    field が ``output_dir`` / ``last_result_dir`` の場合は OUTPUT_DATA_CANDIDATES を
    先頭に置き、出力フォルダ → 生データフォルダの順で検索する。
    """
    output_first: list[Path] = []
    if field in ("output_dir", "last_result_dir"):
        output_first = list(OUTPUT_DATA_CANDIDATES)
    if modality == "desi":
        return output_first + list(DESI_DATA_CANDIDATES)
    if modality == "tims":
        return output_first + list(TIMS_DATA_CANDIDATES)
    # auto: DESI / TIMS 両方を試す
    return output_first + list(DESI_DATA_CANDIDATES) + list(TIMS_DATA_CANDIDATES)


def _infer_modality(path_str: str) -> Modality:
    """パス文字列から DESI / TIMS を推測"""
    lower = path_str.lower()
    if "/tims" in lower or "\\tims" in lower:
        return "tims"
    if "/desi" in lower or "\\desi" in lower:
        return "desi"
    return "auto"


def _collect_by_tail(tail: str, root: Path, is_file: bool) -> list[Path]:
    """root 配下で末尾名 (tail) に一致するパスを **すべて** 集める。

    ★ ver51.9 / C-5: 従来は最初の 1 件を返していた。`rglob` の順序は環境依存で、
      `Data` / `output` / `RDS_Files` のような一般名は **どのプロジェクトにも
      ある**ため、別プロジェクトのフォルダへ貼り替わっていた。パスは補正済みと
      して表示されるので利用者は気づけない。呼び出し側が曖昧さを判断できるよう
      全件返す。

    ★ 併せて `_MAX_SCAN_DEPTH` を実際に効かせる（宣言だけで未使用だった）。
      巨大な共有ドライブで補正のたびに全走査になるのを防ぐ。
    """
    if not root.is_dir():
        return []
    out: list[Path] = []
    try:
        for depth in range(1, _MAX_SCAN_DEPTH + 1):
            pattern = "/".join(["*"] * (depth - 1) + [tail]) if depth > 1 else tail
            for candidate in root.glob(pattern):
                if is_file and candidate.is_file():
                    out.append(candidate)
                elif not is_file and candidate.is_dir():
                    out.append(candidate)
    except (PermissionError, OSError) as exc:
        logger.debug("探索中にエラー %s: %s", root, exc)
    return out


def _tail_match_score(candidate: Path, parts: list[str]) -> int:
    """壊れたパスの末尾から何段一致するかを返す (ver51.9 / C-5)。

    `.../ProjectB/Data` を探すとき、`<root>/ProjectB/Data` は 2 段一致、
    `<root>/ProjectA/Data` は 1 段一致。深く一致する方が確からしい。
    """
    cand = [p for p in candidate.parts if p not in ("/", "\\")]
    score = 0
    for a, b in zip(reversed(cand), reversed(parts)):
        if a.lower() != b.lower():
            break
        score += 1
    return score


def _resolve_unique(parts: list[str], candidates: list[Path],
                    is_file: bool, broken_path: str) -> Optional[Path]:
    """候補ルート群から **一意に定まるときだけ** 解決する (ver51.9 / C-5)。

    末尾からの一致段数で採点し、最良スコアの候補が 1 つに決まらなければ
    補正しない。「黙って別プロジェクトを指す」より「未解決として警告する」
    ほうが安全（DEG の手法スコープ ver51.9 A-2 と同じ方針）。
    """
    tail = parts[-1]
    found: list[Path] = []
    for root in candidates:
        found.extend(_collect_by_tail(tail, root, is_file))
    # 同じ実体を複数ルートから拾った場合は 1 つに畳む
    uniq = {p.resolve(): p for p in found}
    found = list(uniq.values())
    if not found:
        return None
    if len(found) == 1:
        logger.info("パス補正: %s → %s", broken_path, found[0])
        return found[0]

    scored = [(_tail_match_score(p, parts), p) for p in found]
    best = max(s for s, _ in scored)
    winners = [p for s, p in scored if s == best]
    if len(winners) == 1:
        logger.info("パス補正 (%d 段一致): %s → %s", best, broken_path, winners[0])
        return winners[0]

    logger.warning(
        "パス補正を見送り（候補が %d 件あり一意に定まらない）: %s → %s",
        len(winners), broken_path, [str(p) for p in winners[:5]])
    return None


def resolve_data_path(
    broken_path: str,
    modality: Modality = "auto",
    is_file: bool = False,
    field: Optional[str] = None,
) -> Optional[Path]:
    """破損した絶対パスを DATA_CANDIDATES 配下で再解決する。

    Parameters
    ----------
    broken_path : str
        projects.json に保存されていた絶対パス (別マシン由来の可能性あり)
    modality : "desi" | "tims" | "auto"
        "auto" は DESI / TIMS 両方を探索
    is_file : bool
        True の場合はファイルを、False の場合はディレクトリを探す
    field : str, optional
        補正対象のフィールド名。``output_dir`` / ``last_result_dir`` の場合は
        OUTPUT_DATA_CANDIDATES を優先的に検索する。

    Returns
    -------
    Optional[Path]
        発見したパス。未発見時は None
    """
    if not broken_path:
        return None

    # 既に有効な絶対パスなら何もしない
    if Path(broken_path).exists():
        return Path(broken_path)

    if modality == "auto":
        modality = _infer_modality(broken_path)

    parts = _split_path(broken_path)
    if not parts:
        return None

    candidates = _candidates_for(modality, field)
    found = _resolve_unique(parts, candidates, is_file, broken_path)
    if found is not None:
        return found

    logger.warning("パス補正失敗: %s", broken_path)
    return None


def resolve_project_paths(sub_info: dict) -> tuple[dict, list[str]]:
    """サブプロジェクトのメタデータ内パスを一括補正。

    Parameters
    ----------
    sub_info : dict
        `_project_meta.json` 内の ``sub_project`` セクション

    Returns
    -------
    corrected : dict
        補正後の sub_info のコピー
    unresolved : list[str]
        解決できなかったパス (警告表示用)
    """
    corrected = dict(sub_info)
    unresolved: list[str] = []

    modality = _infer_modality(
        str(sub_info.get("data_folder", ""))
        or str(sub_info.get("ms_instrument", ""))
    )

    # last_analysis_settings 内のパスも補正
    settings = corrected.get("last_analysis_settings")
    if isinstance(settings, dict):
        new_settings = dict(settings)
        for field in DATA_PATH_FIELDS:
            value = new_settings.get(field, "")
            if not value:
                continue
            resolved = resolve_data_path(value, modality, is_file=False, field=field)
            if resolved:
                new_settings[field] = str(resolved)
            else:
                unresolved.append(f"{field}: {value}")

        for field in FILE_PATH_FIELDS:
            value = new_settings.get(field, "")
            if not value:
                continue
            resolved = resolve_data_path(value, modality, is_file=True, field=field)
            if resolved:
                new_settings[field] = str(resolved)
            else:
                unresolved.append(f"{field}: {value}")

        corrected["last_analysis_settings"] = new_settings

    # サブプロジェクト直下の data_folder / output_dir も補正
    for field in DATA_PATH_FIELDS:
        value = corrected.get(field, "")
        if not value:
            continue
        resolved = resolve_data_path(value, modality, is_file=False, field=field)
        if resolved:
            corrected[field] = str(resolved)
        elif field not in ("last_result_dir",):
            # last_result_dir は呼び出し側が found_dir で上書きするため除外
            unresolved.append(f"{field}: {value}")

    return corrected, unresolved
