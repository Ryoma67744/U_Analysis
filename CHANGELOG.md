# Changelog

このプロジェクトの全ての顕著な変更を記録する。
バージョンは `<日付>_ver<番号>` (`App/app/version.py`) と連動する。

修正をリリースするたびに以下 3 箇所を必ず同期する:
- `App/app/version.py` の `APP_VERSION` と `RELEASE_DATE`
- 本ファイル (`CHANGELOG.md`) に新エントリを追加
- コミットメッセージのタイトル末尾に `[verX.Y]` を付ける

付番ルール: バグ修正のみ → パッチ +0.1 / 機能追加 → メジャー +1.0

---

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
