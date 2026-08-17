"""「インタラクティブ解析」ボタンで開いた画面を自動補完に乗っ取らせない。

--------------------------------------------------------------------------
症状 1: ボタンを押すと別の画面が開き、押し直しても直らない (S2)
--------------------------------------------------------------------------
ランディングの「インタラクティブ解析」ボタンは、
`interactive_entry_mode = "standalone"`（＝利用者が自分でプロジェクトを選ぶ）
を書いてタブを切り替える。ところが同じタブ切替で
`auto_fill_interactive_from_analysis` も走り、直前の解析の情報で

    entry_mode  → "sub_project"
    project     → 直前の解析のプロジェクト
    sub_project → 直前の解析のサブプロジェクト
    result_folder → 直前の解析の出力先

を**後勝ちで上書き**していた。`entry_mode="sub_project"` になると
`toggle_project_dropdown_visibility` がプロジェクト選択欄に
`display:none` を掛けるため、**選び直す UI ごと消える**。

`app_state` は `storage_type="session"` の Store で、解析完了後もタブを
閉じるまで残る。したがってホームに戻って同じボタンを押し直しても、
毎回同じ上書きが起きて復帰しない。タブを閉じるまでこのボタンは壊れたままになる。

さらに `entry_mode="sub_project"` は `auto_load_on_rds_ready` の実行条件も
満たすため、書き込まれた result_folder から**データ読み込みまで自動で走る**。
同コールバックのコメントは「entry_mode が手動 standalone の場合は自動実行
しない」と明記しており、その宣言も破られていた。

--------------------------------------------------------------------------
症状 2: プロジェクトに紐づかない解析でも選択欄が消える (S3)
--------------------------------------------------------------------------
プロジェクトを選ばずに解析した場合 `app_state["project_id"]` は空になる。
それでも `entry_mode` には位置固定のリテラル `"sub_project"` が書かれるため、
**選択されたプロジェクトが無いのに選択欄だけ消える**。プロジェクト選択欄が
無い以上その場では選べず、`populate_interactive_sub_projects` もサブプロ
選択を `no_update` にする分岐へ入るので、以後の選択も残り続ける。

entry_mode を「sub_project だ」と名乗るのは、実際にプロジェクトが
決まったときだけにする。
"""

import app.callbacks.interactive_project as ip

NO = ip.no_update

APP_STATE = {
    "full_output_dir": "/data/out/PREVIOUS_RUN",
    "project_id": "proj-94eb",
    "sub_project_id": "sub-87d0",
    "is_running": False,
}


def _call(entry_mode, app_state=APP_STATE, data_folder="/data/msi"):
    return ip.auto_fill_interactive_from_analysis(
        "interactive", app_state, data_folder, entry_mode, "analysis")


# ---------------------------------------------------------------------------
# 症状 1
# ---------------------------------------------------------------------------

def test_standalone_entry_is_left_alone():
    """★ standalone で入った画面は一切書き換えないこと。"""
    assert _call("standalone") == (NO,) * 5


def test_standalone_stays_standalone_even_with_a_finished_analysis():
    """直前の解析が残っていても standalone のままであること。

    app_state は session Store なのでタブを閉じるまで残る。ここが崩れると
    「押し直しても直らない」に戻る。
    """
    entry_mode = _call("standalone")[2]
    assert entry_mode is NO, (
        f"standalone が {entry_mode!r} へ書き換えられている"
        "（プロジェクト選択欄が消え、自動読み込みまで走る）")


def test_project_dropdown_stays_visible_in_standalone():
    """standalone のままならプロジェクト選択欄が消えないこと。"""
    assert ip.toggle_project_dropdown_visibility("standalone") == {}
    # 従来どおり、sub_project / shared では隠す
    assert ip.toggle_project_dropdown_visibility("sub_project") == {"display": "none"}
    assert ip.toggle_project_dropdown_visibility("shared") == {"display": "none"}


def test_existing_guards_are_unchanged():
    """sub_project / shared からの遷移は従来どおりスキップされること。"""
    assert _call("sub_project") == (NO,) * 5
    assert _call("shared") == (NO,) * 5


# ---------------------------------------------------------------------------
# 症状 2
# ---------------------------------------------------------------------------

def test_entry_mode_is_not_claimed_without_a_project():
    """★ プロジェクトが決まっていないのに sub_project を名乗らないこと。"""
    state = dict(APP_STATE, project_id="", sub_project_id="")
    result_folder, msi_folder, entry_mode, project, sub = _call("", state)

    assert result_folder == "/data/out/PREVIOUS_RUN", "結果フォルダの補完は従来どおり"
    assert msi_folder == "/data/msi"
    assert entry_mode is NO, (
        "プロジェクト未連携なのに sub_project へ切り替えている"
        "（選択欄が消えて選び直せなくなる）")
    assert project is NO and sub is NO


def test_entry_mode_switches_when_a_project_is_known():
    """プロジェクトが決まっていれば従来どおり sub_project へ切り替えること。"""
    result_folder, _, entry_mode, project, sub = _call("")
    assert result_folder == "/data/out/PREVIOUS_RUN"
    assert entry_mode == "sub_project"
    assert project == "proj-94eb"
    assert sub == "sub-87d0"


# ---------------------------------------------------------------------------
# 既存の前提（壊していないことの確認）
# ---------------------------------------------------------------------------

def test_other_tabs_and_pages_are_untouched():
    assert ip.auto_fill_interactive_from_analysis(
        "settings", APP_STATE, "/d", "", "analysis") == (NO,) * 5
    assert ip.auto_fill_interactive_from_analysis(
        "interactive", APP_STATE, "/d", "", "landing") == (NO,) * 5


def test_running_or_missing_analysis_is_skipped():
    assert _call("", None) == (NO,) * 5
    assert _call("", {}) == (NO,) * 5
    assert _call("", dict(APP_STATE, is_running=True)) == (NO,) * 5
    assert _call("", {"project_id": "p"}) == (NO,) * 5
