# 付録C: 全指摘事項の詳細台帳

U_Analysis デバッグ総点検 (2026-08) の付録。本文は `DEBUG_AUDIT_2026-08.md`。

本ファイルは、独立した検証官による反証審査を通過した全指摘 67 件の詳細記録である。
各項目は「利用者から見た症状」「科学的影響」「検証の経緯(どう反証を試み、なぜ失敗したか)」
「再現手順」「根拠(ファイル:行)」「修正方針」で構成する。

- **判定**: UPHELD = 反証できず主張が成立 / WEAKENED = 条件付きで成立(訂正後の主張を併記) /
  OVERTURNED = 反証成立(誤検出。透明性のため残す)
- **確証度**: [実行確認] = 実際にアプリ・関数・スクリプトを動かして再現 /
  [コード確認] = コード読解のみ / [要データ検証] = 実データが必要で未確定
- **修正区分**: 機械的修正 = 挙動の設計判断なしに直せる / 要科学的判断・要設計判断 = 承認が必要

---

## C-1. ② ボタン・UI 結線に関する指摘 (38 件)

### 1. [S2] sub_action_new_analysis: 前サブプロジェクトの設定値が残留 (ver52.3 同型の未修正残り)

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/project_callbacks.py:692`

**利用者から見た症状**

サブプロジェクト B の「解析」ボタンを押して解析設定画面を開いたのに、データフォルダ・出力先フォルダの欄には直前に開いていたサブプロジェクト A のパスが入ったままになる。画面のどこにも「今どのサブプロジェクトを操作しているか」は表示されないため、そのまま実行すると『B の解析』と称して A のデータを解析し、結果を A の出力先に書き、設定と結果は B のものとして記録される(データの取り違え)。

**検証の経緯(反証の試みと結果)**

反証を4方向から試みたが全て失敗し、稼働アプリの実操作で決定的に再現した。(1)「他所で上書きされる説」= data_folder への書き手は project_callbacks.py:646 と file_handlers.py:480/579/603 のみで、後者は全てユーザーの明示操作(フォルダ参照・初期設定適用・手法切替)でしか発火せず遷移では働かない。(2)「auto_switch_data_folder が連鎖して上書きする説」= 保存済み設定が無い場合は analysis_method 自体が no_update なので連鎖しない(実測で確認)。(3)「data_folder 空のサブプロは実在しない説」= 作成モーダルの当該欄は placeholder が『データフォルダのパス（任意）』であり、通常 UI で空のまま作成できることを実操作で確認。(4)「画面表示で気付ける説」= 解析画面のヘッダは固定文字列で、current_sub_project_id は不可視 Store。どのサブプロジェクトを操作中かを示す表示は解析画面に存在しない。既存テストも 0 件。実測では SUB_B の応答に data_folder/output_dir が現れず(=no_update)、current_sub_project_id だけが SUB_B に更新された。重大度は台帳 S3 から S2 に引き上げ: 実行に進むと SUB_A のフォルダが SUB_B の設定として保存され(analysis_callbacks.py:454-457)、結果は SUB_A の出力先に SUB_B 名義で記録される(:827)ため由来の取り違えが起きる。ただし既存結果の上書きには確認モーダル(:217-251, :399-404)が挟まるので無条件のデータ破壊(S1)には至らない。なお保存済み設定が『ある』場合は analysis_method が更新されるため auto_switch_data_folder(file_handlers.py:487-492)が連鎖発火し、復元した data_folder を全体既定フォルダで潰す — どちらの経路でもサブプロ固有のフォルダが画面に出ない点は共通で、修正時に併せて見る必要がある。

**再現手順**

1) master password でログイン。2) プロジェクトを作り、サブプロジェクト SUB_A をデータフォルダ /V1/DATA_A・出力先 /V1/OUT_A で作成。3) サブプロジェクト SUB_B を両方とも空欄のまま作成(UI 上「任意」)。4) SUB_A の「解析」を押す → 欄に /V1/DATA_A と /V1/OUT_A が入る。5)「< プロジェクトに戻る」で一覧に戻り、SUB_B の「解析」を押す。6) 欄は /V1/DATA_A と /V1/OUT_A のまま。サーバ応答にも data_folder/output_dir が含まれない。スクリプト: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v1_probe2.py

**根拠**

- `App/app/callbacks/project_callbacks.py:691-693: sub_id,                                                 # current_sub_project_id / data_folder or no_update,                               # data_folder / output_dir or no_update,                                 # output_dir`
- `App/app/callbacks/project_callbacks.py:697: settings.get("p_thresh") if settings else no_update,    # p_thresh`
- `App/app/callbacks/project_callbacks.py:747-749: # ★ ver52.3: `x or no_update` をやめ、常に明示的な値を入れる。 / #   `no_update` は「変更しない」なので、**切り替える前のサブプロジェクトの / #   結果フォルダ / MSI データフォルダがそのまま残る**。`
- `/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/audit/w3/v1_key_evidence.txt:5: {"multi":true,"response":{"current_page":{"data":"analysis"},"main_tabs":{"active_tab":"settings"},"current_sub_project_id":{"data":"5c4bd3f4"}}}   ← SUB_B の応答に data_folder/output_dir が無い = no_update`
- `/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/audit/w3/v1_key_evidence.txt:8: {"data_folder_A": "/V1/DATA_A", "data_folder_B": "/V1/DATA_A", "retained": true, "output_dir_A": "/V1/OUT_A", "output_dir_B": "/V1/OUT_A", "output_retained": true}`
- `App/app/callbacks/analysis_callbacks.py:454-457: save_sub_project_settings(project_id, current_sub_project_id, { / "analysis_method": desi_method, / "analysis_method_tims": tims_method, / "data_folder": data_folder,`

**修正方針**

sub_action_interactive が ver52.3 で採った形(project_callbacks.py:747-755 のコメント)をそのまま適用し、:692-693 の `data_folder or no_update` / `output_dir or no_update` を `data_folder` / `output_dir`(未設定なら空文字を明示)に変える。これだけでフォルダの取り違えは止まる。閾値系(:696-710 の `... if settings else no_update`)も明示値にすべきだが、空文字ではなくアプリ既定値を入れる必要があるので既定値の決定だけは設計判断。


### 2. [S2] toggle_edit_sub_modal: confirm で無条件クローズ+フィールドクリア → name 空保存で編集内容が黙って破棄

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/project_callbacks.py:1225`

**利用者から見た症状**

サブプロジェクトの編集画面でメモやフォルダを直し、ついでに名前を打ち直そうとして名前欄を空にしたまま「保存」を押すと、モーダルは普通に閉じてエラーも出ないのに、変更は何ひとつ保存されていない。入力欄も全部消えるので、開き直しても打ち直した内容は戻ってこない。保存できたように見えるため、後から気付くのが難しい。

**検証の経緯(反証の試みと結果)**

最も強い反証仮説は「2 つの callback が同時発火するので、名前の有無に関係なく保存が壊れているのでは(= 名前空が原因という主張は不正確)」だったが、対照実験で明確に否定した: 名前を SUB_A のまま残してメモだけ変更して保存すると sub_project_list_refresh:1 が返り projects.json に反映される。原因は競合ではなく handle_edit_sub_project:1277-1278 の name 検証分岐で確定した。実測では、メモを EDIT_MARK_SHOULD_BE_LOST・データフォルダを /V1/DATA_A_EDITED に変えて名前だけ空にして「保存」を押すと、toggle_edit_sub_modal が is_open=false と 11 欄全てのクリアを返す一方、handle_edit_sub_project は HTTP 204 を返し projects.json は memo=""・data_folder=/V1/DATA_A のまま変わらなかった(可視アラート 0 件)。重大度は台帳 S3 から S2 に引き上げる。C01-3 と構造は同じだが結果が違い、(a) toggle 側が全欄をクリアするため入力済みの編集内容が復旧不能に破棄される、(b) モーダルが閉じてエラーも出ず一覧のカードも(名前が保存されていないので)元のまま表示されるため、成功と区別がつかない、(c) 名前欄を全選択して打ち直そうとして一瞬空になる操作は日常的に起きる。『変更が反映されない』という S2 の定義に正面から当たる。プロジェクト版 handle_edit_project(:1043-1045)は検証失敗時に is_open=True 維持 + edit_project_error 表示で正しく直っており、サブプロ版のみ未対応という台帳の指摘も裏付けた。

**再現手順**

1) master password でログイン、プロジェクトを開く。2) サブプロジェクトカードの鉛筆(編集)を押す。3) メモとデータフォルダを書き換え、さらにタイトル欄を空にする。4)「保存」を押す。5) モーダルは閉じるがエラーは出ず、projects.json のメモ・データフォルダは変更前のまま。6) 対照: 同じ操作でタイトルを残すと保存される。スクリプト: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v1_probe2.py

**根拠**

- `App/app/callbacks/project_callbacks.py:1223-1225: if (triggered == "cancel_edit_sub_project" / or triggered == "confirm_edit_sub_project"): / return False, "", "", None, "", "", "", [], "", "", ""`
- `App/app/callbacks/project_callbacks.py:1277-1278: if not n_clicks or not project or not sub_id or not name: / return no_update`
- `App/app/callbacks/project_callbacks.py:1043-1045: if not name or not experiment_date: / return (no_update, True, / "「プロジェクトタイトル」と「実験日」は必須です。")   ← プロジェクト版は閉じずにエラー表示`
- `/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/audit/w3/v1_key_evidence.txt:14: {"prefilled": {"name": "SUB_A", ...}, "handle_status": 204, "modal_visible_after": false, "memo_after": "", "data_folder_after": "/V1/DATA_A", "saved": false, "visible_alerts": []}`
- `/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/audit/w3/v1_key_evidence.txt:17: {"memo_after": "EDIT_OK_CONTROL", "saved_ok": true, "name_after": "SUB_A"}   ← 名前ありなら保存される(競合ではない)`
- `/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/audit/w3/v1_key_evidence.txt:20: {"multi":true,"response":{"edit_sub_project_modal":{"is_open":false},"edit_sub_name":{"value":""},...,"edit_sub_data_folder":{"value":""},"edit_sub_memo":{"value":""}}}`

**修正方針**

handle_edit_project(project_callbacks.py:1039-1045)と同じ形にする。toggle_edit_sub_modal の Input から confirm_edit_sub_project を外し(:1212 と :1223-1225 の分岐を cancel のみに)、handle_edit_sub_project の Output に edit_sub_project_modal.is_open と edit_sub_error.children を追加、name 空のときは is_open=True 維持 +「「タイトル」は必須です。」を返す。action_page.py の編集モーダルにエラー表示 Div を 1 つ追加する。


### 3. [S2] C03-1

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正

**利用者から見た症状**

log2FC 閾値の欄に 0（＝フォールドチェンジで絞り込まない、という正当な指定）を入れて「解析実行」を押しても、実際の計算には 0.25 が使われる。画面の入力欄は 0 のままなので、利用者は自分の指定どおりに解析されたと思い込む。結果として出てくる変動代謝物の一覧は、本人が頼んだものより絞り込まれたものになる。p値閾値・m/z許容誤差でも同じことが起き、しかもエラーも警告も一切出ない。さらに紛らわしいのは、同じ 0 を『再解析』の欄に入れた場合はちゃんと 0 として扱われる点で、同じ数字が押すボタンによって別の意味になる。

**検証の経緯(反証の試みと結果)**

(台帳 id は C03-01。ゼロ埋めの表記揺れなので同一項目として扱った) 反証を4方向から試みたが全部失敗した。(1)「0 は UI から入らないのでは」→ 本体と同一宣言 (type=number, min=0) の最小 Dash アプリを実 Chromium で操作し、ブラウザが送るペイロードが文字列 "0" ではなく数値 0 (falsy) であることを傍受して確認。(2)「preflight が弾くのでは」→ 逆で validate_param('logfc_thresh', 0) は (True,'') を返し 0 を正当と承認する。validation.py 自身が「★ 0 を殺さないこと」と書いている。(3)「後段が 0 を拒否するので同じでは」→ analysis_runner:497/502 は is not None 判定なので 0 が届けば 0 を注入する。すり替えは callback 側だけの瑕疵。(4)「R では同じでは」→ FindAllMarkers(logfc.threshold=DEG_LOGFC_TH_VAL) でマーカー絞り込みに直接使われ、0 と 0.25 では DEG 表が別物になる。run_analysis を直接呼んだ実測でも p/fc/tol=0 → 0.05/0.25/0.01 に化け、再解析経路 (is not None) では同じ 0 が 0.0 のまま通ることを確認した。番人の死角も再現: 同じ AST 機構で IfExp 形を走査すると有害 3 箇所ちょうどが出るのに、番人 (BoolOp/Or のみ) は 0 件で 16 passed の緑。重大度は「利用者が入れた設定が反映されない」=S2 の定義そのもので、0 を入れれば 100% 決定的に起きるため S3 から引き上げた。

**再現手順**

実行済み。(A) python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v3_harness.py — run_analysis を直接呼び、p_thresh=0/logfc_thresh=0/tolerance_mz=0 が params で 0.05/0.25/0.01 に化けること、再解析経路では 0.0 のまま通ること、analysis_params.json にも 0.05/0.25/0.01 が記録されることを確認。(B) v3_min_app.py を 127.0.0.1:8795 で起動し v3_probe_min.py (実 Chromium) で `0` をキー入力 → ペイロード "value":0 を確認。(C) 画面操作での再現: 解析設定タブ → log2FC閾値に 0 を入力 → 解析実行 → 出力先の analysis_params.json の logfc_thresh が 0.25 になっている。

**根拠**

- `App/app/callbacks/analysis_callbacks.py:526: "p_thresh": float(p_thresh) if p_thresh else 0.05,`
- `App/app/callbacks/analysis_callbacks.py:527: "logfc_thresh": float(logfc_thresh) if logfc_thresh else 0.25,`
- `App/app/callbacks/analysis_callbacks.py:586: params["tolerance_mz"] = float(tolerance_mz) if tolerance_mz else 0.01`
- `App/app/callbacks/analysis_callbacks.py:753: if reanalysis_p_thresh is not None:  ← 再解析側は 0 を通す正しい対照`
- `App/app/utils/validation.py:62: "logfc_thresh": (0, None, 0.25, "log2FC閾値")  → validate_param('logfc_thresh', 0) = (True, '') を実行確認`
- `App/app/layouts/settings_tab.py:624: dbc.Input(id="logfc_thresh", type="number",   / :625 value=ls.get("logfc_thresh", 0.25), min=0, step=0.05),`
- `App/app/services/analysis_runner.py:502: if params.get("logfc_thresh") is not None:  ← 0 なら 0 を注入する(後段は無罪)`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:1893: deg <- FindAllMarkers(obj, only.pos=FALSE, min.pct=DEG_MIN_PCT_VAL, logfc.threshold=DEG_LOGFC_TH_VAL, test.use="wilcox")`

**修正方針**

L526/527/586 を `float(x) if x is not None else 既定値` に変える（より望ましくはリポジトリに既にある coerce_number(x, "logfc_thresh") を使い既定値の出典を PARAM_BOUNDS 一本にする）。あわせて番人 _or_default_violations に ast.IfExp（test が Name、orelse が数値リテラル）の走査を追加し、KNOWN は空のまま維持する。


### 4. [S2] Spatial: 閉→再オープンで閉中の変更が反映されない (accordion noop ガードの記録欠落)

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_spatial.py:1114`

**利用者から見た症状**

「Spatial Mapping」の見出しを閉じた状態でクラスタの色を変えたり名前を付け替えたりして、あとで見出しを開き直すと、図が変更前のまま表示される。画面右の設定は新しい値なのに図だけが古いという食い違いが起きる。さらにその状態で「一括保存」やレポート出力(.pptx)を押すと、古いままの図がそのままファイルに書き出される。別の設定（サンプル選択やスライダー等）を一度触ると正しい図に入れ替わるため、利用者は「たまに反映されない」としか認識できない。

**検証の経緯(反証の試みと結果)**

反証を4方向から試みたが、すべて失敗した。(1)再オープン時に別の callback が Spatial を描き直す経路は存在しない（interactive_accordion.active_item を Input に持つのは 4 コールバックのみで、spatial_plots_container を書くのは当該 1 本だけ）。(2)assets に accordion を扱う JS は無い（styles.css のみ）。(3)畳んでもコンテンツは DOM に残り再マウントされない（実 Chromium で exists=True を確認）。(4)既存テストは accordion_toggle_is_noop をヘルパー単体で閉状態を渡して呼んでおり、実呼出側が閉状態では絶対にヘルパーへ到達しない事実を検査していない。実関数 update_spatial_plots を import して 4 手順を実行した結果、再オープンが (no_update, no_update) を返すことを確認した。noop 経路は set_export_figures と last_spatial_figure_store の更新より手前で return するため、画面だけでなく一括保存・サムネ・PPTX 元データも古いまま残る。主張の内容・行番号はすべて実ファイルで一致。なお C09-1 は同一根本原因を UMAP 側から見たもので、報告書では 3 呼出箇所（interactive_umap.py:534/543, :655/662, interactive_spatial.py:1114/1120）を持つ 1 件に統合するのが妥当。

**再現手順**

1) プロジェクトを読み込み Spatial Mapping セクションが開いた状態で図が出るのを待つ。2) Spatial Mapping の見出しをクリックして畳む。3) 畳んだままクラスタ名の変更（適用ボタン）またはクラスタ色の変更を行う。4) Spatial Mapping を開き直す→図が古いまま。5) 対照として、開いた状態で「表示行数」等を変えると正しい図になる。自動実行版: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v4_spatial.py（実データ不要、合成 DataFrame を注入して実関数を直接呼ぶ）。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/interactive_spatial.py:1114: if "acc_spatial" not in active_list: / return no_update, no_update  ← guard 呼出より前`
- `/home/user/U_Analysis/App/app/callbacks/interactive_spatial.py:1120: if accordion_toggle_is_noop("acc_spatial", session_id, rds_path, / active_items, ctx.triggered_id): / return no_update, no_update`
- `/home/user/U_Analysis/App/app/callbacks/interactive_callbacks.py:379: _accordion_seen[key] = is_open  ← 記録はヘルパー内のみ＝開状態しか記録されない`
- `/home/user/U_Analysis/App/app/callbacks/interactive_callbacks.py:391: return prev == is_open  ← prev が False になる実経路が無い`
- `実行検証 /home/user/U_Analysis/App で python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v4_spatial.py: 初期表示=DRAW / 畳む=NO_UPDATE / 閉のまま改名=NO_UPDATE / 開き直す=NO_UPDATE / 対照(開状態で別Input)=DRAW`
- `/home/user/U_Analysis/App/app/callbacks/interactive_pptx.py:1724: State("last_spatial_figure_store", "data"),  ← noop 経路では更新されない古い figure を PPTX が読む`
- `/home/user/U_Analysis/App/tests/test_misc_silent_wrongs.py:87: def test_toggle_is_detected_as_a_change(self):  ← ヘルパー単体に閉状態を直接与えるだけで、実呼出側の到達可能性は未検査`

**修正方針**

accordion_toggle_is_noop(...) の呼び出しを「閉なら早期 return」より前に移し、戻り値を変数に受けてから『閉なら return』→『noop なら return』の順に判定する。これだけで閉状態が _accordion_seen に False として記録され、再オープン時に prev(False) != is_open(True) となって再描画される。3 呼出箇所（interactive_umap.py:534/543・:655/662、interactive_spatial.py:1114/1120）を同じ形に揃える。


### 5. [S2] クラスタ情報: リネーム後に統計テーブル行選択で『0 pixels (0.0%)』誤表示 (表示名と raw ID の比較)

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_cluster.py:99`

**利用者から見た症状**

クラスタに好きな名前を付けたあと、「クラスタ統計」の表でその行を選ぶと、右側の説明に「Tumor: 0 pixels (0.0%)」と表示され、サンプルごとの内訳も空になる。同じ行の表には「100 pixels / 33.3%」と正しい数字が出ているので、画面の中で数字が矛盾する。名前を付けていないクラスタでは正しく出るため、「名前を変えたクラスタだけ数が 0 になる」という見え方になる。

**検証の経緯(反証の試みと結果)**

反証仮説「リネームは df['Cluster'] 自体を書き換えるので比較は成立する」を検証したが否定された。cluster_name_map_store を書くのは apply_cluster_rename(:303-335) と load_saved_cluster_name_map(:338-347) の 2 本だけで、いずれも raw ID→表示名の対応表を保存するのみ。plot_data の Cluster 列は raw ID のまま。row_selectable も layouts/interactive_tab.py:788 で 'single' が有効なので通常操作で到達する。実関数 update_cluster_stats → update_cluster_info を合成データで実行し、cluster_name_map={'1':'Tumor'} のときテーブル行 1 の選択が 'Tumor: 0 pixels (0.0%)'（サンプル内訳 0 行）を返すこと、同じクラスタでも umap_highlight_cluster 経由（raw 値）なら 'Tumor: 100 pixels (33.3%)' と正しく出ることを確認した。主張どおり。なお cluster_info_text は html.Pre の表示専用（layouts/interactive_tab.py:749）でエクスポート経路には入らないため、データ破壊ではなく表示の誤り（S2 据え置き）。

**再現手順**

1) プロジェクトを読み込む。2) クラスタ情報セクションでクラスタ 1 に任意の名前（例: Tumor）を入力し「適用」。3) 「クラスタ統計」テーブルで Tumor の行の選択ボタンを押す。4) 右の説明が『Tumor: 0 pixels (0.0%)』になる。自動実行版: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v4_cluster.py（実データ不要）。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/interactive_cluster.py:60: {"Cluster": _cluster_display_name(c, cluster_name_map), "Pixels": int(n), "Percent": f"{n / total * 100:.1f}%"}`
- `/home/user/U_Analysis/App/app/callbacks/interactive_cluster.py:87: cluster_id = table_data[selected_rows[0]].get("Cluster")  ← 表示名が入る`
- `/home/user/U_Analysis/App/app/callbacks/interactive_cluster.py:99: mask = df["Cluster"].astype(str) == str(cluster_id)  ← raw ID と比較＝恒偽`
- `/home/user/U_Analysis/App/app/utils/color_utils.py:115: def cluster_display_name(cl_id, name_map): / :117 if name_map and str(cl_id) in name_map: / :120 return dn`
- `/home/user/U_Analysis/App/app/layouts/interactive_tab.py:788: row_selectable="single",  ← 通常操作で到達`
- `実行検証 python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v4_cluster.py: リネーム後の行1選択→'Tumor: 0 pixels (0.0%)'（内訳0行）、ハイライト経由→'Tumor: 100 pixels (33.3%)'`

**修正方針**

update_cluster_stats の各行に raw ID を別キー（例 '_cluster_id'）で持たせ（表示列 'Cluster' は表示名のまま）、update_cluster_info は table_data[...]['_cluster_id'] を使って df['Cluster'] と比較する。DataTable の columns は変更不要（未宣言キーは表示されない）。


### 6. [S2] relayoutフィルタが発火元でなく宣言順先頭のannotationsを採用し、別グラフの旧ドラッグ座標を誤保存

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/assets/relayout_filter.js:78`

**利用者から見た症状**

統合UMAPのクラスタ名ラベルを動かした後に、Spatial（またはサンプル別UMAP）のラベルを動かすと、動かしたはずのラベルが元の位置に戻ってしまい保存されない。そのうえ、そのサンプルのSpatial図のラベルだけが図の外へ飛んで見えなくなる（UMAP側の座標が誤って書き込まれるため）。この状態は保存ファイルに残るので、次に開き直しても直っていない。統合UMAPを一度パン/ズームすると、その後のドラッグは正常に戻る。

**検証の経緯(反証の試みと結果)**

反証を4方向から試みたが全て失敗し、実ブラウザで汚染そのものを再現した。(1)先順グラフでラベルをドラッグできない可能性→interactive_umap_plot(layouts/interactive_tab.py:971)・_UMAP_PER_SAMPLE_CONFIG(interactive_umap.py:220)・_SPATIAL_IMG_CONFIG(interactive_spatial.py:466)の3種すべてが edits:{annotationPosition:True} を持ち反証不成立。(2)ドラッグ後に図が再構築されて relayoutData が消える可能性→accumulated_label_positions は全経路 State なので図は作り直されず、さらに dcc.Graph の clearState は prependData/extendData 専用で relayoutData に触れない。実測でも figure を丸ごと差し替えた後の spatial ドラッグが依然として古い UMAP 座標を送った(台帳の『パンで解消』より悪い)。(3)サーバ側の二重防御→_accumulate_core(:688) の確認は annotations[ を含むかだけなので古い値も通る。(4)実測: 本物の assets/relayout_filter.js をそのまま使った最小 Dash アプリで、統合UMAPのラベルをドラッグ(111/222)→spatialタイルのラベルをドラッグ(333/444)すると、サーバが受け取ったのは relayout={'annotations[0].x':111,'annotations[0].y':222} / triggered_id={'index':'SampleB','type':'spatial_graph'} だった。ユーザーの実ドラッグは完全に捨てられ、UMAP埋め込み座標がSampleBのspatialセクションへ書かれる。同型の実装が fs_annotation_relayout_signal(interactive_fullscreen.py:788-794) にもある。重大度は台帳どおり S2(ドラッグが保存されない＋別図の座標が誤保存され label_positions JSON に永続化)。

**再現手順**

実データ不要。/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/c05_1/repro_c05_1.py（assets に本物の App/app/assets/relayout_filter.js を配置、port 8078）を起動し、Playwright + 実 Chromium で (1) 1枚目のグラフに Plotly.relayout(gd,{'annotations[0].x':111,'annotations[0].y':222}) を発行、(2) 2枚目(spatial_graph/SampleB)に {'annotations[0].x':333,'annotations[0].y':444} を発行。サーバが受け取るのは 111/222 + spatial_graph の triggered_id。実アプリでは『統合UMAPのラベルをドラッグ → Spatialタイルのラベルをドラッグ』の順で操作すれば再現する。

**根拠**

- `App/app/assets/relayout_filter.js:78: for (var j = 0; j < flat.length; j++) {
79: if (hasAnnotationMove(flat[j])) {
81: relayout: flat[j],`
- `App/app/callbacks/interactive_fullscreen.py:782: [Input("interactive_umap_plot", "relayoutData"),
783: Input({"type": "umap_per_sample_graph", "index": ALL}, "relayoutData"),
784: Input({"type": "spatial_graph", "index": ALL}, "relayoutData")],`
- `App/app/utils/label_persistence.py:311: m = re.match(r"annotations\[(\d+)\]\.([xy])", key)
315: if idx < len(sorted_clusters):
316: cl = str(sorted_clusters[idx])`
- `App/app/callbacks/interactive_fullscreen.py:834: result = _accumulate_core(triggered_id, existing, _get_excl, rd)
835: _auto_save_label_positions(result, rds_path=rds_path, method=method)`
- `App/app/layouts/interactive_tab.py:971: "edits": {"annotationPosition": True},（先順グラフもドラッグ可能であることの根拠）`

**修正方針**

filterAnnotationRelayout の引数走査をやめ、window.dash_clientside.callback_context.triggered[0].value（発火元グラフの relayoutData がそのまま入っていることを実測で確認済み）を使う。すなわち var t = ctx.triggered[0]; if (hasAnnotationMove(t.value)) return {relayout: t.value, triggered_id: triggeredId(), seq: ...}; else return NU; とすれば値と発火元が必ず一致する。同じ関数を使う fs_annotation_relayout_signal も同時に直る。


### 7. [S2] 個体復元後の回転スライダ非同期により、フリップ1回で復元rotationが巻き戻り保存済み対応点がディスク上でも全消去される

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/hne_overlay_callbacks.py:212`

**利用者から見た症状**

H&E の個体を切り替えたあと（またはページを開き直したあと）、回転スライダや左右／上下反転のチェックに一度触れただけで、その個体で苦労して打った位置合わせの点（対応点）が全部消え、位置合わせ前の状態に戻ってしまう。同時に、その個体の回転角が前に見ていた個体の角度に勝手に変わる。しかも消えた状態がそのまま保存ファイルに書き込まれるので、開き直しても元に戻せない。

**検証の経緯(反証の試みと結果)**

実アプリの callback 関数を直接 import して実行し、主張 (a)(b)(c) をすべてそのまま再現した(@callback は dash/_callback.py:582 で元の関数を返すため実物を呼べる)。反証の試み: (1)スライダ value をどこかで復元しているのでは→hne_rotation_angle / hne_rotation_flip を Output に持つ callback は grep で 0 件、persistence 属性も無く反証不成立。(2)タブを開き直せば初期化されるのでは→main_layout.py:352 で H&E タブの children はタブ生成時に一度だけ構築されるので、リロードするまでスライダ値は残り反証不成立。(3)対応点クリアは UI に明記された仕様では→フリップ変更に伴うクリア自体は明記されているが、本件は触っていない angle まで別個体の値へ書き換わり、それがディスクへ黙って保存される点が別問題であり主張は崩れない。実測: 個体A=angle90/対応点3点、個体B=angle0/対応点3点を保存 → A を 90 に合わせてスライダが 90 の状態で B へ切替 → B で『左右反転』を1回チェックしただけで、hne_update_rotation は ({'angle':90.0,'flip_h':True},{'tic':[],'hne':[]}) を返し、hne_autosave 後のディスク hne_overlay_state.json は B が landmarks {'tic':[],'hne':[]} / rotation angle 90.0 になった(個体Aは無傷)。S1 にしない理由: 対応点が消えると affine が未設定に戻り hne_assign_and_summarize(:612) が処理を止めるため誤った集計結果は出ない、hne_tic_figure が rotation_store を Input に持つので回転変化は画面に見える、フリップ変更時のクリア自体は仕様として明示されている。よって S2(ユーザーに見える誤動作＋保存済み状態の破壊)。

**再現手順**

実データ不要。/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/verify_c05_2.py を実行（App を sys.path に入れて hne_restore_sample / hne_update_rotation / hne_autosave を直接呼ぶ）。実アプリでは: H&E タブで個体A を選び回転スライダを 90 にして対応点を3点打つ → 個体B（保存済み angle=0・対応点あり）へ切替 → B で「左右反転」を1回チェック。B の対応点が消え、B の回転が 90 になり、hne_overlay_state.json にその状態が保存される。

**根拠**

- `App/app/callbacks/hne_overlay_callbacks.py:538: # 回転スライダ(value)は出力しない（出力すると hne_update_rotation が発火し復元直後の
539: # 対応点を消す恐れがあるため）。rotation_store を直接復元すれば図・割当は正しい
540: # （スライダ表示のみ前の値が残る軽微な制限）。`
- `App/app/callbacks/hne_overlay_callbacks.py:212: rot = {"angle": float(angle or 0),
213: "flip_h": "flip_h" in flips, "flip_v": "flip_v" in flips}`
- `App/app/callbacks/hne_overlay_callbacks.py:216: if (float(prev.get("angle", 0) or 0) == rot["angle"]
219: return no_update, no_update
221: return rot, {"tic": [], "hne": []}`
- `App/app/callbacks/hne_overlay_callbacks.py:586: def hne_autosave(lm, polys, rotation, sample, rds_path):
588: hp.save_hne_overlay_sample(rds_path, sample, {
589: "landmarks": lm or {"tic": [], "hne": []},`
- `App/app/services/hne_persistence.py:94: entry.update(partial or {})
95: data[str(sample)] = entry
97: _atomic_write_json(path, data)（クリア状態がディスクへ確定する箇所）`

**修正方針**

hne_restore_sample に Output("hne_rotation_angle","value", allow_duplicate=True) と Output("hne_rotation_flip","value", allow_duplicate=True) を追加し、復元した rot からスライダ／チェックの値も同時に戻す。hne_update_rotation の無変化ガード(:216-219)が効くことは実測済み（復元値で書くと必ず (no_update, no_update) を返す）なので、:538-540 のコメントが懸念する副作用は現行実装では発生しない。


### 8. [S2] データ読込のたびに保存済み付加イオン選択が既定値で上書きされ、既定値が再保存される

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_calibration.py:1139`

**利用者から見た症状**

キャリブレーションの「付加イオン」チェックを、例えば +H だけに絞って保存しても、そのプロジェクトを読み込み直すたびに必ず既定の4種（+H / +Na / +NH4 / +K、Negative なら -H）に戻ってしまう。しかも戻った既定値がそのまま設定ファイルに上書き保存されるので、何度やり直しても自分の選択は残らない。

**検証の経緯(反証の試みと結果)**

最大の反証点である『Dash は同じ値の書込では下流を発火させないのではないか』を最初に検証し、否定した。dash-renderer(2.18.2) の executedCallbacks 観測子は callback 応答由来の更新について keys(props)（サーバが返した Output の一覧）だけを見て getCallbacksByInput を呼び、古い値との比較を一切行わない（UI 操作由来の BaseTreeContainer.setProps:4073 だけが equals で差分を取る）。実測でも int_cal_ion_mode に既に入っているのと同じ 'Positive' を書いただけで auto_switch_int_cal_adduct が発火した。他の反証も失敗: int_cal_restore_pending ガードを読むのは INT-CB2 update_int_cal_table_on_matrix(interactive_calibration.py:796-799) だけで、auto_switch_int_cal_adduct(:1139) は State を一つも取らず無条件に既定値を返す。復元成功経路(interactive_callbacks.py:1260)は r_ion_mode / r_adduct を実値で返すので必ず発火する。順序も実測どおり auto_switch → auto_save の順(Dash が依存順に並べる)。実測結果: 保存値 ['+H'] が復元された直後に auto_switch が ['+H','+Na','+NH4','+K'] を書き、auto_save が2波目でその既定値をディスクへ上書きし、画面のチェックも既定4種のままだった。S2 据置: 保存が効かない＝『変更が反映されない』典型で、付加イオンはアノテーション照合の絞り込みにも波及するが、選択状態は画面のチェックボックスに見えているため黙って誤った科学的結果になるわけではない。

**再現手順**

実データ不要。/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/repro_c06_1.py（port 8077、Link D 相当・auto_switch 相当・auto_save 相当の3コールバックを実アプリと同じ結線で再現）を起動し、drive_c06_1.py で load ボタンを押す。保存値 ['+H'] が既定4種で上書きされ DISK も既定になる。実アプリでは: インタラクティブ解析で付加イオンを +H のみにする（auto_save が保存）→ プロジェクトを読み込み直す → チェックが4種に戻り interactive_settings.json も4種になる。

**根拠**

- `App/app/callbacks/interactive_calibration.py:1135: Output("int_cal_adduct_filter", "value", allow_duplicate=True),
1136: Input("int_cal_ion_mode", "value"),
1139: def auto_switch_int_cal_adduct(ion_mode):（State を取らず無条件に既定値を返す）`
- `App/app/callbacks/interactive_callbacks.py:1015: Output("int_cal_ion_mode", "value"),
1017: Output("int_cal_adduct_filter", "value", allow_duplicate=True),`
- `App/app/callbacks/interactive_callbacks.py:1260: r_table, r_enable, r_ion_mode, r_matrix, r_adduct, r_mrm,（復元成功時は実値を返すので必ず発火する）`
- `App/app/callbacks/interactive_calibration.py:796: def update_int_cal_table_on_matrix(matrix_type, ion_mode, is_restoring):
798: if is_restoring:
799: return no_update, False（INT-CB2 だけがガードを持つ非対称）`
- `App/app/callbacks/interactive_calibration.py:1058: Input("int_cal_adduct_filter", "value"),（auto_save_int_cal が既定値を interactive_settings.json へ再保存する経路）`

**修正方針**

auto_switch_int_cal_adduct に State("int_cal_restore_pending","data") を追加し、復元中（True）なら no_update を返す（INT-CB2 と同じガードを入れて非対称を解消する）。手動でイオンモードを切り替えたときの自動切替はそのまま維持される。


### 9. [S2] キャリブレーション適用時、アノテーションファイル欄に指定した CSV が黙って無視される(要検証)

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_calibration.py:742`

**利用者から見た症状**

キャリブレーションの「アノテーションファイル」欄にファイル（同梱の MRM の .xlsx でも、TraceFinder / HMDB の CSV でも）を指定して「キャリブレーション適用」を押すと、成功メッセージは出るのに、そのファイル由来の化合物名がマーカー表や図にまったく反映されない。何も指定していないのと同じ結果になり、エラーも警告も出ないので原因が分からない。

**検証の経緯(反証の試みと結果)**

台帳の『CSV が無視される』は実測で確認できたが、反証を試みる過程で設計意図（この欄は DESI の MRM .xlsx 用）が事実であることも確認できた。そこで意図どおりの入力なら動くのかを実ファイルで検証したところ、同梱 App/DB/DESI/263010-MRM.xlsx も 0 件だった。原因は列名正規化 cl = col.lower().replace(' ','.').replace('_','.')(:230) が / を置換しないこと。'Parent m/z' → 'parent.m/z' となり候補集合 ('parent.m.z','parent.mz','parent','precursor','q1',...) に一致せず、Parent_mz / Daughter_mz が一度も作られないため :251 のループが何も拾わない。実測: 同梱 MRM .xlsx → 0 件、同梱 TraceFinder CSV → 0 件、同 CSV を双子関数 _build_annotation_csv_map に通すと 7412 件、HMDB 形式 CSV → 0 件（双子関数では正常に読める）。さらに App/tests/test_annotation_map_nan.py:52-56 はこの不一致をコメントで明記しながら fixture 列名を Parent_mz に変えて回避しており、正規化そのものは直っていない。したがって台帳は正しいが範囲が狭すぎるため UPHELD のうえ主張を拡張し、条件付き(S3)ではなく既定構成で確実に起きる無音の機能不全として S2 へ引き上げた。誤った化合物名が付くのではなく付かないだけなので S1 ではない。

**再現手順**

実データ不要。python3 で App を sys.path に入れ、from app.callbacks.interactive_calibration import _build_mz_to_compound_map, _build_annotation_csv_map を実行し、_build_mz_to_compound_map('App/DB/DESI/263010-MRM.xlsx') → 0 件、_build_mz_to_compound_map('App/DB/TIMS/4500_endogenous_metabolites_mod.csv') → 0 件、_build_annotation_csv_map(同 CSV, ion_mode='Positive', adduct_patterns=['+H','+Na','+NH4','+K']) → 7412 件 を確認する。実アプリでは: DESI サブプロジェクトを読み込み、キャリブレーション欄にアノテーションファイルを指定して「キャリブレーション適用」を押しても annotation 列が更新されない。

**根拠**

- `App/app/callbacks/interactive_calibration.py:742: mz_to_compound = _build_mz_to_compound_map(mrm_path, tolerance=tolerance)（suffix 分岐なし）`
- `App/app/callbacks/interactive_calibration.py:230: cl = col.lower().replace(" ", ".").replace("_", ".")
234: elif cl in ("parent.m.z", "parent.mz", "parent", "precursor",
235: "q1", "q1.m.z", "precursor.m.z", "precursor.mz"):（'Parent m/z'→'parent.m/z' は不一致）`
- `App/app/callbacks/interactive_calibration.py:1434: if is_excel:
1435: mz_to_compound, n_unreadable = _build_mz_to_compound_map(
1438: mz_to_compound, n_unreadable = _build_annotation_csv_map(（再アノテーション画面だけが出し分けている非対称）`
- `App/app/callbacks/interactive_calibration.py:747: mz_to_compound.update(ann_map)  # ann_map を優先
748: if not mz_to_compound:
749: return deg_data（無音で戻る）`
- `App/tests/test_annotation_map_nan.py:53: 正規化は `lower().replace(" ", ".").replace("_", ".")` なので
54: "Parent m/z" は "parent.m/z" になり **一致しない**（`/` は置換されない）。
55: 最初この形で書いたら地図が空になり、xfail が xpass して気付いた。（既知だが fixture 側で回避されている）`

**修正方針**

(1) _build_mz_to_compound_map(:230) の正規化に .replace("/", ".") を足す（'parent.m/z'→'parent.m.z' となり既存候補に一致し、既存テストの Parent_mz も引き続き一致する）。(2) _reannotate_with_calibration(:742) を execute_reannotation(:1434) と同じ suffix 分岐にし、.xlsx/.xls 以外は _build_annotation_csv_map へ回す。(3) 地図が空だったときは適用ステータスに :1451-1452 と同じ「アノテーションファイルから化合物情報を読み取れませんでした」を必ず添える。


### 10. [S2] Methodsモーダルの再施錠が不完全（閉→再開でパスワード無しに本文表示・DLボタンは有効表示のまま無反応）

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/provenance_callbacks.py:361`

**利用者から見た症状**

Methods 文をパスワードで表示させた後、モーダルを閉じてもう一度「📝 Methods 文を表示」を押すと、パスワードを聞かれずに本文がそのまま出てくる(席を離れた端末では他人にも読める)。しかもその状態では「Methods をダウンロード」ボタンは押せる見た目のままなのに、何度押しても何も起きない。パスワード入力欄も消えているのでやり直すこともできず、ブラウザを再読込するまでダウンロードできない。

**検証の経緯(反証の試みと結果)**

3方向の反証を試みたが全て否定され、さらに主張より悪い事実が出た。(1)『別の callback がパネル style を戻しているのでは』→ 否。稼働アプリの登録済み callback グラフ(dash-dependencies.json, 381件)を全数突合したところ、methods_lock_panel.style / methods_content_panel.style / methods_rendered_store.data / btn_download_methods.disabled / btn_methods_copy.disabled の writer はいずれも unlock_methods(Input=btn_methods_unlock)の1本のみ。開閉 callback は methods_unlock_store と methods_unlock_error しか戻せない(:364-366)。(2)『モーダルを閉じると children が unmount されるので状態はリセットされる』→ 否。実測で閉じた瞬間は DOM から消える(全 id が null)が、Dash の props は redux 側 layout に残るため再オープン時に display:block のまま復元される。(3)『タブ切替やページ遷移で layout が作り直される』→ 否。main.py:371 の app.layout = create_main_layout() は起動時1回の静的レイアウトで、Methods モーダルはその中(interactive_tab.py:1982)。dcc.Location は refresh=False(main_layout.py:183)なのでアプリ内遷移でも再生成されない。実証: provenance_callbacks.py:351-449 と interactive_tab.py:1986-2058 の結線を1:1で写した最小アプリ(差分は verify_master を固定文字列比較にした点と本文をダミーにした点のみ)を実 Chromium で操作した結果、解錠→閉じる→再オープンで lock_panel は display:none(不可視)のまま、content_panel は display:block で本文が丸見え、DL・コピー両ボタンは enabled。その状態で DL を押しても発火せず(観測用 Div が n1 のまま)。さらに追試で判明した悪化事実: 再オープン後はパスワード入力欄と『表示』ボタンが lock_panel(display:none)の中に隠れているため再解錠する手段が画面から消え、ページを再読込(F5)するまで Methods のダウンロードは二度と使えない。docstring『開くたびに解錠状態をリセットする』(:362)との不一致も明白。

**再現手順**

実データ(seurat の .rds)がある環境で: 1) インタラクティブ解析タブの「📝 Methods 文を表示」を押す。2) Master Password を入れて表示。3) 「閉じる」。4) もう一度「📝 Methods 文を表示」→ パスワードを聞かれず本文が出る。5) 「Methods をダウンロード」を押す → 何も起きない。※稼働環境には .rds が1件も無く unlock_methods:422 の rds_path ガードを越えられないため、稼働アプリでの通し再現は未実施。結線1:1複製での再現は実行済み: python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v7_methods_repro_app.py 8098 → v7_probe_methods.py / v7_probe_methods2.py。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/provenance_callbacks.py:362-366: """開くたびに解錠状態をリセットする…""" / if ctx.triggered_id == "btn_methods_close": return False, None, "" / return True, None, ""  ← パネル style も rendered_store も戻さない`
- `/home/user/U_Analysis/App/app/callbacks/provenance_callbacks.py:432-434: return ({"ok": True, …}, "", {"display": "none"}, {"display": "block"}, rendered, False, False, "")  ← 施錠5propの唯一のwriter`
- `/home/user/U_Analysis/App/app/callbacks/provenance_callbacks.py:533-535: if not (unlock or {}).get("ok") or not _is_tier_a(): … raise PreventUpdate  ← ボタンは enabled のまま無反応`
- `/home/user/U_Analysis/App/app/layouts/interactive_tab.py:2012: html.Div(id="methods_content_panel", style={"display": "none"},  ← 初期値。再オープン時には復元されない`
- `稼働アプリの dash-dependencies.json 全数突合: methods_lock_panel.style / methods_content_panel.style / methods_rendered_store.data / btn_download_methods.disabled / btn_methods_copy.disabled の writer は各1本、いずれも Input=btn_methods_unlock`
- `実ブラウザ実測 (scratchpad/tmp/v7_probe_methods2.py, 結線1:1複製): 解錠直後の DL=DOWNLOAD-FIRED-n1 / 再オープン後 password欄 visible=False, 表示ボタン visible=False, DL disabled=False, DL押下後も n1 のまま(無反応) / F5 後に password欄 visible=True`
- `/home/user/U_Analysis/App/app/main.py:371 app.layout = create_main_layout() と /home/user/U_Analysis/App/app/layouts/main_layout.py:183 dcc.Location(id="url_bar", refresh=False)  ← レイアウトは起動時1回、アプリ内遷移では再生成されない`

**修正方針**

toggle_methods_modal の Output に methods_lock_panel.style / methods_content_panel.style / methods_rendered_store.data / btn_download_methods.disabled / btn_methods_copy.disabled を allow_duplicate=True で追加し、開閉どちらでも施錠値 ({"display":"block"}, {"display":"none"}, None, True, True) を返す(prevent_initial_call=True は既に付いている)。methods_rendered_store を None に戻せば switch_methods_format(:437-449)が連鎖して本文 DOM も空になるので本文消去は別途不要。修正版を実ブラウザで検証済み: 再オープンで施錠復帰・本文空・両ボタン disabled、再解錠後は DL が正しく発火。


### 11. [S2] アコーディオン再オープン時に再描画がスキップされ「閉じている間の変更が反映されない」— ver51.9 C-3 修正は挙動不変の空修正

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_umap.py:534`

**利用者から見た症状**

UMAP や Spatial の見出しを畳んだ状態でクラスタ名の変更・色の変更・マージ表示の切替などを行い、そのあと見出しを開き直しても、図が変更前のまま表示される。設定欄は新しい値なのに図だけが古いという食い違いが起き、そのまま「一括保存」やレポート出力(.pptx)を押すと古い図がファイルに出る。別の設定を一度触ると正しい図に入れ替わるため、利用者からは「反映されるときとされないときがある」という不安定な挙動に見える。ver51.9 のリリースノートはこの症状を修正したと書いているが、実際には現行版でも再現する。

**検証の経緯(反証の試みと結果)**

『ver51.9 C-3 は挙動不変の空修正』という中核主張を独立に検証し、成立を確認した。git show fa1e358^ で改名前ソースを取得したところ、早期 return（if "acc_umap" not in active_list: return no_update）は改名前から同じ位置にあり、guard に到達する時点で改名前は is_open≡False、改名後は is_open≡True と、いずれも定数になる。_accordion_seen に入るのはその定数だけなので prev == is_open は prev is not None と同値になり、両版とも『noop = (triggered_id=='interactive_accordion') かつ 2 回目以降』に帰着する（論理的に完全同値）。実行でも、実関数 update_umap_plot をヘルパーの section 引数だけ差し替えて 6 遷移比較し、全一致（True）を確認した。さらに (a) 実関数呼び出しで『初期描画→畳む→閉のまま改名→再オープン』が no_update を返し、開いた状態で別 Input を発火させると初めて Tumor が反映されること、(b) 本物のソースを機械変換して guard を早期 return の前に出した版では同じ 4 手順で再オープン時に ['0','Tumor'] が描画されること、(c) 最小再現アプリ＋実 Chromium で、畳んでもグラフは DOM に残り（exists=True）、再オープン後もタイトルが古いまま（Store は Tumor1、図は '1'）で、サーバログの triggered が ['acc.active_item'] 単独＝SKIP(noop) になることを確認した。反証材料（他 callback による再描画、assets の JS、DOM 再マウント、_accordion_seen のクリア、既存テストによる担保）はいずれも成立しなかった。

**再現手順**

1) プロジェクトを読み込み UMAP セクションに図が出るのを待つ。2) UMAP の見出しをクリックして畳む。3) 畳んだままクラスタ名を変更して「適用」（または色を変更）。4) UMAP を開き直す→図の凡例・ラベルが古いまま。5) 対照として、開いた状態でマーカーサイズを動かすと新しい名前になる。自動実行版: /home/user/U_Analysis/App で python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v4_accordion.py（旧新 ID 比較を含む）と v4_accordion2.py（修正案との対比）、実ブラウザ版は v4_mini_app.py + v4_drive.py。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/interactive_umap.py:534: if "acc_umap" not in active_list: / :535 return no_update  ← guard 呼出(:543)より前`
- `/home/user/U_Analysis/App/app/callbacks/interactive_umap.py:543: if accordion_toggle_is_noop("acc_umap", None, rds_path, / active_items, ctx.triggered_id): / return no_update`
- `git show fa1e358^:App/app/callbacks/interactive_umap.py: 改名前も同位置に『if "acc_umap" not in active_list: return no_update』があり、guard 引数のみ acc_umap_integrated だった`
- `実行検証 v4_accordion.py セクションB: [ver51.9以降 acc_umap] と [ver51.9以前 acc_umap_integrated] の 6 遷移が完全一致 (DRAW, NO_UPDATE×5) → 『6 遷移の結果は完全一致か: True』`
- `実行検証 v4_accordion2.py: [現行] 開き直す→NO_UPDATE / [guard を早期returnの前へ移した版] 開き直す→凡例 ['0','Tumor']`
- `実ブラウザ検証 v4_mini_app.py+v4_drive.py: 畳んだ直後 DOM={'exists': True,'visible': True}、開き直した後のタイトル='1'(Store は Tumor1)、サーバログ triggered=['acc.active_item'] result=SKIP(noop)`
- `/home/user/U_Analysis/App/app/callbacks/interactive_batch_save.py:269: State("interactive_umap_plot", "figure"),  ← 一括保存はブラウザ上の古い figure をそのまま読む`

**修正方針**

3 呼出箇所（interactive_umap.py:534/543、:655/662、interactive_spatial.py:1114/1120）で accordion_toggle_is_noop(...) を先に評価して結果を変数に取り、その後『閉なら return』→『noop なら return』の順にする。閉状態が False として記録されるため、再オープン時に prev(False) != is_open(True) となり再描画される（機械変換版で実証済み）。併せて番人テストに『閉→改名→再オープンで描画される』呼出順のテストを追加する。


### 12. [S2] RDS軽量化の進捗バーが実行中ずっと 0%（進捗 regex に re.MULTILINE 欠落）

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/rds_maintenance_callbacks.py:237`

**利用者から見た症状**

「RDS 軽量化」モーダルで実行を押すと、進捗バーが最後まで 0%「準備中」の帯のまま一切動かない。ログだけは流れていくので、固まったのか進んでいるのか判断できない。処理が終わった瞬間だけ突然 100%「完了」に跳ぶ。対象ファイルが数百件ある場合は、逆に実際より大幅に遅れた数字(例: 全部終わっているのに 201/400 で 50%)が出続ける。

**検証の経緯(反証の試みと結果)**

反証を4方向から試みたが全て崩せず、逆に主張より悪い挙動を実測した。(1)「\s* が改行を食うので2行目以降にも当たる」→ 否。\s* は位置0から始まる連続空白しか食えず、ログ先頭は Python が書く「解析を開始しています...」(analysis_runner.py:1028)→ R の「[slim] Scanning …」(slim_existing_rds.R:120) なので、count 用は Scanning で、progress 用は [slim] の s で必ず失敗する。(2)「ログが200行を超えれば tail 先頭が [i/n] 行になり動く」→ 部分的に当たるが救済にならない。MULTILINE 無しの ^ は finditer でも最大1件しかマッチしないため、current が「tail の先頭行」= 約200ファイル遅れの嘘の数字に固定される(400件処理完了時点で 50% (201/400) を表示)。(3)「既存テストが担保」→ App/tests に rds_maint / poll_rds_slim を参照するテストは0件。(4)「バーが見えていない」→ 否。run_rds_slim:174 が visible_style_bar={"height":"18px","display":"block"} を書き、初期の display:none (rds_maintenance_modal.py:138) から可視に変わる。実測(v7_c11_1_regex.py: 実 get_analysis_log(last_n=200) を経由)で、対象12/60件では全チェックポイントで (0, '準備中')、対象400件では 194件処理時点まで (0,'準備中')、以降は (13,'51/400')→(50,'201/400') と遅延した誤値。姉妹モジュール parquet_maintenance_callbacks.py:230-231 のコメントが本件を「rds_maintenance_callbacks の既存不具合」と名指ししている点も一致。実測 flags は rds=32(MULTILINE 無し)/ parquet=40(有り)。

**再現手順**

1) サイドバーまたはランディングの「🧹 RDS 軽量化」を開く。2) .rds を含むフォルダを指定して実行。3) 実行中ずっとバーが 0%「準備中」のままであることを確認。実データ無しでも再現可能: python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v7_c11_1_regex.py (R の実出力形状のログを作り、実 get_analysis_log と実 regex で poll_rds_slim:308-321 と同一の抽出を行う)。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/rds_maintenance_callbacks.py:237-238: _FILE_COUNT_RE = re.compile(r"^\s*\[slim\]\s+(\d+)\s+files matched") / _FILE_PROGRESS_RE = re.compile(r"^\s*\[(\d+)/(\d+)\]")  ← 実測 flags=32 で re.MULTILINE(8) が立っていない`
- `/home/user/U_Analysis/App/app/callbacks/parquet_maintenance_callbacks.py:230-233: 「re.MULTILINE は必須。これが無いと `^` がログ全体の先頭にしかマッチせず、進捗バーが実行中ずっと 0% のままになる（rds_maintenance_callbacks の既存不具合）。」/ _FILE_COUNT_RE = re.compile(..., re.MULTILINE)  ← 実測 flags=40`
- `/home/user/U_Analysis/App/Script/helpers/slim_existing_rds.R:120: cat(sprintf("[slim] Scanning %s\n", target))  ← ログの先頭行。位置0のマッチは必ず失敗する`
- `/home/user/U_Analysis/App/app/services/analysis_runner.py:1028: log_file.write_text("解析を開始しています...\n", encoding="utf-8")  ← Python 側が先頭に書く行`
- `/home/user/U_Analysis/App/app/callbacks/rds_maintenance_callbacks.py:314-316: for m in _FILE_PROGRESS_RE.finditer(log_text): / current = int(m.group(1)) / total = max(total, int(m.group(2)))`
- `/home/user/U_Analysis/App/app/callbacks/rds_maintenance_callbacks.py:174: visible_style_bar = {"height": "18px", "display": "block"}  ← 実行中はバーが可視になる`
- `実測ログ (scratchpad/tmp/v7_c11_1_regex.py): 対象12件 → 処理1/6/12件のいずれでも (pct,label)=(0,'準備中')、MULTILINE 付きなら (8,'1/12')/(50,'6/12')/(100,'12/12')`

**修正方針**

rds_maintenance_callbacks.py:237-238 の両 re.compile に re.MULTILINE を足す(parquet_maintenance_callbacks.py:232-233 と同じ)。併せて :315 の current = int(m.group(1)) を parquet :318 と同じ current = max(current, int(m.group(1))) にすると、ログの並びに依存しなくなる。挙動変化は進捗表示のみ。


### 13. [S2] C11-3

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正

**利用者から見た症状**

ログインしても、画面右上のヘッダーに出るはずの「解析者: 山田 (A)」という表示が最初から最後まで空欄のまま。誰としてログインしているのかが画面から一切分からない。共有サーバで複数人が使う場合、別人のアカウントのまま作業していても気づけない。ブラウザの開発者コンソールにはページを移動するたびにエラーが出続ける。

**検証の経緯(反証の試みと結果)**

【id 対応】台帳(findings_ledger.json, 75件)に C11-3 は存在しない。全文検索の結果、header_analyst_label_shared を扱う C11 群の項目は C11-0 のみだったので、これを担当として扱った(本エントリは『欠陥そのもの』に対する判定。C11-0 の『実害評価』に対する判定は別エントリで OVERTURNED としている)。反証を試みたが崩せず、逆に実害が実証された。決定実験1(最小再現アプリ v7_orphan_repro_app.py, dash 2.18.2, suppress_callback_exceptions=True, 実 Chromium): auth_callbacks.py:41-56 と同型の clientside callback(Output 3本中1本が layout 不在)を作ると、実在する2本のラベルも初期値のまま更新されない(label_landing='INITIAL-L', label_analysis='INITIAL-A')。全 Output が実在する対照 callback は同じ Input で正常に更新される(sanity='OK:probe_user')ので、他 callback への巻き添えは無い。決定実験2(稼働アプリ 3838 に master password でログイン): サーバは current_analyst={'name':'v7_auditor','tier':'A'} を正しく返しているのに、header_analyst_label_landing / _analysis はいずれも text='' の空欄で、コンソールに ReferenceError(header_analyst_label_shared)が出る。機序は dash-renderer で確定: makeResolvedCallback が callback 本体の実行前に全 Output の layout パスを解決し、1本でも不在なら refErr が ReferenceError を投げるため、callback は起動すらしない。suppress_callback_exceptions は Python 側の検証を止めるだけでブラウザ側のこの検証には効かない(実験も True で実施)。2つの span はどちらもヘッダーの可視領域(landing_page.py:42 はパスワード変更ボタンの隣、main_layout.py:292 はヘルプリンクの隣)にあるので、恒常的にユーザーの目に触れる箇所の機能が沈黙している。

**再現手順**

1) http://127.0.0.1:3838 に master password でログイン(解析者名は任意)。2) ヘッダーの解析者ラベルが空欄であることを確認。3) DevTools コンソールに『A nonexistent object was used in an `Output` … `header_analyst_label_shared`』が出ることを確認。スクリプト: python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v7_probe_live.py / 機序の最小再現: v7_orphan_repro_app.py + v7_probe_repro.py。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/auth_callbacks.py:52-55: Output("header_analyst_label_landing","children"), Output("header_analyst_label_analysis","children"), Output("header_analyst_label_shared","children"), Input("current_analyst","data")  ← 第3 Output だけ layout 不在(grep 全 App でこの id は :54 のみ)`
- `稼働アプリ実測 (scratchpad/tmp/v7_probe_live.py): labels={"landing":{"exists":true,"text":""},"analysis":{"exists":true,"text":""},"shared":{"exists":false}} / サーバ応答={"current_analyst":{"data":{"name":"v7_auditor","tier":"A"}}}  ← データはあるのに表示が空`
- `最小再現実測 (scratchpad/tmp/v7_orphan_repro_app.py, dash 2.18.2): {"label_landing":"INITIAL-L","label_analysis":"INITIAL-A","sanity":"OK:probe_user"}  ← 同一 callback の実在 Output も更新されない/他 callback は無事`
- `/usr/local/lib/python3.11/dist-packages/dash/dash-renderer/build/dash_renderer.dev.js:1011-1013: if (outputErrors.length) { if (nonEmpty(inVals).length) { refErr(outputErrors, paths); } }  ← callback 本体の実行前に全 Output を解決し、不在なら投げる`
- `/usr/local/lib/python3.11/dist-packages/dash/dash-renderer/build/dash_renderer.dev.js:543: throw new ReferenceError(err);`
- `/home/user/U_Analysis/App/app/layouts/landing_page.py:42 と /home/user/U_Analysis/App/app/layouts/main_layout.py:292: html.Span(id="header_analyst_label_landing" / "_analysis", className="text-muted small")  ← どちらもヘッダーの可視領域`

**修正方針**

auth_callbacks.py:54 の Output("header_analyst_label_shared", "children") を1行削除する。これだけで表示が復活することを実ブラウザで確認済み(JS の return が3要素のままでも dash-renderer が zipIfArray で短い方に切り詰めるため無害)。:45/:49 の return を2要素にするのは可読性のための同時修正で必須ではない。再発防止には『登録済み callback の全 Output id が layout に実在すること』を見るテストの追加が有効(現状 App/tests に該当なし)。


### 14. [S2] プリセット読込の adduct_filter が auto_switch_adduct に即座に上書きされる

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要設計判断／**該当**: `App/app/callbacks/file_handlers.py:505`

**利用者から見た症状**

保存しておいたプリセットを読み込むと、「✅ 読み込みました」と表示されるのに、付加イオン(Adduct)フィルターだけは保存した組み合わせにならず、イオンモードに応じた既定の組み合わせ(Positive なら +H/+Na/+NH4/+K、Negative なら -H)に勝手に戻ります。再解析側の Adduct フィルターも同様です。気づかずにそのまま解析を実行すると、意図した付加イオンとは違う条件で m/z 照合が行われます。

**検証の経緯(反証の試みと結果)**

稼働アプリ(127.0.0.1:3838)で実際にプリセットを読み込んで再現した。検証用プリセット V2-Overwrite-Test に adduct_filter=["+H","+Na"] / ion_mode="Negative" / reanalysis_adduct_filter=["+K"] を仕込み、実 Chromium で 📋プリセット → 選択 → 📂読込 を実行したところ、adduct_filter は ["-H"](= auto_switch_adduct("Negative") の戻り値)に、reanalysis_adduct_filter は ["+H","+Na","+NH4","+K"](= reset_reanalysis_defaults → reanalysis_ion_mode="Positive" → auto_switch_reanalysis_adduct の 2 段連鎖)になった。一方で自動切替器を持たない 6 項目(ion_mode / tolerance_mz / p_thresh / logfc_thresh / reanalysis_p_thresh / reanalysis_logfc_thresh)は全て正しく復元されており、『下流に自動切替器が居る項目だけが壊れる』という切り分けが実測で確定した。反証は全滅: (1) 同値書込なら発火しない説 → 最小再現アプリで ion_mode を同じ "Positive" で書いても auto_switch_adduct が発火することをサーバ側呼び出しログで確認、(2) ガード説 → auto_switch_adduct:505-508 に no_update 分岐は無い、(3) 既存テスト説 → 該当テスト 0 件、(4) 意図的仕様説 → CHANGELOG.md:1555-1558 は逆に『プロジェクト読込は ion_mode を書くので auto_switch_adduct が発火する』と連鎖の存在を自認しており、プリセット値が壊れる側は見落とされている。PRESET_KEYS は ion_mode を常に含むので無条件に発生する。画面には「✅ 読み込みました」と成功表示が出る点が症状を悪化させている。

**再現手順**

1) Data/Other/presets/presets.json に adduct_filter=["+H","+Na"], ion_mode="Negative", reanalysis_adduct_filter=["+K"] を持つプリセットを用意(または画面上で Adduct のチェックを一部外して保存)。2) ログイン → 解析設定 → 📋プリセット → 当該プリセットを選択 → 📂読込。3) Adduct フィルターが ["-H"]、再解析 Adduct が ["+H","+Na","+NH4","+K"] になっていることを確認。実施済みスクリプト: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/probe_preset2.py

**根拠**

- `App/app/callbacks/preset_callbacks.py:91,93,112,120-121: Output("ion_mode","value",allow_duplicate=True) / Output("adduct_filter","value",allow_duplicate=True) / def load_preset_cb(...) / values = [params.get(k, no_update) for k in PRESET_KEYS]; return (f"✅ 「{selected}」を読み込みました", *values)`
- `App/app/callbacks/file_handlers.py:505-508: def auto_switch_adduct(ion_mode): / if ion_mode == "Positive": return ["+H","+Na","+NH4","+K"] / return ["-H"]   ← no_update 分岐なし`
- `App/app/callbacks/file_handlers.py:516-519: def auto_switch_reanalysis_adduct(ion_mode): / if ion_mode == "Positive": return ["+H","+Na","+NH4","+K"] / return ["-H"]`
- `App/app/services/preset_manager.py:22-24: PRESET_KEYS = [ / "analysis_method", "analysis_method_tims", / "ion_mode", "tolerance_mz", "adduct_filter",  ← ion_mode を常に書くので連鎖は無条件`
- `稼働アプリ実測 (probe_preset2.py): preset_status="✅ 「V2-Overwrite-Test」を読み込みました" / adduct_filter 保存値["+H","+Na"]→実際["-H"] / reanalysis_adduct_filter 保存値["+K"]→実際["+H","+Na","+NH4","+K"] / 自動切替器の無い 6 項目は全て正しく復元`
- `最小再現アプリ実測 (repro_chain.py): 同値書込 ("Positive"→"Positive") でも auto_switch_adduct が連鎖発火することをサーバ側呼び出し順で確認`

**修正方針**

adduct_filter.value / reanalysis_adduct_filter.value を単一 callback が所有する形に寄せ、Input(ion_mode.value) と Input(プリセット適用シグナル) を並べて dash.ctx.triggered_id で『イオンモード変更なら既定に振り直す / 復元なら保存値を入れる』を分岐させる。暫定策として、load_preset_cb が同一レスポンスで restore_in_progress_store を立て、auto_switch_adduct が State で読んで no_update を返す形でも塞げる(prop 適用は下流発火より先なので State で見える)。


### 15. [S2] reset_reanalysis_defaults が復元直後の再解析パラメータを既定値で上書き

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要設計判断／**該当**: `App/app/callbacks/file_handlers.py:469`

**利用者から見た症状**

サブプロジェクトの「解析」ボタンで前回の設定を開いたときや、プリセットを読み込んだときに、再解析の「イオンモード」と「m/z許容誤差」だけが保存した値にならず、既定値(Positive / 0.01)に戻ります。他の項目(p値・logFC・対象クラスタ・RDSフォルダなど)は正しく戻るため、この 2 つだけ戻っていないことに気づきにくく、そのまま再解析を実行すると保存時と違う条件で走ります。

**検証の経緯(反証の試みと結果)**

稼働アプリで 2 経路とも再現し、さらに A/B 対照実験で因果を確定した。(A) プリセット読込経路: 保存値 reanalysis_ion_mode="Negative" / reanalysis_tolerance_mz=0.0777 が読込直後に "Positive" / 0.01 (= DEFAULT_ION_MODE / DEFAULT_TOLERANCE_MZ) になった。(B) サブプロジェクト「解析」経路: last_analysis_settings に analysis_method="desi_v8" を含む SUB_V2 では同じく Negative/0.0777 → Positive/0.01 に戻ったが、analysis_method / analysis_method_tims を持たない対照サブプロ SUB_V3 では Negative/0.0777 が正しく復元された。つまり『analysis_method(_tims) を同一レスポンスで書いたときだけ壊れる』という機序が A/B で確定した。『条件付きでは』という反証は analysis_callbacks.py:454-456 が解析実行のたびに analysis_method / analysis_method_tims をサブプロ設定へ必ず保存することで潰れる — 復元対象の設定が存在する=一度は解析した、なので analysis_method は常に入っており実運用では無条件に発生する。reset_reanalysis_defaults:469-472 にはガードが一切無い。既存テストも 0 件。

**再現手順**

1) 解析設定で再解析イオンモードを Negative、再解析 m/z許容誤差を 0.0777 にしてサブプロジェクトの解析を実行(または同じ値でプリセットを保存)。2) 一度別画面へ移動。3) プロジェクト一覧 → 開く → 当該サブプロの「解析」ボタン(またはプリセット読込)。4) 再解析イオンモードが Positive、許容誤差が 0.01 に戻っていることを確認。実施済みスクリプト: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/probe_subaction.py (処理群) と probe_subaction_v3.py (対照群)

**根拠**

- `App/app/callbacks/file_handlers.py:469-472: def reset_reanalysis_defaults(desi_val, tims_val): / from app.config import DEFAULT_ION_MODE, DEFAULT_TOLERANCE_MZ / return DEFAULT_ION_MODE, DEFAULT_TOLERANCE_MZ   ← 無条件`
- `App/app/callbacks/project_callbacks.py:685-687: settings.get("analysis_method") or no_update,  # analysis_method / settings.get("analysis_method_tims") or no_update,  # analysis_method_tims  ← 復元値と同一レスポンスで method を書く`
- `App/app/callbacks/analysis_callbacks.py:454-456: save_sub_project_settings(project_id, current_sub_project_id, { / "analysis_method": desi_method, / "analysis_method_tims": tims_method,  ← 実行のたび必ず保存されるので条件は常に成立`
- `稼働アプリ実測(処理群 SUB_V2, analysis_method あり): reanalysis_ion_mode 保存値 Negative → 実際 Positive / reanalysis_tolerance_mz 保存値 0.0777 → 実際 0.01`
- `稼働アプリ実測(対照群 SUB_V3, analysis_method なし): reanalysis_ion_mode → Negative / reanalysis_tolerance_mz → 0.0777 と正しく復元 = 因果の A/B 確定`
- `稼働アプリ実測(プリセット経路): reanalysis_ion_mode 保存値 Negative → Positive / reanalysis_tolerance_mz 保存値 0.0777 → 0.01`

**修正方針**

reset_reanalysis_defaults を『手法が利用者操作で変わったとき』にだけ効かせる必要がある。復元系 (sub_action_new_analysis / load_preset_cb / send_to_reanalysis) が同一レスポンスで restore_in_progress_store を立て、reset_reanalysis_defaults が State で読んで no_update を返す形が最小。恒久策は reanalysis_ion_mode / reanalysis_tolerance_mz の書き手を単一 callback に統合し ctx.triggered_id で分岐させること。


### 16. [S2] auto_switch_data_folder がサブプロジェクト復元/再解析転記直後の data_folder を既定フォルダで上書き

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要設計判断／**該当**: `App/app/callbacks/file_handlers.py:487`

**利用者から見た症状**

プロジェクト一覧からサブプロジェクトの「解析」を開くと、そのサブプロに紐づけて保存してあったデータフォルダではなく、サイドバーの「既定データフォルダ」が入った状態で画面が開きます。出力先やしきい値は正しく戻っているので気づきにくく、そのまま実行すると別の場所のデータを解析してしまいます。インタラクティブ画面の「再解析へ送る」を押したときも、それまで指定していたデータフォルダが既定に戻ります。

**検証の経緯(反証の試みと結果)**

稼働アプリでサブプロジェクト「解析」ボタンを実クリックして再現し、A/B 対照実験で因果を確定した。サブプロ本体の data_folder に /…/scratchpad/tmp/V2_SUB_FOLDER を設定し、last_analysis_settings に analysis_method="desi_v8" を含めた SUB_V2 で「解析」を押すと、data_folder は復元されず …/repo/Data/DESI/Data(= default_desi_data_folder の値)のままだった。analysis_method を持たない対照サブプロ SUB_V3 では data_folder が V3_SUB_FOLDER に正しく復元された。auto_switch_data_folder:487-493 は method が真なら必ず既定を返し、no_update は method が空のときだけなのでガードは無い。実運用では analysis_callbacks.py:454-456 が解析のたびに analysis_method を保存するため条件は常に成立する。プリセット読込でも同じ機序で data_folder が DESI 既定 → TIMS 既定へ切り替わることを実測済み。台帳が併記する btn_send_to_reanalysis の波及も、interactive_reanalysis_bridge.py:47-48,79 が analysis_method/_tims を書くことをコードで確認した(機序は実行確認済みの経路と同一)。既存テストは 0 件、CHANGELOG にも意図的仕様とする記述は無い。

**再現手順**

1) プロジェクトにサブプロを作り、data_folder に既定とは違うフォルダを設定して一度解析を実行(= last_analysis_settings に analysis_method が保存される)。2) プロジェクト一覧 → 開く → 当該サブプロの「解析」ボタンを押す。3) 解析設定タブの「データフォルダ」がサブプロの値ではなくサイドバーの既定 DESI/TIMS フォルダになっていることを確認。実施済みスクリプト: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/probe_subaction.py (処理群) と probe_subaction_v3.py (対照群)

**根拠**

- `App/app/callbacks/file_handlers.py:487-493: def auto_switch_data_folder(desi_val, tims_val, desi_default, tims_default): / active = desi_val or tims_val / if active in ("desi_v8","desi_cluster_filter"): return desi_default or DEFAULT_DESI_DATA_FOLDER   ← method が真なら必ず既定を返す`
- `App/app/callbacks/project_callbacks.py:684-686: data_folder = sub.get("data_folder", "") or settings.get("data_folder", "") / ... / data_folder or no_update,  # data_folder   ← 復元値と analysis_method を同一レスポンスで書く`
- `App/app/callbacks/interactive_reanalysis_bridge.py:47-48: Output("analysis_method", "value", allow_duplicate=True), / Output("analysis_method_tims", "value", allow_duplicate=True),  ← 再解析へ送るでも同じ連鎖が起きる`
- `稼働アプリ実測(処理群 SUB_V2): data_folder 復元値 …/scratchpad/tmp/V2_SUB_FOLDER → 実際 …/repo/Data/DESI/Data(既定)`
- `稼働アプリ実測(対照群 SUB_V3, analysis_method なし): data_folder → …/scratchpad/tmp/V3_SUB_FOLDER と正しく復元 = 因果の A/B 確定`
- `稼働アプリ実測(プリセット経路): data_folder が …/Data/DESI/Data → …/Data/TIMS/Data へ自動切替(同一機序の別経路確認)`

**修正方針**

auto_switch_data_folder を『利用者が手法ラジオを操作したとき』だけ効かせる。復元系 (sub_action_new_analysis / load_preset_cb / send_to_reanalysis) が同一レスポンスで restore_in_progress_store を立て、auto_switch_data_folder が State で読んで no_update を返すのが最小の塞ぎ方。恒久策は data_folder.value の書き手を単一 callback に統合し ctx.triggered_id で分岐させること(現状 5 つの writer が競合している)。


### 17. [S2] キャリブレーションプリセット読込が switch_cal_sample の連鎖で旧テーブルに巻き戻される

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/analysis_callbacks.py:2110`

**利用者から見た症状**

キャリブレーションのプリセットを選ぶと「✓「〇〇」を読み込みました」と成功メッセージは出るのに、その下のリファレンス/実測値の対応表がまったく変わらない（空のままか、直前の内容のまま）。何度選び直しても同じで、プリセットを保存する機能が事実上使えない。さらに悪いことに、読み込んだはずのサンプル別データが画面に表示中の（古い）表で上書きされるので、その状態で「List保存」やプリセットの再保存をすると、保存済みプリセットの中身まで壊れる。気づかずにこのまま校正を有効にして解析すると、意図した参照 m/z とは違う値で質量補正の係数が計算され、化合物の同定結果が変わりうる。

**検証の経緯(反証の試みと結果)**

台帳の主張は全て正しく、しかも台帳が『セレクターが既に __all__ の場合』と条件付きで書いた枝が **実機の既定状態そのもの** である点で、より深刻に読み替えるべき。本体と同じ Output/Input/State 形（allow_duplicate 含む）の最小 Dash アプリを実 Chromium で動かして連鎖を実測した。ケース(a) セレクタが既に "__all__": cb61 が同じ値 "__all__" を書いても **cb60 は発火する**（Dash は同値でも発火する）。その cb60 が受け取る State calibration_table.data は **1 ラウンド古い旧テーブル**（cb61 の直接出力ではなく cb_sync の出力であるため）。一方 cal_per_sample_store は cb61 の直接出力なので新しい。この鮮度の非対称が事故の芯。結果、store["__all__"]=旧テーブル で読み込んだプリセット値が破壊され、続く分岐 `if new_sample in store and store[new_sample]` が旧テーブルを返して cb60 が後勝ちし、DataTable も旧テーブルに戻る。ケース(b) "SampleX"→"__all__": 画面は正しくプリセットになるが per_sample_store["SampleX"] が旧テーブルで汚染される。反証も4方向試みて全て失敗: (1)「Dash は State の出所が未完了なら待つのでは」→ 実ブラウザで cb60 が古い値を受け取った。(2)「同値なら発火しないのでは」→ 発火した。(3)「他の callback が救済するのでは」→ calibration_table_data の書き手 9 本を全数確認したが、プリセット連鎖で走るのは cb61/cb_sync/cb60/sync_selection_to_use のみで、update_calibration_table_on_matrix は cb61 が calibration_matrix を意図的に出力しないため発火せず、sync_selection_to_use は既に巻き戻った表を読むので救済にならない。(4)「プリセット未発見時は」→ 早期 return で no_update なので連鎖せず、**成功時にだけ壊れる**。稼働アプリの /_dash-layout を読むだけで cal_sample_selector.value="__all__" / cal_sample_selector_prev.data="__all__" / calibration_table.data=[] を確認済みで、ページを開いて最初にプリセットを選んだ時点でケース(a) が成立する。本体関数 switch_cal_sample を直接呼んだ結果でも、テーブルが空でも・マトリクスで埋まっていても・旧プリセットが入っていても、いずれもプリセットが返らないことを確認した。

**再現手順**

実行済み。(A) 最小再現: v3_cal_repro.py を INIT_SEL=__all__ PORT=8797 で起動し v3_cal_probe.py 8797 を実 Chromium で実行 → ORDER=[preset, sync(PRESET), switch, sync(OLD), sel2use]、switch の State current_table=["OLD"]、最終 DataTable=["OLD"]、per_sample_store={"__all__":["OLD"]}。INIT_SEL=SampleX PORT=8796 では最終 DataTable=["PRESET"] だが per_sample_store["SampleX"]=["OLD"] に汚染。(B) 本体関数の直接呼び出し: switch_cal_sample("__all__", "__all__", 旧表, プリセット store) → 旧表を返す。current_table=[] なら [] を返し、マトリクス表なら 551.0 を返す（プリセットは 900.0）。(C) 稼働アプリ 127.0.0.1:3838 の /_dash-layout で cal_sample_selector.value と cal_sample_selector_prev.data がともに "__all__" であることを確認。画面操作での再現: 設定タブ → キャリブレーションを有効化 → 任意のプリセットを保存 → 表を別の内容に変える → そのプリセットを選び直す → 表が戻らない。

**根拠**

- `App/app/callbacks/analysis_callbacks.py:2104: Input("cal_sample_selector", "value"), / :2106 State("calibration_table", "data"),  ← DataTable を直読み（Store ではない）`
- `App/app/callbacks/analysis_callbacks.py:2118: if prev_sample and current_table is not None: / :2119 store[prev_sample] = current_table  ← 旧テーブルで読込済み per_sample を上書き`
- `App/app/callbacks/analysis_callbacks.py:2121: if new_sample in store and store[new_sample]: / :2130 new_data = current_table or [] / :2131 return new_data, store, new_sample  ← 旧テーブルを返して後勝ち`
- `App/app/callbacks/analysis_callbacks.py:2161: Output("cal_sample_selector", "value", allow_duplicate=True)], / :2197 "__all__",  ← 同値でも書くので cb60 が発火する`
- `App/app/callbacks/analysis_callbacks.py:1715: Input("calibration_table_data", "data"), / :1717 def sync_calibration_store_to_table(store_data): / :1721 return data, selected  ← DataTable は Store より 1 ラウンド遅れる`
- `App/app/callbacks/analysis_callbacks.py:2168: NOTE: calibration_matrix は出力しない（変更すると / :2169 update_calibration_table_on_matrix が発火しテーブルがリセットされるため）  ← 救済 callback は発火しない`
- `App/app/layouts/settings_tab.py:450: dcc.Store(id="cal_sample_selector_prev", data="__all__"),  / :458 value="__all__",  ← 初回からケース(a) が成立`
- `App/app/callbacks/analysis_callbacks.py:2179: status = f"✓ 「{preset_name}」を読み込みました ({matrix_info} / {ion_info})"  ← 失敗しても成功メッセージだけ残る`

**修正方針**

最小修正は switch_cal_sample の先頭に `if new_sample == prev_sample: return no_update, no_update, no_update` を入れること（ケース(a) が消える）。本筋の修正は State("calibration_table","data") を State("calibration_table_data","data") に変えて Store を単一の真実にすること（手動編集は recalculate_ppm_on_edit が data_timestamp 経由で Store に反映済みなので値は失われない）。


### 18. [S2] 共有リンクが固定した result_dir を set_interactive_folders_from_sub_project が最新結果で上書き (shared ガード欠落)

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_project.py:155`

**利用者から見た症状**

共有リンクを作った後にそのサブプロジェクトを解析し直すと、共有リンクを開いた人には「共有した時点の結果」ではなく「最新の結果」が表示される。画面上は正しい共有を開いたように見え、警告も出ないため、送った側も受け取った側も別の結果を見ていることに気付けない。

**検証の経緯(反証の試みと結果)**

4方向の反証を全て潰し、実ブラウザで連鎖上書きを直接観測した。(1)「Dash は同一応答で書いた値を下流 callback で上書きしない説」= 否定。route_share_url が interactive_sub_project_select.value を書く→それを Input に持つ set_interactive_folders_from_sub_project が連鎖発火し、同じ interactive_result_folder.value を後から書く様子を 2 本の応答本文で観測した。(2)「共有レコードの rds_path が使われるのでフォルダ表示がずれてもデータは固定される説」= 否定。route_share_url は rds_path を一切返さず shared_session にも入れない(share_callbacks.py:193-201)。実際に読むファイルは auto_scan_rds_files(interactive_callbacks.py:574-586)が interactive_result_folder.value を再スキャンして決めるので、上書き後のフォルダから読む。(3)「そもそも固定する意図が無い説」= 否定。共有作成時に result_dir を確定保存し(project_callbacks.py:1359)、その RDS を事前ウォームまでしている(:1373-1377)。さらに populate_interactive_sub_projects は entry_mode∈(sub_project, shared) のとき value を no_update にするガードを持つ(interactive_project.py:112-113)のに、set_interactive_folders_from_sub_project にだけ同種のガードが無いという非対称がある。(4)「既存テストが担保している説」= 否定。App/tests/test_share_routing_scope.py は route_share_url を関数単体で呼んで戻り値を見るだけなので、ver52.3 の修正がアプリ全体では下流に潰されていることを検出できない(単体テスト緑・実機で赤の典型)。実測: 共有作成時 result_dir=/V1/OUT_A が persistent_shares.json に固定 → その後サブプロの結果フォルダを /V1/OUT_A_REANALYZED に変更 → 共有 URL を開くと route_share_url が /V1/OUT_A を書いた直後に set_interactive_folders_from_sub_project が /V1/OUT_A_REANALYZED で上書きし、画面の結果フォルダ欄は後者になった。台帳の S2 を維持。今回は上書き先が実在しないパスなので警告が出て気付けたが、実運用(再解析後の実在する新フォルダ)では警告も出ず閲覧者は黙って別の結果を見る。

**再現手順**

1) master password でログイン、サブプロジェクト SUB_A(出力先 /V1/OUT_A)を用意。2) SUB_A の「共有」→ 無期限共有・パスワード保護 OFF でリンク生成(persistent_shares.json に result_dir=/V1/OUT_A が固定される)。3) SUB_A を再解析するか、編集モーダルで出力先を /V1/OUT_A_REANALYZED に変更する。4) 別のブラウザで共有 URL /view/<token> を開く。5) 結果フォルダ欄が /V1/OUT_A_REANALYZED になっている。スクリプト: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v1_probe2.py

**根拠**

- `App/app/callbacks/share_callbacks.py:193-199: "analysis", / result_dir, / data_folder, / project_id, / sub_project_id, / "shared",            # interactive_entry_mode → auto_load 対象`
- `App/app/callbacks/interactive_project.py:150: Input("interactive_sub_project_select", "value"),`
- `App/app/callbacks/interactive_project.py:178: result_dir = sub.get("last_result_dir") or sub.get("output_dir", "")`
- `App/app/callbacks/interactive_project.py:112-113: if entry_mode in ("sub_project", "shared"): / return options, no_update   ← 対照: populate 側だけがガードを持つ`
- `/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/audit/w3/v1_key_evidence.txt:29: {"multi":true,"response":{..."interactive_result_folder":{"value":"/V1/OUT_A"}...}}   ← route_share_url は固定値を正しく書く`
- `/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/audit/w3/v1_key_evidence.txt:32: {"multi":true,"response":{"interactive_result_folder":{"value":"/V1/OUT_A_REANALYZED"},...}}   ← 直後に上書き`

**修正方針**

set_interactive_folders_from_sub_project(interactive_project.py:143-155)に State("interactive_entry_mode","data") を足し、entry_mode == "shared" のときは interactive_result_folder と interactive_msi_folder の 2 出力だけ no_update にする(残り 5 出力は現状どおり返して ms_instrument や data_info は従来通り設定する)。populate_interactive_sub_projects(:112-113)と同じガードに揃えるだけで、他の経路の挙動は変わらない。


### 19. [S2] 孤児 Output: header_analyst_label_shared (①-B 再確認・原因特定)

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/auth_callbacks.py:54`

**利用者から見た症状**

ログインしてもヘッダーの「解析者: 名前 (tier)」が常に空欄のままで、自分が誰としてログインしているのか画面上で確認できない。ページを移動するたびにブラウザのコンソールにエラーが出続けるため、本当のエラーが埋もれる。

**検証の経緯(反証の試みと結果)**

主張は本体・原因・限定のいずれも実測と一致し、反証できなかった。(a)『実在2ラベルの更新が汚染される』→ 実証。稼働アプリでサーバが current_analyst={'name':'v7_auditor','tier':'A'} を返しているのに header_analyst_label_landing / _analysis は空欄。dash 2.18.2 の最小再現でも実在2ラベルが初期値のまま。(b)『非パターン callback なので ver42.1 型の全 callback 停止は起きない』→ 正しい。全 Output が実在する対照 clientside callback は同じ Input で正常動作した(sanity='OK:probe_user')。影響は当該 callback 1本に閉じる。(c)『suppress_callback_exceptions=True(main.py:68)のため起動時検証では検出されない』→ 正しい。加えて、この設定はブラウザ側の Output 解決検証には効かないことも実験で確認(True のまま ReferenceError が発生)。(d) 原因(ver52.3 f3daa1b で shared_view.py を削除した際の取り残し)も git 履歴と一致。既存テストによる担保も無い(App/tests に Output id と layout を突合するテストは無く、test_callback_wiring.py は @callback の引数個数のみ、clientside_callback は対象外)。唯一の誤りは修正上の注記(下記 corrected_claim_jp)で、これは本体の主張ではないため UPHELD を維持する。

**再現手順**

1) http://127.0.0.1:3838 に master password でログイン。2) ヘッダーの解析者ラベルが空欄であることと、コンソールの ReferenceError(header_analyst_label_shared)を確認。スクリプト: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v7_probe_live.py。修正方針の検証: v7_halffix_app.py 8097 + v7_probe_half.py 8097。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/auth_callbacks.py:52-55: Output("header_analyst_label_landing","children"), Output("header_analyst_label_analysis","children"), Output("header_analyst_label_shared","children")  ← 第3のみ layout 不在`
- `/home/user/U_Analysis/App/app/main.py:67: suppress_callback_exceptions=True,  ← 起動時の静的検証が抑止される(ただしブラウザ側の Output 解決検証には効かない)`
- `稼働アプリ実測 (scratchpad/tmp/v7_probe_live.py): console ReferenceError: A nonexistent object was used in an `Output` … `header_analyst_label_shared` / labels landing="" analysis="" (サーバ応答には name=v7_auditor)`
- `最小再現 (scratchpad/tmp/v7_orphan_repro_app.py): 孤児 Output 有り callback → 2ラベル未更新 / 全 Output 実在の対照 callback → 正常更新 = 巻き添えは無いという限定は正しい`
- `片側修正の検証 (scratchpad/tmp/v7_halffix_app.py: Output 2本・JS は3要素返却): {"L":"解析者: probe_user (master)","A":"解析者: probe_user (master)"} errors:[]  ← 台帳の『片方だけだと壊れる』は誤り`
- `/usr/local/lib/python3.11/dist-packages/dash/dash-renderer/build/dash_renderer.dev.js:616: zipIfArray(outputs, returnValue).forEach(function (_ref12) {  ← 短い方に切り詰めるので余剰戻り値は無害`

**修正方針**

auth_callbacks.py:54 の Output("header_analyst_label_shared", "children") を削除する(これだけで表示が復活することを実ブラウザで確認済み)。:45 と :49 の return 配列を2要素に揃えるのは可読性のための任意の同時修正。再発防止に『登録済み callback の全 Output id が app.layout に実在する』ことを検査するテストを追加すると良い。


### 20. [S2] 死にOutput: header_analyst_label_shared が layout に不在（既知候補①-B を確認）

**判定**: OVERTURNED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/auth_callbacks.py:54`

**利用者から見た症状**

(C11-3 と同一の欠陥)ログインしてもヘッダーの「解析者: 名前 (tier)」が常に空欄で、誰として作業しているのか画面から分からない。C11-0 が言うような『本番では無症状』ではなく、全ユーザーに常時見える形で機能が失われている。

**検証の経緯(反証の試みと結果)**

【重要】欠陥の存在は正しいが、この項目の中心的主張である『影響評価』が実測と正反対で、過小評価の方向に誤っている。よって OVERTURNED とする(白判定ではない。同一欠陥に対する正しい評価は C14-F1 / 本レポートの C11-3 エントリを参照)。誤りは3点。(1)『dash-renderer 2.18 の applyProps は path 不在の出力を per-output で無言 skip するため、landing/analysis の2ラベルは正常に更新され続ける』→ 誤り。applyProps(dash_renderer.dev.js:2047-2053)は callback が値を返した後に通る道で、今回そこに到達しない。makeResolvedCallback が callback 本体の実行前に全 Output のパスを解決し(:1002)、1本でも不在なら refErr が ReferenceError を投げる(:1011-1013 → :543)ため、callback は起動すらしない。読んだコードは実在するが実行経路が違う。(2)『本番では無症状のデッドコード』→ 誤り。稼働アプリ(3838)に master password でログインした実測で、サーバは current_analyst={'name':'v7_auditor','tier':'A'} を返しているのにヘッダーの2ラベルは text='' の空欄。最小再現アプリでも実在2ラベルが初期値のまま更新されないことを実ブラウザで確認。(3)『S3』→ ログイン中の全ページで可視機能が恒久的に沈黙するので S2。なお C11-0 が正しく言い当てている点もある: 生成箇所が main_layout.py:292 と landing_page.py:42 の2つのみであること、ver52.3 の残骸であること、修正が Output 1行削除であること。

**再現手順**

1) http://127.0.0.1:3838 に master password でログイン。2) ヘッダーの解析者ラベルが空欄であることを確認(サーバ応答には名前が入っている)。反証の決め手となった最小再現: python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v7_orphan_repro_app.py 8099 → v7_probe_repro.py 8099。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/auth_callbacks.py:52-55: Output 3本のうち header_analyst_label_shared のみ layout 不在(欠陥の存在自体は C11-0 の記述どおり)`
- `/usr/local/lib/python3.11/dist-packages/dash/dash-renderer/build/dash_renderer.dev.js:1002: unwrapIfNotMulti(paths, …, cb.callback.outputs[i], cb.anyVals, 'Output')  ← callback 実行前に全 Output を解決`
- `/usr/local/lib/python3.11/dist-packages/dash/dash-renderer/build/dash_renderer.dev.js:1011-1013: if (outputErrors.length) { if (nonEmpty(inVals).length) { refErr(outputErrors, paths); } }  ← ここで throw、applyProps には到達しない`
- `/usr/local/lib/python3.11/dist-packages/dash/dash-renderer/build/dash_renderer.dev.js:2047-2053: function applyProps(id, updatedProps) { … var itempath = getPath(paths, id); if (!itempath) { return false; }  ← C11-0 が引いた箇所。callback が値を返した後の経路`
- `最小再現実測 (scratchpad/tmp/v7_orphan_repro_app.py): 実在2ラベルは 'INITIAL-L'/'INITIAL-A' のまま。対照 callback のみ 'OK:probe_user' に更新 → per-output skip 説は成立しない`
- `稼働アプリ実測 (scratchpad/tmp/v7_probe_live.py): サーバ応答 {"current_analyst":{"data":{"name":"v7_auditor","tier":"A"}}} に対しヘッダー2ラベルは text="" → 『本番では無症状』は成立しない`

**修正方針**

修正方針自体は C11-0 の記述どおりで正しい: auth_callbacks.py:54 の Output 1行を削除する(JS の3要素 return はそのままでも動く)。ただし『実害が無いので直さなくてよい』という優先度判断は撤回すべきで、表示機能の復旧として S2 相当で扱う。


### 21. [S3] toggle_create_sub_modal: confirm で無条件クローズ → name 未入力時にサブプロジェクトが黙って作成されない

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/project_callbacks.py:1076`

**利用者から見た症状**

新規サブプロジェクト作成で「タイトル」を入れ忘れて「作成」を押すと、モーダルはすっと閉じるのに一覧にはサブプロジェクトが増えない。「タイトルを入れてください」といった説明も一切出ないので、押し損ねたのか失敗したのか分からない。

**検証の経緯(反証の試みと結果)**

反証を3方向試みたが全て失敗した。(1)「HTML の required でブラウザが弾く説」= action_page.py:249-252 は素の dbc.Input で required 無し、そもそも form でないので何も弾かない。(2)「名前空のとき作成ボタンが disabled になる説」= action_page.py:348-351 に disabled 制御は無く、confirm_create_sub_project.disabled を Output に取る callback も存在しない。(3)「どこかにエラーが出る説」= new_sub_error / create_sub_project_error / sub_project_error のいずれも DOM に存在せず、可視アラートも 0 件(実測)。実操作では、名前を空のまま「作成」を押すと toggle_create_sub_modal が create_sub_project_modal.is_open=false を返して閉じ、handle_create_sub_project は HTTP 204(全 Output が no_update)を返してサブプロジェクト数は 2 のまま変わらなかった。プロジェクト版が ver3.16 で既に直っている(toggle_create_modal:839-843 は confirm を Input に含まず、handle_create_project:890-895 が検証失敗時に is_open=True 維持 + メッセージ、表示先 new_project_error も landing_page.py:250 に実在)のと対照的で、サブプロ版にだけ未適用という台帳の指摘も裏付けられた。重大度は台帳の S3 を維持する: 一覧に新しいカードが増えないので利用者はすぐ失敗に気付け、handle 側が全 no_update を返すためモーダルの入力値自体は保持され、開き直して入力し直せば復旧できる(この点が C01-4 と決定的に違う)。

**再現手順**

1) master password でログイン、プロジェクトを開く。2)「+ 新規サブプロジェクト」を押す。3) タイトルを空のまま、メモやデータフォルダだけ入力する。4)「作成」を押す。5) モーダルは閉じるが、サブプロジェクトは作成されず、エラー表示もトーストも出ない。スクリプト: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v1_probe2.py

**根拠**

- `App/app/callbacks/project_callbacks.py:1066-1068: [Input("open_create_sub_project_modal", "n_clicks"), / Input("cancel_create_sub_project", "n_clicks"), / Input("confirm_create_sub_project", "n_clicks")],`
- `App/app/callbacks/project_callbacks.py:1074-1076: if triggered == "open_create_sub_project_modal": / return True / return False`
- `App/app/callbacks/project_callbacks.py:1113-1114: if not n_clicks or not name or not project: / return (no_update,) * 10`
- `App/app/callbacks/project_callbacks.py:841-842: [Input("open_create_project_modal", "n_clicks"), / Input("cancel_create_project", "n_clicks")],   ← プロジェクト版は confirm を Input に含まない`
- `/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/audit/w3/v1_key_evidence.txt:11: {"name_field_value_at_click": "", "sub_count_before": 2, "sub_count_after": 2, "created": false, "handle_create_callback_status": 204, "modal_visible": false, "error_components_present": [], "visible_alerts": []}`

**修正方針**

プロジェクト版(project_callbacks.py:839-852 / :890-895)をそのまま写す。(1) toggle_create_sub_modal の Input から confirm_create_sub_project を外す。(2) handle_create_sub_project の Output に create_sub_project_modal.is_open とエラー Div を追加し、検証失敗時は is_open=True 維持 +「「タイトル」は必須です。」を返す。(3) action_page.py の作成モーダルに html.Div(id="new_sub_error", className="text-danger") を追加。


### 22. [S3] C03-3

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要設計判断

**利用者から見た症状**

「reduction を再利用して UMAP 以降だけ」を実行するボタン（④）を、UMAP の条件を変えずにもう一度押すと、前回と同じ名前のフォルダに解析結果が書き込まれ、前回の ④ の結果が上書きされて消える。「解析実行」や「reduction のみ作成」なら『既に結果があります。上書きしますか』という確認画面が出るのに、④ だけは何も聞かれず、トーストも『解析を開始しました（出力: ...）』と普通の成功表示になるため、上書きされたことに気づけない。さらに UMAP の 3 つの数値欄（n.neighbors / min.dist / dims）をすべて空にして ④ を押すと、①「reduction のみ作成」で作ったフォルダそのものに書き込まれ、再利用元の中間データごと壊れる。

**検証の経緯(反証の試みと結果)**

(台帳 id は C03-03) 反証を2方向から試みたが失敗した。(1)「ハイパラ自動命名で衝突しないから実害なしでは」→ 否定。_umap_hp_suffix はタイムスタンプも連番も付けない純関数なので、同じハイパラは必ず同じフォルダ名に解決する。④ を同一ハイパラで2回実行する実験で、1回目・2回目とも full_output_dir が .../Analysis_X_nn30_md0p3_dim30 に一致し、2回目も確認なしで解析プロセスが起動した。(2)「①/フル解析と同じゲートに入っているのでは」→ 否定。open_overwrite_modal の仮引数を実測すると ['run_clicks','reduction_clicks','output_dir','output_subfolder'] で ④ は Input に無く、実行本体の L406 も trig を run_analysis/btn_make_reduction に限定している。同じ出力先で run_analysis を押した場合はモーダルが is_open=True で開くことも実測しており、④ だけが例外扱いであることが確定した。台帳の但し書き（全ハイパラ未入力なら _suf が空で output_subfolder そのものに書く）も実測で確認し、その場合 ④ の書き先が ① の reduction RDS を読んでいる元フォルダと完全一致した（読み元と書き先が同じになる）。重大度は S4 から S3 に引き上げた。同一ハイパラでの ④ 再実行（サンプル選択や正規化だけ変えて回す）は普通の操作で、そのとき前回の ④ 結果が無確認で失われる。_output_has_existing_results の docstring 自身が『確認なしで前の解析結果を上書きする』損害の大きさを明記しているのに、④ だけその原則の外にある。

**再現手順**

実行済み。python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v3_harness3.py。D1=同一ハイパラで ④ を 2 回 → 出力先が .../Analysis_X_nn30_md0p3_dim30 で一致し両方とも起動。D2=そのフォルダに既存結果あり(_output_has_existing_results=True)でも ④ は起動。D3=ハイパラ全欄 None → 出力先が ① のフォルダと同一。D4=同条件で open_overwrite_modal(run_analysis) は is_open=True。画面操作での再現: ① で reduction を作る → ④ を押す → UMAP 条件を変えずにもう一度 ④ を押す。

**根拠**

- `App/app/callbacks/analysis_callbacks.py:220: Input("run_analysis", "n_clicks"), / :221 Input("btn_make_reduction", "n_clicks"),  ← ④ は open_overwrite_modal の Input に無い`
- `App/app/callbacks/analysis_callbacks.py:406: elif trig in ("run_analysis", "btn_make_reduction") and output_dir:  ← 実行本体の既存結果チェックも ④ を除外`
- `App/app/callbacks/analysis_callbacks.py:487: _suf = _umap_hp_suffix(umap_n_neighbors_input, umap_min_dist_input, / :490 _base = _strip_hp_suffix(output_subfolder or "umap") / :491 full_output_dir = str(Path(output_dir) / f"{_base}{_suf}")`
- `App/app/callbacks/analysis_callbacks.py:138: def _umap_hp_suffix(nn, md, dims, metric) -> str:  ← タイムスタンプ/連番を含まない純関数(:145-163)`
- `App/app/callbacks/analysis_callbacks.py:183: **確認なしで前の解析結果を上書きする**のとでは、後者の損害が桁違いに大きい。  ← 本ファイル自身の原則`
- `App/app/layouts/settings_tab.py:1033: value=30, min=2, max=100, step=1, size="sm"),  （:1043 value=0.3 / :1064 value=30 / :1053 value="cosine"）← 既定値では _suf は常に非空`


### 23. [S3] 拡大対象が空のとき拡大ボタン押下でモーダルが開かないのに fullscreen_closed_trigger が加算され重い5コールバックが一斉再走

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_fullscreen.py:393`

**利用者から見た症状**

DEG を計算していない状態で DEG の拡大ボタン（⤢）を押す、あるいは Feature Plot をまだ描いていない状態で Feature の拡大ボタンを押すと、拡大画面は開かないのに UMAP と Spatial の全部の図がローディングになって数秒固まる。押した回数だけ毎回固まる。図の中身やズーム位置は変わらないので、結果が壊れることはない。

**検証の経緯(反証の試みと結果)**

反証を3方向から試みたが失敗し、空押しでのトリガ加算と下流発火を実測で再現した。(1)対象が空ならボタンは押せないのでは→expand_deg_btn は deg_results_section 内だが interactive_callbacks.py:1173 が『deg_section_style = {} # 常に表示』としており DEG データが無くても押せる。expand_feature_btn は Feature Plot アコーディオン直下(interactive_tab.py:1240)で常時表示。よって反証不成立。(2)snapshot 比較で必ず抑止されるのでは→fs_label_positions_snapshot の初期値は None(interactive_tab.py:1893)で、:436 の抑止条件が snapshot is not None を要求するため、一度もモーダルを開いていない間と、直前クローズでラベルが変わって :439 が snapshot を None に戻した後は必ず加算される(台帳の条件付き記述どおり)。(3)下流はどうせ no_update では→update_umap_plot はアコーディオンが開いていれば再描画し、interactive_accordion は always_open=True かつ既定 active_item=["acc_umap","acc_spatial"](interactive_tab.py:555,558)なので統合UMAPとSpatial全タイルは既定で再構築対象。accordion_toggle_is_noop も trigger が interactive_accordion でなければ False を返す(:385)。実測(dbc.Modal + 実装どおりの on_fullscreen_close): 3回の空押しで trigger 0→1→2→3 と毎回加算され、毎回下流が走り、モーダルは一度も開かなかった。下流は dash-dependencies.json の deps[81][83][84][99][134] のちょうど 5 本。重大度は台帳どおり S3 に据置: 実害は『開かないのに数秒フリーズする』だけで、uirevision(interactive_umap.py:574 / interactive_spatial.py:1188)によりズームは保持され結果は変わらず、発生も snapshot=None のときに限られる条件付きのため。

**再現手順**

実データ不要。/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/repro_c05_3.py（port 8080）を起動し、ボタンを3回クリックする。モーダルは開かないまま trigger が 1→2→3 と加算され、毎回下流の再描画コールバックが走る。実アプリでは: データを読み込む → 一度も拡大モーダルを開かないまま、DEG マーカーの ⤢ を押す（DEG データ無しのプロジェクト）。

**根拠**

- `App/app/callbacks/interactive_fullscreen.py:393: return False, "", ""（対象が空でも is_open へ書込む唯一のフォールスルー）`
- `App/app/callbacks/interactive_fullscreen.py:436: if snapshot is not None and now is not None and now == snapshot:
439: return (current_val or 0) + 1, None`
- `App/app/callbacks/interactive_callbacks.py:1173: deg_section_style = {}  # 常に表示
1174: deg_no_data_style = {"display": "none"} if deg_data else {}（DEG 無しでも ⤢ は押せる）`
- `App/app/layouts/interactive_tab.py:555: always_open=True,
558: active_item=["acc_umap", "acc_spatial"],（既定で UMAP と Spatial が開いている＝再構築が走る）`
- `App/app/callbacks/interactive_callbacks.py:385: if triggered_id != "interactive_accordion":
386: return False（accordion 以外のトリガでは抑止されない）`

**修正方針**

toggle_fullscreen の各フォールスルー（:87 の `if not trigger:`、:100、:204、:393）を `return False, "", ""` から `raise PreventUpdate`（または 3 出力とも no_update）に変える。モーダルは元々閉じているため表示挙動は変わらず、fullscreen_closed_trigger の空加算だけが止まる。あわせて対象データが無い旨のメッセージを出すと親切（別件・S4）。


### 24. [S3] ブックマーク選択肢: _set_active_key 無しで label_from_active_state を呼び、SCiLS/CSV 由来の化合物名が常に欠落

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_deg.py:1070`

**利用者から見た症状**

Feature の検索欄には「760.58510 (PI 38:4)」のように化合物名が出るのに、その右隣の『ブックマーク』プルダウンだけは同じピークが「760.58510」と数字のまま並ぶ。SCiLS や CSV から化合物名を取り込んだプロジェクト（DEG 表に化合物名が無いデータ）では、ブックマークから目的の化合物を名前で選べない。

**検証の経緯(反証の試みと結果)**

実行確認で主張どおり。reset_active_key()（app/main.py:99-102 の before_request 相当）を呼んだ直後に update_bookmark_options を呼ぶと、annotation_map / feature_annotations を読めず化合物名が付かない。_set_active_key(rds) を先に呼べば付く。annotation_map は読み込み時に必ず構築される（interactive_callbacks.py:1113-1153）ため『材料はあるのに読めていない』状態で、すぐ隣の Feature ドロップダウンは化合物名付きで表示するため同じ m/z が画面上の 2 か所で違う表示になる。反証を 2 つ試みたがいずれも不成立: (a) 既存の番人テスト TestReadersAlsoScopeTheirProject は AST で _interactive_data の直接参照のみを見るため、_label_from_active_state 経由の間接参照はすり抜ける（同テストが現状 pass することも実行確認）、(b) deg_data 由来の annotation は引数で渡されるので付くが、DESI など markers_annotated.csv が無いデータや SCiLS サイドカーのみのプロジェクトでは 1 件も付かない。影響が表示のみでデータ種別に依存するため S3 に据え置く。

**再現手順**

実データ不要。scratchpad/tmp/v6_e2_bookmark.py を実行（プロジェクト state に annotation_map を入れ、reset_active_key() 後に update_bookmark_options を呼ぶ）。実アプリでは SCiLS 由来サイドカーがあるプロジェクトを開き、Feature をブックマークするとプルダウンのラベルに化合物名が付かないことで確認できる。

**根拠**

- `App/app/callbacks/interactive_deg.py:1068: # annotation_map（SCiLS/CSV 由来）も参照して化合物名を付与（deg のみに依存しない）`
- `App/app/callbacks/interactive_deg.py:1070: {"label": _label_from_active_state(f, deg_annotation=ann_map.get(f), style="paren"), "value": f}（関数内に _set_active_key 呼び出しも rds_path State も無い）`
- `App/app/utils/annotation_label.py:132: アクティブキーは呼び出し側が _set_active_key(rds_path) 済みである前提。`
- `App/app/main.py:99: @server.before_request / :102 reset_active_key()`
- `App/tests/test_active_project_scope.py:175: reads_state = ("_interactive_data" in names) or ("_get_state" in calls)（間接参照は検出できない）`
- `実行ログ scratchpad/tmp/v6_e2_bookmark.py: (A) 'm/z 760.5851' → (C) 'm/z 760.5851 (PI 38:4)'`

**修正方針**

update_bookmark_options に State("seurat_rds_path_store","data") を追加し、関数先頭で _set_active_key(rds_path) を呼ぶ。併せて tests/test_active_project_scope.py の走査対象に label_from_active_state 等『アクティブ state を読むヘルパ』の呼び出しを加え、間接参照の抜けを塞ぐ。


### 25. [S3] 選択グループ undo がプロジェクト切替をまたいで残存し、旧プロジェクトの cell_ids を新プロジェクトへ保存しうる

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_selection_groups.py:86`

**利用者から見た症状**

プロジェクト A で選択グループを削除したあと、そのままブラウザで別プロジェクト B を開いて『削除を取り消す』を押すと、B に身に覚えのないグループ（A のピクセル群）が現れ、B のフォルダに保存されて残り続ける。そのグループを『現在の選択に読込』しても 0 px しか選ばれない（ピクセル ID が偶然一致する場合は、まったく別の場所が選ばれる）。

**検証の経緯(反証の試みと結果)**

実コールバックを ctx モックで順に実行し、A で保存→A で削除→B へ切替→B で『削除を取り消す』の手順で、プロジェクト B の selection_groups_state.json に A の cell_ids（A_px1..3）が実際に書き込まれることを確認した。前提条件も個別に検証して反証できなかった: (1) selection_groups_undo は memory Store（interactive_tab.py:1974）、(2) レイアウトは起動時に 1 回だけ構築され（main.py:371）、ページ切替は toggle_pages が display を切り替えるだけで再マウントしないため、プロジェクト一覧に戻って別プロジェクトを開いても Store は生存する、(3) 『削除を取り消す』ボタンは常時描画・常時 enabled（disabled を書く callback は無い）、(4) undo 分岐に由来プロジェクトの検査は無い。実害の主線は『B の sidecar に消えないゴミグループが残り、読み込むと 0 px になる』だが、CellID が <sample_prefix>_Spot_<n>（prefix 既定 "Sample"）なので既定 prefix のまま作ったデータセット同士では衝突しうる。その場合は無関係なピクセルが黙って選択され、選択統計・選択 DE の入力が誤る。手順依存のため S3 を維持する。

**再現手順**

実データ不要。scratchpad/tmp/v6_e3_selgroups.py を実行すると 4 手順を再現し、B の sidecar JSON に A の CellID が書かれることを確認できる。実アプリでは、A でグループ保存→削除→（ページを閉じずに）プロジェクト一覧経由で B を開く→『削除を取り消す』の順で再現する。

**根拠**

- `App/app/callbacks/interactive_selection_groups.py:61: return sg.load_groups(rds_path), no_update, no_update（第3出力=undo を温存）`
- `App/app/callbacks/interactive_selection_groups.py:89: state = sg.add_group(state, undo_state.get("name"), undo_state.get("cell_ids", []), color=undo_state.get("color"))（由来検査なし）`
- `App/app/callbacks/interactive_selection_groups.py:124: sg.save_groups(rds_path, state)（切替後の rds_path へ保存）`
- `App/app/layouts/interactive_tab.py:1974: dcc.Store(id="selection_groups_undo", data=None)`
- `App/app/callbacks/project_callbacks.py:111: hide = {"display": "none"} / :113 "landing": [{}, hide, hide, hide]（ページ切替は style のみ＝再マウントしない）`
- `実行ログ scratchpad/tmp/v6_e3_selgroups.py: B/selection_groups_state.json に {"cell_ids": ["A_px1","A_px2","A_px3"]} が保存された`

**修正方針**

interactive_selection_groups.py:58-61 の seurat_rds_path_store 分岐で undo を破棄する（return sg.load_groups(rds_path), no_update, None）。より確実にするなら undo レコードに rds_path を持たせ、:86 の復元分岐で現在の rds_path と一致しなければ復元せず『別プロジェクトの削除は取り消せません』を返す。


### 26. [S3] 既知候補②-C確定: interactive_umap.py:543 の session_id=None で _accordion_seen キーが全セッション共有になり、別セッションの初回オープンが誤スキップされる

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_umap.py:543`

**利用者から見た症状**

同じプロジェクトを複数の人（別々のブラウザ）が同時に開いているとき、後から見る人が UMAP の見出しを畳んだままプロジェクトを切り替え、そのあと UMAP を初めて開くと、図がまったく表示されない（空白のまま）。ほかの表示設定を何か触ると図が出るため、「開いても出ないことがある」という再現しにくい不具合として現れる。一人だけで使っているときには起きない。

**検証の経緯(反証の試みと結果)**

反証仮説『アコーディオン既定が開（layouts/interactive_tab.py:558 active_item=["acc_umap","acc_spatial"]）なので初回ロードで必ず非 accordion トリガーの描画が入り、共有キーは無害』を検証した。大半の経路ではそのとおりだが、『UMAP を畳んだ状態で rds_path が届く』（畳んでからプロジェクト／サブプロジェクトを切り替える）場合だけは早期 return により自セッションで一度も描画されず記録も残らないため、他セッションが残した True がそのまま効く。実関数 update_umap_plot を用いて A→B の順で呼び出したところ、B の初回オープンが NO_UPDATE（＝空のまま）になり、ヘルパーの session_id だけを :662 と同じ方式に差し替えた対照では同シナリオが描画されることを確認した。したがって主張の実害シナリオは成立する。加えて tests/test_render_payload.py:467 の test_accordion_guard_is_isolated_per_section_and_session は『セッションごとに独立であること』を設計意図として明示的にテストしており、:543 はその意図を単独で破っている。前提条件（複数セッション同時利用＋畳んだ状態でのプロジェクト切替）が重なる必要があるため S3 を維持する。

**再現手順**

2セッション必要。1) ブラウザ A でプロジェクト X を開き、UMAP セクションに図が出るのを確認する。2) ブラウザ B（別ブラウザ／別ユーザー）で UMAP セクションを畳む。3) B でプロジェクト X を読み込む（またはサブプロジェクトを X に切り替える）。4) B で UMAP セクションを開く→図が出ない。自動実行版: /home/user/U_Analysis/App で python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v4_accordion.py のセクション C（共有キー版=NO_UPDATE、session 別キー版=描画 を対比）。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/interactive_umap.py:543: if accordion_toggle_is_noop("acc_umap", None, rds_path,  ← None 固定`
- `/home/user/U_Analysis/App/app/callbacks/interactive_umap.py:662: if accordion_toggle_is_noop("acc_umap", session_id, rds_path,  ← 兄弟は session_id を渡す`
- `/home/user/U_Analysis/App/app/callbacks/interactive_callbacks.py:375: key = (str(section), str(session_id or "__nosession__"), str(rds_path or ""))`
- `/home/user/U_Analysis/App/app/callbacks/edit_lock_callbacks.py:35: clientside_callback( / :36 ClientsideFunction(namespace="session", function_name="get_session_id"), / :37 Output("session_id_store", "data"),  ← Cookie 由来＝別タブは同一 session_id`
- `実行検証 v4_accordion.py セクションC: A が projX 描画後 seen={('acc_umap','__nosession__','/rds/projX.rds'): True} → B は畳んだまま読込=早期return → B の初回オープン=NO_UPDATE(空のまま)。session 別キーの対照では同シナリオが描画 ['0','1']`
- `/home/user/U_Analysis/App/tests/test_render_payload.py:467: def test_accordion_guard_is_isolated_per_section_and_session():  ← セッション独立が設計意図としてテストされている`

**修正方針**

update_umap_plot に State("session_id_store", "data") を追加し、accordion_toggle_is_noop("acc_umap", None, ...) の None を session_id に置き換える（兄弟の interactive_umap.py:662 / interactive_spatial.py:1120 と同じ形にするだけ）。C09-1 の guard 順序修正とは独立に必要。


### 27. [S3] RDS軽量化: 一部ファイル失敗でも緑の『完了しました。』成功表示（parquet 側と非対称）

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/rds_maintenance_callbacks.py:340`

**利用者から見た症状**

RDS 軽量化で一部の .rds が変換に失敗しても、画面には緑色で「完了しました。」と出て、実行結果サマリも緑色になる。よく見ると同じ緑の枠内に「エラー: 2」と書いてあるが、色と見出しは成功を主張しているため、全部うまくいったと誤解しやすい。(失敗したファイル自体は元のまま無傷で残る)

**検証の経緯(反証の試みと結果)**

3方向の反証を試みたが全て否定された。(1)「R が失敗時に非0で終わるのでは」→ slim_existing_rds.R に quit( は0件。per-file 失敗は tryCatch で握って n_error に積むだけで main() は正常終了する。同型骨格を実 R で走らせ Rscript exit code = 0 を確認(v7_exit_demo.R)。stop() は引数不正/フォルダ不在の4箇所のみで per-file 失敗では呼ばれない。(2)「check_process_completion がログを見て error に落とすのでは」→ analysis_runner.py:1269 は終了コード一本槍でログ本文を見ない。(3)「サマリの色で気づける」→ _render_summary:265 も status=='finished' で success 色。決定的な実測: poll_rds_slim を直接呼び、4件中2件 ERROR・「[slim] Errors : 2」を含む実形状ログを食わせると、rds は alert=('success','完了しました。') + サマリ success 色。同じ形状のログを parquet 側 poll_parquet_repack に与えると ('warning','一部のファイルで失敗しました。…')。非対称は確定。ただし重大度は S3 に据え置く: 書き込みは tmp→rename のアトミック置換で失敗ファイルの元データは無傷(データ破壊なし)、かつ緑の枠の中に「エラー: 2」の行自体は表示されるため情報が隠蔽されているわけではない。誤りは色と文言のみ。

**再現手順**

実データでの再現には変換に失敗する .rds が必要。コード経路の再現は実行済み: python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v7_c11_2_alert.py (check_process_completion を finished に固定し、Errors:2 を含む実形状ログで poll_rds_slim / poll_parquet_repack を直接呼び、Alert の色と文言を比較)。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/rds_maintenance_callbacks.py:340-343: if status == "finished": / alert = dbc.Alert("完了しました。" + (…), color="success")  ← Errors 件数を一切見ない`
- `/home/user/U_Analysis/App/app/callbacks/rds_maintenance_callbacks.py:265: color = "success" if status == "finished" else "warning"  ← サマリ側も同じ`
- `/home/user/U_Analysis/App/app/services/analysis_runner.py:1269: status = "finished" if exit_code == 0 else "error"  ← 判定源は終了コードのみ`
- `/home/user/U_Analysis/App/Script/helpers/slim_existing_rds.R: grep -c "quit(" = 0 / :157-166 per-file 失敗は tryCatch で n_error に積むだけ / 実 R で同型骨格を実行 → Rscript exit code = 0`
- `/home/user/U_Analysis/App/app/callbacks/parquet_maintenance_callbacks.py:347-357: if status == "finished" and not has_err: … elif status == "finished" and has_err: dbc.Alert("一部のファイルで失敗しました。…", color="warning")  ← 対照(二重ガード)`
- `実測 (scratchpad/tmp/v7_c11_2_alert.py, 同一の Errors:2 ログ): rds → ('success','完了しました。') / parquet → ('warning','一部のファイルで失敗しました。ログを確認してください。')`

**修正方針**

parquet_maintenance_callbacks.py:255-261 の _summary_has_errors() をそのまま rds 側へ移植し、rds_maintenance_callbacks.py:340 の分岐を「finished and not has_err → success」「finished and has_err → warning『一部のファイルで失敗しました。』」の2段に割る。_render_summary:265 の color も同じ判定に合わせる(parquet :263-264 と同型)。


### 28. [S3] チェック全解除が「全選択」に反転する(annotation/ROIフィルタのNone集約)

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要設計判断／**該当**: `App/app/callbacks/file_handlers.py:280`

**利用者から見た症状**

切片(Annotation)や ROI のチェックを全部外してから解析を実行すると、「1 つも選ばない=何も使わない」つもりなのに、逆に全部の切片/ROI が解析対象になります。画面にも記録にも「フィルタなしで実行しました」としか残らないので、外したはずのデータが混ざったまま結果が出ます。なお 1 サンプル分だけ全部外した場合は逆に解析が『ANNOTATION_FILTER に一致する spot がありません』で失敗するため、挙動が正反対になります。

**検証の経緯(反証の試みと結果)**

当該 callback を __wrapped__ 経由で直接呼び出して実証した: sync_annotation_to_store([[], []]) / sync_desi_roi_to_store([[], []]) / sync_reanalysis_annotation_to_store([[], []]) はいずれも None を返す。下流 analysis_callbacks.py:638 / :630 / :769 の truthy 判定は None を『フィルタ指定なし』と解釈するので params にキーが入らず、R 側は ANNOTATION_FILTER <- NULL / ROI_FILTER <- NULL のまま = 全採用となる(R を実行して kept 2/2 を確認)。反証は 4 方向から試みて全滅した: (1) 実行前バリデーションで止まる分岐は analysis_callbacks / preflight_callbacks に存在しない、(2) 全解除後に UI が自動で全選択へ戻る経路は無い(再描画は selected_samples/data_folder/手法の変化時のみ)、(3) provenance/receipt には None が『フィルタなし』として記録されるだけで気づけない、(4) 既存テストは 0 件。UI 非表示時の None (file_handlers.py:225,228,257) と『部品はあるが全解除』の None (:280) が同値で、下流に区別手段が無い点も台帳どおり。発火条件が『利用者が全部のチェックを外す』という限定操作なので S3 に据え置く。

**再現手順**

実データが必要(annotation 列を持つ TIMS Parquet、または ROI 列を持つ DESI txt)。手順: 解析手法=TIMS UMAP、データフォルダを指定して Annotation チェックボックスを表示 → 全サンプルの全チェックを外す → ▶解析実行 → 生成される runtime スクリプトの ANNOTATION_FILTER が NULL のままであること(=全行採用)を確認。ロジックのみの実証は済み: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/direct_call_c02.py

**根拠**

- `App/app/callbacks/file_handlers.py:274-280: if not all_values: return None ... return sorted(set(merged)) if merged else None`
- `App/app/callbacks/analysis_callbacks.py:638-639: if analysis_type == "tims_v8" and annotation_filter_data: / params["annotation_filter"] = annotation_filter_data`
- `App/app/callbacks/analysis_callbacks.py:630-631: if desi_roi_filter_list: / params["roi_filter"] = list(desi_roi_filter_list)`
- `App/Script/DESI/260619_DESI-UMAP_Template_v15.R:281-283: # - ROI_FILTER = NULL: 全 ROI を使用 / ... / ROI_FILTER <- NULL`
- `直接呼び出し実測: sync_annotation_to_store([[], []]) -> None / 下流式で params = {} (フィルタ無し) / Rscript 実行: ANNOTATION_FILTER=NULL -> kept 2 / 2`

**修正方針**

『部品なし(None)』と『部品はあるが全解除(空リスト)』を型で区別する必要がある。sync_*_to_store を merged が空なら [] を返すよう変え、下流を `if x is not None` 判定に切り替えるのが素直。ただし空リストのときに(a)解析を止めて警告するか(b)全採用のままにするかは製品判断で、1 サンプルだけ全解除した場合の R 側 stop() との非対称も同時に設計する必要がある。


### 29. [S3] L484/L492 が try の外にあるのは事実だが、実害が出るのは **output_dir が空文字のとき** のみ。空文字だと Path("")= ".

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正

> **訂正後の主張**: L484/L492 が try の外にあるのは事実だが、実害が出るのは **output_dir が空文字のとき** のみ。空文字だと Path("")= "." となり、解析結果がアプリの CWD 相対に書かれる。L406 の既存結果ゲートも上書き確認モーダルも `output_dir` の falsy 判定で素通りし、出力先バッジも空欄では何も表示しないため、利用者には一切警告が出ないまま『解析を開始しました』と出る。一方 output_dir=None での未捕捉 TypeError は、(a) テキスト欄をクリアしても "" にしかならない、(b) output_dir.value に書く callback は全て `or no_update` か実在パス、(c) 起動ごとに last_settings.json が削除される、の3点により **UI 操作からは到達しない**。output_subfolder=None も同じ行で例外になるが同様に到達しない。

**利用者から見た症状**

「出力先」の欄をうっかり空にしたまま解析実行を押すと、警告も確認も一切なく『解析を開始しました』と表示され、解析結果はアプリの作業フォルダの中（コンテナ内の一時領域）に作られる。利用者が普段見ているデータ置き場には何も現れず、SFTP で覗いても見えない。コンテナを作り直すとその結果は消える。つまり「解析は成功したのに結果がどこにも見当たらない」という形で現れる。なお、出力先を空にした状態でクラッシュして何も起きない、という現象は起きない（欄を空にしても None ではなく空文字になるため）。

**検証の経緯(反証の試みと結果)**

(台帳 id は C03-02) 主張は2つの枝から成り、片方は実害確定、もう片方は反証できた。【枝1: output_dir=None で未捕捉 TypeError → 到達不能】例外自体は実測で確認した (run_analysis 直接呼び出しで TypeError: expected str, bytes or os.PathLike object, not NoneType が try 外から伝播、対照実験として try 内の例外はトースト『エラー: ...』になることも確認)。しかし None には到達できない: (a) output_dir は type 指定なしのテキスト欄で、実 Chromium で空にしたときブラウザが送る値は "" であって None ではない (ペイロード実測)。(b) output_dir.value に書く callback は4本だけで全て `x or no_update` か実在パスを返す。(c) レイアウト既定は実在パス。(d) main.py:20-23 が起動のたびに last_settings.json を削除するので null が永続化する経路も塞がれている。よって『None でクラッシュして何も起きない』は理論上の枝。【枝2: output_dir="" → CWD 相対書込 → 実害確定】CWD を空ディレクトリにして実行したところ full_output_dir が絶対パスですらない 'C2_EMPTYDIR' になり、CWD 直下に結果フォルダと analysis_params.json が作られ、トーストは『解析を開始しました』と成功表示。L406 の既存結果ゲートも open_overwrite_modal (L234) も `output_dir` が falsy なので素通りし、validate_output_dir_input も空欄ではバッジを返さない (:2329-2330) ため画面に警告も出ない。なお output_subfolder=None でも同じ L484 で TypeError になることを追加確認したが、こちらも UI からは "" しか来ない。以上より重大度は S3（結果が消える場所に落ちるが、利用者が出力先欄を空にするという条件が要る）。

**再現手順**

実行済み。python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v3_harness2.py（空の CWD に chdir して run_analysis を直接呼ぶ）。C1=output_dir None → TypeError 伝播、C2=output_dir "" → CWD 直下に 'C2_EMPTYDIR' が作られトーストは成功、C3=output_subfolder None → TypeError、C4=output_subfolder "" → output_dir 直下、C5=try 内例外 → トースト『エラー: ...』。到達可能性は v3_probe_min.py（実 Chromium）でテキスト欄クリア時の値が "" であることを確認。

**根拠**

- `App/app/callbacks/analysis_callbacks.py:484: full_output_dir = str(Path(output_dir) / output_subfolder)  ← try(L494) より前`
- `App/app/callbacks/analysis_callbacks.py:492: Path(full_output_dir).mkdir(parents=True, exist_ok=True)`
- `App/app/callbacks/analysis_callbacks.py:406: elif trig in ("run_analysis", "btn_make_reduction") and output_dir:  ← 空文字はゲートを素通り`
- `App/app/callbacks/analysis_callbacks.py:234: if not output_dir:  （open_overwrite_modal も空なら開かない） / :236 target = str(Path(output_dir) / (output_subfolder or ""))`
- `App/app/callbacks/analysis_callbacks.py:2329: if not folder or not folder.strip(): / :2330 return ""  ← 空欄では検証バッジすら出ない`
- `App/app/layouts/settings_tab.py:983: dbc.Input(id="output_dir", value=ls.get("output_dir", str(OUTPUT_DATA_DIR))),  ← type 指定なし=text。実ブラウザで空にすると ""`
- `App/app/callbacks/file_handlers.py:599: return desi_folder or no_update, annotation_file or no_update, desi_output or no_update  （:623 / :639 / project_callbacks.py:693 も同型）`
- `App/app/main.py:21: _last_settings = SESSIONS_DIR / "last_settings.json" / :22 if _last_settings.exists(): / :23 _last_settings.unlink(missing_ok=True)  ← null の永続化経路を塞ぐ`

**修正方針**

L484 の直前で `_out = (output_dir or "").strip()` を作り、空なら `return (app_state, True, {"display":"none"}, ..., "出力先を指定してください", True, no_update, no_update)` で中止する。あわせて L484 を `Path(_out) / (output_subfolder or "")` にして open_overwrite_modal:236 とガードを揃える。


### 30. [S3] 色編集ロックが別クラスタ(先頭)に誤取得される (全 picker 書き戻し→triggered_id 取り違え) 要検証

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_spatial.py:1445`

> **訂正後の主張**: 正しい主張: 誤ロックが起きるのは【スウォッチ経路のみ】。update_custom_color_map が全 picker 値を書き戻すと、値が変わっていない picker も含めて全 prop が acquire_cluster_color_lock の ctx.triggered に載る。このとき triggered の先頭は『ユーザーが実際に動かした prop』であり、スウォッチ n_clicks は当該 callback の Input ではないため先頭に現れない。結果、triggered の先頭は Output 並び順の先頭 picker（clusters=sorted(...) の最小クラスタ ID, interactive_spatial.py:577）となり、ctx.triggered_id が『先頭クラスタ』に解決される。すなわち (a) 触ったクラスタのロックは取得されず、(b) 無関係な先頭クラスタのロックが誤取得され、他ユーザー画面でその picker が disabled＋『編集中』表示になる。一方【ピッカー経路は正しい】: ユーザーが変更した picker の prop が triggered の先頭に来るため ctx.triggered_id は対象クラスタに解決され、呼び出しも 1 回に併合されて二重発火しない（元主張の『任意クラスタの色編集のたびに先頭クラスタのロックを誤取得する』は誤り）。

**利用者から見た症状**

複数人で同じプロジェクトを同時に開いているとき、誰かがクラスタの色を『色見本（スウォッチ）』のクリックで変えると、他の人の画面では関係のない先頭クラスタ（通常クラスタ 0）の色ピッカーが薄くなって操作できなくなり、「編集中: ○○」と表示される。本当に編集されているクラスタは保護されないため、同じクラスタの色を二人が同時に変えても警告が出ない。約30秒で自動的に解除される。カラーピッカー（色の四角）を直接使った場合は正しいクラスタが保護されるので、操作方法によって挙動が変わる。

**検証の経緯(反証の試みと結果)**

最小再現 Dash アプリ（本番と同じ結線: Output({type:cluster_color_picker,index:ALL},'value') を自 Input にも持つ callback ＋ その prop を Input とする lock callback）を実 Chromium で操作し、ctx.triggered の順序まで観測した。結果、主張の前提（値が同一でも全 picker prop が triggered に載る＝Dash renderer は値の同一性で間引かない）は確認できたが、主張が想定した経路の割り当てが逆だった。ピッカーで cluster 5 を変更→ triggered=[index5, index0, index1]、triggered_id={'index':'5'}（正しい）。スウォッチ（cluster 1 / cluster 5）クリック→ triggered=[index0, index1, index5]、triggered_id={'index':'0'}（先頭クラスタ＝誤り）。また『スウォッチ経路ではロックが一切取得されない』も不正確で、実際には別クラスタのロックが取得される（実害としてはより悪い）。単独セッションでは自分のロックは reflect_cluster_color_lock で disabled にならないため無症状、EDIT_LOCK_TIMEOUT_SEC=30 秒で自動解放されることから S3 を維持する。

**再現手順**

2セッション必要。1) ブラウザ A・B で同じプロジェクトの Spatial セクションを開く。2) A でクラスタ 5 を選び、色見本（スウォッチ）をクリックする。3) B の画面でクラスタ 0 の色ピッカーが disabled になり『編集中: A』が出る（クラスタ 5 は保護されない）。4) 対照として A でカラーピッカーを直接変更すると、B ではクラスタ 5 が正しく『編集中』になる。機序の最小再現: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v4_mini_app.py を port 3900 で起動し v4_drive.py / v4_drive2.py を実行（実 Chromium）。

**根拠**

- `/home/user/U_Analysis/App/app/callbacks/interactive_spatial.py:786: Output({"type": "cluster_color_picker", "index": ALL}, "value")]  ← 自 Input と同一 prop への書き戻し`
- `/home/user/U_Analysis/App/app/callbacks/interactive_spatial.py:823: return current_store, updated_picker_values  ← スウォッチ経路。全 picker 値を再送出`
- `/home/user/U_Analysis/App/app/callbacks/interactive_spatial.py:1455: triggered = ctx.triggered_id / :1458 target_index = triggered.get("index") / :1461 field_id = f"cluster_color:{target_index}"`
- `/home/user/U_Analysis/App/app/callbacks/interactive_spatial.py:577: clusters = sorted(df["Cluster"].unique(), key=_cluster_sort_key)  ← picker の並び順＝先頭は最小クラスタ ID`
- `実ブラウザ実測(ピッカー経路, v4_drive2.py): acquire_cluster_color_lock triggered=['{"index":"5"}.value','{"index":"0"}.value','{"index":"1"}.value'] / triggered_id={'index':'5'} ← 正しい`
- `実ブラウザ実測(スウォッチ経路, v4_drive2.py): triggered=['{"index":"0"}.value','{"index":"1"}.value','{"index":"5"}.value'] / triggered_id={'index':'0'} ← 先頭クラスタに誤取得`
- `/home/user/U_Analysis/App/app/config.py:239: EDIT_LOCK_TIMEOUT_SEC = int(os.environ.get("EDIT_LOCK_TIMEOUT_SEC", "30"))  ← 誤ロックは30秒で自動解放`

**修正方針**

acquire_cluster_color_lock の Input に Input({"type":"cluster_color_swatch","index":ALL,"color":ALL}, "n_clicks") を追加する。実測どおり『ユーザーが動かした prop が triggered の先頭に来る』ため ctx.triggered_id がスウォッチ dict に解決され、既存の triggered.get("index") がそのまま正しいクラスタを返す（ピッカー経路の挙動は変わらない）。


### 31. [S3] UMAP ポリゴン下書き overlay の Patch data[-1] 前提崩壊(空 figure への Patch / 不可視下書きの確定)

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 要設計判断／**該当**: `App/app/callbacks/interactive_loupe.py:93`

> **訂正後の主張**: 空 figure への Patch data[-1] は実測で無害な no-op（エラーも UI 破壊も無い）。真の欠陥は『umap_polygon_draft_store が、頂点を描いた figure より長生きする』こと。UMAP の表示オプション変更・マージ切替・プロジェクト切替のいずれでも figure は作り直されて下書き線だけが消える一方、store と『下書き N 点』表示は残り、『確定』を押すと見えない範囲で選択が確定する。特にマージ表示へ切り替えた場合は別の埋め込み座標系に適用されるため、意図とまったく異なるピクセル集合が静かに選択される。

**利用者から見た症状**

UMAP をクリックして選択範囲の頂点を置いたあと、マーカーサイズや凡例などの表示設定を変えると、画面上のピンク色の下書き線だけが消える。それでも『下書き 5 点 —「確定」で選択を確定』という文字は残っており、そのまま「確定」を押すと、見えない範囲でピクセルが選択される。マージ表示に切り替えてから確定した場合は、描いた場所とは無関係なピクセルが選ばれる。

**検証の経緯(反証の試みと結果)**

主張は 2 つに分かれ、前半は反証できた。実 Chromium（dash 2.18.2 / dcc 2.0.0 の最小再現アプリ）で測定したところ、trace 0 本の figure に Patch()['data'][-1] を当てても console メッセージ 0・page error 0 の完全な no-op で、その後の再構築も正常だった。つまり『空 figure への Patch でレンダラーエラー/UI 破壊』は起きず、per_sample 中に取消/クリアを押しても副作用は無い。一方、後半は反証できず、むしろ主張より広い: _build_umap_integrated_fig は再構築のたびに空の下書き trace を末尾に足す（interactive_umap.py:209-214）のに対し umap_polygon_draft_store の writer はクリック/取消/クリア/確定だけなので、表示オプション（update_umap_plot の Input 15 個のいずれか）を触っただけで線は消え、umap_polygon_draft_info は『下書き N 点』のまま残り、『確定』は store の頂点で選択を実行する。さらに umap_merge_toggle を merged にすると commit は UMAP_1_merged/UMAP_2_merged で内外判定する（interactive_loupe.py:136-137）ため、別の埋め込み空間の座標として適用され、まったく別のピクセル集合が黙って選択される。この重い方の経路が残るため S3 を維持する。

**再現手順**

要実データ（UMAP 描画にプロジェクト読込が必要）。手順: インタラクティブ解析で統合 UMAP を表示 → 選択ツールを開き UMAP を 3 回以上クリックして下書きを作る → マーカーサイズ等の表示オプションを変更（または『マージ表示』へ切替）→ 下書き線が消えるが『下書き N 点』表示は残ることを確認 → 「確定」を押し、選択統計の px 数/クラスタ構成が描いた範囲と一致しないことを確認。空 figure への Patch が no-op であることは scratchpad/tmp/v6_repro_app.py + v6_probe_repro.py で実データ無しに確認済み。

**根拠**

- `App/app/callbacks/interactive_umap.py:209: fig.add_trace(go.Scattergl( / :210 x=[], y=[], mode="lines+markers", name="_umap_poly_draft",（再構築のたびに空で作り直される）`
- `App/app/callbacks/interactive_loupe.py:93: patched["data"][-1]["x"] = xs / :94 patched["data"][-1]["y"] = ys`
- `App/app/callbacks/interactive_loupe.py:136: if merge_toggle == "merged" and "UMAP_1_merged" in df.columns: / :137 xcol, ycol = "UMAP_1_merged", "UMAP_2_merged"`
- `App/app/layouts/interactive_tab.py:965: html.Div(id="umap_integrated_wrapper", children=[ / :1010 dbc.Button("確定", id="umap_polygon_commit",（ボタンは wrapper の外）`
- `実行ログ scratchpad/tmp/v6_probe_repro.py: 「空 figure に Patch : {'ntraces': 0} / 新規 console msg : [] / 新規 page error : []」「その後 rebuild : ntraces=2」`

**修正方針**

『figure を作り直したら下書きも描き直す』（update_umap_plot が draft store の頂点を最終 trace に入れて返す）か『作り直しの契機で下書きを捨てる』（umap_display_mode / umap_merge_toggle / seurat_rds_path_store の変化で draft store を空にする）かの選択。どちらでも「画面の見た目と確定結果が一致する」ことを満たす。


### 32. [S3] PreFlight ポーリングが process ハンドル消失時に永久スピナー+無限ポーリングになる

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/preflight_callbacks.py:437`

> **訂正後の主張**: poll_preflight は _preflight_process_state['process'] が None のとき status が常に None となり、poll を止める分岐が無いため永久ポーリング＋『実行中です…』表示のままになる。ただし成立条件は (a) 2 人がほぼ同時に実行ボタンを押して共有ハンドルが上書きされる数十 ms の競合、または (b) 診断実行中のサーバ再起動、に限られる。復旧は再実行・📂 ボタンだけでなく、画面リロードやプロジェクト/サブプロジェクトの選び直しでも可能。

**利用者から見た症状**

PreFlight 診断が実際には終わって結果ファイルもできているのに、画面は「PreFlight 診断を実行中です…」のスピナー表示のまま止まり、裏で 1.5 秒ごとの問い合わせが延々と続く。画面を再読み込みするか、プロジェクト/サブプロジェクトを選び直すか、📂 前回の診断を表示を押すと結果が出る。

**検証の経緯(反証の試みと結果)**

機構は実行確認できた。_preflight_process_state['process'] を None にして poll_preflight を n_intervals=1/2/50/1000 で呼ぶと常に (no_update, no_update, no_update) を返し、diagnostics.json が存在しても表示されず poll.disabled=True になる分岐が無い（プロセスが終了済みなら Alert + status=done + disabled=True を返すことも対比確認）。ただし主張の 2 点を訂正する。(a) 発生条件は狭い: 2 番目の実行は run_preflight:312-317 のガードで弾かれ、その戻り値は disabled に no_update を返すので 2 番目のタブはポーリングを始めない。したがって『別タブが先に完了を検知して共有ハンドルを消す』が成立するのは、ガード通過から :384 の代入までの数十 ms に 2 人が同時に押した場合に限られる（PreFlight は job_meta=None のため analysis_runner の台帳ガードにも載らず、この窓を通ると R が 2 本同時に同じ preflight/ へ書く）。サーバ再起動中に実行が跨いだ場合は素直に成立する。(b) 復旧経路は主張より広い: 画面リロードで Interval はレイアウト既定 disabled=True に戻り、プロジェクト/サブプロジェクトの選び直しでも autoload_saved_diagnostics が disabled=True を返して保存済み結果を表示する。『復旧は再実行か 📂 ボタンのみ』は不正確。症状の重さと条件の狭さを合わせて S3 を維持する。

**再現手順**

機構の再現は実データ不要: scratchpad/tmp/v6_e4_preflight.py を実行（process=None + store.status='running' で poll_preflight を反復呼び出し）。実運用での再現は (b) が容易 — 診断を開始し、完了前にアプリを再起動してから同じブラウザタブを放置すると、スピナーのまま終端しない。

**根拠**

- `App/app/callbacks/preflight_callbacks.py:437: if status is None: / :439 return no_update, no_update, no_update`
- `App/app/callbacks/preflight_callbacks.py:442: _preflight_process_state["process"] = None（完了を検知したセッションが共有ハンドルを消す）`
- `App/app/callbacks/preflight_callbacks.py:312: proc = _preflight_process_state.get("process") / :313 if proc is not None and proc.poll() is None: → :316 no_update, no_update（2 番目のタブは poll を有効化しない）`
- `App/app/layouts/settings_tab.py:1147: dcc.Interval(id="preflight_poll", interval=1500, disabled=True),（max_intervals 無し）`
- `App/run_app.py:143: logger.info("Serving with waitress (threads=%d, workers=1)"（1 プロセス＝モジュールグローバルは全タブ共有）`
- `実行ログ scratchpad/tmp/v6_e4_preflight.py: n_intervals=1/2/50/1000 いずれも poll.disabled=no_update、diagnostics.json は存在=True`

**修正方針**

poll_preflight に『store['status'] == "running" かつ proc is None』の分岐を追加し、diagnostics.json があれば表示して disabled=True、無ければ『実行状態を追跡できなくなりました。📂 で結果を確認するか再実行してください』を出して disabled=True を返す。恒久対策として実行ごとの run_id を store と _preflight_process_state の双方に持たせ、他人の実行完了を自分の完了と誤認しない形にする（こちらは設計判断）。


### 33. [S3] annotation選択UIがTIMS追加データフォルダを無視する

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/file_handlers.py:235`

> **訂正後の主張**: update_annotation_selector が追加フォルダを見ない点(UI 欠落)は確定。ただし『R 側でそのデータが黙って除外される』は誤り。R を実行して確認したところ、追加フォルダ側サンプルの annotation ラベルが filter と 1 つも重ならない場合は stop() が発生し、呼び出し側 (ver6 本体:2385) に tryCatch が無いため解析全体が失敗する(騒がしく落ちる)。silent なデータ欠落が起きるのはラベルが部分的にしか重ならない場合に限られ、そのときだけ該当 spot がログ 1 行だけ残して落ちる。

**利用者から見た症状**

TIMS 解析で「追加データフォルダ」を足すと、そのフォルダにしか無いサンプルは切片(Annotation)選択の一覧に出てきません。その状態で主フォルダ側のチェックを外して解析を実行すると、追加フォルダのデータは切片指定の対象外なので、(1) ラベルがまったく違う場合は解析が『ANNOTATION_FILTER に一致する spot がありません』というエラーで途中失敗する、(2) ラベルが一部だけ共通の場合は、選びようがない切片のデータが警告なく解析から抜け落ちる、のどちらかになります。

**検証の経緯(反証の試みと結果)**

UI 欠落の機序は反証できなかった。find_tims_file_path は単一フォルダしか走査せず(data_manager.py:331-333)、内部で追加フォルダを補完する経路も無い。追加フォルダ機能は add_extra_folder / render_extra_folders / remove_extra_folder で完全に結線されており死にコードでもない。一方、台帳自身が留保していた『R 側の filter 適用方式』を Rscript で実行検証した結果、完全不一致=解析全体エラー、部分一致=静かな行落ち、と判明したため『黙って除外される』という主張は成立しない。実害には (a) 追加フォルダ機能の使用 (b) 追加フォルダ側 Parquet に annotation 列が有効に存在 (c) 主フォルダ側でチェックを外して filter を非空化、の 3 条件が同時に必要なので S3 (条件付き・潜在) に据え置く。

**再現手順**

実データが必要(annotation 列を持つ TIMS Parquet が 2 フォルダ分)。手順: 解析設定で解析手法=TIMS UMAP にし、データフォルダに A、「TIMS 追加データフォルダ」に B を指定 → サンプル一覧には A と B のサンプルが並ぶが、Annotation 選択には A のサンプルしか出ないことを確認。次に A のチェックを 1 つ外して解析実行し、B のラベル構成に応じて上記 (1)(2) が起きることを確認する。R 側セマンティクスのみは実データ無しで確認済み: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/annfilter.R

**根拠**

- `App/app/callbacks/file_handlers.py:178-179: all_folders = [data_folder] + (extra_folders or []) / samples = list_tims_files_multi(all_folders)`
- `App/app/callbacks/file_handlers.py:235-237: file_path = find_tims_file_path(data_folder, sample) / if not file_path: / continue`
- `App/app/services/data_manager.py:331-333: folder = Path(data_folder) / if not folder.is_dir(): / return None  ← 単一フォルダのみ走査`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:930-932: mask <- coordinates$annotation %in% ANNOTATION_FILTER / if (sum(mask) == 0) { / stop(sprintf("ANNOTATION_FILTER に一致する spot がありません: %s",`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2385-2386: for (fp in input_paths) { / dat <- read_desi_data_cached(fp, sn)  ← tryCatch 無し = stop() が解析全体を落とす`
- `scratchpad/tmp/annfilter.R 実行結果: 完全不一致→ERROR: ANNOTATION_FILTER に一致する spot がありません / 部分一致→kept 1 / 3`

**修正方針**

update_annotation_selector に Input("extra_data_folders_store","data") を足し、find_tims_file_path(data_folder, sample) を [data_folder]+extra_folders を順に探す複数フォルダ版(list_tims_files_multi と同じ方針の find_tims_file_path_multi)に置き換える。update_sample_selector:178-179 と同じ形にそろえるだけで挙動は壊れない。


### 34. [S4] C03-4

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要設計判断

**利用者から見た症状**

データフォルダや出力先の指定が間違っている状態で「解析実行」を押すと、画面上部に赤い枠で「入力チェックでエラーが見つかりました: データフォルダ: フォルダが見つかりません」と出るのに、同時に「解析を開始しました」という緑の通知も出て、進捗バーが動き出す。利用者はエラーなのか動いているのか判断できず、しばらく待たされた末に R 側のエラーログで失敗を知ることになる。また、①「reduction のみ作成」や ④「reduction を再利用」を押したときはこの入力チェック自体が走らないので、同じ間違いをしていても赤い一覧は出ない（ボタンによって親切さが違う）。

**検証の経緯(反証の試みと結果)**

(台帳 id は C03-04) 主張どおりであることを実行確認した。存在しないデータフォルダ・存在しない出力先で preflight_validation を直接呼ぶと style={'display':'block'} で『データフォルダ: フォルダが見つかりません』『出力先: 親フォルダが見つかりません』の 2 件が返る。同じ入力で run_analysis を呼ぶと ok=True で解析プロセスが起動し、トーストは『解析を開始しました』になる。つまり赤いエラー一覧と成功トーストが同じクリックで同時に出る。反証も試みた: (a)「run_analysis が検証結果を State で読んでいるのでは」→ State 一覧 (L280-343) に validation_summary は無く、読んでいない。(b)「① ④ でも走るのでは」→ Input は run_analysis.n_clicks の 1 本のみ (:2350)。走らない。(c)「確認実行で走らないのが問題では」→ 走らないのは事実だが、モーダルを開いた最初のクリックで既に走っており、モーダル表示中は背後の入力を触れないため実害は薄い。追加で見つけた不整合として、preflight が『出力先: 親フォルダが見つかりません』と言った直後に実行本体の mkdir(parents=True) がその親を作ってしまう（検証と実行が別の現実を見ている）。ただし誤った科学的結果は出ず、R 側で失敗すればログに出るので重大度は S4 のまま。修正は『advisory のままにする（文言を直し ① ④ でも走らせる）』か『実行を止める』かの設計選択が要るので design-judgment。

**再現手順**

実行済み。python3 /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v3_harness3.py の D5。preflight_validation(1, None, 'tims_v8', '/nonexistent/data', None, '/nonexistent/out', 0.05, 0.25, 0.01, False, '', '') → style={'display':'block'}、項目は「データフォルダ: フォルダが見つかりません」「出力先: 親フォルダが見つかりません」。同じ入力で run_analysis → 解析プロセス起動 True、トースト『解析を開始しました』。画面操作での再現: データフォルダに存在しないパスを入れて解析実行を押す。

**根拠**

- `App/app/callbacks/analysis_callbacks.py:2350: Input("run_analysis", "n_clicks"),  ← 起動ボタン 4 つ中 1 つのみ`
- `App/app/callbacks/analysis_callbacks.py:2422: if not errors: / :2423 return "", {"display": "none"}  ← 表示以外の副作用が無い`
- `App/app/callbacks/analysis_callbacks.py:393: if (not n_clicks and not reduction_clicks and not downstream_clicks / :394 and not confirm_overwrite_clicks):  ← クリック数のみガード、検証結果を読む State は無い`
- `App/app/callbacks/analysis_callbacks.py:492: Path(full_output_dir).mkdir(parents=True, exist_ok=True)  ← preflight の「親フォルダが見つかりません」を実行本体が自分で解消する`
- `App/app/callbacks/analysis_callbacks.py:2428: html.Strong("入力チェックでエラーが見つかりました:"),`
- `App/app/callbacks/analysis_callbacks.py:956: ("解析を開始しました（出力: " + Path(full_output_dir).name + "）"  ← 同じクリックで出る成功トースト`


### 35. [S4] プロジェクト切替時の _drop_state()/_set_active_key(None) が恒等 no-op（宣言された state 破棄が実行されない）

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_project.py:134`

**利用者から見た症状**

利用者の画面には何も現れない。プロジェクトを切り替えても古い解析データがサーバのメモリに残り続けるだけで、表示や解析結果が間違うことはない(残ったデータは RDS ファイルのパスごとに分けて持っているため、別プロジェクトのデータとして読まれることはない)。最大 8 件・30 分で自動的に片付く。

**検証の経緯(反証の試みと結果)**

主張の技術的中身(引数なし _drop_state() が恒等 no-op で、コメントが謳う state 破棄は一度も実行されない)は実行して確認した通り完全に正しいので OVERTURNED にはしない。実 Flask の before_request 経路まで再現して検証したところ、コールバック本体に入った時点で _active_key_var は必ず None であり(active_key_seen=None)、_drop_state() 前後で _project_states のキー集合が完全に一致した(before == after)。したがって interactive_project.py:133-135 と :168-170 の 2 箇所は完全な死にコードである。ただし台帳が『要検証』としていた「ロードを経ずに旧 state を読む経路」を調べた結果、実害はメモリ滞留のみと確定したので重大度を S3 → S4 に引き下げる: (a) state のキーは RDS ファイルパスで、読み書きは全て _set_active_key(rds_path) → _get_state(rds_path) の対で行われる(interactive_callbacks.py:807/830/895/976/1072、interactive_umap.py:546/614/665/774、interactive_feature_lists.py:135/215 ほか)ため、プロジェクトが違えばキーが違い混線しない。(b) _set_active_key を呼び忘れた callback が読む _DEFAULT_KEY("__default__")は、_drop_state() が正しく動いたとしても破棄対象(=アクティブキー=rds_path)にならないので、死にコードであることは __default__ 経路の危険度を一切変えていない。(c) 残るのは古い state が LRU(既定 8 件)/TTL(既定 30 分)まで残ることだけ(interactive_callbacks.py:96-97)。利用者に見える誤動作は生じない。とはいえ『コードが持っていない安全性をコメントが主張している』型の欠陥そのものなので、放置ではなく整理を推奨する。

**再現手順**

/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/v1_c07f2_dropstate.py を実行する(App ディレクトリで FLASK_SECRET_KEY=x を付けて python3 実行)。実 Flask の before_request で reset_active_key() を呼んだ後にコールバック本体と同じ 2 行を実行すると、_project_states のキー集合が before/after で完全に一致する(= 何も破棄されていない)。

**根拠**

- `App/app/callbacks/interactive_project.py:133-135: # アクティブプロジェクトの state エントリを破棄（プロジェクト別キャッシュ対応） / _drop_state() / _set_active_key(None)`
- `App/app/callbacks/interactive_project.py:168-170: # 前のプロジェクトの state を破棄（複数プロジェクト同時閲覧時の混線防止） / _drop_state() / _set_active_key(None)`
- `App/app/main.py:99-102: @server.before_request / def _reset_active_project_key(): / from app.callbacks.interactive_callbacks import reset_active_key / reset_active_key()`
- `App/app/callbacks/interactive_callbacks.py:227-229: key = project_key or _active_key_var.get() / if not key: / return`
- `/tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/audit/w3/v1_c07f2_output.txt:9: callback body result: {'active_key_seen': None, 'after': ['KEY_A', 'PROJ_OLD', 'PROJ_NEW'], 'before': ['KEY_A', 'PROJ_OLD', 'PROJ_NEW']}`
- `App/app/callbacks/interactive_callbacks.py:96-97: _MAX_PROJECT_STATES = int(os.environ.get("MAX_PROJECT_STATES", 8)) / _PROJECT_STATE_TTL_SEC = int(os.environ.get("PROJECT_STATE_TTL_SEC", 30 * 60))`

**修正方針**

seurat_rds_path_store を State に取って _drop_state(rds_path) と明示キーで呼ぶか、実行されない 2 行と『state エントリを破棄』『前のプロジェクトの state を破棄(混線防止)』のコメントを削除して実態(キーが rds_path 別なので破棄不要)を書く。どちらも挙動は変わらない。


### 36. [S4] デフォルト適用ボタンが空欄を反映しない(`x or no_update` が空文字を飲む)

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要設計判断／**該当**: `App/app/callbacks/file_handlers.py:599`

**利用者から見た症状**

サイドバーの「DESI/TIMS 初期設定」「出力先の既定」の欄を空にした状態で「適用」を押すと、解析設定側の欄が何も変わりません。成功メッセージもエラーも出ないため、ボタンが壊れているように見えます(欄に値が入っているときは正常に反映されます)。

**検証の経緯(反証の試みと結果)**

apply_desi_defaults / apply_tims_defaults / apply_output_defaults を直接呼び出して実測した: 全欄空文字 → (no_update, no_update, no_update)、1 欄だけ空 → その欄だけ no_update。主張は正しい。ただし反証寄りの事実が 2 つある: (1) 空の既定値で解析設定欄を無条件に上書きすると『サイドバーを空にしたまま適用 → 解析欄が全消し』というより悪い挙動になるため、`or no_update` は防御としては妥当で、修正は挙動変更を伴う。(2) main.py が起動時に last_settings.json を消すので空欄状態は永続しない。よって S4 のまま、修正区分は design-judgment。

**再現手順**

アプリにログイン → 解析設定サイドバーの「⚙ DESI初期設定」を開く → 「DESIデータフォルダ」欄を空にする → 「適用」を押す → 解析設定タブの data_folder が変わらず、画面に何のメッセージも出ないことを確認。ロジックの実証は済み: /tmp/claude-0/-home-user-U-Analysis/36bca7d3-f22b-5213-86d8-66891f484e7a/scratchpad/tmp/direct_call_c02.py

**根拠**

- `App/app/callbacks/file_handlers.py:599: return desi_folder or no_update, annotation_file or no_update, desi_output or no_update`
- `App/app/callbacks/file_handlers.py:623: return tims_folder or no_update, annotation_csv or no_update, tims_output or no_update`
- `App/app/callbacks/file_handlers.py:639: return output_dir or no_update`
- `App/app/main.py:21-23: _last_settings = SESSIONS_DIR / "last_settings.json" / if _last_settings.exists(): / _last_settings.unlink(missing_ok=True)`
- `直接呼び出し実測: apply_desi_defaults(1, "", "", "") -> ('no_update','no_update','no_update') / apply_desi_defaults(1,'/data/desi','','/out') -> ('/data/desi','no_update','/out')`

**修正方針**

『空欄で潰さない』方針自体は維持したうえで、この callback に status 出力(例: 適用しました / 空欄のため適用しませんでした)を 1 つ足して無反応をなくすのが最小の改善。欄を空へリセットしたい要求まで満たすなら、明示的な「クリア」操作を別に用意する(無条件返却への変更は挙動変更になるので不可)。


### 37. [S4] 共発現散布図: 発現行列と plot_data の行順照合が無い（ver52.5 の番人が feature plot のみ）

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正／**該当**: `App/app/callbacks/interactive_feature_lists.py:235`

> **訂正後の主張**: run_coexpression は発現行列を plot_data へ位置で対応付けており、検査は行数のみで ver52.5 の行順照合が適用されていない（防御の一貫性欠如）。ただし現行コードには行順が食い違う expression_matrix.parquet を生じる経路が無く、実際に誤った共発現図が出た証拠も無い。将来の抽出仕様変更やキャッシュ操作に対する保険として番人を揃えるべき、という位置づけ。

**利用者から見た症状**

現状は利用者に見える症状は無い（共発現散布図は正しく描かれる）。将来なんらかの理由で発現行列とピクセル座標の並びが食い違った場合には、Feature plot は『対応が取れませんでした』と止まるのに、共発現散布図だけは何事も無かったかのように“もっともらしい別の図”を出してしまう。

**検証の経緯(反証の試みと結果)**

コードの指摘は正確（検査は len(matrix)!=len(df) だけで、ver52.5 の _expression_alignment_ok は interactive_deg.py:577/:847 の 2 か所にしか無い）。行順を逆にした parquet を与えて run_coexpression を直接実行したところ、無警告で図が出て全 8/8 点で強度とホバー CellID が入れ替わることを再現できた（同じ材料で番人は False を返す）。しかし『行順がずれた parquet』が生じる経路が現行コードに見当たらない: (1) plot_data の代入は interactive_callbacks.py:820 の 1 か所のみでリポジトリ全体に inplace=True は 0 件・並べ替えも無い、(2) plot_data.parquet と expression_matrix.parquet は同じ R 実行の同じ変数 cell_ids から書かれる、(3) キャッシュキーに RDS の mtime と R スクリプトの mtime が入るため新旧の混在が起きない、(4) 唯一 parquet を書き換える repack ツールは行順不変（書き込み後に全列ビット比較）。開発側も ver52.5 のテスト冒頭で『現状ずれている証拠は無い』と明記している。よって『誤った図が出ている』欠陥ではなく『同種の位置代入に番人が適用されていない』防御の一貫性欠如であり、S3→S4 に落とす。

**再現手順**

実データ不要。scratchpad/tmp/v6_e1_coexpr.py を実行（CellID を逆順にした expression_matrix.parquet を作り、plot_data と共に run_coexpression を直接呼ぶ）。全点で強度とホバー CellID が入れ替わった図が無警告で返り、同じ材料で _expression_alignment_ok は False を返す。ただし本番でこの parquet が生じる操作手順は見つかっていない。

**根拠**

- `App/app/callbacks/interactive_feature_lists.py:235: if len(matrix) != len(df):`
- `App/app/callbacks/interactive_feature_lists.py:250: clusters = df["Cluster"].astype(str).to_numpy()`
- `App/app/callbacks/interactive_feature_lists.py:251: cell_ids = df["CellID"].to_numpy()`
- `App/tests/test_expression_row_alignment.py:13: - `plot_data` は 1 箇所でしか代入されず並べ替えも無いことは確認済み（**現状ずれている証拠は無い**）`
- `App/Script/helpers/extract_seurat_data.R:204: names(acols) <- c("CellID", features) / :205 acols[[1L]] <- arrow::Array$create(cell_ids)（plot_data と同じ cell_ids から書く）`
- `App/app/services/seurat_bridge.py:292: raw = f"{rds_path}|{mtime}|{r_mtime}"（RDS が変われば別キャッシュ）`
- `実行ログ scratchpad/tmp/v6_e1_coexpr.py: 「x とホバー CellID が食い違う点: 8/8」「_expression_alignment_ok(cache, plot_data) = False」`

**修正方針**

interactive_feature_lists.py:235 の行数検査の直後に interactive_deg._expression_alignment_ok(cache_dir, df) を呼び、False なら go.Figure() と『発現量とスポットの対応が取れませんでした（キャッシュを作り直してください）』を返す。判定不能(None)と一致(True)は従来どおり通すので通常時の挙動は変わらない。


### 38. [INFO] マーカーテーブルのクラスタ filter が stale 値のまま全行除外され空表示になる

**判定**: OVERTURNED(独立検証)／**確証度**: [実行確認]／**修正区分**: 修正不要／**該当**: `App/app/callbacks/interactive_loupe.py:359`

**利用者から見た症状**

利用者に見える不具合は無い。解析結果を切り替えると、クラスタ絞り込みは自動的に『全クラスタ』へ戻り、マーカーテーブルには新しいデータの全行が表示される（切替直後の一瞬だけ表が空になることがある程度）。

**検証の経緯(反証の試みと結果)**

反証成立。dcc.Dropdown 自身が『新しい options に含まれない value を null に戻して setProps する』実装を持つため、stale な絞り込みは残らない。本体と同じ dash 2.18.2 / dcc 2.0.0 で populate_marker_table と同型の callback（options だけ返し value は返さない）を作り実 Chromium で測定したところ、cluster='1' を選んだ状態でデータセットを切り替えると、サーバ側は (deg 更新, value='1') → rows=0 の 1 往復のあと即座に (value=None) → rows=6 が続き、画面のドロップダウンも placeholder『全クラスタ』へ戻ってテーブルは全行に復帰した。対象の deg_markers_cluster_filter は再現アプリと同条件（clearable=True、persistence 無し）。したがって『DEG データはあるのにマーカーテーブルが空に見える』という残留症状は発生しない。value の writer が居ないのは欠陥ではなく不要だからで、残るのは 1 往復（数百 ms）のちらつきのみ。

**再現手順**

実データ不要。scratchpad/tmp/v6_repro_app.py を起動し v6_probe_repro.py（実 Chromium）を実行すると、value='1' 選択後にデータセットを切り替えても最終的に rows=6・placeholder 表示へ復帰することを確認できる。

**根拠**

- `App/app/callbacks/interactive_loupe.py:359: if cluster_filter: / :360 recs = [r for r in recs if str(r.get("cluster")) == str(cluster_filter)]`
- `App/app/layouts/interactive_tab.py:1715: id="deg_markers_cluster_filter", / :1716 placeholder="全クラスタ", clearable=True),（persistence 無し＝再現アプリと同条件）`
- `/usr/local/lib/python3.11/dist-packages/dash/dcc/async-dropdown.js: var e=m.map(function(e){return e.value}); ... else e.includes(c)||l({value:null});（options に無い value を null にして setProps）`
- `実行ログ scratchpad/tmp/v6_repro_calls.log: populate: trig=deg deg_clusters=['10','11'] value='1' -> rows=0 / populate: trig=f deg_clusters=['10','11'] value=None -> rows=6`


---

## C-2. ③ R スクリプト・解析の科学的妥当性に関する指摘 (29 件)

### 1. [S1] patch_v8_disable_otsu の終了アンカー(seu_list[[ii]] <- filtering_result_otsu$filtered_seurat)が v1

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**

**利用者から見た症状**

クラスタを選んで「再解析」すると、選んだはずのスポットの半分ほどが黙って捨てられ、信号の強い側だけで UMAP とクラスタが作り直されます。特に信号が弱い組織(脂肪や壊死した部分など)はほぼ全部消えます。画面にもエラーにも何も出ず、出力フォルダの spot_filtering_*_otsu.png とログの Removed 行を見に行かない限り気付けません。

**科学的影響**

再解析入力は1回目の Otsu を通過したスポットのみ(左裾切断済み)なので残存分布はほぼ単峰となり、Otsu のクラス間分散最大化は分割点を平均近傍に置く。結果、2回目は分布形にほとんど依存せず約半数を切り落とす。逐語移植シミュレーション(背景4000+低強度組織1500+中強度2000+高強度1500)では、exclude(背景クラスタ除外)で 2152/4999=43.0% を追加除去し、内訳は低強度組織 98.9% 除去・中強度 33.5%・高強度 0.1% と生物学的に極端に偏る。keep(低強度クラスタのみ抽出)では 744/1499=49.6% 除去。分布形を振った20試行で中央 49.5%(47.5〜53.2%)。再解析の再解析ではさらに 52.3% が消える。すなわち UMAP・クラスタ・DEG は利用者が選んだスポット集合ではなく『その TIC 上位半分』で計算され、低 TIC の生物学的領域(脂肪・壊死巣・低イオン化領域)が通知なく系統的に消失する。論文の Methods は『クラスタ N を抽出して再解析』と書かれるが実体が違う。

**検証の経緯(反証の試みと結果)**

反証を4方向から試みたがすべて不成立。(1)パッチ関数 patch_v8_disable_otsu を逐語移植して DESI テンプレ全8版に実行 → v9〜v13 は生存、v14/v15/v16 は終了 anchor が 0 件で patched_identical=TRUE(完全 no-op)を機械確認。(2)v16 全文に Otsu の ON/OFF 定数は存在せず、Python 側にも注入経路なし。(3)LIVE 既定は config.py:85 の v16 で、analysis_callbacks.py:745-749 → analysis_runner.py:736-741 が V8_SCRIPT_PATH へ注入。(4)Otsu 呼出(v16:2221)は raw 読込ブロック内で reduction ゲート(2323)より前、RESUME_FROM_RDS は ver3.R:340 で必ず FALSE 注入されるため full/reduction_only 両 stage で必ず実行。既存テスト test_r_patch_anchors.py:71-76 が KNOWN_DEAD_ANCHORS として同じ事実を登録済みで、反証どころか追認。さらに帰結を v16:1965-2026 の逐語移植で数値例証した(下記)。元の S2 は過小評価と判断し S1 へ引き上げる。

**根拠**

- `/home/user/U_Analysis/App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:275: end_idx <- grep("seu_list\\[\\[ii\\]\\]\\s*<-\\s*filtering_result_otsu\\$filtered_seurat", code_vec)`
- `/home/user/U_Analysis/App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:277: if (length(end_idx) == 0) return(code_vec)`
- `/home/user/U_Analysis/App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2225: seu_list[[length(seu_list) + 1]] <- filtering_result_otsu$filtered_seurat`
- `/home/user/U_Analysis/App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2221: filtering_result_otsu <- filter_low_count_spots(`
- `/home/user/U_Analysis/App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2189: for (sub in sub_samples) {   (置換文 seu_list[[ii]] が破壊的になる根拠。外側は 2143 の for(ii in seq_along(sample_names)))`
- `/home/user/U_Analysis/App/app/config.py:85: DESI_V8_TEMPLATE_PATH = DESI_SCRIPT_DIR / "260623_DESI-UMAP_Template_v16.R"`
- `/home/user/U_Analysis/App/app/services/analysis_runner.py:738-740: var_name = "V13_SCRIPT_PATH" if is_tims else "V8_SCRIPT_PATH" / lines = _replace_assign(lines, var_name, _r_str(params["main_analysis_script_path"]))`
- `/home/user/U_Analysis/App/tests/test_r_patch_anchors.py:72-76: ("DESI 再解析", r"seu_list\[\[ii\]\]..."): "Otsu スキップの終了 anchor。…背景除去が再実行される"  (リポジトリ自身が既知の実害として登録済み)`

**修正方針**

DESI_RDS_ClusterFilter_ver3.R:275 の終了 anchor を v16 の実コードに合わせて `seu_list\[\[length\(seu_list\) \+ 1\]\]\s*<-\s*filtering_result_otsu\$filtered_seurat` にする。同時に ver3.R:285 の置換文も `seu_list[[length(seu_list) + 1]] <- seurat_obj` に変えること(旧文 `seu_list[[ii]] <- seurat_obj` のままだと v16:2189 の内側ループ for(sub in sub_samples) で ii がファイル添字のままなので ROI 分割時に同じ要素を上書きし最後の ROI しか残らない)。あわせて ver3.R:277 の `if (length(end_idx)==0) return(code_vec)` を .stopif へ変えて fail-closed にし、テンプレ改版で再び死んでも気付けるようにする。挙動の設計判断は不要(意図はパッチ自身のコメントが明記)なので mechanical だが、過去の全 DESI 再解析結果が変わるため利用者への告知は必須。


### 2. [S1] RPCA分岐は DefaultAssay を "integrated"(2805)に設定したまま FindAllMarkers(2930)を実行するため、Wilcoxon検定・av

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**

**利用者から見た症状**

複数サンプルの DESI 解析を回すと、RPCA フォルダに出る「クラスタごとの特徴分子リスト」(analysis_deg_all_markers.csv)と、そこから作られる Top5 の分子画像・volcano 図・AI 解釈だけが、他の手法(Harmony・PCA)とは違う土台の数値で作られる。しかも一部の分子は計算に失敗して一覧から黙って消える。画面上は何のエラーも出ないため、利用者は「RPCA ではこの分子が出てこなかった」と誤って解釈してしまう。

**科学的影響**

RPCA の DEG は、バッチ補正後の(負値を含む)統合行列の上で Wilcoxon 検定・avg_log2FC・検出率 pct が計算される。Seurat 公式も integrated assay は corrected data であると明記しており(integration.R:1372-1374)、DE 検定に使うことは想定されていない。pct.1/pct.2 は FoldChange.default:1042-1044 の『値 > 0 の割合』であり補正値に対しては検出率の意味を持たないのに、その上に min.pct=0.25 の検定前フィルタが掛かる。加えて群平均 log2((Σexpm1+1)/n) は補正値がわずかに負に揃うだけで NaN となり、当該 feature が differential_expression.R:568 で無言に脱落する。さらに検定対象 feature 集合が RPCA だけ SelectIntegrationFeatures の 3000 個に限られ、全 feature を対象とする Harmony と手法間比較が成立しない。論文に RPCA のマーカー表を載せた場合、統計量そのものが誤りであり再現性がない。

**検証の経緯(反証の試みと結果)**

反証を5方向から試みて全滅した。(1) 2805〜2930 の間に assay を戻す行が無いことを grep -n DefaultAssay の全8件(104/563/565/567/2308/2688/2689/2805)で確認。(2) FindAllMarkers 3呼出とも assay= 引数なし。(3) run_volcano_and_msi の Spatial 切替(563)は呼出が 2992 で検定より後かつ関数ローカル。(4) 到達性: DESI v16 に ANALYSIS_METHOD は grep -c=0 で存在せず、2512 の Multi-sample mode 以降 Harmony と RPCA が無条件に両方走る=多サンプル DESI では必ず踏む。(5) Seurat 側の救済も原典で潰した。FoldChange.Assay は norm.method が NULL でも slot='data' なら expm1 版 mean.fxn を選び(differential_expression.R:1111→:1088)、さらに IntegrateData が FindIntegrationAnchors コマンドを付ける(integration.R:1604)ため FoldChange.Seurat(:999-1000)が norm.method='LogNormalize' と判定してやはり expm1 版になる。逃げ道なし。base R 実行で、n=500 pixel の補正値が 1 pixel あたり -0.005 程度揃うだけで (Σexpm1+1)/n が負になり log2 が NaN、現実的混合(90%がわずかに負)でも NaN を確認。NaN feature は differential_expression.R:568 の which() が無言で除去することも実演した。ただし元主張の『警告なく脱落』は不正確で、log2(負) は R が 'NaNs produced' 警告を出す(実行確認)。無言なのは『どの feature が消えたか』であって警告自体は出る。この訂正は本質を変えない。深刻度は R2 の S2 から S1 に引き上げる: 条件付きでなく必ず発火し、出力 CSV の p 値・avg_log2FC・pct が補正値上の値で利用者に見分けがつかず、同一スクリプトの他2分岐(2688-2690, 2308)と TIMS ver6(1850-1851)が全て Spatial に戻している以上これは設計判断でなく書き忘れである。

**根拠**

- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2805: DefaultAssay(seu_rpca) <- "integrated"`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2930: FindAllMarkers(seu_rpca, only.pos = FALSE, min.pct = 0.25, logfc.threshold = 0.25, test.use = "wilcox")`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2688-2689: assay_hm_harmony <- if ("Spatial" %in% Seurat::Assays(seu_harmony)) "Spatial" else DefaultAssay(seu_harmony) / DefaultAssay(seu_harmony) <- assay_hm_harmony  ← Harmony だけ Spatial に戻している`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2512: } else {  ← Multi-sample mode 開始。v16 に ANALYSIS_METHOD は 0 件(grep -c)で Harmony と RPCA が無条件に両方走る`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2774: features <- SelectIntegrationFeatures(object.list = seu_list_norm, nfeatures = 3000)  ← RPCA の検定対象は 3000 feature、Harmony は Spatial 全 feature`
- `(原典) seurat/R/differential_expression.R:1088: return(log(x = (rowSums(x = expm1(x = x)) + pseudocount.use)/NCOL(x), base = base))  ← :1111 'data' = log1pdata.mean.fxn により slot='data' の既定`
- `(原典) seurat/R/integration.R:1566-1567: integrated.assay <- CreateAssayObject( data = integrated.data,  ← 補正値がそのまま data 層に入り counts は空`
- `(原典) seurat/R/differential_expression.R:568: names(x = which(x = abs(x = total.diff) >= logfc.threshold))  ← NaN は which() が無言で除去`

**修正方針**

RPCA 分岐の FindAllMarkers(2930)の直前に、Harmony 分岐 2688-2690 の 3 行(Spatial への DefaultAssay 切替 + JoinLayers の tryCatch)をそのまま複写する。設計判断は不要で、同一スクリプト内に正解の実装が既に 2 箇所ある。ただし過去の RPCA DEG 出力(CSV・Top5・volcano・GPT 解釈)の数値は全面的に変わるため、利用者への通知と再解析の案内が必須。回帰テストは『RPCA の DEG 実行時に DefaultAssay が Spatial であること』を assert する形で先に置く。


### 3. [S1] キャリブレーション表で「使わない」にした行が本解析経路で除外されない（use="No" が truthy）

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**／**該当**: `App/app/services/analysis_runner.py:278`

**利用者から見た症状**

キャリブレーション表でチェックを外した参照ピーク（＝明らかにおかしいので使わないと決めた点）が、実際には補正曲線の計算に混ざったままになります。画面の「使用ピーク」一覧にはその行が出ないので、利用者は正しく外れたと信じたまま解析を進めてしまいます。

**科学的影響**

誤検出ピークを1点外したつもりでも回帰に残るため、m/z 補正量が桁で狂います。実測再現では m/z 400 の補正量が本来 +0.0012 Da のところ +0.0434 Da となり、その差 0.042 Da は既定の照合許容 0.01 Da の 4 倍にあたります。補正係数は R 側で全フィーチャー名の m/z を書き換え（ver6 スクリプト 1137-1138 行）、名前が衝突したフィーチャーは統合されるため、markers_annotated.csv の化合物同定が総崩れするだけでなく発現行列そのものが変質しえます。さらに対話画面で検証した補正曲線と本解析の補正曲線が別物になるため、論文用の「対話画面で妥当性を確認した」という主張が成立しません。過去に「使わない」行を含む表で実行した解析の校正係数は全て再検証が必要です。

**検証の経緯(反証の試みと結果)**

反証を4方向から試みたがすべて失敗し、むしろ悪化材料が2件見つかった。(1)呼出元の絞り込み: analysis_callbacks.py:646/696 は Store の中身をそのまま compute_calibration_coefficients へ渡しており中間の濾過は無い。(2)use はブールでは: 表の use を書くのは行チェックボックス同期コールバックただ一つで、analysis_callbacks.py:1738 が文字列 "Yes"/"No" を書く。表の列定義に use 列は無く、row_selectable="multi"(settings_tab.py:481) のチェックボックスだけが入口。(3)既存テストの担保: 逆に tests/test_calibration_fit_sanity.py:31 の fixture が {"use": True} とブール値を使っており UI の文字列を一度も通していない（20件とも現状で pass＝検出不能）。(4)既定OFFなので起きない: DEFAULT_CALIBRATION_ENABLE=False だが calibration_enable は _AUTO_SAVE_KEYS にあり一度ONで永続、かつ「外れ値のチェックを外す」ことがこの表の唯一の用途。実行確認（tmp/w1s_r1301.py）: 4点中1点を use="No" にした表で n_points=4 となり除外されない。m/z 400 での補正量は誤 +0.04337 Da に対し正 +0.00120 Da で、差 0.042 Da は照合許容 DEFAULT_TOLERANCE_MZ=0.01 Da の 4 倍。追加発見として、実行時サマリ analysis_callbacks.py:2536 は use=="Yes" の行だけを『使用ピーク』として表示するため、画面に出ない点が回帰に入るという目視不能な構造になっている。さらに係数は R:1137-1138 で全 feature 名を書き換え、:1139-1140 では衝突した feature を merge_duplicate_features で統合するため、過補正は改名にとどまらず行の併合＝データ改変を起こしうる。対話側 interactive_calibration.py:1197 が != "Yes" で正しく除外しており、正しいのは対話側である。

**根拠**

- `App/app/services/analysis_runner.py:278:         if not row.get("use"):`
- `App/app/callbacks/analysis_callbacks.py:1738:         new_use = "Yes" if i in selected_set else "No"`
- `App/app/callbacks/interactive_calibration.py:1197:         if row.get("use") != "Yes":`
- `App/app/callbacks/analysis_callbacks.py:2536:     used_rows = [r for r in cal_table if r.get("use") == "Yes"]`
- `App/app/layouts/settings_tab.py:481:                                                         row_selectable="multi",`
- `App/tests/test_calibration_fit_sanity.py:31:     return [{"use": True, "ref_mz": r, "obs_mz": r + e}`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:1138:     new_names <- sprintf("m/z %.5f", new_mz)`
- `実行(tmp/w1s_r1301.py): use="No" 1行を含む4行表 → n_points=4 / 除外3行時 n_points=3。m/z 400 の補正量 誤+0.04337 Da vs 正+0.00120 Da`

**修正方針**

analysis_runner.py:278 を、文字列とブールの両方を受ける判定に置き換える。_use = row.get("use", True); 文字列なら strip().lower() が ("yes","true","1") に含まれるときだけ採用、そうでなければ bool(_use) で採用可否を決める。単純に != "Yes" にすると既存テスト（use=True のブール fixture）が全滅するのでこの形が要る。scratchpad/tmp で単体検証済み: use=True→採用、"Yes"→採用、"No"→除外、キー欠落→採用（現行は黙って捨てていたのでここも同時に直る）。正解の意味論は対話側 interactive_calibration.py:1197 に既に実装済みで設計判断は不要。回帰テストとして "Yes"/"No" 混在表を渡すケースを追加すること。


### 4. [S1] 複数ファイル入力では『無補正』シナリオでも Harmony/RPCA が sample 単位で必ず実行され、Methods はシナリオ文言を優先して『バッチ補正は行わなかった』と誤記する

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**／**該当**: `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2539`

**利用者から見た症状**

解析シナリオで「同一切片のクラスタ／群比較（Ctrl vs KO 等）：補正なし＝無補正PCA」を選んでも、Ctrl と KO を別ファイルで読み込んだ場合は裏で Harmony（と RPCA）によるバッチ補正が実行され、画面の既定表示もその補正済み結果になる。それにもかかわらず、論文用に出力される Methods 下書き（METHODS_prose_ja.md / 画面の Methods 欄）には「同一切片内の比較であり、バッチ補正は行わなかった」とだけ書かれ、Harmony を使ったことはどこにも出てこない。

**科学的影響**

(1) Ctrl / KO のように群がファイル単位で分かれている（＝sample と condition が完全交絡した）設計では、Harmony が群間差そのものをバッチ差とみなして除去する。最小実装での数値実証では群間分離が Cohen's d 3.90 → 0.45（残存 11.4%）まで縮小した。すなわち本来検出できるはずの群差が UMAP・クラスタ構成・クラスタ由来 DEG から消え、「差が無かった」という誤った結論に至りうる。これは ver4 ヘッダ(:27-29)が防ごうとした過補正そのもの。(2) 論文の Methods に「バッチ補正は行わなかった」という事実と異なる記載がそのまま載る。平文 Methods には統合手法の表が含まれないため、生成物だけを見ても矛盾に気づけない。再現性・査読の観点で致命的。

**検証の経緯(反証の試みと結果)**

反証を4方向から試みて全て失敗し、むしろ根拠が強化されたため S2→S1 に引き上げた。(1)『実は integration_method が優先されるのでは』→ methods_text.py を直接 import し全シナリオ×全手法で build_methods_prose() を実出力した結果、scenario があると integration_method は一切参照されない(within_slice + Harmony → 「同一切片内の比較であり、バッチ補正は行わなかった」)。(2)『同じ文書の表に統合手法=Harmony が出るので矛盾に気づける』→ 論文貼付用の平文 Methods(render_methods_prose → METHODS_prose_ja.md/.html)には統合手法の表が含まれず、全文を生成したところ Harmony への言及がどこにも無い完結した虚偽記述になる。_sec_warnings もシナリオと実手法の乖離を検知しない(警告コードは cache_only_embedding / derived_pca_not_persisted の2種のみ)。(3)『within_slice で複数ファイルは想定外で到達しない』→ BATCH_VAR="sample" は .bv_is_bio の対象外なので levels>1 で必ず group_var になり、seu$sample <- sn(:2393)はファイル単位。UI ラベル自体が「Ctrl vs KO 等：補正なし＝無補正PCA」と最も普通の比較設計を誘導し、ヘルプ表も「行われる補正と出力: 補正なし＝無補正PCA」と断言。複数ファイル時の検査・警告は R/Python どちらにも無い。(4)『Harmony 結果は既定表示ではない』→ interactive_callbacks.py:561/598 とも default="Harmony"。加えて Harmony 中核(soft-kmeans+クラスタごとのバッチ重心除去)を最小実装して交絡下(batch=sample=condition)で数値実証したところ、群間分離 max|Cohen's d| が 3.902→0.445(残存 11.4%)まで縮小した。methods_text.py が冒頭で「絶対に守る原則: 値を捏造しない」「PCA=未補正。『補正した』と書かないことが重要」と自ら宣言している点でも、本件はその原則の逆方向の違反である。

**根拠**

- `App/app/services/methods_text.py:725-728: if scenario and scenario in _SCENARIO_TEXT: txt = _SCENARIO_TEXT[scenario][0 if ja else 1] ← integration_method の分岐(729-)より前で確定し、以降到達しない`
- `App/app/services/methods_text.py:704-706: "within_slice": ("同一切片内の比較であり、バッチ補正は行わなかった", "the comparison was within a single slice and no batch correction was applied")`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2539: group_var  <- if (.bv_levels > 1 && !.bv_is_bio) .bv else NA_character_ ← .bv="sample" は :2538 の .bv_is_bio 判定(condition/slice_id のみ)に該当せず無条件に補正対象`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2686: } else if (length(seu_list) >= 2 || .rpca_section_ok) { ← ENABLE_RPCA は :259 で TRUE 既定・App からの上書き経路なし。複数ファイルなら RPCA も実行`
- `App/app/layouts/settings_tab.py:338: {"label": "同一切片のクラスタ／群比較（Ctrl vs KO 等）：補正なし＝無補正PCA", "value": "within_slice"}`
- `App/app/templates/help/analysis.html:270: <td>同一切片のクラスタ／群比較</td><td>1切片の部分構造／Ctrl vs KO が別アノテーション</td><td><strong>補正なし＝無補正PCA</strong>`
- `App/app/callbacks/interactive_callbacks.py:561: default = "Harmony" if "Harmony" in rds_map else list(rds_map.keys())[0] ← 補正済み結果が既定表示(:598 も同一)`
- `App/app/callbacks/analysis_callbacks.py:96: "within_slice":      ("biological", "sample",   False), ← BATCH_VAR="sample" が analysis_runner.py:558-559 で R へ注入される`

**修正方針**

二段構え。【即時・機械的・数値不変】methods_text.py:725-728 でシナリオ文を integration_method より優先させない。実際に表示中の手法が判っている場合は実手法を主文にし（例:「試料間のバッチ効果は Harmony により補正した」）、シナリオは「設定上の方針」として併記する。加えて scenario が『補正なし』系なのに integration_method が Harmony/RPCA のとき、_sec_warnings に不整合警告コードを追加する。【要科学判断・数値が変わる】within_slice/condition_compare で入力ファイルが 2 つ以上のときの扱いを決める: (i) group_var を NA にして本当に補正しない、(ii) 実行前に「複数ファイルなので sample 単位の補正が入ります」と警告して同意を取る、(iii) シナリオ再選択を強制する。どれを採るかで結果数値が変わるため利用者の承認が必要。


### 5. [S1] 無補正 PCA companion が Harmony の入力 pca を流用すること自体は妥当(RunHarmony は reduction "pca" を変更せず "harmo

**判定**: UPHELD(独立検証)／**確証度**: [コード確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**

**利用者から見た症状**

結果画面の「統合手法」で「PCA (uncorrected)」を選ぶと、Harmony とは別の独立した解析結果のように見える。しかし実際に表示されるクラスタ番号・各クラスタのマーカー一覧（markers_annotated.csv）・空間マップ・クラスタ割当 CSV・GPT 解釈は、すべて Harmony の結果をそのままコピーしたものである。異なるのは UMAP の点の配置だけ。そのため「補正しても結果は変わらなかった」と見えるが、それは補正の有無を比べていないためである。

**科学的影響**

ver4 が宣言した「補正の妥当性を無補正クラスタリングと比較する」という用途(スクリプト :34-35)が成立しない。無補正 PCA 側のクラスタが Harmony 由来なので、比較すれば必ず「クラスタは補正に対して頑健だった／差が無かった」という偽の結論が返る。論文で「バッチ補正の有無でクラスタ構成が変わらないことを確認した」と書けば循環論法になり、無補正 PCA のマーカーとして報告した代謝物は実際には Harmony クラスタのマーカーである。Harmony が走る全ての複数サンプル TIMS 解析で無条件に発生する。

**検証の経緯(反証の試みと結果)**

クラスタ継承の点について7方向の反証を試み、全て失敗した。むしろ (a) 無条件で全ての複数サンプル TIMS 解析に発生する、(b) CHANGELOG にも意味変更が記載されていない、ことが判ったため S2→S1 に引き上げた。(1) pca 流用の妥当性自体は元監査員が既に自ら退けており争点ではない（RunHarmony は pca を読み取り harmony を追加するのみ、同 HVG/同 npcs で比較が公平）。(2) 下流の再クラスタは if (!("seurat_clusters" %in% colnames(obj@meta.data)))(:1760) という『列が無いときだけ』の条件で、seu_unc は run_pipeline が最後に FindClusters(...) を返したオブジェクトのコピー(:2620)なので列も active.ident も保持しており分岐に入らない。:2621-2622 の除去ループは reduction スロットしか触らない。(3) diet_seurat_safe(rds_io.R:100-133) は DietSeurat に counts/data/scale.data/dimreducs/graphs しか渡さず @meta.data と @active.ident は対象外。load_rds_compact(:2642) にも Idents を張り直す処理は無い。(4) UMAP だけは :1730-1751 で red_src="pca" として再計算される（キャッシュは prefix 別 :1656 なので混線もしない）＝主張どおり「UMAP 埋め込みだけが pca 由来」。(5) FindAllMarkers(:1893) は (data 層, Idents) のみに依存する決定的 wilcox（max.cells.per.ident 既定 Inf でダウンサンプリング無し）なので出力は複製になる。(6) CHANGELOG ver19.5(:4792-4804) は PCA の設定統一と計算削減のみを述べ『下流・PreFlight 診断・アプリは無改修』と結んでおり、クラスタが Harmony 由来に変わったことは一言も書かれていない。ver5(:2348)は run_pipeline(FALSE, cfg) で FindClusters まで独立に実行していた。(7) ALWAYS_OUTPUT_UNCORRECTED_PCA <- TRUE(:253) が既定で App からの上書き経路は無く、Harmony が走る全ての複数サンプル解析で必ず生成される。Seurat が本環境に無いため実行検証はできず confidence は code-read とする。

**根拠**

- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2620-2622: seu_unc <- seu_harmony / for (.rn in setdiff(names(seu_unc@reductions), "pca")) seu_unc[[.rn]] <- NULL ← reduction スロットのみ除去、meta.data と active.ident は保持`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:1760: if (!("seurat_clusters" %in% colnames(obj@meta.data))) { ← 列が既存なので FindNeighbors/FindClusters は走らない`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:1893: deg <- FindAllMarkers(obj, only.pos=FALSE, min.pct=DEG_MIN_PCT_VAL, logfc.threshold=DEG_LOGFC_TH_VAL, test.use="wilcox") ← Idents(obj)(=Harmony クラスタ)と data 層のみに依存`
- `App/Script/helpers/rds_io.R:120-127: suppressWarnings(Seurat::DietSeurat(obj, counts=keep_counts, data=TRUE, scale.data=keep_scale, dimreducs=dimreducs, graphs=graphs)) ← meta.data / active.ident は引数に無く保持される`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:253: ALWAYS_OUTPUT_UNCORRECTED_PCA <- TRUE ← App からの上書き経路は無く常時有効`
- `App/Script/TIMS/260619_DBSCAN_With_cluster_ver5_no-png_slim.R:2348: ok2 <- tryCatch({ seu_unc <- run_pipeline(FALSE, cfg); TRUE }, error=function(e) FALSE) ← ver5 は UMAP+FindClusters まで独立に実行していた`
- `CHANGELOG.md:4798-4800: 変更…無補正PCAは seu_harmony が内部に持つ入力 pca をそのまま流用…保存形式・ファイル名は不変のため下流・PreFlight 診断・アプリは無改修 ← クラスタ継承への言及なし`
- `App/app/utils/deg_utils.py:552: "PCA (uncorrected)": "pca_uncorrected", ← アプリは独立した統合手法として扱う(interactive_callbacks.py:470 でドロップダウンにも追加)`

**修正方針**

設計意図の確認が先。【診断オーバーレイが意図だった場合＝数値不変】表示名とフォルダ名を実態に合わせる（例: 「無補正PCA埋め込み（クラスタは Harmony 由来）」/ prefix を pca_uncorrected_overlay に）。統合手法ドロップダウンからは外し、比較用の独立手法として提示しない。markers_annotated.csv などクラスタ依存の成果物は出力しない。【独立比較が意図だった場合＝数値が変わる】:2620-2622 の後に seu_unc の seurat_clusters 列と active.ident を落とすか、run_downstream_analysis に force_recluster 引数を足して pca に対する FindNeighbors/FindClusters を必ず実行する。この場合 pca_uncorrected 配下の全数値（クラスタ・マーカー・空間図・CSV）が変わるので利用者の承認が必要。いずれにせよ ver19.5 の CHANGELOG に意味変更を追記すべき。


### 6. [S2] p.adjust(deg$p_val,"BH")(2394,2710,2942)の適用集合は (i) min.pct/logfc 検定前フィルタ生存行 (ii) FindAllMa

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**

**利用者から見た症状**

解析設定タブの「p 値閾値」を既定の 0.05 のまま使うかぎり、この設定はまったく効かない。CSV に出てくる分子は全部が自動的に「有意」と判定され、volcano 図で色が付くかどうか・Top5 の分子画像に選ばれるかどうかは、実質 fold change の大小だけで決まっている。CSV の p_val_adj 列は FDR(偽発見率)として読める値になっていないため、その数字を根拠に「偽陽性は 5% 以下」と述べると実際よりかなり甘い見積りになる。

**科学的影響**

p_val_adj 列が FDR として解釈できない。要因は 2 つで、(a) FindAllMarkers の既定 return.thresh=0.01 により返却行がすべて p<0.01 に切り詰められ、BH の性質 max(adjusted)=max(raw) から p_val_adj<0.01<0.05 が数学的に保証される(唯一の例外は利用者が p 閾値を 0.01 未満に設定した場合。UI は min=0 で許す)。(b) min.pct と |avg_log2FC| による検定前フィルタが同じデータから計算されて検定統計量と強く相関するため、独立フィルタの前提が破れて帰無 p が小さい側に偏る。模擬データでフィルタ通過後の帰無 p は P(p<0.05)=0.46(期待 0.05、KS p=4e-23)、実現 FDR は名目の 2.0 倍(pixel 400 vs 1600)〜5.8 倍(60 vs 240)、真の DE が皆無なら 1.00(正しい BH なら 0)。帰結として volcano で有意色が付く分子、Top5 MSI 画像の対象分子、GPT 解釈に渡る分子集合が、実際の FDR より多く選ばれる。なお実 MSI では数千 pixel を独立標本扱いする擬似反復と、同じ発現行列から Leiden でクラスタを定義する循環推論のため厳密な帰無 feature がほぼ存在せず、膨張倍率はデータ規模依存である点は報告書に明記すべき。

**検証の経緯(反証の試みと結果)**

R2(return.thresh による切り詰め)と R11(検定前フィルタの非独立性)の 2 主張を統合して判定する。両方とも成立し、互いに補強する。機構(a): Seurat 原典で FindAllMarkers の return.thresh 既定が 1e-2(differential_expression.R:66)、test.use='wilcox' かつ node=NULL でこの枝を通ること(:181-183)を確認。v16 の 3 呼出は return.thresh を指定していないので返る行はすべて p_val<0.01。BH は step-up 構造上 max(adjusted)=max(raw) なので p_val_adj も全行 <0.01 となり、DEG_P_THRESH_VAL=0.05 を使う有意判定(598-599/655/669/2461-2462)は数学的に恒真=機能していない。base R で一様乱数 5000 本を使い、p<0.01 の 46 本だけに BH をかけると 46/46 が『有意』、max(adjusted)=max(raw)=0.009643 と一致することを実演した。機構(b): Seurat の仕様(pct=値>0 の割合、avg_log2FC=expm1 版、max(pct)>=min.pct かつ |lfc|>=logfc.threshold で検定前に落とす)を Python で再現し、feature 2000・6 反復で実現 FDR を測定。フィルタ通過後の帰無 p は P(p<0.05)=0.459(期待 0.05、KS 一様性検定 p=4e-23)と激しく非一様で、実現 FDR は現行 0.1023 に対し全 feature に BH をかければ 0.0257、Bonferroni なら 0.0000(名目 0.05)。全帰無シナリオでは現行が実現 FDR 1.000(1 回あたり 9.2 件を有意と宣言)に対し正しい BH は 0 件。小クラスタ(60 vs 240)では現行 0.2883 と名目の 5.8 倍。全シナリオで『返却行のうち p_val_adj<0.05 の割合 = 100.0%』を確認した。反証の試みはすべて失敗: p_val_adj は CSV の飾りではなく volcano 着色・Top5 MSI 抽出・GPT 解釈の選抜に使われる(598-599/655/669)。p_val_adj=0 の床置換(2396-2401)は min_nz*0.1 でさらに小さくするだけ。inference_note(2404-2406 他)は『pixel 単位の探索的ランキング』とは言うが『p_val_adj は FDR ではない』とは言っていない。ただし R2 の第 3 副主張(全クラスタをプールしたこと自体が問題)は弱い。BH は PRDS の下で FDR を制御し、任意依存でも高々 log 係数の損(BY)であるため、プーリングは付随的論点であり本丸は (a) と (b) である。深刻度は S2: DEG の p 閾値という UI 設定が既定値では常に無効(利用者に見える誤動作)。

**根拠**

- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2394: deg_markers$p_val_adj <- p.adjust(deg_markers$p_val, method = "BH")  (同型: 2710, 2942)`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2390: FindAllMarkers(seu_single, only.pos = FALSE, min.pct = 0.25, logfc.threshold = 0.25, test.use = "wilcox")  ← return.thresh 未指定`
- `(原典) seurat/R/differential_expression.R:66: return.thresh = 1e-2,`
- `(原典) seurat/R/differential_expression.R:183: gde <- subset(x = gde, subset = p_val < return.thresh)  ← :181 is.null(node) が真なのでこの枝を通る`
- `(原典) seurat/R/differential_expression.R:545-547: alpha.min <- pmax(fc.results$pct.1, fc.results$pct.2) / features <- names(x = which(x = alpha.min >= min.pct))  ← 検定『前』に落とす`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:655: dplyr::filter(p_val_adj < DEG_P_THRESH_VAL, avg_log2FC > 0) %>%   (同型: 669、着色は 598-599/2461-2462)`
- `(実行) /tmp/.../scratchpad/tmp/w4s/c02_integrated_nan.R ケース6: 全5000本にBH -> 有意0件 / p<0.01の46本だけにBH -> 46/46が有意, max(adjusted)=max(raw)=0.009643`
- `(実行) /tmp/.../scratchpad/tmp/w4s/c04_bh_sim.py シナリオ1: 通過後の帰無pのP(p<0.05)=0.4586(期待0.05, KS p=4.05e-23), 実現FDR A)現行0.1023 B)全featureにBH 0.0257 C)Bonferroni 0.0000`

**修正方針**

FindAllMarkers に return.thresh = 1 を明示して全検定結果を返させ、logfc.threshold / min.pct による検定前フィルタは検定後の表示用フィルタに移す(または min.pct=0, logfc.threshold=0 で全 feature を検定してから BH)。BH の分母は『実際に行った検定数(クラスタ数 x feature 数)』に合わせ、可能ならクラスタごとに family を分ける。あわせて CSV の inference_note に『p_val_adj の補正母集団』を明記する。有意集合が大きく変わるので利用者承認が必須。R11-N1 と同じ FindAllMarkers 行が起点なので 1 つの設計変更としてまとめること。


### 7. [S2] 空間平滑化の radius=0.1/sigma=0.05 が座標単位に対してハードコードされており(App からの注入経路なし・SPATIAL_SMOOTH<-TRUE も固定)、

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**

**利用者から見た症状**

「空間平滑化」は必ず実行される設定になっていますが、実際にどれくらい効くかは読み込んだデータの座標の書き方次第です。座標がピクセル番号(1,2,3…)や µm(0,100,200…)なら平滑化は完全に何もせず、それでも保存ファイル名と方法の記録は「平滑化済み」になります。逆に 0.05 mm 刻みのデータではかなり強くぼかされます。設計上の想定である 0.1 mm 刻みでも、場所によって「隣を4つ使う/1つも使わない」が縞模様に切り替わります。画面から強さを変えることも、効いたか確認することもできません。

**科学的影響**

平滑化の実効強度がデータの座標単位・ピッチだけで no-op〜強平滑まで変わるため、HVF/PCA/Harmony/RPCA/UMAP/クラスタ/DEG のすべてがデータセット間で比較不能な前処理条件になる。実測: 整数ピクセル座標(ピッチ1)および µm 座標(ピッチ100)では全スポットの近傍が自分のみ = 平滑化後と元値の最大差 0 の完全 no-op。0.05 mm ピッチでは内点の近傍が 9〜13 個、分散が元の 11.0% まで低下する強平滑。設計意図どおりの 0.1 mm ピッチでは radius とピッチが一致するため座標の浮動小数点表現が採否を決め、内点 784 個中 理論値5 は 25 個のみ(1:36, 2:204, 3:349, 4:170, 5:25)、900 点中 56 点は一切動かない。しかもこの近傍数は乱雑ではなく座標値に由来する決定論的な縞(周期3〜4)になり、意図どおりの5点十字平滑化との差は滑らかな勾配場で信号 SD の 12.9% に達する。空間構造そのものを論じる MSI で、空間的に構造化した前処理ムラが入る。加えて RDS 名 DESI_SeuratList2_smoothed.rds と Methods 記載は『平滑化実施』を主張するため、no-op のデータセットでは解析記録が事実と異なる。

**検証の経緯(反証の試みと結果)**

反証を4方向試みたがすべて不成立。(1)App から SPATIAL_SMOOTH/radius/sigma を注入・無効化する経路は全 grep で 0 件。(2)座標正規化は無く、v16:1813-1814 の生値がそのまま v16:2208-2209 経由で平滑化に渡る。Excel/CSV 登録経路の変換器も desi_converter.py:257-258 で素通し。(3)frNN の eps=radius*(1+1e-6) は候補を広げるだけで確定は v16:1896 の厳密 `<= radius`。(4)QC 関数 visualize_spatial_smoothing は v16:1938 に定義のみで呼出 0 件。base R で v16:1892-1903 と同一の距離式・同一判定を再現した実測でも主張どおりの挙動を確認(下記)。severity は S2 を維持(記録と実態の乖離+縞アーティファクトは実在するが、実効強度の変調は信号 SD の 13% 程度で生物学的結論を反転させるほどではないと判断)。

**根拠**

- `/home/user/U_Analysis/App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2251-2252: smooth_radius <- 0.1 / sigma <- 0.05`
- `/home/user/U_Analysis/App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2238: SPATIAL_SMOOTH <- TRUE  (App からの注入経路なし。TIMS ver6:426 は SPATIAL_SMOOTH_ENABLE <- FALSE で非対称)`
- `/home/user/U_Analysis/App/Script/DESI/260623_DESI-UMAP_Template_v16.R:1813-1814: x = data_df[, 2], / y = data_df[, 3],  (列2/3の生値をそのまま座標に採用)`
- `/home/user/U_Analysis/App/Script/DESI/260623_DESI-UMAP_Template_v16.R:1896: keep <- d_cand <= radius         # 現行と同一の <= 判定で確定`
- `/home/user/U_Analysis/App/Script/DESI/260623_DESI-UMAP_Template_v16.R:1888: dbscan::frNN(as.matrix(coords), eps = radius * (1 + 1e-6))  (eps拡幅は1896のexact判定で無効化)`
- `/home/user/U_Analysis/App/Script/DESI/260623_DESI-UMAP_Template_v16.R:1938: visualize_spatial_smoothing <- function(   (定義のみ・呼出ゼロ)`
- `/home/user/U_Analysis/App/app/services/desi_converter.py:257-258: x = r[0] if len(r) > 0 else "" / y = r[1] if len(r) > 1 else ""   (単位の素通し)`
- `実行検証 scratchpad/tmp/w5s_c07_smooth.R (v16:1892-1903 と同一式を base R で再現): 整数ピクセル30x30 → 全900点が近傍1・平滑化差0 / µm(ピッチ100) → 全900点が近傍1 / 0.1mm → 内点784中 近傍5は25個のみ(1:36,2:204,3:349,4:170,5:25)、56/900点は不動 / 0.05mm → 内点近傍9〜13・分散比0.110`

**修正方針**

(a) radius を絶対値でなく実測ピッチの倍数にする(近接スポット間距離の最頻値 d0 を求め radius = k*d0, sigma = radius/2 等)、または (b) SPATIAL_SMOOTH / radius / sigma を UI から注入可能にして receipt・Methods に実値を記録する。いずれの場合も境界判定は `d <= radius` の厳密比較をやめ `d <= radius*(1+1e-9)` 等の許容付きにして浮動小数点の縞を消す。あわせて visualize_spatial_smoothing を実際に呼んで平滑化前後の QC 図を出す。どの平滑化強度が科学的に正しいかの判断が要るため science-judgment。


### 8. [S2] DESI で FindAllMarkers の閾値が直書きされ、UI 設定(DEG_LOGFC_TH_VAL)が検定に届かない

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**／**該当**: `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2390`

**利用者から見た症状**

解析設定タブで「log2FC 閾値」を変えて DESI 解析を回しても、出てくる特徴分子の一覧がまったく変わらない(設定が黙って無視される)。さらに自動生成される Methods 下書きには「検定に先立ち、いずれかの群で 0.05 以上の測定点に検出される特徴量に限定した」と書かれるが、実際に使われた値は 0.25 である。

**科学的影響**

(1) UI の logFC 閾値が DESI の検定対象 feature を変えないため、利用者は「閾値を下げたのにマーカーが増えない」という挙動に直面するか、気付かずに設定が効いた前提で解釈する。(2) より重大なのは Methods 自動生成の汚染である。DESI の実行スクリプトには DEG_MIN_PCT_VAL が無いので runtime_script.py:45 の復元が必ず失敗し、methods_text.py:851-853 が provenance.py:58-60 の BATCH_DE_FIXED_PARAMS['min_pct']=0.05 にフォールバックして、実際には 0.25 で走った検定を「0.05」と印字する。同様に methods_text.py:864-869 は UI の logFC 値を『有意と判定する閾値』として印字する。methods_text.py:8-14 が自ら「絶対に守る原則: 値を捏造しない」「実際に検定へ渡ったのは解析設定タブの p_thresh / logfc_thresh」と宣言している以上、DESI ではモジュールが防ごうとした事故そのものが起きている。論文の Methods に誤った前処理条件が載るため、影響は結果数値ではなく再現性・記載の正確性に及ぶ。

**検証の経緯(反証の試みと結果)**

核心(UI の閾値が DESI の検定に届かない)は完全に成立する。v16:167 の DEG_LOGFC_TH_VAL は analysis_runner.py:502-505 の _replace_assign で書き換わるが、_replace_assign(:171-189)は代入行の右辺を差し替えるだけなので、2390/2698/2930 の直書きリテラル 0.25 には何も起きない。DEG_MIN_PCT_VAL は v16 に grep 0 件で存在すらしない。TIMS ver6:1893 は正しく変数渡ししており対比が明確。反証は 2 点だけ成立したので副主張を弱め、用語を訂正する。(a) 『既定 0.10 なので volcano の破線が空白帯の内側に引かれる』は既定運用では起きない。アプリ側の実効既定は settings_tab.py:625 と analysis_callbacks.py:527 でいずれも 0.25 であり、注入後の破線位置(±0.25)と検定フィルタ(±0.25)が一致する。誤読が起きるのは利用者が値を変えたとき、すなわち『設定が効かない』バグが噛む場面に限られる。テンプレを素の R として単独実行した場合のみ 0.10 になる。(b) 『runtime_script.py:45 のマップは DESI では常に注入失敗する』は用語が誤り。runtime_script.py は注入器ではなく実行済みスクリプトから条件を読み戻す復元器(:1-20 の冒頭コメント、RUNTIME_VAR_MAP :33-61)。正しくは『DESI では min_pct の復元が常に失敗する』。その代わり、反証作業中に R11 が挙げていない増幅要因を発見した(scientific_impact 参照)。深刻度は S2 のまま: DEG の数値自体が誤っているのではなく、設定が黙って無視され、かつ Methods に実際と違う値が載る。

**根拠**

- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:167: DEG_LOGFC_TH_VAL <- 0.10  # Log2 Fold Change の閾値`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2390: deg_markers <- FindAllMarkers(seu_single, only.pos = FALSE, min.pct = 0.25, logfc.threshold = 0.25, test.use = "wilcox")  (同型: 2698, 2930)`
- `App/app/services/analysis_runner.py:171-183: pattern = re.compile(rf"^\s*{re.escape(var)}\s*<-\s*.*$") / lines[i] = f"{var} <- {new_rhs}"  ← 代入行しか書き換えない`
- `App/app/layouts/settings_tab.py:624-625: dbc.Input(id="logfc_thresh", type="number", value=ls.get("logfc_thresh", 0.25), min=0, step=0.05)  ← UI 実効既定は 0.25(0.10 ではない)`
- `App/app/callbacks/analysis_callbacks.py:527: "logfc_thresh": float(logfc_thresh) if logfc_thresh else 0.25,`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:1893: deg <- FindAllMarkers(obj, only.pos=FALSE, min.pct=DEG_MIN_PCT_VAL, logfc.threshold=DEG_LOGFC_TH_VAL, test.use="wilcox")  ← TIMS は正しい`
- `App/app/services/methods_text.py:851-853: min_pct = _get(c, "analysis.de.min_pct") / if min_pct in (None, ""): min_pct = fixed.get("min_pct")`
- `App/app/services/provenance.py:58-60: BATCH_DE_FIXED_PARAMS = { "test": "wilcox", "min_pct": 0.05,  ← DESI では復元不能なのでこの 0.05 が Methods に出る`

**修正方針**

v16 に DEG_MIN_PCT_VAL <- 0.25 を新設し(既定値を現行リテラルと同じ 0.25 にしておけば既定運用の数値は不変)、2390/2698/2930 の 2 つのリテラルを DEG_MIN_PCT_VAL / DEG_LOGFC_TH_VAL に置換する。あわせて provenance.py の BATCH_DE_FIXED_PARAMS を DESI/TIMS で分けるか、復元不能時に 0.05 を埋めず『未記録』のまま出す(methods_text.py の設計原則どおり)ように直す。DESI 再解析(DESI_RDS_ClusterFilter_ver3.R:355-356)も同じ穴を継承するので同時に確認する。C04 と同じ 3 行が起点なので 1 つの修正として設計するのが合理的。


### 9. [S2] KNOWN_DEAD とされる USE_ROI_AS_SAMPLE / ROI_FILTER について「ROI を使った解析を再解析しても実害は無い」は成立しない。ROI モードで

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**

**利用者から見た症状**

ROI(関心領域)ごとに別サンプルとして解析した DESI データで、クラスタを絞り込んで再解析すると、ROI に分けたサンプルが結果から丸ごと消える。ログには「skip (no remaining spots): <サンプル名>」としか出ないので、利用者は「そのサンプルは選んだクラスタを全部除外されたのだろう」と誤解し、実際にはサンプル名が食い違って読み飛ばされているとは気づけない。ROI ありのファイルだけの場合は「新規txtが1つも生成されませんでした」と止まるが、エラー文が ROI に一言も触れないため原因にたどり着けない。

**科学的影響**

ROI 有無(または ROI_FILTER 適用結果)が混在する DESI データセットでは、ROI 展開されたサンプルが誤誘導的なログだけを残して再解析対象から消え、残りのサンプルだけで再 UMAP・Harmony/RPCA・DEG が回る。サンプル数が変われば Multi-sample 判定、統合手法(PCA 単独か Harmony/RPCA か)、クラスタ構成、DEG の p 値・logFC まですべて変わり、しかも解析は緑で完走するため誤った結果が論文に載りうる。全ファイルに ROI がある場合は停止するので数値は汚染されないが、結果として ROI を使った DESI 解析ではクラスタフィルタ再解析が事実上使えない。さらに ver3:337-356 は USE_ROI_AS_SAMPLE を v16 コピーへ伝播しないため、仮に export が通っても再 UMAP は v16:286 の既定 FALSE で走り本解析とサンプル定義が食い違う。

**検証の経緯(反証の試みと結果)**

経路を全段で確認し、Rscript で挙動を再現した。v16:2175 が ROI モード時のサンプル名を paste0(sample_name, "_", r) にし、v16:2206 seurat_obj$sample <- sub_name で RDS の meta.data$sample が <txt名>_<ROI名> になる。一方 SAMPLE_NAMES は analysis_callbacks.py:721 list_msi_files() が返す拡張子なしファイル名なので、ver3:420 の一致判定が全件外れ :422 で skip される。シミュレーション(scratchpad/tmp/roi_sim.R)で 3 ケースを実行し、ROI OFF は正常、全ファイル ROI ありは ver3:448 の .stopif で大声で停止、ROI 有無が混在すると 2 サンプル中 1 つが落ちたまま完走することを確認した。反証を 3 方向試みた。(1)『注入層で ROI が渡るのでは』→ 渡らない(use_roi_as_sample を積むのは analysis_callbacks.py:629 の desi_v8 分岐のみ、analysis_runner.py:746 の条件は再解析で常に偽)。注入層に限れば既存テストの KNOWN_DEAD 注記は正しいが、実害は R 側で成立する。(2)『無言で脱落は誇張では』→ 一部成立(記述補正が必要)。ver3:422 は「.. skip (no remaining spots): S1」を解析ログに出すので完全な無言ではない。ただしこの文言は「クラスタ絞り込みで残スポットが 0 になった」と読ませるもので真因(サンプル名の不一致)を指さない。(3)『ROI 混在データは非現実的では』→ 反証失敗。むしろ発生条件は元主張より広い。v16:2165-2169 は ROI フィルタ適用後に候補が 0 件になったファイルを『ファイル全体を 1 サンプル』として元名のまま残すため、全ファイルに ROI 列があっても ROI_FILTER が一部ファイルの ROI を全部落とすだけで混在状態が生まれる。以上より「ROI を使った解析を再解析しても実害は無い」は反証できず、UPHELD とする。

**根拠**

- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2175: list(name = paste0(sample_name, "_", r),`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2206: seurat_obj$sample <- sub_name`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2226: expanded_sample_names <- c(expanded_sample_names, sub_name)
App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2231: sample_names <- expanded_sample_names`
- `App/app/callbacks/analysis_callbacks.py:721: sample_names = list_msi_files(reanalysis_data_folder)`
- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:420: rows_sn <- md_keep[as.character(md_keep$sample) == as.character(sn), , drop = FALSE]
App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:421: if (nrow(rows_sn) == 0) {
App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:422: message(".. skip (no remaining spots): ", sn)`
- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:448: .stopif(length(exported_files) > 0, "新規txtが1つも生成されませんでした（RDSの sample 名やクラスタ指定を確認してください）")`
- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:134: .stopif <- function(cond, msg) { if (!isTRUE(cond)) stop(msg, call. = FALSE) }`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2165: if (length(roi_values) == 0) {
App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2168: sub_samples <- list(list(name = sample_name,`

**修正方針**

当座の最小手(mechanical・数値不変): ver3 で SAMPLE_NAMES と RDS の meta.data$sample が 1 つも一致しない、あるいは一部しか一致しない場合に、ROI 展開名の可能性を明示したメッセージで停止する。少なくとも ver3:422 のログ文言を「RDS の sample 名に一致するサンプルがありません(ROI モードで解析した場合、RDS 側は <txt名>_<ROI名> になります)」に直し、部分一致時も .stopif で止める。恒久修正(science-judgment): RDS の sample 名から元 txt と ROI を逆引きして ROI 単位で export し、USE_ROI_AS_SAMPLE / ROI_FILTER を v16 コピーへ伝播する。1 つの txt を複数サンプルへ展開したものをどう書き戻すか(ファイル分割か ROI 列保持か)という設計判断を伴うため利用者の承認が必要。


### 10. [S2] resume_rds_paths が空だと RESUME_DIR_PATH が注入されず、テンプレート直書きの Windows Dropbox パスが runtime スクリプトに残る

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・数値不変／**該当**: `App/app/services/analysis_runner.py:484`

**利用者から見た症状**

「途中再開 (RDSから)」にチェックを入れてフォルダ欄を空のまま(または .rds が入っていないフォルダを指定して)実行すると、赤い入力エラーが表示されるのに解析はそのまま始まってしまう。しかも再開は効かず最初から全部計算し直すため、利用者は「途中から再開したはずなのに」と数時間待たされる。TIMS では解析結果フォルダの log/ の下に、C:\Users\... という名前のまま文字化けしたようなゴミフォルダが 1 つ残る。

**科学的影響**

Linux/Docker 運用では解析結果の数値は変わらない(再開に失敗して全計算をやり直すため結果自体は正しい)。損なわれるのは所要時間と出力フォルダの清潔さのみ。ただし run_app.bat / setup.bat は Docker を介さず python run_app.py を直接起動する Windows ネイティブ経路であり、テンプレ直書きパス(v16:163 / ver6:199 は開発者の Dropbox 実パス)が実在する端末では、無関係データセットの Step1/Step2/Step3 中間 RDS を読み込んで解析が続行され結果が全面的に汚染されうる(その場合 S1 相当)。

**検証の経緯(反証の試みと結果)**

実行で完全に再現した。analysis_callbacks.py:528-529 の既定(resume_from_rds=bool(resume_rds), resume_rds_paths=[])のまま generate_v8_config() を直接呼ぶと、DESI/TIMS とも RESUME_FROM_RDS <- TRUE が注入される一方 RESUME_DIR_PATH はテンプレ直書きの Windows Dropbox パスのまま残った。さらに Rscript で TIMS 側の後段を再現し、ver6:2335 の空文字フォールバックが発動せず else 分岐に落ちて ver6:2339 の dir.create(recursive=TRUE) が 158 バイトのバックスラッシュ入り単一ディレクトリを cwd に作成すること、直後の再開ファイル探索が file.exists=FALSE で静かに失敗することを確認した。生成先はサブプロセスの cwd(analysis_runner.py:1109 cwd=Path(script_path).parent)= 解析出力先の log/ 配下。反証を 2 方向試み、1 つは部分的にしか成立しなかった。(1)『プリフライト検証が止めるのでは』→ analysis_callbacks.py:2395-2399 が resume_rds 時に validate_rds_folder(rds_folder) を呼び、空欄も *.rds 0 件も NG(data_manager.py:569-578)なので赤いエラー表示は出る。しかし preflight_validation は表示専用の別コールバックで、本実行コールバックはその結果を一切参照しないため解析はそのまま走る。警告は出るが止まらない、が正確。(2)『DESI 再解析にも波及するのでは』→ 波及しない。ver3:469 が resume_dir_path = NULL を渡すので ver3:342 の条件が偽になり置換されず、かつ ver3:93 V8_RESUME_FROM_RDS <- FALSE は Python から注入されないため v16 コピーは RESUME_FROM_RDS <- FALSE で走り直書きパスは無害。本件は本解析経路(generate_v8_config)限定と確定した。

**根拠**

- `App/app/callbacks/analysis_callbacks.py:528: "resume_from_rds": bool(resume_rds),
App/app/callbacks/analysis_callbacks.py:529: "resume_rds_paths": [],`
- `App/app/callbacks/analysis_callbacks.py:579: if resume_rds and rds_folder and not downstream_mode:
App/app/callbacks/analysis_callbacks.py:581: params["resume_rds_paths"] = [str(f) for f in rds_files]`
- `App/app/services/analysis_runner.py:484: if params.get("resume_from_rds") and params.get("resume_rds_paths"):
App/app/services/analysis_runner.py:486: lines = _replace_assign(lines, "RESUME_DIR_PATH", _r_str(rds_dir))`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:163: RESUME_DIR_PATH <- "C:\\Users\\Cciia\\Biochem Dropbox\\Biochem's shared workspace\\Workspace\\UMAP\\DESI\\250924_Kizu_Dev_Brain\\250924_Kizu_Dev_Brain20251028"`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2335: if (RESUME_FROM_RDS && (is.null(RESUME_DIR_PATH) || RESUME_DIR_PATH == "")) {
App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2339: if(!dir.exists(RESUME_DIR_PATH)) dir.create(RESUME_DIR_PATH, recursive=TRUE, showWarnings=FALSE)`
- `App/app/services/analysis_runner.py:1109: cwd=str(Path(script_path).parent),`
- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:468: resume_from_rds = V8_RESUME_FROM_RDS,
App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:469: resume_dir_path = NULL`
- `[実行] scratchpad/tmp/w3s_resume.py: out_DESI/log/v8_runtime_*.R L159 RESUME_FROM_RDS <- TRUE / L163 RESUME_DIR_PATH <- "C:\\Users\\Cciia\\..." (直書きが残存)。out_TIMS も L193 TRUE / L199 直書き残存`

**修正方針**

analysis_runner.py:484 の条件から `and params.get("resume_rds_paths")` を外し、resume_from_rds が真なら常に RESUME_DIR_PATH を注入する(空のときは _r_str("") を入れて TIMS ver6:2335 のフォールバックが意図どおり RDS_SAVE_DIR に倒れるようにする)。併せて 260623_DESI-UMAP_Template_v16.R:163 と 260623_DBSCAN_With_cluster_ver6_no-png_slim.R:199 の直書き値を "" に置き換える。Linux では注入前後どちらも file.exists() が FALSE のままなので数値は不変(mechanical)。あわせて preflight_validation の結果を本実行コールバックのゲートにするか、少なくとも resume_rds ON + RDS フォルダ未指定を実行時に弾くこと。なお DESI v16 には ver6:2335 相当のフォールバックが無いため、DESI の途中再開を実際に効かせるには RDS_SAVE_DIR へ倒す実装を別途足す必要があるが、本件の必須修正ではない。


### 11. [S2] DESI 再解析には入力正規化ポリシー (INPUT_NORMALIZED / NORM_MODE) の伝播経路が存在せず、正規化済み入力に二重正規化がかかる（TIMS には V13_ 経路があり非対称）

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**／**該当**: `App/app/callbacks/analysis_callbacks.py:779`

**利用者から見た症状**

DESI の本解析で「正規化 OFF(SCiLS RMS 等で正規化済み)」を選んで実行し、そのクラスタを絞り込んで再解析すると、再解析だけが勝手に「正規化 ON」相当の処理に戻ってしまう。画面にも解析ログにも一切表示されず、エラーも警告も出ないため、利用者は同じ前処理で再解析したと信じたまま、実際には違う前処理の結果を見ることになる。さらに再解析側の解析条件記録(Methods 用の記録)は正規化の欄が「未記録」になるので、後から見返しても食い違いに気づけない。

**科学的影響**

親ラン(本解析)と子ラン(再解析)で data layer が変わるため、ScaleData → PCA → UMAP → クラスタリング → DEG の全下流が食い違う。合成 MSI データでの定量では、RMS 出力が O(100) 以上のスケールならクラスタは保たれるが DEG が変わる(有意 DEG の Jaccard 0.962、avg_log2FC の符号反転 5/500、代表マーカー上位 20 の一致 18/20)。SCiLS が RMS=1 に揃える一般的設定(値 O(1))では、×1e4 で約 45 倍に持ち上げられ log1p の圧縮域が移動するため、クラスタ構造そのものが崩壊する(ARI 本解析 vs 再解析 ≈ 0、DEG Jaccard 0.80-0.86)。この場合「クラスタを絞り込んで細かく見る」という再解析の前提が壊れ、UMAP_Merge_Clusters_ver1.R が再解析のサブクラスタラベルを元 UMAP 座標へ重ねる操作も意味を失う。論文の Methods には親ランの正規化しか書けないため、実際の前処理と記載が乖離する。

**検証の経緯(反証の試みと結果)**

【本件を統合案件の正本とする】R11-N2 / R12-N3 / R13-08 の 3 件は「DESI 再解析に INPUT_NORMALIZED / NORM_MODE の伝播経路が無い」という同一の実装欠落であり、1 件に統合して計上する。証拠が最も具体的で TIMS 側 V13_ 経路との非対称性という原因まで到達している R12-N3 を正本とした。反証を 3 方向から試みたがすべて失敗した。(1)『analysis_runner.py:763 の if "input_normalized" in params は TIMS 判定ではないので DESI でも効くのでは』→ desi_cluster_filter 分岐が組む params を実際に構築して確認したところキー自体が存在せず(実行: 'input_normalized' in params -> False)、仮に入れても注入先は V13_INPUT_NORMALIZED 固定で ver3 に宣言が無く _replace_assign が警告して素通りする。(2)『v16 コピーの元は本解析の注入済みランタイムコピーでは』→ main_analysis_script_path は desi_v8_script or DESI_V8_TEMPLATE_PATH(analysis_callbacks.py:748)、すなわち UI 設定のテンプレ本体であり注入済みコピーではない。(3)『provenance/receipt が親ランから継承するのでは』→ 復元結果は None(実行確認)。決定的な実行証拠として generate_cluster_filter_config() を直接呼び、DESI 再解析ランタイムには INPUT_NORMALIZED / NORM_MODE の出現が 0 件、同条件の TIMS では V13_INPUT_NORMALIZED <- TRUE / V13_NORM_MODE <- "log1p" が注入されることを確認した。主張の機序記述(相対化 ×1e4 → log1p)も正確である。

**根拠**

- `App/app/callbacks/analysis_callbacks.py:773: if analysis_type == "tims_cluster_filter":`
- `App/app/callbacks/analysis_callbacks.py:779: params["input_normalized"] = (normalize_input_reanalysis == "OFF")`
- `App/app/services/analysis_runner.py:763: if "input_normalized" in params:
App/app/services/analysis_runner.py:765: lines, "V13_INPUT_NORMALIZED",`
- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:355: code <- replace_assign_line(code, "DEG_P_THRESH_VAL", as.character(V8_DEG_P_THRESH_VAL))
App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:356: code <- replace_assign_line(code, "DEG_LOGFC_TH_VAL", as.character(V8_DEG_LOGFC_TH_VAL))`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:71: INPUT_NORMALIZED <- FALSE`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:117: s <- NormalizeData(s)
App/Script/DESI/260623_DESI-UMAP_Template_v16.R:118: s@misc$preprocessing_method <- "LogNormalize"`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:902: code <- replace_assign_line(code, "INPUT_NORMALIZED", if (isTRUE(V13_INPUT_NORMALIZED)) "TRUE" else "FALSE", multiple = TRUE)`
- `[実行] scratchpad/tmp/w3s_norm.py: DESI 再解析ランタイム cluster_filter_runtime_*.R 内の INPUT_NORMALIZED/NORM_MODE 出現 = 0 件 / 同条件 TIMS = L191 V13_INPUT_NORMALIZED <- TRUE, L192 V13_NORM_MODE <- "log1p" / provenance 復元 preprocessing = None`

**修正方針**

(1) analysis_callbacks.py の再解析分岐で、tims_cluster_filter 限定になっている params["input_normalized"] / params["norm_mode"] の設定を desi_cluster_filter にも広げる。(2) analysis_runner.py の再解析注入で、テンプレが DESI(ver3)なら V8_INPUT_NORMALIZED / V8_NORM_MODE へ注入するよう _is_tims_cf と同じ分岐を追加する(DEG 閾値が既に V13_/V8_ で分岐しているのと同じ形)。(3) DESI_RDS_ClusterFilter_ver3.R に V8_INPUT_NORMALIZED / V8_NORM_MODE を宣言し、make_v8_copy_with_settings(337-356)で v16 コピーの INPUT_NORMALIZED / NORM_MODE を replace_assign_line する(TIMS ver18:901-905 と同型)。(4) settings_tab.py の再解析正規化欄を tims_reanalysis_ion_settings の外へ出し、DESI 再解析でも表示する。(5) provenance に記録して Methods に出す。既存の「正規化 OFF で本解析した DESI データ」の再解析結果は数値が変わるので、利用者への周知と再実行の判断が必要。


### 12. [S2] 再解析の Annotation（切片）フィルタが R に一度も注入されない完全な no-op

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**／**該当**: `App/app/services/analysis_runner.py:628`

**利用者から見た症状**

再解析画面の「Annotation（切片）選択」でチェックを外しても、その切片のスポットが再解析にそのまま含まれます。画面上は正常に完了し、エラーも警告ログも一切出ないため、外したつもりの切片が入っていることに気づく手段がありません。さらに、自動生成される Methods 文には「対象セクションは（外したはずのリスト）とした」と、実際とは違う内容が書かれます。

**科学的影響**

意図的に除外した切片のスポットが再解析の Seurat オブジェクトに入り、HVG 選択・PCA・統合・クラスタリング・DEG の全段に伝播する。誤差は「対象集団が想定より広い」という形で現れるため、クラスタ数やマーカーの順位が変わりうる。加えて Methods 自動生成が実施していない絞り込みを事実として記載するため、論文の材料と方法が実データと食い違う（この点だけを見れば S1 相当の帰結）。ただし初回解析で外した切片が復活するわけではない（エクスポートは RDS に残った id しか書き出さないため）ので、影響は「再解析時の追加絞り込みが効かない」ケースに限定される。

**検証の経緯(反証の試みと結果)**

反証を4方向から試みたが全て失敗。(1)ver18の4段目(872-935)で拾われるか → Rscriptで make_v13_copy_with_settings を隔離実行し、生成された ver6 コピーが ANNOTATION_FILTER <- NULL のままであることを実測。(2)別名(V13_ANNOTATION_FILTER)注入 → 全文検索で存在せず。(3)env経由 → _env_extra は ANNOT_ADDUCTS のみ(analysis_callbacks.py:817-818)。(4)Storeが既定Noneで無害 → 否、file_handlers.py:439 が全annotationをStoreに入れるためTIMS再解析では毎回 params に載る。実際に params['annotation_filter'] を入れて generate_cluster_filter_config を実行したところ、生成された cluster_filter_runtime_*.R に ANNOTATION_FILTER の行は一切現れず、_replace_assign を呼んでいないため ver55.0 で追加された『変数が無い』警告も出ない(完全無言)。なお ver18:602 のエクスポートは元 parquet の全列を行サブセットするだけで annotation 列を保持するため、R側の受け皿は実在する(仕組みは在るのに配線が無い)。既存の test_r_injection_completeness.py は『_replace_assign の呼び出し先が存在するか』を全数照合する設計上、『呼び出しが無いこと』は原理的に検出できず、実行しても素通りする(15 passed)。

**根拠**

- `App/app/callbacks/analysis_callbacks.py:770:                 params["annotation_filter"] = annotation_filter_reanalysis_data`
- `App/app/services/analysis_runner.py:628: def generate_cluster_filter_config(params: dict, output_dir: str) -> str:  ← 628-821 の全文に ANNOTATION_FILTER への _replace_assign は無い(実行検証: 生成物に ANNOTATION_FILTER 行なし・警告なし)`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:872:   code <- replace_assign_line(code, "OUTPUT_DIR",   r_str(output_dir))  ← 872-935 の再注入19行に ANNOTATION_FILTER は無い(Rscript 実行で ver6 コピーが NULL のままと確認)`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:221: ANNOTATION_FILTER <- NULL`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:927:     if (!is.null(ANNOTATION_FILTER) && length(ANNOTATION_FILTER) > 0 &&  ← 受け皿は実在(既定NULLなので素通り)`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:602:     tab2 <- tryCatch(tab[keep_flag, ],  ← 全列の行サブセット＝annotation 列は保持される`
- `App/app/callbacks/file_handlers.py:439:     return ui, sorted(set(all_annotations))  ← 既定で全切片が Store に入るため毎回 params に載る`
- `App/app/callbacks/analysis_callbacks.py:892:                 "annotation_filter": params.get("annotation_filter"),  ← 効いていない値が analysis_params.json に保存される`

**修正方針**

ver18 に定数 V13_ANNOTATION_FILTER <- NULL を追加し、make_v13_copy_with_settings に『exists() かつ length>0 なら replace_assign_line(code, "ANNOTATION_FILTER", …)』を1行足す。generate_cluster_filter_config 側は params['annotation_filter'] があれば c("…") 形式で V13_ANNOTATION_FILTER に注入する。V13_NORM_MODE / V13_ANNOTATION_ROLE 等で同一の4段パターンが既に9件動いており、設計判断は不要。DESI 再解析には切片フィルタ UI 自体が無い（file_handlers.py:401-402 で tims_cluster_filter 以外は空を返す）ので対象外。


### 13. [S2] 再解析のサンプル選択チェックボックスが読まれず、フォルダ全件が対象になる

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**／**該当**: `App/app/callbacks/analysis_callbacks.py:718`

**利用者から見た症状**

再解析画面の「チェックを入れたサンプルが再解析対象になります」というチェックボックスが機能しません。特定のサンプルのチェックを外しても、そのサンプルが 1 回目の解析に含まれていれば再解析にもそのまま入ります。エラーも警告も出ないため、除外できたと誤解します。同じ見た目のチェックボックスが通常解析では正しく効くので、余計に気づきにくい状態です。

**科学的影響**

除外したはずのサンプルが ORIGINAL_INPUT_PATHS（DESI では SAMPLE_NAMES）経由で再解析に入り、統合対象・バッチ構成が意図と異なる。サンプル数が変われば単一サンプル判定と複数サンプル統合（Harmony/RPCA）の分岐条件まで変わりうるため、バッチ補正の有無そのものが想定と食い違う可能性がある。また analysis_params.json に記録される『対象サンプル』はフォルダの中身そのものなので、実際には R 側で skip されたサンプルまで解析対象として Methods に載る（n の誤記載）。

**検証の経緯(反証の試みと結果)**

反証を3方向から試みたが全て失敗。(1)Store 同期があるのでは → selected_samples には sync_selected_samples(file_handlers.py:199-203)があるが、selected_samples_reanalysis には同期先 Store が存在しない。全文検索での出現は生成箇所(file_handlers.py:377)と annotation セレクタの Input(同:390)の2箇所のみ。(2)run_analysis の State に入っているのでは → State 一覧(analysis_callbacks.py:296-343)を全数確認し、annotation_filter_store_reanalysis は在るが selected_samples_reanalysis は無いことを確認。(3)R 側で RDS と突合して勝手に絞られるので無害では → 部分的にしか成立しない。RDS に残っていないサンプルは skip される(ver18:1227-1230 / ver3:421-424)が、RDS に居るサンプルは利用者がチェックを外しても必ず対象に入る。加えて実行検証で判明した精密化: TIMS では params['sample_names'] の注入先 SAMPLE_NAMES が ver18 に存在せず、_replace_sample_names_block は不一致時に警告も出さず lines を返す(analysis_runner.py:228-229)ため二重に無言。実際の対象は ORIGINAL_INPUT_PATHS(フォルダ全件)のみで決まる。DESI では SAMPLE_NAMES が実際に注入され(生成物差分で "S1","S2" への置換を確認)、ver3:416 の書き出しループを直接駆動する。

**根拠**

- `App/app/layouts/settings_tab.py:661:                             dbc.FormText("チェックを入れたサンプルが再解析対象になります")`
- `App/app/callbacks/file_handlers.py:377:         id="selected_samples_reanalysis",  ← この値の読み手は file_handlers.py:390 の annotation セレクタのみ、Store 同期も無し`
- `App/app/callbacks/analysis_callbacks.py:328:      State("selected_samples_store", "data"),  ← 本解析用のみ。State 一覧(296-343)に selected_samples_reanalysis は無い`
- `App/app/callbacks/analysis_callbacks.py:718:                 sample_names = list_tims_files(reanalysis_data_folder)`
- `App/app/callbacks/analysis_callbacks.py:776:                 params["original_input_paths"] = build_tims_input_paths(src_folder)  ← どちらもフォルダ全件`
- `App/app/services/analysis_runner.py:228:     if start_idx is None:
        return lines  ← ver18 に SAMPLE_NAMES が無いため TIMS では警告すら出ず素通り`
- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:416: for (sn in SAMPLE_NAMES) {  ← DESI は注入された SAMPLE_NAMES(フォルダ全件)が書き出しループを駆動`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1227:   if (nrow(rows_sn) == 0) {  ← RDS に居ないサンプルは skip されるが、居るサンプルは外せない`

**修正方針**

本解析と同型に、selected_samples_reanalysis → dcc.Store(selected_samples_reanalysis_store) の同期コールバックを file_handlers.py に追加し、run_analysis の State に加える。再解析ブロック(analysis_callbacks.py:716-721, 776)で sample_names と original_input_paths をそのチェック結果で絞り込む（TIMS は build_tims_input_paths の結果を Path(p).stem で filter、DESI は list_msi_files の結果を filter）。本解析側の599-603 に既に同じ絞り込みコードがあるので、設計判断は不要。


### 14. [S2] DESI 再解析は正規化ポリシーを継承せず、常に LogNormalize になる

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**／**該当**: `App/app/callbacks/analysis_callbacks.py:779`

**利用者から見た症状**

R12-N3 と同一。DESI 本解析を「正規化 OFF」で回した後の再解析だけが黙って「正規化 ON」相当に戻る。画面にもログにも差異が出ない。

**科学的影響**

R12-N3 と同一。親ランと子ランで前処理が変わり、抽出クラスタのサブクラスタ構造が親ランの UMAP と比較できなくなる。UMAP_Merge_Clusters_ver1.R は再解析のラベルを元 UMAP 座標に重ねる設計なので、統合ラベルの意味も損なわれる。

**検証の経緯(反証の試みと結果)**

【R12-N3 と同一問題。統合先 R12-N3、報告書では単独計上しないこと】主張の中核「input_normalized / norm_mode は analysis_type == "tims_cluster_filter" のブロック内でのみ params に設定される」「ver3 の 337-356 にも INPUT_NORMALIZED / NORM_MODE の再注入は無い」「DESI 再解析は v16 既定 INPUT_NORMALIZED=FALSE に戻り NormalizeData が走る」はすべて実行で確認した(生成された DESI 再解析ランタイムに INPUT_NORMALIZED/NORM_MODE の出現 0 件、ver3 全 537 行に grep ヒット 0 件)。反証は成立しなかった。ただし科学的影響の記述「RMS×TIC の二重正規化」は機序としてやや不正確で、numpy 検証によれば支配項は TIC 除算ではなく Seurat LogNormalize の scale.factor=1e4 による再スケーリングである(TIC 除算のみならクラスタは保たれる)。この補正は結論を弱めず、むしろ強める(RMS 出力が O(1) のときクラスタが総入れ替わる)。また「マージスクリプトは両者の UMAP を重ねる前提」という指摘は UMAP_Merge_Clusters_ver1.R が再解析のサブクラスタラベルを元 UMAP 座標空間へ写像する実装であることから正しい。

**根拠**

- `App/app/callbacks/analysis_callbacks.py:773: if analysis_type == "tims_cluster_filter":
App/app/callbacks/analysis_callbacks.py:779: params["input_normalized"] = (normalize_input_reanalysis == "OFF")`
- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:337: code <- replace_assign_line(code, "data_folder", r_str(data_folder))
App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:356: code <- replace_assign_line(code, "DEG_LOGFC_TH_VAL", as.character(V8_DEG_LOGFC_TH_VAL))`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:71: INPUT_NORMALIZED <- FALSE`
- `App/Script/Common/UMAP_Merge_Clusters_ver1.R:7: #   クラスタ抽出（keep）→ 再UMAP 後のサブクラスタラベルを
App/Script/Common/UMAP_Merge_Clusters_ver1.R:8: #   元の UMAP 座標空間にマッピングして統合表示する。`
- `[実行] scratchpad/tmp/w3s_norm.py: DESI 再解析ランタイムの INPUT_NORMALIZED/NORM_MODE 出現 = 0 件`
- `[実行] scratchpad/tmp/mech.py: log1p(x/TIC*平均TIC)(TIC 除算のみ)は ARI 対真値 1.0000 を保つが、log1p(x*44.8)(倍率のみ)は -0.0005。支配項は ×1e4`

**修正方針**

R12-N3 の fix_sketch と同一。重複のため単独の修正項目は立てない。


### 15. [S2] 再解析には UMAP/クラスタリング ハイパーパラメータの注入経路が存在しない

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**／**該当**: `App/app/services/analysis_runner.py:628`

**利用者から見た症状**

PreFlight の「③ 推奨値を入力欄へ反映」で n.neighbors・dims を最適値に設定しても、再解析（クラスタ絞り込み）ではその値が一切使われず、常に既定値（n.neighbors=30 / min.dist=0.3 / metric=cosine / dims=30 / k.param=20 / resolution=0.5）で計算されます。画面の説明文は「次回の『解析実行』に反映されます」としか書いておらず、再解析では効かないことがどこにも示されていないため、反映されたと信じたまま結果を解釈することになります。

**科学的影響**

PreFlight で近傍数・次元数を最適化した意味が再解析で失われ、初回と再解析で近傍グラフの構成が変わる。クラスタ抽出後のサブクラスタ数・境界はこれらのパラメータに強く依存するため、「同じ設定のまま部分集合を絞り込み直した」という前提が成立せず、初回クラスタと再解析サブクラスタの対応づけ（マージスクリプトによるサブクラスタ統合）の解釈も揺らぐ。さらに ver18 のパッチが Step2 の HP を Seurat 既定に固定する一方 Step3(RPCA) は定数を参照するため、Python 側だけを修正すると Step2 と Step3 で異なる HP が使われる不整合が新たに生じる。

**検証の経緯(反証の試みと結果)**

Python 側・R 側の両方を実行して確認した。(1) generate_cluster_filter_config に params['umap_n_neighbors'] 等をすべて入れて実行したところ、生成された cluster_filter_runtime_*.R には UMAP_N_NEIGHBORS / UMAP_MIN_DIST / UMAP_METRIC / UMAP_DIMS_N / CLUSTER_K_PARAM が 1 行も現れず、警告も出なかった（_hp_int/_hp_num/_hp_str は generate_v8_config:446-470 にしか無い）。(2) ver18 の make_v13_copy_with_settings を Rscript で隔離実行し、ver6 コピーが UMAP_N_NEIGHBORS=30L / UMAP_MIN_DIST=0.3 / UMAP_METRIC="cosine" / UMAP_DIMS_N=30L / CLUSTER_K_PARAM=20L / CLUSTER_RESOLUTION=0.5 とテンプレ既定のままであることを確認。(3) DESI 側 ver3 の make_v8_copy_with_settings(256-377)の再注入は data_folder / output_dir / PROJECT_NAME_PREFIX / RESUME_* / PIPELINE_STAGE / DEG 閾値 / sample_names のみで、UMAP HP は無い。反証はできなかった。むしろ主張を強化する事実を発見した: ver18 の patch_v13_step2_pipeline は ver6 の run_pipeline を丸ごと差し替えるが、差し替え後の RunUMAP / FindNeighbors は n.neighbors / min.dist / metric / k.param を一切渡さず(766-770)、FindClusters は algorithm=4 をハードコードする(772)。したがって TIMS 再解析の Step2 は定数を注入しても効かず Seurat の関数既定に固定される（偶然 ver6 の既定と一致するため現状は数値差にならない）。一方 Step3(RPCA) 側(ver6:2767-2770)は定数を参照するため、Python だけ直すと Step2 と Step3 で HP が食い違う新たな不整合を生む。

**根拠**

- `App/app/services/analysis_runner.py:446:     _hp_int = {
        "umap_dims_n": "UMAP_DIMS_N", "umap_n_neighbors": "UMAP_N_NEIGHBORS",  ← generate_v8_config 内のみ`
- `App/app/services/analysis_runner.py:628: def generate_cluster_filter_config(params: dict, output_dir: str) -> str:  ← 628-821 に _hp_* 相当が無い(実行検証: params に umap_* を入れても生成物に一切現れず警告も出ない)`
- `App/app/callbacks/analysis_callbacks.py:537:             if umap_n_neighbors_input is not None:
                params["umap_n_neighbors"] = int(umap_n_neighbors_input)  ← analysis_type in (desi_v8, tims_v8) の分岐内のみ。再解析ブロック(735-811)には無い`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:769:         '      s <- RunUMAP(s, reduction = "pca", dims = dims_use) %>%',
        '        FindNeighbors(reduction = "pca", dims = dims_use)',  ← 差し替え後は HP 定数を渡さない`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:772:         '    FindClusters(s, resolution = CLUSTER_RESOLUTION, algorithm = 4)',  ← CLUSTER_ALGORITHM を無効化`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2570:     s <- RunUMAP(s, reduction=red_use, dims=1:cfg$umap_dims,
                 n.neighbors=UMAP_N_NEIGHBORS, min.dist=UMAP_MIN_DIST,  ← 本体は定数を渡す(パッチで消える)`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:440: UMAP_N_NEIGHBORS  <- 30L          # Seurat RunUMAP 既定  ← 再解析コピーはこの既定のまま(Rscript 実測)`
- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:337:   code <- replace_assign_line(code, "data_folder", r_str(data_folder))  ← 337-356 の再注入一覧に UMAP HP は無い`

**修正方針**

3 箇所を揃えて直す必要がある: (1) generate_cluster_filter_config に V13_UMAP_*/V13_CLUSTER_* への注入を追加、(2) ver18 の make_v13_copy_with_settings に対応する replace_assign_line を追加、(3) ver18 の patch_v13_step2_pipeline が生成する RunUMAP/FindNeighbors/FindClusters に n.neighbors=UMAP_N_NEIGHBORS, min.dist=UMAP_MIN_DIST, metric=UMAP_METRIC, k.param=CLUSTER_K_PARAM, annoy.metric=CLUSTER_METRIC, algorithm=CLUSTER_ALGORITHM を渡す。直すと既定以外の HP を入れている利用者の数値が変わるため承認が要る。暫定の mechanical 対応としては、再解析モードでは HP 入力欄を非活性化するか、settings_tab.py:1072-1078 の説明文に「再解析には反映されません」を明記する。


### 16. [S2] 単一サンプル実行(既定シナリオ)は公称 3000/30/30 でなく 1000HVG/20PC/20dims で走り、UI の dims=30 だけが反映されない

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**／**該当**: `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:457`

**利用者から見た症状**

ファイルを1つだけ指定して TIMS 解析を実行すると、画面の dims 欄に 30 と入っていても実際には 20 次元で計算されます。29 や 31 に変えるとちゃんと反映されるのに、既定値の 30 のときだけ無視されます。使われる特徴量の数も、スクリプトに書かれた 3000 ではなく 1000 です。それでも結果に付く記録(レシート)と自動生成される Methods 文には「30 次元」と書かれます。

**科学的影響**

入力 parquet が1ファイルの TIMS 解析(既定3シナリオ)は、定数宣言・UI・receipt・Methods がいずれも 3000HVG/30PC/30dims を主張しているのに、実体は 1000HVG/20PC/20dims で HVF 選択・PCA・UMAP・クラスタリング・マーカー抽出が行われる。特徴量が 1/3、次元が 2/3 なので、クラスタ数・境界・マーカー順位はすべて変わりうる。さらに receipt.py:161 と runtime_script.py:37 が焼き込み定数 UMAP_DIMS_N=30 を読むため、methods_text.py:761 が生成する論文用 Methods に『入力次元数 = 30』と書かれる。実行内容と公表内容が恒常的に食い違う(再現性の主張が成り立たない)。dims を 29 や 31 にすると反映されるのに 30 だけ効かないという挙動は、利用者が『設定が効いている』と誤認する原因にもなる。

**検証の経緯(反証の試みと結果)**

反証を4方向試みたがすべて不成立。(1)入力1ファイルなら seu$sample は1水準(ver6:2393)で .bv_levels=1 → group_var=NA(ver6:2539)。既定シナリオ within_slice/condition_compare/serial_section はいずれも BATCH_VAR='sample'(analysis_callbacks.py:95-100)なので必ず PCA 経路に落ちる。(2)dims=30 は UI 既定(settings_tab.py:1063-1064)→ params['umap_dims_n'](analysis_callbacks.py:543-544)→ _replace_assign で UMAP_DIMS_N <- 30L(analysis_runner.py:462-464)と常時注入され、ver6:466 の `!= 30L` が偽で override 不発。(3)後段で 3000/30/30 に戻る経路は無い: Step1 の N_VAR_FEATURES=3000(ver6:2436)は run_pipeline 内の FindVariableFeatures(s, nfeatures=cfg$n_var_features)(ver6:2550)で上書きされ、MAX_PCS=30 は RPCA(Step3)専用、ver4 にあった救済ループ c(PCA_RETRY_GRID, HARMONY_RETRY_GRID) は ver6 に存在しない(FAILSAFE_ENABLE は定義のみで未使用)。(4)『そういう仕様』ではなく回帰である: ver17:1895 では Harmony ループが無条件で回り PCA_RETRY_GRID は『3000/30/30 で落ちた後のフォールバック』だった。ver4(260525:2166)で `if (!is.na(group_var))` が入り、フォールバック用グリッドが単一サンプルの主経路に昇格した。中身は ver17 から未変更。ver6:431-479 を逐語再現した実測で 30 だけが死角になることも確認。

**根拠**

- `/home/user/U_Analysis/App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:456-459: PCA_RETRY_GRID <- list( list(n_var_features = 1000, max_pcs = 20, umap_dims = 20), list(n_var_features = 500, max_pcs = 15, umap_dims = 15) )`
- `/home/user/U_Analysis/App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:466: if (is.numeric(UMAP_DIMS_N) && UMAP_DIMS_N > 0L && UMAP_DIMS_N != 30L) {`
- `/home/user/U_Analysis/App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2589-2590: if(is.null(seu_harmony)) { / for (cfg in PCA_RETRY_GRID) {   (単一 sample は必ずこの経路)`
- `/home/user/U_Analysis/App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2539: group_var  <- if (.bv_levels > 1 && !.bv_is_bio) .bv else NA_character_`
- `/home/user/U_Analysis/App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2393: seu$sample <- sn; ...   (入力 parquet 1 ファイル = sample 1 水準)`
- `/home/user/U_Analysis/App/Script/TIMS/260308_DBSCAN_With_cluster_ver17.R:1895: for (cfg in HARMONY_RETRY_GRID) {   (旧版は無条件。PCA_RETRY_GRID は本来フォールバック)`
- `/home/user/U_Analysis/App/Script/TIMS/260525_DBSCAN_With_cluster_ver4_no-png_slim.R:2166: if (!is.na(group_var)) {   (ここでフォールバック用グリッドが単一サンプルの主経路に昇格)`
- `/home/user/U_Analysis/App/app/layouts/settings_tab.py:1063-1064: dbc.Input(id="umap_dims_input", type="number", value=30, min=2, max=50, step=1, size="sm"),`

**修正方針**

(a) PCA_RETRY_GRID の先頭に list(n_var_features=3000, max_pcs=30, umap_dims=30) を追加して Harmony グリッドと同じ出発点にする、または (b) ver6:466 の override 条件から `!= 30L` を外し、あわせて n_var_features も追随させる(現行の .apply_ud は n_var_features を一切触らないので dims だけ直しても HVG は 1000 のまま)。どちらも HVG 1000→3000・PC/dims 20→30 と結果数値が変わるため利用者承認が必要。応急措置として、R 側で『実際に採用された tier』(cfg の3値)をサイドカーに書き出し、receipt/Methods が宣言値でなく実測値を載せるようにすべき。


### 17. [S2] リトライ劣化(3000/30/30→1000/20/20→500/15/15)は発生してもログ以外どころかログにも残らない。tryCatch(error=function(e) F

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・数値不変

**利用者から見た症状**

大きなデータでメモリが足りずに 1 回目の計算が失敗すると、アプリは黙って軽い設定（可変遺伝子 3000→1000→500、主成分 30→20→15、UMAP 次元 30→20→15）に落として計算をやり直す。しかし「落とした」ことは画面にもログにも一切出ず、あとから受領書（receipt.json）や Methods 下書きを見ても最初に指定した設定値（例: 次元数 30）が書かれている。同じデータを同じ設定で流したはずなのに結果が違う、という現象が起きても原因を追えない。

**科学的影響**

tier2/3 に落ちた実行は 1000 または 500 個の可変特徴量・20 または 15 次元で UMAP とクラスタリングが計算されるため、tier1 で通った実行とはクラスタ数・クラスタ境界・マーカーが系統的に異なる。にもかかわらず receipt と Methods は dims=30・HVG 3000 相当の構成値を報告するので、論文の Methods に実際とは異なるハイパーパラメータが載る。同一設定での再現実験も成立しない。数値そのものは（そのとき使われた設定に対しては）正しいので、記録を直しても解析結果は変わらない。

**検証の経緯(反証の試みと結果)**

『実は痕跡が残る』方向で3系統の反証を試み、いずれも主張を覆せなかった。(1) ログ: retry.R で :2582-2594 の構造を再現。ok <- tryCatch({...}, error=function(e) FALSE) はエラー文言ごと完全に破棄し、ループ内に cat/message は 1 行も無い（:2582 の cat("Preprocessing ...") はループ外で 1 回のみ）。試行番号も採用 cfg も出力されない。(2) 成果物: R サイドカー(rds_io.R:326-337)・receipt.py:161・runtime_script.py:37 の全経路を追跡し、いずれも構成値のみで実効値は無し。extract_seurat_data.R:255 の n_features は rownames(expr_data)（:176、全 feature 数）で HVG 数ではない。App 全体に n_var_features/nfeatures を扱うコードは 0 件。部分的な反証として run_diagnostics.R:140 が diagnostics.json に n_dims = ncol(emb) を書くため採用 max_pcs は復元しうるが、これは「PreFlight 診断」を別途手動実行したときだけ生成され、レシート/Methods には流れず、HVG 数と UMAP dims は判らないので主張は維持。(3) UI dims による回避: retry.R で :466-479 の .apply_ud を忠実に再現したところ、UI dims=30(既定)では grid は 30/20/15 のまま、dims=15 以下なら全 tier 同一になる。ただし UI 既定は 30(settings_tab.py:1064 value=30)であり、n_var_features(3000/1000/500) と max_pcs(30/20/15) は UI dims に関係なく必ず劣化し記録経路が存在しない。以上より UPHELD。発火は tier1 が R レベルのエラーで失敗した場合に限られる（SIGKILL によるプロセス強制終了ではリトライされない）ため S1 には上げず S2 を維持。

**根拠**

- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2585: ok <- tryCatch({ seu_harmony <- run_pipeline(TRUE, cfg); TRUE }, error=function(e) FALSE) ← 失敗理由を破棄し cat も無い（:2591 の PCA 側も同形）`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:450-454: HARMONY_RETRY_GRID <- list(list(n_var_features=3000, max_pcs=30, umap_dims=30), list(1000,20,20), list(500,15,15))`
- `App/Script/helpers/rds_io.R:326-337: info <- list(r_version=..., clustering_resolution=..., norm_mode=..., batch_correction=..., threads=..., package_versions=...) ← n_var_features / max_pcs / umap_dims の実効値は無い`
- `App/app/services/receipt.py:161: "dims": params.get("umap_dims_n") ← UI 構成値のみ。App/app/services/runtime_script.py:37: ("UMAP_DIMS_N", "analysis.umap.dims") も焼き込み定数の読み取り`
- `App/Script/helpers/extract_seurat_data.R:176: features <- rownames(expr_data) ← :255 の n_features は全 feature 数で HVG 数ではない`
- `App/Script/helpers/run_diagnostics.R:140: rd <- list(reduction = red, n_dims = ncol(emb)) ← 実効 PC 数の唯一の利用者向け経路だが手動 PreFlight 診断専用で Methods には流れない`
- `App/app/layouts/settings_tab.py:1064: value=30, min=2, max=50, step=1 ← UI 既定が 30 のため .apply_ud(:466-479) が発火せずグリッドは 30/20/15 のまま`

**修正方針**

数値を変えずに直せる。(1) リトライループ内で採用した cfg と試行番号を出力する: for (i in seq_along(HARMONY_RETRY_GRID)) { cfg <- ...; ok <- tryCatch({...}, error=function(e){ message(sprintf("[retry] tier%d 失敗: %s", i, conditionMessage(e))); FALSE }); if (ok) { cat(sprintf("[retry] 採用 tier%d: HVG=%d PCs=%d dims=%d\n", i, cfg$n_var_features, cfg$max_pcs, cfg$umap_dims)); break } }。(2) 採用 cfg をグローバルに保持し rds_io.R の info に n_var_features_effective / max_pcs_effective / umap_dims_effective として追記。(3) receipt.py / methods_text.py はサイドカーの実効値があればそちらを優先し、構成値と食い違う場合は Methods に注記を出す。グリッドの値そのものの見直し（R3-N1）は別件で science-judgment。


### 18. [S2] ReUMAP置換: 無名ベクトルの文字添字で cell key が全NA化し、merge が NA 同士を総当り結合（OOM または無変更成果物）

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**／**該当**: `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1007`

**利用者から見た症状**

TIMS で「抽出 (keep)」再解析をすると、抽出したクラスタを細かく分け直した結果を元の UMAP に貼り戻す機能が働きません。データが小さめのときはエラーも警告も出ずに完走し、ログに「Saved: UMAP_replace_..._seurat_with_umap_replaced.rds」と表示されますが、中身は元の UMAP・元のクラスタと完全に同じで、貼り戻しは 1 ピクセルも行われていません（3_0 / 3_1 のような分割後ラベルが 1 つも付きません）。データが大きい（抽出クラスタが 1 万ピクセル級）と、再解析が全部終わったあとにメモリを食い尽くして固まる・落ちるようになり、ジョブがエラー終了します。

**科学的影響**

「クラスタ内の亜構造を再クラスタリングして元の UMAP 上に示す」という解析が実際には一度も行われていない。生成される RDS を『貼り戻し済み』と信じて図にすると『再クラスタリングしても亜構造は無かった』という誤った結論になり得る（ただし分割ラベルが皆無なので気付ける余地はある）。大規模データではさらに悪く、apply_reumap_replace が tryCatch されていない（ver18:1377）ため、ここで落ちると後段の正常動作するマージブロック（1401-1439）まで巻き添えで実行されず、結果フォルダもプロジェクト登録されない。修正すると初めて置換座標・置換ラベルが実体化するので affects_numbers=true。

**検証の経緯(反証の試みと結果)**

最有力の反証候補（ENABLE_REUMAP_REPLACE は文字列 'auto' なので isTRUE() が FALSE になり置換ブロックへ到達しないのでは）を潰した: .preflight_validate()（ver18:287）が 375-383 行で 'auto' を解釈し FILTER_MODE=='keep' なら ENABLE_REUMAP_REPLACE <<- TRUE に書き換え、この関数は 443 行で無条件に呼ばれる。トップレベル関数内の <<- は globalenv に効くため、keep では必ず 1341 行の isTRUE() を通る。RERUN_PIPELINE_STAGE も既定 'full'（ver18:135）で PreFlight① 以外は素通り。paste0 が names を落とすことを実測し（names(paste0(c(a=..),..)) は NULL）、ver18:995-996 の key が確実に無名であることを確定。base R で (a) 無名文字ベクトルへの文字添字は全 NA、(b) merge() は NA キー同士をマッチさせ 2x3→6 行のデカルト積を返す、を実証。さらに n=500/1000/2000/4000 で規模実測し、nrow(m) が厳密に n^2（16,000,000 行, 56.35 秒, m=854.9 MB, 1 行あたり約 56 byte）で伸びること、かつ全規模で置換ループ実行回数が 0 であることを確認した。外挿すると keep クラスタ 10,000 spot で 1e8 行 ≈ 5.2 GB・merge だけで約 6 分、30,000 spot で 9e8 行 ≈ 47 GB となり TIMS の実データ規模では OOM が現実的。S1 とせず S2 にしたのは、(i) 置換ラベルが 1 個も生成されないため成果物を開けば「効いていない」と判別できる、(ii) この RDS はビューアの RDS 探索（interactive_callbacks.py:458-483 の harmony/rpca/step2/step3/single マッチ）に引っかからずアプリ画面には出ない、(iii) 大規模側は loud fail、の 3 点による。

**根拠**

- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:995:   key <- paste0(sn, "|", si)  / :996:   key   （names 付与なしで return）`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1007:   emb$key <- .make_cell_key(obj, sample_name_map)[emb$cell]`
- `App/Script/Common/UMAP_Merge_Clusters_ver1.R:115:   names(key) <- rownames(md)   # セル名で名前付き（同一関数の別実装は正しく付けている＝非対称の証拠）`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1059:   m <- merge(base_sub, rer_um, by = "key", suffixes = c("_old", "_new"))`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:378:       ENABLE_REUMAP_REPLACE <<- TRUE   （keep では必ず TRUE 化。反証候補の否定）`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:443: .preflight_validate()   （無条件呼び出し）`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1090:   rer_sub_aligned$base_cell <- key_to_basecell[rer_sub_aligned$key]  / :1091:   rer_sub_aligned <- rer_sub_aligned[!is.na(rer_sub_aligned$base_cell), , drop=FALSE]`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1145:   save_rds_compact(seu2, file.path(out_dir, paste0(out_prefix, "_seurat_with_umap_replaced.rds")))`

**修正方針**

ver18:995-996 の .make_cell_key に names(key) <- rownames(md) を 1 行足す（同ファイル 1108/1115 行と Common ver1.R:115 が既に採っている書き方と揃えるだけで、設計判断は不要）。ただしこれ単独では R6-N2 のサフィックス不一致により merge が 0 行になり .stopif(nrow(m)>=3) で停止するため、N1 と N2 は必ず同時に修正すること。回帰テストとして、無名/命名済みキーで .get_umap_df 相当の突合を行い置換件数 > 0 を確認する R テストを追加するのが望ましい。


### 19. [S2] ReUMAP置換: rerun サンプル名の _KEEP_Cl_x suffix を吸収する .merge_sample_map を渡しておらず、対応付けが原理的に不成立

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**／**該当**: `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1381`

**利用者から見た症状**

R6-N1 と同じ「抽出 (keep) 再解析の貼り戻しが効かない」症状のもう一つの原因です。仮に R6-N1 だけを直しても、元データ側のピクセル名（例: 20250115_slice_A|101）と再解析側のピクセル名（20250115_slice_A_KEEP_Cl_8|101）が別物のままなので、対応が 1 件も取れず「対応セルが少なすぎます（n=0）」というエラーで、重い再解析が終わったあとに停止します。現状でも、分割後クラスタのラベル（3_0 / 3_1 など）が 1 つも付かない直接の原因になっています。

**科学的影響**

貼り戻し機能の第二の根本原因。単独でもラベル置換を完全に不成立にしており、修正すると初めて分割後クラスタラベルが元 UMAP 上に付与されるため成果物の数値（ラベル値・置換座標）が変わる。N1 と N2 の両方を直さない限り貼り戻し機能は動作し得ないので、修正は必ずセットで行う必要がある。

**検証の経緯(反証の試みと結果)**

反証を 3 系統試みてすべて不成立。(1) SAMPLE_NAME_MAP に有効値が入る別経路: ver18:99 で c() に初期化され、Python 側に SAMPLE_NAME_MAP を対象とする _replace_assign 呼び出しは存在しない。1381 行に渡るのは常に空。(2) 利用者が手で SAMPLE_NAME_MAP を設定すれば救えるのでは: 救えない。この変数は resolve_rds_samples_for_input(input_sn, ...)（1225）用に『サフィックス無しの入力名 → RDS sample 名』として定義される一方、.make_cell_key(rerun_seu, SAMPLE_NAME_MAP) が引くキーはサフィックス付きの rerun sample 名なので sample_name_map[sn] は NA を返し元の名前のまま残る。どう設定しても一致しない。(3) N1 に隠れて観測不能（重複主張）では: 否。上記のとおりラベル置換パスは names が正しく付いており N2 単独で成立する。base R で実際の命名（20250115_slice_A vs 20250115_slice_A_KEEP_Cl_8）を再現し、k %in% names(key_to_rercl) が全件 FALSE で置換ラベル数 0、.merge_sample_map を渡すと 3 件全一致することを実証した。ver6:2386-2393（sn <- file_path_sans_ext(basename(fp)); seu$sample <- sn）により rerun 側 sample が必ずサフィックス付きになることも確認済み。

**根拠**

- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1381:     sample_name_map = SAMPLE_NAME_MAP,   （既定 c() をそのまま渡す）`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1424:     SAMPLE_NAME_MAP    <- if (length(.merge_sample_map) > 0) .merge_sample_map else SAMPLE_NAME_MAP   （マージ側だけが正しい非対称）`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1272:     .merge_sample_map[rerun_sn] <- rs   （1381 行の時点で既に構築済みのグローバル）`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1260:   out_name <- paste0(input_sn, suffix, ".", out_ext)`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2386:     sn <- tools::file_path_sans_ext(basename(fp))  / :2393:     seu$sample <- sn; ...`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:1115:   base_key <- .make_cell_key(base_seu); names(base_key) <- rownames(md_base)   （ラベル経路は names が正しく付く＝N1 と独立）`
- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:99: SAMPLE_NAME_MAP <- c()   （Python 非注入。全リポジトリ grep で確認）`
- `実行実証 scratchpad/tmp/w6s_keys.R: 'k %in% names(key_to_rercl) : FALSE, FALSE, FALSE -> 置換されるラベル数 = 0' / '.merge_sample_map を渡した場合: 置換されるラベル数 = 3'`

**修正方針**

ver18:1381 の sample_name_map = SAMPLE_NAME_MAP を、1424 行と同一の式 sample_name_map = if (length(.merge_sample_map) > 0) .merge_sample_map else SAMPLE_NAME_MAP に置換する。.merge_sample_map は 1217 行で初期化され 1269-1273 のエクスポートループで構築済みなので、1381 行の時点で利用可能。R6-N1 の names 付与と同時に適用すること。


### 20. [S2] 選択DE(local): 選択と対象クラスタの重複ピクセルが無言で ident.2 側へ奪われる

**判定**: UPHELD(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**／**該当**: `App/Script/helpers/run_findmarkers.R:92`

**利用者から見た症状**

インタラクティブ画面で UMAP を投げ縄選択し、Locally モードで「比較対象クラスタ」を選んで差次発現を実行したとき、選んだ範囲と対象クラスタが重なっていると、重なった部分のピクセルが無言で比較対象（B）側に取られます。クラスタの中の一部だけを選んでそのクラスタ自身と比べる（最も自然な使い方）と、選択側が空になり『DE 失敗: ident.1 (A) has too few cells: 0』という意味の分からないエラーが出ます。投げ縄がクラスタの境界をまたいだ場合は、エラーも警告も出ないまま『選択 vs 指定群』と表示された結果が返りますが、実際に検定されているのは『選択のうちクラスタに入っていない部分 vs クラスタ全体』です。

**科学的影響**

Locally モードの avg_log2FC / p 値が、利用者が指定した対比とは異なる群で計算される。重なりの割合次第で効果量も有意性も変わるため、この結果を図表や解釈に使うと誤った対比を報告することになる。加えて群の割当てが groups_csv の行順という実装都合に依存しており（行順を変えるだけで n_a=2 が n_a=5 に反転することを実証）、科学的に定義された対比になっていない。修正には意味論の決定（B から A を除いて A vs B∖A にする／現状の A∖B vs B を明示表示する／重複があればエラーにする）が必要で、どれを選んでも結果数値が変わるため利用者の承認が要る。

**検証の経緯(反証の試みと結果)**

反証を 5 系統試みてすべて不成立。(1) UI が重複を防いでいるのでは: interactive_de.py:128 の cells_in_clusters は選択を除外せず（selection_utils.py:34-35 は該当クラスタの CellID を全部返す）、対象クラスタのドロップダウンも重なりを排除しない。(2) Python 側の去重: seurat_bridge.py:963-964 は rows_id = ident1_ids + ident2_ids の単純連結で去重なし。(3) R 側の去重: run_findmarkers.R:89-92 は match で添字を作り ident_vec[idx[keep]] <- groups$Group[keep] で代入するため、重複添字は R の仕様どおり後書き優先となり B が必ず勝つ。(4) 利用者への警告: 実際の A の大きさは R の stdout（131-132 行の cat）にしか出ず seurat_bridge は成功時 stdout を読まない。画面表示は interactive_de.py:153-155 の『完了: 選択 vs 指定群 — N features』で、実際の対比とずれたラベルが無言で出る。(5) 既存テスト: tests/test_selection_utils.py は cells_in_clusters の単体のみで、重複時の ident 割当てを検証するテストは存在しない。base R で 89-92 行をそのまま再現し、部分重複（選択 5 個中 3 個がクラスタと重複）で n_a が 5→2 に減ること、完全包含で n_a=0 となり 'ident.1 (A) has too few cells: 0' で停止することを実測した。S1 とせず S2 にしたのは、出力される統計量自体は『A∖B vs B』として数学的に妥当であり、誤っているのは群の定義とその表示ラベルだから。ただし部分重複時は完全に無言なので S1 隣接である。

**根拠**

- `App/Script/helpers/run_findmarkers.R:92: ident_vec[idx[keep]] <- groups$Group[keep]   （重複添字は後書き優先＝B が勝つ）`
- `App/Script/helpers/run_findmarkers.R:89: idx <- match(groups$CellID, all_cells)`
- `App/app/services/seurat_bridge.py:963:             rows_id = ident1_ids + ident2_ids  / :964:             rows_grp = ["A"] * len(ident1_ids) + ["B"] * len(ident2_ids)   （去重なし）`
- `App/app/callbacks/interactive_de.py:128:         ident2 = cells_in_clusters(df, target_clusters)   （選択の除外なし）`
- `App/app/utils/selection_utils.py:34:     cset = {str(c) for c in clusters}  / :35:     mask = df["Cluster"].astype(str).isin(cset)`
- `App/app/callbacks/interactive_de.py:153:     label = "選択 vs 全体" if mode == "global" else "選択 vs 指定群"   （実際の対比とずれた表示）`
- `App/Script/helpers/run_findmarkers.R:96: if (n_a < 3) stop("ident.1 (A) has too few cells: ", n_a)`
- `実行実証 scratchpad/tmp/w6s_keys.R: '選択 |A|= 5 -> 実際の n_a = 2 / n_b = 6 (重複 3 件が B に奪われた)' / 完全包含で 'STOP: ident.1 (A) has too few cells: 0' / 行順反転で 'n_a = 5 / n_b = 3'`

**修正方針**

意味論を決めたうえで seurat_bridge.py:963 付近に去重を入れるのが最小変更（例: ident2_ids から ident1_ids を除外して A vs B∖A にする、set 差分で実装）。同時に run_findmarkers.R:92 の代入を『既に割当て済みのセルに再代入しない』か『重複があれば stop する』に変え、行順依存を構造的に消すこと。いずれの案でも UI 表示（interactive_de.py:153）に実際の n_a / n_b と除外件数を出し、利用者が対比を確認できるようにする。どの案を採るかは解析上の判断なので実装前に承認を得ること。


### 21. [S2] DESI 再解析に正規化ポリシーが伝播せず、UI トグルが完全な死にコントロールになっている

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**／**該当**: `App/app/callbacks/analysis_callbacks.py:773`

> **訂正後の主張**: DESI 再解析(desi_cluster_filter)には正規化ポリシー(INPUT_NORMALIZED / NORM_MODE)の伝播経路が全段で存在せず、常に v16 テンプレ既定 INPUT_NORMALIZED=FALSE すなわち Seurat LogNormalize で走る。ただし UI トグル normalize_input_reanalysis は TIMS 専用コンテナ(tims_reanalysis_ion_settings、既定 display:none)の内側にあり DESI 再解析では画面に表示されないため『死にコントロール』ではなく『設定手段が無い』が正しい。また TIMS 側の本解析/再解析テンプレ既定値の不一致は、Python が両経路とも無条件に INPUT_NORMALIZED / V13_INPUT_NORMALIZED を注入するため実運用では顕在化しない。

**利用者から見た症状**

DESI の再解析画面には正規化の設定欄が最初から表示されない。本解析で「正規化 OFF」を選んでいても再解析はそれを引き継がず、黙って「正規化 ON」相当で走る。利用者から見れば「同じデータの同じ設定で絞り込んだだけ」なのに、1 回目と 2 回目のクラスタが直接比べられない状態になる。

**科学的影響**

R12-N3 と同一の帰結。1 回目と 2 回目で正規化が変わるため再解析のクラスタは元のクラスタと直接比較できず、クラスタ絞り込みという操作の前提が崩れる。合成データ検証でも SCiLS RMS 出力が O(1) のとき ARI 対真値が 1.0000 から ≈0 へ落ちることを再現した。対策は正規化を変えることではなく、本解析と再解析で一致させ provenance に記録することという結論部分は妥当。ただし TIMS 側は既に一致しているため、対策範囲は DESI 側に限定される。

**検証の経緯(反証の試みと結果)**

【R12-N3 と同一問題。統合先 R12-N3、報告書では単独計上しないこと】共通部分(DESI 再解析に正規化ポリシーの伝播経路が無い / ver3 に INPUT_NORMALIZED・NORM_MODE の grep が 0 件 / params 設定が tims_cluster_filter 分岐内に閉じている)は実行で確認され成立する。しかし本件を他 2 件と分ける固有主張 2 点は反証に成功した。(a)『UI トグルが完全な死にコントロールになっている』は誤り。AST で入れ子を確認したところ normalize_input_reanalysis(settings_tab.py:864)は tims_reanalysis_ion_settings(settings_tab.py:804-914、既定 display:none)の内側にあり、file_handlers.py:84 が active == "tims_cluster_filter" のときだけ表示する。TIMS の手法は tims_v8 / tims_cluster_filter の 2 択のみ(sidebar.py:99,102)で、DESI 選択時は TIMS 側がクリアされるため、DESI 再解析モードではこの欄はそもそも画面に出ない。利用者が触っても効かない死にコントロールではなく「設定欄が存在しない」。(b)『TIMS 側にも同型の食い違いがある(本解析既定 ver6:247 FALSE vs 再解析既定 ver18:191 TRUE、tims_v8 以外の経路で不整合)』も誤り。アプリ経由の実行ではテンプレ既定は必ず上書きされ、実際に生成した 3 本のランタイムすべてで注入済みであることを確認した。加えて TIMS の本解析手法は tims_v8 しか存在しないため『tims_v8 以外の経路』は成立しない。以上より、本件は重複計上を避けつつ WEAKENED として記録する。

**根拠**

- `App/app/layouts/settings_tab.py:805: id="tims_reanalysis_ion_settings",
App/app/layouts/settings_tab.py:806: style={"display": "none", "marginTop": "15px"},`
- `App/app/layouts/settings_tab.py:864: id="normalize_input_reanalysis",`
- `App/app/callbacks/file_handlers.py:79: is_tims_reanalysis = active == "tims_cluster_filter"
App/app/callbacks/file_handlers.py:84: tims_reanalysis_ion_style = {} if is_tims_reanalysis else {"display": "none"}`
- `App/app/layouts/sidebar.py:99: "value": "tims_v8"},
App/app/layouts/sidebar.py:102: "value": "tims_cluster_filter"},`
- `App/app/callbacks/analysis_callbacks.py:531: "input_normalized": (normalize_input == "OFF"),`
- `[実行] AST 解析: settings_tab.py の tims_reanalysis_ion_settings Div は 804-914 行、その内側に normalize_input_reanalysis(863) と norm_mode_reanalysis(873) が含まれる。style = {'display':'none','marginTop':'15px'}`
- `[実行] scratchpad/tmp/w3s_resume.py + w3s_norm.py: out_DESI/log/v8_runtime_*.R:71 INPUT_NORMALIZED <- FALSE(注入済) / out_TIMS/log/v8_runtime_*.R:247 同(注入済) / out_tims/log/cluster_filter_runtime_*.R:191 V13_INPUT_NORMALIZED <- TRUE(注入済) → テンプレ既定は残らない`

**修正方針**

R12-N3 の fix_sketch と同一。加えて本件の (a) を踏まえ、settings_tab.py の再解析正規化欄を tims_reanalysis_ion_settings の外へ出して DESI 再解析でも表示すること(現状は DESI では非表示なので、伝播経路だけ足しても利用者が値を選べない)。TIMS 側テンプレ既定値の是正は不要(注入で上書きされるため no-op)。


### 22. [S2] 正規化 ON/OFF と NORM_MODE が永続化されず、毎回テンプレ既定へ戻る

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**／**該当**: `App/app/services/session_manager.py:26`

> **訂正後の主張**: save_last_settings が _AUTO_SAVE_KEYS に無い normalize_input / norm_mode / normalize_input_reanalysis / norm_mode_reanalysis を黙って捨てるため、正規化の手動選択はアプリ再起動で失われ、また解析法ラジオの操作でも同一セッション中に上書きされる（機構は台帳の主張どおり。実行確認済み）。ただし戻る先は推奨既定（TIMS=OFF／DESI=ON）であり値は画面のラジオに見えている。さらに provenance.py:260-261 と methods_text.py:170-171 が実行ごとに input_normalized / norm_mode を記録するため事後追跡が可能で、『科学的に誤った結果が黙って出る』S1 ではなく『設定が永続化されず再現性が損なわれる』S2 が妥当。

**利用者から見た症状**

正規化のON/OFFと変換方法（log1p など）を手で選んでも、アプリを閉じて開き直すと元の既定に戻ってしまいます。さらに同じ画面のまま解析法のラジオボタン（UMAP解析／再解析、DESI／TIMS）を触っただけでも、選んだ値が黙って既定に書き戻されます。画面には戻った後の値が表示されるため、気づかないまま「前回と同じ設定」のつもりで実行できてしまいます。

**科学的影響**

二重正規化（SCiLS の RMS 正規化済みデータに更に LogNormalize をかける）を避けるかどうかという、本パイプラインで最も結果を左右する前処理設定が、利用者の明示的な選択にもかかわらず保持されません。TIMS で敢えて ON にした場合や DESI で敢えて OFF にした場合、同じ画面・同じ操作でも実行時期によって前処理が変わり、UMAP 配置とクラスタ分割が再現しなくなります。ただし戻る先は推奨既定であり、各実行の来歴（provenance / Methods 文）に input_normalized と norm_mode が記録されるため、事後に差異を特定して切り分けることは可能です。論文への影響は『黙って誤る』ではなく『再現性が担保されない』という性質のものです。

**検証の経緯(反証の試みと結果)**

機構の主張は実行確認で完全に裏付けられた（反証失敗）が、深刻度 S1 は過大なので S2 へ引き下げる。実行確認1（tmp/w1s_r1302.py、SESSIONS_DIR を隔離して往復）: normalize_input / norm_mode / normalize_input_reanalysis / norm_mode_reanalysis の4キーはすべて消え、_AUTO_SAVE_KEYS にある data_folder 等だけが残った。session_manager.py:65 の `if k in _AUTO_SAVE_KEYS:` が例外も警告も出さずに捨てており、analysis_callbacks.py:439-442 は完全な dead write。実行確認2（tmp/w1s_layout_sim.py、TIMS で正規化 ON にして終えた直後の再起動を再現）: normalize_input の初期値は 'OFF' に戻る。試みた反証はすべて不成立: (a)サブプロジェクト保存 analysis_callbacks.py:454-474 にも normalize 系は無い、(b)Output("normalize_input","value") の書き手は file_handlers.py:99 の set_default_normalize ただ一つで既定へ戻す側、(c)settings_tab.py:272 のフォールバック式自体は DESI/TIMS 排他コールバック(file_handlers.py:39-55)が非選択側を None にするため健全で追加バグは無い。深刻度を下げた根拠は3点: (1)戻る先が推奨既定（TIMS=OFF／DESI=ON、二重正規化を避ける側）で科学的に不正な値ではない、(2)実行時の値は画面のラジオにそのまま見えており R13-01 のような画面と計算の食い違いではない、(3)provenance.py:260-261 が input_normalized / norm_mode を実行ごとに記録し methods_text.py:170-171 が Methods 文へ出すため事後追跡が可能。よって『科学的に誤った結果が黙って出る(S1)』ではなく『設定が保存されず実行時期で前処理が変わる(S2)』が正しい。なお在席中のより痛い症状も確認した: set_default_normalize(file_handlers.py:104-111) は無条件に返すため、同一セッション中でも解析法ラジオを触った瞬間に手動選択が消える（TIMS UMAP で ON → 再解析タブ → UMAP へ戻す、で黙って OFF）。これは FormText の『手動変更可』(settings_tab.py:287-291)と矛盾する。

**根拠**

- `App/app/services/session_manager.py:65:             if k in _AUTO_SAVE_KEYS:`
- `App/app/services/session_manager.py:26: _AUTO_SAVE_KEYS = [ （26-55 行に normalize_input / norm_mode は無い）`
- `App/app/callbacks/analysis_callbacks.py:439:             "normalize_input": normalize_input,`
- `App/app/layouts/settings_tab.py:272:                                 value=ls.get("normalize_input", "OFF" if ls.get("analysis_method_tims") == "tims_v8" else "ON"),`
- `App/app/callbacks/file_handlers.py:111:     return "OFF" if active == "tims_v8" else "ON"`
- `App/app/services/provenance.py:260:             "input_normalized": params.get("input_normalized"),`
- `実行(tmp/w1s_r1302.py): save→load 往復で normalize_input / norm_mode / *_reanalysis の4キーが <<消えた>>、data_folder 等は残存`
- `実行(tmp/w1s_layout_sim.py): ion_mode=Negative を復元した状態でレイアウト構築 → normalize_input 初期値 = 'OFF'`

**修正方針**

session_manager.py の _AUTO_SAVE_KEYS に normalize_input / norm_mode / normalize_input_reanalysis / norm_mode_reanalysis の4キーを追加するだけで永続化は直る。set_default_normalize は prevent_initial_call=True なので初期表示では発火せず、復元値を潰さないことを確認済み。これは analysis_callbacks.py:439-442 が既に渡している値を受け取れるようにするだけで、設計判断は不要（mechanical）。なお『解析法ラジオを触ったときに手動選択を残すか』は別の設計判断であり、この修正の必須要件ではない（残すなら set_default_normalize を State 併用にして、既定値のままのときだけ切り替える形にする）。


### 23. [S2] セッション復元直後に ion_mode と adduct_filter が不整合になり、Negative でアノテーションが全滅する

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・数値不変／**該当**: `App/app/layouts/settings_tab.py:388`

> **訂正後の主張**: ion_mode は _AUTO_SAVE_KEYS にあり復元されるが adduct_filter の初期値は Positive 4 種のハードコード（settings_tab.py:388、再解析側 :856 も同型）で ls を見ず保存もされず、同期コールバック auto_switch_adduct は prevent_initial_call=True のため初期表示で発火しない。その結果 ion_mode=Negative／adduct=Positive の不整合が生成され、analysis_runner.py:552-554 が R テンプレートの極性由来の安全な既定をブロックごと潰して注入するため、R 側で db_use が 0 行となり無警告で全同定が失われる（ここまで実行確認済み）。ただし台帳の『セッション復元直後に全滅する』は不正確で、復元直後は R13-03 により DB 照合そのものが OFF に戻っているため annotate は呼ばれない。実害には「再起動後に DB 照合スイッチを入れ直し、かつイオンモードのラジオを触らずに実行する」という手順が必要である（Negative 運用では現実的な経路のため S2 は維持）。

**利用者から見た症状**

前回 Negative（陰イオン）モードで解析して終わった後にアプリを開き直すと、イオンモードは Negative と正しく表示されているのに、その下の Adduct フィルターだけが Positive 用（+H, +Na, +NH4, +K）に戻っています。この矛盾した組み合わせのまま実行すると、化合物名が 1 件も付かない結果表ができあがり、エラーも警告も一切出ません。イオンモードのラジオを一度クリックし直せば自動的に -H へ直りますが、正しく表示されているので触る理由がありません。

**科学的影響**

Negative モードの解析で代謝物同定が全滅し、markers_annotated.csv の annotation 列が m/z 文字列のままになります。R 側は該当行 0 件のとき無警告で入力をそのまま返すため（ver6 スクリプト 731 行）、利用者は「この試料には既知代謝物が無い」と誤読しうる点が最も危険です。数値（発現量・p 値・UMAP 座標）は変わらず、失われるのは同定ラベルのみ。ただし発火には、再起動後に DB 照合スイッチを入れ直しつつイオンモードのラジオを触らない、という手順が必要です（再起動直後は R13-03 により DB 照合自体が OFF に戻っているため）。プリセットやサブプロジェクト経由で設定を読み込んだ場合は同期が働き発生しません。

**検証の経緯(反証の試みと結果)**

不整合の生成・R への注入・R での無警告切り捨てをすべて実行確認したが、台帳の『セッション復元直後に…全滅する』という発火条件の記述が不正確なので WEAKENED（深刻度 S2 は維持）。実行確認1（tmp/w1s_layout_sim.py）: 前回 Negative で終えた後の再起動を再現すると ion_mode='Negative'（settings_tab.py:366 で ls から復元）／adduct_filter=['+H','+Na','+NH4','+K']（:388 の固定リテラル）が同時に生成される。auto_switch_adduct(file_handlers.py:500-508) は prevent_initial_call=True で初期表示では発火しない。稼働中アプリの /_dash-layout を実取得しても adduct_filter は常に Positive 4 種で、app.layout = create_main_layout()(main.py:371) がプロセス起動時に一度だけ評価されるため初期値はサーバ再起動まで固定される。実行確認2（tmp/w1s_r1305_gen.py、実注入関数を実テンプレートに適用）: 生成後 287 行 ION_MODE <- "Negative" / 300 行 ANNOT_ADDUCT_PATTERNS <- c("+H","+Na","+NH4","+K")。R テンプレート 300-306 行の極性由来の安全な既定（Negative なら c("-H")）は _replace_block_assign(analysis_runner.py:552-554) が if/else ブロックごと潰すため残らない。実行確認3（tmp/w1s_r1305.R、Rscript 4.3.3、実テンプレートから annotate_mz_with_format を抜き出して評価、同梱 DB と同じ Adduct 表記 M+H/M+Na/M+NH4/M-H）: 正しい c("-H") では db_use 2 行で Glucose/Citrate が同定されるのに対し、画面初期値の Positive 4 種では db_use 0 行となり m/z 文字列がそのまま返り、warnings() は空（無警告）。反証として見つかった条件不足: R13-03 により再起動直後は DB 照合そのものが OFF（use_annotation_check が ['embedded'] に戻る → analysis_callbacks.py:524 の annotation_enable が False → R の ann_db が NULL → :1921-1924 で annotate 自体が呼ばれない）。つまり R13-03 が R13-05 を一時的に覆い隠しており、実害には (1)前回 Negative で終えている (2)このセッションで DB 照合を入れ直す (3)その際イオンモードのラジオを触らない、の 3 条件が要る。Negative 運用のラボでは (2) は毎セッション必須の操作、(3) はイオンモードが既に正しく Negative と表示されているので触らないのが自然であり、現実的な経路。よって S2 を維持する。プリセット経由(preset_callbacks.py:91-105 が ion_mode と adduct を同時に Output)とサブプロジェクト経由(project_callbacks.py:653 → auto_switch_adduct が連鎖)では同期が働き発生しないという台帳の但し書きも正しい。再解析側(settings_tab.py:840/856)も同型。

**根拠**

- `App/app/layouts/settings_tab.py:366:                                         value=ls.get("ion_mode", "Positive"), inline=True,`
- `App/app/layouts/settings_tab.py:388:                                         value=["+H", "+Na", "+NH4", "+K"], （:856 の再解析側も同じ固定値）`
- `App/app/callbacks/file_handlers.py:503:     prevent_initial_call=True,
App/app/callbacks/file_handlers.py:505: def auto_switch_adduct(ion_mode):`
- `App/app/services/analysis_runner.py:554:         lines = _replace_block_assign(lines, "ANNOT_ADDUCT_PATTERNS", r_vec) （R 300-306 行の極性由来既定をブロックごと潰す）`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:731:   if (nrow(db_use) == 0) return(mz_vec)`
- `App/app/main.py:371: app.layout = create_main_layout() （初期値はプロセス起動時に一度だけ評価）`
- `実行(tmp/w1s_layout_sim.py): ion_mode='Negative' と adduct_filter=['+H','+Na','+NH4','+K'] が同一レイアウトに同時生成`
- `実行(tmp/w1s_r1305.R, Rscript 4.3.3): Negative×正 c("-H") は db_use 2 行で同定成功、Negative×Positive4種 は db_use 0 行・出力は m/z 文字列のまま・警告なし`

**修正方針**

settings_tab.py:388 と :856 の固定リテラルを ion_mode 依存にする（value=ls.get("adduct_filter", ["-H"] if ls.get("ion_mode") == "Negative" else ["+H","+Na","+NH4","+K"])、再解析側は reanalysis_ion_mode を見る）。併せて adduct_filter / reanalysis_adduct_filter を _AUTO_SAVE_KEYS と run_analysis の保存辞書へ追加する。『Positive なら +H/+Na/+NH4/+K、Negative なら -H』という正解は auto_switch_adduct(file_handlers.py:506-508) と R テンプレート 300-306 行に既に二重に実装済みで、初期値をそれに揃えるだけなので設計判断は不要（mechanical）。別項目として R 側 731 行の 0 行素通りに warning() を足すことを推奨する。


### 24. [S2] 取り込み RDS キャッシュの鍵が size+mtime のみで、ANNOTATION_FILTER / USE_EMBEDDED_COMPOUND_NAMES 変更後の再実行が旧データを黙って再利用

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**／**該当**: `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:1088`

> **訂正後の主張**: 取り込みキャッシュ read_desi_data_cached の鍵が入力ファイルの size+mtime のみで、キャッシュ対象データに焼き込まれる設定(ANNOTATION_FILTER による行選別・USE_EMBEDDED_COMPOUND_NAMES による feature 命名)を含まない。キャッシュ置き場は OUTPUT_DIR/_csv_rds_cache なので、発症は『出力フォルダーを同じにしたまま設定を変えて再実行した場合』に限られる。台帳が根拠とした『出力フォルダー入力はページ読込時に初期化されたまま同一セッション中固定(settings_tab.py:974)』は現行コードでは正しくない — ver56.3 の refresh_output_subfolder(analysis_callbacks.py:1524-1543)が解析設定タブを開き直すたびに名前を作り直すため、別タブへ行って戻れば発症しない。それでも (a) 解析設定タブから離れずに切片フィルタだけ変えて実行ボタンを押し直す(実行ボタン・進捗・ログはすべて同タブ上 settings_tab.py:1162 なのでごく自然)、(b) 出力フォルダーに独自名を付けている(_AUTO_SUBFOLDER_RE に一致しないと no_update で恒久固定)、の2経路で通常運用のまま発症する。

**利用者から見た症状**

解析設定タブから離れずに、切片(スライス)の選択だけを変えて「解析実行」をもう一度押すと、変更前のスライスのデータでそのまま解析が走ります。出力フォルダーに自分で名前を付けている場合も同じことが起きます。画面には何の警告も出ず、結果に付く記録には新しく選んだスライス名が書かれるため、見た目には正常に見えます。出力フォルダー名を変えて実行し直すと正しい結果になります。

**科学的影響**

発症すると『切片フィルタを変えて再実行した』結果が旧フィルタの母集団で計算される。実測例では Slice3 のみ(100 spots)を解析したつもりが Slice1+Slice2(200 spots)で計算され、しかも receipt の annotation_filter には新しい設定(Slice3)が記録されるため、出力ラベルと実データが完全に食い違う。母集団が違えば HVF・PCA・UMAP・クラスタ・DEG の全数値が誤りとなる。USE_EMBEDDED_COMPOUND_NAMES を切り替えた場合は feature 名(化合物名 vs m/z)が旧設定のまま固定され、下流のアノテーション照合結果も旧設定のものになる。副次的な観察として、既定フローで毎回フォルダ名が変わるなら _csv_rds_cache は一度もヒットしない(実行ごとに新しい空ディレクトリ)ため、このキャッシュは『効くときは間違い、効かないときは無駄』という状態にある。

**検証の経緯(反証の試みと結果)**

機構は逐語移植の実行で完全に再現できた(同一フォルダ2回目の実行でフィルタを Slice1+Slice2 → Slice3 に変えても [CACHE HIT] で 200 spots/Slice1,Slice2 の旧データが返り、フォルダを変えると [CACHE MISS] で正しい 100 spots/Slice3 になる)。ANNOTATION_FILTER と USE_EMBEDDED_COMPOUND_NAMES はいずれも read_desi_data(ver6:806-)の内側で適用されてから戻り値に焼き込まれ(ver6:927-940 / 884-904)、キャッシュ検証は size と mtime のみ(ver6:1088)。RDS_CACHE_* は App から未注入(grep 0 件)、末尾 cleanup で消えるのは Step1 RDS だけ(ver6:2804)。一方で台帳の根拠のひとつは反証できた: refresh_output_subfolder(ver56.3)が出力フォルダ名を再生成するため『同一セッション中固定』は現行では成り立たない。また open_overwrite_modal(analysis_callbacks.py:216-249)が『このフォルダには既存の解析結果ファイルがあります』と確認を出す(ただし警告しているのは出力の上書きであって入力キャッシュの再利用ではない)。発症条件が台帳より狭いため WEAKENED とするが、残る2経路はいずれも通常運用であり、発症時は無警告で全下流数値が誤るため severity は S2 を維持する。

**根拠**

- `/home/user/U_Analysis/App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:1088: ok <- isTRUE(all.equal(as.numeric(obj$meta$size), as.numeric(fi$size))) && isTRUE(all.equal(as.numeric(obj$meta$mtime), as.numeric(fi$mtime)))`
- `/home/user/U_Analysis/App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:929: mask <- coordinates$annotation %in% ANNOTATION_FILTER   (read_desi_data(806-)の内側なのでキャッシュ対象データに焼き込まれる)`
- `/home/user/U_Analysis/App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:202-204: RDS_CACHE_ENABLE <- TRUE / RDS_CACHE_FORCE_REBUILD <- FALSE / RDS_CACHE_DIR <- file.path(OUTPUT_DIR, "_csv_rds_cache")   (App から未注入)`
- `/home/user/U_Analysis/App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2804: file.remove(rds_step1_out)   (末尾 cleanup は Step1 RDS のみ。_csv_rds_cache は残る)`
- `/home/user/U_Analysis/App/app/services/analysis_runner.py:567-570: if params.get("annotation_filter"): ... lines = _replace_assign(lines, "ANNOTATION_FILTER", r_vec)   (設定は毎回注入されるがキャッシュ鍵には入らない)`
- `/home/user/U_Analysis/App/app/callbacks/analysis_callbacks.py:1539-1543: if active_tab != "settings": return no_update / if current and not _AUTO_SUBFOLDER_RE.match(...): return no_update / return datetime.now().strftime("Analysis_%Y%m%d_%H%M%S")   (台帳の『セッション中固定』への反証。ただしタブ切替が無い/独自名なら固定のまま)`
- `/home/user/U_Analysis/App/app/layouts/settings_tab.py:1162: ["▶ 解析実行"], id="run_analysis",   (実行ボタンが解析設定タブ上にあるためタブを離れない運用が自然 = refresh が発火しない)`
- `/home/user/U_Analysis/App/app/callbacks/analysis_callbacks.py:237-238: if not _output_has_existing_results(target): return False, no_update, mode   (上書き確認は出るが、警告対象は出力の上書きであってキャッシュ再利用ではない)`

**修正方針**

根本策: read_desi_data を『素の読み込み』と『ANNOTATION_FILTER 適用 + feature 命名』に分割し、キャッシュは前者だけに掛けてフィルタ・命名はキャッシュ後段で適用する(設定を変えても再読込が不要になりヒット率も上がる)。簡易策: キャッシュ鍵に設定のハッシュ(ANNOTATION_FILTER・USE_EMBEDDED_COMPOUND_NAMES・sample_prefix・テンプレ版)を含め、ver6:1088 の検証に一致条件として加える。あわせて RDS_CACHE_DIR を OUTPUT_DIR 配下から共有キャッシュ領域へ移すか、少なくともキャッシュヒット時に『どの設定でキャッシュされたか』をログに出す。挙動の設計判断は不要なので mechanical(ただし発症していたケースの数値は誤った値から正しい値へ変わる)。


### 25. [S2] keep モードのマージ統合が構造的に不成立: SAMPLE_NAME_MAP=NULL 固定 × rerun サンプル名の _KEEP_Cl_ サフィックスでキーが一切一致せず、再解析ジョブが最後に必ずエラー終了

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・**過去の結果と数値が変わる**／**該当**: `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:517`

> **訂正後の主張**: 主張の本体（keep モードの DESI 再解析は最終段のマージで必ず stop し、ジョブがエラー終了する）は base R で実証のうえ UPHELD。ただし付随主張の「レシート未出力」は事実誤認で訂正が必要。v16 コピーは自身の末尾（v16:3015）で try(write_receipt_sidecar()) を実行済みであり、write_receipt_sidecar は出力先候補に V8_OUTPUT_DIR を含む（helpers/rds_io.R:298-300）ため、R サイドカー analysis_receipt_r.json は出力される。出力されないのは ver3:537 の 2 回目呼出しと、Python 側の receipt.json / RECEIPT.md（analysis_finalizer が status!=finished では生成しないため）。逆に元の主張より重い事実が判明した: status='error' では analysis_finalizer.py:68-71 の分岐で _link_to_project() も呼ばれないため、数時間かけた再解析の結果フォルダがサブプロジェクトに登録されず、projects.json の last_result_dir と _project_meta.json が更新されない。結果としてインタラクティブ画面から再解析結果を自動では開けなくなる。

**利用者から見た症状**

DESI で「抽出 (keep)」を選んだ再解析は、UMAP もクラスタリングも DEG も全部終わったあと、最後の一歩で必ず赤いエラーで止まります。画面には「解析でエラーが発生しました」と出ます。しかもエラー扱いになると結果フォルダがプロジェクトに自動登録されないため、インタラクティブ画面のプルダウンに再解析結果が出てこず、数時間かけた計算が「丸ごと失敗した」ように見えます（ファイル自体はディスクに残っています）。売りである「抽出したクラスタを 3-a / 3-b に分けて元の UMAP に重ねる」統合図・統合ラベルは、現行スクリプトでは一度も作られたことがありません。

**科学的影響**

誤った数値が論文に載る種類の不具合ではない（必ず停止するので誤結果は生成されない）。影響は「サブクラスタ統合という解析手段が DESI では実質存在しない」ことと、再解析成果物が画面から辿れなくなる運用上の損失。修正すると umap_merged 座標と seurat_clusters_merged ラベルという新しい数値成果物が初めて生成されるため affects_numbers=true とした（既存の v16 出力の数値は変わらない）。修正は TIMS ver18:1269-1273 と同じ書き方でエクスポートループ内に map[paste0(sn, suffix)] <- sn を積み、517 行で渡すだけで、ROI 分割が非注入・既定 OFF のため対応は一意（mechanical）。

**検証の経緯(反証の試みと結果)**

反証を 5 系統試みたがすべて不成立。(1) Python が SAMPLE_NAME_MAP を注入する経路は全リポジトリ grep で存在せず、ver3:517 の NULL がそのまま UMAP_Merge_Clusters_ver1.R:463 に渡る。(2) .should_merge は FILTER_MODE=='keep' && RUN_V8_AFTER_EXPORT（既定 TRUE, ver3:84）で成立し、MERGE_SCRIPT_PATH は analysis_runner.py:730-733 が実在パスを注入する。(3) .find_rerun_rds は V8_OUTPUT_DIR 配下の RDS_Files を再帰探索し、v16:303-304 が作る output_dir/RDS_Files に harmony/RPCA/SingleSample の RDS が保存される（v16:2520-2521 ほか）ので必ず見つかる。(4) rerun 側 sample 名は ver3:438/445 → make_v8_copy_with_settings の sample_names 置換（ver3:366-371）→ v16:2144/2206 の経路で必ず _KEEP_Cl_* サフィックス付きになる。USE_ROI_AS_SAMPLE は v16:286 で既定 FALSE かつ再解析では非注入（tests/test_r_injection_completeness.py:63-64 の KNOWN_DEAD）なので名前は一意。(5) ver3:526 の source は tryCatch されていない。実際の命名規則（250621_Ohashi_CV-AAs + _KEEP_Cl_3-5）で base R 実行したところ merge() は 0 行、.stopif(nrow(m)>=3) が STOP を投げ、Rscript --vanilla は 'Execution halted' で exit code 1 を返すことを実測した（analysis_runner.py:1268-1269 でそのまま status='error'）。逆引き map を渡す修正案を同条件で試すと merge は 3 行で成立し、spot_index 側は一致していて不一致要因がサンプル名だけであることも確認した。S1 ではなく S2 とした理由は、誤った数値が出るのではなく loud fail で終わるため。

**根拠**

- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:517:     SAMPLE_NAME_MAP     <- NULL`
- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:438:   out_txt <- file.path(EXPORT_TXT_DIR, paste0(sn, suffix, ".txt"))`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:2206:       seurat_obj$sample <- sub_name`
- `App/Script/Common/UMAP_Merge_Clusters_ver1.R:242:   m <- merge(base_sub, rer_um, by = "key", suffixes = c("_old", "_new"))`
- `App/Script/Common/UMAP_Merge_Clusters_ver1.R:244:   .stopif(nrow(m) >= 3,`
- `App/Script/DESI/DESI_RDS_ClusterFilter_ver3.R:526:     source(MERGE_SCRIPT_PATH)   （tryCatch なし）`
- `App/app/services/analysis_finalizer.py:68:     if status == "finished":  / :69:         _link_to_project(output_dir, job, result)  / :71:     else:`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:3015: if (exists("write_receipt_sidecar")) try(write_receipt_sidecar(), silent = TRUE)  （R サイドカーは出力される＝元主張の訂正点）`

**修正方針**

ver3 のエクスポートループ（416-446）に .merge_sample_map <- c() を用意し、out_txt を書いた直後に .merge_sample_map[tools::file_path_sans_ext(basename(out_txt))] <- sn を追加。517 行を SAMPLE_NAME_MAP <- if (length(.merge_sample_map) > 0) .merge_sample_map else NULL に置換する（ver18:1424 と同一の書式）。併せて 526 行の source(MERGE_SCRIPT_PATH) を tryCatch で包み、マージ失敗が再解析本体の成否を巻き添えにしない（＝結果がプロジェクト登録される）ようにするのが望ましい。


### 26. [S3] 『UI の正規化 OFF でも log1p が黙って適用され表記と実処理が乖離』という主張は不成立。OFF の選択肢ラベル自体が「OFF（正規化済み入力: SCiLS RMS 等）

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・数値不変

> **訂正後の主張**: apply_input_norm の INPUT_NORMALIZED=TRUE 経路が spot 単位スケーリングを行わないこと自体は、入力が真に RMS 正規化済みであれば二重正規化を避ける妥当な設計であり、UI も OFF 時の変換(NORM_MODE=log1p)を常時可視の有効なセレクタで表示している(『表記と実処理の乖離』という主張は実行検証で反証された)。実害は『入力が実は正規化済みでない』ときに限られ、そのとき pixel 総量(TIC)の空間変動が PC1 の 90% を占め、クラスタが組織型ではなく TIC の高低で割れる。DESI はテンプレ既定・UI 既定・再解析経路のいずれも LogNormalize 側に倒れているため、手動で OFF を選んだ場合のみの条件付き欠陥である。真の欠陥は R 側に『入力が本当に正規化済みか』を検証するガードが無いこと。

**利用者から見た症状**

「正規化」を OFF にしたまま生データ(RMS 正規化していないデータ)を解析すると、UMAP のかたまりやクラスタの分かれ方が、組織の種類ではなく「その測定点の全体的な明るさ(総イオン量)」で決まってしまう。警告は一切出ないので、利用者は組織の違いを見ていると思い込んだまま図を作ってしまう。ただし DESI では初期設定が ON になっているため、手動で OFF に切り替えないかぎりこの状態にはならない。

**科学的影響**

INPUT_NORMALIZED=TRUE + NORM_MODE=log1p は log1p(counts) を data 層に置く。LogNormalize との差は理論上 log(T_j)-log(S) という feature に依らない pixel ごとの加法定数(rank-1 項)で、実測でも相関 +0.9995 で一致した。ScaleData は feature 方向の z 化なので pixel 方向のこのオフセットを除去できず、そのまま PCA の主軸に乗る。模擬データ(feature 300 / pixel 800、TIC は組織型と独立に幾何 SD 0.85)では PC1 の寄与率が LogNormalize の 9.31% に対し log1p のみで 90.10%、PC1 と log TIC の相関 0.999、k-means のクラスタが組織型ではなく TIC 高低に一致(ARI 0.865)した。ただしこれは前提(入力が RMS 済み)が破れたときの帰結であり、前提が満たされていれば設計は妥当である。対策は正規化の選択を変えることではなく、INPUT_NORMALIZED=TRUE のときに counts の列和(TIC)の変動係数を実測してログ出力し、大きければ警告する QC を R 側に入れること。この修正は正しく使われた解析の数値を変えない。

**検証の経緯(反証の試みと結果)**

同じ cid に R1(REFUTED・UI 論点)、R11(CONFIRMED S2・科学論点)、R13(CONFIRMED S3・UI 論点)の 3 主張がある。分けて判定した。(1) R11 の科学的中核は独立に再現でき UPHELD。apply_input_norm の TRUE 経路(v16:102-120)は log1p(counts) だけで pixel 総量による割り算がなく、LogNormalize との差は pixel ごとの加法定数である。numpy で実測: 実測 pixel 平均差と理論 log(T)-log(S) の相関 +0.999511、ScaleData 相当(feature 方向 z 化)後も log1p のみは corr(pixel 平均 z, log TIC)=+0.9991(LogNormalize は -0.0401)、PC1 寄与率 90.10% で |corr(PC1, log TIC)|=0.999、k-means(k=2) の ARI は組織型 -0.0011 / TIC 高低 +0.8647。ScaleData が feature(行)方向の z 化であるのに対しオフセットは pixel(列)方向の rank-1 項なので除去できないという機構も確認した。(2) R13 の『UI 表記と実処理が食い違う』は実行検証で反証成立=OVERTURNED。稼働コードを直接呼んで toggle_norm_mode_enabled('OFF')->False / ('ON')->True、set_default_normalize('desi_v8',None)->'ON' を確認。さらに create_settings_tab() をレンダリングして可視要素を列挙し、NORM_MODE の Select が Collapse にも display:none にも入らず常時可視で、OFF 選択時のみ有効化され、表示値が『log1p（log変換・推奨）』であることを確認した。隠蔽構造ではなく R1 の REFUTED が正しい。R13 の残る有効な指摘(norm_mode が _AUTO_SAVE_KEYS に無い)は normalize_input も同様に保存されないため、正規化ラベル固有ではなく設定永続化一般の問題。(3) 露出度: テンプレ既定は v16:71 FALSE、UI 既定は DESI=ON(実行確認)、DESI 再解析は DESI_RDS_ClusterFilter_ver3.R に V8_INPUT_NORMALIZED が無く analysis_runner.py:762-769 が V13_* しか注入しないため v16 既定 FALSE が保たれる。よって DESI で TRUE 経路に入るには利用者が手動で OFF を選ぶ必要があり、実運用の既定は安全側。以上より S2 は過大で S3(条件付き・誤用依存)が妥当。

**根拠**

- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:107: cm  <- LayerData(s[[asy]], layer = "counts")  / :111 "log1p" = log1p(cm),  ← spot スケーリング無しで data 層へ`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:117: s <- NormalizeData(s)  ← FALSE 経路のみ LogNormalize(TIC 正規化+log)`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:71: INPUT_NORMALIZED <- FALSE  ← DESI テンプレ既定は安全側(TIMS ver6:247 も FALSE)`
- `App/app/layouts/settings_tab.py:274-285: html.Small("OFF時の変換 (NORM_MODE)") / dbc.Select(id="norm_mode", options=[log1p / sqrt / none], value=ls.get("norm_mode","log1p"))  ← Collapse にも display:none にも入っていない`
- `(実行) create_settings_tab() のレンダリング結果: [ID] norm_mode / [OPTIONS] log1p（log変換・推奨）… / [VALUE] norm_mode='log1p'  ← 常時可視で値も表示されている`
- `(実行) app.callbacks.file_handlers: toggle_norm_mode_enabled('ON')->True, ('OFF')->False, set_default_normalize('desi_v8',None)->'ON', (None,'tims_v8')->'OFF'`
- `(実行) /tmp/.../scratchpad/tmp/w4s/c10_c11_norm.py C10-B/C/D: log1p のみ corr(pixel平均z, logTIC)=+0.9991 / PC1寄与率 90.10% |corr(PC1,logTIC)|=0.999 / ARI 組織型 -0.0011・TIC高低 +0.8647`
- `(実行) 同上 LogNormalize 側: corr(pixel平均z, logTIC)=-0.0401 / PC1寄与率 9.31% |corr(PC1,組織型)|=0.993 / ARI 組織型 +1.0000・TIC高低 -0.0009`

**修正方針**

apply_input_norm の TRUE 経路に sanity check を追加する: counts の列和(pixel ごとの TIC)の変動係数または幾何 SD を計算し、閾値(例: 幾何 SD > 0.3)を超えたら『入力が正規化済みでない可能性』を message() で警告し、その実測値を provenance/receipt に記録する。あわせて本解析と再解析の正規化既定の不一致(settings_tab.py:869 の無条件 OFF)を揃える。UI 側の追加改修は不要(実行検証で NORM_MODE は常時可視・OFF 時有効と確認済み)。数値は変わらない。


### 27. [S3] アノテーションの由来スイッチ（use_annotation_check）が永続化されず毎回 ["embedded"] に戻る

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・数値不変／**該当**: `App/app/layouts/settings_tab.py:244`

> **訂正後の主張**: use_annotation_check は save_last_settings の呼び出しにも _AUTO_SAVE_KEYS にも含まれず、annotation_path だけが保存されるため、再起動後は「パス欄だけ埋まって照合スイッチはOFF」という誤解を招く状態になる（機構は台帳の主張どおり。実行確認済み）。ただし戻る先は ver55.0 が意図的に定めた既定であり、スイッチと説明文は画面に表示されている。数値は一切変わらず影響は同定ラベルの有無に限られるため、S2 ではなく条件付き・潜在の S3 が妥当。

**利用者から見た症状**

「代謝物データベース CSV で m/z 照合する」をONにして解析した後、アプリを開き直すと、CSVのパス欄だけが前回のまま残り、照合のスイッチだけがOFFに戻っています。パスが入っているので「DBを使う設定のままだ」と思い込みやすい組み合わせです（スイッチ自体は画面に見えており、すぐ下の説明文にも「『代謝物データベース』を選んだときだけ上のパスを使います」と書かれています）。

**科学的影響**

照合が走らないまま解析が完了し、markers_annotated.csv の annotation 列が化合物名ではなく m/z 文字列のままになります。同定の有無が実行ごとに変わるため、同じデータ・同じつもりの設定で作った 2 つの結果表の中身が食い違います。数値（発現量・p 値・logFC・UMAP 座標）は一切変わらず、影響は同定ラベルの有無に限定されます。

**検証の経緯(反証の試みと結果)**

機構は実行確認で裏付けられた（反証失敗）が、深刻度 S2 は過大なので S3 へ引き下げる。実行確認1（tmp/w1s_r1302.py）: use_annotation_check は save_last_settings の往復で消え、annotation_path（session_manager.py:28 の一覧にある）は残った。実行確認2（tmp/w1s_layout_sim.py）: 再起動直後の初期値は annotation_path='/db/4500.csv' かつ use_annotation_check=['embedded'] となり、『パス欄は埋まっているのに照合スイッチは OFF』という誤解を招く組み合わせが実際に生成されることを確認した。試みた反証はすべて不成立: (a)run_analysis の保存辞書 analysis_callbacks.py:414-445 に use_annotation_check は無い（渡してすらいない）、(b)サブプロジェクト保存 454-474 行にも無い、(c)preset_manager.py:24-31 の保存キーにも無いのでプリセットでも救えない。深刻度を下げた根拠は3点: (1)戻る先は ver55.0 で意図的に選ばれた既定（settings_tab.py:242-243『既定: 自分で Export したデータ由来の名前だけ使う』）であり暴走ではない、(2)スイッチは画面に見えており直下の FormText(settings_tab.py:258-261)が『「代謝物データベース」を選んだときだけ上のパスを使います』と明示している、(3)数値は一切変わらず、変わるのは markers_annotated.csv の annotation 列が化合物名か m/z 文字列かという表示の別のみ。『毎回の再起動で必ず起きる』ので S4 ではなく、条件付き・潜在の S3 が妥当。

**根拠**

- `App/app/layouts/settings_tab.py:244:                                 value=ls.get("use_annotation_check", ["embedded"]),`
- `App/app/services/session_manager.py:28:     "data_folder", "annotation_path", "output_dir", （annotation_path は保存対象だが use_annotation_check は 26-55 行の一覧に無い）`
- `App/app/callbacks/analysis_callbacks.py:524:                 "annotation_enable": ("db" in _use_annot),`
- `実行(tmp/w1s_r1302.py): use_annotation_check は往復で <<消えた>>、annotation_path='/db/metabolites.csv' は残存`
- `実行(tmp/w1s_layout_sim.py): 再起動再現で annotation_path='/db/4500.csv' / use_annotation_check=['embedded'] が同時に生成される`

**修正方針**

session_manager.py の _AUTO_SAVE_KEYS に use_annotation_check を追加し、analysis_callbacks.py の save_last_settings 呼び出しに "use_annotation_check": _use_annot（既に 514 行で手元にある）を 1 行足す。settings_tab.py:244 は既に ls.get("use_annotation_check", ["embedded"]) と書かれており受け側の修正は不要。数値は変わらず設計判断も不要（mechanical）。


### 28. [S3] TIMS 再解析で m/z アライメント・埋め込み化合物名・切片フィルタ・サンプル別校正がテンプレ既定へ戻る

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 要科学的判断・**過去の結果と数値が変わる**／**該当**: `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:872`

> **訂正後の主張**: 再解析テンプレ ver18 の再注入一覧(872-935)に MZ_ALIGN_PPM / USE_EMBEDDED_COMPOUND_NAMES / ANNOTATION_FILTER / CALIBRATION_BY_SAMPLE が無く、ver6 コピーが既定値(0 / FALSE / NULL / list())で走るのは事実（Rscript で実測）。ただし実害は 4 項目一律ではない。(1) ANNOTATION_FILTER は再注入不要——spot_index は parquet の id そのもので、エクスポートは RDS に残った id しか書き出さないため初回の切片フィルタは自動的に継承される。(2) USE_EMBEDDED_COMPOUND_NAMES は feature 名の表示のみに影響し、しかも列名に化合物名が焼き込まれた旧データ(is_annotated=TRUE)に限られるので数値は変わらない。(3) 数値が変わるのは MZ_ALIGN_PPM（初回に >0 を設定し複数サンプルの場合）と CALIBRATION_BY_SAMPLE（サンプル別校正を使った場合）の 2 項目で、いずれも初回設定に条件付き。(4) 併せて、復元側 load_calibration_from_first_analysis は analysis_params.json に保存済みの calibration_by_sample を読まず、共通係数 [0.0] を「検出した回帰式」として自動適用するため、補正量ゼロを補正済みと誤表示する。

**利用者から見た症状**

1 回目の解析で「m/z アライメント (ppm)」を設定していても、再解析ではその設定が無かったことにされます（画面に再解析用の入力欄も無いため確認できません）。また、サンプルごとに個別の m/z 校正曲線を作っていた場合、再解析画面には「✅ 前回の解析から回帰式を検出」と表示され、チェックも自動で入るのに、実際に適用される補正量はゼロです。化合物名の表示も、旧形式データでは再解析だけ m/z 表記に戻ります。

**科学的影響**

m/z アライメントを使っていた解析では、再解析でサンプル間の m/z が統一されず共通 feature が大幅に減るため、HVG 選択・PCA・統合の入力が初回と別物になり、UMAP とクラスタが比較不能になる。サンプル別キャリブレーションを使っていた解析では、初回は各サンプル固有の補正後 m/z を統合キーにしていたのに、再解析は無補正の生 m/z を使うため、同じく feature の対応付けが崩れる。しかも UI が「前回の回帰式を復元した」と表示するため、無補正であることに気づけない。一方、切片フィルタの継承欠落は実害が無く（エクスポート段階で担保済み）、化合物名スイッチはラベル表示のみで数値に影響しない。

**検証の経緯(反証の試みと結果)**

『4変数が ver18:872-935 の再注入一覧に無く、ver6 コピーで既定値のまま走る』という事実関係は、make_v13_copy_with_settings を Rscript で隔離実行して完全に確認した（MZ_ALIGN_PPM=0 / USE_EMBEDDED_COMPOUND_NAMES=FALSE / ANNOTATION_FILTER=NULL / CALIBRATION_BY_SAMPLE=list() がすべて既定のまま）。ただし4項目のうち2項目は実害が主張より小さいため WEAKENED とする。(a) ANNOTATION_FILTER: ver6 の spot_index は parquet の id 列そのもの(ver6:908-913)で、エクスポートは RDS 由来 id との突合(ver18:1242→595)なので、初回の切片フィルタで落ちたスポットはそもそも再解析の入力に含まれない。よって再注入は不要で、この項目は実害なし（OVERTURNED）。(b) USE_EMBEDDED_COMPOUND_NAMES: UI 既定が ['embedded']=TRUE なので毎回 FALSE へ転ぶのは事実だが、.use_embedded が効くのは is_annotated=TRUE のとき、すなわち列名に化合物名が焼き込まれた旧データのみ(ver6:823-843、^mz_ 列で読めた場合は FALSE 固定)。しかも影響は feature 名の表示だけで、本数も強度も変わらず make.unique で一意化されるため UMAP/クラスタは不変＝数値に効かない。(c)(d) は実害あり: MZ_ALIGN_PPM=0 では複数サンプルの m/z が文字列完全一致でしか統合されず共通 feature が激減する(ver6:2423-2424)。CALIBRATION_BY_SAMPLE 欠落については、初回がサンプル別のみだった場合に Python が共通係数 [0.0] を保存し(analysis_callbacks.py:687)、復元側は calibration_by_sample を保存しているのに読まず(同:927 保存 / 2486 読み出し)、[0.0] が truthy なので『✅ 前回の解析から回帰式を検出』と表示してチェックを自動 ON にする(同:2566)。R で calibrate_mz(mz, c(0.0)) を実測したところ補正量は厳密に 0 で、『補正 ON なのに無補正』が確認できた。ただし (c) は初回で mz_align_ppm>0 を設定した場合(UI 既定 0)、(d) はサンプル別キャリブレーションを使った場合に限られるため、無条件の誤動作ではなく条件付き＝S3 とする。

**根拠**

- `App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R:872:   code <- replace_assign_line(code, "OUTPUT_DIR",   r_str(output_dir))  ← 872-935 に MZ_ALIGN_PPM / USE_EMBEDDED_COMPOUND_NAMES / ANNOTATION_FILTER / CALIBRATION_BY_SAMPLE は無い(Rscript 実行で ver6 コピーが全て既定のままと確認)`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:351: MZ_ALIGN_PPM <- 0`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:2423:   if (MZ_ALIGN_PPM > 0 && length(seu_list) > 1) {
    seu_list <- align_mz_features(seu_list, MZ_ALIGN_PPM)`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:843:         is_annotated <- n_ann > 0  ← 列名に化合物名が焼き込まれた旧データでのみ TRUE。^mz_ 経路では 823 の FALSE 固定`
- `App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R:346: CALIBRATION_BY_SAMPLE <- list()  ← 再注入されないため 1119 の has_per_sample が常に FALSE`
- `App/app/callbacks/analysis_callbacks.py:687:                             params["calibration_coefficients"] = [0.0]`
- `App/app/callbacks/analysis_callbacks.py:927:             _params_to_save["calibration_by_sample"] = params.get("calibration_by_sample")  ← 保存はしている`
- `App/app/callbacks/analysis_callbacks.py:2486:     cal_coefficients = params_data.get("calibration_coefficients")  ← 復元側は calibration_by_sample を読まない`

**修正方針**

(A) MZ_ALIGN_PPM: 値の出所を決める設計判断が先（初回の analysis_params.json['mz_align_ppm'] を継承するか、再解析パネルに欄を新設するか）。決めたうえで V13_MZ_ALIGN_PPM を ver18 に足し make_v13_copy_with_settings で MZ_ALIGN_PPM を置換する。特徴量集合が変わるので利用者の承認が要る。(B) CALIBRATION_BY_SAMPLE: analysis_params.json には既に保存済みなので、load_calibration_from_first_analysis(2486付近)で calibration_by_sample も読み、V13_CALIBRATION_BY_SAMPLE 経由で list("name"=c(...)) を再注入する。併せて、共通係数が [0.0] かつ per-sample がある場合は『前回はサンプル別校正でした』と明示する（現状の『回帰式を検出』表示は誤誘導）。(C) USE_EMBEDDED_COMPOUND_NAMES: V13_USE_EMBEDDED_COMPOUND_NAMES を足すだけで数値は変わらない（mechanical）。(D) ANNOTATION_FILTER: 継承目的の対応は不要。


### 29. [S4] INPUT_NORMALIZED=TRUE 経路が spot 単位スケーリングを一切行わない(log1p/sqrt/none のみ)のは事実で、R 側に『入力が本当に正規化済みか』

**判定**: WEAKENED(独立検証)／**確証度**: [実行確認]／**修正区分**: 機械的修正・数値不変

> **訂正後の主張**: LogNormalize の scale.factor S=1e4 は Seurat 既定の踏襲であり、MSI に対しても実害がほぼ無い。log1p(x*S) は x の単調増加変換なので Wilcoxon の p 値は S に厳密に不変(実測 Δp=0)、FindVariableFeatures(vst)は counts レイヤーを読むので HVF 選抜も無関係、k-means クラスタも S=1e2〜1e6 で完全一致(ARI=1.0)、avg_log2FC も順位相関 0.999 以上。また『微量 feature の分散が過大評価される』という機構は向きが逆で、log1p は小さい値ほど log より強く圧縮し、しかも ScaleData が単位分散へ z 化する。C11 として残る有効成分は『apply_input_norm が INPUT_NORMALIZED を盲信し入力の正規化状態を検証しない』ことのみで、S4 相当。

**利用者から見た症状**

scale.factor そのものによる目に見える不具合は無い(値を変えてもクラスタも p 値も変わらないことを実測で確認した)。残るのは、正規化 OFF を選んだときにアプリが「本当にこのデータは正規化済みですか」を一切チェックせず、設定を鵜呑みにして走る点だけである。

**科学的影響**

scale.factor=1e4 は解析結果の数値をほぼ変えないため、論文への影響は無視できる(Wilcoxon p は厳密不変、クラスタは ARI=1.0 で同一、avg_log2FC は順位相関 0.999 以上)。ただし反証作業中に、はるかに重い別問題を発見した: Seurat の avg_log2FC は data レイヤーが log1p スケールである前提で expm1() を掛ける(differential_expression.R:1088/:1111)。expm1 は x>709.78 で Inf に発散するため、NORM_MODE='none' を選ぶと MSI の通常強度(>=1e3)で群平均が Inf になり avg_log2FC = Inf - Inf = NaN、C02 と同じ which()(:568)で全 feature が無言で脱落する。'sqrt' も強度 1e6 級で溢れ、溢れない範囲でも群平均が 209 対 13(log1p)と桁違いになり logfc.threshold=0.25 が無意味になる。一方 Wilcoxon の p は 3 モードで完全に同一(4.46329447611e-18)なので、利用者は『p 値は出ているのにマーカーが一つも出ない』という不可解な状態に陥る。これは C11 とは独立の新規発見として別 id での起票を推奨する。

**検証の経緯(反証の試みと結果)**

R11 の『scale.factor S=1e4 が MSI のダイナミックレンジに合っておらず FindVariableFeatures / PCA の重み付けに影響する』という主張は、数値検証と Seurat 原典の両方で実質的に反証された。(1) Wilcoxon の p 値は S にまったく依らない。log1p(x*S) は x の単調増加変換なので pixel 間の順位が S に依らず、S=1e2/1e4/1e6 で p の最大絶対差は 0.000e+00(実測)。(2) HVF 選抜にも最初から無関係。既定 selection.method='vst' は counts レイヤーを読む(preprocessing.R:4221-4222、preprocessing5.R:78-79)ので、scale.factor も NORM_MODE も影響しない。(3) クラスタリング結果も同一。S=1e2/1e3/1e5/1e6 と S=1e4 の k-means クラスタの ARI はすべて +1.0000。(4) avg_log2FC も Spearman 順位相関 >= 0.999、S=1e4 対 1e5/1e6 の最大差は 0.0048。差が 0.33 に達するのは S=1e2 という極端値のみ。(5) 主張の機構自体が逆向きである。d/dx log1p(x)=1/(1+x) < 1/x=d/dx log(x) なので log1p は小さい値ほど log より強く圧縮する(実測: x=0.1 での 2 倍変化が生む差 0.0870 対 log の 0.6931)。したがって微量 feature の分散は過小になるのであって『過大評価』にはならない。加えて ScaleData が feature ごとに単位分散へ z 化するため『分散の大小で重み付けが変わる』という筋道自体が成立しない。よって scale.factor 論点は単独では INFO 相当。残る有効成分は R1 が挙げた『apply_input_norm(v16:102-120)が INPUT_NORMALIZED を盲信し、counts の列和変動や整数性を一切検証しない』ことだけであり、これは事実で S4 相当。総合して WEAKENED / S4 とする。なお反証作業中に、C11 が触れる『NORM_MODE の選択肢は性質が大きく異なる』を支持するが理由がまったく別の重大事実を発見した(scientific_impact 参照)。これは C11 とは別 id での起票を推奨する。

**根拠**

- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:73: NORM_MODE <- "log1p"  / :108-111 dat <- switch(NORM_MODE, "none"=cm, "sqrt"=sqrt(cm), "log1p"=log1p(cm),`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:117: s <- NormalizeData(s)  ← scale.factor は Seurat 既定 1e4 のまま(引数指定なし)`
- `(原典) seurat/R/preprocessing.R:4221-4222: if (selection.method == "vst") { data <- GetAssayData(object = object, slot = "counts")  ← HVF は counts を読む`
- `(原典) seurat/R/preprocessing5.R:78-79: if (selection.method == 'vst') { layer <- layer%||%'counts'`
- `(実行) /tmp/.../scratchpad/tmp/w4s/c10_c11_norm.py C11-A: S=1e2/1e4/1e6 で Wilcoxon の p の最大絶対差 = 0.000e+00`
- `(実行) /tmp/.../scratchpad/tmp/w4s/c11_extra.py: S=1e2/1e3/1e5/1e6 いずれも ARI(S=1e4 のクラスタと)=+1.0000, avg_log2FC の spearman >= 0.999, 最大差 S=1e5 で 0.0048`
- `(実行) 同上 C11-E: x=0.1 の 2 倍変化が生む log1p 差 0.0870(log は 0.6931) ← log1p は小さい値ほど『強く』圧縮する=主張の向きが逆`
- `App/Script/DESI/260623_DESI-UMAP_Template_v16.R:102-120: apply_input_norm <- function(s) { if (isTRUE(INPUT_NORMALIZED)) { ...  ← counts の列和変動も整数性も検証しない(R1 の指摘は成立)`

**修正方針**

C11 本体(scale.factor)は修正不要。報告書では INFO へ格下げするか項目ごと落とす。実施すべきは (a) apply_input_norm に入力 sanity check(counts 列和の変動係数、整数性)と警告ログを追加(数値は変わらない、C10 の修正と共通)、(b) 新規起票として NORM_MODE が 'none'/'sqrt' のときに Seurat の avg_log2FC が破綻する件のガード(該当モード選択時に警告するか、DEG の mean.fxn を NORM_MODE に合わせて差し替える)。(b) は挙動が変わるため別途承認が必要。
