"""DESI の空間平滑化を行わないこと、そしてそれが記録に残ること (A-4)。

--------------------------------------------------------------------------
なぜ止めるか
--------------------------------------------------------------------------
DESI は平滑化が常時オン（`SPATIAL_SMOOTH <- TRUE`）で、探索半径が `0.1` の
ハードコードだった。この半径は**座標の単位に対して固定**なので、

  - 座標が画素番号 (1,2,3…) や µm (0,100,200…) → 近傍が自分だけ＝**何もしない**
  - 座標が 0.05 mm 刻み → 10〜12 近傍を平均する**強い平滑化**

と、**同じ設定でもデータセットによって前処理が別物**になっていた。
画面から強さを変えることも、効いたか確認することもできない。
TIMS は既定オフ（`SPATIAL_SMOOTH_ENABLE <- FALSE`）なので、これは DESI だけの話。

--------------------------------------------------------------------------
止めるだけでは足りない
--------------------------------------------------------------------------
平滑化の有無は **受領書にも Methods 文にも一度も記録されていなかった**
（`methods_text` / `provenance` / `runtime_script` / `receipt` に該当語句ゼロ）。
それでいて中間ファイル名は `DESI_SeuratList2_smoothed.rds` なので、
記録上は「平滑化した」ように読める。止めるなら**止めたことを残す**必要がある。

--------------------------------------------------------------------------
再解析との整合
--------------------------------------------------------------------------
再解析は本解析テンプレートを**コピーして**使う。平滑化には Python からの
注入経路が無く、テンプレートの定数だけが唯一の出どころなので、
**テンプレートを FALSE にすれば本解析と再解析の両方に同時に効く**。
「片方だけ平滑化される」状態を作らないため、注入経路を増やさない方針を
テストで固定する。
"""

import re
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "Script"
APP = Path(__file__).resolve().parents[1] / "app"
DESI_V16 = SCRIPT / "DESI" / "260623_DESI-UMAP_Template_v16.R"
TIMS_V6 = SCRIPT / "TIMS" / "260623_DBSCAN_With_cluster_ver6_no-png_slim.R"

_SRC = DESI_V16.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 本丸: 平滑化を行わない
# ---------------------------------------------------------------------------

def test_desi_smoothing_is_off():
    """★ DESI の平滑化が既定でオフであること。"""
    m = re.search(r"^SPATIAL_SMOOTH\s*<-\s*(\S+)", _SRC, re.M)
    assert m, "SPATIAL_SMOOTH の定義が見つからない"
    assert m.group(1) == "FALSE", (
        f"DESI の平滑化が {m.group(1)} のまま。半径が座標の単位に対して固定なので、"
        "データセットによって前処理が別物になる")


def test_tims_smoothing_stays_off():
    """TIMS は元からオフ（この論点は DESI 限定）。"""
    m = re.search(r"^SPATIAL_SMOOTH_ENABLE\s*<-\s*(\S+)",
                  TIMS_V6.read_text(encoding="utf-8"), re.M)
    assert m and m.group(1) == "FALSE"


def test_the_smoothing_code_is_kept_for_reference():
    """★ 直しすぎの検出: 実装ごと消さないこと（再検討・既存結果の再現のため）。"""
    assert "spatial_smooth_seurat" in _SRC, (
        "平滑化の実装まで削除されている。既定をオフにするだけに留めること")


# ---------------------------------------------------------------------------
# 止めても後段が壊れないこと（不変条件）
# ---------------------------------------------------------------------------

def test_the_smoothed_rds_is_only_used_inside_its_own_block():
    """★ 平滑化 RDS を外から読む箇所が無いこと。

    ここが崩れると「オフにした瞬間に後段が落ちる」ようになる。
    """
    lines = _SRC.splitlines()
    start = next(i for i, l in enumerate(lines) if l.strip().startswith("if(SPATIAL_SMOOTH)"))
    # 対応する閉じ括弧まで
    depth, end = 0, None
    for i in range(start, len(lines)):
        depth += lines[i].count("{") - lines[i].count("}")
        if depth == 0 and i > start:
            end = i
            break
    assert end, "平滑化ブロックを閉じられない"

    outside = [(i + 1, l) for i, l in enumerate(lines)
               if ("rds_path2" in l or "rds_filename2" in l) and not (start <= i <= end)]
    # 冒頭の NULL 初期化と末尾の後片付けだけは外に居てよい（NULL 安全）
    allowed = [ln for ln in outside
               if "<- NULL" in ln[1] or "file.exists(rp)" in ln[1] or "for (rp in" in ln[1]]
    unexpected = [ln for ln in outside if ln not in allowed]
    assert not unexpected, (
        "平滑化 RDS をブロックの外から参照している。"
        "オフにすると未定義になり後段が落ちる:\n  "
        + "\n  ".join(f"v16:{n}  {l.strip()[:90]}" for n, l in unexpected))


def test_the_cleanup_stays_null_safe():
    """後片付けが NULL 安全であること（オフのとき rds_path2_out は NULL）。"""
    assert re.search(r"rds_path1_out\s*<-\s*NULL;\s*rds_path2_out\s*<-\s*NULL", _SRC), (
        "冒頭の NULL 初期化が消えている。平滑化をオフにすると"
        "末尾の後片付けで未定義エラーになる")


# ---------------------------------------------------------------------------
# 記録に残ること
# ---------------------------------------------------------------------------

def test_the_smoothing_flag_is_recoverable_from_the_script():
    """★ 実行スクリプトから平滑化の有無を復元できること。"""
    from app.services.runtime_script import recover_conditions

    conditions = {}
    recover_conditions(conditions, script_path=str(DESI_V16))
    got = conditions.get("analysis", {}).get("preprocessing", {}).get("spatial_smoothing")
    assert got is False, (
        f"平滑化の有無を復元できない (得られた値: {got!r})。"
        "記録できないと『平滑化したのか、しなかったのか』が後から分からない")


def test_the_methods_text_says_no_smoothing():
    """★ Methods 文に「平滑化は行わなかった」と出ること。"""
    from app.services.methods_text import render_methods

    conditions = {"analysis": {"preprocessing": {
        "input_normalized": True, "spatial_smoothing": False}}}
    text = render_methods(conditions, lang="ja")
    assert "平滑" in text, (
        "Methods 文に平滑化の記述が無い。"
        "中間ファイル名が *_smoothed.rds なので、書かないと"
        "「平滑化した」と読まれる")


def test_the_methods_text_reports_smoothing_when_it_was_on():
    """★ 直しすぎの検出: オンだった過去の解析は「行った」と書くこと。"""
    from app.services.methods_text import render_methods

    conditions = {"analysis": {"preprocessing": {
        "input_normalized": True, "spatial_smoothing": True}}}
    text = render_methods(conditions, lang="ja")
    assert "平滑" in text
    assert "行わなかった" not in text.split("平滑")[1][:60], (
        "平滑化を行った解析にも「行わなかった」と書いている")


# ---------------------------------------------------------------------------
# 本解析と再解析が食い違わないこと
# ---------------------------------------------------------------------------

def test_there_is_a_single_source_of_truth_for_smoothing():
    """★ 平滑化の出どころをテンプレート定数 1 か所に保つこと。

    Python からの注入経路を足すと、本解析と再解析で値が食い違いうる
    （再解析は本解析テンプレートを **コピーして** 使うため、
    テンプレートの定数だけが唯一の出どころなら自動的に一致する）。
    """
    runner = (APP / "services" / "analysis_runner.py").read_text(encoding="utf-8")
    assert "SPATIAL_SMOOTH" not in runner, (
        "平滑化を Python から注入する経路が増えている。"
        "本解析と再解析で値が食い違う余地を作らないこと")
