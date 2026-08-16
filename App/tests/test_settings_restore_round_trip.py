"""前回設定の保存と復元が往復すること (R13-05 / R13-03)。

このファイルが押さえているのは、利用者から見える 2 つの症状である。

--------------------------------------------------------------------------
症状 1: イオンモードだけ Negative で、付加イオンが Positive 4 種のまま
--------------------------------------------------------------------------
「イオンモード」は前回設定から復元されるのに、「Adductフィルター」の初期値は
画面に固定で書かれていた。イオンモードを変えたときに付加イオンを自動で
切り替えるコールバックは `prevent_initial_call=True` なので、**起動直後の
表示は直してくれない**。

その結果、前回 Negative で終えたラボが翌日アプリを開くと

    イオンモード  = Negative     (復元される)
    Adductフィルター = +H +Na +NH4 +K  (固定値のまま)

という物理的にありえない組み合わせが表示され、しかも利用者はイオンモードが
正しく Negative になっているので**触らない**。この状態で解析すると R には
ION_MODE="Negative" と ANNOT_ADDUCT_PATTERNS=c("+H","+Na","+NH4","+K") が
そのまま注入され、R 側は不一致を警告しないまま DB 照合が 0 件になり、
化合物名の代わりに m/z の文字列が出力に残る。

--------------------------------------------------------------------------
症状 2: 設定を変えて解析したのに、次に開くと既定へ戻っている
--------------------------------------------------------------------------
`save_last_settings()` は `_AUTO_SAVE_KEYS` に載っているキーだけを書き出す
ホワイトリスト方式で、載っていないキーは**例外も警告も出さずに捨てる**。
呼び出し側は正規化設定 (normalize_input / norm_mode ほか) を渡していたが
ホワイトリストに載っていなかったため、渡した側も画面側も何も気づかないまま
毎回既定値に戻っていた。逆に、画面が `ls.get(...)` で読もうとしているのに
どこからも保存されないキーもあった。

「渡す側」「ホワイトリスト」「画面が読む側」の 3 つが揃わないと復元は
成立しないので、3 つの集合を突き合わせて機械的に検査する。
"""

import ast
import pathlib
import re

import pytest

from app.config import (
    DEFAULT_ADDUCT_NEGATIVE,
    DEFAULT_ADDUCT_POSITIVE,
    adducts_for_ion_mode,
)
from app.services.session_manager import _AUTO_SAVE_KEYS

APP_DIR = pathlib.Path(__file__).resolve().parents[1] / "app"

# 旧い設定ファイルとの互換のために読むだけの別名。保存はしない。
#   annotation_path ← mrm_path / default_annotation_file ← default_mrm_file
LEGACY_ALIASES = {"mrm_path", "default_mrm_file"}


# ---------------------------------------------------------------------------
# 症状 1: イオンモードと付加イオンの整合
# ---------------------------------------------------------------------------

def test_adducts_for_ion_mode_pairs_correctly():
    assert adducts_for_ion_mode("Positive") == DEFAULT_ADDUCT_POSITIVE
    assert adducts_for_ion_mode("Negative") == DEFAULT_ADDUCT_NEGATIVE
    # 未知・未設定は Positive 側に倒す (従来の既定と同じ)
    assert adducts_for_ion_mode(None) == DEFAULT_ADDUCT_POSITIVE
    assert adducts_for_ion_mode("") == DEFAULT_ADDUCT_POSITIVE
    # 返すのは複製。呼び出し側が書き換えても既定値は汚れない
    got = adducts_for_ion_mode("Positive")
    got.append("+X")
    assert adducts_for_ion_mode("Positive") == DEFAULT_ADDUCT_POSITIVE


def _component_values(layout, ids):
    """レイアウト木から指定 id のコンポーネントの value を集める。"""
    found = {}

    def walk(node):
        cid = getattr(node, "id", None)
        if isinstance(cid, str) and cid in ids:
            found[cid] = getattr(node, "value", None)
        children = getattr(node, "children", None)
        if isinstance(children, (list, tuple)):
            for c in children:
                walk(c)
        elif children is not None:
            walk(children)

    walk(layout)
    return found


@pytest.mark.parametrize("ion_mode,expected", [
    ("Negative", DEFAULT_ADDUCT_NEGATIVE),
    ("Positive", DEFAULT_ADDUCT_POSITIVE),
])
def test_settings_tab_initial_adducts_follow_restored_ion_mode(
        monkeypatch, ion_mode, expected):
    """★ 復元したイオンモードと初期の付加イオンが噛み合っていること。

    自動切替コールバックは prevent_initial_call=True で初期表示に効かないので、
    ここがずれていると起動直後の画面がそのまま R へ流れる。
    """
    import app.layouts.settings_tab as st

    monkeypatch.setattr(
        st, "load_last_settings",
        lambda: {"ion_mode": ion_mode, "reanalysis_ion_mode": ion_mode})
    layout = st.create_settings_tab()
    vals = _component_values(
        layout, {"ion_mode", "adduct_filter",
                 "reanalysis_ion_mode", "reanalysis_adduct_filter"})

    assert vals["ion_mode"] == ion_mode
    assert vals["adduct_filter"] == expected, (
        f"イオンモード {ion_mode} なのに付加イオンが {vals['adduct_filter']}")
    assert vals["reanalysis_ion_mode"] == ion_mode
    assert vals["reanalysis_adduct_filter"] == expected


def test_settings_tab_keeps_explicitly_saved_adducts(monkeypatch):
    """利用者が絞り込んで保存した選択は、イオンモード既定より優先されること。"""
    import app.layouts.settings_tab as st

    monkeypatch.setattr(
        st, "load_last_settings",
        lambda: {"ion_mode": "Positive", "adduct_filter": ["+H"]})
    vals = _component_values(st.create_settings_tab(), {"adduct_filter"})
    assert vals["adduct_filter"] == ["+H"]


def test_auto_switch_callbacks_share_the_same_source():
    """画面初期値と自動切替が同じ既定値を返すこと (片方だけ変わるのを防ぐ)。"""
    from app.callbacks.file_handlers import (
        auto_switch_adduct, auto_switch_reanalysis_adduct)
    from app.callbacks.interactive_calibration import (
        auto_switch_int_cal_adduct, auto_switch_reann_adduct)

    for mode in ("Positive", "Negative"):
        want = adducts_for_ion_mode(mode)
        assert auto_switch_adduct(mode) == want
        assert auto_switch_reanalysis_adduct(mode) == want
        assert auto_switch_reann_adduct(mode) == want
        assert auto_switch_int_cal_adduct(mode, False) == want


# ---------------------------------------------------------------------------
# 症状 2: 保存経路・ホワイトリスト・復元経路の突き合わせ
# ---------------------------------------------------------------------------

def _keys_passed_to_save():
    """`save_last_settings({...})` に渡しているキーを全ソースから集める。"""
    keys = set()
    for path in APP_DIR.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        if "save_last_settings" not in src:
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = getattr(fn, "id", None) or getattr(fn, "attr", None)
            if name != "save_last_settings" or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Dict):
                for k in arg.keys:
                    if isinstance(k, ast.Constant) and isinstance(k.value, str):
                        keys.add(k.value)
    return keys


def _keys_restored_by_layouts():
    """レイアウトが `ls.get("...")` で復元しようとしているキー。"""
    keys = set()
    for path in (APP_DIR / "layouts").rglob("*.py"):
        keys |= set(re.findall(r'ls\.get\(\s*"([A-Za-z0-9_]+)"',
                               path.read_text(encoding="utf-8")))
    return keys


def test_every_saved_key_is_whitelisted():
    """★ 渡したのにホワイトリスト外 = 無音で捨てられているキーが無いこと。

    `save_last_settings` は `_AUTO_SAVE_KEYS` に無いキーを黙って落とす。
    呼び出し側は保存したつもりでいるので、誰も気づけない。
    """
    dropped = sorted(_keys_passed_to_save() - set(_AUTO_SAVE_KEYS))
    assert not dropped, (
        "save_last_settings に渡しているが _AUTO_SAVE_KEYS に無い "
        f"(無音で捨てられる): {dropped}")


def test_every_restored_key_has_a_save_path():
    """★ 画面が復元しようとしているキーに、保存経路があること。

    保存されないキーを `ls.get(...)` で読んでも常に既定値が返るだけで、
    「設定したのに次に開くと戻っている」になる。
    """
    missing = sorted(
        _keys_restored_by_layouts() - _keys_passed_to_save() - LEGACY_ALIASES)
    assert not missing, (
        "レイアウトが復元しようとしているが誰も保存していない: " + str(missing))


def test_restored_keys_survive_the_whitelist():
    """復元対象キーが _AUTO_SAVE_KEYS にも載っていること。"""
    missing = sorted(
        _keys_restored_by_layouts() - set(_AUTO_SAVE_KEYS) - LEGACY_ALIASES)
    assert not missing, (
        "レイアウトが復元するのに _AUTO_SAVE_KEYS に無い: " + str(missing))


def test_save_last_settings_round_trip(tmp_path, monkeypatch):
    """実際に書いて読み直し、復元対象キーが往復すること。"""
    import app.services.session_manager as sm

    monkeypatch.setattr(sm, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(sm, "_LAST_SETTINGS_FILE", tmp_path / "last_settings.json")

    payload = {
        "ion_mode": "Negative",
        "adduct_filter": ["-H"],
        "reanalysis_ion_mode": "Negative",
        "reanalysis_adduct_filter": ["-H"],
        "mz_align_ppm": 15,
        "use_annotation_check": ["db"],
        "normalize_input": "OFF",
        "norm_mode": "sqrt",
        "normalize_input_reanalysis": "ON",
        "norm_mode_reanalysis": "none",
    }
    sm.save_last_settings(payload)
    loaded = sm.load_last_settings()
    for k, v in payload.items():
        assert loaded.get(k) == v, f"{k} が往復しない ({loaded.get(k)!r})"


def test_unknown_keys_are_still_dropped(tmp_path, monkeypatch):
    """ホワイトリスト方式そのものは維持されていること (何でも保存しない)。"""
    import app.services.session_manager as sm

    monkeypatch.setattr(sm, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(sm, "_LAST_SETTINGS_FILE", tmp_path / "last_settings.json")
    sm.save_last_settings({"ion_mode": "Positive", "__not_a_setting__": 1})
    assert "__not_a_setting__" not in sm.load_last_settings()
