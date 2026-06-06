# Changelog

このプロジェクトの全ての顕著な変更を記録する。
バージョンは `<日付>_ver<番号>` (`App/app/version.py`) と連動する。

修正をリリースするたびに以下 3 箇所を必ず同期する:
- `App/app/version.py` の `APP_VERSION` と `RELEASE_DATE`
- 本ファイル (`CHANGELOG.md`) に新エントリを追加
- コミットメッセージのタイトル末尾に `[verX.Y]` を付ける

付番ルール: バグ修正のみ → パッチ +0.1 / 機能追加 → メジャー +1.0

---

## 2026-06-06_ver4.21

### 機能: SCiLS 化合物アノテーションの埋め込み＋化合物名表示
SCiLS peak-list の `Name`（パイプ区切り：化合物名＋分類/DB/adduct/ppm/分子式/SMILES 等）を取り込み、
m/z ではなく化合物名で feature を扱えるようにした。**注釈の無いデータは完全に現状維持**（加算的）。

- **新規 `peak_annotation.py`（パーサ）**: `Name` を構造化（adduct `[M…]` を起点に化合物名と adduct の
  間のフィールド数で分類/DB を確定、`key=value` はキー名で格納、`No DB hit` は m/z 表示、raw 全文保持）。
- **SCiLS 変換（`scils_converter.py`）**: フォルダ内 peak-list CSV を**自動検出**（新 role）。強度 parquet の
  m/z 列名を `化合物名_<m/z 4桁> | …(全文)` に**埋め込み置換**しつつ、フル桁 m/z は schema メタデータ
  `mz_sorted` に保持（列名がパイプ全文でも m/z を確実に復元）。per-feature 注釈の**サイドカー
  `*_feature_annotations.parquet`** も出力。
- **m/z 検出の改修**: 列名から m/z を読む処理を「`mz_sorted` メタ＋列除外」方式へ（Python `data_manager.py`、
  R `260422_DBSCAN_With_cluster_ver3_no-png_slim.R` の parquet 読込）。Seurat の feature 名は従来どおり
  `m/z %.5f`（安全キー）を維持し、特殊文字での名前破壊を回避。
- **運搬（サイドカー → キャッシュ）**: 解析実行時にサイドカーを結果フォルダへコピー（`analysis_runner.py`）、
  抽出時に `seurat_bridge.py` が features_list へ m/z で join し `extraction_meta` 隣の
  `feature_annotations.json` としてキャッシュ→インタラクティブへ供給。
- **優先（スキップ）**: 外部注釈ありデータは、in-app の m/z キャリブレーションと CSV 照合をスキップし、
  `annotation_map` を外部表から直接構築（`interactive_callbacks.py`）。
- **表示**: Feature Plot に「化合物名で表示（m/z ⇄ 化合物名）」トグル（既定 ON）を追加。既存の Feature
  選択・DEG（Volcano/Heatmap）は `annotation_map` 経由で自動的に化合物名を表示。
  ※ Spatial Mapping はクラスタ表示で feature 名を持たないため対象外。
- 退行防止: 新規 `tests/test_peak_annotation.py`（パーサ／変換器埋め込み＋サイドカー＋メタ／data_manager
  の埋め込み列検出）。`222 passed`。R 変更は実 R 必須のためデプロイ後 E2E で確認。

---

## 2026-06-05_ver4.20

### 整理: 動作していなかった残骸UI/Storeを3点削除（結果不変）
ver4.19 の調査で判明した「効かない/重複した」部品を、正規の代替が存在することを確認のうえ削除。
解析結果・既存機能には影響なし。

- **サンプル選択Checklist（`interactive_msi_sample_checks`）削除**：インタラクティブ解析タブの
  非表示ブロック内にあり、選択値を誰も読まない孤児だった。正規のサンプル選択は設定タブの
  `selected_samples`→`selected_samples_store`→解析実行(`run_analysis`)が担っており重複のため、
  チェックリストと自動/手動スキャン callback（`scan_msi_files`/`auto_scan_msi_files`/
  `_build_msi_samples_ui`）およびスキャンボタンを除去。MSIフォルダパス欄
  （`interactive_msi_folder`：プロジェクト復元/データ書き出しが参照）は残置。
- **RDS選択Checklist（`selected_rds_files`）削除**：設定タブで表示されるが選択値を誰も読まない。
  解析が使うRDS指定は `rds_folder`/`rds_path`/`resume_rds`/`rds_folder_reanalysis` で成立済み。
  `rds_file_selector` コンテナと `update_rds_file_selector` callback を除去（`rds_folder` 欄は残置）。
- **死蔵Store（`cal_preset_loading_flag`）削除**：読み書きする callback が皆無の完全な未使用 Store。
  プリセット機能本体は別IDで正常動作。
- 退行防止: 削除した全IDの参照ゼロを確認、`208 passed`、全タブのレイアウト構築も確認。

---

## 2026-06-05_ver4.19

### 機能: インタラクティブ解析のデータ読込「キャンセル」を再実装
- ver3.8 で読込を background 長時間コールバックから foreground 化した際、キャンセルボタン
  (`btn_cancel_load`) の表示(`running=`)と中断(`cancel=`)配線が消え、ボタンだけが取り残されて
  機能しなくなっていた退行を修正。**Rサブプロセスを実際に kill する協調的キャンセル**として復活。
- 仕組み: 単一プロセス・マルチスレッド構成を活かし、ロード token ごとの `threading.Event` を
  モジュール内 registry で共有。Stage A で token を発行しキャンセルボタンを表示、Stage B の R 抽出を
  `subprocess.Popen`＋0.3秒ポーリングで実行し、キャンセル時はサブプロセスを kill→部分キャッシュ掃除
  →`ExtractionCancelled` で中断（後続ステージは起動しない）。キャンセルボタンの表示は進捗コンテナの
  可視状態に追従させ、各ステージの戻り値を増やさず実装。
- 非キャンセル経路（通常読込・Feature/expression 抽出など他の呼び出し）は従来どおり `subprocess.run`
  のままで挙動不変。`seurat_bridge._popen_with_cancel` を分離し単体テストを追加。
- 変更: `seurat_bridge.py`（`_popen_with_cancel`/`ExtractionCancelled`/cancellable 抽出）、
  `interactive_callbacks.py`（token registry・表示追従・キャンセル callback・Stage A/B 配線）、
  `interactive_tab.py`（`load_token_store`）。解析結果には影響なし。

---

## 2026-06-05_ver4.18

### 改善: DESI 空間平滑化の高速化（結果不変）＋ Otsu スポット除去 QC 画像の保存・表示
- **平滑化の高速化（結果ビット一致）**: `Script/DESI/260422_DESI-UMAP_Template_v14.R` の
  `spatial_smooth_seurat` の近傍探索を、1スポットずつの O(n²) ループ（ver2.2 で OOM 防御のため
  15,000 スポット超は総当たりにフォールバックしていた）から **k-d 木の固定半径探索 `dbscan::frNN`**
  に置換。自分自身を足し戻し、近傍は現行と同一の `<= radius` で確定、距離・重みは現行と同一式で
  再計算するため、**平滑化結果は現行（>15,000 スポットの総当たり経路）とビット一致**。`dbscan` は
  導入済みで新規依存なし。失敗時は総当たりへフォールバック。保存されない無駄な平滑化プロット生成も削除。
- **Otsu QC 画像の保存**: `filter_low_count_spots` がコメントアウトで未保存だった「ヒストグラム＋
  空間分布＋Filtered＋Otsu分散」の結合 PNG（`spot_filtering_<sample>_otsu.png`）を `safe_ggsave` で
  保存（QC 画像生成失敗は解析を止めない）。解析結果（クラスタ/UMAP/DEG）には影響しない。
- **表示**: インタラクティブ解析の画像ギャラリーは出力フォルダを走査し "Filtering" カテゴリで自動表示
  （カテゴリ選択に "Filtering" を追加）。簡易ビューアー(/lite) には `_build_spot_filtering_section` を
  新設し、保存 PNG を base64 で埋め込む節を末尾に追加（画像が無ければ節を出さない）。
- ※R は本リポジトリ環境でテスト不可・デプロイ後に実機確認。クラスタリング(Leiden)バックエンドは
  変更しない（igraph 化は v5 で結果が変わるため見送り）。

---

## 2026-06-05_ver4.17

### バグ修正: DESI マルチサンプル解析が `Seurat::merge` で停止する
複数サンプル（または ROI モードで複数 ROI）の DESI-UMAP を実行すると、`Multi-sample mode: Harmony...`
の直後に `Error: 'merge' is not an exported object from 'namespace:Seurat'` で停止し、Harmony / RPCA が
一切出力されない問題を修正。
- **原因**: `Script/DESI/260422_DESI-UMAP_Template_v14.R` の Harmony 統合で `Seurat::merge(...)` を
  呼んでいた。Seurat オブジェクトの `merge` は S3 メソッド `merge.Seurat`（基本ジェネリック `merge` に
  登録）で、Seurat 名前空間に `merge` という名のエクスポート済みオブジェクトは無いため、`::` 呼び出しが
  必ず失敗する。2026-05-23 の [ver3.8]（左結合 O(n²) → 一括 O(n) への性能改善）で `Seurat::` を付けて
  しまったリグレッション。
- **修正**: `Seurat::merge(` を素の `merge(` に変更（`y = list` の一括マージ書式・性能改善は維持）。
  基本ジェネリックが `merge.Seurat` へ S3 ディスパッチする。旧 DESI テンプレ・全 TIMS スクリプトと
  同一の正しい書式。R テンプレートのファイル名（v14）は据え置き。
- ※R は本リポジトリ環境でテスト不可・デプロイ後に 4 サンプル DESI で実機確認（Harmony/RPCA 出力）。

---

## 2026-06-04_ver4.16

### バグ修正: DESI の ROI 選択に測定ピクセル連番が大量に並ぶ
DESI 生データ読込時、「ROI 選択 (DESI)」に測定ピクセルの連番（1, 10, 100, 10000…）が個別 ROI
として列挙される問題を修正。原因は、ROI 列を「列数が 3+metabolite を超えたら最右列」と判定して
いたため、末尾の `line`/`pixel`（ピクセル連番）列を ROI と誤認していた。
- **修正（UI 本丸）**: `read_desi_roi_list`（`data_manager.py`）を**内容ベース判定**に変更。
  データ列を右から走査し、「非空値の過半が非数値の文字列ラベルで、ユニーク数が小さく行数未満」の
  列のみを ROI 列として採用（数値のピクセル/ライン連番列は除外）。列番号推定に依存しないため
  ヘッダのズレにも強い。これで TIMS の annotation と同様に**領域ラベルだけ**が選択肢に出る。
- **修正（R 解析・整合）**: `Script/DESI/260422_DESI-UMAP_Template_v14.R` の ROI 検出も同基準に変更
  （ROI-as-sample 解析がピクセル単位で分割されないように）。※R は本リポジトリ環境でテスト不可・
  デプロイ後に実機確認。
- **テスト**: `read_desi_roi_list` を tmp DESI 風 .txt で検証（line/pixel のみ→空、文字列ラベル列→
  ラベル一覧、ラベルが line/pixel の手前にあっても検出）。

---

## 2026-06-04_ver4.15

### バグ修正: サブプロジェクト/プロジェクトが削除できない（ver4.14後の本丸）
ver4.14（削除コールバックの出力数是正）後も削除が無反応だった問題を修正。真因は所有権ガードと
エラーの非表示だった。
- **所有権ガードを無効化**: `delete_sub_project` / `delete_project` は `created_by`（ログイン時の解析者名）
  一致を要求し、不一致だと `ProjectAccessDenied` で削除を拒否していた（`can_modify_project`）。削除は
  ログイン中のユーザーであれば作成者に関わらず可能とするため、両削除ハンドラ
  （`project_callbacks.py`）の呼び出しを `enforce_owner=False` にした。`project_manager` の関数定義・
  既定値は不変。
- **通知トーストをグローバル化**: 失敗/成功トースト先 `notification_toast` が `page_analysis`（一覧表示中は
  `display:none`）の中にあり見えなかった。`main_layout.py` の最上位へ移動し、全ページで成否・理由が
  見えるようにした。
- 補足: 削除は projects.json のメタデータのみ削除で、ディスク上の解析データ（生データ/結果/RDS）は
  消えない（従来通り）。
- **テスト**: `delete_sub_project(enforce_owner=False)` が created_by 不一致でも削除できることを検証。

---

## 2026-06-04_ver4.14

### A) バグ修正: サブプロジェクトが削除できない
`handle_delete_sub_project`（`project_callbacks.py`）が、デコレータの Output 宣言が2個だけなのに
全 `return` が5個の値を返していた（`handle_delete_project` の5出力版からのコピペで
`notification_toast` の3出力が欠落）。Dash が出力数不一致でコールバックを失敗させ、削除が
無反応になっていた。**修正**: デコレータに `notification_toast`(is_open/children/icon) の3出力を
追加し、5出力＝5戻り値に揃えた（エラー時はトースト通知も出る＝プロジェクト削除と同等）。

### B) コンテナとSFTPユーザーの権限共有（Permission denied 再発防止）
アプリ(コンテナ uid 1002)が `/srv/msi` 配下に作るフォルダが SFTP ユーザー(グループ msi-lab)から
書けない問題の恒久対応。
- `App/run_app.py`: 起動時に `os.umask(0o002)` を設定。生成ファイル/ディレクトリがグループ書込可
  (664/775)になり、R サブプロセスも親 umask を継承する。
- `docker-compose.yml`: `msi-app` に `group_add: ["${MSI_SHARED_GID:-1001}"]` を追加し、アプリを
  共有グループ(msi-lab)へ参加させる（primary group は不変）。
- `.env.docker`: `MSI_SHARED_GID` を追記（既定 1001）。
- サーバ側で `chown -R root:msi-lab /srv/msi && chmod -R 2775 /srv/msi`、SFTPユーザーを msi-lab に
  追加することで、setgid 継承によりアプリ生成フォルダもグループ msi-lab・グループ書込可になる。
  ※デプロイは「app を group_add 付きで再ビルド → その後 chown/chmod」の順で行う。

---

## 2026-06-04_ver4.13

### バグ修正: クラスタ名変更を手法(Harmony/RPCA)ごとに独立化
クラスタ名変更マップが単一キー `cluster_name_map` で保存され、Harmony と RPCA で**共有**されて
いた。両者はクラスタの中身が異なるため、**手法ごとに独立**して保存・表示・出力するよう修正。

- **永続化**: `label_persistence.py` に `cluster_name_map_key(method)` /
  `load_cluster_name_map(rds_path, method)` / `save_cluster_name_map(rds_path, method, value)` を
  追加。キーを `cluster_name_map::<手法>` にし、手法別キーが無ければ旧 `cluster_name_map` に
  フォールバック（既存リネームを失わない移行）。
- **適用/復元**: `apply_cluster_rename` / `load_saved_cluster_name_map`
  （`interactive_cluster.py`）に `interactive_integration_method` を渡し、手法別に保存・復元。
  手法切替（再読込）時に該当手法の名前が表示される。
- **データ出力**: 多手法出力で、各手法のクラスタ列にその手法の変更名を適用
  （`_build_all_method_lookups` が他手法分を `load_cluster_name_map` で読み込む）。
- **テスト**: 手法別の独立保存・復元と旧形式フォールバックの検証を追加。

---

## 2026-06-04_ver4.12

### ① 生データフォルダの自動保存（出力時の推定フォールバックを解消）＆ ② 出力にクラスタ変更名
- **① data_folder 自動保存**: サブプロジェクトの `data_folder`（MSIデータフォルダ）が空だと、
  データ出力が推定フォールバックに落ちていた。これを常に埋まるようにした。
  - **解析完了時**: `run_analysis` 完了処理で、解析に使った生データフォルダを
    `update_sub_project(..., {"data_folder": data_folder})` で保存（新規解析は常に埋まる）。
  - **既存プロジェクトの自己修復**: ヘルパー `ensure_sub_project_data_folder()` を新設し、
    `load_stage_d_finish`（データ読込完了）から呼ぶ。`data_folder` が空なら、instrument を
    パスから解決(`_resolve_instrument`)→プロジェクト内限定推定(`_infer_data_folder`)で解決し
    保存する。既存サブプロジェクトを開くたびに空欄が埋まる。
- **② 出力にクラスタ変更名**: データ出力(Excel/CSV/Parquet)のクラスタ列を、画面で変更した
  クラスタ名で表示。`_build_cluster_lookup` の値を `str(cluster)` →
  `cluster_display_name(cluster, cluster_name_map)`（変更名があれば名前、無ければ番号）に変更し、
  `data_export_stage_b` に `cluster_name_map_store` を渡すようにした。
- **テスト**: `ensure_sub_project_data_folder`（空→推定保存／既存は不変）と、
  `_build_cluster_lookup` の変更名反映を追加。

---

## 2026-06-04_ver4.11

### バグ修正(ver4.10のリグレッション): データセット直下の生データを「見つかりません」と誤判定
ver4.10 で別プロジェクト混入対策として `_infer_data_folder` の走査をプロジェクト内に限定した際、
**サブフォルダの中だけ**を走査するようになり、生データ `.txt` が**データセットフォルダ直下**
（＝結果フォルダの親、例: `251213_Kizu-Embryo-E14-P0/` 直下の `*_PN.txt`）に置かれている運用で
「MSIデータフォルダが見つかりません」になっていた問題を修正。

- **修正**: `_infer_data_folder` で、サブフォルダだけでなく**走査ルート自身**（結果フォルダの親＝
  データセットフォルダ）も MSI データ有無を確認するようにした。プロジェクトルート配下のみを
  対象にする制約は維持しているため、別プロジェクト混入は引き続き起きない。
- **テスト**: `test_infer_data_folder_finds_data_directly_in_dataset_dir` を追加。

---

## 2026-06-04_ver4.10

### データ出力: ①進捗表示の追加 ＆ ②別プロジェクト混入の防止
「データ出力 (UMAP cluster)」に進捗表示を追加し、別プロジェクトのデータが出力される
重大バグを修正。

- **② 別プロジェクト混入の修正（最優先）**: 開いているプロジェクト（Embryo）を出力したのに
  別プロジェクト（TDLN/LN）の結果（`LN`/`TDLN`/`ROI` annotation）が出力される問題。
  - 原因1: `_infer_data_folder` が結果フォルダの**親の親（=全プロジェクト共通の Data ルート）**
    まで走査し、最初に見つかった**別プロジェクトのデータフォルダ**を返していた。
  - 原因2: `_set_active_key` が条件付きで、`plot_data` が別/既定プロジェクトになり得た。
  - 修正: データフォルダ推定を**プロジェクトルート配下に限定**（`_project_root_for` /
    `_is_within` を追加、Data ルートは走査しない）。出力本体 `_do_export` で
    `seurat_rds_path_store`（=実際に読み込んだ RDS）から**無条件にアクティブキーを固定**し、
    常に開いているプロジェクトの `plot_data`/クラスタを使う。
- **① 進捗表示の追加**: `cb_export_data` を前景 2 段チェーン（Stage A: 進捗バー表示＋ボタン無効化
  ＋トリガ / Stage B: 出力実行→ダウンロード→「完了」表示）に分割。`data_export_progress_*` を
  `interactive_tab.py` に追加。読込チェーンと同じ前景方式（background は `_interactive_data` の
  インプロセス状態を fork worker が共有できないため不可）。
- **テスト**: `App/tests/test_data_export.py` に `_project_root_for`/`_is_within` と、
  `_infer_data_folder` が別プロジェクトのフォルダを返さないことの検証を追加。

---

## 2026-06-04_ver4.9

### バグ修正: DESIプロジェクトの「データ出力」がTIMS経路に入り別ファイルが出力される
DESIサブプロジェクトで「データ出力 (UMAP cluster)」を押すと、本来の「生データ＋クラスター番号」
ではなく統合ベンチマーク指標表に空のクラスター列を付けた `UMAP_cluster_TIMS.xlsx` が出力される
問題を修正（DESI のみ対応。TIMS 入力ファイルの検証強化は別途）。

- **原因**: instrument 判定が `sub.get("ms_instrument", "TIMS")` で、DESIサブプロジェクトに
  metadata 未設定だと既定の "TIMS" にフォールバック → エクスポートが TIMS 経路
  (`_export_tims`) に入り、データフォルダ内の parquet/csv（指標ファイル）を無検証で読み込んで
  いた。
- **修正**: `interactive_data_export.py` にヘルパー `_resolve_instrument()` を追加し、明示
  "DESI" を優先しつつ未指定/曖昧時はパス規約 (`Data/DESI/Data` ・ `Data/TIMS/Data`) から
  instrument を判定。`cb_export_data` の「データフォルダ自動推定」と「DESI/TIMS 分岐」の両方に
  適用し、DESIプロジェクトを `_export_desi`（元 `.txt` → サンプル別シート＋「UMAP cluster」列）に
  正しくルーティングする。
- **テスト**: `App/tests/test_data_export.py` を追加（`_resolve_instrument` の判定、
  `_export_desi` がサンプル別シートにクラスター番号を付与することを検証）。
- 注: 「TIMS 経路でも指標ファイル等を拾い得る」入力検証の強化は今回スコープ外（別途対応）。

---

## 2026-06-04_ver4.8

### バグ修正: 変更したクラスタ名が「クラスタ名変更」パネルに表示されない
ver4.7 でクラスタ名の保存・復元は直ったが、「クラスタ情報 > クラスタ名変更」パネルの
入力欄とラベルが、変更後の名前ではなく元のID（1,2,3…）のまま表示されていた問題を修正。
（クラスタ統計表・円グラフ・Top5 には変更名が出ていた。）

- **原因**: `populate_cluster_rename_panel` が保存名マップを `State("cluster_name_map_store")`
  で読み、これを復元する `load_saved_cluster_name_map` と同じ `seurat_rds_path_store` で
  同時発火するため、パネル生成時には store がまだ空(`{}`) で、入力欄が空・ラベルがIDのまま
  描画されていた（順序依存）。統計表/円グラフ/Top5 は同 store を `Input` で受けるため
  正しく反映されていた。
- **修正**: `populate_cluster_rename_panel` の `cluster_name_map_store` 依存を
  `State` → `Input` に変更。保存名ロード直後や「適用」「リセット」後にもパネルが再生成され、
  既存の `value=current_name` / `display_label` ロジックで変更名がプリフィル表示される。
- **テスト**: `App/tests/test_cluster_rename_persistence.py` にパネル生成の値反映テストを追加。

---

## 2026-06-04_ver4.7

### バグ修正: クラスタ名・色・Spatial 設定の変更が再オープンで消える
インタラクティブ解析で「クラスタ名の変更 → 適用」後、ページ再読み込みやプロジェクト
再オープンで変更が失われる問題を修正。

- **原因**: 永続化を行う一部コールバックが、プロジェクト別 state を切り替える
  `_set_active_key(rds_path)` を呼んでおらず、保存先 `rds_path` が `__default__`(None) に
  解決されて `interactive_settings.json` に書き込まれていなかった（適用直後はメモリ上の
  Store にのみ反映されるため、閉じると消えていた）。
- **修正**: 対象コールバックに `seurat_rds_path_store` を渡し、冒頭で
  `_set_active_key(rds_path)` を呼ぶよう統一（正常動作していた `update_sample_name_map`
  と同じ作法に揃えた）。
  - `apply_cluster_rename` / `load_saved_cluster_name_map`（クラスタ名・報告事象）
  - `update_custom_color_map`（クラスタ色）
  - `update_rotation_store_from_per_sample`（Spatial 回転/反転）
  - `save_spatial_display_settings`（Spatial 表示設定）
- **テスト**: `App/tests/test_cluster_rename_persistence.py` を追加（プロジェクト別の保存先
  隔離、リネームの永続化と復元を検証）。

---

## 2026-05-26_ver4.6

### インタラクティブ解析: 段階的ローディング進捗 + 失敗原因の表示
「データを読み込む」実行中、結果が出るまで画面が無反応で「読み込み中か固まったか」が
判別できなかった問題を改善。読み込み処理 (`load_interactive_data`) を foreground のまま
**4 リンクの連鎖コールバック**に分割し、処理段階に応じた進捗メッセージを表示する。

- **段階メッセージ**: 「RDSデータを抽出中…（最大2分程度）」→「マーカー(DEG)を読み込み中…」
  →「設定を復元中…」→「完了」。各メッセージは実際の処理境界に同期（Dash の仕様上、
  メッセージは次段の重い処理が始まる前に描画される）。既存の未使用だった進捗 UI
  (`load_progress_container` / `load_progress_bar` / `load_progress_label`) を再利用。
- **失敗原因の明示**: RDS 未検出 / Rエラー(stderr 末尾) / タイムアウト(10分) / Rscript 不在 /
  抽出結果が空 などを `interactive_data_info` に赤アラートで表示し連鎖を停止。
  DEG 未検出と m/z キャリブレーション失敗は非致命（読み込みは継続、警告のみ表示）。
- 手動ボタン・サブプロ/共有の自動読み込みの両経路で進捗が出る。
- 背景: background callback は fork worker で `_project_states` を共有できないため
  foreground を維持。中間データは同一プロセスの `_get_state(rds_path)` で受け渡す。
- テスト: `App/tests/test_interactive_load_chain.py`（bridge mock による連鎖進行・各エラー
  分岐・active key 隔離・非致命系の 16 ケース）を追加。

---

## 2026-05-25_ver4.5

### TIMS 解析スクリプト ver4（方法論の安全化）+ webアプリ対応
TIMS UMAP 解析 R スクリプトを **ver4** に更新し（旧 ver3 は保持）、過補正・二重正規化など
の方法論リスクを是正。あわせて webアプリで無補正 PCA 結果を手法として選択可能にした。

- **R: `App/Script/TIMS/260525_DBSCAN_With_cluster_ver4_no-png_slim.R`（新規。ver3 から版を 1 つ進める）**
  - ① 過補正の防止: バッチ補正は技術的バッチ(`BATCH_VAR='sample'`)に対してのみ実施。
    単一 sample（切片＝生物学的 ROI/群）では Harmony/RPCA をスキップし無補正 PCA を使用。
    `condition`/`slice_id` 補正は `ALLOW_CONDITION_CORRECTION=TRUE` のときのみ許可。
  - ② 二重正規化の回避: `INPUT_NORMALIZED=TRUE`（SCiLS RMS 等で正規化済み入力）なら
    LogNormalize を行わず `NORM_MODE`("none"/"sqrt"/"log1p") のみ適用。
  - ③ マーカー表記の是正: `markers_annotated.csv` に `ranking_type`/`inference_note` 列を追加し、
    Volcano に副題を付与。「ピクセル単位の探索的ランキングであり群間の統計的推論ではない」旨を明記。
  - ④ 無補正 PCA の併走出力: 補正使用時も `Step2_PCA_uncorrected.rds` を別途出力（prefix `pca_uncorrected`）。
  - ※ 既定フラグでは複数 sample の補正挙動は従来どおり。単一 sample の挙動が「無補正 PCA」へ変わる点に注意。
- **webアプリ: 無補正 PCA を手法として選択可能化**
  - `interactive_callbacks._detect_integration_methods`: `Step2_PCA_uncorrected.rds` を
    "PCA (uncorrected)" として検出（`deg_`/`plotdata_` の data.frame RDS は誤検出しない）。
  - `deg_utils.load_deg_results`: 手法名 "PCA (uncorrected)" → 出力フォルダ `pca_uncorrected` を解決。
- ※ R スクリプトはこの環境では実行検証不可。実機での動作確認を推奨。

---

## 2026-05-25_ver4.4

### パフォーマンス改善（共有URLを開いたときの読み込み高速化）
- **Seurat 抽出キャッシュを永続化**（再デプロイで消えない）
  - 従来 `/tmp/msi_seurat_cache`（Docker 非永続）→ コンテナ recreate のたびに
    消え、共有を開く受信者が毎回コールド R 抽出（数十秒級）を踏んでいた
  - `config.py`: `SEURAT_CACHE_DIR` を環境変数で上書き可能化（既定は従来の tempdir）
  - `docker-compose.yml`: 専用ボリューム `msi-seurat-cache` を
    `/app/Data/Other/seurat_cache` にマウントし `SEURAT_CACHE_DIR` を設定。
    `SEURAT_CACHE_MAX_ENTRIES` を 12→30 に引き上げ
  - ※専用ディレクトリ必須（LRU 退避 `_evict_seurat_cache_lru` が配下サブ
    ディレクトリを削除するため、diskcache `/app/Data/Other/cache` とは別ボリューム）
- **共有リンク生成時にキャッシュをバックグラウンド・プリウォーム**
  - `project_callbacks.generate_share_link`: 受信者が最初に見る既定手法
    （Harmony 優先）の RDS を daemon スレッドで先行抽出。受信者は初回から
    ウォームで開ける。失敗しても共有作成には影響させない（best-effort）
- **二重抽出の防止**
  - `seurat_bridge.extract_data`: ベース抽出を FileLock で保護
    （`ensure_expression_matrix` と同パターン）。プリウォームと受信者の初回
    オープンが同時でも R 抽出は 1 回のみ。通常の同時初回オープンにも有効

### 注意
- 反映後、新パスでキャッシュを作り直すため各 RDS で初回のみコールド 1 回。以後永続。

---

## 2026-05-25_ver4.3

### バグ修正
- **共有リンクが受信側で開けない問題を修正**（生成 URL が Docker 内部アドレスだった）
  - 症状: 生成された共有 URL が `http://172.18.0.3:3838/...`（コンテナ内部 IP + 内部
    ポート）になり、外部の受信者は `ERR_CONNECTION_TIMED_OUT` でアクセス不能
  - 原因: `SHARE_BASE_URL` 未設定時、`build_share_url` / `build_persistent_view_url`
    が `socket.gethostname()` にフォールバックし、コンテナの Docker bridge IP を採用
    していた（`.env.docker` の `SHARE_BASE_URL` も空だった）
  - 修正:
    - 新 `services/url_utils.external_base_url()`: 未設定時はアクセス元ホスト
      （`request.host` + `X-Forwarded-Proto`、Caddy 等のプロキシ対応）から
      公開 URL を組み立てる。内部 IP フォールバックは request 文脈外の最終手段に降格
    - `.env.docker` の `SHARE_BASE_URL` に本番アドレス `https://133.167.73.188` を設定
      （明示が最優先）。`.env.example` の説明も更新
  - 効果: 既存の共有リンク（token は不変）も、再デプロイ後は表示 URL が
    `https://133.167.73.188/view/<token>` に修正され、作り直し不要で開けるようになる

---

## 2026-05-25_ver4.2

### 新機能
- **共有のパスワード要否を「期限」と独立化**（無期限/期間付き × パスあり/なし の自由な組合せ）
  - 従来は「期間付き=常にパス必須／無期限=常に認証なし」と固定だったのを、
    共有ごとの **`require_password` フラグ**で制御するよう変更
  - 共有作成モーダルに **「🔒 パスワード保護」スイッチ**を追加（既定 ON）。
    共有方式ラジオは「有効期限の有無」だけを選ぶ意味に整理
  - `auth_middleware.py`: `/view/` の無条件バイパスを廃止し、`/share/`・`/view/`
    ともにトークンからレコードを引いて `require_password` で認証要否を判定
    (`_share_password_required`)。見つからないトークンは fail-closed で認証要求
  - `share_manager.create_share`（既定 True）/ `persistent_share_manager.create_persistent_share`
    （既定 False）に `require_password` を追加
  - 認証なし警告は「パスワード保護 OFF」のときのみ表示するよう連動
- **無期限共有の一覧・削除 UI を追加**
  - サブプロジェクト一覧「共有リンク管理」に無期限共有も併記（期間付き/無期限の
    バッジ・🔒/🔓 表示・閲覧数）。`list_persistent_shares` を UI に接続
  - 削除は既存ボタンを流用し、`revoke_persistent_share` も試行して該当を失効

### 後方互換
- 既存レコード（フラグ無し）は従来どおり: `/share/`=パス必須、`/view/`=認証なし。

### 検証
- ログイン要否: パス必要 share の `/share//view/` は未ログインで /login へ、
  パス不要 share は認証なしで開く（Flask test client + 実機 4 通り）
- 無期限共有が一覧表示され削除できる / パス OFF 時のみ警告表示

---

## 2026-05-25_ver4.1

### 新機能
- **共有リンク作成: 統合手法に「all（全手法を共有）」を追加**
  - 共有モーダルの「統合手法」セレクタに `all` を追加し、既定値に設定
  - `all` を選ぶと、共有先（受け手）は結果フォルダ内の全統合手法
    (Harmony / RPCA / PCA) を自動取得して**自由に切り替え可能**
  - `Harmony` など個別手法を選ぶと、共有先には**その手法のみ表示**される
    （「基本は全手法、特定手法だけ共有したいときは個別選択」というニーズに対応）
  - `action_page.py`: `share_integration_method` に `all` 選択肢 + 説明文を追加
  - `share_callbacks.py`: `route_share_url` が共有元の統合手法を
    `shared_session.integration_method` で受け手に伝達
  - `interactive_callbacks.py`: `auto_scan_rds_files` を共有対応にし、
    特定手法指定時は `interactive_rds_map` をその手法のみに限定

### 補足
- 既存の共有リンク（ver4.0 以前に作成、統合手法が個別値で保存済み）は、
  本変更後に開くとその手法のみ表示になる。全手法を見せたい場合は `all` で再共有する。

---

## 2026-05-24_ver4.0

共有モデルと認証を大きく見直したメジャーアップデート。

### ① 共有 = インタラクティブ解析の全機能 (操作可・保存あり)
- 共有リンクを従来の read-only ビューアから「インタラクティブ解析の
  全機能」に変更。共有先での色変更・クラスタマージ・ラベル編集などの
  操作は **元プロジェクトに保存される**。
- `share_callbacks.py`: `route_share_url` を全面改修。`/share/<token>`
  (期間付き) / `/view/<token>` (無期限) を解決し、`page_analysis` の
  interactive タブへ遷移 + `shared_session` を設定する 9 出力 callback に。

### ② パスワード無し共有
- 無期限共有 (`/view/`) は認証不要で開ける (既存の bypass を活用)。
- 共有作成モーダルの警告文を「操作可・元プロジェクトに保存される」旨に
  更新 (`action_page.py`)。

### ③ パスワード変更: ログイン済なら Master 再入力を省略
- bcrypt 保存のため現在値の事前入力は不可。代替として Tier A
  ログイン済なら Master 再入力なしで変更フォームを使えるよう緩和。
- `auth_middleware.py:_change_password_view` で master を任意化
  (入力時のみ照合)。モーダル/JS から該当必須を除去。

### ④ パスワードを Master + 共有用の 2 本に統合
- Master で日常ログイン (Tier A: プロジェクト一覧) + パスワード変更権限。
- 共有パスワード (旧 Password B) は共有 URL 閲覧用 (Tier B)。
- **Password A を廃止**。`_login_view` は Master→Tier A 判定に変更。
- `auth_service.py`: 初期化必須を共有パスワードのみに緩和
  (`password_a_hash` は後方互換で残置・参照しない)。
- `login.html` / 変更モーダル / `auth.js` の表記・項目を 2 本構成に整理。

### ⑤ 「インタラクティブ」ボタンで即時読込
- サブプロジェクト/共有から開いた際、「スキャン」「データを読み込む」を
  押さずに UMAP/Spatial が自動表示される。
- `interactive_callbacks.py`: `auto_load_on_rds_ready` callback を追加。
  RDS マップ準備完了 + entry_mode が sub_project/shared のとき自動 load。

### ⑥ 共有 URL では interactive タブのみ表示
- `project_callbacks.py`: `apply_shared_mode` callback を追加。
  `shared_session` 有効時に他タブのヘッダー (`shared-mode-tabs` で
  nav-tabs を非表示)・戻るボタン・ヘッダー操作ボタン群を隠す。
- サイドバー非表示・全幅化は既存 `toggle_sidebar_content`
  (interactive タブ選択時) が担当。
- `main_layout.py`: `main_tabs` を `main_tabs_wrapper` div で包み、
  `shared_session` Store を追加。`styles.css` に
  `.shared-mode-tabs .nav-tabs { display:none; }` を追加。

### 注意 / 移行
- **セキュリティ**: 無期限共有は認証なしの第三者が元データを変更可能に
  なる (ユーザー明示選択の仕様)。共有作成時の警告を確認すること。
- Password A でのログインは廃止。今後は Master を使用。

### 検証
- ログイン: Master → Tier A、共有パスワード → Tier B、旧 A → 不可
- ログイン済で「パスワード変更」が Master 再入力なしで使える
- サブプロの「インタラクティブ」で即時表示
- 共有 URL (`/view/`) で全機能表示・操作が元プロジェクトに保存
- 共有 URL で interactive 以外のタブ/サイドバー/戻るボタンが非表示

---

## 2026-05-24_ver3.17

### 改善
- **サブプロジェクト一覧の「プロジェクト関連情報」を編集可能化**:
  - ver3.16 は表示専用 (read-only) だったが、ユーザー要望でその場で編集
    + 保存できるよう変更
  - `action_page.py`: 3 つの URL `InputGroup` (Google Keep / MSI Share /
    Other) + memo `Textarea` + 💾 保存ボタンを配置
  - `project_callbacks.py`:
    - 旧 `render_project_info` callback を廃止
    - 新 `load_project_info`: ページ遷移時に既存値を input にロード
    - 新 `save_project_info`: 保存ボタンで `update_project()` を呼び、
      保存ステータスを表示 (例: `✓ 保存しました (14:23:45)`)
  - 保存後は `project_list_refresh` を発火してプロジェクト一覧側も同期
- **フォントサイズを拡大** (text-muted small → 0.95-1rem):
  - 全要素 (Label / Input / Textarea) で `fontSize: 0.95rem` を明示指定
  - 旧 `className="text-muted small"` (0.875rem) より読みやすく
  - URL 入力欄の左ラベル枠の min-width を 150px に統一

### 検証
- サブプロ一覧で URL/memo を編集 → 「💾 保存」 → ステータスに ✓ + 時刻
- プロジェクト切替時に新プロジェクトの値が input に再ロードされる
- プロジェクト一覧側にも反映 (project_list_refresh 連動)

---

## 2026-05-24_ver3.16

### 新機能・改善
- **① 新規プロジェクト作成: タイトル + 実験日を必須化**
  - 「実験日」ラベルに `*` を追加
  - `handle_create_project` で `name` または `experiment_date` 空欄時は
    モーダルを閉じずエラーメッセージを表示 (`new_project_error` Div)
  - 編集モーダル (`handle_edit_project`) も同様に必須化

- **② 新規/編集モーダルに URL 入力欄 3 つを追加**
  - 📝 Google Keep / 🔗 MSI Share / 🌐 Other の 3 種類 (`type="url"`)
  - `project_manager.create_project()` に
    `google_keep_url / msi_share_url / other_url` 引数を追加
  - projects.json に保存され、編集モーダルで既存値が復元される

- **③ プロジェクト一覧のデフォルトソートを「実験日 (新しい順)」に変更**
  - ソート選択肢に `実験日 (新しい順)/(古い順)` を追加
  - `_sort_items` に `experiment_date_desc / experiment_date_asc` 分岐
  - デフォルト `value` を `experiment_date_desc` に変更
  - 編集時の自動先頭移動を防ぐ効果 (last_modified ベースではないため)

- **④ プロジェクトカードから memo 表示を削除**
  - カードには タイトル / 実験日 | サブプロ数 / 最終更新 / 「開く」のみ
  - memo データは projects.json に残し、編集モーダルで引き続き編集可

- **⑤ サブプロ一覧ページに「プロジェクト関連情報」セクションを追加**
  - 共有リンク管理の直下に配置
  - 3 つの URL を `📝 Google Keep / 🔗 MSI Share / 🌐 Other` の
    クリッカブルリンク (別タブで開く) として表示
  - 未設定の項目は「(未設定)」と表示
  - メモは Pre タグで pre-wrap 整形表示

- **⑥ サブプロ一覧 (action_page) のヘッダー「MSI Analysis Application」
  クリックでプロジェクト一覧に戻れない不具合を修正**
  - `action_page.py:16-26` の素の H1 を `dbc.Button(color="link")` で
    ラップ (id="header_title_home_btn_action" — DOM 重複回避のため別 ID)
  - `header_title_to_landing` callback の Input に追加

### 影響範囲
- `projects.json` 既存エントリには `google_keep_url` 等のフィールドが無いが、
  `dict.get(...)` で安全に空文字 fallback (下位互換 OK)
- `last_modified` ベースのソート挙動を期待していたユーザーは選択肢から
  `更新日 (新しい順)` を明示選択可能

---

## 2026-05-24_ver3.15

### 性能改善
- **サムネ登録のラグを大幅短縮** (3 つのボトルネックを解消):

  **A. kaleido (Plotly→PNG) 解像度を削減 — 5-10× 高速化**
  - サムネ用 PNG 生成解像度を 1600x1400〜2400x1800 → **600x600 px (scale=1)**
    に縮小
  - 最終的に `thumbnail_service` で 300x300 にリサイズされるため、過剰な
    高解像度は無駄だった
  - `interactive_batch_save.py` に新規定数 `_THUMB_RENDER_W=600 /
    _THUMB_RENDER_H=600 / _THUMB_RENDER_SCALE=1` を追加
  - `cb_set_thumbnail_spatial` / `cb_set_thumbnail_umap` がサムネ専用に
    この縮小解像度を使用
  - **バッチ一括保存 (`cb_batch_save_*`) は高解像度維持** で別扱い
  - 期待効果: クリック → トースト 1-5 秒 → **0.2-0.5 秒**

  **B. ブラウザキャッシュバスター — 即時反映**
  - 旧: `/api/project_thumb/<id>` URL が固定 → 再登録しても
    `Cache-Control: max-age=3600` で **最大 1 時間古いサムネが表示** され
    続けていた (Ctrl+Shift+R で強制リロードが必要)
  - 修正: `project_callbacks.py:render_project_cards` の `<img src>` に
    `?t=<last_modified>` クエリパラメータを付与
  - `update_project()` で `last_modified` が自動更新されるため、サムネ
    更新ごとに URL が変化 → ブラウザは新画像を即 fetch
  - 期待効果: 最大 1 時間 (要強制リロード) → **即時反映**

  **C. サーバー側 cache pre-warm**
  - `_save_figure_as_thumbnail` 内で `update_project()` 成功直後に
    `get_thumbnail_path()` を呼んで Pillow リサイズを完了させる
  - 次の Flask route 呼出は cache hit で即時配信
  - 期待効果: 100-500ms → **<100ms**

### 検証
- 「📌 サムネ登録」ボタン → トーストが 1 秒以内に表示
- プロジェクト一覧へ戻る → 新サムネが即時表示 (強制リロード不要)
- ブラウザ Network タブで `?t=...` 付き URL を確認

---

## 2026-05-24_ver3.14

### 改善・バグ修正
- **サムネ画像サイズを 100x100 → 150x150 に拡大**:
  - `project_callbacks.py:render_project_cards` の左サムネ width/height を
    150px に変更
  - キャッシュ解像度も 200x200 → 300x300 に引上げ (DPR=2 で sharp)
- **複数切片の連結画像 (横長 PNG) を自動で 1 枚目だけにクロップ**:
  - R が出力する `UMAP_per_sample_*_ALLclusters.png` 等は複数サンプルを
    横一列に連結した wide image。アスペクト比 > 1.4 を「wide」と判定し、
    `thumbnail_service.get_thumbnail_path` 内で **最左端の正方形領域だけ**
    をクロップしてからリサイズするように変更
  - 結果: 自動検出されたサムネも 1 枚目のサンプルのみで square 表示される
  - 「📌 サムネ登録」ボタンで登録した PNG も同じ処理が適用される
  - ログに `cropped=True src_size=WxH` が記録される

### 検証
- 複数切片プロジェクト (例: TDLN_LN_07) → サムネが 1 枚目のサンプルのみ
- 単一切片プロジェクト → 従来通り (aspect 比 ≤ 1.4 はクロップなし)
- サムネサイズが 150x150 で sharp に表示

---

## 2026-05-24_ver3.13

### バグ修正
- **サムネ画像を 100x100 固定サイズに変更** (ver3.12 で全高 stretch にして
  いたが、特定の wide サムネで縦長に過度に伸びる問題があった):
  - `project_callbacks.py:render_project_cards` の左サムネ画像から
    `alignSelf=stretch` を廃止し、`width=100px / height=100px` 固定に
  - `CardBody` の padding を 0 → 12px に戻し、左サムネと右内容に gap=12px
  - 右カラム側の padding は 0 にして重複を防止
  - 角丸 (`borderRadius=6px`) と薄い border を追加 (見やすさ)
  - カードの高さは右カラムの内容で決まる (左サムネ高に引きずられない)

### 検証
- 縦長サムネ画像でもカード全体が引き伸ばされない
- サムネは常に 100x100 で表示
- 「開く」ボタンは右カラム幅のみ (ver3.12 のレイアウト方針は維持)

---

## 2026-05-23_ver3.12

### UI 変更
- **プロジェクトカードを「左サムネ + 右内容」の 2 列レイアウトに変更**:
  - ユーザー要望のスクリーンショットに合わせ、
    `project_callbacks.py:render_project_cards` のカード構造を変更
  - `dbc.CardBody` を `padding=0` の flex container 化
  - **左カラム**: サムネ画像 (`width=130px, minWidth=130px, objectFit=cover,
    height=stretch`) — カード全高に渡って表示
  - **右カラム**: `flexGrow + padding=12px`、縦並びで以下を配置:
    1. タイトル + ✎ x ボタン (タイトル右上)
    2. 実験日 | サブプロジェクト数
    3. メモ (任意)
    4. `<hr>`
    5. 最終更新
    6. 「開く」ボタン (`w-100` = 右カラム幅)
  - `dbc.Card` に `overflow: hidden` を追加し、角丸内にサムネ画像が収まるように
  - Bootstrap `col=4` (3 列レイアウト) は維持
  - サムネ無しは透明 PNG (ver3.9 のフォールバック) + `background=#f0f0f0`
    でグレー領域として表示、レイアウト崩れなし

### 検証
- プロジェクト一覧で各カードが 2 列レイアウトに
- サムネが左 130px 全高に表示される (cover)
- 「開く」ボタンは右カラム幅のみ
- カード幅 (3 列レイアウト) は変わらない

---

## 2026-05-23_ver3.11

### バグ修正・改善
- **① サムネ登録時、複数切片は最初の 1 枚のみを使うように変更**:
  - ver3.10 では per-sample で複数 figure を **横一列結合** していたが、
    50x50 square 表示で wide image が見切れる問題があった
  - `_save_figure_as_thumbnail` で `_concat_pngs_horizontal` 呼出を廃止し、
    `figures_list[0]` (最初の 1 枚) のみを使うよう変更
  - 複数切片時はトーストに `(複数切片は 1 枚目のみ使用)` を付記
- **② サムネ表示サイズを 50x50 → 100x100 に拡大** (カードレイアウト維持):
  - `project_callbacks.py:render_project_cards` で `width/height: 100px`、
    `borderRadius: 6px`、薄い border を追加
  - Bootstrap col=4 のカード幅は維持、タイトル側を `flexGrow + wordBreak`
    で対応 (溢れたら自動折返し)
- **キャッシュ解像度も 60x60 → 200x200 に引上げ** (sharp 表示):
  - `thumbnail_service.py:THUMB_SIZE = (200, 200)` に変更
  - 100x100 表示 + retina (DPR=2) でも sharp に見える
  - cache 名に解像度 tag を含めて自動再生成 (旧 60x60 cache は次回アクセスで上書き)

### 検証
- 複数切片の per-sample Spatial で「サムネ登録」 → 1 枚目だけが
  square なサムネとして表示される (見切れなし)
- プロジェクト一覧で 100x100 のサムネが sharp に見える
- カード全体の幅は変わらない (3 列レイアウト維持)

---

## 2026-05-23_ver3.10

### 新機能
- **インタラクティブ解析のプロットを 1 クリックでプロジェクトサムネに登録**
  できる「📌 サムネ登録」ボタンを追加。
  - UMAP セクション右上 / Spatial Mapping セクション右上の「📷 一括保存」
    ボタン横に配置
  - ボタンクリックで現在表示中の Plotly figure を PNG 化し、
    `Data/Other/cache/project_thumbnails_src/<project_id>_<kind>.png` に
    保存、続けて `projects.json` の `thumbnail_source` を自動更新
  - 複数 figure (per-sample 等) がある場合は **横一列に結合** して 1 枚化
  - UMAP は表示モードに応じて切替:
    - 「統合」モード → `interactive_umap_plot.figure` (2400x1800)
    - 「サンプル別」モード → `batch_umap_figures_store.data` 横結合
  - 登録完了は Toast で通知し、プロジェクト一覧を自動 refresh
  - 既存の `_concat_pngs_horizontal` / `fig_to_png_bytes` を再利用

### 実装内容
- `interactive_tab.py`: UMAP / Spatial の両アコーディオン右上にボタン追加
  (`btn_set_thumbnail_umap` / `btn_set_thumbnail_spatial`)
- `interactive_batch_save.py`:
  - `_save_figure_as_thumbnail` ヘルパ (PNG 化 + 保存 + project 更新)
  - `cb_set_thumbnail_spatial` / `cb_set_thumbnail_umap` callback
- 完成までユーザーが何度でも調整 (色変更、ラベル位置ドラッグ、回転反転、
  クラスタ除外等) し、最後の状態をサムネとして 1 クリック登録できる

### 検証
- インタラクティブで Spatial を整える → 「📌 サムネ登録」 →
  プロジェクト一覧でサムネが更新される
- UMAP も同様に登録可能 (per-sample / 統合 どちらも対応)
- 同じプロジェクトで何度でも登録し直せる (上書き)
- 元の自動検出に戻すには編集モーダルで「サムネ画像」を空欄にして保存

---

## 2026-05-23_ver3.9

### 新機能
- **ヘッダータイトル「MSI Analysis Application」クリックでプロジェクト一覧へ**:
  - 解析画面のヘッダー H1 を `dbc.Button(color="link")` でラップし、
    新規 callback `header_title_to_landing` で `current_page="landing"` へ
    遷移するように
  - 見た目はそのまま、ホバー時にカーソルが pointer に変化
  - 主要ファイル: `main_layout.py`, `project_callbacks.py`

- **プロジェクトカードにサムネ画像 (50x50px) を表示**:
  - カードのタイトル左に小さな UMAP / Spatial 画像を表示
  - ユーザーが任意指定可能: プロジェクト編集モーダルに「サムネ画像」
    入力欄 + ファイルブラウザ `[...]` ボタンを追加 (`thumbnail_source`
    フィールドとして projects.json に保存)
  - 省略時は自動検出: 最新サブプロの `Harmony/RPCA/PCA` フォルダから
    `UMAP_per_sample_*_ALLclusters.png` を順次探索、それも無ければ
    rglob で `*UMAP*.png` / `*spatial*.png` を試行

### 性能設計 (プロジェクト数 100+ でも遅くならない)
- **Pillow リサイズ + ディスクキャッシュ**: 新規 service
  `App/app/services/thumbnail_service.py` で 60x60 JPG を
  `Data/Other/cache/thumbnails/<project_id>_<mtime>.jpg` に保存。
  source 画像の mtime が変わると新規 cache 生成 + 旧 cache 自動削除
- **Flask route 配信**: `/api/project_thumb/<project_id>` で配信。
  `Cache-Control: max-age=3600` でブラウザキャッシュも活用。
  base64 インラインを廃止し、ネットワーク負荷を抑制
- **フォールバック PNG**: サムネが無い場合は透明 1x1 PNG を返す
  ことで img タグの broken-icon を抑止

### 検証
- 解析画面で「MSI Analysis Application」クリック → プロジェクト一覧
- プロジェクト編集モーダルでサムネパス指定 → 一覧に即反映
- 同じプロジェクトの 2 回目表示で network request 最小化 (cache hit)
- 解析結果無しの新規プロジェクトでもカード崩れなし

### 下位互換
- 既存プロジェクト (`thumbnail_source` フィールド無し) は自動検出
- Pillow 不在環境では cache 生成失敗 → 透明 PNG にフォールバック

---

## 2026-05-23_ver3.8

### バグ修正 (R スクリプトデバッグの成果)

ver3.7 で Python 側のデバッグを完了後、ユーザー要望で R スクリプト群と
Python ↔ R 連携を全面調査。誤検知を除外して確実な実害バグを修正。

**[High] 実害大の修正**:
- **H1: DESI Harmony マージの O(n²) メモリ問題を解消**
  - `260422_DESI-UMAP_Template_v14.R:2332`
  - `Reduce(function(x,y) merge(x,y,...), seu_list)` (左結合で逐次マージ、
    中間結果が毎回拡大して O(n²)) を `Seurat::merge(seu_list[[1]],
    y=seu_list[-1], add.cell.ids=...)` の 1 回呼出しに置換
  - ROI モードで 10+ サンプル時、解析時間・メモリが大幅削減
- **H2: extract_features.R が qs 形式 RDS を読めない不具合を修正**
  - `helpers/extract_features.R:22-25`
  - 旧来の `readRDS()` を `rds_io.R` の `load_rds_compact()` に置換し、
    qs 圧縮 RDS でも Feature Plot が動くように。
  - スクリプトの絶対パス検出で helpers/rds_io.R を source する
- **H3: DESI RPCA の `FindAllMarkers` エラーハンドリング欠落を修正**
  - `260422_DESI-UMAP_Template_v14.R:2664-` (旧 line 2651, 2701)
  - Harmony 側にあった `tryCatch(...) + NULL チェック + else 内処理`
    パターンを RPCA にも適用。DEG 失敗時の致命的 abort を防ぐ
  - 既存の `write.csv` / `run_volcano_and_msi` 呼出しも有効 DEG 時のみ
    実行されるよう else 内に統合
- **H4: TIMS `spatial_smooth_seurat` の dist_mat 閾値を統一**
  - `260422_DBSCAN_With_cluster_ver3_no-png_slim.R:910`
  - `50000 → 15000` に下げ DESI と整合。25K spots 程度の TIMS サンプルで
    Docker メモリ上限を超えて OOM 強制終了する問題を防御

**[Medium] 診断容易化**:
- **M1: DESI ROI フィルタ前後の値を message() で出力**
  - `260422_DESI-UMAP_Template_v14.R:1988-2008`
  - ログから `>> ROI: 検出=[...] フィルタ=[...] 適用後=[...] (sample=...)`
    が読めるようになり、ROI 不一致の原因究明が容易に
- **M2: rds_io.R の DietSeurat 警告を抑制**
  - `helpers/rds_io.R:67-95`
  - Seurat 5.0 の `counts/data/scale.data argument deprecated` 警告
    3 件 (毎回出る) を `suppressWarnings()` で抑制。ログのノイズ低減

**[Low] 補助的**:
- **L1: install_r_packages.R の presto 失敗メッセージを強化**
  - 低速 wilcox フォールバックの存在と再試行手順を明示

### 検証
- DESI ROI モードでサンプル数 10+ → 解析時間・メモリが大幅削減
- Feature Plot が qs 圧縮 RDS でも動く
- RPCA で DEG 失敗データを与えても致命的に落ちず `>> DEG(RPCA) skipped`
  でスキップ
- TIMS 25K+ spots で OOM 強制終了せず完走
- 解析ログに `>> ROI: 検出=[0,LN,TDLN] フィルタ=[LN,TDLN] 適用後=[LN,TDLN]`
- DietSeurat の deprecated 警告 3 件が消える

---

## 2026-05-23_ver3.7

### バグ修正 (全 Python スクリプトデバッグの成果)

ver3.0〜3.6 で導入した変更を全体スキャンして判明した複数の問題を一括修正:

**[High] 軽量ビューア / 共有ビューの実害バグ**:
- **H1: `umap_color_by` が軽量ビューアに反映されない**
  - ver3.5 で `save_umap_display_settings` の保存対象に追加したが、
    `lite_view_callbacks._build_per_sample_umap_grid` で
    `color_by="Cluster"` をハードコードしたまま使っていた
  - 修正: `umap_display.get("color_by") or "Cluster"` を読出して
    `_build_umap_integrated_fig` に渡す
- **H2: 画像読込失敗の silent failure**
  - `share_callbacks.py` の Gallery / Modal 画像読込、`lite_view_callbacks.py`
    の bundle 再構築失敗が `except Exception: pass/return None` で
    ユーザーに何も伝わらなかった
  - 修正: 全箇所で `logger.error("... path=%s: %s", path, e)` に変更
- **H3: `_shared_data[token]` の KeyError 競合**
  - `if token in _shared_data:` チェック後に他スレッドで削除される
    race condition で KeyError が出る可能性
  - 修正: `data = _shared_data.get(token) if token else None` で
    1 段階アクセスに統一 (sv_update_umap / sv_update_spatial /
    sv_update_feature_plot)

**[Medium] silent failure を可視化**:
- **M2: file_browser_modal の `PermissionError` 無視**
  - `except PermissionError: pass/continue` で権限エラーが完全に
    握り潰されていた
  - 修正: `logger.info("PermissionError on %s: %s", path, e)` を追加
- **M3: seurat_bridge subprocess の TimeoutExpired 処理**
  - `subprocess.run(timeout=...)` は内部で kill するので zombie 化は
    避けられるが、TimeoutExpired が伝播するとユーザー向けに
    解釈不能なメッセージになる
  - 修正: `try/except subprocess.TimeoutExpired` で捕まえて
    `RuntimeError("Seurat extraction timed out (10min): rds=...")` に整形
- **M4: `interactive_settings.json` mtime 取得失敗の暗黙握り潰し**
  - `except Exception: pass` を `logger.debug` に格上げ

### 対応見送り (将来課題)
ユーザー指示により R スクリプトは対象外:
- `Reduce(merge(...))` の O(n²) 性能問題 (多サンプル時に大きく影響)
- `FindAllMarkers` 失敗時の NULL アクセス防御
- TIMS スクリプトの `dist_mat` 閾値統一
- ROI フィルタ後の log 出力

### 検証
- 軽量ビューア初期表示で `umap_color_by` の値が反映される
- 画像読込失敗時にサーバーログに `image load failed` が出る
- 共有ビューに複数ユーザーが同時アクセスしても KeyError で死なない
- ファイルブラウザで権限なしフォルダにアクセス → `PermissionError on ...`
  がログに出る (UI は破綻しない)

---

## 2026-05-23_ver3.6

### 新機能
- **軽量ビューアにプロットサイズ・ラベルサイズの調整 UI を追加**:
  - Per-sample UMAP セクションの「番号」Switch 横に
    `ラベル` / `パネル高 (px)` の数値入力を追加
    - ラベル範囲: 8〜30 pt
    - パネル高範囲: 200〜700 px (20 px 刻み)
  - Per-sample Spatial Mapping セクションにも同等の入力を追加
  - 値の変更で即座に再描画 (RDS は bundle キャッシュから再読込なし)
  - 初期値は `interactive_settings.json` の `umap_display.label_size` /
    `spatial_display.label_size` から復元 (インタラクティブ側で保存された値)

### 実装内容
- `lite_view_callbacks._build_overview_section`: ヘルパ `_size_toolbar` で
  数値入力 2 個 (label + panel) を生成し、UMAP / Spatial 各セクション上部に
  配置
- `_build_per_sample_spatial` / `_build_per_sample_umap_grid`:
  `label_size_override` 引数を追加し、軽量ビューア側 UI の値を
  `spatial_display` / `umap_display` の保存値より優先
- `update_spatial_labels` / `update_umap_labels` callback:
  Input に `lv_*_label_size` / `lv_*_panel_size` を追加し、変更検知で
  再描画 + 引数として渡す

---

## 2026-05-23_ver3.5

### バグ修正
- **ver3.4 でも UMAP / Spatial クラスター番号位置がページ再オープンで
  初期化されていた問題の真因を修正**:
  - `accumulate_annotation_positions_normal` (interactive_fullscreen.py:702)
    およびフルスクリーン版 2 つの relayoutData 蓄積 callback が
    `_set_active_key(rds_path)` を呼んでおらず、`_interactive_data` を
    使う `_auto_save_label_positions` が **ContextVar 未設定で空 dict を
    返し、JSON save が無音で skip されていた** (multi-thread dispatch 時の
    race condition)
  - 3 callback すべてに `State("seurat_rds_path_store", "data")` を追加し、
    callback 先頭で `_set_active_key(rds_path)` を呼ぶよう修正
  - `_auto_save_label_positions(accumulated, rds_path=, method=)` に
    引数を追加し、ContextVar に依存せず直接 rds_path/method を渡す
  - rds_path が None の場合は `logger.warning` で明示

- **インタラクティブで設定する UMAP / Spatial 表示オプションが
  軽量ビューアに保存・反映されていない問題を修正**:
  - `save_umap_display_settings` を拡張し、`umap_exclude_cluster` /
    `umap_show_legend` / `umap_color_by` も `umap_display` 配下に保存
  - `save_spatial_display_settings` を拡張し、`spatial_exclude_cluster` も
    `spatial_display` 配下に保存
  - `lite_view_callbacks._build_per_sample_spatial` / `_build_per_sample_umap_grid`
    で `exclude_cluster` / `show_legend` を読み出して図表に反映

### 永続化されている設定一覧 (ver3.5 時点)

`interactive_settings.json` 配下:
- `umap_display`: marker_size, label_size, show_labels, columns_per_row,
  exclude_cluster, show_legend, color_by
- `spatial_display`: marker_size, label_size, show_labels, columns_per_row,
  exclude_cluster
- `cluster_name_map` (クラスタ名変更)
- `sample_name_map` (サンプル名変更)
- `spatial_rotation` (回転・反転)
- `custom_color_map` (クラスタ色変更)
- `int_calibration` (キャリブレーション)
- `feature_bookmarks` (Feature Plot のブックマーク)

`label_positions_<method>.json` (RDS と同ディレクトリ):
- `umap_integrated`, `umap_per_sample`, `spatial` の各セクションに
  クラスタ番号位置 (x, y)

### 検証
- ラベルをドラッグ → サーバーログに `[label_persistence] saving:` が出る
- ブラウザ閉じて再オープン → JSON から位置が復元される
- インタラクティブ「軽量ビューアを開く」 → exclude_cluster / show_legend /
  label_size などすべて反映

---

## 2026-05-23_ver3.4

### バグ修正
- **簡易ビューアで Spatial クラスター番号のサイズが反映されない問題を修正**:
  - UMAP は `save_umap_display_settings` で永続化されていたが、Spatial には
    対応する callback が無く、簡易ビューアは `label_size=10` をハードコード
    していた
  - `interactive_spatial.py` に新規 `save_spatial_display_settings` callback
    を追加し、`spatial_label_size` / `spatial_marker_size` /
    `spatial_show_labels` / `spatial_columns_per_row` の変更を
    `interactive_settings.json` の `spatial_display` キーに保存
  - `interactive_tab.py` に新規 `dcc.Store(id="spatial_display_save_trigger")`
    を追加
  - `lite_view_callbacks.py` の `_build_per_sample_spatial` に
    `spatial_display` 引数を追加し、`label_size` / `marker_size` を
    インタラクティブ側の設定値から読込むように変更
  - `_build_overview_section` / `_build_report_body` / `initialize_lite_view`
    の各層に `spatial_display` をプロパゲート

- **UMAP / Spatial クラスター番号位置が画面再オープンで初期化される問題を修正**:
  - `_get_merged_label_positions` を `rds_path` / `method` 引数で呼び出せる
    版に拡張
  - `update_umap_plot` / `update_umap_per_sample` / `update_spatial_plots`
    から `_interactive_data.get("method")` と Input の `rds_path` を渡し、
    `_interactive_data` 未初期化時の race condition でも JSON を確実に
    読み込めるようにした
  - `label_persistence.py` の `load_label_positions` / `save_label_positions`
    に診断ログ追加 (`[label_persistence] saved/loaded: path=... sections=...`)

### 対応不要 (周知のみ)
- ブラウザコンソールに出る `chrome-extension://oingodpdjohhkelnginmkagmkbplgema/
  content.js: Cannot read properties of null (reading 'startContainer')` は
  **「Weblio 英和辞典」Chrome 拡張** のバグで、本アプリ起因ではない。
  気になる場合は当該サイトで拡張を無効化する

### 動作確認 (要実機テスト)
- `/app/results` を未認証で直接アクセス → ログイン → 結果閲覧タブで開く
  (auth_middleware._safe_next が next URL を保持。コード上は問題なし)

---

## 2026-05-23_ver3.3

### バグ修正
- **ver3.2 適用後もブラウザコンソールに "Duplicate callback outputs"
  ランタイムエラーが残り、UI ボタンが反応しない問題を修正**。

  ブラウザコンソールが下記エラーを出していた:
  ```
  In the callback for output(s):
    current_page.data@<hash>
  Output 0 (current_page.data@<hash>) is already in use.
  ```

  原因: Dash 2.x では `(Input, Output)` ペアが同じ callback が複数
  存在すると、両方に `allow_duplicate=True` を付けても **実行時の dispatch
  で hash 衝突を検出してエラー** を投げる。具体的には:
  - `share_callbacks.route_share_url`:
    Input=`url_bar.pathname` → Output=`current_page.data`
  - `tab_url_routing._route_app_url_to_analysis` (ver3.0 で追加):
    Input=`url_bar.pathname` → Output=`current_page.data`

  この (Input, Output) ペアの重複により、コンポーネントが pathname 変化
  時に常時エラーを発生し、結果として `render_project_list` 等の連鎖
  callback も dispatch されない状態だった。

  既存コードベースの `lite_view_callbacks.route_lite_url` /
  `navigate_to_lite_page` が同じ問題を **中間 Store による二段 callback**
  パターンで解決済みだったため、同じ修正を適用:

  - `main_layout.py`: 新規 `dcc.Store(id="app_path_target_store")` を追加
  - `tab_url_routing.py` を二段化:
    - step 1 `_detect_app_path`:
      `url_bar.pathname` → `app_path_target_store.data` (中間)
    - step 2 `_route_app_url_to_analysis`:
      `app_path_target_store.data` → `current_page.data`

  これで `(Input=url_bar.pathname, Output=current_page.data)` の組合せ
  が `route_share_url` 単一となり、Dash の hash 衝突を解消。

### 検証
- ブラウザコンソール (F12) に `Duplicate callback outputs` が出ない
- プロジェクト一覧が表示される
- 「復元」「+新規プロジェクト」「環境設定」等のボタンが反応する
- `/app/results` 直接アクセスで結果閲覧タブが選択されて開く

---

## 2026-05-23_ver3.2

### バグ修正
- **ver3.1 適用後もプロジェクト一覧が空白で UI ボタンが反応しない問題を修正**。
  ver3.0 で追加した軽量ビューア「開く」ボタンの callback chain が 2 node
  の **循環依存** を形成しており、Dash が静的グラフ解析でこれを検出し、
  callback 群の登録に失敗していた:
  - server callback (`_flush_settings_before_lite_open`):
    `btn_open_lite_viewer.n_clicks` → `lite_viewer_open_signal.data`
  - clientside_callback (新タブ open):
    `lite_viewer_open_signal.data` → `btn_open_lite_viewer.n_clicks`

  clientside_callback は実装上 `no_update` を返すが、Dash は実行時の
  返り値ではなく宣言された Output に基づいて静的にグラフを解析するため、
  循環依存と判定される。`suppress_callback_exceptions=True` 下では
  CircularDependency も sass エラーが握りつぶされ、結果として **無関係な
  callback (`render_project_list` 等) も連鎖的に登録失敗** していた。

  症状:
  - プロジェクト一覧が空白
  - 「復元」「環境設定」「+新規プロジェクト」など UI ボタンが反応しない
  - 解析者名ラベルも表示されない

  修正内容:
  - `interactive_tab.py`: 新規 `dcc.Store(id="lite_viewer_open_dummy")`
    を追加
  - `lite_view_callbacks.py:1450`: clientside_callback の Output を
    `btn_open_lite_viewer.n_clicks` → `lite_viewer_open_dummy.data`
    に変更し、循環依存を解消

### 検証
- 起動後ログに `CircularDependency` / `DuplicateCallback` / `Error` が
  出ない
- プロジェクト一覧が表示される (ver2.2 時と同じ 4 つのプロジェクトカード)
- 「復元」ボタンでモーダルが開く
- 「+新規プロジェクト」「環境設定」「インタラクティブ解析」全てクリック可
- 軽量ビューアを開くボタンも従来通り新タブで開く

---

## 2026-05-23_ver3.1

### バグ修正
- **ver3.0 で「復元」など複数の UI ボタンが反応しなくなる回帰を修正**。
  ver3.0 で追加した `App/app/callbacks/tab_url_routing.py` の
  `_sync_tab_from_url` callback が `Output("main_tabs", "active_tab")`
  に `allow_duplicate=True` を付け忘れていた。

  既存の `project_callbacks.py` (4 callback) と `session_callbacks.py`
  (1 callback) は全て `allow_duplicate=True` で `main_tabs.active_tab`
  に書き込んでいるため、Dash 2.x の制約により `DuplicateCallback`
  エラーが発生。`main.py` で `suppress_callback_exceptions=True` を
  設定しているため、エラーがログに出ず当該 Output に紐づく callback
  群が静かに無効化されていた。

  症状:
  - ランディングの「復元」ボタンが反応しない
  - 「開く」ボタンでサブプロ → 解析画面遷移が不安定
  - セッション履歴からのタブ切替が動作せず

  修正内容:
  - `tab_url_routing.py:60` の `Output` に `allow_duplicate=True` を追加
  - `prevent_initial_call` を `False` → `"initial_duplicate"` に変更
    (duplicate output で初回ロード時にも fire させるため必須。
    `False` は duplicate output と非互換)

### 検証
- ランディング画面で「復元」「開く」ボタンが反応する
- サブプロ「開く」→ 解析画面遷移が `/app/settings` に同期
- セッション履歴の「再現」ボタンで settings タブに自動切替
- `/app/results` を直接 URL バーに入力 → 結果閲覧タブで開く
- 起動時のサーバーログに `DuplicateCallback` 警告が出ない

---

## 2026-05-23_ver3.0

### 新機能
- **タブ別 URL ルーティング**を追加。解析画面の各タブに個別 URL を割り当て、
  URL をブックマーク / コピペで意図したタブを直接開けるようにした。
  - `/app/settings` (解析設定) / `/app/results` (結果閲覧) /
    `/app/interactive` (インタラクティブ解析) / `/app/history` (セッション履歴)
  - 認証は従来通り Tier A 必須
  - 実装: `App/app/callbacks/tab_url_routing.py` (新規) で URL ↔
    `main_tabs.active_tab` を双方向同期。`/app/*` への直接アクセス時は
    `current_page` を `analysis` に遷移
- **無期限共有 URL** (`/view/<token>`) を追加。期間付き共有
  (`/share/<token>`, Tier B 必須) と並列で運用し、用途で使い分け可能。
  - 認証不要 (`auth_middleware._BYPASS_PREFIXES` に `/view/` を追加)
  - `token_urlsafe(16)` 由来の不推測 URL (128 bit エントロピー)
  - 1 プロジェクト × サブプロジェクトにつき 1 token (再発行で旧 token 失効)
  - 実装: `App/app/services/persistent_share_manager.py` (新規) で
    `persistent_shares.json` を管理。`share_callbacks.route_share_url`
    を拡張し、`/view/<token>` を内部的に `share_token` Store に
    `"v:<token>"` prefix で格納 → `initialize_shared_view` で
    `get_persistent_share` を呼ぶ分岐ロジックを追加
- 共有作成 UI に「共有方式」ラジオを追加 (期間付き / 無期限)。
  無期限選択時は警告 Alert を表示し、有効期限欄を非表示化

### バグ修正
- **インタラクティブ解析の flip/rotation が軽量ビューア (新タブ) に反映
  されない問題を修正**。
  - 「🔗 軽量ビューアを開く」ボタンの clientside_callback (`window.open`)
    と Store → `interactive_settings.json` への保存 callback が非同期で
    競合し、新タブが旧設定を読む競合状態だった
  - 新規 server callback `_flush_settings_before_lite_open` で
    クリック時に flip/rotation・サンプル名・クラスタ名・カラーマップを
    同期 save → `lite_viewer_open_signal` Store 更新 →
    clientside_callback が signal の変化で `window.open` する順序に変更し、
    JSON 書込み完了後に新タブが開く事を保証
- **軽量ビューアの「Cross-cluster Heatmap (Top 3 markers / cluster)」が
  軸ラベルだけ残ってプロット領域が空白になる問題を修正**。
  - `df["gene"]` / `df["cluster"]` の前後空白や型混在で `pivot_table`
    の index と `top_genes` が一致せず `reindex` で全行 NaN 化していた
  - `astype(str).str.strip()` で正規化 + 空文字行を除外、`reindex` 後の
    `pivot.dropna(how="all")` + empty 判定 + フォールバック表示
  - 副次的に DEG ロード直後と `interactive_settings.json` の mtime を
    `logger.info` でログ出力し、再発時の切り分けを容易に

### ドキュメント
- `App/app/templates/help/analysis.html` を最新化:
  - DESI ROI モード (ver2.0+) の使い方を 3-1 節として追加
  - インタラクティブ解析の flip/rotation・軽量ビューアの説明を追加
  - 新規セクション「🔗 共有機能 (期間付き / 無期限) + タブ別 URL」を追加
  - バージョン履歴セクションを追加
- `App/app/templates/help/registration.html` の共有 URL 発行手順に
  期間付き / 無期限の選び方を追記

### 検証
- インタラクティブで flip 90° + 水平反転を設定 → 軽量ビューアを開いて
  両方が反映される
- 通常 DEG ありデータで軽量ビューア → Cross-cluster Heatmap が描画される
- DEG 空データで軽量ビューア → 「ヒートマップ用データが生成できません」
  の説明テキストが表示される
- `/app/results` を直接 URL バーに入力 → 結果閲覧タブが選択された状態で
  解析画面が開く
- タブを切り替えると URL バーが `/app/<tab_id>` に同期する
- 無期限共有を生成 → 別ブラウザ (未ログイン) で URL アクセス → 読み取り
  専用ビューが表示される
- 期間付き共有 (Tier B 必須) は従来挙動どおり動作

---

## 2026-05-22_ver2.2

### バグ修正
- **DESI ROI モードが通常 UMAP 解析で適用されない不具合を修正**。
  ver2.0 で追加した「ROI 列があれば各 ROI を別サンプルとして解析」
  トグル + ROI 選択チェックボックスが、再解析 (Cluster Filter) では
  動作していたが、**通常 UMAP 解析** では UI 値が R スクリプトに
  注入されず、ROI 列があっても無視されてファイル全体が 1 サンプル
  として処理されていた。

  症状: ユーザーが ROI トグル ON + LN/TDLN を選択しても、ログには
  `Original spots: 49323` (ファイル全体) と単一の spot filtering
  パスのみが現れ、`>> ROI モード ON: ... を 2 個の ROI に分割`
  メッセージが出ない。25K spots を超える単一サンプル扱いとなり、
  後段の `spatial_smooth_seurat` で ~5 GB の距離行列確保により
  Docker メモリ上限で R プロセスが OOM Killer に殺され、明確な
  R エラーログが残らないまま「エラー発生」となっていた。

  実装内容:
  - `analysis_runner.py:generate_v8_config`: `MZ_ALIGN_PPM` 注入の
    直後に `USE_ROI_AS_SAMPLE` / `ROI_FILTER` の `_replace_assign`
    ブロックを追加 (`generate_cluster_filter_config:454-462` と同じ
    ロジック)。これにより通常 UMAP 解析でも UI トグル値・ROI 選択が
    R スクリプトに反映される
  - `260422_DESI-UMAP_Template_v14.R:spatial_smooth_seurat`:
    `use_dist_mat` の閾値を **50000 -> 15000** に引き下げ。
    25K spots の単一サンプルケースでも ~1.4 GB に収まるループ法へ
    自動フォールバックし、OOM Killer による突然死を防御

### 検証
- ROI ありデータ + ROI モード ON + LN/TDLN チェック → ログに
  `>> ROI モード ON: ... を 2 個の ROI に分割 (LN, TDLN)` が出る、
  spot filtering メッセージが 2 回 (各 ROI 用) 表示される、
  Harmony / RPCA で 2 サンプル統合された UMAP が完走する
- ROI モード OFF (デフォルト) で旧データ → 従来通り 1 サンプル解析
- ROI ありデータ + ROI モード ON + LN のみチェック → LN のみ
  Single-sample mode で解析、TDLN は除外
- 生成された `{output_dir}/log/v8_runtime_*.R` で
  `USE_ROI_AS_SAMPLE <- TRUE` / `ROI_FILTER <- c("LN", "TDLN")`
  に置換されていることを確認

---

## 2026-05-22_ver2.1

### UI 改善
- DESI ROI 設定 UI を **テキスト入力からチェックボックス形式** に変更し、
  **「データフォルダ・サンプル選択」の直下** に移動 (TIMS の annotation_
  selector と同じレイアウト)。ユーザー要望: 「自身で入力するのではなく、
  check box 形式を採用し、TIMS と同じように、データフォルダ・サンプル
  選択の下に来るように」。

  実装内容:
  - `data_manager.py:read_desi_roi_list`: DESI .txt の最終列から ROI を
    自動列挙する関数を新規追加 (ヘッダー 3 行目 = pre_masses 行の
    トークン数で n_metabolite を推定し、データ行の列数超過時に
    最終列を ROI として収集、R 側 `read_desi_data` と同じ判定ロジック)
  - `file_handlers.py`:
    - `update_desi_roi_selector` callback を追加。`selected_samples` /
      `data_folder` / `analysis_method` の変化で trigger され、選択
      サンプルの .txt から ROI 一覧を読込んで pattern-matching id
      (`{"type": "desi_roi_check", "index": sample}`) のチェックボックスを生成
    - `sync_desi_roi_to_store` callback を追加。全 desi_roi_check の
      選択値を `desi_roi_filter_store` に集約
  - `settings_tab.py`:
    - 右カラムの「ROI 設定 (DESI、オプション)」セクションを削除
    - 左カラム「データフォルダ・サンプル選択」内の `sample_selector` /
      `FormText` の直後に「ROI 設定」を追加: Switch +
      `desi_roi_selector` Div + `desi_roi_filter_store` Store
  - `analysis_callbacks.py`:
    - State `desi_roi_filter` (text Input) → `desi_roi_filter_store` (Store)
    - 受け取り側を文字列分割から list 直接受け取りに変更
    - `save_last_settings` から `desi_roi_filter` 文字列の永続化を撤去
      (チェック状態はファイル内容に依存するためセッション復元不要)
  - `session_manager.py`: 許可キーから `desi_roi_filter` を撤去

  R スクリプト (v14) は変更なし。`params["roi_filter"]` の型が list で
  R 側に注入される構造はそのまま。

## 2026-05-22_ver2.0

### 機能追加 (Major Bump)
- **DESI 解析: 入力 .txt の最終列に `ROI` 列がある場合、各 ROI を「別サンプル」
  として Multi-sample mode (Harmony/RPCA) で統合解析できる機能を追加**。
  TIMS スクリプトに既に存在する annotation/slice_id 機能 (`260308_DBSCAN_
  With_cluster_ver17.R`) と同等の仕組みを DESI v14 にも実装した。

  実装内容:
  - `App/Script/DESI/260422_DESI-UMAP_Template_v14.R`:
    - USER SETTINGS に `USE_ROI_AS_SAMPLE` / `ROI_FILTER` 変数追加
    - `read_desi_data` 関数で列数判定により ROI 列を検出
      (`ncol(data_df) > 3 + length(metabolite_names)` ならあり)、
      文字列カラムとして別途読込んで `coordinates$ROI` に格納
    - メインフローで `USE_ROI_AS_SAMPLE && has_roi` の場合、各 ROI を
      mask で subset して独立した Seurat object として `seu_list` に追加
    - `sample_names` を ROI 別サンプル化後のリストで上書きし、後続の
      Multi-sample mode (Harmony/RPCA) フローにそのまま乗せる
  - `App/app/layouts/settings_tab.py`:
    - UMAP 解析設定右カラムに「ROI 設定 (DESI、オプション)」セクション追加
    - `dbc.Switch(id="desi_use_roi_as_sample")` トグル
    - `dbc.Input(id="desi_roi_filter")` カンマ区切りでフィルタ可能
  - `App/app/callbacks/analysis_callbacks.py`:
    - `run_analysis` の State に `desi_use_roi_as_sample` /
      `desi_roi_filter` を追加
    - `save_last_settings` に同キーを追加 (永続化)
    - DESI 解析時のみ `params["use_roi_as_sample"]` /
      `params["roi_filter"]` をセット
  - `App/app/services/analysis_runner.py`:
    - `params` から `USE_ROI_AS_SAMPLE` / `ROI_FILTER` を R スクリプトに
      `_replace_assign` で注入
  - `App/app/services/session_manager.py`:
    - 永続化許可キーに `desi_use_roi_as_sample` / `desi_roi_filter` を追加

  互換性:
  - ROI 列無しの既存データ → 従来挙動 (警告も出ない)
  - ROI 列ありデータ + ROI モード OFF → ROI 列無視で 1 サンプル扱い
  - ROI 列ありデータ + ROI モード ON → 各 ROI が別サンプル
  - ROI 列なし + ROI モード ON → 警告ログ "ROI 列なし" + 従来挙動
  - フィルタにマッチする ROI 0 件 → 警告 + 従来挙動

  ※ 機能追加のため major bump (1.12 → 2.0)。

## 2026-05-22_ver1.12

### 修正
- R パッケージ `leidenbase` を `install_r_packages.R` の依存リストに
  追加。UMAP 再解析実行時に下記エラーで R スクリプトが停止していた:

  ```
  Error in RunLeiden(...) :
    Package 'leidenbase' is required for leiden_method = 'leidenbase'.
    Please install it with: install.packages('leidenbase')
  ```

  原因: R スクリプト (`App/Script/DESI/260312_DESI-UMAP_Template_v13.R`
  ほか) で `FindClusters(... algorithm = 4)` (Leiden アルゴリズム) を
  指定しているが、Seurat の最新版で `RunLeiden` のデフォルト
  `leiden_method` が `"leidenbase"` に変更され、同名 R パッケージが
  必須になった。`install_r_packages.R` には `leiden` パッケージは
  含まれていたが `leidenbase` は無く、Docker rebuild で Seurat が
  新版に更新されたタイミングでエラーが顕在化。

  本パッチで `leidenbase` をビルド時にインストールするため、Docker
  rebuild 後は `algorithm = 4` (Leiden) の解析が正常に走る。R
  スクリプト本体は一切変更なし (案 A 採用)。

### 反映手順
`docker compose ... up -d --build msi-app` で Docker image を再ビルド
する必要あり (R パッケージインストール工程が走る、時間がかかる)。

## 2026-05-22_ver1.11

### 機能追加
- 簡易ビューアー: Per-sample UMAP セクションのヘッダに **「番号」
  Switch** を追加 (Spatial にあるものと同じ仕組み)。
  Switch を ON にすると、各サンプル別 UMAP プロットに**クラスタ番号
  annotation** が表示される。Spatial 側の Switch とは独立で、両者を
  個別に ON/OFF できる。
  実装:
  - `_build_per_sample_umap_grid` に `show_labels` 引数を追加
    (既存 `umap_display.get("show_labels", False)` はフォールバック
    として維持)
  - Switch id は `{"type": "lv_show_umap_labels_switch", "scope":
    "main"}` の pattern-matching dict 形式 (DOM 不在ページでの
    callback 登録失敗を防止、ver1.5 と同じ手法)
  - 新 callback `update_umap_labels` が Switch トグルで
    `lv_umap_container` のみを再描画
  - clientside callback (Plotly resize/autorange) の Input にも UMAP
    Switch を追加し、トグル時にも自動 resize が走る

### 修正
- `validate_output_dir` (`data_manager.py:385-400`) のエラーメッセージ
  に**対象 path を含める**ように変更。ユーザーから「UMAP 解析実行時に
  『出力先: 書き込み権限がありません』と出るが、どの path が問題か
  分からない」というフィードバックを受けた対応。
  変更前: `"書き込み権限がありません"`
  変更後: `"書き込み権限がありません: /app/Data/.../UMAP_exclude2"`

  ※ 本パッチはエラーメッセージの改善のみ。真の修復には VPS ホスト側
    での `chmod` / `chown` が必要 (Docker コンテナ内のアプリ実行
    ユーザーが該当ディレクトリに書き込めない問題)。

## 2026-05-22_ver1.10

### 修正
- ver1.9 で導入した「凡例ダブルクリック時の灰色背景 trace」を
  Spatial Mapping に限り、**単色灰色から TIC (白黒) 表示** に変更。
  ユーザー要望: 「Spatial Mapping の背景の灰色は MSI 画像の TIC を
  白黒にしたものにしてほしい」。

  既存のハイライト/選択時の挙動 (`_create_single_spatial_fig` の
  line 128-135 / 156-160) は `TotalCount` を Greys colorscale で
  表示しており、これと一貫性を持たせる。

  実装: `_create_single_spatial_fig` の `embed_legend=True` ブロックの
  背景 trace で、`TotalCount` 列が利用可能なら Greys colorscale で
  TIC を描画。利用不可なら HIGHLIGHT_GRAY 単色フォールバック。

  UMAP の背景灰色は変更なし (UMAP には TIC データが存在しない)。

## 2026-05-22_ver1.9

### 機能追加
- UMAP / Spatial Mapping の凡例ダブルクリック時、選択したクラスタ
  以外を**完全非表示にせず灰色で残す**ように変更。
  Plotly のデフォルト挙動 (ダブルクリック = 他 trace 非表示) を、
  ユーザーの希望「他クラスタを灰色で残す」に合わせた。
  実装方針: 各 figure の通常表示分岐の冒頭で全点を
  `HIGHLIGHT_GRAY + opacity 0.2` でプロットする背景 trace を 1 つ
  追加。`showlegend=False` で Plotly のダブルクリック操作対象外に
  なるため、色付き trace が `visible=False` になっても下の灰色背景
  trace は常に表示される仕組み。

  対応関数 (`App/app/config.py:HIGHLIGHT_GRAY` を再利用):
  - `interactive_umap.py:_build_umap_integrated_fig` の通常表示分岐
  - `interactive_umap.py:_build_umap_per_sample_graphs` の通常表示
    分岐
  - `interactive_spatial.py:_create_single_spatial_fig` の
    `embed_legend=True` ブロック

  共通関数経由でインタラクティブ解析・簡易ビューアー・共有ビューア
  すべてに自動反映される。

## 2026-05-22_ver1.8

### 撤去
- 簡易ビューアー: 「📂 全クラスタの詳細を一括展開 / 折りたたみ」機能を
  撤去。ver1.0〜1.5 で複数回試みたが、新規 mount 時の Plotly レンダ
  リング問題 (lazy rendering / axis range) が安定して解決できなかった
  ため、機能ごと撤去する判断。
  個別「▶ 詳細を表示」(toggle_cluster_card) は引き続き利用可能で、
  そちらは ver1.5 で clientside callback が正しく登録された後は
  安定動作する。

撤去した要素:
- `lite_view_callbacks.py`:
  - `expand_all_clusters` callback (line 374-440 付近)
  - `_build_per_cluster_cards` 内の「📂 全クラスタの詳細を一括展開」
    ボタン (`lv_expand_all_clusters`)
  - ヘッダコメントの「5. 全クラスタ一括展開」記述
  - clientside callback のコメント中の「一括展開」表記

残した要素:
- `toggle_cluster_card` callback (個別展開、必須)
- clientside callback の `Input({"type": "lv_card_collapse",
  "cluster": ALL}, "is_open")` (個別展開時の Plotly resize/autorange
  に必要)
- 各カードの `lv_card_collapse` / `lv_card_body` / `lv_card_toggle`
  関連の pattern-matching id (個別展開で使用)

## 2026-05-22_ver1.7

### 修正
- ver1.6 で `create_project` / `create_sub_project` に `force_id` 重複
  チェックを入れたが、**soft-deleted (`deleted_at` 設定済) なエントリを
  そのまま返してしまう**問題があった。これにより「過去に削除した
  プロジェクトを `_project_meta.json` から復元しようとしても、
  UI に出てこない」症状が発生 (例: 250621_大橋_胎児 プロジェクト)。
  ユーザー検証で `60fdbbdd 250621_大橋_胎児 deleted_at=
  2026-05-22T05:38:13` が確認された。

  対策:
  - `create_project`: force_id 既存チェックで一致したエントリに
    `deleted_at` があれば、`pop` して `last_modified` を更新してから
    返すように変更。
  - `create_sub_project`: 同様にサブの `deleted_at` を解除して返す。
  これにより「復元」機能が本来の意味 (soft-delete されたものを再び
  UI に戻す) を持つようになり、削除 → 復元の冪等性も確保される。

## 2026-05-22_ver1.6

### 修正 (致命的バグ修正)
- プロジェクト復元機能で「✅ 復元」と表示されるのにサブプロジェクトが
  UI に出てこない問題を調査した結果、`projects.json` に **同じ id の
  プロジェクトが複数 (subs=9 / subs=0)** と **同じ sub_id のサブが
  4 回ずつ重複** している致命的データ破損が判明。

  根本原因: `project_manager.py` の `create_project` /
  `create_sub_project` 関数で `force_id` 指定時の重複チェックが無く、
  既存と同じ id を持つエントリでも問答無用で `data["projects"]
  .append()` / `p["sub_projects"].append()` していた。これにより
  ユーザーが復元を何度か試行するたびに同じプロジェクト/サブが新規
  エントリとして増え続け、`list_projects()` のソート順により UI 上は
  「サブ 0 件の側」が見え、実体 (サブ 9 件の側) が見えない状況を
  招いていた。

  対策:
  - `create_project`: `force_id` 指定時に `data["projects"]` を走査し、
    同じ id のプロジェクトが既に存在すれば新規追加せず**既存を返す**
    ように変更 (復元の冪等性確保)。
  - `create_sub_project`: 同様に、対象プロジェクトの `sub_projects` を
    走査し、同じ `force_id` のサブが既にあれば**既存を返す**ように変更。
  - どちらも警告ログを残すので、ログから重複試行を追跡可能。

  これで復元を何度実行してもデータが増えない (冪等) ようになる。

### 注意
- 既に破損している `projects.json` は本修正だけでは自動修復されない。
  ユーザー側で `dedupe_projects` ワンライナー (リリースノート別途) を
  実行して既存の重複エントリを統合する必要がある。

## 2026-05-22_ver1.5

### 修正 (致命的バグ修正)
- ver1.3 で導入した clientside callback と ver1.0 で導入した
  `update_spatial_labels` callback が、`Input("lv_show_labels_switch",
  "value")` のように string id で**動的生成コンポーネント**を参照
  していたため、`lv_show_labels_switch` が DOM 上に未生成のページ
  (ランディング / アクション / 解析 etc) で Dash が "ReferenceError:
  A nonexistent object was used in an Input of a Dash callback" を
  発生させていた。これにより **clientside callback 全体が登録失敗**
  し、ver1.3 / ver1.4 で追加した `Plotly.Plots.resize()` /
  `Plotly.relayout(autorange: true)` が実行されないままだった。
  ユーザー DevTools Console 出力から判明。
  対策:
  - `dbc.Switch(id="lv_show_labels_switch", ...)` の id を
    `{"type": "lv_show_labels_switch", "scope": "main"}` の
    pattern-matching dict 形式に変更。
  - `update_spatial_labels` callback の Input を `Input({"type":
    "lv_show_labels_switch", "scope": ALL}, "value")` に変更し、
    引数 `show_labels_list` をリストで受けて先頭要素を取り出す。
  - clientside callback の同 Input も同様に変更。
  ALL pattern-matching は対応コンポーネントが 0 個でも Dash が
  エラーを出さないため、これで全ページで callback が正常登録される。
- これにより ver1.3 で意図した一括展開時の自動 resize/autorange が
  実際に動くようになり、「全クラスタ詳細を一括展開」で Highlighted
  Spatial が空白のまま残る問題が解消される想定。

## 2026-05-22_ver1.4

### 修正
- 簡易ビューアー: ver1.3 で `Plotly.Plots.resize()` を強制発火したが、
  **一括展開時に Highlighted Spatial だけが空白のまま**残る症状があった。
  UMAP は正常表示なのに Spatial だけ空白という非対称の原因は、
  `_create_single_spatial_fig` (interactive_spatial.py:260-269) が
  `xaxis.range` を明示せず autorange に依存していたため。新規 mount 時
  に autorange 計算がスキップされると、**Spatial はピクセル座標が大きい
  ためデータが画面外** に出て空白に見える (UMAP は座標が小さく問題に
  出にくい)。
  ユーザー仮説「拡大倍率の問題で見えなくなっている可能性」が決定的
  ヒントとなった。
  clientside callback に `Plotly.relayout(el, {xaxis.autorange: true,
  yaxis.autorange: true, ...})` を追加し、resize 後に axis range を
  data に合わせて再計算するようにした (= ツールバー "Autoscale" ボタン
  の動作を自動化)。

## 2026-05-22_ver1.3

### 修正
- 簡易ビューアー: 「全クラスタの詳細を一括展開」で各カード内 Plotly Graph
  が**空白のまま**になる、および Harmony→RPCA 切替時にも上部 Overview が
  同様に空白になる症状を修正。
  ユーザー検証で「Plotly のツールバー (左上の四角=Autoscale/Reset ボタン)
  を押すと表示される」という決定的ヒントを得て、これが Plotly の lazy
  rendering 問題であると特定。新規 mount された `dcc.Graph` は親要素の
  サイズ取得タイミングによっては内部レイアウトが `height=0` のまま固まる
  ため、`Plotly.Plots.resize()` を強制発火する必要がある。
  `lite_view_callbacks.py` の末尾に clientside callback を追加し、以下の
  トリガーで `document.querySelectorAll('.js-plotly-plot')` 各要素に対し
  100ms / 350ms / 800ms / 1500ms の複数タイミングで `Plotly.Plots.resize`
  を呼ぶようにした:
  - 個別 / 一括カード展開 (`lv_card_collapse.is_open`)
  - 番号 Switch トグル (`lv_show_labels_switch.value`)
  - Harmony/RPCA 切替 (`lv_method_store.data`)
  - 初回 URL ロード (`lite_target_store.data`)
  `lite_view.py` layout にダミー Output 用の
  `dcc.Store(id="lv_resize_trigger")` を追加。

## 2026-05-22_ver1.2

### 修正
- 簡易ビューアー: ver1.1 まで残っていた以下 2 つの症状を解消:
  - 「全クラスタの詳細を一括展開」で各カード内 Highlighted UMAP/Spatial
    の画像本体が**空白**になる。
  - 「番号」Switch トグルで Per-sample Spatial Mapping が**消えて**、
    その下の Cluster Statistics / Cluster Ratio が「プルダウンが閉じる
    ように」上に詰まってくる。
  根本原因は、簡易ビューアー側の `dcc.Graph` に `style={"height": ...}`
  が無く、親 div が `height: auto` に依存していたため新規 mount 直後の
  Plotly が `height=0` で描画されていたこと (インタラクティブ側
  `interactive_spatial.py:989` では `style={"height": "350px"}` で固定
  していた)。簡易ビューアー側の 3 つの `dcc.Graph` 呼出しに
  `style={"height": f"{panel_height}px"}` を明示追加して解消。
- 簡易ビューアー: Spatial Mapping の表記方法をインタラクティブ解析と
  一致させる:
  - `_build_per_sample_spatial` の `_create_single_spatial_fig` 引数を
    インタラクティブ側 (interactive_spatial.py:954-966) と揃える
    (`marker_size=0` 自動計算 / `label_size=10` / `embed_legend=True`)。
  - `fig.update_layout(height=..., showlegend=True, margin=...)` の
    上書きを撤廃し、`_create_single_spatial_fig` 内 layout を尊重。
  - Per-sample Spatial Mapping の `panel_height` を overview 350 /
    per-cluster カード内 280 に統一。
- 簡易ビューアー: 「番号」Switch のデフォルトを `True` → `False` に
  変更。インタラクティブ解析の番号チェックボックスのデフォルト OFF と
  一致。

## 2026-05-22_ver1.1

### 修正
- バージョン表示を**全画面共通のグローバル固定位置 (右上)** に移動。
  ver1.0 では簡易ビューアー (`/lite/...`) のレポートヘッダ内にしか
  表示されておらず、プロジェクト一覧画面など他の画面では「自分が
  最新版を見ているか」を確認できなかった。`main_layout.py` の最上位
  に `position: fixed; top: 4px; right: 12px` で `version_label()` を
  1 箇所だけ配置することで、landing / action / analysis / shared /
  lite すべての画面で常に右上に表示されるようにした。
- 重複を避けるため、簡易ビューアーの `_build_header` 内の
  `version_label()` 表示と `position: relative` 化を撤去 (ver1.0 で
  入れたもの)。

## 2026-05-22_ver1.0

### 修正
- 簡易ビューアー: 個別「▶ 詳細を表示」クリックでブラウザがリロードしたように
  見える / 「全クラスタの詳細を一括展開」で上部 Overview の図まで含めて全部
  空白になる、という 2 つの症状を修正。
  根本原因は `lv_report_body` 全体を `dcc.Loading` で覆っていたため、内部の
  `lv_card_body.children` 更新で外側 Loading が triggered され、内部の全
  Plotly Graph が unmount → spinner → remount されること。
  `dcc.Loading` に `target_components={"lv_report_body": "children"}` を追加
  し、初期化 callback の Output だけを監視するよう範囲を絞った。
- 簡易ビューアー: Per-sample Spatial Mapping / Per-sample UMAP /
  Highlighted UMAP の Graph に `responsive=True` を追加
  (`dbc.Collapse` 内 `clientWidth=0` + 再 mount 対策の保険)。

### 追加機能
- 簡易ビューアー: Per-sample Spatial Mapping ヘッダに「番号」ON/OFF Switch
  (`lv_show_labels_switch`) を追加。トグルで `lv_spatial_container` だけが
  再描画され、インタラクティブ解析と完全に同じ見た目 (番号なし) にも切替可能。
- 簡易ビューアー: ヘッダ右上にバージョン表示 (`2026-05-22_ver1.0`) を追加。
  ユーザーが今見ているページが最新の修正反映後かを即座に判別できる。
  version.py / CHANGELOG.md / コミット末尾 `[verX.Y]` の 3 点同期ルールを
  運用に追加。
