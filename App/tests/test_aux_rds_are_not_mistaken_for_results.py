"""R が書き出す「描画材料」の RDS を解析結果として拾わないこと (ver66.3)。

--------------------------------------------------------------------------
症状
--------------------------------------------------------------------------
TIMS の結果フォルダを開くと、解析手法の一覧に **`PCA` が出る**。無補正 PCA だと
思って選ぶと、実体は `RDS_Files/pixel_table_rpca.rds` — 空間マップを描くための
data.frame で、Seurat オブジェクトではない。

--------------------------------------------------------------------------
なぜ起きたか
--------------------------------------------------------------------------
手法の検出 (`_detect_integration_methods`) はファイル名の**部分一致**で行うので、
解析結果ではない RDS を先に落とす必要がある。その表が `_EXCLUDE_PREFIXES` だが、
R スクリプトは手法ごと (harmony / rpca / pca_uncorrected) に補助 RDS を **7 系統**
書き出すのに、表には 4 系統しか載っていなかった。

漏れていた `pixel_table_rpca.rds` は `"rpca" in name` に当たるが `RPCA` の枠は既に
埋まっているので条件が偽になり、`elif` 連鎖の最後の `"pca" in name` まで落ちて
`PCA` として登録されていた。

--------------------------------------------------------------------------
ここで守ること
--------------------------------------------------------------------------
表を手で足しただけでは、次に R 側へ補助出力が増えたときにまた同じ穴が空く
（ver58.0・ver66.2・ver66.3 と 3 度続けてこの一帯を直している）。そこで
**R スクリプトの出力と Python 側の除外表が食い違ったら落ちる**番人を置く。

対象は「手法ごとに 1 つずつ書き出される補助 RDS」＝ファイル名に `prefix` を
差し込んでいるもの。固定名の RDS (`Step2_HarmonyPCA_Result.rds` など) は解析結果
そのものなので対象外。
"""

import re
from pathlib import Path

from app.callbacks.interactive_callbacks import _EXCLUDE_PREFIXES

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "Script"

# 2026-09 時点で見つかる系統数。正規表現が壊れて 0 件になっても気づけるようにする。
_MIN_FAMILIES = 7


def _paste0_calls(src: str):
    """`paste0(...)` の呼び出しを、括弧の対応を数えながら 1 つずつ返す。

    入れ子 (`paste0("x_", tolower(prefix), ".rds")`) があっても最後まで取れること。
    正規表現で `[^()]*` と書くと入れ子で**黙って何も拾わなくなる**ので数える。
    """
    for m in re.finditer(r"\bpaste0\(", src):
        depth = 0
        for k in range(m.end() - 1, len(src)):
            if src[k] == "(":
                depth += 1
            elif src[k] == ")":
                depth -= 1
                if depth == 0:
                    yield src[m.start():k + 1]
                    break


def _aux_rds_families():
    """手法別に書き出される補助 RDS の「ファイル名の頭」を集める。

    Returns:
        {先頭リテラル or None: {スクリプトの相対パス, ...}}
    """
    found = {}
    for path in sorted(SCRIPT_DIR.rglob("*.R")):
        src = path.read_text(encoding="utf-8", errors="replace")
        for call in _paste0_calls(src):
            if ".rds" not in call:
                continue
            # `out_prefix` のような別物を拾わないよう単語境界で見る。
            if not re.search(r"\bprefix\b", call):
                continue
            lit = re.match(r'paste0\(\s*("(?:[^"\\]|\\.)*")', call)
            head = lit.group(1)[1:-1] if lit else None
            found.setdefault(head, set()).add(str(path.relative_to(ROOT)))
    return found


def test_every_per_method_aux_rds_is_excluded():
    """★ 本丸: 手法別の補助 RDS の系統が、すべて除外表で塞がれていること。

    R 側に新しい補助出力を足すと、この検査が落ちて
    `_EXCLUDE_PREFIXES` への追加を促す。
    """
    families = _aux_rds_families()
    assert len(families) >= _MIN_FAMILIES, (
        f"手法別の補助 RDS が {len(families)} 系統しか見つからない "
        f"(想定 {_MIN_FAMILIES} 以上)。paste0 の走査が壊れていて、"
        "この検査が素通りしている可能性が高い")

    offenders = {}
    for head, files in families.items():
        if head is None:
            offenders["(先頭が文字列リテラルでない paste0)"] = files
        elif not head.lower().startswith(_EXCLUDE_PREFIXES):
            offenders[head] = files

    assert not offenders, (
        "解析結果ではない RDS が手法として拾われうる。\n"
        "  app/callbacks/interactive_callbacks.py の _EXCLUDE_PREFIXES に"
        "系統を足すこと:\n  "
        + "\n  ".join(f"{h!r}  <- {sorted(f)[0]} ほか {len(f)} 件"
                      for h, f in sorted(offenders.items(), key=lambda kv: str(kv[0]))))


def test_the_pixel_table_family_is_the_one_that_was_missing():
    """★ 今回の穴そのもの: `pixel_table_` が表に載っていること。

    ここを外すと、TIMS の結果フォルダで `pixel_table_rpca.rds` が `PCA` を名乗る
    （＝利用者が見ていた症状）。
    """
    assert "pixel_table_" in _EXCLUDE_PREFIXES, (
        "pixel_table_ が除外表から外れている。"
        "空間マップの描画材料 (data.frame) が解析手法として出てしまう")
    assert "pixel_table_" in _aux_rds_families(), (
        "R スクリプト側の pixel_table_ 出力が見つからない。"
        "出力を廃止したのなら除外表と本テストも整理すること")
