"""E2E smoke tests（Inc.3）。データ不要 — ログイン/UI 存在/タブ遷移を検証。

実行: `pytest App/tests/e2e/test_smoke.py`（playwright + Chromium が必要）。
無い環境では conftest の fixture が skip する。
データを要する操作（バリデーション赤表示・リセット・選択統計）は
test_interactive_data.py（requires_data）でカバーする。
"""

import pytest

pytestmark = pytest.mark.e2e


def test_app_loads_and_logged_in(page):
    # ログイン後、メインアプリのタブシェルが存在する
    assert page.locator("#main_tabs").count() >= 1


def test_interactive_panels_present(page):
    # dbc.Tabs は全タブを DOM に描画するため、未活性・未ロードでも id は存在する。
    # 今回追加した主要コンポーネントが欠落していないことの回帰チェック。
    expected = [
        "interactive_umap_plot",      # UMAP 本体
        "selection_summary_card",     # P1 ライブ選択統計
        "selection_groups_table",     # P3 選択グループ
        "btn_restore_deleted_group",  # Inc.1 Undo
        "onthefly_de_mode",           # P2 選択 DE
        "onthefly_de_volcano",        # P2 Volcano
        "feature_lists_table",        # P5-b Feature リスト
        "feature_list_picker",        # Inc.2 検索駆動
        "coexpr_scatter",             # P5-b 共発現散布図
        "hne_overlay_show",           # P4 H&E オーバーレイ
        "umap_facet_by",              # P5-a Split View
        "feature_colorscale_reset",   # Inc.1 リセット
        "volcano_reset",              # Inc.1 リセット
        "deg_markers_table",          # P1 マーカー表
    ]
    for cid in expected:
        assert page.locator(f"#{cid}").count() >= 1, f"missing #{cid}"
    # データソースの結果フォルダ入力も存在（インタラクティブタブが描画されている）
    assert page.locator("#interactive_result_folder").count() >= 1
