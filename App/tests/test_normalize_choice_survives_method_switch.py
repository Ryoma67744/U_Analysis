"""手で選んだ正規化の設定が、解析法の切替で消えないこと (S2)。

--------------------------------------------------------------------------
症状
--------------------------------------------------------------------------
画面には「TIMS(SCiLS RMS等で正規化済み)は既定OFF＝二重正規化を回避。
DESI(生データ)は既定ON。**解析法に応じて自動切替（手動変更可）**」と
書かれている。ところが `set_default_normalize` は解析法が変わるたびに
**現在の値を見ずに**方式既定を書き込むため、手動変更は一度も残らない。

とくに効くのが「再解析へ送る」経路で、UMAP 解析を選んでいる状態から
再解析へ切り替えると、正規化 OFF を選んでいても **ON に戻る**
(`set_default_normalize` は tims_v8 以外を全部「生データ」とみなすため、
 `tims_cluster_filter` でも ON を返す)。

さらに書き戻された値は `run_analysis` の自動保存で last_settings.json に
保存し直されるので、**再起動しても手動選択が戻ってこない**。

正規化 ON/OFF は R 側で `INPUT_NORMALIZED` になり、LogNormalize を
実行するかどうかが変わる。既に正規化済みの入力に対して ON のまま走ると
**二重正規化**になる。

なお ver56.5 で「保存されない (再起動で既定に戻る)」側は直っている。
残っていたのは、この**書き戻し**である。

--------------------------------------------------------------------------
直し方
--------------------------------------------------------------------------
画面の文言どおり「自動切替するが、手動変更は尊重する」を実装する。
直前に自動で入れた既定値を控えておき、**現在値がその既定のままなら**
新しい方式の既定へ切り替える。違っていれば利用者が選んだ値なので触らない。
"""

import pytest
from dash import no_update

from app.callbacks.file_handlers import set_default_normalize


def _switch(desi, tims, current, last_default):
    """コールバックを直接呼ぶ (戻り値: (値, 控え))。"""
    return set_default_normalize(desi, tims, current, last_default)


class TestTheAutomaticDefaultStillWorks:
    """★ 直しすぎの検出: 自動切替そのものは残すこと。"""

    def test_switching_to_tims_defaults_to_off(self):
        value, owner = _switch(None, "tims_v8", "ON", "ON")
        assert value == "OFF", "TIMS へ切り替えたら既定 OFF になること"
        assert owner == "OFF", "控えも更新すること"

    def test_switching_to_desi_defaults_to_on(self):
        value, owner = _switch("desi_v8", None, "OFF", "OFF")
        assert value == "ON" and owner == "ON"

    def test_the_first_switch_without_a_record_still_applies(self):
        """控えが無い状態 (初期) でも従来どおり既定を入れること。"""
        value, _ = _switch(None, "tims_v8", None, None)
        assert value == "OFF"


class TestAManualChoiceIsRespected:
    """★ 本丸: 手で選んだ値を書き戻さないこと。"""

    def test_a_manual_off_survives_a_switch_to_desi(self):
        # 直前の既定は ON だったが、利用者が OFF を選んでいる
        value, owner = _switch("desi_v8", None, "OFF", "ON")
        assert value is no_update, (
            "手で選んだ OFF が方式既定 ON で上書きされている"
            "（画面には「手動変更可」と書いてある）")
        assert owner is no_update, "控えも書き換えないこと"

    def test_a_manual_on_survives_a_switch_to_tims(self):
        value, _ = _switch(None, "tims_v8", "ON", "OFF")
        assert value is no_update, "手で選んだ ON が方式既定 OFF で上書きされている"

    def test_the_reanalysis_bridge_does_not_flip_it_on(self):
        """★ 「再解析へ送る」で OFF が ON に化けないこと。

        再解析 (tims_cluster_filter) は tims_v8 ではないので、
        従来の実装は「生データ扱い」で ON を返していた。
        """
        value, _ = _switch(None, "tims_cluster_filter", "OFF", "OFF")
        assert value != "ON", (
            "再解析へ送った瞬間に正規化が ON へ化ける"
            "（正規化済みの入力に二重正規化がかかる）")


class TestTheUiPromiseIsWrittenDown:
    """画面の文言と実装が食い違わないこと。"""

    def test_the_screen_still_promises_manual_override(self):
        from pathlib import Path
        src = (Path(__file__).resolve().parents[1]
               / "app" / "layouts" / "settings_tab.py").read_text(encoding="utf-8")
        assert "手動変更可" in src, (
            "画面の説明から「手動変更可」が消えている。"
            "実装を変えるなら文言も一緒に直すこと")

    def test_the_record_store_exists_in_the_layout(self):
        """控えの置き場がレイアウトに実在すること。"""
        from app.layouts.settings_tab import create_settings_tab

        ids = set()

        def walk(node):
            cid = getattr(node, "id", None)
            if isinstance(cid, str):
                ids.add(cid)
            children = getattr(node, "children", None)
            if isinstance(children, (list, tuple)):
                for c in children:
                    walk(c)
            elif children is not None:
                walk(children)

        walk(create_settings_tab())
        assert "normalize_default_owner" in ids, (
            "自動で入れた既定値の控えがレイアウトに無い"
            "（存在しない宛先へ書くとコールバックごと無効になる）")
