"""復元した設定が、直後の自動切替に上書きされないことの番人。

★ ver56.5 / デバッグ総点検 §4.2 (C06-1 / C13-H4):

  キャリブレーションの「付加イオン」チェックを +H だけに絞って保存しても、
  そのデータを読み込み直すたびに必ず既定の 4 種（+H / +Na / +NH4 / +K、
  Negative なら -H）へ戻ってしまう。しかも戻った既定値が自動保存によって
  設定ファイルへ書き直されるため、**絞り込みは二度と保持されない**。

■ なぜ起きるか

  データ読込の復元処理は `int_cal_ion_mode` と `int_cal_adduct_filter` を
  同じラウンドで書き戻す。ところが ion_mode の変化が
  `auto_switch_int_cal_adduct` を発火させ、復元したばかりの付加イオン選択を
  既定値で塗り潰していた。

  同じファイルの `update_int_cal_table_on_matrix` は、まさにこの事故を防ぐため
  `int_cal_restore_pending`（復元中フラグ）を見てスキップしている。
  付加イオン側だけがそのガードを持っていなかった。
"""
import ast
import inspect

import pytest
from dash import no_update

import app.callbacks.interactive_calibration as cal


class TestAdductRestoreGuard:
    """★ 本丸: 復元中は既定値で上書きしないこと。"""

    def test_does_not_overwrite_during_restore(self):
        assert cal.auto_switch_int_cal_adduct("Positive", True) is no_update, (
            "復元中なのに付加イオンを既定値で上書きしている。"
            "利用者が絞り込んだ選択が読み込みのたびに消える")

    def test_user_change_still_switches_defaults(self):
        """利用者がイオンモードを変えたときは従来どおり既定へ揃えること。"""
        from app.config import DEFAULT_ADDUCT_POSITIVE, DEFAULT_ADDUCT_NEGATIVE
        assert cal.auto_switch_int_cal_adduct("Positive", False) == DEFAULT_ADDUCT_POSITIVE
        assert cal.auto_switch_int_cal_adduct("Negative", False) == DEFAULT_ADDUCT_NEGATIVE
        # フラグ未設定 (None) も「復元中ではない」として扱う
        assert cal.auto_switch_int_cal_adduct("Negative", None) == DEFAULT_ADDUCT_NEGATIVE

    def test_guard_is_declared_in_the_callback(self):
        """`int_cal_restore_pending` を実際に受け取っていること。"""
        tree = ast.parse(inspect.getsource(cal))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef)
                    and node.name == "auto_switch_int_cal_adduct"):
                continue
            deco = ast.dump(node.decorator_list[0])
            assert "int_cal_restore_pending" in deco, (
                "復元中フラグを受け取っていない。復元直後に既定値で潰される")
            return
        pytest.fail("auto_switch_int_cal_adduct が見つからない")


class TestSiblingGuardStillWorks:
    """同じ理由でガードしている隣の処理を壊していないこと。"""

    def test_table_restore_guard_intact(self):
        out = cal.update_int_cal_table_on_matrix("agarose", "Positive", True)
        assert out[0] is no_update, "復元中にテーブルを上書きしている"
        assert out[1] is False, "復元フラグを降ろしていない"
