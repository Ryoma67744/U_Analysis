# 付録A: 全ボタン結線表 (214/214)

U_Analysis デバッグ総点検 (2026-08) の付録。本文は `DEBUG_AUDIT_2026-08.md`。

**生成方法**: AST 全走査 (btn_raw.json: dbc.Button 呼出 205 箇所; html.Button 0)。factory ヘルパー 2 種 (sidebar._path_input_row, env_settings_modal._row) を呼出側 17 箇所へ解決 (うち browse_id=None の 3 箇所はボタン非生成)。配線は cb_scan.json (345 callback の Input 静的解析; パターン ALL/MATCH と 動的 Input リスト _BROWSE_BUTTONS(35鍵)/_PATH_INPUT_IDS を展開) + C14 clientside 監査で判定し、C01-C11 監査行 (buttons フィールド) と突合済。

**内訳**: dbc_button_call_sites=205 / rendered_button_entities_rows=214 / static_ids=193 / pattern_types=19 / pattern_generation_sites=22 / upload_proxy_buttons_no_id=2 / wired=214 / unwired=0

**未結線ボタン: 0 件**(全ボタンがコールバックへ到達することを機械照合で確認)


凡例: 「発火するコールバック」= そのボタンの押下を受け取る関数。「効果」= 利用者から見て何が起きるか。「判定」= 監査結果(OK 以外は本文の該当項目を参照)。


## App/app/callbacks/data_management_callbacks.py (3 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `{"type":"dm_audit_pick","index\|path\|cluster":ANY}` | 移動元に入れる | data_management_callbacks.py:on_audit_pick | 監査結果の該当パスを移動元入力欄へセット | OK |
| `{"type":"dm_crumb","index\|path\|cluster":ANY}` |  | data_management_callbacks.py:on_crumb_click | パンくずの該当ディレクトリへ移動し一覧を更新 | OK |
| `{"type":"dm_restore_btn","index\|path\|cluster":ANY}` | ↩ 復元 | data_management_callbacks.py:on_restore | スキャン結果の該当項目をプロジェクトとして復元 | OK |

## App/app/callbacks/file_handlers.py (1 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `{"type":"btn_remove_extra_folder","index\|path\|cluster":ANY}` | × | file_handlers.py:remove_extra_folder | TIMS 追加データフォルダ一覧から該当フォルダを除去 | OK |

## App/app/callbacks/interactive_fullscreen.py (1 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `fs_spatial_marker_auto_btn` | Auto | interactive_spatial.py:auto_fs_spatial_marker | フルスクリーン Spatial のマーカーサイズを自動調整 | OK |

## App/app/callbacks/lite_view_callbacks.py (2 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `{"type":"lv_card_toggle","index\|path\|cluster":ANY}` | ▶ 詳細を表示 (UMAP / Spatial / Volcano) | lite_view_callbacks.py:toggle_cluster_card | 軽量ビューアのクラスタカードを展開し UMAP/Spatial/Volcano を遅延描画 | OK |
| `{"type":"lv_volcano_toggle","index\|path\|cluster":ANY}` | ▼ Volcano Plot を表示 | lite_view_callbacks.py:toggle_volcano_section | クラスタカード内の Volcano セクション表示を切替 | OK |

## App/app/callbacks/project_callbacks.py (11 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `{"type":"delete_project_btn","index\|path\|cluster":ANY}` | x | project_callbacks.py:toggle_delete_modal | 該当プロジェクトの削除確認モーダルを開く | SUSPECT |
| `{"type":"delete_share_btn","index\|path\|cluster":ANY}` | 削除 | project_callbacks.py:open_share_delete_modal | 該当共有リンクの削除確認モーダルを開く | OK |
| `{"type":"delete_sub_btn","index\|path\|cluster":ANY}` | x | project_callbacks.py:toggle_delete_sub_modal | 該当サブプロジェクトの削除確認モーダルを開く | SUSPECT |
| `{"type":"edit_project_btn","index\|path\|cluster":ANY}` | ✎ | project_callbacks.py:toggle_edit_project_modal | 該当プロジェクトの編集モーダルを現在値付きで開く | OK |
| `{"type":"edit_sub_btn","index\|path\|cluster":ANY}` | ✎ | project_callbacks.py:toggle_edit_sub_modal | 該当サブプロジェクトの編集モーダルを開く | SUSPECT |
| `{"type":"select_project_btn","index\|path\|cluster":ANY}` | 開く | project_callbacks.py:select_project | プロジェクトを開きアクションページへ移動 | OK |
| `{"type":"sub_action_add_molinfo","index\|path\|cluster":ANY}` | 分子情報を登録 | add_molinfo_callbacks.py:open_add_molinfo | 分子情報登録モーダルを開く | OK |
| `{"type":"sub_action_analysis","index\|path\|cluster":ANY}` | 解析 | project_callbacks.py:sub_action_new_analysis | サブプロジェクト設定を解析フォームへ読込み解析タブへ移動 | SUSPECT |
| `{"type":"sub_action_annotations","index\|path\|cluster":ANY}` | 化合物名 | annotation_preview_callbacks.py:open_annotation_preview | 化合物名(アノテーション)プレビューモーダルを開く | OK |
| `{"type":"sub_action_interactive","index\|path\|cluster":ANY}` | インタラクティブ | project_callbacks.py:sub_action_interactive | 当該結果を対象にインタラクティブ解析ページへ移動 | OK |
| `{"type":"sub_action_share","index\|path\|cluster":ANY}` | 共有 | project_callbacks.py:open_share_modal | 共有モーダルを開きリンク一覧を表示 | OK |

## App/app/layouts/action_page.py (14 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `back_to_landing` | < プロジェクト一覧に戻る | project_callbacks.py:back_to_landing | プロジェクト一覧(ランディング)へ戻る | OK |
| `cancel_create_sub_project` | キャンセル | project_callbacks.py:toggle_create_sub_modal | 新規サブプロジェクト作成モーダルを閉じる | SUSPECT |
| `cancel_delete_share` | キャンセル | project_callbacks.py:cancel_delete_share_link | 共有リンク削除確認モーダルを閉じる | OK |
| `cancel_delete_sub_project` | キャンセル | project_callbacks.py:toggle_delete_sub_modal | サブプロジェクト削除確認モーダルを閉じる | SUSPECT |
| `cancel_edit_sub_project` | キャンセル | project_callbacks.py:toggle_edit_sub_modal | サブプロジェクト編集モーダルを閉じる | SUSPECT |
| `close_share_modal` | 閉じる | project_callbacks.py:close_share_modal | 共有モーダルを閉じる | OK |
| `confirm_create_sub_project` | 作成 | project_callbacks.py:toggle_create_sub_modal<br>project_callbacks.py:handle_create_sub_project | サブプロジェクトを作成し一覧更新+モーダルを閉じる | SUSPECT |
| `confirm_delete_share` | 削除 | project_callbacks.py:confirm_delete_share_link | 共有リンクを削除(永続リンク含む) | OK |
| `confirm_delete_sub_project` | 削除 | project_callbacks.py:toggle_delete_sub_modal<br>project_callbacks.py:handle_delete_sub_project | サブプロジェクトを削除し一覧を更新 | SUSPECT |
| `confirm_edit_sub_project` | 保存 | project_callbacks.py:toggle_edit_sub_modal<br>project_callbacks.py:handle_edit_sub_project | サブプロジェクトの変更を保存 | SUSPECT |
| `generate_share_link` | 共有リンクを生成 | project_callbacks.py:generate_share_link | 共有リンクを生成し URL を表示 | OK |
| `header_title_home_btn_action` | MSI Analysis Application | project_callbacks.py:header_title_to_landing | ヘッダータイトルクリックでランディングへ戻る | OK |
| `open_create_sub_project_modal` | + 新規サブプロジェクト | project_callbacks.py:toggle_create_sub_modal | 新規サブプロジェクト作成モーダルを開く | SUSPECT |
| `project_info_save_btn` | 💾 保存 | project_callbacks.py:save_project_info | プロジェクトの URL 3 種+メモを保存 | OK |

## App/app/layouts/add_molinfo_modal.py (2 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `add_molinfo_close_btn` | 閉じる | add_molinfo_callbacks.py:close_add_molinfo | 分子情報登録モーダルを閉じる | OK |
| `add_molinfo_confirm_btn` | この内容で登録 | add_molinfo_callbacks.py:confirm_add_molinfo | 入力した分子情報を molinfo として登録しサブプロジェクト一覧を更新 | OK |

## App/app/layouts/annotation_preview_modal.py (1 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `annotation_preview_close_btn` | 閉じる | annotation_preview_callbacks.py:close_annotation_preview | 化合物名プレビューモーダルを閉じる | OK |

## App/app/layouts/data_management_subtab.py (8 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `dm_browse_move_dest` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを dm_move_dest_path 欄へ反映 | OK |
| `dm_browse_move_src` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを dm_move_src 欄へ反映 | OK |
| `dm_move_btn` | 📦 移動する | data_management_callbacks.py:on_move_request | 移動プランを検証し確認モーダルを表示 | OK |
| `dm_move_cancel_btn` | キャンセル | data_management_callbacks.py:on_move_cancel | 移動確認モーダルを閉じる | OK |
| `dm_move_exec_btn` | 移動を実行 | data_management_callbacks.py:on_move_execute | フォルダ移動を実行し結果を表示 | OK |
| `dm_refresh_btn` | 🔄 再読込 | data_management_callbacks.py:render_layout_summary<br>data_management_callbacks.py:render_directory<br>data_management_callbacks.py:render_result_audit<br>data_management_callbacks.py:render_storage_stats<br>data_management_callbacks.py:render_backup_list | データ管理タブの各一覧(配置/ディレクトリ/監査/使用量/バックアップ)を再読込 | OK |
| `dm_scan_btn` | 🔍 出力フォルダをスキャン | data_management_callbacks.py:on_scan | 出力フォルダをスキャンし未登録項目の復元候補を一覧表示 | OK |
| `{"type":"dm_loc_btn","index\|path\|cluster":ANY} ×4` | DESI生データ | data_management_callbacks.py:on_location_select | 表示対象の保存場所(DESI/TIMS/出力/内部)を切替 | OK |

## App/app/layouts/env_settings_modal.py (5 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `browse_env_desi_data_dir` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを env_desi_data_dir 欄へ反映 | OK |
| `browse_env_r_home` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを env_r_home 欄へ反映 | OK |
| `browse_env_tims_data_dir` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを env_tims_data_dir 欄へ反映 | OK |
| `env_settings_cancel_btn` | キャンセル | env_settings_callbacks.py:toggle_env_settings_modal | 環境設定モーダルを閉じる | OK |
| `env_settings_save_btn` | 保存 | env_settings_callbacks.py:save_env_settings | .env に環境設定を保存(アプリ再起動で有効) | OK |

## App/app/layouts/file_browser_modal.py (4 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `fb_cancel_btn` | キャンセル | file_handlers.py:close_file_browser | ファイルブラウザを選択せず閉じる | OK |
| `fb_go_btn` | 移動 | file_handlers.py:update_file_browser | 入力したパスへファイルブラウザ内で移動 | OK |
| `fb_select_btn` | 選択 | file_handlers.py:apply_file_browser_selection | 現在の選択パスを確定し呼び出し元の入力欄へ反映 | OK |
| `{"type":"fb_shortcut","index\|path\|cluster":ANY}` |  | file_handlers.py:handle_fb_shortcut | ショートカット先(ドライブ/既定フォルダ)へジャンプ | OK |

## App/app/layouts/hne_overlay_tab.py (6 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `hne_assign_btn` | ③ 領域を spot に割当 → 集計 | hne_overlay_callbacks.py:hne_assign_and_summarize | 確定領域を spot へ割当し領域別集計表を表示 | OK |
| `hne_export_btn` | ④ 解析用データ出力 (ZIP) | hne_overlay_callbacks.py:hne_export_stage_a | HNE 領域割当の解析用データを ZIP 生成しダウンロード(2 段階) | OK |
| `hne_landmark_clear` | 対応点をクリア | hne_overlay_callbacks.py:hne_capture_landmark | HNE 位置合わせの対応点をすべてクリア | OK |
| `hne_polygon_clear_draft` | 下書きクリア | hne_overlay_callbacks.py:hne_polygon_draft | HNE 領域ポリゴンの下書きを消去 | OK |
| `hne_polygon_commit` | 領域を確定 | hne_overlay_callbacks.py:hne_polygon_commit | 下書きポリゴンを領域として確定 | OK |
| `hne_polygon_undo` | 頂点を取り消し | hne_overlay_callbacks.py:hne_polygon_draft | 下書きポリゴンの最後の頂点を取り消し | OK |

## App/app/layouts/interactive_tab.py (69 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `(no id: dcc.Upload#upload_feature_list の子)` | CSV取込 | interactive_feature_lists.py:mutate_feature_lists | クリックでファイル選択(dcc.Upload)を開き、選択 CSV を feature リストとして取込 | OK |
| `(no id: dcc.Upload#upload_selection_groups の子)` | CSV取込 | interactive_selection_groups.py:mutate_selection_groups | クリックでファイル選択(dcc.Upload)を開き、選択 CSV を選択グループとして取込 | OK |
| `add_feature_bookmark_btn` | ★ 追加 | interactive_deg.py:add_feature_bookmark | 表示中の feature をブックマーク(★リスト)に追加し設定を保存 | OK |
| `apply_feature_mz_filter` | 絞り込み | interactive_deg.py:apply_mz_filter | m/z 範囲で feature 候補ドロップダウンを絞り込み | OK |
| `browse_int_cal_annotation` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを int_cal_annotation_path 欄へ反映 | OK |
| `browse_interactive_msi` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを interactive_msi_folder 欄へ反映 | OK |
| `browse_interactive_result` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを interactive_result_folder 欄へ反映 | OK |
| `browse_reann_annotation` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを reann_annotation_path 欄へ反映 | OK |
| `btn_batch_save_deg` | 📷 一括保存 | interactive_batch_save.py:cb_batch_save_deg | DEG タブの表示中図一式を結果フォルダへ PNG 一括保存(provenance 記録) | OK |
| `btn_batch_save_feature` | 📷 一括保存 | interactive_batch_save.py:cb_batch_save_feature | Feature タブの図一式を結果フォルダへ PNG 一括保存(provenance 記録) | OK |
| `btn_batch_save_spatial` | 📷 一括保存 | interactive_batch_save.py:cb_batch_save_spatial | Spatial タブの図一式を結果フォルダへ PNG 一括保存(provenance 記録) | OK |
| `btn_batch_save_umap` | 📷 一括保存 | interactive_batch_save.py:cb_batch_save_umap | UMAP タブの図一式を結果フォルダへ PNG 一括保存(provenance 記録) | SUSPECT |
| `btn_cancel_load` | キャンセル | interactive_callbacks.py:cancel_data_load | 実行中のデータ読込を中断しプログレス表示を閉じる | SUSPECT |
| `btn_combine_groups` | 結合 | interactive_selection_groups.py:mutate_selection_groups | 選択した複数の選択グループを結合して新グループを作成 | SUSPECT |
| `btn_delete_feature_list` | 削除 | interactive_feature_lists.py:mutate_feature_lists | 選択中の feature リストを削除 | OK |
| `btn_delete_group` | 削除 | interactive_selection_groups.py:mutate_selection_groups | 選択中の選択グループを削除(取り消し可) | SUSPECT |
| `btn_download_methods` | Methods をダウンロード | provenance_callbacks.py:download_methods_bundle | Methods 文書一式(ZIP)をブラウザダウンロード | OK |
| `btn_export_conditions` | 📋 解析条件をまとめて出力 | provenance_callbacks.py:export_conditions_bundle | 解析条件+日英 Methods を結果フォルダ provenance/ へ書き出し | OK |
| `btn_export_data` | 📥 データ出力 (UMAP cluster) | interactive_data_export.py:data_export_start | UMAP/クラスタ等のデータ出力ジョブを開始し進捗表示(完了で ZIP DL) | OK |
| `btn_export_feature_lists` | CSV出力 | interactive_feature_lists.py:export_feature_lists | Feature リストを Feature,List 形式 CSV でダウンロード | OK |
| `btn_export_groups` | CSV出力 | interactive_selection_groups.py:export_selection_groups | 選択グループを CellID,Group 形式 CSV でダウンロード | OK |
| `btn_export_marker_table` | CSV出力 | interactive_loupe.py:export_marker_table | マーカー表を CSV でダウンロード | OK |
| `btn_export_onthefly_de` | CSV出力 | interactive_de.py:export_onthefly_de | オンザフライ DE 結果表を CSV でダウンロード | OK |
| `btn_export_report` | 📊 レポート出力 (.pptx) | interactive_pptx.py:cb_export_report | PPTX レポートを生成しダウンロード | OK |
| `btn_list_from_bookmarks` | ブックマークから作成 | interactive_feature_lists.py:mutate_feature_lists | ブックマーク中の feature から新しいリストを作成 | OK |
| `btn_list_from_mzfilter` | 絞り込みから作成 | interactive_feature_lists.py:mutate_feature_lists | 現在の m/z 絞り込み結果から feature リストを作成 | OK |
| `btn_list_from_picker` | 選択 feature でリスト作成 | interactive_feature_lists.py:mutate_feature_lists | ピッカーで選択した feature からリストを作成 | OK |
| `btn_load_group_to_selection` | 現在の選択に読込 | interactive_selection_groups.py:load_group_to_selection | 保存済みグループを現在のセル選択へ読込(図にハイライト反映) | OK |
| `btn_methods_close` | 閉じる | provenance_callbacks.py:toggle_methods_modal | Methods モーダルを閉じる | SUSPECT |
| `btn_methods_copy` | 📋 書式つきでコピー | provenance_callbacks.py:clientside:inline(clientside) | Methods 本文を書式付きでクリップボードへコピー(clientside) | OK |
| `btn_methods_unlock` | 表示 | provenance_callbacks.py:unlock_methods | マスターパスワード検証後に Methods 本文を表示 | OK |
| `btn_open_lite_viewer` | 🔗 軽量ビューアを開く（新タブ） | lite_view_callbacks.py:_flush_settings_before_lite_open | 設定保存後、軽量ビューアを新タブで開く(store 経由 clientside window.open) | OK |
| `btn_rename_feature_list` | 改名 | interactive_feature_lists.py:mutate_feature_lists | 選択中の feature リストを改名 | OK |
| `btn_rename_group` | 改名 | interactive_selection_groups.py:mutate_selection_groups | 選択中の選択グループを改名 | SUSPECT |
| `btn_restore_deleted_group` | 削除を取り消す | interactive_selection_groups.py:mutate_selection_groups | 直前に削除した選択グループを復元 | SUSPECT |
| `btn_run_coexpr` | 共発現を描画 | interactive_feature_lists.py:run_coexpression | リスト A×B の共発現プロットを描画 | SUSPECT |
| `btn_run_onthefly_de` | DE 実行 | interactive_de.py:run_onthefly_de | 選択グループ間の DE 計算を実行し結果表を表示 | OK |
| `btn_save_selection_group` | 現在の選択を保存 | interactive_selection_groups.py:mutate_selection_groups | 現在のセル選択を新規グループとして保存 | SUSPECT |
| `btn_send_to_reanalysis` | 再解析フォームへ送る | interactive_reanalysis_bridge.py:send_to_reanalysis | 選択クラスタ/RDS を再解析フォームへ転記し設定タブへ切替 | OK |
| `btn_set_thumbnail_spatial` | 📌 サムネ登録 | interactive_batch_save.py:cb_set_thumbnail_spatial | 表示中 Spatial 図をプロジェクトのサムネイルに登録 | OK |
| `btn_set_thumbnail_umap` | 📌 サムネ登録 | interactive_batch_save.py:cb_set_thumbnail_umap | 表示中 UMAP 図をプロジェクトのサムネイルに登録 | SUSPECT |
| `btn_show_methods` | 📝 Methods 文を表示 | provenance_callbacks.py:toggle_methods_modal | Methods モーダルを開く | SUSPECT |
| `close_save_as_project_modal` | キャンセル | interactive_project.py:toggle_save_as_project_modal | プロジェクト保存モーダルを閉じる | OK |
| `cluster_rename_apply_btn` | 適用 | interactive_cluster.py:apply_cluster_rename | クラスタ表示名の変更を適用し全図へ反映 | OK |
| `cluster_rename_reset_btn` | リセット | interactive_cluster.py:apply_cluster_rename | クラスタ表示名を既定へ戻す | OK |
| `execute_save_as_project` | 保存 | interactive_project.py:execute_save_as_project | 現在のインタラクティブ状態を新規プロジェクトとして保存 | OK |
| `expand_deg_btn` | ⤢ | interactive_fullscreen.py:toggle_fullscreen | DEG(Volcano)図をフルスクリーンモーダルで表示 | SUSPECT |
| `expand_feature_btn` | ⤢ | interactive_fullscreen.py:toggle_fullscreen | Feature 図をフルスクリーンモーダルで表示 | SUSPECT |
| `expand_spatial_btn` | ⤢ | interactive_fullscreen.py:toggle_fullscreen | Spatial 図をフルスクリーンモーダルで表示 | SUSPECT |
| `expand_umap_btn` | ⤢ | interactive_fullscreen.py:toggle_fullscreen | UMAP 図をフルスクリーンモーダルで表示 | SUSPECT |
| `feature_colorscale_reset` | リセット | interactive_resets.py:reset_feature_colorscale | Feature カラースケール設定を既定に戻す | OK |
| `feature_marker_auto_btn` | Auto | interactive_spatial.py:auto_feature_marker | Feature 図のマーカーサイズを自動調整 | OK |
| `hne_overlay_reset` | リセット | interactive_resets.py:reset_hne_overlay | HNE オーバレイ設定をリセット | OK |
| `int_cal_add_row` | 行追加 | interactive_calibration.py:add_int_cal_row | 内部キャリブレーション表に空行を追加 | OK |
| `int_cal_apply` | キャリブレーション適用 | interactive_calibration.py:apply_int_calibration | 内部キャリブレーションを適用し m/z 補正を実行 | SUSPECT |
| `int_cal_auto_detect` | ピーク自動検出 | interactive_calibration.py:auto_detect_int_cal_peaks | ピーク自動検出で内部キャリブレーション表の観測 m/z を補完 | SUSPECT |
| `int_cal_delete_rows` | 選択行削除 | interactive_calibration.py:delete_int_cal_rows | 内部キャリブレーション表の選択行を削除 | OK |
| `int_cal_save_list` | List保存 | interactive_calibration.py:save_int_cal_list | 内部キャリブレーションリストを保存 | SUSPECT |
| `load_interactive_data` | データを読み込む | interactive_callbacks.py:load_stage_a_show_progress | 選択 RDS の読込を開始(進捗表示→完了で図を表示) | OK |
| `open_save_as_project_modal` | プロジェクトとして保存 | interactive_project.py:toggle_save_as_project_modal | プロジェクトとして保存モーダルを開く | OK |
| `reann_execute_btn` | 再アノテーション実行 | interactive_calibration.py:execute_reannotation | 再アノテーション(m/z 照合)を実行し結果を反映 | OK |
| `remove_feature_bookmark_btn` | ✕ | interactive_deg.py:remove_feature_bookmark | 選択中の feature ブックマークを削除 | OK |
| `scan_result_folder` | スキャン | interactive_callbacks.py:scan_rds_files | 結果フォルダをスキャンし RDS 候補ドロップダウンを更新 | OK |
| `spatial_marker_auto_btn` | Auto | interactive_spatial.py:auto_spatial_marker | Spatial 図のマーカーサイズを自動調整 | OK |
| `toggle_integration_method` | 解析手法 ▼ | interactive_callbacks.py:toggle_integration_method | 解析手法セクションの展開/折りたたみを切替 | OK |
| `umap_polygon_clear` | クリア | interactive_loupe.py:umap_polygon_draft | UMAP 投げ縄選択の下書きをクリア | OK |
| `umap_polygon_commit` | 確定 | interactive_loupe.py:umap_polygon_commit | 下書き多角形内のセルを選択へ確定(結合/置換) | OK |
| `umap_polygon_undo` | 1点取消 | interactive_loupe.py:umap_polygon_draft | UMAP 投げ縄下書きの頂点を 1 点取り消し | OK |
| `volcano_reset` | リセット | interactive_resets.py:reset_volcano | Volcano 図の閾値/表示設定を既定へリセット | OK |

## App/app/layouts/landing_page.py (18 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `browse_edit_thumbnail` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを edit_project_thumbnail 欄へ反映 | OK |
| `browse_restore_scan_folder` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを restore_scan_folder 欄へ反映 | OK |
| `cancel_create_project` | キャンセル | project_callbacks.py:toggle_create_modal | 新規プロジェクト作成モーダルを閉じる | OK |
| `cancel_delete_project` | キャンセル | project_callbacks.py:toggle_delete_modal | プロジェクト削除確認モーダルを閉じる | SUSPECT |
| `cancel_edit_project` | キャンセル | project_callbacks.py:toggle_edit_project_modal | プロジェクト編集モーダルを閉じる | OK |
| `close_restore_modal_btn` | 閉じる | project_callbacks.py:toggle_restore_modal | プロジェクト復元モーダルを閉じる | OK |
| `confirm_create_project` | 作成 | project_callbacks.py:handle_create_project | プロジェクトを新規作成し一覧を更新 | OK |
| `confirm_delete_project` | 削除 | project_callbacks.py:toggle_delete_modal<br>project_callbacks.py:handle_delete_project | プロジェクトを削除し一覧を更新 | SUSPECT |
| `confirm_edit_project` | 保存 | project_callbacks.py:handle_edit_project | プロジェクトの名称/説明/サムネ変更を保存 | OK |
| `open_change_password_btn` | パスワード変更 | auth_callbacks.py:toggle_change_password_modal | パスワード変更モーダルを開く | OK |
| `open_create_project_modal` | + 新規プロジェクト | project_callbacks.py:toggle_create_modal | 新規プロジェクト作成モーダルを開く | OK |
| `open_env_settings_modal_landing` | ⚙ 環境設定 | env_settings_callbacks.py:toggle_env_settings_modal | 環境設定モーダルを開き現在の .env 値を読込表示 | OK |
| `open_interactive_from_landing_btn` | インタラクティブ解析 | project_callbacks.py:open_interactive_from_landing | ランディングからインタラクティブ解析ページへ移動 | OK |
| `open_parquet_maintenance_modal_landing` | 📦 Parquet 再パック | parquet_maintenance_callbacks.py:toggle_parquet_maintenance_modal | Parquet 再パックモーダルを開く | OK |
| `open_rds_maintenance_modal_landing` | 🧹 RDS 軽量化 | rds_maintenance_callbacks.py:toggle_rds_maintenance_modal | RDS 軽量化モーダルを開く | OK |
| `open_restore_modal_btn` | 復元 | project_callbacks.py:toggle_restore_modal | プロジェクト復元モーダルを開く | OK |
| `restore_execute_btn` | 選択したプロジェクトを復元 | project_callbacks.py:execute_restore | 選択した復元アクションを実行しプロジェクトを再登録 | OK |
| `restore_scan_btn` | スキャン開始 | project_callbacks.py:execute_scan | 指定フォルダを再帰スキャンし復元候補(_project_meta.json)を一覧表示 | OK |

## App/app/layouts/main_layout.py (8 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `back_to_action_from_analysis` | < プロジェクトに戻る | project_callbacks.py:back_to_action | 解析画面からプロジェクトのアクションページへ戻る | OK |
| `close_backup_list_btn` | 閉じる | session_callbacks.py:toggle_backup_list_modal | バックアップ一覧モーダルを閉じる | OK |
| `cp_cancel_btn` | キャンセル | auth_callbacks.py:toggle_change_password_modal | パスワード変更モーダルを閉じる | OK |
| `cp_submit_btn` | 保存 | auth_callbacks.py:clientside:auth.submitChangePassword(clientside) | 新パスワードを送信し変更結果を表示(clientside fetch) | OK |
| `header_title_home_btn` | MSI Analysis Application | project_callbacks.py:header_title_to_landing | ヘッダータイトルクリックでランディングへ戻る | OK |
| `preset_delete_btn` | 🗑 削除 | preset_callbacks.py:delete_preset_cb | 選択した解析プリセットを削除 | OK |
| `preset_load_btn` | 📂 読込 | preset_callbacks.py:load_preset_cb | プリセットを読込み解析フォーム 19 項目へ反映 | OK |
| `preset_save_btn` | 💾 保存 | preset_callbacks.py:save_preset_cb | 現在の解析フォーム設定をプリセットとして保存 | OK |

## App/app/layouts/parquet_maintenance_modal.py (4 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `browse_parquet_maint_folder` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを parquet_maint_folder 欄へ反映 | OK |
| `parquet_maint_close_btn` | 閉じる | parquet_maintenance_callbacks.py:toggle_parquet_maintenance_modal | Parquet 再パックモーダルを閉じる | OK |
| `parquet_maint_run_btn` | 実行 | parquet_maintenance_callbacks.py:run_parquet_repack | Parquet 再パックジョブを実行しログ/進捗を表示 | OK |
| `parquet_maint_stop_btn` | 停止 | parquet_maintenance_callbacks.py:stop_parquet_repack | 実行中の Parquet 再パックを停止 | OK |

## App/app/layouts/rds_maintenance_modal.py (4 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `browse_rds_maint_folder` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを rds_maint_folder 欄へ反映 | OK |
| `rds_maint_close_btn` | 閉じる | rds_maintenance_callbacks.py:toggle_rds_maintenance_modal | RDS 軽量化モーダルを閉じる | OK |
| `rds_maint_run_btn` | 実行 | rds_maintenance_callbacks.py:run_rds_slim | RDS 軽量化ジョブを実行しログ/進捗を表示 | OK |
| `rds_maint_stop_btn` | 停止 | rds_maintenance_callbacks.py:stop_rds_slim | 実行中の RDS 軽量化を停止 | OK |

## App/app/layouts/scils_converter_modal.py (4 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `browse_scils_input_folder` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを scils_input_folder 欄へ反映 | OK |
| `browse_scils_output_folder` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを scils_output_folder 欄へ反映 | OK |
| `scils_cancel_btn` | キャンセル | scils_converter_callbacks.py:toggle_scils_converter_modal | SCiLS 変換モーダルを閉じる | OK |
| `scils_run_btn` | 変換実行 | scils_converter_callbacks.py:run_scils_conversion | SCiLS エクスポートの変換ジョブを実行しログ表示 | OK |

## App/app/layouts/settings_tab.py (25 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `browse_annotation` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを annotation_path 欄へ反映 | OK |
| `browse_folder` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを data_folder 欄へ反映 | OK |
| `browse_output` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを output_dir 欄へ反映 | OK |
| `browse_rds_folder` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを rds_folder 欄へ反映 | OK |
| `browse_rds_folder_reanalysis` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを rds_folder_reanalysis 欄へ反映 | OK |
| `browse_reanalysis_annotation` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを reanalysis_annotation_path 欄へ反映 | OK |
| `browse_reanalysis_folder` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを reanalysis_data_folder 欄へ反映 | OK |
| `browse_resume_reanalysis_dir` | 参照... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを resume_reanalysis_dir 欄へ反映 | OK |
| `btn_add_extra_folder` | ＋ フォルダ追加 | file_handlers.py:open_file_browser | ファイルブラウザを開き、選択フォルダを TIMS 追加データフォルダ一覧へ追加 | OK |
| `btn_make_reduction` | ① reduction のみ作成（診断用） | analysis_callbacks.py:open_overwrite_modal<br>analysis_callbacks.py:run_analysis | (必要なら上書き確認後) reduction のみ作成する R 解析ジョブを起動 | SUSPECT |
| `btn_preflight_apply` | ③ 推奨値を入力欄へ反映 | preflight_callbacks.py:apply_preflight_recommendation | PreFlight 推奨値を解析フォームの入力欄へ反映 | OK |
| `btn_preflight_load` | 📂 前回の診断を表示（再計算なし） | preflight_callbacks.py:load_saved_diagnostics_button | 保存済み PreFlight 診断を再計算なしで表示 | OK |
| `btn_preflight_run` | ② 🩺 PreFlight 診断を実行 | preflight_callbacks.py:run_preflight | PreFlight 診断(R ジョブ)を実行し結果と推奨値を表示 | OK |
| `btn_run_downstream` | ④ 続きを実行（reduction再利用） | analysis_callbacks.py:run_analysis | 既存 reduction を再利用して解析の続き(downstream)を実行 | SUSPECT |
| `cal_preset_delete_btn` | 削除 | analysis_callbacks.py:delete_cal_preset | 選択したキャリブレーションプリセットを削除 | OK |
| `cal_preset_save_btn` | 保存 | analysis_callbacks.py:save_cal_preset | 現在のキャリブレーションリストをプリセットとして保存 | OK |
| `calibration_add_row` | 行追加 | analysis_callbacks.py:add_calibration_row | キャリブレーション表に空行を追加 | OK |
| `calibration_auto_detect` | ピーク自動検出 | analysis_callbacks.py:auto_detect_observed_peaks | スペクトルからピークを自動検出し観測 m/z 列へ設定 | OK |
| `calibration_delete_rows` | 選択行削除 | analysis_callbacks.py:delete_calibration_rows | キャリブレーション表の選択行を削除 | OK |
| `calibration_reset_list` | リセット | analysis_callbacks.py:reset_calibration_list | キャリブレーション表を初期状態にリセット | OK |
| `calibration_save_list` | List保存 | analysis_callbacks.py:save_calibration_list | キャリブレーション表をセッションへ保存 | OK |
| `cancel_overwrite_results` | キャンセル | analysis_callbacks.py:close_overwrite_modal | 上書き確認モーダルを閉じ解析実行を中止 | OK |
| `confirm_overwrite_results` | 実行する | analysis_callbacks.py:close_overwrite_modal<br>analysis_callbacks.py:run_analysis | 既存結果の上書きを承諾し R 解析ジョブを開始 | SUSPECT |
| `run_analysis` | ▶ 解析実行 | analysis_callbacks.py:open_overwrite_modal<br>analysis_callbacks.py:run_analysis<br>analysis_callbacks.py:preflight_validation | 入力検証→(必要なら上書き確認)→R 解析ジョブを起動し進捗表示 | SUSPECT |
| `stop_analysis` | ⏹ 実行停止 | analysis_callbacks.py:handle_stop | 実行中の R 解析ジョブを停止(レシート記録) | OK |

## App/app/layouts/sidebar.py (24 件)

| ボタン id | ラベル | 発火するコールバック | 効果 | 判定 |
|---|---|---|---|---|
| `apply_desi_defaults` | 適用 | file_handlers.py:apply_desi_defaults | DESI 既定(データ/アノテ/出力)を解析フォームへ一括適用しトースト通知 | SUSPECT |
| `apply_output_defaults` | 適用 | file_handlers.py:apply_output_defaults | 既定出力先を解析フォームの出力欄へ適用しトースト通知 | SUSPECT |
| `apply_tims_defaults` | 適用 | file_handlers.py:apply_tims_defaults | TIMS 既定(データ/アノテ/出力)を解析フォームへ一括適用しトースト通知 | SUSPECT |
| `browse_default_annotation` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを default_annotation_csv 欄へ反映 | OK |
| `browse_default_annotation_desi` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを default_annotation_file 欄へ反映 | OK |
| `browse_default_desi_folder` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを default_desi_data_folder 欄へ反映 | OK |
| `browse_default_desi_output` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを default_desi_output_dir 欄へ反映 | OK |
| `browse_default_output` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを default_output_dir 欄へ反映 | OK |
| `browse_default_tims_folder` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを default_tims_data_folder 欄へ反映 | OK |
| `browse_default_tims_output` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択フォルダのパスを default_tims_output_dir 欄へ反映 | OK |
| `browse_desi_cluster_script` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを desi_cluster_filter_script_path 欄へ反映 | OK |
| `browse_desi_v8_script` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを desi_v8_script_path 欄へ反映 | OK |
| `browse_tims_cluster_script` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを tims_cluster_filter_script_path 欄へ反映 | OK |
| `browse_tims_v8_script` | ... | file_handlers.py:open_file_browser | ファイルブラウザモーダルを開き、選択ファイルのパスを tims_v8_script_path 欄へ反映 | OK |
| `open_backup_list_btn` | 🗂 バックアップ | session_callbacks.py:toggle_backup_list_modal | バックアップ一覧モーダルを開く | OK |
| `open_env_settings_modal` | ⚙ 環境設定 | env_settings_callbacks.py:toggle_env_settings_modal | 環境設定モーダルを開き現在の .env 値を読込表示 | OK |
| `open_parquet_maintenance_modal` | 📦 Parquet 再パック | parquet_maintenance_callbacks.py:toggle_parquet_maintenance_modal | Parquet 再パックモーダルを開く | OK |
| `open_preset_modal` | 📋 プリセット | preset_callbacks.py:toggle_preset_modal | 解析プリセット管理モーダルを開く | OK |
| `open_rds_maintenance_modal` | 🧹 RDS 軽量化 | rds_maintenance_callbacks.py:toggle_rds_maintenance_modal | RDS 軽量化モーダルを開く | OK |
| `open_scils_converter_modal` | 🔄 SCiLS 変換 | scils_converter_callbacks.py:toggle_scils_converter_modal | SCiLS 変換モーダルを開く | OK |
| `reset_desi_defaults` | リセット | file_handlers.py:reset_desi_defaults | DESI 既定欄を初期値(.env 由来)に戻す | OK |
| `reset_output_defaults` | リセット | file_handlers.py:reset_output_defaults | 既定出力欄を初期値に戻す | OK |
| `reset_script_paths` | デフォルトに戻す | file_handlers.py:reset_script_paths | R スクリプトパス 4 欄をデフォルトへ戻す | OK |
| `reset_tims_defaults` | リセット | file_handlers.py:reset_tims_defaults | TIMS 既定欄を初期値に戻す | OK |