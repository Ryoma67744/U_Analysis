# 付録B: 全コールバック監査表

U_Analysis デバッグ総点検 (2026-08) の付録。本文は `DEBUG_AUDIT_2026-08.md`。

監査対象: **401 コールバック**(11 モジュール群 + clientside)。各行は「発火条件(Input)→書き込み先(Output)→判定」。

凡例: 判定 OK = 宣言と実挙動が一致。SUSPECT/MISMATCH = 本文の該当項目を参照。

**判定内訳**: MISMATCH=5 / NG=1 / OK=346 / SUSPECT=49


## ? (20 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `` |  | {'id': 'current_analyst', 'prop': 'data', 'exists': 'App/app/layouts/main_layout.py:192'} | {'id': 'header_analyst_label_landing', 'prop': 'children', 'exists': 'App/app/layouts/landing_page.py:42'}, {'id': 'header_analyst_label_analysis', 'prop': 'children', 'exists': 'App/app/layouts/main_layout.py:292'}, {'id': 'header_analyst_label_shared', 'prop': 'children', 'exists': None, 'note': 'layout不在。ver52.3 (f3daa1b) で shared_view.py 削除時に span も削除、Output だけ残存'} | NG |  |
| `` |  | {'id': 'cp_submit_btn', 'prop': 'n_clicks', 'exists': 'App/app/layouts/main_layout.py:100'} | {'id': 'cp_status', 'prop': 'children', 'exists': 'App/app/layouts/main_layout.py:88'}, {'id': 'cp_master', 'prop': 'value', 'exists': 'App/app/layouts/main_layout.py:56'}, {'id': 'cp_new_master', 'prop': 'value', 'exists': 'App/app/layouts/main_layout.py:69'} 他1 | OK |  |
| `` |  | {'id': 'edit_lock_heartbeat', 'prop': 'n_intervals', 'exists': 'App/app/layouts/interactive_tab.py:1850'} | {'id': 'session_id_store', 'prop': 'data', 'exists': 'App/app/layouts/interactive_tab.py:1847'} | OK |  |
| `` |  | {'id': 'spatial_marker_size', 'prop': 'value', 'exists': 'App/app/layouts/interactive_tab.py:1156'} | {'id': 'spatial_restyle_dummy', 'prop': 'data', 'allow_duplicate': True, 'exists': 'App/app/layouts/interactive_tab.py:1896'} | OK |  |
| `` |  | {'id': 'spatial_label_size', 'prop': 'value', 'exists': 'App/app/layouts/interactive_tab.py:1167'} | {'id': 'spatial_restyle_dummy', 'prop': 'data', 'allow_duplicate': True, 'exists': 'App/app/layouts/interactive_tab.py:1896'} | OK |  |
| `` |  | {'id': 'hne_overlay_opacity', 'prop': 'value', 'exists': 'App/app/layouts/interactive_tab.py:1208'} | {'id': 'spatial_restyle_dummy', 'prop': 'data', 'allow_duplicate': True, 'exists': 'App/app/layouts/interactive_tab.py:1896'} | OK |  |
| `` |  | {'id': 'hne_overlay_marker_size', 'prop': 'value', 'exists': 'App/app/layouts/interactive_tab.py:1216'} | {'id': 'spatial_restyle_dummy', 'prop': 'data', 'allow_duplicate': True, 'exists': 'App/app/layouts/interactive_tab.py:1896'} | OK |  |
| `` |  | {'id': 'feature_marker_size', 'prop': 'value', 'exists': 'App/app/layouts/interactive_tab.py:1297'} | {'id': 'feature_restyle_dummy', 'prop': 'data', 'allow_duplicate': True, 'exists': 'App/app/layouts/interactive_tab.py:1899'} | OK |  |
| `` |  | {'id': 'feature_colorscale', 'prop': 'value', 'exists': 'App/app/layouts/interactive_tab.py:1380'} | {'id': 'feature_restyle_dummy', 'prop': 'data', 'allow_duplicate': True, 'exists': 'App/app/layouts/interactive_tab.py:1899'} | OK |  |
| `` |  | {'id': 'interactive_umap_plot', 'prop': 'relayoutData', 'exists': 'App/app/layouts/interactive_tab.py:967'}, {'id': '{type:umap_per_sample_graph,index:ALL}', 'prop': 'relayoutData', 'exists': 'App/app/callbacks/interactive_umap.py:386 (キー集合 {type,index} 一致)'}, {'id': '{type:spatial_graph,index:ALL}', 'prop': 'relayoutData', 'exists': 'App/app/callbacks/interactive_spatial.py:1248 (キー集合一致)'} | {'id': 'annotation_relayout_signal', 'prop': 'data', 'exists': 'App/app/layouts/interactive_tab.py:1879'} | OK |  |
| `` |  | {'id': 'fs_umap_integrated_graph', 'prop': 'relayoutData', 'exists': '動的生成: App/app/callbacks/interactive_fullscreen.py:507 (fullscreen modal body)'}, {'id': '{type:fs_spatial_graph,index:ALL}', 'prop': 'relayoutData', 'exists': 'App/app/callbacks/interactive_fullscreen.py:632 (キー集合 {type,index} 一致)'} | {'id': 'fs_annotation_relayout_signal', 'prop': 'data', 'exists': 'App/app/layouts/interactive_tab.py:1880'} | OK |  |
| `` |  | {'id': 'btn_methods_copy', 'prop': 'n_clicks', 'exists': 'App/app/layouts/interactive_tab.py:2049'} | {'id': 'methods_copy_status', 'prop': 'children', 'exists': 'App/app/layouts/interactive_tab.py:2047'} | OK |  |
| `` |  | {'id': 'lite_viewer_open_signal', 'prop': 'data', 'exists': 'App/app/layouts/interactive_tab.py:1902', 'note': '書き手はサーバ callback (lite_view_callbacks.py:1716 が epoch ms を返す)'} | {'id': 'lite_viewer_open_dummy', 'prop': 'data', 'exists': 'App/app/layouts/interactive_tab.py:1906'} | OK |  |
| `` |  | {'id': '{type:lv_card_collapse,cluster:ALL}', 'prop': 'is_open', 'exists': 'App/app/callbacks/lite_view_callbacks.py:1250 (キー集合 {type,cluster} 一致)'}, {'id': '{type:lv_show_labels_switch,scope:ALL}', 'prop': 'value', 'exists': 'App/app/callbacks/lite_view_callbacks.py:870 (キー集合 {type,scope} 一致)'}, {'id': '{type:lv_show_umap_labels_switch,scope:ALL}', 'prop': 'value', 'exists': 'App/app/callbacks/lite_view_callbacks.py:844 (キー集合一致)'} 他2 | {'id': 'lv_resize_trigger', 'prop': 'data', 'exists': 'App/app/layouts/lite_view.py:48'} | OK |  |
| `` |  | {'id': 'data_export_download_url', 'prop': 'data', 'exists': 'App/app/layouts/interactive_tab.py:1944', 'note': "書き手は data_export_poll (interactive_data_export.py:1147、完了時 URL / エラー時 '')"} | {'id': 'data_export_download_sink', 'prop': 'data', 'exists': 'App/app/layouts/interactive_tab.py:1945'} | OK |  |
| `` |  | {'id': '20x _PATH_INPUT_IDS', 'prop': 'value', 'exists': 'settings_tab.py:137,209,247,648,692,774,923,983 + sidebar.py (同上 11 本) + interactive_tab.py:346 = 計20本全て実在'} | {'id': '20x {pid}_path_hint', 'prop': 'children', 'exists': 'sidebar.py:43 (f-string、_path_input_row 経由 11 本: sidebar.py:172,177,186,191,219,224,229,261,271,276,309) + settings_tab.py:145,216,254,656,700,783,931,993 + interactive_tab.py:358 = 計20本全て実在'} | OK |  |
| `` |  | {'id': 'hne_mode', 'prop': 'value', 'exists': 'App/app/layouts/hne_overlay_tab.py:45'} | {'id': 'hne_tic_graph_wrap', 'prop': 'className', 'exists': 'App/app/layouts/hne_overlay_tab.py:181'}, {'id': 'hne_image_graph_wrap', 'prop': 'className', 'exists': 'App/app/layouts/hne_overlay_tab.py:191'} | OK |  |
| `` |  | {'id': 'hne_image_graph', 'prop': 'hoverData', 'exists': 'App/app/layouts/hne_overlay_tab.py:192'} | {'id': 'hne_coord_readout', 'prop': 'children', 'exists': 'App/app/layouts/hne_overlay_tab.py:194'} | OK |  |
| `` |  | {'id': 'hne_tic_graph', 'prop': 'hoverData', 'exists': 'App/app/layouts/hne_overlay_tab.py:182'} | {'id': 'hne_tic_coord_readout', 'prop': 'children', 'exists': 'App/app/layouts/hne_overlay_tab.py:184'} | OK |  |
| `` |  | {'id': 'hne_polygon_draft_store', 'prop': 'data', 'exists': 'App/app/layouts/hne_overlay_tab.py:211'} | {'id': 'hne_draft_dummy', 'prop': 'data', 'exists': 'App/app/layouts/hne_overlay_tab.py:213'} | OK |  |

## App/app/callbacks/add_molinfo_callbacks.py (4 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `open_add_molinfo` | 45 | {"index": "<ALL>", "type": "sub_action_add_molinfo"}.n_clicks | add_molinfo_modal.is_open, add_molinfo_target.data, add_molinfo_body.children 他2 | OK | 5 Output/両分岐5値。any(clicks) ガードで再描画発火を抑止。ボタンは project_callbacks.py:595 で動的生成(実在) |
| `preview_add_molinfo` | 68 | add_molinfo_upload.contents | add_molinfo_body.children, add_molinfo_confirm_btn.disabled | OK | upload contents→dry-run プレビュー。2 Output/全4分岐2値。一時CSVは finally で削除 |
| `confirm_add_molinfo` | 118 | add_molinfo_confirm_btn.n_clicks | add_molinfo_body.children, add_molinfo_confirm_btn.disabled, sub_project_list_refresh.data | OK | 3 Output/全4分岐3値。成功で sub_project_list_refresh+1(他3 writerと入力素、重複無し)。失敗時 confirm 再有効化 |
| `close_add_molinfo` | 158 | add_molinfo_close_btn.n_clicks | add_molinfo_modal.is_open | OK | 1 Output/両分岐1値 |

## App/app/callbacks/analysis_callbacks.py (32 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `toggle_sidebar_content` | 119 | main_tabs.active_tab | sidebar_col.style, main_content_col.width | OK | 2出力/2返却、両分岐一致。ID実在確認済(dash-layout.json) |
| `open_overwrite_modal` | 216 | run_analysis.n_clicks, btn_make_reduction.n_clicks | overwrite_results_modal.is_open(dup), overwrite_results_detail.children, overwrite_pending_mode.data | OK | 全分岐3返却。target構築式は run_analysis L407 と同一で判定不一致なし。html は L2271 のモジュール後方 import だが実行時には解決済み。btn_run_downstream は意図的に警告対象外(docstring) |
| `close_overwrite_modal` | 252 | cancel_overwrite_results.n_clicks, confirm_overwrite_results.n_clicks | overwrite_results_modal.is_open(dup) | OK | どちらのボタンでも False=閉じる。confirm は同時に run_analysis も発火し本実行される(正しい) |
| `run_analysis` | 263 | run_analysis.n_clicks, btn_make_reduction.n_clicks, btn_run_downstream.n_clicks 他1 | app_state.data(dup), progress_interval.disabled(dup), stop_button_container.style(dup) 他7 | SUSPECT | 62 State の位置引数マッピングは1:1で取り違えなし。全7分岐で返却10個一致。だが (1) L526/527/586: 'x if x else 既定値' が正当な入力0を既定値へ差替(再解析経路L753はis not Noneで正しい)。(2) L484/492 が try 外で output_dir空→CWD書込/None→無トースト例外。no |
| `update_progress` | 1108 | progress_interval.n_intervals | analysis_log.children, analysis_progress_bar.value, analysis_progress_bar.label 他8 | OK | 全4分岐で11返却一致。log_lines_count=0(全行)は ver51.1 修正済で正しく分岐。finalize は冪等(finalizer に一本化)。_process_state はモジュールグローバルだが施設全体同時1解析ガード(_start_lock+fail-closed scan)前提で整合 |
| `handle_stop` | 1261 | stop_analysis.n_clicks | notification_toast.children(dup), notification_toast.is_open(dup) | OK | 全5分岐2返却一致。所有者チェック(may_stop+tier A override)はサーバ側で実施、output_dir空ガードあり。PID経路(_stop_by_pid)も返却整合 |
| `restore_running_analysis` | 1360 | url_bar.pathname | app_state.data(dup), progress_interval.disabled(dup), stop_button_container.style(dup) 他6 | OK | 全6分岐9返却一致。F5復帰(ver51.1)・他人の解析の停止ボタン無効化(ver51.2)とも実装通り。例外時は no_update×9(ログのみ) |
| `reflect_analysis_busy` | 1474 | analysis_busy_poll.n_intervals, app_state.data | run_analysis.disabled(dup), btn_make_reduction.disabled(dup), btn_run_downstream.disabled(dup) 他2 | OK | 解析起動4ボタンの排他制御。全3分岐5返却一致。二重発火源(poll+app_state)は二度押し隙間対策で意図的。失敗時は有効のまま=サーバ側ガード(_start_lock)に委譲(fail-open だが起動側で fail-closed) |
| `refresh_output_subfolder` | 1524 | main_tabs.active_tab | output_subfolder.value | OK | 自動生成形(_AUTO_SUBFOLDER_RE)のみ差替、利用者命名は no_update で保護。ver56.3 対応 |
| `detect_rds_files` | 1593 | rds_folder_reanalysis.value, analysis_method.value, analysis_method_tims.value | rds_detection_badge.children, cluster_source.options, cluster_source.value(dup) 他1 | OK | 全6分岐4返却一致。instrument判定 'tims if tims_method else desi' は file_handlers の排他クリア(clear_tims_on_desi_select/clear_desi_on_tims_select)前提で run_analysis の 'desi or tims' と整合 |
| `toggle_calibration_panel` | 1680 | calibration_enable.value | calibration_detail_panel.style | OK | 単純表示切替 |
| `update_calibration_table_on_matrix` | 1693 | calibration_matrix.value, ion_mode.value | calibration_table_data.data | OK | matrix=custom/None は no_update で編集保護。ion_mode 変更でテーブル初期化(参照m/zが極性依存のため意図的だが obs_mz は消える点は仕様) |
| `sync_calibration_store_to_table` | 1712 | calibration_table_data.data | calibration_table.data, calibration_table.selected_rows | OK | Store→DataTable 片方向同期。use=='Yes' から selected_rows 復元 |
| `sync_selection_to_use` | 1724 | calibration_table.selected_rows | calibration_table_data.data(dup) | OK | 逆方向同期。changed ガードの no_update がループ(store→table→store)を正しく遮断 |
| `add_calibration_row` | 1747 | calibration_add_row.n_clicks | calibration_table_data.data(dup) | OK | 問題なし |
| `delete_calibration_rows` | 1762 | calibration_delete_rows.n_clicks | calibration_table_data.data(dup) | OK | 問題なし |
| `auto_detect_observed_peaks` | 1776 | calibration_auto_detect.n_clicks | calibration_table_data.data(dup), calibration_status_text.children | OK | 9引数=1 Input+8 State 一致(extra_folders=None 既定は無害)。全6分岐2返却一致。_bridge._parquet_column_names/get_feature_means 実在確認(seurat_bridge.py:501,581)。ver51.8/52.3 の修正(追加フォルダ探索・強度不明行の空欄化)は実装通り |
| `recalculate_ppm_on_edit` | 1971 | calibration_table.data_timestamp | calibration_table_data.data(dup) | OK | changed ガードでループ遮断 |
| `reset_calibration_list` | 2008 | calibration_reset_list.n_clicks | calibration_table_data.data(dup), calibration_status_text.children(dup) | OK | 保存キー calibration_table_data は auto_save/save_calibration_list と一致 |
| `auto_save_calibration_settings` | 2028 | calibration_enable.value, calibration_matrix.value, calibration_table_data.data 他3 | calibration_save_trigger.data | OK | 副作用専用(常に no_update 返却)。出力はダミーStore |
| `save_calibration_list` | 2052 | calibration_save_list.n_clicks | calibration_status_text.children(dup) | OK | 問題なし |
| `update_cal_sample_options` | 2086 | selected_samples_store.data | cal_sample_selector.options | OK | 問題なし |
| `switch_cal_sample` | 2100 | cal_sample_selector.value | calibration_table_data.data(dup), cal_per_sample_store.data(dup), cal_sample_selector_prev.data | OK | 3返却一致。State は生 DataTable から読む(手動編集込み)規約が run_analysis L646 の per_store 同期と整合 |
| `load_cal_preset` | 2154 | cal_preset_select.value | calibration_table_data.data(dup), calibration_regression_mode.value(dup), calibration_search_window.value(dup) 他4 | OK | 全3分岐7返却一致。calibration_matrix 非出力はテーブルリセット連鎖回避で意図的(docstring)。p.get('regression_mode', no_update) は保存値が明示 null の場合のみ None を書くが実運用では起きない |
| `save_cal_preset` | 2201 | cal_preset_save_btn.n_clicks | cal_preset_select.options, cal_preset_status.children(dup), cal_preset_name_input.value | OK | 全3分岐3返却一致 |
| `delete_cal_preset` | 2241 | cal_preset_delete_btn.n_clicks | cal_preset_select.options(dup), cal_preset_select.value(dup), cal_preset_status.children(dup) | OK | 全3分岐3返却一致 |
| `validate_data_folder_input` | 2287 | data_folder.value | data_folder_badge.children | OK | 表示専用バリデーション |
| `validate_rds_folder_input` | 2313 | rds_folder.value | rds_folder_badge.children | OK | 表示専用 |
| `validate_output_dir_input` | 2324 | output_dir.value | output_dir_badge.children | OK | 表示専用。非永続パス警告あり |
| `preflight_validation` | 2347 | run_analysis.n_clicks | validation_summary.children, validation_summary.style | SUSPECT | 表示のみで実行を止めない(run_analysis はエラーでも走る)。また btn_make_reduction/btn_run_downstream/confirm_overwrite_results 押下では発火せず①④確認実行はチェック無し。返却数は全分岐2で一致 |
| `load_calibration_from_first_analysis` | 2442 | rds_folder_reanalysis.value | reanalysis_calibration_data.data, reanalysis_calibration_details.children, reanalysis_calibration_info.style 他1 | OK | 全分岐4返却一致(_no_data 共通タプル)。ver51.9 の requested_degree/ref_mz_min/max 復元も実装通り。params 不在時にチェックを False へ戻すのは意図的 |
| `toggle_reanalysis_calibration_panel` | 2570 | reanalysis_calibration_use_previous.value | reanalysis_calibration_info.style(dup), reanalysis_calibration_details.children(dup) | OK | 全4分岐2返却一致 |

## App/app/callbacks/annotation_preview_callbacks.py (3 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `open_annotation_preview` | 86 | {"index": "<ALL>", "type": "sub_action_annotations"}.n_clicks | annotation_preview_modal.is_open, annotation_preview_target.data, annotation_preview_body.children | OK | 3 Output/両分岐3値。ボタンは project_callbacks.py:585 で動的生成。nonce=n_clicks で再発火担保(リスト再描画で n_clicks リセット後に同一 nonce 再現の理論edgeはあるが、ver46.3 の実測では同値でも発火するため実害なしと判断) |
| `populate_annotation_preview` | 113 | annotation_preview_target.data | annotation_preview_body.children | OK | target 変更→重い inspect を実行し本文描画。例外も UI に表示 |
| `close_annotation_preview` | 136 | annotation_preview_close_btn.n_clicks | annotation_preview_modal.is_open | OK | 1 Output/両分岐1値 |

## App/app/callbacks/auth_callbacks.py (4 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `populate_current_analyst` | 24 | url_bar.pathname | current_analyst.data | OK | url遷移→Flask session→Store。RuntimeError 時も2キーdictを返す |
| `(clientside)` | 41 | current_analyst.data | header_analyst_label_landing.children, header_analyst_label_analysis.children, header_analyst_label_shared.children | MISMATCH | C11-0(既知①-B): Output header_analyst_label_shared は layout 不在(ver52.3 の shared ページ削除の残骸。実行採取layoutにも無し)。renderer applyProps は per-output skip のため landing/analysis は更新される=実害は devtool |
| `toggle_change_password_modal` | 63 | open_change_password_btn.n_clicks, cp_cancel_btn.n_clicks | change_password_modal.is_open | OK | triggered_id str比較2分岐+no_update |
| `(clientside)` | 82 | cp_submit_btn.n_clicks | cp_status.children, cp_master.value, cp_new_master.value 他1 | OK | auth.js submitChangePassword 実在・全経路4値return=4 Output一致。fetch後の結果表示は直接DOM書換(cp_status)でDash管理外だが結線は正 |

## App/app/callbacks/data_management_callbacks.py (14 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `on_location_select` | 31 | {type:dm_loc_btn,key:ALL}.n_clicks | dm_state.data(dup) | OK | triggered_id.get('key')で押下ボタン=動作対応。生成時発火はany(clicks)ガード。実行確認済み |
| `render_layout_summary` | 50 | dm_state.data, dm_refresh_btn.n_clicks | dm_layout_summary.children | OK | 表描画のみ。get_layout_summary契約キー(exists/env_var/env_value/label/description/path)一致確認 |
| `render_directory` | 103 | dm_state.data, dm_refresh_btn.n_clicks | dm_directory_listing.children, dm_breadcrumb.children | OK | dm_item/dm_crumbパターン部品はここで生成(n_clicks=0初期化で再レンダ後のクリック検知正常)。2要素返却 |
| `on_item_click` | 181 | {type:dm_item,path:ALL}.n_clicks | dm_state.data(dup) | OK | ファイルクリックはno_update(ディレクトリのみ遷移)、triggered_id.pathで対象特定。実行確認済み |
| `on_crumb_click` | 200 | {type:dm_crumb,path:ALL}.n_clicks | dm_state.data(dup) | OK | パンくず遷移。実行確認済み |
| `on_scan` | 218 | dm_scan_btn.n_clicks | dm_scan_results.children, dm_scan_cache.data, dm_scan_summary.children | OK | 全3分岐3要素返却。dm_restore_btnとdm_scan_cacheを同時更新するためindex整合 |
| `on_restore` | 280 | {type:dm_restore_btn,index:ALL}.n_clicks | dm_toast.is_open, dm_toast.header, dm_toast.children 他2 | OK | 全4分岐5要素返却(実行確認)。idx範囲チェックあり。restore_projects_from_meta([meta],{sub_id:'restore'})はシグネチャ一致。例外はtoastで可視化 |
| `render_result_audit` | 318 | dm_state.data, dm_refresh_btn.n_clicks | dm_result_audit.children | OK | RESULT_DIR_STATESはmissing/volatile両キー定義済、audit_result_dirsの生成stateと一致 |
| `on_audit_pick` | 371 | {type:dm_audit_pick,path:ALL}.n_clicks | dm_move_src.value(dup) | OK | 移動元欄へ流し込み。空pathはno_update。実行確認済み |
| `on_move_request` | 387 | dm_move_btn.n_clicks | dm_move_confirm_modal.is_open, dm_move_confirm_body.children, dm_move_pending.data 他4 | OK | 全3分岐7要素返却(実行確認)。検証結果をdm_move_pendingに固定し実行時の入力書換を防ぐ設計。preview_move契約キー全一致 |
| `on_move_cancel` | 439 | dm_move_cancel_btn.n_clicks | dm_move_confirm_modal.is_open(dup) | OK | モーダル閉のみ。pending残置は次回on_move_requestが上書きするため無害 |
| `on_move_execute` | 474 | dm_move_exec_btn.n_clicks | dm_move_confirm_modal.is_open(dup), dm_move_src.value(dup), dm_state.data(dup) 他7 | OK | 全4分岐10要素返却(実行確認)。move_entry契約キー(ok/msg/old_path/new_path/path_updates)一致。skip_autoloadはinteractive_callbacks:614-619で消費される結線あり。開いている結果フォルダの読替(_remap_open_result_folder)実行確認 |
| `render_storage_stats` | 558 | dm_state.data, dm_refresh_btn.n_clicks | dm_storage_stats.children | OK | 表描画のみ。get_storage_stats契約キー一致 |
| `render_backup_list` | 604 | dm_refresh_btn.n_clicks | dm_backup_list.children | OK | バックアップ一覧描画のみ。日付parse失敗はpassで生文字列表示(妥当) |

## App/app/callbacks/edit_lock_callbacks.py (4 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `(clientside)` | 35 | edit_lock_heartbeat.n_intervals | session_id_store.data | OK | session.get_session_id(session_id_loader.js:7 実在)→session_id_store。heartbeat 駆動 |
| `refresh_edit_lock_state` | 46 | edit_lock_heartbeat.n_intervals | edit_lock_state.data | OK | 1 Output。ver46.3 の同値→no_update ガードは正当(値が変わる時のみ配信)。lock は rds_path(プロジェクト)スコープ+heartbeat で期限切れ削除 |
| `acquire_calibration_panel_lock` | 132 | int_cal_enable.value, int_cal_ion_mode.value, int_cal_matrix.value 他6 | calibration_panel_lock_indicator.children | OK | 9 Input+2 State=*args 11。args[-2]/[-1] の State 取り出し正。常に no_update 1値(Output は indicator 1つ)。int_cal_table_data は Store 実在(interactive_tab.py:1949) |
| `reflect_calibration_panel_lock` | 157 | edit_lock_state.data | int_cal_enable.disabled, int_cal_ion_mode.options, int_cal_matrix.disabled 他7 | OK | 10 Output/全3分岐10値。他者ロック時 disabled 一括反映。session_id None 時は安全側(disabled) |

## App/app/callbacks/env_settings_callbacks.py (2 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `toggle_env_settings_modal` | 36 | open_env_settings_modal.n_clicks, open_env_settings_modal_landing.n_clicks, env_settings_cancel_btn.n_clicks | env_settings_modal.is_open, env_settings_result.children, env_tims_data_dir.value 他5 | OK | 8 Output(2+6 env入力)/全3分岐8値。開時 read_env_values() は全キー返却保証(:57)。env_*_dir.value は file_browser(fb_select_btn) と多重writerだが入力素で衝突無し |
| `save_env_settings` | 63 | env_settings_save_btn.n_clicks | env_settings_result.children | OK | 1 Output/全分岐1値。書込失敗は Alert。再起動要否も表示 |

## App/app/callbacks/file_handlers.py (38 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `clear_tims_on_desi_select` | 34 | analysis_method.value | analysis_method_tims.value | OK | 排他クリア。無限ループ無し(Noneで相手側はno_update) |
| `clear_desi_on_tims_select` | 46 | analysis_method_tims.value | analysis_method.value | OK | 上記の対称形。OK |
| `toggle_settings_panels` | 62 | analysis_method.value, analysis_method_tims.value | umap_settings_panel.style, reanalysis_settings_panel.style, tims_ion_settings.style 他4 | OK | 7出力=7返却。切替時の一時的desi優先は排他クリア後に再発火し収束 |
| `set_default_normalize` | 98 | analysis_method.value, analysis_method_tims.value | normalize_input.value | OK | TIMS=OFF/DESI=ON。切替過渡の二重発火は最終値正 |
| `toggle_norm_mode_enabled` | 114 | normalize_input.value | norm_mode.disabled | OK | OK |
| `toggle_norm_mode_reanalysis_enabled` | 123 | normalize_input_reanalysis.value | norm_mode_reanalysis.disabled | OK | OK |
| `toggle_resume_panel` | 136 | resume_rds.value | resume_rds_panel.style | OK | OK |
| `toggle_resume_reanalysis_panel` | 150 | resume_reanalysis.value | resume_reanalysis_panel.style | OK | OK |
| `update_sample_selector` | 164 | data_folder.value, analysis_method.value, analysis_method_tims.value 他1 | sample_selector.children | OK | 動的Checklist id=selected_samples を生成。suppress_callback_exceptions=True 確認済 |
| `sync_selected_samples` | 198 | selected_samples.value | selected_samples_store.data | OK | 動的部品→静的Storeブリッジ。挿入時はprevent_initial_callが効かず同期される(出力が別レイアウト) |
| `update_annotation_selector` | 211 | selected_samples.value, data_folder.value, analysis_method.value 他1 | annotation_selector.children, annotation_filter_store.data | SUSPECT | extra_data_folders を参照せず find_tims_file_path(data_folder) のみ。追加フォルダのサンプルの annotation が選択肢に出ない (F-C02-1) |
| `sync_annotation_to_store` | 267 | {"type":"annotation_check","index":ALL}.value | annotation_filter_store.data | SUSPECT | 全チェック解除→None→下流(analysis_callbacks.py:638)で「フィルタ無し=全件」に反転 (F-C02-2) |
| `update_desi_roi_selector` | 287 | selected_samples.value, data_folder.value, analysis_method.value | desi_roi_selector.children, desi_roi_filter_store.data | OK | DESI専用のためtims入力なしは妥当。2出力=2返却 |
| `sync_desi_roi_to_store` | 341 | {"type":"desi_roi_check","index":ALL}.value | desi_roi_filter_store.data | SUSPECT | 全解除→None→analysis_callbacks.py:630 で roi_filter 省略=全ROI (F-C02-2 と同型) |
| `update_reanalysis_sample_selector` | 357 | reanalysis_data_folder.value, analysis_method.value, analysis_method_tims.value | sample_selector_reanalysis.children | OK | 動的Checklist id=selected_samples_reanalysis 生成 |
| `update_reanalysis_annotation_selector` | 387 | selected_samples_reanalysis.value, reanalysis_data_folder.value, analysis_method.value 他1 | annotation_selector_reanalysis.children, annotation_filter_store_reanalysis.data | OK | 再解析は単一フォルダのみなので extra_folders 非参照は妥当 |
| `sync_reanalysis_annotation_to_store` | 442 | {"type":"annotation_check_reanalysis","index":ALL}.value | annotation_filter_store_reanalysis.data | SUSPECT | 全解除→None反転 (F-C02-2 と同型、analysis_callbacks.py:769) |
| `reset_reanalysis_defaults` | 462 | analysis_method.value, analysis_method_tims.value | reanalysis_ion_mode.value, reanalysis_tolerance_mz.value | OK | モード切替毎にリセット(docstring通りの仕様) |
| `auto_switch_data_folder` | 479 | analysis_method.value, analysis_method_tims.value | data_folder.value | OK | 切替過渡で旧方式フォルダを一瞬書くが排他クリア後の再発火で最終値正 |
| `auto_switch_adduct` | 500 | ion_mode.value | adduct_filter.value | OK | OK |
| `auto_switch_reanalysis_adduct` | 511 | reanalysis_ion_mode.value | reanalysis_adduct_filter.value | OK | OK |
| `reset_script_paths` | 526 | reset_script_paths.n_clicks | desi_v8_script_path.value, desi_cluster_filter_script_path.value, tims_v8_script_path.value 他1 | OK | 4出力=4返却 |
| `reset_desi_defaults` | 543 | reset_desi_defaults.n_clicks | default_desi_data_folder.value, default_annotation_file.value, default_desi_output_dir.value | OK | OK |
| `reset_tims_defaults` | 554 | reset_tims_defaults.n_clicks | default_tims_data_folder.value, default_annotation_csv.value, default_tims_output_dir.value | OK | OK |
| `reset_output_defaults` | 565 | reset_output_defaults.n_clicks | default_output_dir.value | OK | OK |
| `apply_desi_defaults` | 578 | apply_desi_defaults.n_clicks | data_folder.value, annotation_path.value, output_dir.value | SUSPECT | `x or no_update` — 既定欄が空だと適用ボタンが無反応(空文字を反映できない) (F-C02-3) |
| `apply_tims_defaults` | 602 | apply_tims_defaults.n_clicks | data_folder.value, annotation_path.value, output_dir.value | SUSPECT | 同上 F-C02-3 |
| `apply_output_defaults` | 626 | apply_output_defaults.n_clicks | output_dir.value | SUSPECT | 同上 F-C02-3 |
| `open_file_browser` | 731 | browse_folder.n_clicks, browse_annotation.n_clicks, browse_rds_folder.n_clicks 他32 | file_browser_modal.is_open, fb_state.data, fb_drive_selector.options 他1 | OK | 35ボタン全て実レイアウトに存在(孤児ゼロ)。State並びとindex参照整合。全分岐4返却 |
| `update_file_browser` | 787 | fb_state.data, fb_drive_selector.value, fb_go_btn.n_clicks | fb_file_list.children, fb_breadcrumb.children, fb_path_input.value | OK | triggered分岐3系統網羅。全分岐3返却 |
| `handle_fb_item_click` | 859 | {"type":"fb_item","path":ALL}.n_clicks | fb_state.data, fb_selected_path.children | OK | n_clicks=0ガードで再描画時の誤発火防止。dict id の path 取り出し正 |
| `apply_file_browser_selection` | 888 | fb_select_btn.n_clicks | data_folder.value, annotation_path.value, rds_folder.value 他33 | OK | 36出力=36返却(早期returnも36)。caller_id一致の1個のみ更新。Store/valueプロパティ判定正 |
| `close_file_browser` | 912 | fb_cancel_btn.n_clicks | file_browser_modal.is_open | OK | OK |
| `handle_fb_shortcut` | 924 | {"type":"fb_shortcut","path":ALL}.n_clicks | fb_state.data | OK | n_clicksガード+is_dir検証あり |
| `(clientside) path_hint_badges` | 957 | data_folder.value, rds_folder.value, annotation_path.value 他17 | data_folder_path_hint.children, rds_folder_path_hint.children, annotation_path_path_hint.children 他17 | OK | 20入力→map→20出力。全 <pid>_path_hint がレイアウトに存在(_path_input_row factory 解決済) |
| `add_extra_folder` | 976 | extra_folder_pending_store.data | extra_data_folders_store.data | OK | is_dir+重複ガード。btn_add_extra_folder→ブラウザ→pending store経由の間接発火 |
| `render_extra_folders` | 992 | extra_data_folders_store.data | extra_data_folders_container.children | OK | 削除ボタン {btn_remove_extra_folder,index} を動的生成 |
| `remove_extra_folder` | 1020 | {"type":"btn_remove_extra_folder","index":ALL}.n_clicks | extra_data_folders_store.data | OK | dict triggered_id + index範囲ガード正。押したボタンのindexの要素を正しく削除 |

## App/app/callbacks/hne_overlay_callbacks.py (21 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `hne_populate_samples` | 130 | main_tabs.active_tab, seurat_rds_path_store.data | hne_sample_select.options, hne_sample_select.value, hne_data_status.children | OK | tab_id 'hne' 実在(main_layout.py:351)。tab!=hne は no_update×3。全分岐3値。deps[294]登録済 |
| `hne_store_image` | 157 | hne_image_upload.contents | hne_image_store.data, hne_upload_info.children | OK | 例外時も2値でメッセージ返却。永続保存は sample+rds があるときのみ。deps[295] |
| `hne_update_rotation` | 202 | hne_rotation_angle.value, hne_rotation_flip.value | hne_rotation_store.data, hne_landmarks_store.data(dup) | SUSPECT | C05-2: 復元後スライダ非同期状態でフリップ等に触れると stale スライダ角度で rot を再構成→復元 rotation 巻き戻し+対応点全消去(autosaveが即永続化)。flip値 'flip_h/flip_v' は layout と一致 |
| `hne_capture_landmark` | 227 | hne_tic_graph.clickData, hne_image_graph.clickData, hne_landmark_clear.n_clicks | hne_landmarks_store.data | OK | triggered_id 3分岐網羅・str比較正当。mode 'landmark' は layout 値と一致。polygon側との二重購読は trig+mode ガードで排他 |
| `hne_estimate_affine` | 257 | hne_landmarks_store.data | hne_affine_store.data, hne_landmark_info.children | OK | npair<3 と推定失敗は (None, info) で affine 未設定に戻す。全分岐2値 |
| `hne_polygon_draft` | 285 | hne_image_graph.clickData, hne_polygon_undo.n_clicks, hne_polygon_clear_draft.n_clicks | hne_polygon_draft_store.data | OK | trig 3分岐網羅。clickはpolygonモード限定。undo/clearはモード非依存(意図通り)。空draftのundoも安全 |
| `hne_polygon_commit` | 313 | hne_polygon_commit.n_clicks | hne_polygons_store.data(dup), hne_polygon_draft_store.data(dup), hne_polygon_table.data(dup) | OK | <3頂点は no_update×3。store と表を同一ラウンド更新で整合 |
| `hne_polygon_draft_info` | 335 | hne_polygon_draft_store.data | hne_polygon_draft_info.children | OK | 表示のみ |
| `hne_polygon_table_to_store` | 360 | hne_polygon_table.data | hne_polygons_store.data(dup), hne_polygon_table.data(dup) | OK | 過渡状態ガード(範囲外/重複idx/超過)は正当で全削除・改名・1行削除は通る。自己参照は renderer が分割し循環なし。自己エコーは rows==desired で停止 |
| `hne_tic_figure` | 396 | hne_sample_select.value, hne_landmarks_store.data, hne_affine_store.data 他3 | hne_tic_graph.figure | OK | _get_state(rds_path)で ContextVar キー再確立(プロジェクトスコープ適切) |
| `hne_image_figure` | 472 | hne_image_store.data, hne_landmarks_store.data, hne_polygons_store.data 他2 | hne_image_graph.figure | OK | name='下書き' trace を常設(clientside restyle の前提を満たす)。hne_opacity は layout 実在 |
| `hne_restore_sample` | 541 | hne_sample_select.value, seurat_rds_path_store.data | hne_image_store.data(dup), hne_landmarks_store.data(dup), hne_polygons_store.data(dup) 他3 | SUSPECT | C05-2 起点: hne_rotation_angle/flip の value を意図的に復元しない(538行コメント)ため UI と store が乖離。hne_update_rotation には無変化ガードがあるのでスライダも復元すれば安全なのに、現状は逆に破壊トラップを作る。全分岐6値は一致 |
| `hne_autosave` | 577 | hne_landmarks_store.data, hne_polygons_store.data, hne_rotation_store.data | hne_save_dummy.data | SUSPECT | C05-2 破壊実行部: hne_update_rotation が消した対応点を即ディスクへ上書き保存。復元直後の identity 保存は無害。sample/rds State のタイミングは安全(入力は store のみ) |
| `hne_assign_and_summarize` | 599 | hne_assign_btn.n_clicks | hne_result_area.children | OK | 前提未達は alert 返却で無反応なし。回転を表示・割当で同一適用(規約一貫) |
| `update_hne_export_method_options` | 662 | interactive_rds_map.data | hne_export_method.options, hne_export_method.value | OK | interactive_rds_map は layout 実在。全分岐2値 |
| `hne_export_stage_a` | 704 | hne_export_btn.n_clicks | hne_export_progress_container.style, hne_export_progress_label.children, hne_export_progress_bar.value 他3 | OK | trigger={'n':n} は常に truthy なので Stage B の早期 return で進捗が固まる経路なし |
| `hne_export_stage_b` | 721 | hne_export_trigger.data | hne_export_download.data, hne_export_info.children, hne_export_progress_container.style(dup) 他3 | OK | 全体 try + fail()/ok() が常に6値でボタン復帰保証。intensity/unit/qea の値域は layout options と一致(linear/counts/data, compound/mz, qea) |
| `clientside:crosshair_class` | 966 | hne_mode.value | hne_tic_graph_wrap.className, hne_image_graph_wrap.className | OK | 2値返却、両 wrap id 実在。deps[311] |
| `clientside:hne_coord_readout` | 980 | hne_image_graph.hoverData | hne_coord_readout.children | OK | deps[312] |
| `clientside:hne_tic_coord_readout` | 993 | hne_tic_graph.hoverData | hne_tic_coord_readout.children | OK | deps[313] |
| `clientside:draft_restyle` | 1007 | hne_polygon_draft_store.data | hne_draft_dummy.data | OK | '下書き' trace 名依存だが hne_image_figure が常設(507行)。見つからなければ no_update で安全。deps[314] |

## App/app/callbacks/interactive_batch_save.py (6 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `cb_batch_save_umap` | 266 | btn_batch_save_umap.n_clicks | dl_batch_zip.data(allow_duplicate) | SUSPECT | per_sample 表示中にサーバ保持 figs が LRU/TTL 追い出し済だと elif umap_fig へ落ちるが、per_sample 時の canonical figure は空 go.Figure() (truthy) のため空白 PNG の ZIP を出力 (C10-5) |
| `cb_batch_save_spatial` | 307 | btn_batch_save_spatial.n_clicks | dl_batch_zip.data(allow_duplicate) | OK | opacity/100 は slider min=0 max=100 (interactive_tab.py:1208) と整合。figs 不在時は silent PreventUpdate=無反応でトースト無し (C10-7, 軽微) |
| `cb_batch_save_feature` | 347 | btn_batch_save_feature.n_clicks | dl_batch_zip.data(allow_duplicate) | OK | kind 'feature' は producer (interactive_deg.py:495,936) と一致。figs 不在時 silent PreventUpdate (C10-7) |
| `cb_batch_save_deg` | 384 | btn_batch_save_deg.n_clicks | dl_batch_zip.data(allow_duplicate) | OK | 画面上の figure をそのまま保存=取り違えなし。ただし全クラスタ横断の Heatmap にも volcano の cluster suffix を付けたファイル名 (C10-6, 表記のみ) |
| `cb_set_thumbnail_spatial` | 512 | btn_set_thumbnail_spatial.n_clicks | notification_toast.is_open(dup), notification_toast.children(dup), notification_toast.icon(dup) 他1 | OK | figs 空は _save_figure_as_thumbnail が (False,メッセージ) を返しトースト通知される。4 出力 4 値 return 一致 |
| `cb_set_thumbnail_umap` | 546 | btn_set_thumbnail_umap.n_clicks | notification_toast.is_open(dup), notification_toast.children(dup), notification_toast.icon(dup) 他1 | SUSPECT | cb_batch_save_umap と同型: per_sample 中に figs 追い出し済だと空 go.Figure() から真っ白サムネを生成し既存サムネを上書き (C10-5)。4 値 return は全分岐一致 |

## App/app/callbacks/interactive_calibration.py (15 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `toggle_int_cal_panel` | 774 | int_cal_enable.value | int_cal_detail_panel.style | OK | チェックボックス→パネル表示切替。全ID実在・戻り値1個一致・登録済み |
| `update_int_cal_table_on_matrix` | 788 | int_cal_matrix.value, int_cal_ion_mode.value | int_cal_table_data.data(dup), int_cal_restore_pending.data(dup) | OK | 復元フェーズguard実装済(restore_pending)。Link Dはmatrix+ion_modeを同一応答で書くため1回発火でフラグ消費。全分岐2要素返却を実行確認 |
| `sync_int_cal_store_to_table` | 812 | int_cal_table_data.data | int_cal_table.data, int_cal_table.selected_rows | OK | Store→DataTable同期。use=Yes→selected_rows導出を実行確認 |
| `sync_int_cal_selection_to_use` | 825 | int_cal_table.selected_rows | int_cal_table_data.data(dup) | OK | CB3との往復ループはchanged判定のno_updateで収束(実行確認) |
| `add_int_cal_row` | 848 | int_cal_add_row.n_clicks | int_cal_table_data.data(dup) | OK | 行追加。n=0ガードのみ、正常 |
| `delete_int_cal_rows` | 863 | int_cal_delete_rows.n_clicks | int_cal_table_data.data(dup) | OK | 選択行削除。未選択時no_update(削除対象なし)は妥当 |
| `auto_detect_int_cal_peaks` | 877 | int_cal_auto_detect.n_clicks | int_cal_table_data.data(dup), int_cal_status_text.children | SUSPECT | S4: テーブル空だと(no_update,no_update)でボタン無反応・無説明(891-892行)。他分岐は2要素返却・status文言あり |
| `recalculate_int_cal_ppm` | 1009 | int_cal_table.data_timestamp | int_cal_table_data.data(dup) | OK | 編集時Δppm再計算。数値化不能時に'--'へ戻す修正(ver52.3)を実行確認。no_update収束 |
| `auto_save_int_cal` | 1054 | int_cal_enable.value, int_cal_ion_mode.value, int_cal_adduct_filter.value 他6 | int_cal_save_trigger.data | OK | _set_active_key(rds_path)でスコープ確立後保存、rds_path無しはPreventUpdate(正当)。ただしC06-1の連鎖でクロバー後の既定adductを永続化する側 |
| `save_int_cal_list` | 1093 | int_cal_save_list.n_clicks | int_cal_status_text.children(dup) | SUSPECT | S4: rds_path未設定時PreventUpdate(1114-1115行)でList保存ボタンが完全無反応(フィードバック無し)。保存処理自体はスコープキー確立済みで正常 |
| `auto_switch_int_cal_adduct` | 1134 | int_cal_ion_mode.value | int_cal_adduct_filter.value(dup) | SUSPECT | S2 C06-1: Link D復元がint_cal_ion_modeを書くと本CBが発火し、直前に復元された保存済みadduct選択を既定値で上書き→CB9が既定値を再保存。CB2にはrestore_pendingガードがあるが本CBには無い |
| `toggle_int_cal_annotation_section` | 1147 | int_cal_ms_instrument.data | int_cal_annotation_section.style | OK | DESIのみ表示。ID実在・登録済み |
| `apply_int_calibration` | 1290 | int_cal_apply.n_clicks | deg_data_store.data(dup), int_cal_apply_status.children | SUSPECT | 全10分岐2要素返却・FileLock取得/解放・active_key設定は正常。S3 C06-2: annotation_pathをMRM形式でのみ解釈(CSVは黙って無視、reann側はsuffix分岐あり)。S4 C06-3: 無効時にdeg_data_storeへディスク版を書き戻すのにメッセージは『無効です』のみ |
| `auto_switch_reann_adduct` | 1353 | reann_ion_mode.value | reann_adduct_filter.value | OK | reann_ion_modeへ書くcallbackは存在しない(手動変更のみ発火)ためC06-1型のクロバー無し |
| `execute_reannotation` | 1366 | reann_execute_btn.n_clicks | deg_data_store.data(dup), reann_status_text.children | OK | 全6分岐2要素返却。suffix分岐でMRM/CSV両対応。coerce_number既定値登録済(reann_tolerance=0.01)でfloat(None)不可能。active_key設定あり |

## App/app/callbacks/interactive_callbacks.py (10 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `scan_rds_files` | 543 | scan_result_folder.n_clicks | interactive_integration_method.options, interactive_integration_method.value, interactive_rds_map.data | OK | 3出力=全経路3値。無効フォルダで [] クリア(意図通り) |
| `auto_scan_rds_files` | 570 | interactive_result_folder.value | interactive_integration_method.options, interactive_integration_method.value, interactive_rds_map.data | OK | 3出力OK。無効/途中入力は no_update 維持(入力途中のクリア防止で意図的)。共有モード手法限定OK |
| `auto_load_on_rds_ready` | 612 | interactive_rds_map.data | load_interactive_data.n_clicks, dm_move_skip_autoload.data | OK | 2出力=全経路2値。skip_autoload は1回で消費、entry_mode ゲート正しい |
| `toggle_integration_method` | 637 | toggle_integration_method.n_clicks | integration_method_collapse.is_open, toggle_integration_method.children | OK | 2出力OK |
| `_toggle_cancel_button` | 710 | load_progress_container.style | btn_cancel_load.style | OK | 進捗表示に連動。A表示/D・エラー非表示の連鎖確認済み |
| `cancel_data_load` | 721 | btn_cancel_load.n_clicks | load_progress_label.children | SUSPECT | ロード完了後にキャンセル押下すると _LOAD_CANCELS にイベントが再作成され二度と削除されない(微小リーク,S4) |
| `load_stage_a_show_progress` | 739 | load_interactive_data.n_clicks | load_progress_container.style, load_progress_label.children, load_progress_bar.value 他5 | OK | 8出力=全3経路8値。派生PCA遅延生成の分岐OK |
| `load_stage_b_extract` | 789 | load_stage_trigger.data | load_progress_label.children, load_progress_bar.value, load_progress_container.style 他2 | OK | 5出力=全7経路5値。例外別メッセージ・キャンセル対応・finallyでイベント破棄。state は rds_path キーで分離 |
| `load_stage_c_deg` | 864 | load_stage_trigger_2.data | load_progress_label.children, load_progress_bar.value, load_progress_container.style 他2 | OK | 5出力=全4経路5値。キャリブ失敗は warning に fail-soft。state 消失検知あり |
| `load_stage_d_finish` | 992 | load_stage_trigger_3.data | interactive_data_info.children, interactive_viz_container.style, umap_highlight_cluster.options 他31 | OK | 34出力=正常34/エラー2経路とも 19+11+1+1+2=34 検算済み。info_notes 連結(ver52.3④)正しい |

## App/app/callbacks/interactive_cluster.py (10 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `update_cluster_stats` | 44 | seurat_rds_path_store.data, cluster_name_map_store.data | cluster_stats_table.data | OK | 行のClusterフィールドに表示名を格納(それ自体は表示用として正)。ただし下流update_cluster_infoがこれをraw IDとして誤用(次行参照) |
| `update_cluster_info` | 69 | cluster_stats_table.selected_rows, umap_highlight_cluster.value, cluster_stats_table.data | cluster_info_text.children | MISMATCH | table_data行のCluster=表示名をdf['Cluster'](raw ID)と比較。リネーム済みクラスタの行選択で恒偽→『<名前>: 0 pixels (0.0%)』誤表示(S2)。highlight経由はraw値で正常 |
| `update_cluster_dashboard` | 112 | seurat_rds_path_store.data, cluster_name_map_store.data | cluster_proportion_chart.figure | SUSPECT | 円グラフ色をCLUSTER_PRESET_COLORS連番で決めておりcustom_color_map_storeを参照しない→他図とクラスタ色が食い違う(S4)。return数/分岐はOK |
| `update_cluster_top_markers` | 147 | deg_data_store.data, cluster_name_map_store.data | cluster_top_markers_panel.children | OK | 全分岐1出力。deg列存在ガードあり |
| `populate_cluster_rename_panel` | 205 | seurat_rds_path_store.data, cluster_name_map_store.data | cluster_rename_panel.children | OK | cluster_rename_input/lock_indicatorを1:1生成。name_map InputによりリセットC適用後も再生成(コメントどおり) |
| `acquire_cluster_rename_lock` | 263 | {type:cluster_rename_input,index:ALL}.value | {type:cluster_rename_lock_indicator,index:ALL}.id | OK | 他callbackがrename_input.valueを書き戻さないため色ピッカーのような誤発火経路なし |
| `reflect_cluster_rename_lock` | 285 | edit_lock_state.data | {type:cluster_rename_input,index:MATCH}.disabled, {type:cluster_rename_lock_indicator,index:MATCH}.children | OK | 2出力全分岐一致 |
| `apply_cluster_rename` | 303 | cluster_rename_apply_btn.n_clicks, cluster_rename_reset_btn.n_clicks | cluster_name_map_store.data, cluster_rename_status.children | OK | trigger文字列比較で両ボタン網羅+フォールバックno_update。手法別保存(method)スコープ正。全分岐2出力 |
| `load_saved_cluster_name_map` | 338 | seurat_rds_path_store.data | cluster_name_map_store.data(dup) | OK | allow_duplicateでapply側と衝突なし。load_cluster_name_mapはrds None安全({}返却) |
| `update_cluster_dropdown_labels` | 354 | cluster_name_map_store.data, umap_merge_toggle.value | umap_highlight_cluster.options(dup), umap_exclude_cluster.options(dup), spatial_highlight_cluster.options(dup) 他2 | OK | ver51.9修正(_set_active_key)適用済。5出力全分岐(no_update,)*5含め一致。merged時はCluster_merged値で下流フィルタと整合 |

## App/app/callbacks/interactive_data_export.py (5 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `update_data_export_method_options` | 1067 | interactive_rds_map.data | data_export_method_selector.options, data_export_method_selector.value | OK | rds_map→手法チェックリスト更新。2 Output/全分岐2値。ID実在・登録確認済 |
| `data_export_start` | 1105 | btn_export_data.n_clicks | data_export_progress_container.style, data_export_progress_label.children, data_export_progress_bar.value 他4 | OK | 出力開始: 7 Output/return 7。n_clicks 無→PreventUpdate。contextvars.copy_context() でスレッドへ active key 引継ぎ(scope安全)。job_id は per-session Store 経由 |
| `data_export_poll` | 1146 | data_export_poll.n_intervals | data_export_download_url.data, div_data_export_status.children, data_export_progress_container.style 他5 | OK | 8 Output/全4分岐8値。job無=poll停止のみ(サーバ再起動時ボタンdisabled残り=要リロード,S4級)。done でDL URL配信+poll停止。error で pop |
| `(clientside)` | 1194 | data_export_download_url.data | data_export_download_sink.data | OK | DL URL→window.location 自動DL。sink Store 実在。1値return |
| `toggle_format_selector` | 1207 | int_cal_ms_instrument.data | data_export_format_wrapper.style | OK | DESI→format選択非表示。1 Output/1値 |

## App/app/callbacks/interactive_de.py (4 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `fill_onthefly_target_options` | 79 | seurat_rds_path_store.data | onthefly_de_target.options | OK | 選択肢/述語ともdf['Cluster'](元クラスタ)で一貫。マージ表示中も元クラスタ基準(整合はとれている) |
| `run_onthefly_de` | 99 | btn_run_onthefly_de.n_clicks | onthefly_de_store.data, onthefly_de_status.children | OK | mode値global/localはlayoutと一致。bridge署名一致(ident2はlocal時のみ)。全分岐2値、失敗は可視メッセージ。global時のno_update+エラー文は正当なガード |
| `render_onthefly_de` | 163 | onthefly_de_store.data, onthefly_de_fc.value, onthefly_de_p.value | onthefly_de_table.data, onthefly_de_table.columns, onthefly_de_volcano.figure | OK | _MARKER_TABLE_COLUMNSのid(gene/cluster/avg_log2FC/p_val_adj_raw/pct.1/pct.2/annotation)はstandardize_deg_dfレコードキーと一致 |
| `export_onthefly_de` | 183 | btn_export_onthefly_de.n_clicks | dl_onthefly_de_csv.data | OK | 表が空だとPreventUpdateで無反応(フィードバック無し、軽微)。provenance記録の署名一致。Top N 0=全件はif top_nで正しく素通し |

## App/app/callbacks/interactive_deg.py (14 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `filter_features` | 57 | feature_select.search_value, feature_filter_mode.value, feature_cluster_filter.value | feature_select.options(dup) | OK | 全ID実在/登録済。ctx分岐なし(全Input同一処理)。_set_active_key(rds_path)あり。deg空時はallモードへ静かにfallback(軽微) |
| `apply_mz_filter` | 200 | apply_feature_mz_filter.n_clicks | feature_mz_filtered_list.data | OK | min/max両None→None(解除)。inf(m/z無し)除外はver51.8修正済。単一出力・全分岐1値 |
| `update_feature_options_on_mz_filter` | 236 | feature_mz_filtered_list.data | feature_select.options(dup) | OK | deg表示モード中でも絞込結果で一時上書き(次のfilter_features発火で復元、設計上の即時更新)。dup登録3本はallow_duplicateで衝突なし |
| `update_feature_plot` | 419 | feature_select.value, feature_sample_select.value, feature_intensity_min.value 他5 | feature_plot_container.children, feature_plot_heading.children, feature_intensity_min.placeholder 他1 | OK | 全分岐4値(ver51.6の3値バグは修正済を確認)。data-onlyトリガ集合はCB2のInput集合と一致。ver52.5行順照合あり。エラー分岐でheading据置(軽微) |
| `patch_feature_intensity` | 789 | feature_select.value, feature_intensity_min.value, feature_intensity_max.value 他1 | {feature_graph,ALL}.figure, {feature_graph,ALL}.config | OK | n=0時[],[]/それ以外は常にn個返却。colorbarは末尾タイルのみ(殻側is_lastと一致)。export figures同期は位置対応(ver51.9 B-4修正済)。行順照合あり |
| `clientside feature_restyle.marker_size` | 989 | feature_marker_size.value | feature_restyle_dummy.data(dup) | OK | assets/feature_restyle.js に namespace/関数実在。deps JSONに登録確認。ループ登録だがclientside_callbackは即時実行で束縛問題なし |
| `clientside feature_restyle.colorscale` | 989 | feature_colorscale.value | feature_restyle_dummy.data(dup) | OK | 同上。2登録のInputがmarker_size/colorscaleに正しく分かれていることをdeps JSONで確認 |
| `add_feature_bookmark` | 1001 | add_feature_bookmark_btn.n_clicks | feature_history_store.data | OK | _set_active_key後に_save_interactive_settings(ver51.8修正済、プロジェクト別保存)。guardはno_update(正当) |
| `remove_feature_bookmark` | 1027 | remove_feature_bookmark_btn.n_clicks | feature_history_store.data(dup), feature_history_select.value | OK | 全分岐2値。deps JSONで@hash付き登録確認 |
| `update_bookmark_options` | 1051 | feature_history_store.data | feature_history_select.options | SUSPECT | _label_from_active_stateを_set_active_key無しで呼ぶ(rds_path Stateも無い)。ver51.8のper-request resetにより常に__default__空stateを読み、SCiLS/CSV由来annotation_mapが付与されない(コメントの主張と不一致) |
| `bookmark_to_feature` | 1076 | feature_history_select.value | feature_select.value(dup) | OK | feature_select.valueの他writerなし(deps JSONで唯一の@hash登録) |
| `update_volcano_cluster_options` | 1092 | deg_data_store.data, cluster_name_map_store.data | volcano_cluster_select.options, volcano_highlight_name.options, heatmap_cluster_select.options | OK | 全分岐3値。deg空→[],[],[] |
| `update_volcano_plot` | 1122 | volcano_cluster_select.value, volcano_fc_threshold.value, volcano_p_threshold.value 他6 | volcano_plot.figure | OK | 9Input+3State=12引数一致。FC/p閾値0はcoerce_number(PARAM_BOUNDS実在キー)でver52.3修正済。highlight_name optionsはannotation値と一致 |
| `update_heatmap` | 1326 | heatmap_top_n.value, heatmap_scale.value, heatmap_annotation_switch.value 他2 | heatmap_plot.figure | OK | merged判定値はlayoutのoriginal/mergedと一致。発現はCellID mergeで突合(位置代入でない=安全)。annotation_pathはsettings_tab側に実在 |

## App/app/callbacks/interactive_facet_legend.py (4 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `_legend_to_hidden[umap_shared_legend→umap_legend_hidden_store]` | 48 | umap_shared_legend.restyleData | umap_legend_hidden_store.data(dup) | OK | factory は引数渡しクロージャで捕捉正しい(ループ遅延束縛なし)。legend graph は _facet_block(display_helpers.py:94)が動的生成、trace meta=str(cluster) と突合一致。deps[198] |
| `_legend_to_hidden[fs_umap_shared_legend→umap_legend_hidden_store]` | 49 | fs_umap_shared_legend.restyleData | umap_legend_hidden_store.data(dup) | OK | FS とメインで store 共有は設計意図(コメント明記)。deps[199] |
| `_legend_to_hidden[spatial_shared_legend→spatial_legend_hidden_store]` | 50 | spatial_shared_legend.restyleData | spatial_legend_hidden_store.data(dup) | OK | deps[200]。legend graph 生成元 interactive_spatial:1256 |
| `_legend_to_hidden[fs_spatial_shared_legend→spatial_legend_hidden_store]` | 51 | fs_spatial_shared_legend.restyleData | spatial_legend_hidden_store.data(dup) | OK | deps[201]。生成元 interactive_fullscreen:639 |

## App/app/callbacks/interactive_feature_lists.py (5 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `mutate_feature_lists` | 34 | seurat_rds_path_store.data, btn_list_from_mzfilter.n_clicks, btn_list_from_bookmarks.n_clicks 他4 | feature_lists_store.data, feature_lists_status.children | OK | ctx分岐は7 Input全網羅+else PreventUpdate。引数順=宣言順を照合済。全分岐2値。保存はrds_path別sidecar |
| `filter_feature_list_picker` | 126 | feature_list_picker.search_value | feature_list_picker.options | OK | _set_active_keyあり。選択済み値をoptionsに常時保持(multi消失防止) |
| `render_feature_lists` | 161 | feature_lists_store.data | feature_lists_table.data, feature_list_select.options, coexpr_list_a.options 他1 | OK | 全分岐4値 |
| `run_coexpression` | 188 | btn_run_coexpr.n_clicks | coexpr_scatter.figure, coexpr_status.children | SUSPECT | parquet行→plot_data行を位置対応で結合するのに行順照合(expression_row_order_matches)が無くlen一致のみ。ver52.5でfeature plotに入れた同型の番人が未適用。agg値sum/meanはlayoutと一致、全分岐2値 |
| `export_feature_lists` | 281 | btn_export_feature_lists.n_clicks | dl_feature_lists_csv.data | OK | 空リスト時PreventUpdate(無反応、軽微) |

## App/app/callbacks/interactive_fullscreen.py (8 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `toggle_fullscreen` | 53 | expand_umap_btn.n_clicks, expand_feature_btn.n_clicks, expand_spatial_btn.n_clicks 他1 | fullscreen_plot_modal.is_open, fullscreen_modal_title.children, fullscreen_modal_body.children | SUSPECT | C05-3: 対象空(deg_data無し等)でも最終 return False で is_open を書き込み close ハンドラを誘発。4ボタン分岐は網羅・全分岐3値。初期FSグラフに id 無しだが show_labels=False で注釈なしのため実害なし。spot_opacity に hne_overlay_opacity 適用はメイン(int |
| `on_fullscreen_close` | 400 | fullscreen_plot_modal.is_open | fullscreen_closed_trigger.data, fs_label_positions_snapshot.data | SUSPECT | C05-3: snapshot=None 経路(初回/前回ラベル変更後)で「開かなかった is_open=False 書込」でもトリガ加算→重い5コールバック(deps[81][83][84][99][134])が一斉再走。fingerprint 比較自体は安全側で正当・2値一致 |
| `update_fs_umap` | 446 | fs_umap_display_mode.value, fs_umap_color_by.value, fs_umap_highlight_cluster.value 他8 | fs_umap_graph_container.children | OK | fs_umap_* は toggle_fullscreen が動的生成(123-189行)・出力と同時挿入なので初回発火抑止も正しい。引数順一致。_set_active_key でスコープ確立 |
| `update_fs_spatial` | 535 | fs_spatial_sample.value, spatial_rotation_store.data, fs_spatial_show_labels.value 他7 | fs_spatial_graph_container.children | OK | fs_spatial_* は toggle_fullscreen が動的生成(302-368行)。fs_spatial_marker_auto_btn の消費側は interactive_spatial:1039(deps[97])で結線あり。引数順一致 |
| `clientside:relayout.filter_annotations(normal)` | 779 | interactive_umap_plot.relayoutData, {umap_per_sample_graph,ALL}.relayoutData, {spatial_graph,ALL}.relayoutData | annotation_relayout_signal.data | MISMATCH | C05-1: relayout_filter.js が宣言順で最初に annotations[ を含む値を採用。relayoutData は prop 残存するため、後順グラフのドラッグ時に先順グラフ(特に常設 interactive_umap_plot)の旧データが選ばれ triggered_id と食い違う。deps[171] |
| `clientside:relayout.filter_annotations(fs)` | 788 | fs_umap_integrated_graph.relayoutData, {fs_spatial_graph,ALL}.relayoutData | fs_annotation_relayout_signal.data | MISMATCH | C05-1 同型: FS UMAP 統合の旧 relayoutData が FS Spatial タイルのドラッグを乗っ取る。deps[172] |
| `accumulate_annotation_positions_normal` | 798 | annotation_relayout_signal.data | accumulated_label_positions.data(dup) | SUSPECT | C05-1 被害側: 取り違えた rd を triggered_id のセクション/クラスタ列で解釈し誤保存。C05-5: Sample 比較が astype(str) なしで非文字列 Sample だと常に PreventUpdate(要検証)。triggered_id の str/dict 分岐・除外リスト切替(spatial_graph→spatial |
| `accumulate_annotation_positions_fs` | 840 | fs_annotation_relayout_signal.data | accumulated_label_positions.data(dup) | SUSPECT | C05-1 被害側(FS)。dict=fs_spatial/str=fs_umap の除外切替は正しい。FS 側 State はモーダル未生成時 None で安全 |

## App/app/callbacks/interactive_hne_bg.py (1 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `hne_overlay_status_msg` | 252 | hne_overlay_show.value | hne_overlay_status.children | OK | モジュールキャッシュ _HNE_IMG_CACHE はキー (rds_path,img_file,mono) で project スコープ適切・LRU上限+lock あり。deps[86]。build_hne_overlay_fig は純関数(interactive_spatial から呼出) |

## App/app/callbacks/interactive_loupe.py (8 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `umap_polygon_draft` | 48 | interactive_umap_plot.clickData, umap_polygon_undo.n_clicks, umap_polygon_clear.n_clicks | umap_polygon_draft_store.data | OK | triggered_id 3分岐+fallback no_update で網羅。click 分岐のみ per_sample ガード、undo/clear は非ガード(C10-1 の入口)。全分岐 return 1 値=Output 数一致 |
| `umap_polygon_overlay` | 74 | umap_polygon_draft_store.data | interactive_umap_plot.figure(allow_duplicate) | SUSPECT | Patch data[-1] は末尾 _umap_poly_draft trace 前提だが、update_umap_plot が per_sample/データ未ロード時に素の go.Figure() (trace 0本) を返すため前提崩壊。また統合図の再構築毎に下書き trace が空へ戻り store と表示が乖離 (C10-1) |
| `umap_polygon_draft_info` | 101 | umap_polygon_draft_store.data | umap_polygon_draft_info.children | OK | 純表示。全分岐 return 1 値。依存 JSON に登録確認済 |
| `umap_polygon_commit` | 115 | umap_polygon_commit.n_clicks | selected_cell_ids_store.data, umap_polygon_draft_store.data(allow_duplicate) | OK | 2 出力 return (ids,[]) 一致。PreventUpdate ガードは正当(頂点<3/データ無)。merged 座標系も State で追随。ただし C10-1 により非表示の旧下書きでも確定可能な点に注意 |
| `render_selection_summary` | 189 | selected_cell_ids_store.data | selection_summary_card.children | OK | plot_data 未ロード時 no_update で旧カード残置(軽微)。expr 取得失敗は None フォールバックで集計継続 |
| `update_feature_violin` | 226 | feature_select.value, feature_violin_group_by.value, interactive_accordion.active_item | feature_violin_plot.figure | OK | item_id 'acc_feature' は interactive_tab.py:1235 に実在(ver51.9 型の恒偽なし)。active_item の list/str 両対応。全分岐 1 値 return |
| `populate_marker_table` | 343 | deg_data_store.data, deg_markers_cluster_filter.value | deg_markers_table.data, deg_markers_table.columns, deg_markers_cluster_filter.options | SUSPECT | options は毎回再構築するが value は誰もリセットしない(依存 JSON 全走査で .value の writer 無し)。データ切替後に旧クラスタ filter が残ると全行除外され表が空に見える (C10-2) |
| `export_marker_table` | 364 | btn_export_marker_table.n_clicks | dl_marker_table_csv.data | OK | derived_virtual_data で画面と一致した CSV。top_n None/0 ガード正常。record_csv_export(filename,rds,folder,method,extra) 署名一致 |

## App/app/callbacks/interactive_pptx.py (3 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `sync_export_top_n` | 1686 | input_export_top_n.value | export_top_n_store.data | OK | dbc.Input→Store ブリッジ。coerce_count で既定値一元化 |
| `update_export_method_options` | 1699 | interactive_rds_map.data | export_method_selector.options, export_method_selector.value | OK | rds_map→手法Checklist。2 Output/両分岐2値 |
| `cb_export_report` | 1719 | btn_export_report.n_clicks | dl_report_pptx.data, div_export_status.children, (running/progress)btn_export_report.disabled 他4 | OK | background=True(DiskcacheManager 設定済 main.py:57,71/deps long={interval:1000})。2 Output/全10箇所のreturnが2値。running/progress の5 ID実在。fork子プロセス制約は ver51.9 対策済(_get_merged_label_positions |

## App/app/callbacks/interactive_project.py (12 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `auto_fill_interactive_from_analysis` | 29 | main_tabs.active_tab | interactive_result_folder.value(dup), interactive_msi_folder.value(dup), interactive_entry_mode.data(dup) 他2 | SUSPECT | F3: standalone解析(project_id='')でもentry_mode='sub_project'を書き、プロジェクトDD行が非表示化される。return個数は全分岐5でOK |
| `toggle_project_dropdown_visibility` | 69 | interactive_entry_mode.data | interactive_project_row.style | OK | sub_project/sharedで非表示、他は表示。1出力1return |
| `populate_interactive_projects` | 81 | main_tabs.active_tab, current_page.data | interactive_project_select.options | OK | 両Input変化を1ガードで網羅(interactive+analysis時のみ)。同応答内でのState更新順も整合 |
| `populate_interactive_sub_projects` | 96 | interactive_project_select.value | interactive_sub_project_select.options(dup), interactive_sub_project_select.value(dup) | OK | 全分岐2個。sub_project/shared時はvalue保持(no_update)で取り違え無し |
| `reset_interactive_on_project_change` | 117 | interactive_project_select.value | interactive_viz_container.style(dup), interactive_data_info.children(dup), sap_skip_reset.data(dup) 他1 | SUSPECT | F2: _drop_state()/_set_active_key(None)はbefore_requestのreset_active_keyにより恒等no-op(宣言した破棄が実行されない)。UI出力4個は全分岐OK |
| `set_interactive_folders_from_sub_project` | 142 | interactive_sub_project_select.value | interactive_result_folder.value(dup), interactive_msi_folder.value(dup), interactive_data_info.children(dup) 他4 | SUSPECT | F2同件: L169-170の_drop_state()がno-op。ver51.9のskip分岐並び(L166)は7/7で修正済を確認。全分岐7個OK |
| `toggle_save_as_project_modal` | 204 | open_save_as_project_modal.n_clicks, close_save_as_project_modal.n_clicks | save_as_project_modal.is_open | OK | ctx.triggered_idは両宣言Inputを網羅(str比較)。トグル方式 |
| `switch_sap_action_type` | 219 | sap_action_type.value | sap_new_project_section.style, sap_existing_project_section.style, sap_new_sub_section.style 他1 | OK | new_all/add_sub/link_existing+fallbackの全分岐4個 |
| `populate_sap_projects` | 239 | save_as_project_modal.is_open | sap_project_select.options | OK | 開時のみ更新 |
| `populate_sap_sub_projects` | 253 | sap_project_select.value | sap_sub_select.options | OK | 未選択で[] |
| `display_sap_paths` | 267 | save_as_project_modal.is_open | sap_result_folder_display.children, sap_msi_folder_display.children | OK | 全分岐2個 |
| `execute_save_as_project` | 285 | execute_save_as_project.n_clicks | sap_status.children, save_as_project_modal.is_open(dup), interactive_project_select.options(dup) 他4 | OK | 全9分岐7個。sap_skip_reset=Trueと選択値の同応答書込みで下流2本のリセットskip整合(両者がFalseへ戻す)。例外はAlert表示 |

## App/app/callbacks/interactive_reanalysis_bridge.py (2 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `fill_bridge_cluster_options` | 23 | seurat_rds_path_store.data | reanalysis_bridge_clusters.options | OK | 1出力OK。_set_active_key 済み |
| `send_to_reanalysis` | 40 | btn_send_to_reanalysis.n_clicks | target_clusters.value, filter_mode.value, rds_folder_reanalysis.value 他4 | OK | 7出力=全経路7値。desi/tims_cluster_filter は sidebar.py:78/102 の実在 option 値。相互排他CB(file_handlers:35/47)は falsy ガード済で消し合い無し。tab 'settings' 実在 |

## App/app/callbacks/interactive_resets.py (3 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `reset_feature_colorscale` | 15 | feature_colorscale_reset.n_clicks | feature_colorscale.value(dup), feature_intensity_min.value(dup), feature_intensity_max.value(dup) | OK | 既定値 Plasma/None/None は layout(interactive_tab.py:1380-1386,1366-1372) と一致。対象取り違えなし。deps[228] |
| `reset_volcano` | 28 | volcano_reset.n_clicks | volcano_fc_threshold.value(dup), volcano_p_threshold.value(dup), volcano_y_max.value(dup) | OK | 0.5/1.3/None は layout(1577-1589) の初期値と一致。deps[229] |
| `reset_hne_overlay` | 41 | hne_overlay_reset.n_clicks | hne_overlay_opacity.value(dup), hne_overlay_marker_size.value(dup) | OK | 100/5 は layout(1208-1217) の初期値と一致。ボタン title『透明度・サイズを既定に戻す』と出力対象一致。deps[230] |

## App/app/callbacks/interactive_selection_groups.py (4 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `mutate_selection_groups` | 28 | seurat_rds_path_store.data, btn_save_selection_group.n_clicks, btn_rename_group.n_clicks 他4 | selection_groups_store.data, selection_groups_status.children, selection_groups_undo.data | SUSPECT | ctx分岐は7 Input全網羅・引数順一致・全分岐3値だが、プロジェクト切替(rds分岐)でundoをno_updateのまま残すため、切替後の「削除を取り消す」が旧プロジェクトのcell_idsを新プロジェクトへ保存しうる |
| `render_selection_groups` | 131 | selection_groups_store.data | selection_groups_table.data, selection_group_select.options, selection_groups_combine.options | OK | 削除後もselection_group_select.valueは残る(次操作は未検出idで警告表示、軽微) |
| `load_group_to_selection` | 151 | btn_load_group_to_selection.n_clicks | selected_cell_ids_store.data(dup), selection_groups_load_status.children | OK | selected_cell_ids_storeのwriterはumap_polygon_commitと本CBのみ(allow_duplicate、deps JSONで確認)。gid未検出はPreventUpdate |
| `export_selection_groups` | 174 | btn_export_groups.n_clicks | dl_selection_groups_csv.data | OK | グループ0件時PreventUpdate(無反応、軽微) |

## App/app/callbacks/interactive_spatial.py (26 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `create_spatial_controls` | 477 | seurat_rds_path_store.data | spatial_controls_container.children | OK | 動的生成元。生成するPM id群(sample_block/sample_rename_input/per_sample_*/cluster_block/cluster_color_swatch/cluster_color_picker/各lock_indicator)は下流コールバックと1:1整合。dash-dependencies.json登録確認 |
| `toggle_sample_rotation_visibility` | 685 | spatial_sample_selector.value | {type:sample_block,index:ALL}.style | OK | ctx.outputs_list基準で構築するためreturn個数は常に一致。selector値とindexの比較は同一文字列(サンプル名) |
| `toggle_cluster_color_visibility` | 700 | spatial_cluster_selector.value | {type:cluster_block,index:ALL}.style | OK | 同上。selector value=str(cluster)とindex=cl_strで型一致 |
| `update_swatch_disabled_state` | 719 | custom_color_map_store.data | {type:cluster_color_swatch,index:ALL,color:ALL}.style | OK | ver51.9修正(_set_active_key呼出)適用済を確認。PreventUpdateはrds無し/df未ロード時のみで正当 |
| `update_custom_color_map` | 784 | {type:cluster_color_picker,index:ALL}.value, {type:cluster_color_swatch,index:ALL,color:ALL}.n_clicks | custom_color_map_store.data, {type:cluster_color_picker,index:ALL}.value | SUSPECT | 分岐/return数はOK。ただしpicker初期値=実効色(create_spatial_controls:593)のためpicker分岐で全クラスタが恒久的にcustom保存される(色凍結S4)。全picker書き戻しがacquire_cluster_color_lock誤発火を誘発(別行) |
| `update_rotation_store_from_per_sample` | 840 | {type:per_sample_rotation,index:ALL}.value, {type:per_sample_flip_h,index:ALL}.value, {type:per_sample_flip_v,index:ALL}.value | spatial_rotation_store.data | OK | triggered dictガード+index一致検索で正サンプル特定。保存は_set_active_key後のプロジェクト別settings |
| `update_sample_name_map` | 892 | {type:sample_rename_input,index:ALL}.value, {type:umap_sample_rename_input,index:ALL}.value | sample_name_map_store.data | OK | trigger側優先マージ。triggered=None(挿入発火)時はSpatial優先のelse分岐で安全 |
| `update_sample_dropdown_labels` | 936 | sample_name_map_store.data | interactive_sample.options(dup), feature_sample_select.options(dup) | OK | 2出力全分岐一致。allow_duplicateでload_stage側と衝突なし(依存グラフ確認) |
| `create_umap_name_controls` | 961 | seurat_rds_path_store.data | umap_name_controls_container.children | OK | サンプル1以下は空文字返却→umap_sample_rename系PMコールバックは発火せず整合 |
| `auto_spatial_marker` | 1025 | spatial_marker_auto_btn.n_clicks | spatial_marker_size.value | OK | 0(auto)を返す→clientside marker_sizeがlayout.meta.auto_mszで復元。配線一致 |
| `auto_fs_spatial_marker` | 1037 | fs_spatial_marker_auto_btn.n_clicks | fs_spatial_marker_size.value | OK | fs_*はinteractive_fullscreen.py:302,323,329,347で動的生成(実在確認)。suppress_callback_exceptions=True |
| `auto_feature_marker` | 1054 | feature_marker_auto_btn.n_clicks | feature_marker_size.value | OK | 配線一致 |
| `update_spatial_plots` | 1070 | interactive_sample.value, spatial_highlight_cluster.value, selected_cell_ids_store.data 他15 | spatial_plots_container.children, last_spatial_figure_store.data | SUSPECT | 18入力/6状態/2出力の対応・全分岐return数OK。acc_spatialは実在(interactive_tab.py:1108)、noopのsession_id引数も正。ただし閉状態の早期return(:1114)がaccordion_toggle_is_noopより先のためFalse記録が残らず、閉中の変更が再オープンで反映されない(S2)。set |
| `save_spatial_display_settings` | 1274 | spatial_marker_size.value, spatial_label_size.value, spatial_show_labels.value 他2 | spatial_display_save_trigger.data | OK | Outputは副作用専用ダミーStore(layout:1915)で常にno_update。rds無しPreventUpdateは正当(保存先が無い) |
| `acquire_sample_rename_lock` | 1309 | {type:sample_rename_input,index:ALL}.value | {type:sample_rename_lock_indicator,index:ALL}.id | OK | 全分岐[no_update]*len(ids)。indicatorとinputは1:1生成でlen一致。field_id共有設計はumap側と整合 |
| `acquire_umap_sample_rename_lock` | 1333 | {type:umap_sample_rename_input,index:ALL}.value | {type:umap_sample_rename_lock_indicator,index:ALL}.id | OK | 同上。sample_rename:{s}をSpatial側と共有(意図どおり) |
| `reflect_sample_rename_lock` | 1356 | edit_lock_state.data | {type:sample_rename_input,index:MATCH}.disabled, {type:sample_rename_lock_indicator,index:MATCH}.children | OK | 2出力全分岐一致。user_id比較はacquire側でsession_idを渡しており整合 |
| `reflect_umap_sample_rename_lock` | 1373 | edit_lock_state.data | {type:umap_sample_rename_input,index:MATCH}.disabled, {type:umap_sample_rename_lock_indicator,index:MATCH}.children | OK | 同上 |
| `acquire_sample_rotation_lock` | 1395 | {type:per_sample_rotation,index:ALL}.value, {type:per_sample_flip_h,index:ALL}.value, {type:per_sample_flip_v,index:ALL}.value | {type:sample_rotation_lock_indicator,index:ALL}.id | OK | 3種コンポーネントを1ロックに集約。len(ids)=indicator数=サンプル数で一致 |
| `reflect_sample_rotation_lock` | 1420 | edit_lock_state.data | {type:per_sample_rotation,index:MATCH}.disabled, {type:per_sample_flip_h,index:MATCH}.disabled, {type:per_sample_flip_v,index:MATCH}.disabled 他1 | OK | 4出力全分岐一致 |
| `acquire_cluster_color_lock` | 1445 | {type:cluster_color_picker,index:ALL}.value | {type:cluster_color_lock_indicator,index:ALL}.id | SUSPECT | update_custom_color_mapが全picker値を書き戻すため本callbackが全picker同時トリガーで再発火し、ctx.triggered_idは先頭pickerに解決→先頭クラスタのロックを誤取得。スウォッチ経由では正クラスタのロックが取得されない(要検証) |
| `reflect_cluster_color_lock` | 1466 | edit_lock_state.data | {type:cluster_color_picker,index:MATCH}.disabled, {type:cluster_color_lock_indicator,index:MATCH}.children | OK | 2出力全分岐一致 |
| `clientside spatial_restyle.marker_size` | 1496 | spatial_marker_size.value | spatial_restyle_dummy.data(dup) | OK | JS関数実在(app/assets/spatial_restyle.js:103)。依存グラフに登録あり |
| `clientside spatial_restyle.label_size` | 1496 | spatial_label_size.value | spatial_restyle_dummy.data(dup) | OK | JS関数実在(spatial_restyle.js:108) |
| `clientside spatial_restyle.spot_opacity` | 1496 | hne_overlay_opacity.value | spatial_restyle_dummy.data(dup) | OK | JS関数実在(spatial_restyle.js:113)。hne_overlay_opacityが通常タイルのspot不透明度を兼ねる設計はサーバ側(:1193)と一致 |
| `clientside spatial_restyle.hne_marker_size` | 1496 | hne_overlay_marker_size.value | spatial_restyle_dummy.data(dup) | OK | JS関数実在(spatial_restyle.js:119) |

## App/app/callbacks/interactive_umap.py (5 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `update_umap_plot` | 509 | umap_color_by.value, umap_highlight_cluster.value, umap_show_legend.value 他12 | interactive_umap_plot.figure | SUSPECT | guard順バグ: 閉状態が_accordion_seenに記録されず再オープン時に再描画スキップ=閉中の変更が反映されない(実行検証済)。加えて session_id=None で全セッション共有キー |
| `toggle_umap_integrated_visibility` | 591 | umap_display_mode.value | umap_integrated_wrapper.style | OK | 1出力OK |
| `toggle_merge_controls` | 606 | seurat_rds_path_store.data, fullscreen_closed_trigger.data | umap_merge_controls_wrapper.style | OK | Cluster_merged 列有無で表示。_set_active_key 済み |
| `update_umap_per_sample` | 625 | umap_display_mode.value, umap_highlight_cluster.value, umap_show_labels.value 他13 | umap_per_sample_container.children | SUSPECT | session_id は渡している(662)が guard順バグは同じ: 閉中の変更→再オープンで画面/一括保存が古いまま |
| `save_umap_display_settings` | 747 | umap_marker_size.value, umap_label_size.value, umap_show_labels.value 他4 | umap_display_save_trigger.data | OK | _set_active_key あり(ver51.8修正確認)。常に no_update 返し=ダミー出力で正当 |

## App/app/callbacks/interactive_validation.py (26 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `_validate[volcano_fc_threshold]` | 60 | volcano_fc_threshold.value | volcano_fc_threshold.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[volcano_p_threshold]` | 60 | volcano_p_threshold.value | volcano_p_threshold.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[volcano_y_max]` | 60 | volcano_y_max.value | volcano_y_max.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[heatmap_top_n]` | 60 | heatmap_top_n.value | heatmap_top_n.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[onthefly_de_fc]` | 60 | onthefly_de_fc.value | onthefly_de_fc.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[onthefly_de_p]` | 60 | onthefly_de_p.value | onthefly_de_p.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[feature_intensity_min]` | 60 | feature_intensity_min.value | feature_intensity_min.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[feature_intensity_max]` | 60 | feature_intensity_max.value | feature_intensity_max.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[umap_n_neighbors_input]` | 60 | umap_n_neighbors_input.value | umap_n_neighbors_input.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[umap_min_dist_input]` | 60 | umap_min_dist_input.value | umap_min_dist_input.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[umap_dims_input]` | 60 | umap_dims_input.value | umap_dims_input.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[p_thresh]` | 60 | p_thresh.value | p_thresh.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[logfc_thresh]` | 60 | logfc_thresh.value | logfc_thresh.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[reanalysis_p_thresh]` | 60 | reanalysis_p_thresh.value | reanalysis_p_thresh.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[reanalysis_logfc_thresh]` | 60 | reanalysis_logfc_thresh.value | reanalysis_logfc_thresh.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[tolerance_mz]` | 60 | tolerance_mz.value | tolerance_mz.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[reanalysis_tolerance_mz]` | 60 | reanalysis_tolerance_mz.value | reanalysis_tolerance_mz.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[reann_tolerance]` | 60 | reann_tolerance.value | reann_tolerance.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[mz_align_ppm]` | 60 | mz_align_ppm.value | mz_align_ppm.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[calibration_search_window]` | 60 | calibration_search_window.value | calibration_search_window.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[calibration_min_peaks]` | 60 | calibration_min_peaks.value | calibration_min_peaks.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[int_cal_search_window]` | 60 | int_cal_search_window.value | int_cal_search_window.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[int_cal_min_peaks]` | 60 | int_cal_min_peaks.value | int_cal_min_peaks.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[volcano_label_top_n]` | 60 | volcano_label_top_n.value | volcano_label_top_n.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[input_export_top_n]` | 60 | input_export_top_n.value | input_export_top_n.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |
| `_validate[scils_spot_block]` | 60 | scils_spot_block.value | scils_spot_block.invalid | OK | PARAM_BOUNDS にキー実在・None は valid 扱い・デフォルト引数束縛で閉包バグ無し |

## App/app/callbacks/lite_view_callbacks.py (11 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `route_lite_url` | 86 | url_bar.pathname | lite_target_store.data | OK | 正規表現 ^/lite/<pid>/<sid>/?$。非該当は no_update。lite_target_store は常設 shell (lite_view.py:39) に実在 |
| `navigate_to_lite_page` | 101 | lite_target_store.data | current_page.data(allow_duplicate) | OK | share_callbacks.route_share_url とのハッシュ衝突回避の 2 段パターン。依存 JSON で current_page.data@e9c37≠@2fe48 を確認、重複なし |
| `initialize_lite_view` | 123 | lite_target_store.data, lv_method_store.data | lv_report_body.children, lv_error.is_open, lv_error.children | OK | 全 6 分岐が 3 値 return。_lite_cache_key は (pid,sid,method,mtime_ns,size) でスコープ適正。ver51.8 の get/put ローカル保持も正しい。ただし C10-3 により初回 2 回実行される |
| `update_method_store` | 242 | lv_method_selector.value | lv_method_store.data | SUSPECT | Input の lv_method_selector は動的挿入・Output の lv_method_store は静的 shell 側のため、Dash 2.18 では挿入時に prevent_initial_call が効かず発火。初期 current={} と不一致で store を書き、initialize_lite_view の全再構築が毎回 2  |
| `toggle_volcano_section` | 260 | {type:lv_volcano_toggle,cluster:MATCH}.n_clicks | {type:lv_volcano_collapse,cluster:MATCH}.is_open | OK | pattern id は同モジュール _build_cluster_card_expand_contents 生成分とキー一致。Output/Input 同時挿入なので初期発火なし |
| `toggle_cluster_card` | 387 | {type:lv_card_toggle,cluster:MATCH}.n_clicks | {type:lv_card_collapse,cluster:MATCH}.is_open(dup), {type:lv_card_body,cluster:MATCH}.children(dup), {type:lv_card_toggle,cluster:MATCH}.children(dup) | OK | 全 5 分岐 3 値 return。btn_id['cluster'] で対象クラスタ特定=取り違えなし。bundle None 時は silent no_update(開かない)のみ軽微 |
| `update_spatial_labels` | 444 | {type:lv_show_labels_switch,scope:ALL}.value, lv_spatial_label_size.value, lv_spatial_panel_size.value | lv_spatial_container.children | OK | Switch は scope:'main' 1 個のみで [0] 参照は安全。Output も同一挿入ツリー内のため mount 時発火なし。bundle None→silent no_update は再構築失敗時のみ |
| `update_umap_labels` | 483 | {type:lv_show_umap_labels_switch,scope:ALL}.value, lv_umap_label_size.value, lv_umap_panel_size.value | lv_umap_container.children | OK | update_spatial_labels と対称。id は _build_overview_section/_size_toolbar 生成分と一致確認済 |
| `_flush_settings_before_lite_open` | 1679 | btn_open_lite_viewer.n_clicks | lite_viewer_open_signal.data | OK | save_interactive_settings(key,value,rds_path) 署名一致。保存失敗でも signal を返しタブは開く(警告ログのみ、設計意図と判断) |
| `(clientside) open_lite_tab` | 1719 | lite_viewer_open_signal.data | lite_viewer_open_dummy.data | OK | 循環回避のダミー Store 実在 (interactive_tab.py:1906)。サーバ往復後の window.open はポップアップブロッカーに掛かり得る(既知トレードオフ) |
| `(clientside) lv_resize` | 1766 | {type:lv_card_collapse,cluster:ALL}.is_open, {type:lv_show_labels_switch,scope:ALL}.value, {type:lv_show_umap_labels_switch,scope:ALL}.value 他2 | lv_resize_trigger.data | OK | 動的挿入での発火はリサイズ再計算という設計意図そのもの。lv_resize_trigger は常設 shell に実在 |

## App/app/callbacks/parquet_maintenance_callbacks.py (4 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `toggle_parquet_maintenance_modal` | 54 | open_parquet_maintenance_modal.n_clicks, open_parquet_maintenance_modal_landing.n_clicks, parquet_maint_close_btn.n_clicks | parquet_maintenance_modal.is_open | OK | triggered_id 3分岐+no_update fallback。取り違えなし |
| `run_parquet_repack` | 76 | parquet_maint_run_btn.n_clicks | parquet_maint_alert.children, parquet_maint_log.children, parquet_maint_log.style 他8 | OK | 11 Output/全5分岐11値(_NO_UPDATE_11)。二重起動防止はモジュール大域 _repack_process_state(プロセス単位=全セッション共有。保守タスクとして設計意図通りだが同時実行者は相互排他) |
| `stop_parquet_repack` | 187 | parquet_maint_stop_btn.n_clicks | parquet_maint_alert.children, parquet_maint_interval.disabled, parquet_maint_stop_btn.disabled 他1 | OK | 4 Output/両分岐4値。大域プロセスを停止(どのセッションからでも) |
| `poll_parquet_repack` | 285 | parquet_maint_interval.n_intervals | parquet_maint_log.children, parquet_maint_progress_bar.value, parquet_maint_progress_bar.label 他6 | OK | 9 Output/全3分岐9値。進捗regexは re.MULTILINE 付き(正)。finished+Errors>0 は _summary_has_errors で警告色に二重ガード。サーバ再起動時はproc消失→running表示継続(S4級) |

## App/app/callbacks/preflight_callbacks.py (5 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `run_preflight` | 298 | btn_preflight_run.n_clicks | preflight_results_container.children(dup), preflight_store.data(dup), preflight_poll.disabled(dup) | OK | 全 7 分岐 3 値 return。二重起動防止は proc.poll() で正当。_preflight_process_state はプロセス全域共有だが Rscript 排他は設計意図。poll 側の共有状態競合は C10-4 参照 |
| `poll_preflight` | 414 | preflight_poll.n_intervals | preflight_results_container.children, preflight_store.data, preflight_poll.disabled | SUSPECT | proc None (別タブ/別セッションの poll が先に完了処理して共有 state をクリア、またはサーバ再起動) だと status 恒 None → no_update×3 を永久に返し、スピナー表示のまま 1.5 秒間隔ポーリングが止まらない (C10-4) |
| `autoload_saved_diagnostics` | 477 | selected_project.data, current_sub_project_id.data | preflight_results_container.children(dup), preflight_store.data(dup), preflight_poll.disabled(dup) | OK | 両 Input とも同一アクション(プロジェクト切替)のため triggered_id 分岐不要は妥当。実行中ガード・保存なし silent は仕様通り。全分岐 3 値 |
| `load_saved_diagnostics_button` | 503 | btn_preflight_load.n_clicks | preflight_results_container.children(dup), preflight_store.data(dup), preflight_poll.disabled(dup) | OK | autoload と違い保存なし/未選択を明示 Alert。パースエラー時 store 非更新で表示のみ。全分岐 3 値 |
| `apply_preflight_recommendation` | 542 | btn_preflight_apply.n_clicks | umap_n_neighbors_input.value, umap_dims_input.value, umap_min_dist_input.value 他1 | OK | 4 出力 4 値、None は no_update で個別スキップ。反映先 4 入力は settings_tab.py:1032-1063 に実在。推奨 nn は診断側 allowed_range 上限にクランプ済(入力側 max=100 超過は理論上のみ) |

## App/app/callbacks/preset_callbacks.py (4 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `toggle_preset_modal` | 16 | open_preset_modal.n_clicks | preset_modal.is_open, preset_select.options, preset_select.value 他1 | OK | 全分岐4個。開時にoptions再取得。閉はヘッダX(client-side)で整合 |
| `save_preset_cb` | 42 | preset_save_btn.n_clicks | preset_select.options(dup), preset_status.children(dup) | OK | State19個の並び=PRESET_KEYS(19)と完全一致。zip取り違え無し。プリセット自体は全ユーザー共有(ファイルロック有・設計) |
| `load_preset_cb` | 87 | preset_load_btn.n_clicks | preset_status.children(dup), analysis_method.value(dup), analysis_method_tims.value(dup) 他17 | OK | Output19個の並び=PRESET_KEYSと完全一致。全分岐20個。欠損キーはno_updateで既存値保持 |
| `delete_preset_cb` | 128 | preset_delete_btn.n_clicks | preset_select.options(dup), preset_select.value(dup), preset_status.children(dup) | OK | 全分岐3個 |

## App/app/callbacks/project_callbacks.py (37 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `toggle_pages` | 96 | current_page.data | page_landing.style, page_action.style, page_analysis.style 他1 | OK | 4分岐+デフォルト全て4要素。全ID実在。登録済 |
| `apply_shared_mode` | 125 | shared_session.data | main_tabs_wrapper.className, back_to_action_from_analysis.style, header_analysis_buttons.style | OK | 2分岐とも3要素。OK |
| `render_project_cards` | 148 | current_page.data, project_list_refresh.data, project_sort_order.value 他1 | project_cards_container.children | OK | 1出力。edit/delete/select_project_btn を動的生成(各1 callbackが消費) |
| `select_project` | 341 | {"index": "ALL", "type": "select_project_btn"}.n_clicks | current_page.data, selected_project.data, action_page_project_name.children 他1 | OK | triggered_id.index使用。ガード適切。4出力一致 |
| `open_interactive_from_landing` | 378 | open_interactive_from_landing_btn.n_clicks | current_page.data, main_tabs.active_tab, interactive_entry_mode.data | OK | 3出力一致 |
| `render_sub_project_cards` | 401 | current_page.data, selected_project.data, sub_project_list_refresh.data 他2 | sub_project_cards_container.children | OK | 1出力。sub_action_*5種+edit/delete_sub_btn を動的生成(各1 callbackが消費) |
| `sub_action_new_analysis` | 642 | {"index": "ALL", "type": "sub_action_analysis"}.n_clicks | current_page.data, main_tabs.active_tab, current_sub_project_id.data 他19 | SUSPECT | 22出力は全分岐一致だが `or no_update`/`if settings else no_update` で未保存フィールドに前サブプロの値が残留(ver52.3がsub_action_interactiveで修正した同型) |
| `sub_action_interactive` | 718 | {"index": "ALL", "type": "sub_action_interactive"}.n_clicks | current_page.data, main_tabs.active_tab, interactive_result_folder.value 他5 | OK | ver52.3修正済: 常に明示値を返す。8出力一致 |
| `header_title_to_landing` | 772 | header_title_home_btn.n_clicks, header_title_home_btn_action.n_clicks | current_page.data | OK | 両ボタンとも landing 遷移(意図通り) |
| `back_to_landing` | 794 | back_to_landing.n_clicks | current_page.data | OK | OK |
| `update_back_button_text` | 809 | interactive_entry_mode.data | back_to_action_from_analysis.children | OK | OK |
| `back_to_action` | 821 | back_to_action_from_analysis.n_clicks | current_page.data | OK | entry_mode分岐OK |
| `toggle_create_modal` | 839 | open_create_project_modal.n_clicks, cancel_create_project.n_clicks | create_project_modal.is_open | OK | confirmはInputに含めず(close はhandle_create_projectがvalidation後に実施)— 正しい構造 |
| `handle_create_project` | 859 | confirm_create_project.n_clicks | new_project_name.value, new_project_experiment_date.value, new_project_memo.value 他6 | OK | 9出力全分岐一致。validation失敗時 is_open=True 維持+エラー表示 |
| `toggle_delete_modal` | 914 | {"index": "ALL", "type": "delete_project_btn"}.n_clicks, cancel_delete_project.n_clicks, confirm_delete_project.n_clicks | delete_project_modal.is_open, delete_target_project_id.data | SUSPECT | confirm で store クリア。:939 と (confirm_delete_project→delete_target_project_id.data) ペア共有。Dash2.18.2ではStateはblockされず同一passのlayoutスナップショットからState充填→削除は正常動作(決定的)。将来のdispatch仕様変更で常に無反応化す |
| `handle_delete_project` | 939 | confirm_delete_project.n_clicks | delete_target_project_id.data, project_list_refresh.data, notification_toast.is_open 他2 | SUSPECT | State(delete_target_project_id)はクリア前の値を受け取る(renderer実装で確認)。5出力全分岐一致。動作はするが:914との共有Input設計は脆い |
| `toggle_edit_project_modal` | 970 | {"index": "ALL", "type": "edit_project_btn"}.n_clicks, cancel_edit_project.n_clicks | edit_project_modal.is_open, edit_target_project_id.data, edit_project_name.value 他6 | OK | 9出力全分岐一致 |
| `handle_edit_project` | 1019 | confirm_edit_project.n_clicks | project_list_refresh.data, edit_project_modal.is_open, edit_project_error.children | OK | 3出力全分岐一致。validation失敗時モーダル維持+エラー表示 |
| `toggle_create_sub_modal` | 1064 | open_create_sub_project_modal.n_clicks, cancel_create_sub_project.n_clicks, confirm_create_sub_project.n_clicks | create_sub_project_modal.is_open | SUSPECT | confirm を Input に含み無条件 close。name未入力で confirm するとサブプロ未作成のままモーダルが閉じエラー表示なし(project版ver3.16修正が未適用) |
| `handle_create_sub_project` | 1083 | confirm_create_sub_project.n_clicks | new_sub_name.value, new_sub_experiment_date.value, new_sub_target_compound.value 他7 | OK | 10出力全分岐一致。ただしvalidation失敗をユーザーに通知する出力が無い(:1064の症状の片割れ) |
| `toggle_delete_sub_modal` | 1138 | {"index": "ALL", "type": "delete_sub_btn"}.n_clicks, cancel_delete_sub_project.n_clicks, confirm_delete_sub_project.n_clicks | delete_sub_project_modal.is_open, delete_target_sub_project_id.data | SUSPECT | :914と同型(サブプロ版)。Dash2.18.2では正常動作、実装依存 |
| `handle_delete_sub_project` | 1166 | confirm_delete_sub_project.n_clicks | delete_target_sub_project_id.data, sub_project_list_refresh.data, notification_toast.is_open 他2 | SUSPECT | :939と同型(サブプロ版)。5出力全分岐一致 |
| `toggle_edit_sub_modal` | 1198 | {"index": "ALL", "type": "edit_sub_btn"}.n_clicks, cancel_edit_sub_project.n_clicks, confirm_edit_sub_project.n_clicks | edit_sub_project_modal.is_open, edit_target_sub_project_id.data, edit_sub_name.value 他8 | SUSPECT | confirm で無条件 close+全フィールドクリア。name空で保存すると編集内容が黙って破棄される(project版はvalidation失敗時モーダル維持)。11出力全分岐一致 |
| `handle_edit_sub_project` | 1255 | confirm_edit_sub_project.n_clicks | sub_project_list_refresh.data | OK | 1出力。name空はno_update(症状は:1198側) |
| `open_share_modal` | 1301 | {"index": "ALL", "type": "sub_action_share"}.n_clicks | share_create_modal.is_open, share_target_sub_id.data, share_target_info.children 他2 | OK | 5出力全分岐一致 |
| `generate_share_link` | 1332 | generate_share_link.n_clicks | share_result_area.style, share_generated_url.children, share_links_container.children | OK | 3出力全分岐一致。prewarm はバックグラウンドスレッドでbest-effort |
| `_toggle_share_kind_inputs` | 1420 | share_kind_radio.value, share_require_password.value | share_expiry_wrapper.style, share_persistent_warning.style | OK | 2出力一致 |
| `close_share_modal` | 1438 | close_share_modal.n_clicks | share_create_modal.is_open | OK | OK |
| `render_share_links` | 1450 | current_page.data, selected_project.data | share_links_container.children | OK | delete_share_btn を動的生成(1 callbackが消費) |
| `load_project_info` | 1464 | current_page.data, selected_project.data, project_list_refresh.data | project_info_google_keep_url.value, project_info_msi_share_url.value, project_info_other_url.value 他2 | OK | 5出力全分岐一致。initial_duplicate指定 |
| `save_project_info` | 1494 | project_info_save_btn.n_clicks | project_info_status.children, project_list_refresh.data | OK | 2出力全分岐一致。例外はステータス表示に変換 |
| `open_share_delete_modal` | 1591 | {"token": "ALL", "type": "delete_share_btn"}.n_clicks | share_delete_modal.is_open, share_delete_target_token.data | OK | 2出力一致。triggered_id.token使用 |
| `confirm_delete_share_link` | 1605 | confirm_delete_share.n_clicks | share_delete_modal.is_open, share_links_container.children | OK | 期間付き→無期限の順で削除試行。2出力一致 |
| `cancel_delete_share_link` | 1624 | cancel_delete_share.n_clicks | share_delete_modal.is_open | OK | OK |
| `toggle_restore_modal` | 1639 | open_restore_modal_btn.n_clicks, close_restore_modal_btn.n_clicks | restore_project_modal.is_open | OK | in比較で両ボタン網羅 |
| `execute_scan` | 1653 | restore_scan_btn.n_clicks | restore_scan_results.children, restore_scan_data.data, restore_execute_btn.disabled | OK | 4分岐全て3要素。restore_action Selectを動的生成(execute_restoreがState消費) |
| `execute_restore` | 1797 | restore_execute_btn.n_clicks | restore_status.children, project_list_refresh.data, restore_execute_btn.disabled | OK | 3出力全分岐一致。ALL Stateのid/value zipでaction_map構築は正しい |

## App/app/callbacks/provenance_callbacks.py (14 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `save_volcano_settings` | 69 | volcano_cluster_select.value, volcano_fc_threshold.value, volcano_p_threshold.value 他4 | provenance_save_trigger.data(dup) | OK | _set_active_key(rds_path)のver51.8正規形。rds_path無ならPreventUpdate。全ID実在・登録済 |
| `save_heatmap_settings` | 101 | heatmap_top_n.value, heatmap_scale.value, heatmap_annotation_switch.value 他1 | provenance_save_trigger.data(dup) | OK | 同上 |
| `save_feature_settings` | 125 | feature_select.value, feature_mz_min.value, feature_mz_max.value 他8 | provenance_save_trigger.data(dup) | OK | 同上 |
| `save_onthefly_de_settings` | 166 | onthefly_de_mode.value, onthefly_de_target.value, onthefly_de_fc.value 他2 | provenance_save_trigger.data(dup) | OK | 同上 |
| `save_umap_view_settings` | 192 | umap_display_mode.value, umap_highlight_cluster.value, umap_facet_by.value 他2 | provenance_save_trigger.data(dup) | OK | 同上 |
| `save_spatial_view_settings` | 214 | interactive_sample.value, spatial_highlight_cluster.value, hne_overlay_show.value 他3 | provenance_save_trigger.data(dup) | OK | 同上 |
| `save_hne_export_settings` | 243 | hne_export_method.value, hne_export_intensity.value, hne_export_unit.value 他1 | provenance_save_trigger.data(dup) | OK | 同上 |
| `save_export_settings` | 266 | input_export_top_n.value, export_method_selector.value, export_include_deg.value 他3 | provenance_save_trigger.data(dup) | OK | 同上 |
| `export_conditions_bundle` | 316 | btn_export_conditions.n_clicks | div_conditions_status.children | OK | 全分岐return1個。rds無/result_dir無/失敗を文言で返す |
| `toggle_methods_modal` | 352 | btn_show_methods.n_clicks, btn_methods_close.n_clicks | methods_modal.is_open, methods_unlock_store.data(dup), methods_unlock_error.children(dup) | SUSPECT | F1: 再施錠が不完全。unlock_storeのみNoneにし、lock/content panelのstyle・rendered_store・body・ボタンdisabledを復元しない(writerはunlock_methodsのみ=登録グラフで確認)→解錠→閉→再開でパスワード無しに本文表示のまま |
| `unlock_methods` | 371 | btn_methods_unlock.n_clicks | methods_unlock_store.data, methods_unlock_error.children, methods_lock_panel.style 他5 | OK | 全分岐8個(_locked含む)。パスワードはStoreに残さずサーバ側tier検証あり |
| `switch_methods_format` | 439 | methods_format.value, methods_rendered_store.data | methods_body_ja.children, methods_body_en.children, methods_legend.style | OK | 全分岐3個。rendered空なら空表示 |
| `<clientside L476 copy_methods>` | 476 | btn_methods_copy.n_clicks | methods_copy_status.children | OK | DOMコピー。解錠チェック無し=F1状態では閉→再開後もコピー可(本文がDOMに残るため) |
| `download_methods_bundle` | 520 | btn_download_methods.n_clicks | dl_conditions_bundle.data | OK | ガード自体は正当。ただしF1後(閉→再開)はボタンenabledのままPreventUpdateで無反応になる(原因はtoggle側) |

## App/app/callbacks/rds_maintenance_callbacks.py (4 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `toggle_rds_maintenance_modal` | 46 | open_rds_maintenance_modal.n_clicks, open_rds_maintenance_modal_landing.n_clicks, rds_maint_close_btn.n_clicks | rds_maintenance_modal.is_open | OK | triggered_id 3分岐+no_update fallback |
| `run_rds_slim` | 67 | rds_maint_run_btn.n_clicks | rds_maint_alert.children, rds_maint_log.children, rds_maint_log.style 他8 | OK | 11 Output/全5分岐11値。大域 _slim_process_state で二重起動防止(全セッション共有・設計意図) |
| `stop_rds_slim` | 198 | rds_maint_stop_btn.n_clicks | rds_maint_alert.children, rds_maint_interval.disabled, rds_maint_stop_btn.disabled 他1 | OK | 4 Output/両分岐4値 |
| `poll_rds_slim` | 281 | rds_maint_interval.n_intervals | rds_maint_log.children, rds_maint_progress_bar.value, rds_maint_progress_bar.label 他6 | MISMATCH | C11-1: :237-238 の進捗regexに re.MULTILINE 無し→^ が先頭のみ一致→total/current ほぼ常に0→バー0%固定(parquet側:230コメントが本モジュールの既存不具合と明記)。C11-2: R script は per-file エラーでも exit 0→finished→緑『完了しました。』+_render |

## App/app/callbacks/scils_converter_callbacks.py (2 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `toggle_scils_converter_modal` | 21 | open_scils_converter_modal.n_clicks, scils_cancel_btn.n_clicks | scils_converter_modal.is_open, scils_conversion_result.children | OK | 2 Output/全3分岐2値。is_open トグル式(cancel はモーダル内のみ押下可なので取り違えなし) |
| `run_scils_conversion` | 49 | scils_run_btn.n_clicks | scils_conversion_result.children | OK | 1 Output/全分岐1値。例外3種を UI Alert 化 |

## App/app/callbacks/session_callbacks.py (1 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `toggle_backup_list_modal` | 16 | open_backup_list_btn.n_clicks, close_backup_list_btn.n_clicks | backup_list_modal.is_open, backup_list_body.children | OK | 2 Output/全3分岐2値。triggered_id で close 分岐、他は open+一覧構築 |

## App/app/callbacks/share_callbacks.py (2 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `route_share_url` | 87 | url_bar.pathname | current_page.data, interactive_result_folder.value, interactive_msi_folder.value 他5 | OK | 8 Output/全4分岐8値。/share//view/ トークン解決。ver52.3 で no_update 残留による別プロジェクト混入を修正済(空文字明示)。_shared_data キャッシュは token キー+LRU/TTL(scope正)。main_tabs は二段パターンで衝突回避 |
| `_shared_activate_interactive_tab` | 212 | shared_session.data | main_tabs.active_tab | OK | shared_session→interactive タブ活性。tab_url_routing と Input が異なり (Input,Output) 重複無し |

## App/app/callbacks/tab_url_routing.py (4 件)

| 関数 | 行 | Input | Output | 判定 | 備考 |
|---|---|---|---|---|---|
| `_detect_app_path` | 43 | url_bar.pathname | app_path_target_store.data | OK | 2段Storeの1段目。/app/*のみ書込。PIC=Trueのため初回ロードでは発火しない(設計) |
| `_route_app_url_to_analysis` | 57 | app_path_target_store.data | current_page.data(dup), url_bar.pathname(dup) | SUSPECT | F5(INFO): Output current_pageは全分岐no_updateで一度も書かれない(宣言のみ)。deep link正規化はin-session履歴移動時のみ有効。重複ペア回避(2段Store)は正しく機能 |
| `_sync_tab_from_url` | 92 | url_bar.pathname | main_tabs.active_tab(dup) | OK | 初回ロード非発火(設計)。同一タブ/未知パスno_update。ループ無し |
| `_sync_url_from_tab` | 124 | main_tabs.active_tab | url_bar.pathname(dup) | SUSPECT | F4(S4): _TAB_TO_PATHにhneタブが無く(docstringのresults/historyは実在せず)、H&Eタブ中はURLが直前の/app/*のまま=ブックマークでタブ再現不可。analysis外/共有/同一パスのガードは正当 |