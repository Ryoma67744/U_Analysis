"""DESI `.txt` のヘッダを解釈する共通ロジック（Dash 非依存の純ロジック）。

ヘッダ行数は固定ではなく、実際に 3 種類が流通している:

===== ==================================================== ==========================
行数   レイアウト                                            出どころ
===== ==================================================== ==========================
5      空 / 化合物名 / 代謝物番号 / Q1 / Q3                  装置から出た実データ
4      空 / 代謝物番号 / 化合物名 / 空                       `desi_converter` の組み替え
4      空 / 代謝物番号 / Q1 / Q3                             化合物名を持たない形
===== ==================================================== ==========================

★ ver55.2: 従来は Python (`skip 4 行`) も R (`readLines(n=4)` / `fread(skip=4)`) も
  **4 行決め打ち**だった。実データは 5 行ヘッダなので、

  (a) 特徴量名が「代謝物番号-Q1」（例 ``1-90.0477``）という無意味な連結になり、
  (b) 化合物名は読み込まれた直後に捨てられ、
  (c) Q3 の行がデータ 1 行目として読まれて座標 NA の幽霊ピクセルが混入する

  という 3 つが同時に起きていた。データ開始行は「列1 が整数の PixelID・列2/3 が数値」で
  判定する（R 側 `is_data_line` / `DESI_RDS_ClusterFilter_ver3.R` と同一基準）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger("msi.desi_header")

# 化合物名末尾の `_<数字>_<数字>` (CE/DP 等の測定パラメータ) を落とす。
#   POS_AA_Ala_24_6 -> POS_AA_Ala / Adenosine-POS_32_18 -> Adenosine-POS
_PARAM_SUFFIX_RE = re.compile(r"_[0-9]+_[0-9]+$")

_MAX_PROBE_LINES = 12


@dataclass
class DesiHeader:
    """DESI `.txt` のヘッダ解釈結果。"""

    n_header: int
    compounds: list[str] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    q1: list[str] = field(default_factory=list)
    q3: list[str] = field(default_factory=list)
    feature_names: list[str] = field(default_factory=list)
    #: 修正前のコードが作っていた特徴量名（旧解析結果・保存済みリストとの突合用）
    legacy_feature_names: list[str] = field(default_factory=list)

    @property
    def has_compounds(self) -> bool:
        return bool(self.compounds)


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.rstrip("\r\n").split("\t")]


def _tail_cells(line: str) -> list[str]:
    """4 列目以降の非空セル。

    ★ 空セルを詰める方式は使えない。実データの化合物名行は 1 列目に測定条件由来の
      余計な値（例 ``5``）が入っており、それを拾うと特徴量数と 1 個ズレる。
    """
    cs = _cells(line)
    return [c for c in cs[3:] if c]


def is_data_line(line: str) -> bool:
    """データ行か（列1 が整数の PixelID・列2/3 が数値）。"""
    cs = _cells(line)
    if len(cs) < 3:
        return False
    a, b, c = cs[0], cs[1], cs[2]
    if not a or "." in a:
        return False
    try:
        int(a)
        float(b)
        float(c)
    except ValueError:
        return False
    return True


def strip_param_suffix(name: str) -> str:
    """化合物名末尾の `_<数字>_<数字>` を落とす。"""
    return _PARAM_SUFFIX_RE.sub("", name.strip())


def _legacy_names(lines: list[str]) -> list[str]:
    """修正前のコードが作っていた特徴量名を再現する。

    旧 `read_desi_data` は行数に関わらず 3 行目を pre、4 行目を post として
    `paste(pre, post, sep="-")` していた（空セルは詰める）。
    """
    if len(lines) < 4:
        return []

    def toks(s: str) -> list[str]:
        return [c for c in (x.strip() for x in s.split("\t")) if c]

    pre, post = toks(lines[2]), toks(lines[3])
    if not post:
        return pre
    n = min(len(pre), len(post))
    return [f"{pre[i]}-{post[i]}" for i in range(n)]


def read_desi_header(path) -> Optional[DesiHeader]:
    """DESI `.txt` の先頭を読んでヘッダを解釈する。判定できなければ None。"""
    p = Path(path)
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            lines = []
            for _ in range(_MAX_PROBE_LINES):
                ln = fh.readline()
                if not ln:
                    break
                lines.append(ln)
    except OSError as e:
        logger.warning("DESI ヘッダ読み込みに失敗 (%s): %s", p.name, e)
        return None

    if len(lines) < 5:
        return None

    data_at = next((i for i, ln in enumerate(lines) if is_data_line(ln)), None)
    if data_at is None or data_at < 1:
        return None

    n_header = data_at
    hrows = [_tail_cells(ln) for ln in lines[:n_header]]

    compounds: list[str] = []
    numbers: list[str] = []
    q1: list[str] = []
    q3: list[str] = []
    if n_header >= 5:
        # 空 / 化合物名 / 代謝物番号 / Q1 / Q3（末尾から数えるので先頭の空行数に依らない）
        compounds, numbers, q1, q3 = (
            hrows[n_header - 4], hrows[n_header - 3], hrows[n_header - 2], hrows[n_header - 1],
        )
    elif n_header == 4 and not hrows[3]:
        numbers, compounds = hrows[1], hrows[2]
    elif n_header == 4:
        numbers, q1, q3 = hrows[1], hrows[2], hrows[3]
    else:
        logger.warning("DESI ヘッダの形式を判定できません (%d 行): %s", n_header, p.name)
        return None

    transitions: list[str] = []
    if q1 and len(q1) == len(q3):
        transitions = [f"{a}-{b}" for a, b in zip(q1, q3)]
    elif q1:
        transitions = list(q1)

    if compounds:
        cname = [strip_param_suffix(c) for c in compounds]
        if transitions and len(transitions) == len(cname):
            names = [f"{c} ({t})" for c, t in zip(cname, transitions)]
        else:
            names = cname
    else:
        names = transitions

    return DesiHeader(
        n_header=n_header,
        compounds=compounds,
        numbers=numbers,
        q1=q1,
        q3=q3,
        feature_names=names,
        legacy_feature_names=_legacy_names(lines),
    )


def legacy_alias_map(path) -> dict[str, str]:
    """`{修正前の特徴量名: 修正後の特徴量名}` を返す。

    保存済みの Feature リスト / ブックマークが旧名で書かれていても、同じ特徴量に
    たどり着けるようにするための対応表。ヘッダから決定的に作るので推測を含まない。
    名前が変わらない構成（化合物名を持たない 4 行ヘッダなど）では空を返す。
    """
    hdr = read_desi_header(path)
    if hdr is None:
        return {}
    n = min(len(hdr.legacy_feature_names), len(hdr.feature_names))
    return {
        old: new
        for old, new in zip(hdr.legacy_feature_names[:n], hdr.feature_names[:n])
        if old and new and old != new
    }
