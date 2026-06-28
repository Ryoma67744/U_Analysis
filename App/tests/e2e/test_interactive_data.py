"""E2E（データ依存）テスト（Inc.3）。

実データ（R/Seurat + 小規模結果フォルダ RDS）が必要なため `requires_data` マーク。
環境変数 `E2E_RESULT_FOLDER` に結果フォルダのパスを指定したときのみ実行し、
未指定なら skip する。実行例:
    E2E_RESULT_FOLDER=/app/Data/Other/output/<proj> \
        pytest -m requires_data App/tests/e2e/test_interactive_data.py
"""

import os

import pytest

from tests.e2e.conftest import open_interactive_tab

pytestmark = [pytest.mark.e2e, pytest.mark.requires_data]

RESULT_FOLDER = os.environ.get("E2E_RESULT_FOLDER")


@pytest.fixture
def loaded_page(page):
    if not RESULT_FOLDER:
        pytest.skip("E2E_RESULT_FOLDER 未設定（実データが必要）")
    open_interactive_tab(page)
    page.fill("#interactive_result_folder", RESULT_FOLDER)
    page.click("#scan_result_folder")
    page.wait_for_timeout(1500)
    page.click("#load_interactive_data")
    # 抽出（R）に時間がかかるため、UMAP 本体に点が出るまで待つ
    page.wait_for_timeout(8000)
    return page


def test_data_loads_and_clusters_shown(loaded_page):
    # クラスタ統計テーブルに行が出る（データ読込成功の指標）
    assert loaded_page.locator("#cluster_stats_table").count() >= 1
    # UMAP プロットが描画される
    assert loaded_page.locator("#interactive_umap_plot").count() >= 1


def test_save_selection_group_roundtrip(loaded_page):
    # 名前を入れて保存ボタン（選択が空ならアプリは警告を出すだけでエラーにならない）
    loaded_page.fill("#selection_group_name", "E2E領域")
    loaded_page.click("#btn_save_selection_group")
    loaded_page.wait_for_timeout(1000)
    assert loaded_page.locator("#selection_groups_status").count() >= 1
