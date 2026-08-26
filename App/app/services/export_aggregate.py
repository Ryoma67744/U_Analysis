"""データ出力のグループ平均集計（Dash 非依存）。

「1 ピクセル = 1 行」の出力を、切片 / 領域名(ROI) / クラスタ の組み合わせで
まとめて平均値にする。呼び出し側は `interactive_data_export._export_tims`。

Dash に依存させないのは単体テストのためで、`export_transform.py` /
`export_progress.py` と同じ方針。

---

## なぜ「全行を concat してから groupby」にしないのか

`_export_tims` は切片ごとに parquet を読んで `dfs_out` に貯め、最後に
`pd.concat` する。実データ規模（203,078 spot × 4,566 m/z, float32 で実体 3.7GB）
では concat の時点で実体がもう 1 部でき、そこから集計するとさらに増える。

そこで **ファイルごとに部分集計（n / 総和 / 二乗和）だけを持ち回り、最後に合成**する。
部分集計の大きさは「グループ数 × m/z 数」で、行数に依存しない。切片 8 枚 ×
クラスタ 20 個なら 160 行しかないので、全行を同時に持つ必要がなくなる。

グループが複数ファイルにまたがっても正しい（総和と個数を足してから割るため）。
「クラスタのみ」でまとめる場合は必ずまたがるので、これは必須の性質。

## なぜ強度列をブロックに切って処理するのか

二乗和には値の 2 乗が要る。4,566 列 × 203,078 行を一度に float64 化して 2 乗すると
それだけで 7.4GB × 2 になり、メモリを節約するどころか悪化する。
一度に扱う列数を「目標バイト数 / (行数 × 8)」から決めて、ピークを抑える。
`INGEST_BLOCK_MB`（取り込み側の同種の工夫）と同じ考え方。
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

# 強度列 1 ブロックあたりの目標バイト数。既定 256MB。
# 実測の根拠: 203,078 行のとき float64 で 1 列 = 1.6MB なので約 160 列/ブロック。
# 4,566 列なら 29 ブロックで、groupby の呼び出し回数として現実的な範囲に収まる。
_BLOCK_BYTES = int(os.environ.get("EXPORT_AGG_BLOCK_MB", 256)) * 1024 * 1024

# 出力列の接尾辞。呼び出し側とテストが参照する。
MEAN_SUFFIX = "_mean"
SD_SUFFIX = "_sd"
COUNT_COLUMN = "n"


def _block_size(n_rows: int) -> int:
    """1 ブロックで扱う強度列の数。最低 1 列は処理する。"""
    if n_rows <= 0:
        return 1
    return max(1, _BLOCK_BYTES // (n_rows * 8))


def _group_index(df: pd.DataFrame, group_cols: list) -> pd.Index:
    """グループキー列から Index / MultiIndex を作る。

    キー列は annotation / 領域名 / クラスタで、突合できなかった行は空文字が入る。
    NaN と空文字が別グループに割れると「未割当」が 2 種類に見えるため、文字列化して
    欠損は空文字に寄せる。
    """
    cols = {c: df[c].astype("object").where(df[c].notna(), "").astype(str)
            for c in group_cols}
    key_df = pd.DataFrame(cols, index=df.index)
    if len(group_cols) == 1:
        return pd.Index(key_df[group_cols[0]], name=group_cols[0])
    return pd.MultiIndex.from_frame(key_df)


def accumulate_partial(df: pd.DataFrame, group_cols: list,
                       value_cols: list) -> dict:
    """1 ファイル分の部分集計 `{n, sum, sumsq}` を返す。

    group_cols: グループキー列名（1 つ以上）。
    value_cols: 平均を取る列名（強度列）。

    値に NaN があると平均が静かにずれるため、その場で例外にする。
    変換済み parquet は密（信号が無いところは 0）なので通常は起こらない。
    「静かに間違うより、動かない方が安全」（interactive_callbacks.py と同じ方針）。
    """
    if not group_cols:
        raise ValueError(
            "集計キーが 1 つも指定されていません。"
            "切片 / 領域名 / クラスタ のいずれかを選んでください。")
    missing = [c for c in group_cols if c not in df.columns]
    if missing:
        raise ValueError(f"集計キーの列が出力に含まれていません: {missing}")

    idx = _group_index(df, group_cols)
    levels = list(range(idx.nlevels))

    n = pd.Series(1, index=idx, name=COUNT_COLUMN).groupby(level=levels).sum()

    if not value_cols:
        # 強度を出力しない選択のとき。件数だけ数えれば足りる。
        empty = pd.DataFrame(index=n.index)
        return {"n": n, "sum": empty, "sumsq": empty}

    n_rows = len(df)
    step = _block_size(n_rows)
    sums, sqs = [], []
    for i in range(0, len(value_cols), step):
        cols = value_cols[i:i + step]
        blk = df[cols].to_numpy(dtype="float64", copy=True)
        if np.isnan(blk).any():
            bad = [c for c, has in zip(cols, np.isnan(blk).any(axis=0)) if has]
            raise ValueError(
                "強度に欠損値(NaN)が含まれるため平均を計算できません: "
                f"{bad[:5]}{' ほか' if len(bad) > 5 else ''}。"
                "変換済み parquet が壊れている可能性があります。")
        block_df = pd.DataFrame(blk, columns=cols, index=idx)
        sums.append(block_df.groupby(level=levels).sum())
        block_df = pd.DataFrame(blk * blk, columns=cols, index=idx)
        sqs.append(block_df.groupby(level=levels).sum())
        del blk, block_df

    return {
        "n": n,
        "sum": pd.concat(sums, axis=1) if sums else pd.DataFrame(index=n.index),
        "sumsq": pd.concat(sqs, axis=1) if sqs else pd.DataFrame(index=n.index),
    }


def combine_partials(partials: list, group_cols: list) -> pd.DataFrame:
    """部分集計を合成して平均・SD・件数の表を返す。

    列: group_cols… / n / <列>_mean / <列>_sd

    SD は不偏分散（ddof=1）。n=1 のグループは NaN にする。ばらつきが不明なだけで
    「ばらつきが 0」ではないため、0 を書くと群間比較で誤読される。
    """
    parts = [p for p in partials if p is not None and len(p.get("n", [])) > 0]
    if not parts:
        return pd.DataFrame(columns=list(group_cols) + [COUNT_COLUMN])

    n = parts[0]["n"]
    for p in parts[1:]:
        n = n.add(p["n"], fill_value=0)
    n = n.astype("int64")

    total = parts[0]["sum"]
    total_sq = parts[0]["sumsq"]
    for p in parts[1:]:
        total = total.add(p["sum"], fill_value=0.0)
        total_sq = total_sq.add(p["sumsq"], fill_value=0.0)

    out = pd.DataFrame(index=n.index)
    out[COUNT_COLUMN] = n

    if not total.empty:
        nv = n.reindex(total.index).to_numpy(dtype="float64")[:, None]
        mean = total.to_numpy(dtype="float64") / nv
        # 不偏分散 = (Σx² - n·mean²) / (n-1)。浮動小数の丸めで僅かに負へ振れることが
        # あるので 0 で下限を切る（sqrt が NaN になるのを防ぐ）。
        with np.errstate(invalid="ignore", divide="ignore"):
            var = (total_sq.to_numpy(dtype="float64") - nv * mean * mean) / (nv - 1.0)
        var = np.clip(var, 0.0, None)
        sd = np.sqrt(var)
        sd[np.repeat(nv <= 1, sd.shape[1], axis=1)] = np.nan

        mean_df = pd.DataFrame(mean, index=total.index,
                               columns=[f"{c}{MEAN_SUFFIX}" for c in total.columns])
        sd_df = pd.DataFrame(sd, index=total.index,
                             columns=[f"{c}{SD_SUFFIX}" for c in total.columns])
        # mean と sd を m/z ごとに隣り合わせる（人が読むときに対応が追いやすい）。
        interleaved = [col for pair in zip(mean_df.columns, sd_df.columns)
                       for col in pair]
        out = pd.concat([out, mean_df, sd_df], axis=1)[[COUNT_COLUMN] + interleaved]

    out = out.reset_index()
    # reset_index() は単一キーのとき列名が Index の name になる。念のため揃える。
    if len(group_cols) == 1 and out.columns[0] != group_cols[0]:
        out = out.rename(columns={out.columns[0]: group_cols[0]})
    return out


def aggregate(df: pd.DataFrame, group_cols: list, value_cols: list) -> pd.DataFrame:
    """1 つの DataFrame をその場で集計する（部分集計を挟まない単純版）。

    テストと、切片が 1 ファイルしかない場合の近道。結果は
    `accumulate_partial` → `combine_partials` と一致する。
    """
    return combine_partials(
        [accumulate_partial(df, group_cols, value_cols)], group_cols)
