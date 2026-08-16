"""R が R を文字列手術するときの anchor を **全数照合** する (ver52.2)。

■ この型

再解析テンプレは通常テンプレを `readLines` で読み、`grep` で目印（anchor）を
探してブロックを差し替え、`writeLines` でコピーを書き出す。

問題は、**anchor が見つからなかったときの方針が 2 通りある**こと:

    ① `.stopif(length(idx) >= 1, "…見つかりません")`   → 停止する（fail-closed）
    ② `if (length(idx) == 0) return(code_vec)`         → **無言で元コードを返す**

②の形は、通常テンプレを改版したときに anchor がずれても何も言わない。
**パッチが当たっていないのに解析は最後まで走り、結果が出る。**

■ 実測（ver52.2 時点・自分で全数照合した）

| パッチ | anchor | 一致 | 不一致時 |
|---|---:|---|---|
| DESI: Otsu スキップ | 2 | **1 / 2** | ② 無言 |
| DESI: 短行パディング | 2 | **0 / 2** | ② 無言 |
| DESI: sample_names ブロック | 1 | 1 / 1 | ① 停止 |
| TIMS: run_pipeline ブロック | 1 | 1 / 1 | ② 無言 |
| TIMS: Retry Logic ブロック | 3 | 3 / 3 | ② 無言 |
| TIMS: INPUT_PATHS ブロック | 1 | 1 / 1 | ① 停止 |

**死んでいるのは DESI 側の 2 パッチ・3 anchor。どちらも②。**

★ 規則性がはっきりしている ——
  **①（`.stopif`）を使うパッチは全部生きていて、②のパッチだけが死んでいる。**
  失敗時の方針の違いが、そのまま「気付けるか」の違いになっている。

★ そして **R 側のほうが Python 側より厳しい**。
  R の `.stopif` は 0 件で停止するが、Python の `analysis_runner._replace_assign`
  は 0 件でも成功扱いで返る。同じ設計判断を R では正しくやっている。

■ R は実行しない

anchor は正規表現の文字列なので、**R を起動せずテキストだけで照合できる**。
これまで「この環境に R が無いので検証できない」として R 側を 5 ラウンド
先送りしてきたが、この型に関してはその前提が誤りだった。
"""

import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "Script"

# 「どの再解析スクリプトが、どの通常テンプレを手術するか」。
# `analysis_runner.generate_cluster_filter_config` が組み立てる対応と一致する。
PATCHERS = {
    "DESI 再解析": (
        SCRIPT / "DESI/DESI_RDS_ClusterFilter_ver3.R",
        SCRIPT / "DESI/260623_DESI-UMAP_Template_v16.R",
    ),
    "TIMS 再解析": (
        SCRIPT / "TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R",
        SCRIPT / "TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R",
    ),
}

# 各再解析スクリプトから抽出できる anchor の実数（下限）。
# 抽出ロジックが壊れると 0 個に近づくので、その検出に使う。
EXPECTED_ANCHOR_COUNT = {
    "DESI 再解析": 4,
    # ver56.5: Retry Logic 置換を削除して 4 → 3。
    "TIMS 再解析": 3,
}

# 手術対象のコードを保持している R の変数名。
# `grep(pat, code_vec)` の第 2 引数がこれなら「テンプレを探している」と判定する。
CODE_VARS = ("code_vec", "code")

# ★ 実害を確認済みだが ver52.2 では直さない（ver52.4 の対象。R テンプレ側の変更が要る）。
#   ver52.2 は「番人だけを入れて母数を測る版」。
#   隠さず記録し、(a) これ以上増えないこと (b) 直ったら気付けること を担保する。
KNOWN_DEAD_ANCHORS = {
    ("DESI 再解析", r"seu_list\[\[ii\]\]\s*<-\s*filtering_result_otsu\$filtered_seurat"):
        "Otsu スキップの終了 anchor。v16:2153 は "
        "`seu_list[[length(seu_list) + 1]] <- …` なので一致しない。"
        "★ 開始 anchor は当たるので『壊れている』ように見えないのが厄介。"
        "結果、クラスタ絞り込み後の再 UMAP で **背景除去が再実行される**",
    ("DESI 再解析", r"data_list\s*<-\s*vector\(\"list\",\s*length\(data_lines\)\)"):
        "短行パディングの開始 anchor。`data_list` は v16 に 1 つも存在しない",
    ("DESI 再解析", r"data_df\s*<-\s*as\.data\.frame\(data_matrix,\s*stringsAsFactors\s*=\s*FALSE\)"):
        "短行パディングの終了 anchor。`data_matrix` も v16 に存在しない。"
        "★ このパッチは Waters txt が『末尾の 0 を省略』して行が短くなる問題を"
        "直すために書かれたもので、**丸ごと空振りしているので問題は今も直っていない**",
}


# --------------------------------------------------------------------------
def _unescape_r_string(literal: str) -> str:
    """R の文字列リテラル本体を、実際の文字列へ戻す。

    R ソース上の `"a\\\\s*b"` は正規表現 `a\\s*b` を意味する。
    """
    out, i = [], 0
    while i < len(literal):
        c = literal[i]
        if c == "\\" and i + 1 < len(literal):
            nxt = literal[i + 1]
            out.append({"n": "\n", "t": "\t", "r": "\r"}.get(nxt, nxt))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


_STRING = r'"((?:[^"\\]|\\.)*)"' + r"|'((?:[^'\\]|\\.)*)'"
_ASSIGN_PAT = re.compile(r"^\s*(\w+)\s*<-\s*(?:" + _STRING + r")\s*$", re.M)
_GREP = re.compile(
    r"grep\(\s*(?:" + _STRING + r"|(\w+))\s*,\s*(\w+)")


def _anchors(patcher: Path):
    """`grep(<anchor>, code_vec)` の anchor を (正規表現, 行) で列挙する。

    直接のリテラルと、`start_pat <- "…"` のように一度変数へ入れる形の両方を拾う。
    """
    src = patcher.read_text(encoding="utf-8")
    # 変数に入れられた文字列を解決する
    known = {m.group(1): _unescape_r_string(m.group(2) or m.group(3) or "")
             for m in _ASSIGN_PAT.finditer(src)}

    out = []
    for m in _GREP.finditer(src):
        dq, sq, var, target = m.groups()
        if target not in CODE_VARS:
            continue                     # テンプレ以外を探している grep は対象外
        if dq is not None or sq is not None:
            pattern = _unescape_r_string(dq if dq is not None else sq)
        elif var in known:
            pattern = known[var]
        else:
            continue                     # 動的に組み立てた正規表現は照合できない
        line = src[:m.start()].count("\n") + 1
        out.append((pattern, line))
    return out


def _dead_anchors():
    """どのテンプレにも一致しない anchor を (ラベル, 正規表現, 行) で返す。"""
    dead = []
    for label, (patcher, template) in PATCHERS.items():
        body = template.read_text(encoding="utf-8", errors="replace")
        for pattern, line in _anchors(patcher):
            try:
                hit = re.search(pattern, body, re.M)
            except re.error:
                continue                 # R 固有の記法で Python が解釈できないもの
            if hit is None:
                dead.append((label, pattern, line))
    return dead


# --------------------------------------------------------------------------
class TestTheGuardIsNotInert:
    """★ 番人が空振りしていないこと（ver51.9 で 3 回空振りさせた反省）。"""

    def test_all_scripts_exist(self):
        for label, (patcher, template) in PATCHERS.items():
            assert patcher.exists(), f"{label}: 再解析スクリプトが無い {patcher}"
            assert template.exists(), f"{label}: 通常テンプレが無い {template}"

    @pytest.mark.parametrize("label", sorted(PATCHERS))
    def test_anchors_are_discoverable(self, label):
        """抽出が壊れていないこと（見つかる anchor が急に減っていないこと）。

        ver56.5: 一律 `>= 4` だったが、TIMS 再解析の Retry Logic 置換を
        削除して 3 個になった（本体テンプレが同じことをするようになったため）。
        「抽出の破綻」と「パッチの正当な削減」を区別できるよう、
        スクリプトごとの実数を控える。減ったときはここも一緒に直す。
        """
        found = _anchors(PATCHERS[label][0])
        assert len(found) >= EXPECTED_ANCHOR_COUNT[label], (
            f"{label}: anchor が {len(found)} 個しか見つからない"
            f"（期待 {EXPECTED_ANCHOR_COUNT[label]} 個以上）。"
            "抽出が壊れている疑い（R 側の書き方が変わった？）")

    def test_r_string_unescaping_works(self):
        """抽出の要。ここが壊れると全 anchor が偽陰性になる。"""
        assert _unescape_r_string(r"a\\s*b") == r"a\s*b"
        assert _unescape_r_string(r"seu_list\\[\\[ii\\]\\]") == r"seu_list\[\[ii\]\]"
        assert _unescape_r_string(r'stop\\(\"x\"\\)') == r'stop\("x"\)'


class TestEveryAnchorMatchesItsTemplate:
    """★ 本丸: 手術の目印が、手術対象に実在すること。"""

    def test_no_new_dead_anchor(self):
        new = [(lbl, pat, ln) for lbl, pat, ln in _dead_anchors()
               if (lbl, pat) not in KNOWN_DEAD_ANCHORS]
        assert not new, (
            "現行テンプレに存在しない anchor を探しているパッチが**新たに**増えた。\n"
            "`if (length(idx) == 0) return(code_vec)` の形は**無言で元コードを返す**ので、"
            "パッチが当たらないまま解析が完走して結果が出る:\n  "
            + "\n  ".join(f"{lbl}  {Path(PATCHERS[lbl][0]).name}:{ln}\n"
                          f"      anchor: {pat}"
                          for lbl, pat, ln in new))

    def test_known_dead_anchors_do_not_shrink_silently(self):
        """★ 直ったら登録から外させる。"""
        dead = {(lbl, pat) for lbl, pat, _ln in _dead_anchors()}
        fixed = sorted(set(KNOWN_DEAD_ANCHORS) - dead)
        assert not fixed, (
            "KNOWN_DEAD_ANCHORS に載っているが実際にはテンプレと一致している。"
            "直ったのは良いことなので登録から外すこと:\n  "
            + "\n  ".join(f"{lbl}: {pat}" for lbl, pat in fixed))

    def test_known_dead_anchors_are_still_present_in_source(self):
        """消えた anchor の登録を残さない（登録簿の陳腐化を防ぐ）。"""
        live = {(lbl, pat)
                for lbl in PATCHERS
                for pat, _ln in _anchors(PATCHERS[lbl][0])}
        gone = sorted(set(KNOWN_DEAD_ANCHORS) - live)
        assert not gone, (
            "KNOWN_DEAD_ANCHORS に、もう R ソースに無い anchor が残っている。"
            "登録から外すこと:\n  " + "\n  ".join(f"{lbl}: {pat}" for lbl, pat in gone))


class TestFailureModeIsDeclared:
    """★ anchor 不一致で停止する形（`.stopif`）が使われていること。

    `.stopif(cond, msg)` は `if (!isTRUE(cond)) stop(msg)`。
    これを使っているパッチは 0 件一致で止まるので、**死んでも気付ける**。
    現状 DESI 側で死んでいる 2 パッチはどちらも `.stopif` を使っていない。
    """

    @pytest.mark.parametrize("label", sorted(PATCHERS))
    def test_stopif_helper_exists_and_asserts(self, label):
        src = PATCHERS[label][0].read_text(encoding="utf-8")
        m = re.search(r"\.stopif\s*<-\s*function\s*\(cond,\s*msg\)\s*\{([^}]*)\}", src)
        assert m, f"{label}: `.stopif` の定義が見つからない"
        body = m.group(1)
        assert "!isTRUE(cond)" in body and "stop(" in body, (
            f"{label}: `.stopif` が assert として機能していない。"
            f"条件が偽のときに stop すること: {body.strip()}")

    @pytest.mark.parametrize("label", sorted(PATCHERS))
    def test_stopif_is_actually_used(self, label):
        src = PATCHERS[label][0].read_text(encoding="utf-8")
        uses = len(re.findall(r"\.stopif\(", src)) - 1   # 定義自身を除く
        assert uses >= 3, (
            f"{label}: `.stopif` の使用が {uses} 箇所しかない。"
            "anchor 不一致を黙って通すパッチが増えている疑い")
