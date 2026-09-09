# =============================================================================
# MSI Analysis Application - 進捗と残り時間の推定 (ver65.0)
# =============================================================================
#
# なぜ独立したモジュールにするか
# -----------------------------
# 従来この計算は `analysis_callbacks._format_remaining_time()` の 3 行だけだった。
#
#     progress_ratio = step_current / step_total       # 13 段を等重量とみなす
#     remaining      = elapsed / progress_ratio * (1 - progress_ratio)
#
# この式は **13 段すべてが同じ長さ** という前提で立っている。実測はまったく違う
# （CHANGELOG ver63.3 (3): 68 分の内訳は DEG 61% / 作図 22%）。DEG は 13 段のうち
# 1 段なのに所要の 61% を占めるので、
#
#   - DEG に入った瞬間: 実際は残り 61 分なのに「残り約 12 分」と表示 (5 倍のずれ)
#   - その 41 分のあいだ、表示は 11 分 → 78 分へ **増え続ける**（実際は減っている）
#   - DEG を抜けた瞬間: 実際は残り 19 分なのに「残り約 57 分」（今度は 3 倍の逆方向）
#
# となる。さらに段 4-10 は R の `run_downstream_analysis()` の中にあり、この関数は
# 既定で最大 3 回呼ばれる（harmony / pca_uncorrected / rpca）。「最も index の大きい
# 段」で判定するため、1 巡目が終わった時点でバーは 77% を指し、残り 2 巡（全体の
# 半分以上）のあいだ一切動かない。
#
# つまり問題は係数の調整不足ではなく、**モデルがパイプラインの形と合っていない**
# ことだった。ここでは 3 つを入れ替える。
#
#   1. 段に重みを持たせる（等重量をやめる）
#   2. R が出す実測時刻 `[stage] <名> (前段 N 秒) [t=<ISO>]` を読み、
#      **この実行の実測ペース**で残りを引く（データ規模やマシンに自動追従する）
#   3. `[plan] downstream_passes=N` / `[pass] downstream k/N` を読み、
#      巡回する区間を巡数ぶん数える
#
# 段の記録が無いログ（DESI テンプレート / 古い実行）でも 1 は効くので、
# 従来どおりの部分一致による段検出にフォールバックする。
# =============================================================================

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger("msi.progress_estimator")


# ---------------------------------------------------------------------------
# 段の定義
# ---------------------------------------------------------------------------
#
# (表示名, 検出キーワード, 重み, downstream の中か)
#
# 重み: 相対値。合計を 1 に揃える必要はなく、使うときに正規化する。
#   tims_v8 の値は実測 (CHANGELOG ver63.3 (3): 68 分の内訳 DEG 61% / 作図 22%) を
#   出典にしている。**測れているのは「DEG」と「作図 4 段の合計」までで**、
#   作図 4 段のあいだの配分と残り 17% の内訳は初期値（推測）である。
#   ただしこの推測が効くのは最初の 1〜2 段だけで、以降は実測ペース
#   (`[stage]` 行の時刻) が重みごと上書きしていく。
#
# downstream フラグ: True の段は R の `run_downstream_analysis()` の中にあり、
#   1 回の解析で最大 3 回繰り返される。
_STAGE_TABLE: dict[str, list[tuple[str, str, float, bool]]] = {
    "desi_v8": [
        # DESI は段ごとの実測が無い。TIMS と同じ形（DEG が支配的）を初期値に置く。
        # 合計 1.00。
        ("Loading", "reading desi data", 0.08, False),
        ("Filtering", "spot filtering", 0.02, False),
        ("PCA", "pca", 0.04, False),
        ("UMAP", "umap", 0.04, False),
        ("Clustering", "findclusters", 0.02, False),
        ("Harmony/RPCA", "harmony", 0.05, False),
        ("DEG", "deg", 0.55, False),
        ("Heatmap", "heatmap", 0.05, False),
        ("Volcano", "volcano", 0.03, False),
        ("MSI Images", "msi", 0.10, False),
        ("Saving", "saving", 0.02, False),
        ("Done", "all done", 0.00, False),
    ],
    # 1 巡ぶんの合計が 1.00 になるように置く（downstream が複数巡するときは
    # 巡数ぶん足すので、合計はそのぶん増える）。
    #   DEG 0.61 / 作図 4 段 0.22 は実測、残り 0.17 の内訳は初期値。
    "tims_v8": [
        ("Loading", "reading parquet", 0.06, False),
        ("Preprocessing", "preprocessing", 0.02, False),
        ("Harmony correction", "harmony correction", 0.02, False),
        ("Clustering", "findclusters", 0.02, True),
        # ★実測: 全体の 61%。13 段の等重量では 7.7% としか数えられていなかった。
        ("Markers", "finding markers", 0.61, True),
        ("Annotation", "annotating", 0.01, True),
        # ★実測: 以下 4 段の合計が 22%。4 段のあいだの配分は初期値。
        ("Heatmap", "heatmap", 0.06, True),
        ("Volcano", "volcano", 0.03, True),
        ("MSI Images", "msi images", 0.10, True),
        ("TIC Overlay", "tic overlay", 0.03, True),
        ("RPCA", "running rpca", 0.03, False),
        ("Saving", "saving", 0.01, False),
        ("Done", "all done", 0.00, False),
    ],
    "desi_cluster_filter": [
        ("Loading", "loading", 0.08, False),
        ("Filtering", "filter", 0.04, False),
        ("DEG", "deg", 0.55, False),
        ("Heatmap", "heatmap", 0.06, False),
        ("Volcano", "volcano", 0.03, False),
        ("MSI Images", "msi", 0.15, False),
        ("Saving", "saving", 0.04, False),
        ("Merge", "merge", 0.05, False),
        ("Done", "done", 0.00, False),
    ],
    "tims_cluster_filter": [
        ("Loading", "loading", 0.08, False),
        ("Filtering", "filter", 0.04, False),
        ("Markers", "finding markers", 0.55, False),
        ("Heatmap", "heatmap", 0.06, False),
        ("Volcano", "volcano", 0.03, False),
        ("MSI Images", "msi", 0.15, False),
        ("Saving", "saving", 0.04, False),
        ("Merge", "merge", 0.05, False),
        ("Done", "done", 0.00, False),
    ],
}

_DEFAULT_TYPE = "desi_v8"

# 現在の段がどれだけ進んだかの上限。段が想定より長引いているときに
# 100% に張り付かせない（張り付くと「まもなく完了」のまま何分も待たされる）。
_IN_STAGE_CAP = 0.95

# R が出す機械可読な行
_RE_STAGE = re.compile(r"^\[stage\]\s*(?P<name>.*?)\s*$")
_RE_PREV_SEC = re.compile(r"\(前段\s*(?P<sec>[0-9.]+)\s*秒\)")
_RE_TS = re.compile(r"\[t=(?P<ts>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})\]")
_RE_PLAN = re.compile(r"^\[plan\]\s*downstream_passes=(?P<n>\d+)")
_RE_PASS = re.compile(r"^\[pass\]\s*downstream\s*(?P<k>\d+)\s*/\s*(?P<n>\d+)")
_RE_PASS_SKIP = re.compile(r"^\[pass\]\s*skip\b")


def stage_table(analysis_type: str) -> list[tuple[str, str, float, bool]]:
    return _STAGE_TABLE.get(analysis_type or "", _STAGE_TABLE[_DEFAULT_TYPE])


@dataclass
class _Mark:
    """ログ 1 行から読んだ段の記録。

    `index` は None になりうる。R は段の定義に無い印も打つ（downstream の
    末尾の `[stage] Done` など）。その行を捨ててしまうと、行が運んでいる
    「(前段 N 秒)」＝**直前の段の実測所要**まで一緒に落ちるので、
    印としては残し、段の位置だけ未対応にする。
    """
    index: int | None           # 段の定義上の位置 (0 始まり)。None = 定義に無い印
    at: datetime | None         # その段に入った時刻 ([t=...])
    prev_sec: float | None      # 直前の段の所要秒 ((前段 N 秒))


@dataclass
class Estimate:
    """画面に出すのに必要なものだけを持つ。"""
    step_current: int = 0        # 1 始まり。0 = 未検出
    step_total: int = 0
    step_name: str = "準備中"
    pass_current: int = 0        # downstream の何巡目か。0 = 巡回区間の外
    pass_total: int = 0
    fraction: float = 0.0        # 0.0-1.0
    remaining_sec: float | None = None   # None = まだ推定できない
    measured: bool = False       # 実測ペースを使えたか (False = 静的な重みだけ)
    # 全作業量のうち、実測で裏づけの取れている割合 (0.0-1.0)。
    # 1 段しか測れていないうちは、その段の重みが実際とずれていた分だけ
    # ペースがそのままずれる。数字を言い切ってよいかの判断に使う。
    confidence: float = 0.0
    stage_seconds: dict[str, float] = field(default_factory=dict)  # 実測の段別秒


# ---------------------------------------------------------------------------
# ログの解析
# ---------------------------------------------------------------------------

def parse_markers(log_text: str, analysis_type: str
                  ) -> tuple[list[_Mark], int | None, int]:
    """`[stage]` / `[plan]` / `[pass]` 行だけを読む。

    Returns
    -------
    (marks, planned_passes, skipped_passes)
        marks は出現順。planned_passes は `[plan]` 行が無ければ None。
    """
    steps = stage_table(analysis_type)
    marks: list[_Mark] = []
    planned: int | None = None
    skipped = 0

    for raw in (log_text or "").splitlines():
        line = raw.strip()
        if not line.startswith(("[stage]", "[plan]", "[pass]")):
            continue

        m = _RE_PLAN.match(line)
        if m:
            planned = int(m.group("n"))
            continue
        if _RE_PASS_SKIP.match(line):
            skipped += 1
            continue
        m = _RE_PASS.match(line)
        if m:
            # 予定より多く走ったときは実績を優先する（予定は多め/少なめどちらも
            # ありうるので、実績が超えたら実績に合わせる）。
            planned = max(planned or 0, int(m.group("n")), int(m.group("k")))
            continue

        m = _RE_STAGE.match(line)
        if not m:
            continue
        body = m.group("name")
        low = body.lower()
        idx = None
        for i, (_name, keyword, _w, _ds) in enumerate(steps):
            if keyword in low:
                idx = i
                break
        ts = _RE_TS.search(body)
        prev = _RE_PREV_SEC.search(body)
        marks.append(_Mark(
            index=idx,
            at=(datetime.fromisoformat(ts.group("ts")) if ts else None),
            prev_sec=(float(prev.group("sec")) if prev else None),
        ))

    return marks, planned, skipped


def detect_step_by_keyword(log_text: str, analysis_type: str) -> tuple[int, int, str]:
    """従来どおりの部分一致による段検出（`[stage]` 行が無いログ用）。

    ver63.3 以前の実行、および `.stage_mark()` を持たないテンプレート
    (DESI v16 / 再解析) はこちらを通る。
    """
    steps = stage_table(analysis_type)
    total = len(steps)
    low = (log_text or "").lower()
    for i in range(total - 1, -1, -1):
        if steps[i][1] in low:
            return i + 1, total, steps[i][0]
    return 0, total, "準備中"


# ---------------------------------------------------------------------------
# 推定
# ---------------------------------------------------------------------------

def _weights(analysis_type: str) -> tuple[list[float], list[bool]]:
    steps = stage_table(analysis_type)
    return [w for _n, _k, w, _d in steps], [d for _n, _k, _w, d in steps]


def _total_units(analysis_type: str, passes: int) -> float:
    """巡回を考慮した総作業量。downstream の中は巡数ぶん数える。"""
    ws, ds = _weights(analysis_type)
    outside = sum(w for w, d in zip(ws, ds) if not d)
    inside = sum(w for w, d in zip(ws, ds) if d)
    return outside + inside * max(1, passes)


def estimate(analysis_type: str, log_text: str, started_at: datetime | None,
             now: datetime | None = None) -> Estimate:
    """ログと開始時刻から進捗と残り時間を出す。

    `[stage]` 行があるときは **この実行で実測した段別の所要秒**からペースを求める。
    無いときは静的な重みと総経過時間だけで出す（従来より良いが精度は落ちる）。
    """
    now = now or datetime.now()
    steps = stage_table(analysis_type)
    total_steps = len(steps)
    ws, ds_flags = _weights(analysis_type)

    marks, planned, skipped = parse_markers(log_text, analysis_type)
    passes = max(1, (planned or 0) - skipped) if (planned or marks) else 1
    known = [m for m in marks if m.index is not None]

    if not known:
        # --- フォールバック: 段の記録が無いログ ---
        cur, _tot, name = detect_step_by_keyword(log_text, analysis_type)
        est = Estimate(step_current=cur, step_total=total_steps, step_name=name,
                       pass_total=passes)
        if cur <= 0 or started_at is None:
            return est
        # 到達した段は「入ったところ」なので、完了しているのは 1 つ手前まで。
        # 従来はここを cur/total としており、常に 1 段ぶん進捗を過大に見ていた。
        done = sum(ws[:cur - 1])
        est.fraction = min(0.99, done / _total_units(analysis_type, passes))
        elapsed = (now - started_at).total_seconds()
        if est.fraction > 0.02 and elapsed >= 1:
            est.remaining_sec = elapsed / est.fraction * (1 - est.fraction)
        return est

    # --- 段の記録があるとき ---
    # marks[i].prev_sec は「marks[i-1] の段が何秒かかったか」。
    stage_seconds: dict[str, float] = {}
    measured_units = 0.0
    measured_sec = 0.0
    for i in range(1, len(marks)):
        sec = marks[i].prev_sec
        if sec is None:
            continue
        prev_idx = marks[i - 1].index
        if prev_idx is None:
            # 定義に無い印の直後。どの段の秒数か決められないので重みには足さない
            # （ペースの分母を汚さないため）。時間だけは記録に残す。
            stage_seconds.setdefault("(その他)", 0.0)
            stage_seconds["(その他)"] += sec
            continue
        name = steps[prev_idx][0]
        stage_seconds[name] = stage_seconds.get(name, 0.0) + sec
        measured_units += ws[prev_idx]
        measured_sec += sec

    last = known[-1]
    cur_idx = last.index
    cur_w = ws[cur_idx]

    # 完了した段の重みの合計（同じ段が複数巡で出てくるので出現ぶん足す）
    done_units = sum(ws[m.index] for m in known[:-1])

    # 実測ペース。1 段でも測れていれば使う。
    sec_per_unit = (measured_sec / measured_units) if measured_units > 0 else None

    # いま走っている段の進み具合。
    # 「定義に無い印」がその後に来ていれば、そちらの方が新しい＝その段は
    # 終わっているので、経過の起点は最後の印の時刻を使う。
    last_any = marks[-1]
    stage_started_at = last.at if last_any is last else (last_any.at or last.at)
    in_stage = 0.0
    total_units = _total_units(analysis_type, passes)
    if stage_started_at is not None and sec_per_unit:
        t_in = max(0.0, (now - stage_started_at).total_seconds())
        expected = cur_w * sec_per_unit
        if expected > 0:
            if t_in <= expected:
                in_stage = min(_IN_STAGE_CAP, t_in / expected)
            else:
                # 想定より長引いている。段の重みを実測に合わせて広げる
                # （黙って「まもなく完了」に張り付かせない）。
                grown = t_in / sec_per_unit / _IN_STAGE_CAP
                total_units += (grown - cur_w)
                cur_w = grown
                in_stage = _IN_STAGE_CAP
    done_units += cur_w * in_stage

    fraction = min(0.99, done_units / total_units) if total_units > 0 else 0.0

    remaining = None
    if sec_per_unit:
        remaining = max(0.0, (total_units - done_units) * sec_per_unit)
    elif started_at is not None and fraction > 0.02:
        elapsed = (now - started_at).total_seconds()
        if elapsed >= 1:
            remaining = elapsed / fraction * (1 - fraction)

    # 何巡目か（downstream の中の段にいるときだけ意味がある）
    pass_current = 0
    if ds_flags[cur_idx]:
        first_ds = next((i for i, d in enumerate(ds_flags) if d), None)
        if first_ds is not None:
            pass_current = sum(1 for m in known if m.index == first_ds)
            pass_current = max(1, min(pass_current, passes))

    return Estimate(
        step_current=cur_idx + 1,
        step_total=total_steps,
        step_name=steps[cur_idx][0],
        pass_current=pass_current,
        pass_total=passes,
        fraction=fraction,
        remaining_sec=remaining,
        measured=bool(sec_per_unit),
        confidence=(min(1.0, measured_units / total_units)
                    if (sec_per_unit and total_units > 0) else 0.0),
        stage_seconds=stage_seconds,
    )


# ---------------------------------------------------------------------------
# 表示
# ---------------------------------------------------------------------------

# この割合ぶん実測できるまでは、1 つの数字に言い切らない。
# 序盤は「たまたま最初に測れた段の重みのずれ」がそのままペースのずれになるため。
_CONFIDENT_AT = 0.30


def _hm(x: float) -> str:
    x = max(0, int(x))
    if x >= 3600:
        return f"{x // 3600}時間{(x % 3600) // 60}分"
    if x >= 60:
        return f"{x // 60}分"
    return "1分未満"


def format_remaining(est: Estimate) -> str:
    """残り時間の表示文字列。

    ★ ver65.0: **言い切れないうちは言い切らない**。従来は等重量モデルが
      「残り約 12 分」と断言して 61 分待たせていた。裏づけが薄い序盤は幅で出し、
      全作業量の 3 割ぶんが実測できてから 1 つの数字にする。
    """
    if est.remaining_sec is None:
        return "残り時間: 推定中..."
    if est.fraction >= 0.995:
        return "残り時間: まもなく完了"
    sec = est.remaining_sec
    if est.confidence >= _CONFIDENT_AT:
        return f"残り約 {_hm(sec)}"
    # 幅は確信度で狭まる（裏づけが増えるほど幅が縮む）。
    spread = 1.0 + 1.2 * (1.0 - min(1.0, est.confidence / _CONFIDENT_AT))
    return f"残り {_hm(sec / spread)}〜{_hm(sec * spread)}（目安）"


def format_step(est: Estimate) -> str:
    """「Markers (5/13) 2巡目/3」のような段の表示。"""
    if est.step_current <= 0:
        return "準備中"
    text = f"{est.step_name} ({est.step_current}/{est.step_total})"
    if est.pass_total > 1 and est.pass_current > 0:
        text += f" {est.pass_current}巡目/{est.pass_total}"
    return text
