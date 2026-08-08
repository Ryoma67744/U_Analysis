"""画面の数値入力が **全数** 境界検証に結線されていることを照合する (ver52.2)。

■ なぜこの番人なのか

「数値境界の未検証」は ver51.1 / 51.7 / 51.8 / 52.0 / 52.1 の **5 版**に出ている。
直近が分かりやすい:

    ver52.0  `top=0` が「無制限」になる穴を直した
    ver52.1  **`limit` に同じ穴が残っていた**（`if limit and limit > 0`）

同じ型を、同じファイルの中で、1 版あとにもう一度直している。
原因は「見つかった引数だけ直す」やり方で、母数を数えていなかったこと。

■ 実測した母数

    画面の数値入力 (`type="number"` かつ id が文字列リテラル)   28 個
    `PARAM_BOUNDS` に定義がある                                12 個
    `_VALIDATED_INPUTS` に結線されている                        8 個
    → **20 個が無検証**

しかも `PARAM_BOUNDS` の 12 定義のうち **4 つは画面の id と名前が違い、
一度も適用されていない**（`umap_n_neighbors` に対し実際の id は
`umap_n_neighbors_input`）。**仕組みは在るのに繋がっていない**。

★ これは監査 R-01（Python→R 注入が存在しない変数を狙う）と**同じ型**で、
  「宣言した対象が実在しない」が Dash の層に出たもの。

■ もう一つ守るもの: `x or DEFAULT` は 0 を殺す

    interactive_deg.py:1127   fc_thresh = fc_thresh or 0.5
    interactive_deg.py:1128   p_thresh  = p_thresh  or 1.3

`volcano_fc_threshold` の入力に `min=` が無いので 0 は入力できる。
利用者が「FC 閾値 0 で全部見たい」と打つと **黙って 0.5 で描かれ**、
0〜0.5 の feature が "Not significant" として灰色になる ——
利用者がしていない科学的主張が図に出る。

すぐ下の `volcano_label_top_n` には `min=0, max=50` が付いている。
**同じ画面の隣り合う入力で、片方だけ守られている。**
"""

import ast
import sys
from pathlib import Path

import pytest

# 既存の番人 (ver51.7) のデコレータ解析を再利用する。
# tests/ に __init__.py が無くパッケージではないので、明示的に import 経路を通す。
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_callback_wiring import _is_callback_decorator          # noqa: E402

APP = Path(__file__).resolve().parent.parent / "app"

from app.utils.validation import PARAM_BOUNDS                      # noqa: E402
from app.callbacks.interactive_validation import _VALIDATED_INPUTS  # noqa: E402


# --------------------------------------------------------------------------
# ★ 未結線と分かっているが ver52.2 では直さないもの。
#   ver52.2 は「番人だけを入れて母数を測る版」で、直すのは ver52.3（計画どおり）。
#   隠さず記録し、(a) これ以上増えないこと (b) 直ったら気付けること を担保する。
# --------------------------------------------------------------------------
KNOWN_UNBOUNDED = {
    # --- 科学的な意味が重いもの（ver52.3 で最優先） ---
    "p_thresh": "DEG の統計閾値。異常値で誤った有意判定",
    "logfc_thresh": "DEG の統計閾値",
    "reanalysis_p_thresh": "再解析の統計閾値",
    "reanalysis_logfc_thresh": "再解析の統計閾値",
    "tolerance_mz": "m/z 照合許容差。誤った化合物同定につながる",
    "reanalysis_tolerance_mz": "同上（再解析）",
    "reann_tolerance": "同上（再アノテーション）",
    "mz_align_ppm": "m/z アライメント幅",
    "calibration_search_window": "キャリブレーションの探索窓",
    "calibration_min_peaks": "キャリブレーションの最小点数",
    "int_cal_search_window": "対話キャリブレーションの探索窓",
    "int_cal_min_peaks": "対話キャリブレーションの最小点数",
    # --- 表示件数（API 側で直した `top` と同じ型が画面に残っている） ---
    "volcano_label_top_n": "ラベル件数。API の `top` と同型",
    "input_export_top_n": "エクスポート件数。API の `top` と同型",
    # --- UMAP 条件（PARAM_BOUNDS に定義はあるが id が違って死んでいる） ---
    "umap_n_neighbors_input": "PARAM_BOUNDS の umap_n_neighbors と id が不一致",
    "umap_min_dist_input": "PARAM_BOUNDS の umap_min_dist と id が不一致",
    "umap_dims_input": "PARAM_BOUNDS の pca_dims と id が不一致",
    # --- m/z 範囲フィルタ ---
    "feature_mz_min": "表示 m/z 範囲の下限",
    "feature_mz_max": "表示 m/z 範囲の上限",
    # --- 変換設定 ---
    "scils_spot_block": "SCiLS 変換のスポットブロック数",
}

# ★ `PARAM_BOUNDS` にあるのに画面の id と一致しない定義（＝一度も効いていない）。
#   `validate_param` は未知 id を常に ok として通すので、書いても何も起きない。
#
#   ver52.3 ② で 4 件すべて解消したので現在は空:
#     umap_n_neighbors → umap_n_neighbors_input  (既定 15 → 30、上限 なし → 100)
#     umap_min_dist    → umap_min_dist_input     (既定 0.1 → 0.3)
#     pca_dims         → umap_dims_input         (範囲 10-100 → 2-50、既定 30)
#     perplexity       → 削除（対応する入力が画面に無い）
#   ★ キー名だけでなく範囲・既定値もレイアウトと食い違っていた。名前だけ
#     直すと今度は正当な入力を弾くので、中身も実際の画面/R に合わせた。
KNOWN_DEAD_BOUNDS: dict[str, str] = {}

# ★ `x or DEFAULT` のうち、0 に意味が無く既定値で正しいもの（見た目の設定）。
#   科学的な閾値・件数は**ここに入れてはいけない**。
COSMETIC_OR_DEFAULTS = {
    "marker_size", "label_size", "title_font_size", "height_val",
    "point_size", "font_size", "opacity", "line_width", "dpi",
}


# --------------------------------------------------------------------------
# 走査
# --------------------------------------------------------------------------
def _numeric_input_ids():
    """`type="number"` かつ id が文字列リテラルの入力を {id: "file:line"} で返す。"""
    found = {}
    for path in sorted(APP.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            pytest.fail(f"{path} が構文エラー: {e}")
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            t = kw.get("type")
            if not (isinstance(t, ast.Constant) and t.value == "number"):
                continue
            i = kw.get("id")
            if isinstance(i, ast.Constant) and isinstance(i.value, str):
                found[i.value] = f"{path.relative_to(APP.parent)}:{node.lineno}"
            # pattern-matching id ({"type": ..., "index": ...}) は対象外。
            # id が動的なので静的には結線を判定できない。
    return found


def _numeric_input_props():
    """数値入力の `min` / `max` / `value` のうち **リテラル** を {id: {...}} で返す。

    レイアウトを正とするための材料。定数参照 (`DEFAULT_...`) や
    `ls.get(...)` はここでは拾えないので比較対象から外れる。
    """
    found = {}
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            t = kw.get("type")
            if not (isinstance(t, ast.Constant) and t.value == "number"):
                continue
            i = kw.get("id")
            if not (isinstance(i, ast.Constant) and isinstance(i.value, str)):
                continue
            props = {}
            for prop in ("min", "max", "value"):
                v = kw.get(prop)
                if (isinstance(v, ast.Constant)
                        and isinstance(v.value, (int, float))
                        and not isinstance(v.value, bool)):
                    props[prop] = v.value
            found[i.value] = props
    return found


def _callback_param_to_input_id(fn, dec):
    """コールバックの引数名 → 束縛されている component id の対応を返す。

    Dash は宣言順に位置引数で渡すので、Input/State の出現順と引数の順が対応する。
    """
    ids = []
    considered = list(dec.args) + [
        k.value for k in dec.keywords if k.arg in ("inputs", "state")]
    for node in considered:
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id in ("Input", "State") and sub.args):
                first = sub.args[0]
                ids.append(first.value
                           if isinstance(first, ast.Constant) else None)
    params = [a.arg for a in (*fn.args.posonlyargs, *fn.args.args)]
    return dict(zip(params, ids))


def _or_default_violations():
    """`<数値入力の引数> or <数値リテラル>` を **式の形を問わず** 列挙する。

    ★ ver52.3: 当初は `p = p or 5` という **代入の形** だけを見ていた。
      そのせいで母数 21 件のうち 3 件しか拾えていなかった:

          _top_n = int(label_top_n or 5)     ← 代入先が違い int() に包まれている
          params["x"] = tolerance or 0.01    ← 代入先が添字

      「0 が既定値に化ける」という型は **or 式そのもの**であって、
      その結果をどこへ入れるかとは関係ない。形で近似すると別の現れ方を通す
      （ver51.6 の scipy 番人が `setuptools.backends` を通したのと同じ）。
      束縛の判定だけ残し、代入の形は問わないようにした。
    """
    numeric = set(_numeric_input_ids())
    out = []
    for path in sorted(APP.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decs = [d for d in fn.decorator_list if _is_callback_decorator(d)]
            if not decs:
                continue
            bound = _callback_param_to_input_id(fn, decs[0])
            for node in ast.walk(fn):
                if not (isinstance(node, ast.BoolOp)
                        and isinstance(node.op, ast.Or)
                        and len(node.values) == 2):
                    continue
                left, right = node.values
                if not isinstance(left, ast.Name):
                    continue
                if not (isinstance(right, ast.Constant)
                        and isinstance(right.value, (int, float))
                        and not isinstance(right.value, bool)):
                    continue
                if left.id in COSMETIC_OR_DEFAULTS:
                    continue
                cid = bound.get(left.id)
                if cid in numeric:
                    out.append((
                        f"{path.relative_to(APP.parent)}:{node.lineno}",
                        fn.name, left.id, cid, right.value))
    return out


# --------------------------------------------------------------------------
class TestTheGuardIsNotInert:
    """★ 番人が空振りしていないこと（ver51.9 で 3 回空振りさせた反省）。"""

    def test_numeric_inputs_are_discoverable(self):
        ids = _numeric_input_ids()
        assert len(ids) >= 25, (
            f"数値入力が {len(ids)} 個しか見つからない。走査が壊れている疑い")

    def test_validation_machinery_is_importable(self):
        assert PARAM_BOUNDS, "PARAM_BOUNDS が空"
        assert _VALIDATED_INPUTS, "_VALIDATED_INPUTS が空"


class TestEveryNumericInputIsAccountedFor:
    """★ 本丸: 数値入力が漏れなく「検証済み」か「既知の未検証」に分類されること。"""

    def test_no_unclassified_numeric_input(self):
        ids = _numeric_input_ids()
        classified = set(_VALIDATED_INPUTS) | set(KNOWN_UNBOUNDED)
        new = sorted(set(ids) - classified)
        assert not new, (
            "境界検証に結線されていない数値入力が**新たに**増えた。\n"
            "`PARAM_BOUNDS` に範囲を足して `_VALIDATED_INPUTS` に登録するか、"
            "直せないなら理由付きで `KNOWN_UNBOUNDED` に記録すること:\n  "
            + "\n  ".join(f"{i}  ({ids[i]})" for i in new))

    def test_known_unbounded_entries_still_exist(self):
        """消えた入力の登録を残さない（登録簿の陳腐化を防ぐ）。"""
        ids = _numeric_input_ids()
        gone = sorted(set(KNOWN_UNBOUNDED) - set(ids))
        assert not gone, (
            "KNOWN_UNBOUNDED に画面へ存在しない id が残っている。"
            "登録から外すこと:\n  " + "\n  ".join(gone))

    def test_known_unbounded_does_not_include_validated_ones(self):
        """直ったら登録から外させる。"""
        fixed = sorted(set(KNOWN_UNBOUNDED) & set(_VALIDATED_INPUTS))
        assert not fixed, (
            "KNOWN_UNBOUNDED に載っているが実際には検証されている。"
            "直ったのは良いことなので登録から外すこと:\n  " + "\n  ".join(fixed))


class TestDeclaredBoundsActuallyApply:
    """★ 「宣言した対象が実在する」型（監査 R-01 と同じ）。

    `PARAM_BOUNDS` に書いても、画面の id と一致しなければ
    `validate_param` は `spec=None` で **常に ok を返す**。
    つまり **書いたのに一度も効かない**。実行時に何のエラーも出ない。
    """

    def test_every_param_bound_targets_a_real_input(self):
        ids = set(_numeric_input_ids())
        dead = sorted(set(PARAM_BOUNDS) - ids - set(KNOWN_DEAD_BOUNDS))
        assert not dead, (
            "`PARAM_BOUNDS` に、画面に存在しない id の定義がある。"
            "`validate_param` は未知の id を常に ok として通すので、"
            "**書いたつもりの範囲チェックが黙って効かない**:\n  "
            + "\n  ".join(dead))

    def test_known_dead_bounds_do_not_grow(self):
        ids = set(_numeric_input_ids())
        dead = set(PARAM_BOUNDS) - ids
        new = dead - set(KNOWN_DEAD_BOUNDS)
        assert not new, f"死んだ PARAM_BOUNDS の定義が増えた: {sorted(new)}"
        revived = sorted(set(KNOWN_DEAD_BOUNDS) & ids)
        assert not revived, (
            "KNOWN_DEAD_BOUNDS の定義が画面 id と一致するようになった。"
            "登録から外すこと（穴が塞がったのは良いこと）:\n  " + "\n  ".join(revived))

    def test_known_dead_bounds_entries_are_still_declared(self):
        """★ 登録簿の陳腐化を防ぐ。

        死んだ定義を **キー名ごと直した**場合、旧キーは `PARAM_BOUNDS` から
        消えるので上の 2 つはどちらも素通りしてしまう（実際に ver52.3 ② で
        素通りした）。「登録したキーが今も宣言されている」ことを別に見る。
        """
        stale = sorted(set(KNOWN_DEAD_BOUNDS) - set(PARAM_BOUNDS))
        assert not stale, (
            "KNOWN_DEAD_BOUNDS に、もう PARAM_BOUNDS に無いキーが残っている。"
            "定義を直した／消したなら登録からも外すこと:\n  " + "\n  ".join(stale))

    def test_validated_inputs_all_have_bounds(self):
        """`_VALIDATED_INPUTS` に載せたのに範囲定義が無いと、検証は素通りになる。"""
        missing = sorted(set(_VALIDATED_INPUTS) - set(PARAM_BOUNDS))
        assert not missing, (
            "`_VALIDATED_INPUTS` にあるが `PARAM_BOUNDS` に定義が無い。"
            "`validate_param` が常に ok を返すので検証は効いていない:\n  "
            + "\n  ".join(missing))

    def test_bounds_agree_with_the_layout(self):
        """★★ レイアウトを正として、`PARAM_BOUNDS` がそれと矛盾しないこと。

        ver52.3 ② で分かったのは、死んだ 4 定義が **キー名だけでなく
        範囲・既定値もレイアウトと食い違っていた**こと:

            umap_n_neighbors  (2, None, 15)  ↔ 画面 min=2, max=100, value=30
            umap_min_dist     (0.0, 1.0, 0.1)↔ 画面 value=0.3 (R も PreFlight も 0.3)
            pca_dims          (10, 100, None)↔ 画面 min=2, max=50, value=30

        キー名だけ直すと、今度は**正当な入力を弾く**検証が動き出す。
        画面に書いてある境界が唯一の正なので、それとの一致を固定する。
        """
        props = _numeric_input_props()
        bad = []
        for pid, spec in PARAM_BOUNDS.items():
            lo, hi, default, _label = spec
            p = props.get(pid)
            if not p:
                continue                      # 画面に無い id は別テストが見る
            if "min" in p and lo is not None and float(lo) != float(p["min"]):
                bad.append(f"{pid}: 下限 PARAM_BOUNDS={lo} ↔ 画面 min={p['min']}")
            if "max" in p and hi is not None and float(hi) != float(p["max"]):
                bad.append(f"{pid}: 上限 PARAM_BOUNDS={hi} ↔ 画面 max={p['max']}")
            if ("value" in p and default is not None
                    and float(default) != float(p["value"])):
                bad.append(
                    f"{pid}: 既定 PARAM_BOUNDS={default} ↔ 画面 value={p['value']}")
        assert not bad, (
            "`PARAM_BOUNDS` が画面の min/max/value と食い違っている。\n"
            "検証が正当な入力を弾くか、逆に不正な入力を通す:\n  "
            + "\n  ".join(bad))

    def test_validated_inputs_exist_on_screen(self):
        ids = set(_numeric_input_ids())
        missing = sorted(set(_VALIDATED_INPUTS) - ids)
        assert not missing, (
            "`_VALIDATED_INPUTS` に画面へ存在しない id がある。"
            "`Output(id, 'invalid')` の宛先が無いので結線が効かない:\n  "
            + "\n  ".join(missing))


class TestZeroIsNotSilentlyReplaced:
    """★ `x = x or DEFAULT` が数値入力に使われていないこと。

    0 は falsy なので、利用者が入れた 0 が既定値に化ける。
    「閾値 0 で全部見たい」が「閾値 0.5 で描画」に変わり、しかも無警告。
    """

    # ★ 母数 21 件すべてを分類する。ver52.3 ⑤ で直す（本コミットでは直さない）。
    #   検出を式ベースへ広げた結果 3 → 21 になった。従来は代入の形で
    #   近似していたので 1/7 しか見えていなかった。
    KNOWN = {
        # --- 重: 0 が正当な入力なのに既定値へ化ける ---
        ("interactive_deg.py", "fc_thresh"):
            "H-2 (重): volcano_fc_threshold に min= が無く 0 を入力できる。"
            "入力欄も赤くならないので、0 が 0.5 に化けたことに気付く手段が無い",
        ("interactive_deg.py", "p_thresh"):
            "H-2 (重): volcano_p_threshold も同様",
        ("interactive_deg.py", "label_top_n"):
            "★ 画面と資料が 0 を逆に解釈する。"
            "interactive_deg.py:1163 は `int(label_top_n or 5)` で 0 → 5 (ラベルが出る)、"
            "interactive_pptx.py:438 は `max(0, int(...))` で 0 → 0 (ラベルが出ない)。"
            "レイアウトは min=0 なので 0 は正当な入力（＝ラベル無し）。"
            "ver51.9 B-2 で PPTX だけ正しくした取りこぼし",
        # --- 軽: 0 は範囲外だが、欄が赤いまま図/処理は既定値で進む ---
        ("interactive_deg.py", "top_n"):
            "H-9 (軽): update_heatmap。heatmap_top_n は PARAM_BOUNDS(1,20,5) で"
            "検証済みなので 0 だと欄が赤くなるが、図は既定 5 で描かれ続ける",
        ("interactive_pptx.py", "value"):
            "軽: sync_export_top_n。input_export_top_n は min=1 なので 0 は範囲外。"
            "ただし 0 を入れると黙って 5 になる",
        # --- 探索窓・許容差・点数: 0 は物理的に無意味だが、
        #     「不正な入力を黙って既定値にする」形は同じ ---
        ("analysis_callbacks.py", "calibration_min_peaks"):
            "run_analysis。0 は無意味だが黙って 2 になる",
        ("analysis_callbacks.py", "search_window"):
            "auto_detect_observed_peaks。0 窓は何も一致しないが黙って 0.5 になる",
        ("interactive_calibration.py", "search_window"):
            "auto_detect_int_cal_peaks / auto_save_int_cal / save_int_cal_list",
        ("interactive_calibration.py", "min_peaks"):
            "auto_save_int_cal / save_int_cal_list",
        ("interactive_calibration.py", "tolerance"):
            "execute_reannotation。0 許容差は完全一致しか通さない",
        ("interactive_callbacks.py", "cal_min_peaks"):
            "load_stage_c_deg / load_stage_d_finish",
        ("interactive_callbacks.py", "cal_search_window"):
            "load_stage_c_deg / load_stage_d_finish",
        ("interactive_callbacks.py", "tolerance_mz"):
            "load_stage_c_deg (既定 0.1) / load_stage_d_finish (既定 0.01)。"
            "★ 同じ入力に対し既定値が 2 種類ある点も要確認",
    }

    def _key(self, loc, param):
        return (loc.split(":")[0].split("/")[-1], param)

    def test_no_new_or_default_on_numeric_inputs(self):
        new = [v for v in _or_default_violations()
               if self._key(v[0], v[2]) not in self.KNOWN]
        assert not new, (
            "数値入力に `x = x or DEFAULT` を使っている箇所が**新たに**増えた。\n"
            "0 が既定値に化けて、利用者がしていない設定で図が描かれる。\n"
            "`x if x is not None else DEFAULT` にすること:\n  "
            + "\n  ".join(f"{loc}  {fn}(): {p} = {p} or {d}   ← 入力 {cid}"
                          for loc, fn, p, cid, d in new))

    def test_known_or_defaults_do_not_shrink_silently(self):
        found = {self._key(v[0], v[2]) for v in _or_default_violations()}
        fixed = sorted(set(self.KNOWN) - found)
        assert not fixed, (
            "KNOWN に載っているが `or` 既定値が無くなっている。"
            "直ったのは良いことなので登録から外すこと:\n  "
            + "\n  ".join(f"{f}: {p}" for f, p in fixed))
