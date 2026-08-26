"""データ出力の「m/z 一覧」表（ver62.0）。

Dash に依存させないのは単体テストのためで、`export_options.py` /
`export_aggregate.py` / `export_transform.py` と同じ方針。

---

## なぜ独立した表なのか

データ出力の本体は「1 行 = 1 スポット」で、m/z は**列名**・強度は**値**という形。
そのため「このデータにどの m/z が入っているか知りたいだけ」という用途に答えられない。
実データは 4,566 m/z × 203,078 spot あるので、m/z の顔ぶれを確認するためだけに
数 GB のファイルを出す羽目になっていた。

そこで m/z を行にした別の表を出せるようにする。1 行 = 1 m/z なので数千行で済む。

「m/z 列と intensity 列を並べた縦持ち」は採らない。1 ピクセル単位だと
203,078 × 4,566 ≒ 9.3 億行になり、どの形式でも実用にならないため。

## 注釈が無くても表は出す

化合物名の注釈はサイドカー `*_feature_annotations.parquet` から引くが、
**サイドカーが無くても m/z 一覧は出す**（注釈列が空欄になるだけ）。
注釈を登録していないデータで「m/z が 1 つも出ない」のでは、機能そのものが使えない。
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from app.services.export_options import META_COLUMNS

logger = logging.getLogger(__name__)

# サイドカーの m/z と列名から抜いた m/z を突き合わせる許容差 (Da)。
#
# `interactive_data_export._apply_feature_annotation_columns` が列名のリネームで
# 使っている値と**同一でなければならない**。ここがずれると、強度行列の列見出しに
# 付いた化合物名と、この一覧表の化合物名が食い違う（同じ m/z に別の名前が付く）。
MATCH_TOL_DA = 0.005

# 出力する列。サイドカーに無い列は空欄で残す（列の有無が入力で変わらないように）。
OUTPUT_COLUMNS = ["mz", "列名", "compound", "adduct", "formula", "ppm",
                  "lipid_class", "database"]

# うち数値として扱う列。ここを文字列の空欄で初期化すると、注釈を流し込むときに
# 「文字列 dtype の列に float を入れた」で落ちる（実際に落ちた）。
# 欠測は NaN のままにする。Excel でも parquet でも空欄として扱われ、
# 0 と紛れない（ppm=0 は「ずれが無い」という実在の値）。
NUMERIC_COLUMNS = ("ppm",)

# xlsx に足すシート名。Excel のシート名に使えない文字を避ける。
SHEET_NAME = "m_z"


def mz_columns(df_columns) -> list:
    """parquet の列から m/z 列（＝強度列）を抜き出す。

    `id/x/y/annotation` 以外が m/z 列。`export_options.intensity_columns` と違い、
    こちらはアプリが後から足す列が付く**前**の parquet を見るので、
    クラスタ列名を渡す必要がない。
    """
    return [c for c in df_columns if c not in META_COLUMNS]


def build_mz_list(df_columns, sidecar_path=None) -> pd.DataFrame:
    """m/z 一覧表を作る。1 行 = 1 m/z。

    df_columns: 変換済み parquet の列名（`_apply_feature_annotation_columns` で
        化合物名へリネームした**後**の列名を渡す。そうすると `列名` が実際の
        出力の見出しと文字列一致し、強度行列と突き合わせられる）。
    sidecar_path: `*_feature_annotations.parquet`。None / 読めない場合は注釈列が空欄。

    Returns: OUTPUT_COLUMNS の DataFrame（m/z 昇順）。
    """
    from app.utils.deg_utils import extract_mz_numeric

    cols = mz_columns(df_columns)
    rows = []
    for col in cols:
        mz = extract_mz_numeric(col)
        # extract_mz_numeric は認識できないと inf を返す。列名から m/z を読めない
        # ものは m/z 列ではないので落とす（列の取り違えを防ぐ）。
        if mz is None or not np.isfinite(mz):
            logger.debug("[mzlist] m/z を読めない列を除外: %s", col)
            continue
        rows.append({"mz": float(mz), "列名": str(col)})

    out = pd.DataFrame(rows, columns=["mz", "列名"])
    for c in OUTPUT_COLUMNS:
        if c in out.columns:
            continue
        if c in NUMERIC_COLUMNS:
            out[c] = pd.Series(np.nan, index=out.index, dtype="float64")
        else:
            # object dtype にしておく。pandas 3 の既定 str dtype だと
            # 後から数値や欠測を入れたときに TypeError で落ちる。
            out[c] = pd.Series([""] * len(out), index=out.index, dtype=object)
    out = out[OUTPUT_COLUMNS]
    if out.empty:
        return out

    side = _read_sidecar(sidecar_path)
    if side is not None and not side.empty:
        out = _attach_annotations(out, side)

    return out.sort_values("mz").reset_index(drop=True)


def _read_sidecar(sidecar_path) -> "pd.DataFrame | None":
    """サイドカーを読む。読めなければ None（注釈なしで一覧だけ出す）。"""
    if not sidecar_path:
        return None
    p = Path(sidecar_path)
    if not p.exists():
        return None
    try:
        side = pd.read_parquet(p)
    except Exception as e:  # noqa: BLE001 — 壊れていても一覧は出す
        logger.warning("[mzlist] サイドカーを読めませんでした（注釈なしで出力）: %s", e)
        return None
    if "mz" not in side.columns:
        logger.warning("[mzlist] サイドカーに mz 列がありません（注釈なしで出力）")
        return None
    return side


def _attach_annotations(out: pd.DataFrame, side: pd.DataFrame) -> pd.DataFrame:
    """最近傍の m/z（許容 MATCH_TOL_DA）でサイドカーの注釈を割り当てる。

    許容外は空欄のまま。近い順に 1 件だけ採り、無理に埋めない
    （間違った化合物名が付くのは、空欄より悪い）。
    """
    side_mz = pd.to_numeric(side["mz"], errors="coerce").to_numpy(dtype=float)
    ok = np.isfinite(side_mz)
    if not ok.any():
        return out
    side = side.loc[ok].reset_index(drop=True)
    side_mz = side_mz[ok]

    order = np.argsort(side_mz, kind="mergesort")
    sorted_mz = side_mz[order]

    target = out["mz"].to_numpy(dtype=float)
    pos = np.searchsorted(sorted_mz, target)
    best = np.full(target.shape, -1, dtype=int)
    best_d = np.full(target.shape, np.inf)
    # 挿入位置の左右だけ見れば最近傍が決まる（ソート済みのため）。
    for cand in (pos - 1, pos):
        valid = (cand >= 0) & (cand < sorted_mz.size)
        idx = np.clip(cand, 0, max(sorted_mz.size - 1, 0))
        d = np.where(valid, np.abs(sorted_mz[idx] - target), np.inf)
        take = d < best_d
        best_d = np.where(take, d, best_d)
        best = np.where(take, idx, best)

    hit = np.isfinite(best_d) & (best_d <= MATCH_TOL_DA) & (best >= 0)
    if not hit.any():
        return out

    src_rows = order[best[hit]]
    for col in ("compound", "adduct", "formula", "ppm", "lipid_class", "database"):
        if col not in side.columns:
            continue
        vals = side[col].to_numpy()[src_rows]
        out.loc[hit, col] = pd.Series(vals, index=out.index[hit])
    return out
