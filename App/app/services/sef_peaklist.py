# =============================================================================
# MSI Analysis Application - SCiLS `.sef` (feature list, JSON) の読み取り
# =============================================================================
# SCiLS が書き出す `.sef` は JSON で、peak-list CSV と**同じ情報**を持つ:
#
#   CSV の列                        .sef での持ち方
#   ---------------------------     ----------------------------------------
#   m/z                             (lower + upper) / 2
#   Interval Width (+/- Da)         (upper - lower) / 2
#   Color                           color
#   Name                            name
#   Intensity [Regions]             無し（元々 `intensity=UNAVAILABLE`）
#
# 形:
#   {"version": "2",
#    "peaklist": {"metaInformation": {"numberOfIntervals": "273"},
#                 "intervals": [{"lower": .., "upper": .., "name": "..",
#                                "color": "#999999"}, ...]}}
#
# ★ ver63.0: `.sef` は拡張子で弾かれ、アプリからは**存在しないのと同じ**だった
#   (`auto_detect_file_roles` は `.csv` しか走査せず、後付け UI も `accept=".csv"`)。
#   利用者は毎回手で CSV へ直す必要があった。
#
# ★ ver63.0: ただし「そのまま (m/z, Name) として流す」ことは**できない**。
#   `name` は CSV と**文法が違う**ためで、`peak_annotation.parse_scils_name` は
#   位置区切り (`化合物名 | 分類 | DB | [M+H]+ | 0.85ppm | formula=…`) を前提に
#   読むのに対し、`.sef` は key=value 主体
#   (`… | adduct=[M+H]+ | delta=3.85ppm | best_name_audit=Pyridine | …`) で書く。
#   素通しすると**例外は一切出ないまま**、実ファイル 273 件で
#     - adduct   0 / 273 （`adduct=` は key=value なので素フィールド探索に掛からない）
#     - ppm      0 / 273 （`delta=3.85ppm` も同様）
#     - 化合物名 先頭フィールドの内部符号 `FORMULA_ADDUCT_CANDIDATE C5H5N [M+H]+`
#                （本当の名前は `best_name_audit=Pyridine` の側にある）
#   になる。しかもこの名前は列名 → R の rowname → 画面・CSV・PPTX・PNG まで
#   伝播するので、ver55.0 が塞いだ「黙って捏造アノテーションが付く」のと同型の
#   事故になる。そこで**入口で文法を正規化してから**既存経路へ渡す。
# =============================================================================

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("msi.sef_peaklist")

# 既知の `.sef` スキーマ版。異なっていても**止めない**（読める限り読む）が、
# 黙って違う形を読むと原因追跡ができないので警告だけは必ず出す。
_KNOWN_VERSIONS = ("2",)

# `.sef` 方言かどうかの判定に使うキー。どちらか一方でもあれば key=value 方言。
# ★ ver63.0: 「`.sef` だから必ず変換する」にはしない。SCiLS 側の書式が変わって
#   CSV と同じ Name が入ってきたときに、正規化が**逆に**壊すため。
#   実際 CSV 方言をこの判定に掛けると passthrough になり、位置文法がそのまま
#   効いて分類・DB まで復元される（テスト `test_passthrough_scils_dialect`）。
_SEF_DIALECT_KEYS = ("adduct", "best_name_audit")

# 正規化で**位置フィールドへ昇格**させるキー。昇格させた分は key=value 側から
# 取り除く（同じ情報が二重に出ると読み手が混乱するため）。
_PROMOTED_KEYS = ("adduct", "delta", "best_name_audit")


def _split_fields(name: str) -> list[str]:
    return [p.strip() for p in str(name).split("|")]


def _key_values(fields: list[str]) -> dict:
    """`key=value` フィールドだけを dict にする（最初の `=` で分割）。

    値に `=` を含む場合があるので `partition` を使う（`adduct_family=…;adducts=…` 等）。
    """
    out: dict = {}
    for f in fields:
        if "=" not in f:
            continue
        k, _, v = f.partition("=")
        out[k.strip()] = v.strip()
    return out


def is_sef_dialect(name) -> bool:
    """`name` が `.sef` の key=value 方言かどうか。"""
    kv = _key_values(_split_fields(name)[1:])
    return any(k in kv for k in _SEF_DIALECT_KEYS)


def normalize_sef_name(name) -> str:
    """`.sef` の `name` を SCiLS peak-list `Name` の位置文法へ正規化する。

    `<化合物名> | <adduct> | <ppm> | <残りの key=value> | <残りの素フィールド>`

    ★ ver63.0: 分類 (lipid_class) と DB 名は**入れない**。`.sef` はこの 2 つを
      持たないので、それらしい値を作ると「指定していない情報が付いている」状態に
      なる（ver55.0 が Spot ファイル名からの領域ラベル捏造を塞いだのと同じ理由）。
      `parse_scils_name` の位置判定は「化合物名と adduct の間の非 key=value」を
      分類/DB とみなすので、間を空にすれば両方 None のまま adduct と ppm だけが
      正しく取れる（実測: 273/273）。

    既に SCiLS 文法なら**無変換で返す**。
    """
    raw = "" if name is None else str(name).strip()
    if not raw:
        return ""
    fields = _split_fields(raw)
    rest = fields[1:]
    kv = _key_values(rest)
    if not any(k in kv for k in _SEF_DIALECT_KEYS):
        return raw                      # CSV と同じ書き方 → そのまま通す

    # 先頭 = 本当の化合物名。`best_name_audit` が空なら元の先頭フィールドへ戻す
    # （内部符号でも、名前が消えるよりは辿れる方がよい）。
    #
    # 値に `|` が入っていた場合は上の `_split_fields` の時点で既に切れており、
    # ここへは前半しか来ない。これはパイプ文法そのものの制約で CSV 側も同じ
    # （`_read_peaklist` が復元できるのは Name 列**内部の CSV 区切り**だけ）。
    # 名前に `|` を含む peak-list は SCiLS 側で作れないため実害は無いが、
    # 「ここで落ちているのではない」ことが分かるよう明記しておく。
    compound = ((kv.get("best_name_audit") or "").strip() or fields[0]).strip()

    adduct = (kv.get("adduct") or "").strip()
    delta = (kv.get("delta") or "").strip()

    head: list[str] = [compound]
    if adduct:
        head.append(adduct)
        # ppm は **adduct より後ろ**の素フィールドしか見られないので、
        # adduct が無いときに昇格させても拾われない。その場合は下の
        # `keep` 側に `delta=` のまま残して情報を落とさない。
        if delta:
            head.append(delta)

    dropped = set(_PROMOTED_KEYS) if adduct else {"adduct", "best_name_audit"}
    keep = [f for f in rest if "=" in f and f.partition("=")[0].strip() not in dropped]
    bare = [f for f in rest if "=" not in f and f]   # PUTATIVE_MS1 / NOT_IDENTIFIED 等
    return " | ".join(head + keep + bare)


def read_sef_peaklist(path, *, return_skipped: bool = False):
    """`.sef` から (m/z 配列, Name 配列) を返す。

    戻り値の契約は `scils_converter._read_peaklist` と**同一**にしてある
    （`return_skipped=True` で 3-tuple、`skipped` のキーも同じ）。呼び出し側は
    拡張子だけで分岐すればよく、`peaklist_skip_message` もそのまま使える。

    m/z は `(lower + upper) / 2`。`.sef` は中心値を持たず範囲でしか書かないため。

    ★ ver63.0: 壊れた interval は**黙って捨てない**。捨てた行の化合物名は
      サイドカーに載らず、変換後の成果物からは復元できない（元ファイルを直して
      再登録するしかない）。CSV 側が ver52.3 で同じ理由から内訳を返すように
      なっているので、キーを揃えて同じ文言で報告できるようにする。
    """
    p = Path(path)
    with p.open("r", encoding="utf-8", errors="replace") as fh:
        try:
            doc = json.load(fh)
        except json.JSONDecodeError as e:
            raise ValueError(f"`.sef` を JSON として読めません: {p.name} ({e})") from e

    if not isinstance(doc, dict):
        raise ValueError(f"`.sef` の中身がオブジェクトではありません: {p.name}")

    version = str(doc.get("version", "")).strip()
    if version and version not in _KNOWN_VERSIONS:
        logger.warning("`.sef` の未知のバージョン: %s (%s) — 読み取りは続行します",
                       version, p.name)

    peaklist = doc.get("peaklist")
    if not isinstance(peaklist, dict):
        raise ValueError(f"`.sef` に peaklist がありません: {p.name}")
    intervals = peaklist.get("intervals")
    if not isinstance(intervals, list):
        raise ValueError(f"`.sef` に peaklist.intervals がありません: {p.name}")

    mz_list: list[float] = []
    name_list: list[str] = []
    # キーは CSV 版と共通にする（`peaklist_skip_message` が同じ文言を出せる）。
    #   short_row      … lower/upper/name が揃っていない
    #   non_numeric_mz … lower/upper を数値にできない
    #   non_finite_mz  … 中心が NaN / inf
    skipped = {"short_row": 0, "non_numeric_mz": 0, "non_finite_mz": 0}
    for iv in intervals:
        if not isinstance(iv, dict) or "lower" not in iv or "upper" not in iv:
            skipped["short_row"] += 1
            continue
        try:
            lower = float(iv["lower"])
            upper = float(iv["upper"])
        except (TypeError, ValueError):
            skipped["non_numeric_mz"] += 1
            continue
        mz = (lower + upper) / 2.0
        # ★ ver63.0: `float("nan")` / `float("inf")` は例外を出さないので、
        #   ここで弾かないと後段の最近傍探索で（比較が常に偽になり）どの feature
        #   にも当たらず消える。CSV 側 (ver52.3) と同じ扱いにして数える。
        if not np.isfinite(mz):
            skipped["non_finite_mz"] += 1
            continue
        mz_list.append(mz)
        name_list.append(normalize_sef_name(iv.get("name", "")))

    arr = np.asarray(mz_list, dtype=float)
    n_declared = peaklist.get("metaInformation", {}).get("numberOfIntervals")
    logger.info("`.sef` 読込: %s — %d ピーク (宣言: %s)", p.name, len(arr), n_declared)
    if return_skipped:
        return arr, name_list, skipped
    return arr, name_list


def looks_like_sef(path) -> bool:
    """`.sef` として読める形かどうか（フォルダ走査時の妥当性確認用）。

    中身を JSON として開き `peaklist.intervals` があるかまで見る。拡張子だけで
    判断すると、無関係な `.sef` を peak-list として拾って変換ごと落としかねない。
    """
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as fh:
            doc = json.load(fh)
        return isinstance(doc.get("peaklist", {}).get("intervals"), list)
    except Exception:
        return False
