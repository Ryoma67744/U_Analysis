# Changelog

このプロジェクトの全ての顕著な変更を記録する。
バージョンは `<日付>_ver<番号>` (`App/app/version.py`) と連動する。

修正をリリースするたびに以下 3 箇所を必ず同期する:
- `App/app/version.py` の `APP_VERSION` と `RELEASE_DATE`
- 本ファイル (`CHANGELOG.md`) に新エントリを追加
- コミットメッセージのタイトル末尾に `[verX.Y]` を付ける

付番ルール: バグ修正のみ → パッチ +0.1 / 機能追加 → メジャー +1.0

---

## 2026-08-05_ver50.0

### 追加: 既存 Parquet を再変換せず「全行 1 row group」へ作り直すツール

#### なぜ必要だったか

ver49.0 で変換器の出力は全行 1 row group になったが、**それ以前に変換済みのファイルは
200 行/row group のまま**で、開くたびのコストが残っていた。実データ規模
（203,078 spot × 約 2,700 m/z）では 1,016 row group・フッタ 735MB・開くのに約 3.3 秒。
R 取り込みは 1 回で同じファイルを約 20 回開くため、**取り込みごとに約 65 秒**が
フッタ解析だけに消えていた。

CSV から再変換すれば直るが、元 CSV が残っていない場合があり Phase A に数分かかる。
レイアウトだけ作り直せば十分で、その方が速く安全。

#### 解析結果は変わらない（実測と監査で確認した）

**値はビット単位で不変。** 20,000 行 × 404 列（NaN・±inf・±0.0・subnormal・float32 最大値を
混入）を再パックし、全列を `uint32`/`uint64` ビューで比較して不一致 0 を確認した。
スキーマメタデータ 3 キー（`mz_sorted` / `annotation_files` / `peak_list`）も完全一致。

**再現性の記録は壊れない。** `receipt.json` / `analysis_conditions.json` /
`analysis_params.json` / `_project_meta.json` / `analysis_receipt_r.json` / `METHODS_*.md`
のいずれも parquet のハッシュ・サイズ・mtime を記録していないことを Python・R 両方で監査した。
唯一の該当箇所は R 取り込みキャッシュ（`ver6:1046-1062`）が `size`/`mtime` を照合する点で、
影響は**キャッシュミス → 再取り込み → 同じ結果**。再パック後は取り込み自体が速くなるので損は無い。

**ファイルサイズはむしろ小さくなる。** 同一内容を 200 行/RG と全行 1 RG で書き分けた実測
（20,000 行 × 300 列）:

| データ種別 | 旧(200行/RG) | 新(全行1RG) | 差 | 旧フッタ | 新フッタ |
|---|---|---|---|---|---|
| 疎（8 割ゼロ・MSI に最も近い） | 14.82 MB | **8.01 MB** | −46.0% | 4.12 MB | 0.10 MB |
| 量子化（値が繰り返す） | 16.78 MB | **4.76 MB** | −71.7% | 4.12 MB | 0.10 MB |
| 乱数（圧縮が効かない最悪ケース） | 36.03 MB | **32.72 MB** | −9.2% | 4.13 MB | 0.10 MB |

フッタが消えるうえ、200 行だと 1 ページ約 800 バイトで zstd がほとんど効かないのに対し、
全行 1 つなら大きなページでゼロの連続をまとめて潰せる。サイズが増えるケースは見つからなかった。

#### 変更点

- **`app/services/parquet_repack.py` を新設。** `pf.iter_batches()` 単一経路で読み書きする。
  スキーマは `pf.schema_arrow` をそのまま writer へ渡してメタデータ 3 キーを保持する。
- **`App/tools/repack_parquet_rowgroups.py`（CLI）を新設。**
  `--dry-run` / `--no-backup` / `--skip-verify` / `--allow-split` / `--no-recursive` / `--include=`。
  1 件でも失敗すれば **exit 2** を返す。
- **GUI モーダル「📦 Parquet 再パック」を追加**（サイドバー / ランディングページ）。
- **`analysis_runner.start_analysis_process` に `interpreter` 引数を追加**（2 箇所・4 行）。
  `None`（既定）なら従来どおり `[Rscript, "--vanilla"]` に還元されるので **R 経路は不変**。
  これで同時実行ブロック・空きメモリ/ディスク判定・ログ退避・watchdog をそのまま再利用できる。

#### 列ブロック方式を実測で否決した

当初は「列ブロックごとに `pf.read(columns=[...])` して連結」を予定していたが、
**列名が重複すると壊れる**ことが分かった。`t.column(name)` は
`KeyError: Field "..." exists 2 times`、位置指定に逃げても `pf.read(columns=[0,1,...])` は
列を取りこぼし `ValueError: Schema and number of arrays unequal` になる。
`iter_batches` は列名を一切参照しないので影響を受けず、`RecordBatch` の列は定義上すでに
連続配列なので結合処理も要らない。全行 1 つと分割の両方が同じコードで書ける。

なお `write_batch` を繰り返しても単一 row group にはならない（3 回呼ぶと 3 row group）。
つまり 1 row group にするには全行の materialize が必須で、これは回避できない。

#### メモリモデルは推定ではなく実測で較正した

2,700 列固定で row group 数だけ変えて計測:

| row group | column chunk | フッタ(disk) | 開くコスト | 倍率 | 再パック後ピーク |
|---|---|---|---|---|---|
| 20 | 54k | 7.78 MB | +64 MB | 8.25× | 283 MB |
| 400 | 1,080k | 143.6 MB | +1,097 MB | 7.64× | 1,278 MB |
| 1,000 | 2,700k | 360.7 MB | +2,729 MB | 7.57× | 2,877 MB |

`peak = footer_ram + 全行 × 1 行のバイト数 + writer マージン`。
`footer_ram = max(row group 数 × 列数 × 1KB, フッタ on-disk × 7.6)`。
実データ推定はフッタ 5.6GB + データ 2.2GB で**ピーク約 8.1GB**。

**フッタ項は支配的だが、どんな戦略でも削れない。** ただしフッタが載らないファイルは
そもそも今のアプリでも読めないので、通常の解析が回るファイルなら再パックも回る。
判定のために開いては本末転倒なので、**ファイル末尾 8 バイトからフッタ長だけを読む**
事前ガードを入れた（thrift 解析なし・確保ゼロ）。予算不足時はスキップして必要量を報告する。

変換器の `_plan_row_groups()` は**再利用しなかった**。あれは「row group を小さくすれば
メモリが減る」前提だが、再パックでは全行を materialize する以上 `row_group_size` を
変えても 1 バイトも減らない。流用すると削減ゼロと引き換えに出力レイアウトだけ劣化する。

#### 検証に `pa.Array.equals()` は使えない

実測で両方向に誤る: `NaN == NaN` → **False**（同一データを不一致と誤判定）、
`+0.0 == -0.0` → **True**（符号の違いを見逃す）。よって浮動小数点列は整数ビューで比較し、
null スロットは valid ビットマップで除外する。書き込み後・置換前に必ず全列を照合する。

#### 付随して直したもの

`rds_maintenance_callbacks.py` から写す際に、既存の不具合 3 件は写さず直した。

- 進捗抽出の正規表現に `re.MULTILINE` が無く、`^` がログ先頭にしかマッチしないため
  進捗バーが実行中ずっと 0% のままだった
- 進捗の現在値を最後のマッチから取っていた（最大値を採るべき）
- R 側が全件失敗でも exit 0 を返すため UI が緑の成功表示になっていた

#### 安全策

- 一時ファイルは **`<name>.repacking`**（`.parquet` で終わらせない）。
  `sample.repacking.parquet` だと `data_manager._filter_tims_candidates` が解析サンプルとして
  拾い、`annotation_inspect` がフッタを読んでしまう
- バックアップは `os.link`（追加ディスク 0・瞬時）。`OSError` 時のみ `shutil.copy2`
- 圧縮は元から検出。`compression` は `"UNCOMPRESSED"` を返すが pyarrow は `"none"` しか
  受け付けないため写像表を持つ（素直に `.lower()` すると非圧縮ファイルで例外になる）
- 中断で残った `*.repacking` は起動時に掃除する

#### 検証

- CLI を end-to-end 実行: 3 ファイルを 25/15/10 → 各 1 row group、サイズ −12.7〜−14.5%、
  フッタ約 1/10、`.bak` は旧レイアウトを保持、サイドカーと単一 RG ファイルは不変、残骸なし
- GUI 起動経路（`start_analysis_process` + `interpreter`）も実行し、
  サマリ解析・進捗抽出（`files_matched=2`, `[(1,2),(2,2)]`）・exit 0 を確認
- **テスト 35 件追加。`pytest -m "not e2e"` は 716 件全通過**（既存 681 件は無変更で通る）

#### 既知の課題（今回は対象外）

- 同時実行ガードは片方向。`analysis_runner` は `rscript` という名前を探すので
  「解析中に再パックを始める」は防げるが「再パック中に解析を始める」は防げない。
  実際には解析側の空きメモリ 10GB 判定が効くので事故は起きにくい
- `scils_converter.py:994` の `<stem>.writing.parquet` にも同じ「一時ファイルが
  サンプルとして拾われる」問題が潜在的にある（出力先がデータフォルダのとき）
- 実データ規模でのビット一致は未検証（20,000 行 × 404 列までで確認）。
  最初の 1 本はコピーを取ってから実行すること

---

## 2026-08-05_ver49.0

### 変更: SCiLS 変換の出力 Parquet を「全行 1 row group」にした

#### なぜ必要だったか

`convert_scils_to_parquet` の Phase B は `spot_block`（既定 200）ブロックごとに
`pq_writer.write_table()` を 1 回呼んでいた。ParquetWriter は **1 回の `write_table` = 1 row group**
なので、出力は **200 行 = 1 row group** になっていた。`spot_block` は「1 回に読み込む spot 列数」という
メモリ用のつまみとして書かれた引数で、row group サイズを決めている自覚はコードにも UI にも無かった。
**独立した 2 つの関心事が 1 つの引数に癒着していた。**

実データ規模（203,078 spot × 約 2,700 m/z、ver4.32 参照）だと 1,016 row group になり、
column chunk は約 275 万個。peak-list 由来の長い列名が chunk ごとに `path_in_schema` として
繰り返し格納されるため、**フッタだけで 735 MB** に達していた。

実測（pyarrow 25.0.0）:

| | 200 行 = 1,016 row group | 全行 1 row group |
|---|---|---|
| フッタ | **735.6 MB** | **約 2 MB** |
| `close()` だけの所要 | **49.0 秒** | 数秒 |
| ファイルを開く | **約 3.3 秒** | **約 15 ms** |
| 書き込みピーク | **3.83 GB** | 2.19 GB |

**全行 1 row group のほうがメモリも少ない。** ParquetWriter は row group ごとの column chunk
メタデータを `close()` まで RAM に保持するため（実測 約 6.4 MB/row group）、細かく刻むほど重くなる。
コストは「バッファ＋メタデータ」の U 字で、単調ではない。

読み側の実害も大きかった。`260623_DBSCAN_With_cluster_ver6_no-png_slim.R` は 1 回の取り込みで
同じファイルを約 20 回開き直しており（`ParquetFileReader$create` ＋ メタ列読み ＋ 列ブロックループ
約 18 回）、**約 65 秒がフッタ解析だけに消えていた**。`data_manager.py` も 1 回の読み取りで
フッタを 2 回解析しており、`file_handlers.py` はそれをサンプル数だけループしていた。

#### 変更点

- **`row_group_rows` 引数を新設**（`app/services/scils_converter.py`）。既定 `None` = 全行 1 row group。
  `spot_block` は**名前も既定値も意味も変えず**「読み取り粒度」専用にした（UI・callback・既存テストは無変更）。
- **Phase B を二重ループ化**。外側が row group、内側が `spot_block` 単位の読み取りで、
  形状 `(n_mz, rg_rows)` の C 連続バッファへ書き込む。`buf[j, :n]` が連続ビューになるので
  `pa.array()` がゼロコピーで包み、要素あたり 32 バイト → 4 バイトに落ちる。
  **軸順を反転すると pyarrow が黙ってコピーしてピークが 2 倍になる**ため、
  ポインタ同一性を検査する回帰テストを入れた。
- **`_plan_row_groups()` を新設**。メモリ予算に収まらない場合のみ自動分割する。
  単純に行数を減らすと row group 数が増えてメタデータが膨らみ**かえって悪化する**ので、
  U 字コストを最小化する行数を選ぶ。どう分割しても載らない場合だけ明示エラー。
- **予算判定を cgroup ベースに**。`psutil.virtual_memory().available` はホストの `/proc/meminfo` を
  そのまま返し cgroup を見ないため、大きなホスト上のコンテナで「ほぼ満杯なのにチェックを通る」。
  `analysis_runner._container_memory_limit_gb()` を再利用するようにした。
  Phase A の一時 parquet のフッタが Phase B の間ずっと常駐する分（実データ規模で約 1.15 GB）も
  定数項として計上している。
- **出力を原子的にした**（既存バグの修正）。従来は `out_path` に直接書いており、Phase B が失敗すると
  `close()` が「完了した row group だけで有効なフッタ」を書くため、**「id が連番で `mz_sorted` も
  揃っているのに spot が途中までしかない有効な parquet」が直前の正常ファイルを上書き破壊**していた。
  下流の Python・R いずれもこれを正常なファイルとして受け入れるため誰も気づけない。
  一時パスへ書き、行数を検証してから `os.replace` するようにした。
- `ConversionResult` に `n_row_groups` / `row_group_rows` / `footer_bytes` / `row_group_policy` を追加し、
  変換結果パネルとログにレイアウトを表示するようにした。

#### 旧 .parquet（200 行/row group）はそのまま読める。再変換は不要

Parquet 読み側を Python・R 両方で監査した結果、**row group 単位の API を使っている reader は 1 つも無い**
（`num_row_groups` / `read_row_group` / `iter_batches` / `ReadRowGroup` / `pre_buffer` の検索結果 0 件）。
すべてフッタ・スキーマのみか列単位・全表読みで、`mz_sorted` 等はスキーマレベルのメタデータなので
行グループ分割と無関係。ver45.5 の列ブロック取り込みも「行グループ構成に依存しない」と明記している。

回帰防止として `TestLegacyLayoutCompat` を追加し、複数 row group と全行 1 つで
`read_parquet_annotations` / `_read_mz_sorted_metadata` / 列指定読み / 新旧混在フォルダの候補列挙が
同一結果になることを固定した。

#### 検証

実際の変換器を通した end-to-end（5,000 spot × 800 m/z、NaN・±inf を含む）:

| | row group | フッタ | ファイル | ピーク RSS | 変換 | open |
|---|---|---|---|---|---|---|
| 旧（200 行） | 25 | 2.02 MB | 13.0 MB | 164.9 MB | 6.2 s | 28.2 ms |
| 新（全行 1） | **1** | **0.17 MB** | **9.0 MB** | **148.9 MB** | **3.3 s** | **4.3 ms** |

804 列すべてを 1 本ずつストリーム比較（`np.array_equal(..., equal_nan=True)`）して**完全一致**を確認。
`Table.equals` は IEEE 比較のため NaN を含むと必ず False になるので使っていない。
テストは 15 件追加し、既存テストは無変更で通る。

#### 付随して直したもの

- `260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R` のコメント訂正。「単一行グループだと下流で
  行グループ単位の分割読みができなくなる」と書かれていたが事実に反する（ver6 の取り込みは列ブロック方式）。
  放置すると次の担当者がこの変更を差し戻すため訂正した。`chunk_size` の指定自体は書き込み時の
  メモリ抑制として維持。
- `annotation_inspect.py` の docstring「フッタ（数 KB）のみ読む」— 旧レイアウトでは数百 MB になる。

#### 既知の課題（今回は対象外）

- Phase A の一時 parquet のフッタが約 1.15 GB 常駐し、開くのに約 13 秒かかる。
  一時ファイルを parquet ではなく生の memmap にすれば解消するが、`_csv_to_temp_parquet` の
  書き換えが必要で別課題。一時 parquet の `row_group_size` を上げる手は使えない
  （行数が n_mz しかないため単一 row group ＝全データのバッファリングになり polars のストリーミングが壊れる）。
- 変換に同時実行ガードが無い（R 解析側には `analysis_runner` にある）。1 変換あたり数 GB のため、
  複数ユーザーが同時に実行すると 12 GB コンテナを圧迫する。

---

## 2026-07-29_ver48.0

### 追加: 論文にそのまま貼れる Methods 平文（骨格は黒・復元値は青・未記録は赤）

#### なぜ必要だったか

ver47.0 の Methods 下書きは**条件の表**だった。点検には向くが、論文の Methods は
平文（連続した文章）で書く。実際に本番で開いた画面がこれで、条件は並んでいるが
そのまま原稿には使えない。

さらに本番のスクリーンショットで 2 つの問題が見えた。

1. **大半が「未記録」だった。** 開いていたのは ver47.0 より前（2026-05-22）に回した
   結果フォルダで、`analysis_params.json` の新キーも R サイドカーも無い。
   UMAP パラメータ・正規化モード・バッチ補正・クラスタリングが軒並み空だった。
2. **`キャリブレーション有効 false` なのに `回帰モード poly3` が並んでいた。**
   表なら並記でも害はないが、**平文で「poly3 で補正した」と書けば嘘になる**。

#### 実行スクリプトから復元する

`<結果フォルダ>/log/v8_runtime_<日時>.R` は「UI の値を定数として焼き込んだ、実際に
実行されたスクリプトそのもの」で、上の未記録項目はすべてここに残っている。
whitelist を決めて読み戻すようにした（`app/services/runtime_script.py`）。

- **推測ではない**が、出典が `receipt.json` より一段間接なので**別色（青）**で出し、
  `_sources[path] = "runtime_script"` と印を付けて後から検証できるようにした。
- **既に記録がある値は決して上書きしない。**
- 単純リテラル（数値 / `30L` / `"文字列"` / `TRUE`/`FALSE` / `c(...)`）だけ解釈し、
  式や変数参照は「読めなかった」として捨てる。誤った値を書くくらいなら未記録のままにする。
- **クラスタリング解像度は手法ごとに定数が違う。** DESI テンプレは
  `CLUSTER_RESOLUTION_SINGLE`=0.5 / `_HARMONY`=0.5 / `_RPCA`=0.8、TIMS は単一の
  `CLUSTER_RESOLUTION`。統合手法に対応する定数を選ぶようにした
  （**DESI+RPCA を 0.5 と書くと誤り**。実際は 0.8）。

実測で、スクリーンショットと同条件の結果フォルダの未記録が **13 件 → 2 件**になった。

#### 3 色の下書き

| 色 | 意味 |
|---|---|
| 黒 | `receipt.json` / `analysis_params.json` に直接記録されていた値 |
| 青 | 実行スクリプトから復元した値（**要確認**） |
| 赤 | 未記録。`〔要記入: …〕` / `[TO BE FILLED: …]` として手で埋める |

赤の目印は色だけでなく**文字としても入れて**あるので、書式を落として貼り直しても
埋め残しが消えない。画面から選択してコピーすれば色のまま Word へ貼れる。
「📋 書式つきでコピー」ボタンも用意した（`navigator.clipboard` が使えない場合は
`execCommand` にフォールバック）。

赤を出すのに `dangerously_allow_html` は使っていない。条件にはクラスタ名やサンプル名
などユーザー入力由来の文字列が入るため、HTML 文字列を組むと注入経路になる。
代わりに構造化セグメントを組み、画面は Dash コンポーネント、ダウンロードは
エスケープ済み HTML と、同じセグメント列を 3 通りに描き分けている。

#### 事実でないことを書かない

平文の最大のリスクは、記録が無い／無効なのに断定してしまうこと。分岐を切った箇所:

- `calibration_enable` が偽 → キャリブレーションの段落ごと出さない（`poly3` が残っていても無視）
- `analysis_type` が `*_cluster_filter` → 再解析の文型に切替（新規測定の記述をしない）
- `pipeline_stage` が `reduction_only` → クラスタリングと DEG の段落を出さない（回していない）
- `interactive.onthefly_de` が無い → 投げ縄 DE の段落を出さない
  （固定値の dict は常に存在するが、それは実行した証拠ではない）
- `hne_export_options` が無い → MetaboAnalyst の段落を出さない
- `integration_method` が PCA → 「補正した」ではなく「補正を行わなかった」と書く
- `mz_align_ppm` が 0 → 「アライメントを行わなかった」（0 は記録済みの値であって未記録ではない）
- `input_normalized` が真 → 「入力が正規化済みのため再正規化しなかった」

#### GUI に出ていない検定条件を明記した

R テンプレを読み直したところ、**バッチ側の DEG にも GUI に出ていない固定条件**があった。

```r
deg <- FindAllMarkers(obj, only.pos=FALSE, min.pct=DEG_MIN_PCT_VAL,
                      logfc.threshold=DEG_LOGFC_TH_VAL, test.use="wilcox")
deg$p_val_adj <- p.adjust(deg$p_val, method = "BH")   # Seurat 既定の Bonferroni を置換
```

「Wilcoxon 順位和検定」「min.pct = 0.05」「BH 補正」は Methods に必須だが、
これまでどこにも出ていなかった。`BATCH_DE_FIXED_PARAMS` として記録し本文に書くようにした。

同時に、**クラスタリングの近傍探索距離（`CLUSTER_METRIC` = euclidean）は UMAP の距離尺度
（`UMAP_METRIC` = cosine）とは別物**であることも判明した。取り違えると誤記になるので、
本文では別の文として書き分けている。

#### 収集側の取りこぼしも修正

- `sample_selection` を `analysis_params.json` からしか読んでおらず、`receipt.py` が書いている
  `object.sample_selection` を捨てていた（レシート優先で統合するようにした）。
- `analysis.clustering` に params フォールバックが無く、`receipt.json` が無いと丸ごと空になっていた。
- `annotation.sources` が params 側のフォールバックに無かった。
- 警告を `{"code": ..., "params": ...}` の構造に変え、英語 Methods に日本語が出ないようにした。
  ver47.0 が書いた素の文字列もそのまま読める。
- PCA の「キャッシュのみ」警告を、名前ではなく**結果フォルダの有無**で判定するようにした
  （永続化された PCA の RDS がある場合に誤警告していた）。

---

## 2026-07-29_ver47.0

### 追加: 解析条件を必ず記録する（論文の Methods 用）

#### 何が問題だったか

GUI 上で設定を変えながら解析・作図しているのに、**その設定の大半がどこにも残っていなかった**。
論文の Methods を書くとき、また査読で再現性を問われたときに条件を復元できない。

切り分けると、バッチ解析（設定タブ→実行）は `analysis_params.json` /
`analysis_receipt_r.json` / `receipt.json` / `log/v8_runtime_*.R` でほぼ記録されていた。
一方 **Interactive タブは事実上ノーレコード**で、論文の図はそこから出てくる。
特に次の 3 つは「見た目の設定」ではなく数値そのものを変えるのに、まったく残っていなかった。

| 設定 | 何が変わるか |
|---|---|
| `heatmap_scale` (Z-score / Raw) | プロットしている値そのもの（データ変換） |
| `feature_intensity_min/max` | 色スケールのクリップ（cmin/cmax）。強度画像の解釈が変わる |
| `hne_export_intensity` (linear/counts/data) | MetaboAnalyst に渡る濃度そのもの |

さらに on-the-fly DE は **GUI に出ていない固定値**（Wilcoxon, `min.pct=0.05`,
`logfc.threshold=0.25`, BH）で走っており、Methods に書きようがなかった。
Volcano/Heatmap の閾値が表示専用で、統計判定は `p_thresh`/`logfc_thresh` である点も
取り違えやすい。

#### 記録の土台は「サーバ側記録」、同梱は補助

エクスポートのたびに `<result-dir>/provenance/export_<ts>_<kind>.json` を必ず書く。
ダウンロード形式に依存しないので、生 CSV でもクライアント側 PNG でも記録が残る。
その上で、形式が許すものには manifest を同梱する。

| 出力 | 同梱 |
|---|---|
| PPTX | 「解析条件」スライド + スピーカーノートに JSON 全量 |
| バッチ ZIP (UMAP/Spatial/Feature/DEG) | `analysis_conditions.json` |
| H&E MetaboAnalyst ZIP | `analysis_conditions.json` |
| データ出力 xlsx | `Conditions` シート |
| データ出力 csv/parquet、各種 CSV | サーバ側記録のみ（**CSV の列は変更しない**） |

CSV の列を変えなかったのは、そのまま Supplementary Table として使えるようにするため。
代わりに、表のエクスポートは `derived_virtual_data`（画面の並び替え・絞り込み後）を
書き出しているので、**`sort_by` / `filter_query` を記録に含めた**。これが無いと同じ表を
再現できない。

PPTX の条件は**クリック時点でスナップショット**する。生成に数分かかることがあり、
その間に設定を変えられると出力と記録がずれるため。

#### 日英の Methods 下書きを自動生成（表示は Master Password）

`methods_text.render_methods(conditions, lang)` が同じ conditions から日英を生成するので、
2 言語間で値がずれない。守っている原則:

- **値を捏造しない。** 取れなかった項目は「未記録 / not recorded」と明示し、
  末尾の「⚠ 未記録の項目」に列挙する。もっともらしい既定値で埋めると論文に嘘が載る。
- Volcano/Heatmap の閾値が表示専用であることを本文に明記する。
- GUI に出ていない on-the-fly DE の固定値を明記する。
- `PCA (uncorrected)` の埋め込みは `SEURAT_CACHE_DIR` にしか無く LRU で消えうる旨を警告に出す。

表示には Master Password（ログインと同じもの）が要る。パスワードは検証にだけ使い、
Store にも Output にも残さない。解錠は memory Store なのでリロードで失効する。

#### バッチ側レシートの穴も塞いだ

- `analysis_params.json` に従来欠落していた項目を追加:
  `template_path` とその sha256（v14/v15/v16 のどれで走ったか）、`operator`、
  `input_normalized`/`norm_mode`、`mz_align_ppm`、対象サンプル/ROI/セクション、`tims_scenario`。
  値は UI の State ではなく `params`（R へ実際に注入された dict）から取る。
- **バグ修正**: 再解析で「前回の係数を流用」したとき、`calibration_enable: true` なのに
  `calibration_coefficients: null` になっていた（その経路は `calibration_result` を作らない）。
- **バグ修正**: 再解析なのに full-analysis 側の UI 値（ion_mode / p_thresh 等）を記録していた。
- receipt を v2 に: `pipeline` / `sample_selection` ブロックを追加、常に空だった `outputs` を
  主要成果物で埋め、`inputs` に実行スクリプト `log/v8_runtime_*.R` を sha256 付きで含める
  （大きいファイルは hash を省いてパスとサイズのみ）。
- `analysis_params.json` の書き込みを atomic 化。

#### 対象外

クライアント側 modebar の PNG ダウンロード（Plotly カメラボタン）には manifest を
付けられない。サーバ側記録と「解析条件をまとめて出力」で担保する。
操作履歴の時系列ログは取らない（最終状態のみ記録）。

---

## 2026-07-28_ver46.3

### 変更: 本番 WSGI サーバ化 + heartbeat の扇形抑制 / plotly 6 は見送り

#### plotly 6 化は「計測の結果、逆効果」と判断して見送った

当初の計画では「plotly 6 の base64 型付き配列で転送量が 3〜5 倍減る」としていたが、
**実測したところ ver46.1 の座標丸めを入れた後では逆に増える**ことが分かった。
gzip 圧縮後（＝実際に流れる量）で比較すると:

| 形式 | raw | **gzip 後** | to_json + gzip の CPU |
|---|---|---|---|
| **現行（丸め済み list / plotly 5）** | 0.78MB | **0.35MB** | 116ms |
| plotly 6 numpy float64 → base64 | 1.13MB | 0.46MB (+31%) | 39ms |
| plotly 6 numpy float32 → base64 | 0.57MB | 0.40MB (+14%) | 28ms |

（5 万点 1 トレースあたり）base64 は高エントロピーで gzip が効かず、
既に丸めてある短い十進数のほうが小さい。**報告された症状（転送量）に対しては
plotly 6 は改善ではなく悪化**する。

さらに、plotly 6 は `fig.to_dict()` の配列を `{"dtype": "f8", "bdata": ...}` に
変えるため、figure dict を **list 前提で扱っているコードが無言で誤動作**する。
実際 `pptx_helpers._maybe_downsample_scatter` は `len(x)` と
`isinstance(seq, (list, tuple))` で判定しており、PPTX 出力の間引きが
黙って無効化されることを確認した（例外は出ない）。

サーバ CPU だけは 3〜4 倍速くなるが、それは下記の WSGI 化と将来の共有ストア化で
別途対処できる。リスクに見合わないため **plotly 5 系のまま据え置く**。
kaleido 0.2.1 も現状維持（plotly 6.9 でも動作はするが非推奨警告が出る）。

#### 本番 WSGI サーバを waitress に

`run_app.py` は Flask の `app.run()`（Werkzeug 開発サーバ）で本番運用していた。
waitress は純 Python の本番用 WSGI サーバで、C 拡張もプロセスマネージャも不要なため
**Dockerfile を変えずに**差し替えられる。接続・タイムアウト・バックプレッシャの扱いが堅い。

- `MSI_WSGI_THREADS`（既定 8）でスレッド数を調整可能。
- `MSI_WSGI_SERVER=werkzeug` で従来の起動に戻せる（切り分け用）。
- タイムアウトは Caddy 側（read/write 600s）に合わせて既定 600s。

**ワーカーは 1 プロセスのまま。** このアプリは `plot_data`（数百 MB）・
エクスポート用 figure・H&E 画像キャッシュを**プロセス内メモリ**に持つため
（`interactive_callbacks._project_states` 等）、複数ワーカーにすると
「別ワーカーに当たった瞬間データ未ロード扱い」になる。マルチプロセス化は
それらを diskcache 等の共有ストアへ移すのが先で、本 PR の範囲外。
run_app.py にもその旨を明記した。

#### 編集ロック heartbeat の扇形を抑制

`refresh_edit_lock_state` は 10 秒ごとに**毎回新しい dict** を返していたため、
中身が同じでも Dash は変化とみなし、`edit_lock_state` を Input にする 6 つの
コールバックへ配信していた。うち 4 つは MATCH でサンプル別 / クラスタ別に
展開されるため、8 サンプル × 15 クラスタ規模では **10 秒ごとに数十件の
コールバック実行**が、パン/ズームや描画と同じサーバに積まれていた。

- 内容が前回と同じなら `no_update` を返すようにした。ロックは滅多に変わらないので
  ほぼ常に抑制される。
- heartbeat が兼ねている `cleanup_expired` / `evict_stale_project_states`（リーク防止）
  は抑制時も必ず実行することをテストで固定した。

#### hovertemplate へのテキスト直接埋め込みを全廃

ver46.2 で H&E オーバーレイ 1 箇所を直したが、同じ形が 3 箇所残っていた。
いずれも **ユーザー提供のアノテーションファイル由来の化合物名**などが入るため、
`%{x}` のような Plotly のテンプレート記法を含むとホバー時に展開されてしまう。

- `interactive_deg.py`（Feature Plot の強度ラベル）
- `share_callbacks.py`（共有ビューの強度ラベル）
- `interactive_umap.py`（クラスタ値 / サンプル名）

いずれも `meta` 経由で値として渡す形に統一した。あわせて、
「hovertemplate に動的文字列を f-string で埋め込まない」ことを検査する
テストを追加し、再発を防いでいる（`color_col` は "Cluster"/"Sample" の
固定値のみのため例外として許可）。

#### テスト

単体 576 件 / E2E 14 件すべて通過。E2E はアプリを実際に起動するため、
waitress 経由での動作（gzip 維持を含む）もこれで確認できている。

---

## 2026-07-28_ver46.2

### 修正: Spatial Mapping のホバーに「%{text}」がそのまま表示される（ver46.1 の回帰）

ver46.1 で「全点に同じクラスタ名を並べた `text` 配列」(5 万点で約 0.7MB の無駄) を
スカラーに変えた際、`text=<スカラー>` + `hovertemplate="%{text}"` の組み合わせにした。
**plotly.py の直列化は通る**ためスカラーはそのまま JSON に載るが、**plotly.js は
scattergl のスカラー `text` から `%{text}` を解決できず**、ツールチップに文字列
`%{text}` がそのまま出ていた。Python 側のテストでは検出できない種類の不具合だった。

- `hovertext=<スカラー>` + `hoverinfo="text"` に変更。全点に同じ文字列を出しつつ、
  配列を作らない利点はそのまま維持する。
- テンプレート解釈が入らないため、ユーザーが変更できるクラスタ名に `%{...}` が
  含まれていても安全（下記の H&E 側の修正と同じ理由）。

#### 同種の問題がないか、変更した全ホバー経路をブラウザで実測して確認した

| 経路 | 結果 |
|---|---|
| Spatial クラスタ別（スカラー `text` + `%{text}`） | **NG（本件）→ 修正** |
| Spatial 全点 1 トレース（配列 `text`） | 正常 |
| Feature Plot（`scattergl` + `%{marker.color}` + 配列 `text`） | 正常。SVG 時と同一表示 |
| UMAP（スカラー `meta` + `%{meta}`） | 正常 |
| H&E オーバーレイ（配列 `text`） | 正常 |

Feature Plot の WebGL 化 (ver46.1) でホバーが壊れていないことも併せて確認した。

#### 潜在していた同種の問題も修正: H&E ホバーのクラスタ名

`build_hne_overlay_fig` はクラスタ表示名を `hovertemplate` に f-string で直接
埋め込んでいた。クラスタ名はユーザーが変更できるため、`%{x}` のような Plotly の
テンプレート記法を含むとホバー時に展開されてしまう（実測で確認）。
`meta` 経由で渡し `%{meta.nm}` で参照するようにした。meta の値は再解釈されない。

#### 併せて検証した ver46.1 の他の仮定（いずれも正常）

Python 側でしか確認していなかったものをブラウザで実測した。

- **`go.Image(source=<data URI>)` が実際に描画される**こと、および座標系が
  `x0=0, dx=1`（160px 画像で xrange = [-0.5, 159.5]）であること。
  H&E 縮小時にスポット座標へ同じ倍率を掛ける実装の前提が正しいことを確認。
- **`uirevision` が同値でズーム/パンを保持し、別値でリセットする**こと。
- 列削減した 3 経路（Spatial/UMAP のマージ表示、Feature Plot）が必要な列を
  落としていないこと（`Cluster_merged` 等は元の `df` から読んでおり問題なし）。

#### テスト

**ホバー系は「描画結果」で検証する**方針に変更した。`tests/e2e/test_render_perf.py`
に、実際にマウスを乗せてツールチップの文字列を読むテストを追加（10 → 14 件）。
回帰した形（スカラー `text` + `%{text}`）が NG になることも同時に固定しているので、
同じ間違いをすれば必ず落ちる。

単体側の `test_spatial_hover_text_is_scalar_not_per_point_array` は、
結果的に**バグのある形を正しいものとして固定していた**ため修正した。
`%{text}` を使うトレースは配列 `text` を持つこと、という検査に置き換えている。

単体 571 件 / E2E 14 件すべて通過。

---

## 2026-07-28_ver46.1

### 修正: インタラクティブ解析の画像切替・パン/ズームが重い問題

「画像の切り替えやパンアウトなどの解析操作が少し重い」という報告を受けて全スクリプトを
調査した。体感やマシン性能の問題ではなく、**1 回の操作で数 MB〜数十 MB の figure JSON が
非圧縮でブラウザへ流れ、パン/ズームのたびにサーバ往復が発生していた**構造的な問題だった。

実測（4 サンプル × 25,600 spot = 102,400 spot、サンプル切替 1 回あたりの転送量）:

| | 修正前 | 修正後 |
|---|---|---|
| Spatial（1 操作の総転送量） | 20.4 MB（無圧縮） | 1.6 MB（gzip 後） |
| H&E オーバーレイ（1 タイル） | 56.0 MB | 0.55 MB |
| ホイールズーム 1 回のサーバ往復 | 10〜30 回 | 0 回 |

#### Feature Plot が SVG で描かれていた（最大の原因）

`interactive_deg.py` の Feature Plot だけ `go.Scatter`（SVG）を使っていた。SVG は
1 点 = 1 DOM ノードのため、3 万 spot のタイルで 6 万ノードが生成され、**一度開くと以降の
パンが恒久的に重くなる**状態だった。`utils/raster.py` の冒頭コメントは「対話 UI 側の図は
WebGL で速いので変更しない」としていたが、この図に限って前提が成立していなかった
（Spatial / UMAP は元から `go.Scattergl`）。

- **`go.Scattergl` に変更**（TIC 背景・発現量オーバーレイの両方）。
- **点ごとの `marker.opacity` 配列を廃止**し、強度しきい値未満の点はトレースから除外。
  従来は `opacity=0` で「見えないのに転送・描画されるだけの点」だった。
  全点がしきい値未満になる場合（Intensity Range 下限 100% 等）はカラーバーを残すため
  従来どおり全点を描く（＝図は完全に同一）。
- 各タイルの `dcc.Graph` に **id を付与**（React が再マウントせず差分更新できるようにする）。

#### H&E オーバーレイが生の RGB 配列を JSON で送っていた

`interactive_hne_bg.py` が `go.Image(z=<H,W,3 の uint8 配列>)` を使っていた。plotly は
数値配列を JSON の数値リストに展開するため、2000px の組織像 1 枚で **約 56MB** の JSON に
なっていた（スポット透明度スライダーを 1 目盛り動かすだけでこれが流れる）。

- **`go.Image(source=<data URI>)` に変更**（H&E 位置合わせタブで既に使っていた書き方）。
- 表示用に長辺 1400px へ縮小（既定、`HNE_DISPLAY_MAX_DIM` で変更可）。位置合わせ用の
  原寸 2000px は登録側でそのまま保持する。縮小率をスポット射影座標にも掛けるため、
  背景と重畳スポットの位置関係は原寸時と一致する。
- モノクロ変換の結果もキャッシュ（従来は毎描画で float64 の行列積 + `np.repeat` を実行）。
- キャッシュを件数上限 + ロック付きに変更（従来の `_HNE_ARR_CACHE` は無制限・ロック無し）。

#### 同じ figure を 1 回の操作で 2〜3 回送っていた

Spatial / UMAP / Feature の各描画コールバックが、表示用に加えて
`batch_*_figures_store` にも同じ figure を詰めて返していた。これらは「一括保存」
「サムネ登録」ボタンでしか読まれないのに、**描画のたびに全タイルの点データが
ブラウザへ送られ、ボタン押下時にサーバへ送り返されていた**。

- `batch_{umap,spatial,feature}_figures_store` を廃止し、`interactive_callbacks` の
  サーバ側ストア（件数上限 + TTL + ロック、`session_id` で分離）に移動。
- Spatial の先頭タイルで `fig.to_dict()` を 2 回呼んでいたのを 1 回に。
- `last_spatial_figure_store` は PPTX 出力（`background=True` の別プロセス）から
  参照されるため Store のまま残した。

#### パン・ズームのたびにサーバ往復が発生していた

`relayoutData` を 3 つのサーバコールバックの Input に直結していた。サーバ側は
`annotations[` キーが無いことを確認して捨てるだけだが、`scrollZoom` 有効のため
**ホイール 1 回転で 10〜30 回の POST** が飛んでいた。

- `assets/relayout_filter.js` を追加し、アノテーション移動だけを通す
  **clientside フィルタ**を前段に挟んだ。パン/ズームではネットワークが一切動かない。
- `scrollZoom` と `edits.annotationPosition`（ラベルのドラッグ保存）は従来どおり動作する。
- FS 用の 2 コールバックは 1 本に統合。

#### 操作するたびにズーム/パンが失われていた

メインタブの図に `uirevision` が無く、何を操作しても視野が既定に戻っていた
（「パンする → 設定を触る → 戻る」の繰り返し）。

- Spatial / UMAP / Feature に `uirevision` を設定。**座標が変わる要素**（サンプル切替・
  回転・反転・座標系の切替・表示モード・除外クラスタ）のみを値に含めるため、
  マーカーサイズ・色・ラベル・凡例・強度レンジの変更ではズームが保たれ、
  回転や切替では正しくリセットされる。

#### 無関係なアコーディオン開閉で全図が再構築されていた

`interactive_accordion` は `always_open=True` で、`active_item` が重い描画コールバックの
Input になっていたため、**どのセクションを開閉しても開いている図が全部作り直されていた**
（Feature Plot を開いただけで Spatial 全タイルが再構築）。

- セクションごとに前回の開閉状態を覚え、accordion 単独の発火で状態が不変なら描画を省く。
  記録が無い / 判断がつかない場合は必ず描画する安全側の実装。

#### 転送の圧縮とキャッシュ

- **`Caddyfile` に `encode zstd gzip` を追加**。これまで圧縮指定がどこにも無く、
  数 MB〜数十 MB の figure JSON がそのまま流れていた。figure JSON は 8〜12 倍に縮む。
- `Cache-Control "no-store"` を全応答に付けていたのを、静的アセット
  (`/_dash-component-suites/*`, `/assets/*`) のみ長期キャッシュ可に変更。
  plotly.js だけで約 3.5MB あり、従来はページを開くたび再ダウンロードしていた。
  HTML とコールバック応答は従来どおりキャッシュ禁止（認証付きアプリのため）。
- リバースプロキシを通さない直アクセス / E2E でも効くよう、`flask-compress` を追加し
  `dash.Dash(compress=True)` を指定。

#### サーバ側の細かい無駄

- 全点に同じ文字列を並べた `text` / `meta` 配列を廃止（5 万点で約 0.7MB の純粋な無駄。
  Plotly はスカラーを全点へブロードキャストするので表示は不変）。
- 表示用に座標の有効桁を削減（範囲の 1/100000 まで。回転後の float64 は 1 点 19 バイト
  だった）。相対量なので座標の単位（画素 / µm / mm）に依存しない。
- クラスタ列の `.astype(str)` をクラスタ数ぶんのループ内で毎回行っていたのを 1 回に
  （30 クラスタ × 5 万行 = 150 万回の変換）。点ごとの表示名解決も辞書 map 化。
- `expression_matrix.parquet`（約 18,000 列）の列名スキーマを
  `(path, mtime, size)` キーでキャッシュ。Feature 切替のたびに 3 つのコールバックが
  独立にフッタをパースしていた。
- **`seurat_bridge.get_feature_expression_fast` の `except (KeyError, Exception)` を修正**。
  事実上の `except Exception` で、列名不一致などが**無言で 30〜300 秒の Rscript
  フォールバックに落ちて**いた。列の有無を先に判定し、警告ログを出すようにした。

#### E2E テストハーネスの詰まりを修正

`tests/e2e/conftest.py` がアプリを `stdout=PIPE` で起動したまま読んでいなかったため、
アクセスログが OS のパイプバッファ (64KiB) を埋めた時点で**アプリが write でブロックし
無応答**になっていた。ブラウザでページを 1 回開くと約 9KB 出るため、E2E テストを
7〜8 本並べると再現する（増やした瞬間に直前まで通っていたテストが goto タイムアウトする）。
バックグラウンドスレッドで読み続けるようにした。E2E 全体の実行時間も 108 秒 → 48 秒。

#### テスト

- `tests/test_render_payload.py` を追加（26 件）。Feature/Spatial が `scattergl` である
  こと、H&E が `source` を運び `z` を持たないこと、全点同一の `text` 配列が復活しない
  こと、uirevision が見た目の変更で不変・幾何の変更で変わること、エクスポート figure の
  セッション分離と上限、アコーディオンガードの安全側動作、parquet スキーマキャッシュの
  無効化、列名不一致の警告などを検証する。実コールバックを Dash の dispatch 経路で
  呼ぶテストも含み、Output 数と戻り値の整合を担保する。
- `tests/e2e/test_render_perf.py` を追加（6 件）。clientside フィルタがパン/ズームを
  握りつぶしアノテーション移動は通すこと、コールバック応答が gzip で返ることをブラウザで検証。

### 追加: Stage 2/3 の残作業（見た目操作・トリガー整理・CPU/IO）

ver46.1 の第 1 弾に続き、残っていた項目を実装した。

#### 見た目だけのスライダーが全図再構築を起こしていた（Stage 2-2）

マーカーサイズ・ラベルサイズ・スポット不透明度・H&E スポットサイズは figure の
**データを変えない**のに、`update_spatial_plots`（22 Input）の Input だったため、
1 目盛り動かすたびに全タイルがサーバで作り直され全点が再送されていた。

- これら 4 つを Input → **State** に変更（Input は 22 → 18）。
- `assets/spatial_restyle.js` を追加し、`Plotly.restyle` / `Plotly.relayout` で
  ブラウザ側だけを更新する。**ネットワークもサーバ CPU も一切使わない**。
- JS がトレース構成を仮定しないよう、各トレースに役割を `meta` で持たせた
  (`{"dsz": 基準サイズからの差分, "op": 不透明度の対象か}`)。凡例ダミーや H&E 画像
  トレースには付けないので触られない。`layout.meta.kind` で通常タイルと H&E タイルを
  区別し、対応するスライダーだけが効く。
- 「自動」サイズは常に計算して `layout.meta.auto_msz` に載せる。スライダーを自動に
  戻したとき、サーバに問い合わせずブラウザ側で基準値を復元できる。
  あわせて、同じ計算をしていたインライン実装を `_calc_zero_gap_marker_size` に一本化した。
- **画面と保存 PNG の一致を維持するため**、一括保存・サムネ登録の直前に同じ変換を
  サーバ側 figure にも適用する（`display_helpers.apply_display_overrides`）。
  JS と Python で規則が食い違うと「画面と PNG が違う」という最悪の壊れ方をするため、
  「後付け適用の結果 == 最初からその値でビルドした図」をテストで固定した。

#### フルスクリーンを閉じるだけで全図が再構築されていた（Stage 2-3）

`fullscreen_closed_trigger` は閉じるたびに無条件でインクリメントされ、統合 UMAP・
サンプル別 UMAP・Spatial 全タイル・Feature Plot の 5 コールバックが一斉に走っていた。

調査の結果、フルスクリーン側が書き込む共有 Store は `accumulated_label_positions`
（クラスタラベルのドラッグ位置）**だけ**だった。他（回転・クラスタ色・クラスタ名・
凡例の灰色化）は読み取り専用で、変更されればそれ自体がメイン側の Input なので独立に
再描画が走る。そこで開いた時点と閉じた時点のラベル位置を比較し、**変化が無ければ
再描画しない**ようにした。比較できない場合は従来どおり再描画する安全側の実装。

#### サーバ側 CPU / ディスク I-O（Stage 3 残り）

- **10 万行の全列 `df.copy()` を 4 箇所で廃止**（Spatial のマージ表示・UMAP の
  マージ表示・Feature Plot の発現量結合・Violin）。いずれも数列しか使っていないので、
  必要な列だけを取り出すようにした。
- **`label_positions_*.json` の毎描画ディスク読みをキャッシュ**。ファセット表示では
  図の数だけ `read_text` + `json.loads` + `logger.info` が走っていた。
  `(path, mtime, size)` キーなので保存（`os.replace`）で自動失効する。
  併せて毎回 INFO で出していたログを DEBUG に下げた。

#### テスト

`tests/test_render_payload.py` を 26 → 36 件に、`tests/e2e/test_render_perf.py` を
6 → 8 件に拡充。特に以下を固定した。

- 後付け適用と新規ビルドの一致（画面と保存 PNG の乖離防止）
- 見た目スライダーが Input に戻っていないこと
- パン/ズームのフィルタと見た目 restyle が **clientside として登録**されていること
  （サーバ側に戻ると無音で元の重さに逆戻りするため）
- `relayoutData` をサーバ側 Input に直結したコールバックが復活しないこと

単体 570 件 / E2E 10 件すべて通過。

#### 今回見送ったもの（別 PR 推奨）

- **plotly 5 → 6**。数値配列が base64 送信になり転送量がさらに 3〜5 倍減るが、
  `kaleido==0.2.1` が plotly 5 前提で固定されており PPTX/PNG 出力の全経路の再検証が要る。
- **Werkzeug 開発サーバ → gunicorn/waitress**。同時利用者がいる場合に効くが、
  起動・シグナル処理・`DiskcacheManager` の fork 挙動の再検証が要る。
  なお本 PR のサーバ側 figure ストアはプロセス内メモリのため、マルチワーカー化する際は
  diskcache 等の共有ストアへ移すこと（コード内にも注記済み）。

---

## 2026-07-26_ver46.0

### 機能: 再解析（クラスタ除外→再UMAP）を Step1/Step2 から再開できるようにした

10万px 級の再解析は完走に **2 時間超**かかる。Step3(RPCA) の挙動だけを確認したい場合でも
毎回 Step1/Step2 をやり直すのは現実的でなく、メモリ対策の検証が進めにくかった。
調査の結果、再開できなかった原因は 2 つあった。

1. **再解析パスに再開機能が配線されていなかった**。`V13_RESUME_FROM_RDS` / `V13_RESUME_DIR_PATH`
   はオーケストレータに `FALSE` / `""` とハードコードされ、アプリ側からも設定されていなかった
   （生成コピーへ値を流す仕組み自体は既に存在していた）。
2. **保存済み Step2 RDS に `counts` 層が無い**。`keep_counts=FALSE` で保存されるが、
   Step3 の `apply_input_norm(seu_rpca)` が `counts` を読むため、再開すると RPCA が動かない。

- **`REUMAP_RESUME_DIR` を追加**（`260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R`）:
  前回実行の `RDS_Files` を指定すると Step1/Step2 RDS を読んでその先だけを実行する。
  未指定なら従来どおり最初から。存在しないパスは警告して無視する。
- **`apply_input_norm` を slim RDS 対応に**（`260623_DBSCAN_With_cluster_ver6_no-png_slim.R`）:
  本関数がやることは `data <- <NORM_MODE>(counts)` なので、**`counts` が無くても `data` が既に
  あれば再計算は不要で結果も同一**。counts の不在で停止せず既存の `data` を使うようにした。
  これにより **`keep_counts=FALSE` で保存済みの既存 Step2 RDS からも再開できる**
  （＝過去に失敗した実行の成果物を捨てずに済む）。slim RDS 全般に対する堅牢化でもある。
- **`SAVE_STEP2_WITH_COUNTS` を追加**: Step2 RDS に `counts` を含めて保存する。既定は従来どおり
  `0`（対話ビューアがこの RDS を読むため軽い方が良い）。上の堅牢化により通常は不要だが、
  完全に同一の状態から再開したい場合に使う。
- **`docker-compose.yml` / `.env.docker` の整備**: これまで R 側にだけ追加してコンテナへ渡って
  いなかった `DEG_WORKERS` / `INGEST_BLOCK_MB` / `QS_NTHREADS` / `SAVE_STEP1_RDS` も含め、
  検証用オプションを一括で中継・文書化した（`.env` 変更のみで反映でき、再ビルド不要）。

**再開時も Parquet エクスポートは実行される**。これは無駄ではなく、オーケストレータ側の
メモリ状態（Arrow プールの残留を含む）を通常実行と揃え、**RPCA 直前の条件を忠実に再現する**ため。
また `seu_list`（Step1 RDS 由来）は Step3 の分岐判定で参照されるため、Step1 も読み込む。

#### UI からも指定できるようにした

初回解析（UMAP解析）には「途中再開 (RDSから)」の UI があるが、**再解析側には存在しなかった**
（`generate_analysis_config` には RESUME 処理があるのに `generate_cluster_filter_config` には無い）。
再解析画面に**独立した「途中から再開」ブロック**を追加した。

**「RDS指定」との混同を防ぐことを最優先に設計した。** あの欄は
`resolve_rds_path` → `get_cells_to_keep` に渡る「**どのクラスタリングの番号で除外するか**」の
参照元であって再開地点ではないが、名前が紛らわしく実際に誤解を招いていた。そこで新 UI は
「RDS指定」ブロックの**外**に離して配置し、ラベル・ツールチップ・警告表示で
「上の『RDS指定』とは別の設定です」「『RDS指定』＝どのクラスタリングの番号で除外するか／
ここ＝どこまで計算済みの結果を再利用するか」と明示。あわせて「主に動作検証用です。通常の解析では
OFF のままにしてください」と注意書きを出す。

- `App/app/layouts/settings_tab.py`: チェックボックス `resume_reanalysis` と
  フォルダ指定 `resume_reanalysis_dir`（参照ボタン・パスヒント付き）
- `App/app/callbacks/file_handlers.py`: パネル表示切替、参照ボタン登録、パスヒント対象へ追加
- `App/app/callbacks/analysis_callbacks.py`: State と params 収集
- `App/app/services/analysis_runner.py`: `generate_cluster_filter_config` で
  `V13_RESUME_FROM_RDS` / `V13_RESUME_DIR_PATH` を置換
- `App/app/layouts/tooltips.py`: 説明ツールチップ

**チェック OFF のときは params 自体を立てないため、既定挙動は完全に従来どおり。**
パスが未入力の場合も安全側で無効になる。

## 2026-07-26_ver45.9

### 修正: 実測に基づくメモリ削減（scale.data の早期破棄・DEG 並列数の動的決定）

ver45.8 のタイムアウト修正により解析は **2.4 時間**走り、Step1 / Step2(Harmony 8反復収束) / UMAP と
**harmony ダウンストリーム（24 クラスタのマーカー検出・注釈・ヒートマップ・ボルケーノ・MSI画像・
TIC重ね合わせ）を完走**した。続く pca_uncorrected の `FindAllMarkers` で停止し、
ver45.8 で追加した記録が原因を明示した:
```
[EXIT] R プロセスは シグナル SIGKILL(9) による強制終了 で終了しました。
```
タイムアウトは無効なので SIGTERM ではなく **SIGKILL = OOM**。今回は推測ではなく計測が裏づけている。

**実測されたメモリ推移**（138,142 spot × 2000 features、密度 36.5%、スパース 1.13 GB）:

| 段階 | RSS | 増分 |
|---|---|---|
| 解析のベース（orch 解放後） | 3.05 GB | |
| 取り込み完了 | 3.54 GB | +0.49（ストリーミングが有効） |
| Step1 完了 | 4.29 GB | |
| Step2 ScaleData 前 | 7.28 GB | +2.99 |
| **Step2 ScaleData 後** | **11.96 GB** | **+4.68 ← 上限 12.0GB に肉薄** |
| Step2 RunPCA 後 | 11.44 GB | |
| pca_uncorrected の FindAllMarkers | — | **SIGKILL** |

- **`scale.data` を `RunPCA` 直後に破棄**（ver6 の `run_pipeline` と ver18 のパッチ版の両方）:
  `ScaleData` の +4.68 GB は PCA 計算後は不要。以後 11.4 GB 前後で全工程（Harmony/UMAP/
  downstream ×2/RPCA）が走っていたため、ここで破棄すると**以降すべてが軽くなる**。
  安全な根拠はコードで確認済み — downstream のヒートマップは
  `ScaleData(subset(obj, cells=cells_sub), features=top_genes, ...)` で 1000 セル×上位遺伝子に
  限定して作り直しており（「slim RDS/diet で空の scale.data を補完」というコメントのとおり
  **空である前提の設計**）、Step2 の RDS 保存も `keep_scale=FALSE` で元々落としている。
  `RunHarmony` は PCA 埋め込みに対して動く。RPCA ブロックでも既に破棄済みで、それを前倒しするだけ。
- **`FindAllMarkers` の並列ワーカー数を実測メモリから決定**: 従来は空きメモリを一切見ずに
  常に 4 ワーカーを起こしていた。`multisession` は各ワーカーへオブジェクトを複製するため、
  残 0.6 GB で 4 ワーカーを起こせば確実に落ちる（これが死因）。cgroup の
  `memory.max − memory.current` とオブジェクトサイズから安全な数を算出し、乏しければ
  `sequential`（並列化しない）に落とす。決定理由と採用値を `[deg]` 行に記録する。
  環境変数 `DEG_WORKERS` で明示指定も可能（0/1 で逐次）。
- **downstream に計測を追加**: 開始時・`FindAllMarkers` 前後・終了時の RSS を記録し、
  harmony と pca_uncorrected の間でどれだけ解放されたかを追えるようにした。
- R スクリプトのバージョン番号（ver6 / ver18）は据え置き。**解析結果（クラスタ・reduction・
  マーカー）は不変**で、メモリ特性のみ改善。

## 2026-07-26_ver45.8

### 修正: 停止の真因は 20 分タイムアウトだった。無言 kill の可視化と誤診の是正

**原因が確定した。メモリ不足ではなく `R_ANALYSIS_TIMEOUT_SEC=1200`（20分）による強制終了だった。**
アプリログ（`Data/Other/logs/msi_app.log`）に決定的な記録があった:
```
12:35:42 [WARNING] msi.analysis_runner:
  R subprocess pid=1790 が R_ANALYSIS_TIMEOUT_SEC=1200s を超過、SIGTERM 送信
```
解析開始 12:15:42 → SIGTERM 12:35:42 で**ちょうど 1200 秒後**。`OOMKilled=false` /
`RestartCount=0` / `memory.events: max 0, oom_kill 0` / `memory.max`(12GB) 未到達 であり、
メモリは一切関係していなかった。10万px 級の再解析は実測**約 2 時間**を要するため 20 分では足りない。

**混入経路**: ver45.3 で「`.env` の設定がコンテナに届いていない」配線漏れを修正した際、
`R_MAX_VSIZE_GB` と同時に `R_ANALYSIS_TIMEOUT_SEC` も `docker-compose.yml` の `environment:` へ
中継した。その結果それまで無効だった 20 分タイムアウトが突然有効化された。`R_MAX_VSIZE_GB=8` の
副作用には気づいて対処したが、同じ変更で有効化されたタイムアウトを見落としていた。
45.2〜45.7 で前提にしてきた「無言終了＝メモリ不足」という診断は**誤り**だった。

**誤診を招いた構造的欠陥と、その修正**:
- **タイムアウト kill の理由が解析ログに出ていなかった**。`logger.warning` でアプリログにしか
  残らず、ユーザーが見る解析ログでは「途中で途切れるだけ」に見えた。kill 直前に解析ログへ
  `[TIMEOUT] 実行時間が ... を超過したため強制終了します。解析自体は正常に進行していた可能性が
  あります。` と対処法つきで明記するようにした。
- **有効な実行制限が開始時に明示されていなかった**。`R_ANALYSIS_TIMEOUT_SEC` が有効な場合、
  解析ログ先頭に `[NOTE] 実行時間の上限 ... が有効です` を出す（`R_MAX_VSIZE_GB` と同様）。
- **`.env.docker` の推奨値が実態と乖離していた**。「推奨: 通常解析 1200 (20 分)」を実測に基づき
  改訂し、10万px 級で約 2 時間かかること、まず 0（無効）で所要時間を測ること、制限するなら
  実測の 2 倍以上（6 時間程度）を目安にすることを明記した。

### 併せて: 終了シグナルの記録と停止点の特定

ver45.7 で入れた RSS 計測により、実測値が初めて得られた:

| 段階 | RSS |
|---|---|
| オーケストレータ起動直後 | 1.71 GB（R＋ライブラリの固定費） |
| 元 RDS 読み込み後 | 2.90 GB |
| フィルタ Parquet 書き出し後 | 4.33 GB（Arrow プール） |
| 元オブジェクト解放 + gc 後 | 2.99 GB（解放は効いている） |
| 取り込み完了 | 3.49 GB（ストリーミングにより **+0.50 GB のみ**） |

`[sparsity] 非ゼロ 100,782,779 (密度 36.5%) / スパース保持 1.13 GB vs 密 2.06 GB` も得られ、
**スパース保持が正しい選択**であることを確認（密度 36.5% < 2/3）。

そのうえで `/sys/fs/cgroup/memory.events` を確認したところ **`oom_kill 0` / `max 0`**、すなわち
**コンテナは一度もメモリ上限に触れていなかった**。45.2〜45.7 で前提にしてきた「無言終了＝cgroup OOM」
という診断は誤りだった。原因を推定ではなく確定させるため、以下を追加する。

- **終了コード/シグナルを必ず記録**（`analysis_runner.py`）: R の終了値は取得済みだったが記録して
  いなかったため、無言終了の原因を切り分けられなかった。負値はシグナルによる強制終了
  （-9 SIGKILL / -11 SIGSEGV / -15 SIGTERM）を意味し、正値は R 自身の異常終了を意味する。
  アプリログに加え、**解析ログの末尾にも `[EXIT] ...` として追記**する。
- **停止点の絞り込み**（`rds_io.R`）: 停止は Step1 の `save_rds_compact` 付近だった。
  `DietSeurat` と `qs::qsave` の**開始/完了を個別に出力**し、どちらで落ちるかを特定できるようにした。
  保存サイズも記録する。
- **`QS_NTHREADS` を追加**（`rds_io.R`）: `qs` のマルチスレッド圧縮はネイティブコードで動くため、
  そこでのクラッシュは R のエラーを残さずプロセスごと落ちる。`QS_NTHREADS=1` で単スレッド化して
  切り分けられる。`parallel::detectCores()` はコンテナの CPU 制限ではなくホストのコア数を返す
  場合があり、割り当て以上のスレッドを立てうる点でも上書き手段が必要だった。未設定なら従来どおり。
- **Step1 の計測強化と無駄削減**（ver6）: `FindVariableFeatures` の前後を計測。あわせて
  **Step1 RDS は同一実行内では一度も読まれず、解析完了時に削除される**（読むのは
  `RESUME_FROM_RDS` で別ディレクトリを指した後続実行のみ）ことを確認したため、
  `SAVE_STEP1_RDS=0` で保存をスキップできるようにした。**完走時の最終出力は一切変わらない**
  （中断時の再開ができなくなるだけ）。未設定なら従来どおり保存する。
- R スクリプトのバージョン番号（ver6 / ver18）は据え置き。既定動作・出力とも不変。

## 2026-07-26_ver45.7

### 修正: ver45.5 の子プロセス化による退行を解消し、メモリ実測値をログに出力

ver45.6 で実行した exclude 3-4-5 の再解析が **Step2 の ScaleData 直後**で無言終了した。ログには
R のエラーも ver45.5 で追加した「再解析(ver13 コピー)が異常終了しました」も出ておらず、**親子とも
同時に落ちた = cgroup OOM kill**。ここで退行が判明した:

| 実行 | spot 数 | 実行形態 | 到達点 |
|---|---|---|---|
| 前回 | 139,682（多い） | `source()` 同一プロセス | Step3 RPCA まで |
| 今回 | 127,901（少ない） | 子プロセス (ver45.5) | Step2 ScaleData で停止 |

データが少ないのに早く落ちており、新実装の総メモリ使用が増えている。

- **子プロセス起動を `source()` に戻した**（`260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R`）:
  子プロセス方式は親が `system2` でブロックしたまま常駐し、子は綺麗なヒープを得る代わりに
  **親の解放済み領域を再利用できない**ためピークが `親 + 子` になる。`source()` は 1 プロセスなので
  オーケストレータが解放したヒープを解析側が再利用でき、ピークは `max(親, 子)` で済む。
  ver45.5 で「積み上がりを防ぐ」意図だった変更が、この環境では逆効果だった。
  ver45.2 の「置換しないときは元オブジェクトを解放」は有効なため維持。
- **メモリ実測値(RSS)をログに出力**（ver6 / ver18 両方）: これまでメモリを推測で議論しており、
  実測値は ver45.5 で得た「密換算 1.91 GB」だけだった。`/proc/self/status` の VmRSS を読み
  （依存追加なし）、オーケストレータは「起動直後 / 元 RDS 読み込み後 / Parquet 書き出し後 /
  元オブジェクト解放+gc 後 / コピー実行直前」、解析側は「取り込み完了 / Step1 完了 /
  Step2 ScaleData 前後 / RunPCA 後 / Step3 RPCA 各段」で記録する。**gc() 由来の表示は R ヒープしか
  見ておらず未返却領域や Arrow プールを含む実態と乖離するため、RSS 基準へ統一**した。
- **実際の疎性を出力**: MSI 強度はゼロが少なく `dgCMatrix` は 12 byte/非ゼロ（密は 8 byte/要素）
  のため、密度が 2/3 を超えると「スパース化」の方が密より重い。非ゼロ数・密度・スパース保持サイズと
  密サイズを並べて表示し、密の方が小さい場合はその旨を明示する。
- R スクリプトのバージョン番号（ver6 / ver18）は据え置き（**出力は不変**、計測とプロセス構成のみ変更）。

**調査で判明した他のメモリ地雷（本版では未対応）**: 取り込みの `do.call(rbind, blocks)` が一時的に
2 倍を要する、`apply_input_norm` が counts と data を同時保持する、`merge_duplicate_features`
（m/z 重複時のみ実行・今回は未実行）が全密展開＋2 倍を要する。実測値を見てから対処する。

## 2026-07-26_ver45.6

### 修正: 等価性テストの検証漏れ（複数ブロック連結経路）を解消

ver45.5 の等価性テストは実環境で PASS したが、出力の `[stream] 40 列ずつ 1 ブロックで読み込みます` が
示すとおり、合成データ（500 spot × 40 特徴量）が小さいため **1 ブロックで完結**しており、
`do.call(rbind, blocks)`（複数ブロックの連結）が**一度も実行されていなかった**。実データでは
139,682 spot × 約 3,580 特徴量で約 18 ブロックに分割されるため、本番でだけ未検証の経路が走る状態だった。
ここで特徴量の順序がずれると、エラーにならず m/z 名と強度の対応だけが崩れる silent failure になるため、
出荷前に潰す。ブロック幅は `256MB ÷ (行数 × 8)` で決まり小さなテストデータでは構造的に分割できないので、
ブロック予算を外から与えられるようにした。

- **`INGEST_BLOCK_MB` を追加**（`260623_DBSCAN_With_cluster_ver6_no-png_slim.R`）: 取り込み時の
  1 ブロックあたり予算（既定 256MB）を環境変数で上書き可能に。**未設定なら従来どおり 256MB＝既定の
  挙動は不変**。検証用フックであると同時に、メモリ逼迫時の調整つまみとしても使える。
- **等価性テストを 2 パス構成に拡張**（`App/Script/helpers/test_parquet_ingest_equiv.R`）:
  「既定ブロック幅（単一ブロック）」と「`INGEST_BLOCK_MB=0.05` による複数ブロック強制」の両方で
  旧実装と比較し、どちらか一方でも不一致なら FAIL とする。合成データでは 13 列 × 4 ブロックに分割され、
  連結経路が確実に実行される。パス 2 が実際に複数ブロックになっているかを出力で確認するよう注記も追加。
- R スクリプトのバージョン番号（ver6）は据え置き（既定動作・出力とも不変）。

## 2026-07-25_ver45.5

### 修正: 再解析のメモリ枯渇を「対症療法」から根本対策へ（取り込みのストリーミング化・子プロセス分離）

45.1〜45.4 はいずれも「密コピーを 1 個減らす／設定を直す」もので、失敗地点が後ろへ移動しただけだった
（構文破綻 → FindAllMarkers → RPCA → Parquet 読込）。根本原因は **全 spot × 全 m/z の強度行列を密な R
オブジェクトとして何度も実体化し、さらに再解析がフィルタ処理と本解析を 1 プロセスで連続実行するため、
ピークがデータ実体（10万px 級で約 4GB）の数倍に膨らむ**こと。コンテナ予算 12GB は実体 1 個ぶんしか
想定しておらず、需要と予算のギャップが閉じていなかった。

- **取り込みのストリーミング化**（`260623_DBSCAN_With_cluster_ver6_no-png_slim.R`）: 旧実装は
  `read_parquet(as_data_frame=TRUE)`(密) → `as.matrix`(密2) → `!is.finite` の論理行列 → `t()`(密3) →
  `dgCMatrix` と密コピーを 4〜5 個同時に抱えていた。メタ列（id/x/y/annotation）だけ先に読んで座標と
  行マスクを確定し、強度は **m/z 列をブロック単位で読んで逐次スパース化して積む**方式へ変更。
  全体の密行列・論理行列・`t()` を一切作らない。ブロック幅は 1 ブロックの密サイズが 256MB を超えない
  よう行数から自動決定。行グループ構成に依存しない列ブロック方式のため、単一行グループの Parquet でも効く。
- **事前見積り（PreFlight）**: 取り込み前に `spot 数 × 特徴量数` と密換算サイズをログに出力し、
  規模を即座に把握できるようにした（2 時間走ってから落ちる事故の抑止）。
- **オーケストレータのフィルタを Arrow Table のまま処理**（`260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R`）:
  元 Parquet 全体の密 data.frame 化＋フィルタ後コピーで計 8GB 超を要していた箇所を、
  `as_data_frame=FALSE` で Arrow Table（R ヒープ外の列バッファ、float32 のまま保持）として扱う方式へ変更。
  R 側に載せるのは id 列のみ。arrow のバージョン差に備え、行抽出に失敗した場合は従来の data.frame 経路へ
  自動フォールバックする。書き出しは `chunk_size` を明示し、生成物に行グループを持たせる。
- **本解析を子プロセスで起動**: `source(v13_copy_path)` を `Rscript --vanilla` の子プロセス起動へ変更。
  プロセス終了時に OS がメモリを全回収するため、「オーケストレータの残骸＋本解析のピーク」が積み上がる
  事故が構造的に起こらなくなる（45.2 で個別に潰した問題の一般解）。生成コピーは設定を焼き込んだ
  スタンドアロン版で、初回解析が同じ仕組み（`v8_runtime_*.R` を `Rscript --vanilla` で実行）で動作している
  ことにより単独実行可能性は実証済み。終了ステータスが非 0 なら明示的に停止する。
- **等価性検証スクリプトを追加**（`App/Script/helpers/test_parquet_ingest_equiv.R`）: 旧実装を同ファイル内に
  再現し、新実装との `count_matrix`（次元・dimnames・全要素・非ゼロ数・クラス）と `coordinates` の完全一致を
  検証する。引数省略時は 0/NA/Inf/-Inf を含む合成 Parquet を生成して境界条件も確認。**出力が変わらないことが
  導入の前提条件**のため、実環境で必ず実行すること。
- R スクリプトのバージョン番号（ver6 / ver18）は据え置き（挙動・出力は不変、メモリ特性のみ改善）。

**運用側の対応（コードでは解決しない）**: 上記を入れても 158k spot × 約 3.6k 特徴量を 3 パス処理するには
12GB は小さい。`.env` の `R_MAX_VSIZE_GB=0`（必須。8 のままでは本対策の効果が見えない）、
`mem_limit` の 24〜32g への引き上げ、逼迫時は `RUN_RPCA=0` を推奨。

## 2026-07-25_ver45.4

### 修正: R_MAX_VSIZE_GB が低すぎる設定のまま解析が開始直後に落ちる事故を防止

ver45.3 で `R_MAX_VSIZE_GB` がコンテナへ正しく渡るよう配線を修正した結果、それまで**届いていなかった
（＝実質無制限で動いていた）**設定値が初めて有効化され、`R_MAX_VSIZE_GB=8` の環境で解析が Parquet
読込直後に `Error: vector memory limit of 8.0 Gb reached` で停止するようになった。`mem_limit: 12g` に対し
R 内部上限だけ 8GB と低く、10万px 超のデータでは開始直後に上限へ到達するため。設定値自体の問題だが、
「無言で適用され、低すぎても警告が無い」構造が誤診を招くため作り込みで防ぐ。

- **設定不整合の検知**: cgroup（v2 `memory.max` / v1 `memory.limit_in_bytes`）からコンテナのメモリ上限を
  読み、`R_MAX_VSIZE_GB` がその 75% を下回る場合にアプリログへ WARNING を出力。取得できない環境では
  判定をスキップする（例外を投げない）。
- **解析ログ先頭への明示**: R の出力より前に `[NOTE] R メモリ上限 ... を適用しました` を必ず記録し、
  低すぎる場合は `[WARN]` で具体的な対処（0 にする／コンテナ上限の 9 割へ）を併記。ユーザーがエラーを
  見る場所そのものに原因と対処が出る。
- **`.env.docker` の例示を修正**: 旧記述「mem_limit=10g なら R_MAX_VSIZE_GB=8 等」が今回の誤設定の元。
  「低すぎると開始直後に落ちる」ことと、まず 0（制限なし）で運用し切り分け時のみ mem_limit の 9 割
  （12g なら 11）を設定する方針へ改めた。
- **ヘルプ更新**: トラブルシュート項目を実際のエラー文言 `vector memory limit of N Gb reached` に合わせ、
  「開始直後に落ちるなら値が低すぎる」「まず 0 を試す」という対処順を明記。

## 2026-07-25_ver45.3

### 修正: 再解析が Step3 RPCA でメモリ枯渇(OOM)により停止する不具合

ver45.2 で FindAllMarkers は完走するようになったが、停止点が **Step3 RPCA(IntegrateLayers)** へ移り、
`Running RPCA (Seurat v5 IntegrateLayers)...` の直後に無言終了（R エラー無し）して status=error になっていた。
RPCA は `IntegrateLayers` を tryCatch し失敗時は `!! RPCA(IntegrateLayers) failed:` を出して nfeatures を
2000→1000→500 と下げて再試行する設計であり、そのメッセージすら出ないのは R がエラーを出す前に
cgroup OOM kill されたことを意味する。Step3 突入時点で (a) Harmony 側オブジェクト、(b) そこから
subset した RPCA 用の複製、(c) 直前の無補正PCA下流解析が残した近傍グラフ等の中間、が同時に載っていた。

- **RPCA 前の中間破棄**: 近傍グラフ(graphs)、RPCA で使わない reduction(umap 等)、scale.data を
  Step3 開始前に破棄。RPCA は PCA 空間のみ使うため結果は不変で、複製前に落とすことで複製サイズ自体も縮む。
- **二重保持の解消**: 「複製を作ってから元を捨てる」順序をやめ、全セルが対象で内容が変わらない場合は
  複製せず参照を付け替え、複製が必要な場合も直後に元参照を外して即回収する。
- **診断性**: Step3 の各段階で R ヒープ使用量を `[mem]` 行として出力し、IntegrateLayers の試行ごとに
  nfeatures/k.weight をログ表示。無言終了時も「どこまで進み、どれだけ使ったか」を追える。
- **逃げ道**: `ENABLE_RPCA`（環境変数 `RUN_RPCA=0` で上書き可）を追加。低メモリ環境では RPCA を
  スキップし Harmony/無補正PCA まで完走して正常終了できる。
- **環境変数の配線漏れ修正**: `R_MAX_VSIZE_GB` / `R_ANALYSIS_TIMEOUT_SEC` は `docker-compose.yml` の
  `environment:` に列挙されておらず、`.env` に設定してもコンテナへ渡っていなかった（ヘルプは
  「`.env` の `R_MAX_VSIZE_GB` を増やす」と案内しているのに無効だった）。`RUN_RPCA` と併せて中継するよう修正。
- R スクリプトのバージョン番号（ver6 / ver18）は据え置き（挙動修正のみ）。

## 2026-07-24_ver45.2

### 修正: 再解析(exclude)の FindAllMarkers でメモリ枯渇(OOM)により解析が停止する不具合

ver45.1 で構文破綻は解消したが、クラスタ除外の再解析が `FindAllMarkers`（`plan(multisession)` で
並列化される段階）で無言終了（R エラー無し）し status=error になる事例が残っていた。原因は、再解析
オーケストレータ（`App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R`）が、置換
（`apply_reumap_replace`）用に元の巨大 Seurat オブジェクトを退避保持したまま `source()` で再解析全体を
同一プロセス内実行していたため。exclude では置換が無効で退避が使われないにもかかわらず元データ（例:
158k セル）を抱え続け、その上で FindAllMarkers が並列ワーカー（別プロセス）を起動した瞬間に物理RAM上限
（`mem_limit 12g`）を突破し、cgroup OOM kill が発生していた。1回目の単独実行（RPCA まで完走）が通るのに
より軽い再解析が落ちる、という矛盾もこれが原因。

- **修正**: 置換を実際に行うとき（keep ＋ 置換有効）だけ元オブジェクトを退避し、それ以外（exclude 等で
  置換無効）は `source()` 前に元データを解放（`rm` ＋ `gc`）してから再解析を実行するようにした。再解析
  コピーは自前で入力 parquet を読むため元オブジェクトは不要で、再解析プロセスのベースメモリが単独実行と
  同等まで下がる。FindAllMarkers の並列数は据え置き（速度不変）。
- （運用）`R_MAX_VSIZE_GB`（`analysis_runner.py` 参照）を memswap 上限に近い値で設定すると、超過時に
  無言 OOM ではなく `Error: vector memory exhausted` で明示終了でき、切り分けが容易になる（値は環境依存の
  ため本修正では未設定）。
- R スクリプトのバージョン番号（ver6 / ver18）は据え置き（挙動修正のみ）。

## 2026-07-23_ver45.1

### 修正: ClusterFilter_ReUMAP の再解析コピー生成が構文破綻して解析が停止する不具合

クラスタ除外/抽出の再UMAP時、ベース解析スクリプトを複製して設定を差し替える
`patch_v13_step2_pipeline()`（`App/Script/TIMS/260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R`）の
「Retry Logic」置換が、ベース側の `if (!is.na(group_var)) { ... }` ガードの閉じ括弧を巻き込んで削除し、
生成コピーの波括弧が1個閉じられず `unexpected end of input` で `source()` が失敗、解析が status=error で
停止していた。

- **修正**: ガードを検出し、置換範囲と置換テキストの両方で `if (!is.na(group_var)) { ... }` を保持
  （group_var が NA のとき Harmony をスキップする本来の挙動も回復）。ガードの無い旧ベースは従来動作を維持。
- **防御**: `make_v13_copy_with_settings()` で生成直後に `parse()` 検証を追加し、構文不正なら出力パスを
  明示して即停止。
- ベース/オーケストレータの R スクリプト版番号（ver6 / ver18）は据え置き（挙動修正のみ）。

## 2026-07-16_ver45.0

### 機能: 登録済みデータへ「分子情報（化合物名）」を後から登録

分子情報（化合物名 / m/z / データベース / アダクト / 組成式）は通常 SCiLS→Parquet 変換時に peak-list CSV から
付与されるが、peak-list 無しで登録すると「分子情報なし」データになる。数GBの本体を再登録し UMAP を再計算するのは
非現実的だったため、**SCiLS「Static feature list」CSV を後からアップロードし、本体 parquet を書き換えずに
サイドカー `<BASE>_feature_annotations.parquet` を生成**して分子情報を反映できるようにした。生成物は通常登録と同一
（既存の `scils_converter._read_peaklist` / `peak_annotation.build_feature_annotation_table` を再利用）。対象は
TIMS/SCiLS（MALDI 含む）。

- **新規 `services/molinfo_attach.py`（Dash 非依存）**: `attach_molecular_info(sub, csv_path)` が、本体 parquet の
  footer メタ `mz_sorted`（分子情報なし登録でも存在）から特徴量 m/z を取得し、CSV を最近傍 0.01 Da で突き合わせて
  サイドカーを生成。`data_folder`（＋現行の結果ディレクトリ）へ書き出し、キャッシュを無効化。`dry_run` でマッチ
  件数のみのプレビューも可能。
- **UI**: サブプロジェクトカードに**「分子情報を登録」ボタン**（`sub_action_add_molinfo`）と新規モーダル
  （`add_molinfo_modal.py`）を追加。CSV をアップロードするとマッチ件数をプレビューし、「登録」でサイドカー生成。
  登録後は化合物名バッジ・プレビュー・インタラクティブ解析・PPTX・共有に反映（配線 `add_molinfo_callbacks.py`）。
- **反映範囲を拡張**:
  - `seurat_bridge._load_feature_annotations`: `feature_annotations.json` キャッシュが**サイドカーより古ければ
    作り直す**よう自己修復（後付け/更新が確実に反映）。
  - データ出力（生エクスポート）: `interactive_data_export._export_tims` に `_apply_feature_annotation_columns` を追加し、
    **エクスポート時にサイドカーで m/z 列名を埋め込み名へ変換**（本体多GBは書き換えない）。
  - `data_manager._filter_tims_candidates`: 注釈サイドカー（`*_feature_annotations.parquet`）を TIMS 入力サンプル
    候補から除外（後付けサイドカーが誤ってサンプル扱いされるのを防止）。
- 退行防止: 新規 `tests/test_molinfo_attach.py`（サイドカー生成・件数・tol境界・列名フォールバック・サイドカー除外）、
  `test_seurat_annotation_cache.py`（キャッシュ自己修復）、`test_data_export_annotation.py`（列名変換・冪等）、
  `test_add_molinfo_callbacks.py`（プレビュー/確定/エラー処理）。
- version 44.1→45.0。

---

## 2026-07-16_ver44.1

### 修正: 「化合物名」モーダルが特定サブプロジェクトで“開くのが遅い”問題を解消

サブプロジェクトカードの「化合物名」ボタン（ver44.0）を押すと、特定のサブプロジェクトだけモーダル表示までに
長い待ちが生じ、「押しても開かない（無反応）」に見えていた（実際は時間が掛かって開いていた）。原因は、クリック
コールバックが**重い `inspect_annotations`（ファイル読取）を完了してからモーダルを開く**構造で、進捗スピナー
（`dcc.Loading`）もモーダル本文内にあるため開くまでフィードバックが出ないこと。

- **即時オープン＋スピナー化**（`annotation_preview_callbacks.py` / `annotation_preview_modal.py`）:
  コールバックを「即時オープン（I/O なし・対象を `dcc.Store="annotation_preview_target"` へ積む）」と
  「後追い populate（`inspect_annotations`→描画）」の 2 段に分割。モーダルは即座に開き、`dcc.Loading` が
  populate 実行中のスピナーを表示する。再オープン確実化のためクリック `n_clicks` を Store の nonce に含める。
  併せて未保護だった `_render` を try/except で保護（描画失敗もモーダル内エラー表示になり無反応化しない）。
- **判定の高速化・キャッシュ**（`annotation_inspect.py`）:
  - DESI の named ヘッダ読取を `pd.read_excel(nrows=0)`（openpyxl がブック全体をパース）から
    **`_xlsx_header_fast`（read_only で先頭 1 行のみ）**へ置換。
  - `inspect_annotations` を**署名（sub id＋候補フォルダの mtime）ベースでメモ化**。同一状態の再オープンを即時化。
  - `_main_parquet_peaklist` のフッタ走査に上限（`_PEAKLIST_SCAN_CAP=50`）を追加。
- 退行防止: `tests/test_annotation_inspect.py` に xlsx 高速読取の等価性・キャッシュのヒット/無効化テストを追加。
- version 44.0→44.1。

---

## 2026-07-15_ver44.0

### 機能: 登録済みデータの「化合物名の有無」を生データを開かずに確認

登録したデータに化合物名（注釈）が最初から含まれているかを確認する手段が無く、生データ（.parquet/.txt）は
**1GB 超**で手元で開けなかった。SCiLS 変換時に生成される**小さな副産物だけ**を読んで注釈状況を要約表示する
ビューを追加した（生データ本体は一切開かない）。

- **新規 `services/annotation_inspect.py`（Dash 非依存）**:
  - `has_compound_names(sub)` … バッジ用の安価チェック。TIMS/SCiLS は `<BASE>_feature_annotations.parquet`
    サイドカーの**存在**、DESI は正規化 `.txt` の化合物名行/named ヘッダで判定（本体 parquet は開かない）。
  - `inspect_annotations(sub)` … サイドカーを読み `status / 付与件数 / カバレッジ% / 化合物名の例` を返す。
    件数は `is_meaningful_annotation` ＋ `No DB hit`/数値のみ除外で算出。サイドカー欠如時は本体 parquet の
    フッタ schema メタ `b"peak_list"`（数 KB 読取）にフォールバック。DESI は `.txt` 3 行目/named csv・xlsx ヘッダ由来。
  - サイドカー探索は親フォルダを走査せず対象フォルダ＋直下サブフォルダのみ（別データセットの誤検出防止）。
- **UI**: サブプロジェクトカード（`project_callbacks.render_sub_project_cards`）に
  **化合物名バッジ**（✓ / なし）と**「化合物名」ボタン**を追加。ボタンで新規モーダル
  （`annotation_preview_modal.py`）を開き、「N / M feature に付与（xx%）」の要約＋
  `m/z / 表示名 / 化合物名 / アダクト / 組成式` のプレビュー表（`dash_table`）を表示。
- **配線**: `annotation_preview_callbacks.py`（新規、`main.py` に登録）。既存の登録済みデータにもそのまま効く
  （新規メタ永続化に非依存）。追加のみ・既存挙動は不変。
- 退行防止: 新規 `tests/test_annotation_inspect.py`（サイドカー件数/`No DB hit` 除外・フッタ `b"peak_list"`
  フォールバック・DESI named 判定・親走査なしの兄弟誤検出防止）。`pytest -m "not e2e"` 全 **509 passed**。

---

## 2026-07-15_ver43.0

### 機能: 化合物名表示を全表示面に統一（m/z 残存を解消）

化合物名付きデータ（SCiLS peak Name / 注釈CSV / MRM）を登録しても、MSI画像表示（Feature Plot）
やドロップダウン・各種プロット・共有ビュー等が **m/z 表記のまま**になっていた問題を解消。
原因は「feature→表示ラベル」の解決関数が無く、化合物名が 3〜4 系統のソース
（`feature_annotations` / `annotation_map` / `deg_data.annotation` / MRM）に分散し、各表示箇所が
思い思いに 1 系統だけを参照していたこと。**注釈の無いデータ（例: DESI）は素の m/z に安全劣化**（加算的）。

- **新規 `utils/annotation_label.py`（単一リゾルバ）**: `feature_display_label()`（Dash 非依存の純関数）と
  `label_from_active_state()`（アクティブ state から `annotation_map`/`feature_annotations` を読む薄いラッパ）。
  優先順位 `annotation_map` → `feature_annotations.compound` → `deg annotation` を
  `is_meaningful_annotation` で選別。style（`heading`＝`化合物名_m/z` / `paren`＝`m/z (化合物名)` /
  `compound` / `filename`＝ファイル名安全化 / `auto`）で各表示面の既存フォーマットを保つ。
- **新規 `deg_utils.backfill_annotations()`**: DEG レコードの空 `annotation` を `annotation_map` から補完
  （既存の意味ある注釈は上書きしない）。ロード時（`interactive_callbacks.load_stage_d_finish`）と
  オンザフライ DE（`interactive_de.py`）で 1 回適用するだけで、Volcano/Heatmap/クラスタTop5/
  マーカー表/PPTX が化合物名を一括参照する。
- **画面表示（主訴）**: イオン画像ホバー・Feature Plot 見出しの m/z フォールバック（`annotation_map` も参照）・
  PNG/一括ダウンロードのファイル名・初期/m/zフィルタ/ブックマーク/リストの各 Feature ドロップダウン・
  バイオリン図タイトル・選択サマリカードを化合物名対応に。ホバー/見出しは既存の
  「化合物名で表示（m/z ⇄ 化合物名）」トグルに追従。
- **共有 / Lite ビュー**: 共有バンドルへ `feature_annotations`/`annotation_map` を同梱し、共有 DEG 表に
  「化合物名」列を追加、共有 Feature 図（colorbar/ホバー）とドロップダウン、Lite クロスクラスタ
  ヒートマップの Y ラベルを化合物名対応に。
- **エクスポート**: PPTX の Feature スライドタイトル・クラスタ別マーカー表は補完済み `deg_data` 経由で
  自動的に化合物名を表示。データ書出し（CSV/Parquet）の列見出しは MetaboAnalyst 等の互換維持のため
  m/z のまま（化合物名ラベル出力は H&E オーバーレイの compound 単位が担当・不変）。
- **未対応（別対応）**: DESI R テンプレは注釈サイドカー/annotation 列を出力しないため、DESI データは
  上記各面で素の m/z のまま（安全劣化）。DESI への化合物ソース整備は今後の課題。
- 退行防止: 新規 `tests/test_annotation_label.py`（リゾルバ優先順位・各 style・ファイル名安全化・
  補完ロジック）＋`tests/test_marker_rows.py` 拡張。`pytest -m "not e2e"` 全 **495 passed**。

---

## 2026-07-09_ver42.2

### 修正: ④「続きを実行」(reduction再利用) の UMAP が DoHeatmap で停止するバグ

PreFlight の **④「続きを実行（reduction再利用）」**（`PIPELINE_STAGE=downstream_from_reduction`）で
UMAP 実行が途中停止する不具合を修正。TIMS ver6 テンプレ
（`App/Script/TIMS/260623_DBSCAN_With_cluster_ver6_no-png_slim.R`）のみ対象。

- **原因**: ④は①の軽量化 RDS（`DietSeurat` で `scale.data` を除去して保存）を再利用するが、
  `run_downstream_analysis` の `DoHeatmap` は `scale.data` を必須とする。①（`reduction_only`）は
  下流処理をスキップするため `scale.data` 不在が露呈せず、フル解析（`ScaleData` を毎回実行）でも
  問題にならないが、**④だけ**が `scale.data` 復元なしに `DoHeatmap` へ到達し
  `No requested features found in the scale.data layer for the Spatial assay` →
  `Execution halted` で解析全体が停止していた（harmony 経路の最初のヒートマップで発生）。
- **修正**: `DoHeatmap` 直前に `ScaleData(features=top_genes, assay="Spatial")` を実行して
  scale.data をその場で補完（**DESI v16 に既存の対策を TIMS ver6 へ移植**。DESI 側は L2348/2656/2891）。
  共通関数のため harmony/pca_uncorrected/rpca の3手法すべてに適用。ヒートマップは画像保存されない
  補助計算のため `tryCatch` で保護し、万一失敗しても解析全体は止めない。
- **防御（潜在バグ予防）**: `RunUMAP` の `dims` を選択 reduction の実次元数
  `ncol(Embeddings(obj, red_src))` で上限クランプ（直下 `FindNeighbors` の `.dims_clust` と対称化）。
  ①と④で dims 設定が食い違った場合の `RunUMAP` クラッシュを予防。
- 本リポジトリに R 無しのため実行検証はデプロイ環境で実施（①→④ 通し）。括弧バランスは静的確認済み。
- version 42.1→42.2。

---

## 2026-07-08_ver42.1

### 修正: 全「...」参照ボタンが無反応になるバグ（ファイルブラウザ復活）

フォルダ/ファイル選択の「...」ボタンが**すべて**クリックしても反応しない不具合を修正。

- **原因**: ファイルブラウザは1つの共有コールバック（`open_file_browser` / `apply_file_browser_selection`）で
  全ボタンを処理し、`_ALL_TARGET_IDS`（＝`_BROWSE_BUTTONS` から生成）に依存する。その中に、削除済み
  「手動結果フォルダ」機能の**孤児参照** `browse_result_folder` / `result_folder_manual`（どのレイアウトにも
  未生成）が残っていた。Dash は「Input/State/Output が1つでも DOM に存在しない共有コールバックは実行しない」ため、
  この孤児1つで両コールバックが停止し、**全参照ボタンが道連れで無反応**になっていた
  （コンソールに `nonexistent object ... browse_result_folder` エラー）。
- **修正**: `callbacks/file_handlers.py` の `_BROWSE_BUTTONS` と `_STORE_TARGETS` から孤児参照を削除（2箇所）。
  他ファイルからの参照は無く副作用なし。全「...」が復活する。
- `selected_samples` 系のコンソール警告は、データフォルダ選択時に動的生成される部品の設計上のもので実害なし（対象外）。
- version 42.0→42.1。

---

## 2026-07-08_ver42.0

### 追加: ChatGPT 連携 — インタラクティブ Export のオンデマンド生成 (`/api/gpt/*`) — フェーズ2

フェーズ1（読み取り＋保存済みエクスポート取得）に続き、**インタラクティブ解析の Export
（UMAP_cluster：元データ＋右端に手法別クラスタ列・領域名列）を ChatGPT からその場で生成して
ダウンロード**できるようにした。R 抽出が走り得る重い処理のため**非同期**（開始→ポーリング→取得）。

- **セッション非依存ドライバ** `build_interactive_export_for_project(...)`
  （`callbacks/interactive_data_export.py` 新規関数）:
  `_do_export` からブラウザ session（`_interactive_data`）依存を取り除いた版。全手法のクラスタは
  `_build_all_method_lookups(current_method=None)` で**ディスクから**構築する
  （current_method=None のとき同関数は `_interactive_data` を一切参照しない）。ROI(領域名) は
  primary RDS（既定 Harmony）の plot_data ＋ `hne_overlay_state.json` から割当。既存の
  `_export_tims` / `_export_desi`（純粋関数）をそのまま再利用（＝画面出力と同一結果）。
- **非同期エンドポイント**（`services/gpt_api.py`）:
  - `POST /api/gpt/projects/{pid}/sub/{sid}/exports/interactive?format=parquet|csv|xlsx&methods=...`
    → 作業スレッドを起動し `job_id` と `status_url` を返す。同時実行は `_GPT_EXPORT_SEM`（既定2）で制限。
  - `GET /api/gpt/exports/jobs/{job_id}` → 進捗（running/pct）と、完了時に `download_url`。
  - `GET /api/gpt/exports/jobs/{job_id}/file` → `send_file` でストリーム配信。
  - ジョブは `services/export_progress`（ver40.0）を流用。生成物は `GPT_EXPORT_TMP_DIR` に保存し、
    レジストリが掃除で消えても**ファイルから解決**（`<job_id>__*` をグロブ）。`job_id` は
    `valid_job_id`（16進16-64桁）で検証してからグロブ/送出（パストラバーサル対策）。
- OpenAPI 仕様に 3 オペレーション（startInteractiveExport / getExportJob / downloadExportJob）を追記。
- **テスト** `tests/test_gpt_api.py` に `valid_job_id` とフェーズ2 OpenAPI の検証を追加。
- ChatGPT 実接続には**独自ドメイン＋正規TLS**が別途必要（運用側作業。コード実装とは独立）。version 41.0→42.0。

---

## 2026-07-08_ver41.0

### 追加: ChatGPT 連携用の読み取り専用 API「受付窓口」(`/api/gpt/*`) — フェーズ1

このアプリを ChatGPT（Custom GPT の Action）から使い、**データの検索・抽出・保存済み
エクスポート取得**を自然言語で行えるようにするための「受付窓口」を新設。前アプリのように別サービス
（Supabase/Render）を立てず、**既存 Flask に読み取り専用ルートを追加**して同一プロセスの
データ関数を再利用する（画面と同じ数値を返す）。

- **新規** `services/gpt_api.py`（`register_gpt_api(server)`）:
  - **認証**: `/api/gpt/*` は独自の合言葉 **`X-API-Key`** で保護（`config.GPT_API_KEY`）。
    照合は `hmac.compare_digest`（定数時間比較）。**鍵未設定なら 503 で窓口を閉じる**（fail-closed）。
    判定は flask 非依存の純関数 `key_decision(path, provided, configured)` に分離（単体テスト可）。
    `openapi.json` / `health` のみ鍵不要（ChatGPT の Action 設定用の契約・死活）。
  - **読み取り専用 JSON**: `projects` / `projects/{id}`（詳細＋サブ）/ サブ単位の
    `clusters`（ウォーム抽出キャッシュのみ）/ `markers`（`deg_utils.load_deg_results`）/
    `compounds`（feature アノテーション検索：名前・m/z±tol・脂質クラス）/ `outputs`（出力画像一覧）。
  - **R は起動しない**: 抽出は `SeuratBridge.get_cache_dir` + `_is_cached` で
    **ウォームキャッシュがある場合のみ** CSV/JSON を直接読む（未生成なら「アプリで開くと取得可」と返す）。
  - **保存済み MetaboAnalyst エクスポート取得**: `exports` で
    `<RDS隣>/metaboanalyst_exports/*.zip|*.csv` を列挙し、`download/<token>` で
    `send_file` ストリーム配信（ver40.1 の方式を踏襲）。トークンは (project, sub, kind, name) のみを
    載せ、配信時に列挙し直して一致検証するためパストラバーサルは起きない。
  - **OpenAPI 3.1 仕様** を `openapi.json` で提供（`servers` は実ホストを反映、鍵は仕様書に出さない）。
- `main.py`: `register_auth(server)` の直後に `register_gpt_api(server)` を登録。
- `auth_middleware.py`: `/api/gpt/` をログインゲートの bypass に追加（独自の X-API-Key で守るため）。
- `config.py`: `GPT_API_KEY`（未設定で窓口クローズ）と `GPT_EXPORT_TMP_DIR`（フェーズ2用）を追加。
  `docker-compose.yml` / `.env.docker` に `GPT_API_KEY` を追記。
- **テスト** `tests/test_gpt_api.py`（16件、Dash/Flask 非依存）: 鍵判定・トークン往復・化合物検索・
  マーカー整形・一覧整形・OpenAPI 形。
- 数値・強度（intensity）のオンデマンド算出と**インタラクティブ Export のオンデマンド生成**は
  重い処理のため**フェーズ2**（次リリース）で追加予定。ChatGPT 実接続には**独自ドメイン＋正規TLS**が別途必要
  （運用側作業。コード実装とは独立）。version 40.1→41.0。

---

## 2026-07-06_ver40.1

### 修正: データ出力を「本来の軽さ」に（ベクトル化＋ストリーム配信でタブ落ち解消）

インタラクティブ「📥 データ出力」で大容量時に **Chrome タブが落ちる**問題を修正。出力は本来
「入力 parquet ＋ 右に2列（UMAPクラスタ・領域名）」なので軽いはずだが、実装が重くしていた。

- **A. 変形をベクトル化**（`services/export_transform.py` 新規 ＋ `_export_tims`）:
  `df.iterrows()`（全スポット×全 m/z 列を1行ずつ Series 箱詰め＝O(行×列)）を撤廃。
  x/y/annotation の**3列だけ**をベクトル処理し、キー `(sample, round(x,4), round(y,4))` を作って
  `Series.map(lookup)` で2列付与（丸めは lookup と同一の Python round でキー完全一致）。数分→数秒級・低メモリに。
- **B. 出力形式の健全化**: xlsx は列数 > 16,384（Excel 上限）で明確なエラーを返し CSV/Parquet を案内。
- **C. 配信をストリーム化**（タブ落ち根絶）: `dcc.send_bytes`（base64 でブラウザに全載せ）を廃止。
  生成バイト列を一時ファイル（`DATA_EXPORT_TMP_DIR`）に保存し、**Flask `send_file` ルート
  `/api/data_export/<job_id>`** でストリーム配信。完了時は DL URL を配信して clientside 自動DL＋明示リンク。
- `services/export_progress.py`: `finish_job` にファイルパス/名を保持、`sweep_old_files` で古い一時ファイル掃除。
  `config.py`: `DATA_EXPORT_TMP_DIR`（既定 `SEURAT_CACHE_DIR/_data_exports`、env 上書き可）。
- 出力ファイルの内容・形式は不変（ベクトル版が iterrows 版と同一結果であることをテスト）。
- テスト追加 `tests/test_export_transform.py`（3件）＋ `test_data_export_progress.py` 拡張。version 40.0→40.1。

---

## 2026-07-06_ver40.0

### インタラクティブ「データ出力」の進捗を実 % 表示に

「📥 データ出力 (UMAP cluster)」の進捗バーが不定表示（animated）だったのを、**実際の % 進捗**に変更。
手法クラスタ準備（手法ごと）→ ROI 割当 → ファイル書き込み（ファイルごと）の各段階で 0→100% と
ラベル（例「書き込み中… 3/8 (Section_A)」）が進む。DESI/TIMS 両対応。

- **方式**: サーバは単一プロセス・マルチスレッドで、本出力はセッションのライブ状態
  （`_interactive_data` の plot_data＝flip/rotation・クラスタ改名を含む）を参照するため
  fork型の background callback（`set_progress`）は使えない。代わりに
  **インプロセス作業スレッド ＋ ジョブレジストリ ＋ `dcc.Interval` ポーリング**で実装。
- **新規** `services/export_progress.py`: Dash 非依存のスレッド安全なジョブレジストリ
  （new/update/finish/fail/get/pop。% は 0-99 クランプ＆単調増加）。単体テスト可。
- `callbacks/interactive_data_export.py`: `_do_export` / `_build_all_method_lookups` /
  `_export_desi` / `_export_tims` に `progress_cb(pct,label)` を配線。ボタン押下で
  `contextvars.copy_context()` 付き作業スレッドを起動（active key を維持）、Interval が
  レジストリをポーリングしてバー更新、完了でダウンロード配信・ボタン復帰。
- `layouts/interactive_tab.py`: `dcc.Interval(data_export_poll)` ＋ `dcc.Store(data_export_job)` を追加
  （旧 `data_export_trigger` は廃止）。
- 出力ファイル・形式（DESI xlsx / TIMS xlsx/csv/parquet）は不変。UX のみ変更。MetaboAnalyst ④出力は対象外。
- テスト `tests/test_data_export_progress.py`（5件）。version 39.0→40.0。

---

## 2026-07-06_ver39.0

### 新機能: MetaboAnalyst エンリッチメント(QEA)へ「そのまま投入」できる濃度表を一括出力

④出力（H&E ROI×クラスタ平均強度）を、MetaboAnalyst の Quantitative Enrichment Analysis (QEA) に
ドラッグ&ドロップで投入できる **Sample+Class 濃度表 CSV** へ変換する機能を追加。

- **UI**（`layouts/hne_overlay_tab.py`）: 「エンリッチメント(QEA)用も出力」チェックを追加（化合物単位時のみ有効）。
- **新規サービス**（`services/metaboanalyst_qea.py`）: 既存の強度行列（行=`{stage}_{anatomy}_cluster{n}`）を分解し、
  各手法フォルダへ以下を生成:
  - `exploratory_QEA_stage_{raw,zeroAsNA}.csv` … Class=発生ステージ（最優先）
  - `exploratory_QEA_anatomy_{raw,zeroAsNA}.csv` … Class=臓器（原ラベル）
  - `exploratory_QEA_cluster_all_/ge10_{raw,zeroAsNA}.csv` … Class=UMAPクラスタ（小clusterは行数≥10で除外）
  - `sample_metadata.csv`（Sample↔原Group・stage/anatomy/cluster）、`compound_name_map.csv`、`README.txt`
  - 各表は 1列目=Sample(S0001…英数字ID)・2列目=Class・以降=化合物。0保持版と 0→NA 版の両方。
- **RefMet 保守正規化**: 脂質名（`PG 32:1` 等）は既に RefMet/LIPID MAPS shorthand に近いため、空白/句読点の
  正規化のみ行い（衝突時は原文保持）、照合は MetaboAnalyst の smart-match に委ねる。原文は `compound_name_map.csv` に保持。
- **探索的である旨を明記**: 各行は擬似バルク（1個体運用の切片×臓器×クラスタ平均）で、同一 Class 内反復は
  空間的擬似反復。README とファイル名接頭 `exploratory_` に明記（`run_findmarkers.R` の方針と一致）。
- 出力キャッシュキー（`_export_cache_key`）に QEA フラグを追加。anatomy の意味的標準化（Brain/CNS 等）は
  生物学的対応表が必要なため原ラベルで出力（対応表提供時に標準化版を追加可能）。
- version 38.1→39.0。

---

## 2026-07-06_ver38.1

### 修正: 強度/発現の下流を常に「測定アッセイ(Spatial)」から読む（RPCA の補正値を混入させない）

MetaboAnalyst 出力 `intensity_matrix_compound.csv`（RPCA/線形化）に**負値が約2.64%**含まれる不具合を修正。
原因は、旧 RPCA の RDS が Seurat v4 `IntegrateData` の **`integrated` アッセイ（バッチ補正後の再構成値）を
`DefaultAssay` として保持**しており、強度を読む処理が `DefaultAssay` の `data` 層を読んでいたこと。
`integrated` は測定強度ではなく補正値で0未満を取りうるため（`expm1` 下限 -1 → 実測 min ≈ -0.9187）、
線形化で負値が表面化していた。統合手法(RPCA/Harmony/PCA)の正当な成果物は**次元削減（UMAP/クラスタ）だけ**であり、
強度は統合手法に依存させず常に測定アッセイから読むべき、という方針で是正。

- **共通ヘルパー追加**（`Script/helpers/rds_io.R`）: `pick_measurement_assay()` ＝ 補正アッセイ
  (`integrated`/`SCT`) を避け **`Spatial` を優先**。明示 `--assay` 指定時はそれを尊重（後方互換）。
- **強度/発現を読む R ヘルパー4本を測定アッセイ固定に**:
  - `export_region_cluster_means.R`（MetaboAnalyst 出力＝本丸）。来歴 `ASSAY_USED=` を stdout に出力。
  - `extract_seurat_data.R`（`expression_matrix.parquet` / `features_list` → Feature plot・m/z キャリブ・共発現・出力フォールバック）。
  - `extract_features.R`（単一 feature 抽出）。
  - `run_findmarkers.R`（オンザフライ DE の `avg_log2FC`/`pct`）。
  - **UMAP 座標・クラスタは `reduction`/`Idents` 由来のため不変**（見た目は統合結果のまま、量だけ測定値）。
- **Python 側**（`services/seurat_bridge.py` / `callbacks/hne_overlay_callbacks.py`）:
  `ASSAY_USED` を回収し `df.attrs["assay_used"]` に格納、完了メッセージに「強度アッセイ: Spatial（測定値）」を明記。
  出力キャッシュキー `_export_cache_key` に版ソルト `assaysrc=measured_v1` を追加し、**旧・負値 ZIP を再利用しない**ように。
- UI 文言（`layouts/hne_overlay_tab.py`）に「強度は測定アッセイ(Spatial)から算出／RPCA/Harmony/PCA はクラスタ計算のみ」を明記。
- 効果: 負値 → 0%。integrated はアンカー ≤500 特徴のみのため、出力の化合物列が全 m/z に増える場合がある（測定された全化合物＝正）。
  Harmony/PCA/単一sample/最新 RPCA(v5 IntegrateLayers) は元から `DefaultAssay=Spatial` のため挙動不変。
- version 38.0→38.1。

---

## 2026-07-03_ver38.0

### エクスポート時の統合手法（UMAP）選択

データ出力と MetaboAnalyst 出力で、使用する統合手法（Harmony / RPCA / PCA）を選べるようにした。

- **データ出力**（`callbacks/interactive_data_export.py` / `layouts/interactive_tab.py`）:
  従来は常に全手法のクラスタ列を出力していたが、**手法チェックリスト（既定=全選択）**で絞れるように。
  `_build_all_method_lookups` に `selected_methods` を追加。あわせて**派生 PCA（未補正）の生成漏れを修正**
  （`derive_uncorrected_pca` の遅延生成を追加。従来は RDS 未生成で無言スキップ）。
- **MetaboAnalyst 出力**（`callbacks/hne_overlay_callbacks.py` / `layouts/hne_overlay_tab.py`）:
  従来は読込中の1手法のみだったが、**手法チェックリスト（複数選択・既定=全）**を追加。選択した各手法を
  **ZIP 内のサブフォルダ（手法名/）**に出力（`{手法}/intensity_matrix_*.csv` ＋ `{手法}/feature_map.csv`）。
  強度（data 層）と ROI（空間座標）は手法非依存のため、**ROI は共通、クラスタのみ手法ごとに差し替え**
  （`extract_data(method_rds)` で各手法の Cluster を取得）。派生 PCA は Harmony から遅延生成。
  キャッシュキー・ステータスに選択手法を反映。
- 既存の PPTX 手法セレクタ（ver34.0）と同じ `interactive_rds_map` 駆動パターンを流用。
- version 37.0→38.0。

---

## 2026-07-02_ver37.0

### MetaboAnalyst エクスポート: 同名化合物の統合（代表イオン=最大強度）

ver36.0 では別アダクトの同名化合物を別 feature（m/z）として残していたが、要望により
**同一化合物の m/z を1列に統合**する「集約単位」を追加した。

- **集約単位セレクタ**（`layouts/hne_overlay_tab.py` / `callbacks/hne_overlay_callbacks.py`）:
  `化合物（既定）` / `m/z`。化合物選択時は同一 `compound` の m/z 列を
  **代表イオン（全群平均が最大の m/z）** の値で1列に統合し `intensity_matrix_compound.csv` を出力。
  未注釈（No DB hit）や単独 compound は m/z のまま独立列で残す。
- **トレーサビリティ**: `feature_map.csv` に各 m/z の `group_key`（統合先）/`is_representative`（代表か）/
  `n_in_group`（統合数）を付与し、どの m/z を代表に選んだか明記。
- 純ロジック `merge_features_by_compound`（`services/hne_overlay.py`、`repr_max`/`sum`/`mean` 対応）を
  新設・単体テスト追加。ZIP 名・キャッシュキー・ステータスに集約単位を反映。
- 注: 同位体（M+1 等）・異性体は annotation 上区別できないため、代表イオン方式で二重計上を回避する。
  MetaboAnalyst には化合物名で一意な濃度表として直接投入できる。
- version 36.0→37.0。

---

## 2026-07-02_ver36.0

### MetaboAnalyst 向けエクスポート改善（強度の線形化 ＋ feature_id=m/z 化・ZIP 2ファイル）

H&E ROI×クラスタの MetaboAnalyst 用出力を下流解析に適した形へ改善した。

- **① 二重正規化の回避（強度セレクタ・既定=線形化）**（`layouts/hne_overlay_tab.py` /
  `callbacks/hne_overlay_callbacks.py` / `services/seurat_bridge.py` / `Script/helpers/export_region_cluster_means.R`）:
  現状の強度は Seurat `data` 層＝log 正規化後で、MetaboAnalyst の log/スケーリングと二重変換になる。
  新セレクタ **`線形化(非log・既定) / 生counts / 現状(log)`** を追加。`linear` は `@misc$preprocessing_method` に応じ
  **spot 単位で逆変換**（log1p→`expm1`／sqrt→2乗／none→恒等、`f(0)=0` で sparse 維持）してから群平均。
- **② 化合物名の重複解消（master は m/z=feature_id）**: 別 m/z（アダクト/同位体/候補）が同一化合物名に潰れて
  列名が重複していた。master を **m/z のまま**出し、名前・注釈は別表に分離。
- **出力を ZIP（2ファイル）化**（`callbacks/hne_overlay_callbacks.py`）:
  `intensity_matrix_mz.csv`（行=Group、列=m/z 一意、値=repr 群平均）＋
  `feature_map.csv`（m/z→compound/display_name/adduct/formula/ppm/lipid_class/database）。
  ファイル名・ステータスに強度種別と preprocessing_method を明記。キャッシュキーに repr を含める。
- 純ロジックを `utils`/`services` に集約（`services/hne_overlay.py` に `linearize_expression` /
  `build_feature_map` を新設・単体テスト追加、`build_region_cluster_export` に `intensity_repr`）。
  `services/hne_persistence.py` に ZIP バイト保存 `save_metaboanalyst_bytes` を追加。
- 注: データは N=1（各ステージ＝切片1枚・生物学的反復なし）のため、反復・統計・記述メタは対象外。
- version 35.0→36.0。

---

## 2026-07-01_ver35.0

### PPTX エクスポート 4 件修正（画像反転 / 画像の個別配置 / PCA 未出力 / クラスタ毎 marker 表）

App 表示と出力 PPT の不一致・使い勝手・出力漏れを解消した。

- **① Spatial 画像の上下反転を修正**（`callbacks/interactive_pptx.py`）: クラスタ毎スライドの
  Spatial は `raw_y=-SpatialY` のラスター heatmap（`yc` 昇順）に `autorange="reversed"` を重ねて
  **App と上下逆**になっていた。個別パネル図を **非反転軸**（データ座標 heatmap）で描画し、App
  （`_create_single_spatial_fig`）と向きを一致させた。
- **② PPT 画像の個別オブジェクト化**（同上）: クラスタ毎「UMAP & Spatial」を結合 1 枚 PNG から、
  **各サンプルの UMAP(上段)/Spatial(下段) を個別画像**として 2×N グリッド配置に変更。
  PowerPoint 上で各画像を移動/リサイズ可能。新規 `_build_cluster_umap_panel_fig` /
  `_build_cluster_spatial_panel_fig`（ラスター優先・散布フォールバック、TIC 濃淡背景＋highlight）。
  旧 `_build_cluster_slide_combined_fig` は撤去。高速化（ラスター）は維持。
- **③ PCA が選択されても出力されない不具合を修正**（`callbacks/interactive_pptx.py`）: 派生 PCA
  （未補正）は専用 RDS がディスク未生成のことがあり、エクスポートの Phase-1 が
  `Path.exists()` で無言スキップしていた。UI 読込（`load_stage_b_extract`）と同様に
  抽出前に `_bridge.derive_uncorrected_pca(Harmony, PCA)` で遅延生成するようにし、
  スキップした手法は最終ステータスに明記。
- **④ marker 一覧を per-cluster 表に変更**（同上 + `utils/deg_utils.build_marker_rows` 再利用）:
  DEG 非選択時の末尾集約表を廃止し、**各クラスタの「UMAP & Spatial」の次スライド**に
  そのクラスタの marker 表（クラスタ/m/z/化合物名/方向/log2FC/調整p値）を出力。表描画は
  再利用ヘルパー `_add_marker_table_slide` に集約。
- 進捗計算を per-cluster=3 スライド（A + [DEG or marker 表] + Heatmap）に統一。
- version 34.1→35.0。

---

## 2026-07-01_ver34.1

### 結合図 Spatial の TIC 濃淡背景を復活（高速化で簡略化した分の復元）

ラスター化（ver33.1）で結合図(UMAP+Spatial)の Spatial パネル背景を「TotalCount(TIC) の
グレー濃淡」→「薄グレー一色」に簡略化していたが、要望により **TIC のグレー濃淡（階調表現）
背景を復活**。

- `callbacks/interactive_pptx.py` `_build_cluster_slide_combined_fig` の Spatial ラスター経路を、
  単一の離散 Heatmap から **2 層 Heatmap** に変更:
  背景＝TIC(TotalCount) の `Greys` 濃淡（無い切片は薄グレー一色にフォールバック）、
  前景＝highlight クラスタ単色をその上に重ねる。空セルは透明。
- `grid_index` / `heatmap_trace`（`utils/raster.py`）を再利用。ラスター描画のため高速性は維持。
- feature プロットの TIC 背景は今回対象外（従来どおり発現量のみ）。
- version 34.0→34.1。

---

## 2026-07-01_ver34.0

### PPTX エクスポートの出力選択機能（手法の複数選択 / DEG 任意＋marker 集約表）

用途に応じてエクスポート内容を選べるようにした。

- **① 出力手法の複数選択**（`layouts/interactive_tab.py` / `callbacks/interactive_pptx.py`）:
  `export_method_selector` を `dbc.RadioItems`→`dbc.Checklist` に変更し「Both」を廃止。
  `update_export_method_options` は実在手法（rds_map）から生成し既定で全チェック。
  `cb_export_report` は選択リストを受け、0 件なら「手法を1つ以上選択」を表示。
- **② 含める図の選択**（同上 + `_build_pptx`）: **UMAP+Spatial と Heatmap は常設**、
  **DEG（Volcano＋Feature）は任意**（`export_include_deg`、既定 OFF）。DEG を含めない場合は、
  DEG に出すはずだった **m/z・化合物名・クラスタ・up/down・log2FC・調整p値**を
  **全クラスタ集約表**（行数に応じ自動改ページ）で出力する。
- **表データの純ロジック**は `utils/deg_utils.build_marker_rows` に集約（描画非依存・単体テスト可）。
  化合物名は annotation→MRM 近傍一致→空欄でフォールバック。表描画は
  `_build_marker_table_slides`（既存の add_table パターン流用）。
- 既定 DEG OFF により、最も重い DEG 描画を通常はスキップでき、エクスポートがさらに軽く・速くなる。
- テスト: `tests/test_marker_rows.py` を追加。
- version 33.2→34.0（機能追加のためメジャー +1.0）。

---

## 2026-07-01_ver33.2

### PPTX DEG(feature)描画の逐次化でハング/低速/プロセスリークを解消

ver33.1 のラスター化で UMAP/Spatial スライドは高速化できたが、実機 heartbeat で **DEG スライド
だけ 1 枚 5〜38 分**かかり完走できず（step 25/74 で watchdog 発火）、失敗後に kaleido プロセスが
多数残留していた。原因は DEG の feature/volcano を **4 並列 RenderQueue** で描画しており、並列
kaleido/Chromium が競合して 1 枚ごとにハング（→per-render タイムアウトでスキップ）＋回収漏れ
だったこと（結合図は共有単一プールで正常・高速だった）。

- **feature/volcano を逐次・単一プールに統一**（`callbacks/interactive_pptx.py` の `_build_pptx`）:
  `RenderQueue(max_workers=4)` を撤去し、結合図と同じ `render_png`（タイムアウト付き単一プール）で
  1 枚ずつ描画。ラスター化済みで 1 枚 1〜2 秒のため並列は不要で、競合ハング・欠落・リークを解消。
- **kaleido の確実な回収**（`utils/pptx_helpers.py` `shutdown_shared_queue`）: 残存する kaleido/
  Chromium プロセスを psutil で一掃するバックストップを追加。
- per-render タイムアウト既定を 120s→60s に短縮（`PPTX_RENDER_TIMEOUT_SEC`）。
- テスト: `tests/test_pptx_hang_fixes.py` に kaleido 一掃バックストップの単体テストを追加。
- version 33.1→33.2。

---

## 2026-07-01_ver33.1

### PPTX エクスポートのハング修正 + ラスター描画による高速化

PPTX レポート出力が特定の 1 枚の画像描画で無限待機し（6 時間以上進捗なし）、ProcessPool と
ヘッドレス Chromium(kaleido) が回収されずプロセスが増殖する不具合を修正。さらに、ハング回避で
SVG 散布へ切替えた結果生じた「大量ピクセルの描画が遅く完走できない」問題を、ラスター描画で解消。

- **ハング根絶**（`utils/pptx_helpers.py` / `callbacks/interactive_pptx.py`）:
  kaleido 描画に 1 枚ごとのタイムアウト（`PPTX_RENDER_TIMEOUT_SEC`, 既定 120s）と全体 watchdog
  （`PPTX_EXPORT_TIMEOUT_SEC`, 既定 45 分）を導入。タイムアウトした図はスキップし、worker と
  Chromium をプロセスツリーごと kill してリークを防止。WebGL(scattergl)→SVG 変換で SwiftShader
  由来の無言ハングを回避。スライドごとのハートビートログを追加し進捗を可視化。
- **ラスター描画で高速化**（新規 `utils/raster.py`）: PPTX 専用の feature/spatial/UMAP 図を
  `go.Heatmap`（データ座標つき）に置換し、点数に依存しない一定コストで描画。規則グリッドは
  binning、UMAP は 2D ヒストグラムで集約。任意角回転など格子化できない場合は従来の散布へ自動
  フォールバック。`PPTX_RASTER=0` で無効化可。対話 UI 側の図は WebGL のまま変更なし。
- **Docker**: ヘッドレス Chromium のランタイム依存を導入、`shm_size` を拡張。
- **既存バグ**: `services/seurat_bridge.py` の `_extract_mz_numeric` 誤 import を修正。
- **テスト**: `tests/test_pptx_hang_fixes.py`・`tests/test_raster.py` を追加。
- version 33.0→33.1。

---

## 2026-06-30_ver33.0

### 科学的信頼性の強化（MVP4: 計算・データ・記録の中核 + 注意書きの全面表示）

評価レポート（`App/docs/EVALUATION_AND_ROADMAP_2026-06.md`）の「科学的信頼性を優先」方針に基づく
最小実用アップグレード 4 項目の中核を実装。新規の重い依存は追加せず、既存資産を拡張する方針。
詳細・残課題は `App/docs/MVP4_IMPLEMENTATION_STATUS.md` を参照。

- **P1 アノテーション由来表示**：`services/annotation_sources.py` を新設。LC-MS/MS・METASPACE・MS-DIAL・
  汎用表の取込、m/z 許容での feature 対応づけ、由来優先（LC/MS>METASPACE>MS-DIAL>in-house>manual）、
  出典指標（FDR/MSM/score/RT）を**そのまま併記**する由来ラベル生成。`peak_annotation` に
  `source`/`source_metrics` を追加（SCiLS 由来=in-house）。アプリ側で良し悪しは判定しない。
- **P2 解析レシート**：`services/receipt.py` を新設。既存 `analysis_params.json` と R サイドカーを
  1 つに集約し、機械可読 `receipt.json` と人可読 `RECEIPT.md` を出力（Process Run Crate 形）。
  解析完了時に自動確定（`analysis_callbacks`）。`rds_io.R` に `write_receipt_sidecar()` を追加し、
  R 版・seed・クラスタ/正規化/補正設定・主要パッケージ版を記録。
- **P3 pixel↔sample 統計分離**：(c1) pixel-level 注意書きを **DESI テンプレ・オンザフライ DE・
  DEG 画面・共有画面・PPTX** に展開（これまで TIMS のみ）。出典は `services/caveats.py` に一元化。
  (c2) `services/spatial_stats.py`：feature ごとの Moran's I（numpy）。
  (c3) `services/pseudobulk.py`：ROI/サンプル集約 + Welch t による sample-level 比較（反復不足時は記述のみ）。
- **P4 UMAP/クラスタ安定性**：`services/stability.py`（ARI/Jaccard/silhouette/trustworthiness/旗）、
  `services/stability_runner.py` と `Script/helpers/stability_diagnostics.R`（複数 seed 再クラスタリング）。
- **テスト**：新規 7 モジュールに単体テスト計 ~60 件を追加（`pytest -m "not e2e"` 全 395 件パス）。
- version 32.1→33.0。

> 注: 計算・データ・記録の中核とテスト、注意書きの UI 表示は実装済み。アノテーション由来の
> 表示パネル/取込 UI、Moran's I・pseudobulk・安定性の結果パネル、レシート閲覧 UI など
> 一部の対話 UI 配線は、R + 稼働 Dash 環境での検証を伴う後続作業として残している。

---

## 2026-06-29_ver32.1

### 後片付け: 旧モジュールの物理削除 + 解析画面ヘルプの更新

ver32.0 で UI から外した「結果閲覧」「セッション履歴」関連の不活性コードを物理削除し、ヘルプ文書を最新化。

- **不活性モジュールの削除**：`layouts/results_tab.py`・`callbacks/results_callbacks.py`・`layouts/history_tab.py` を削除。
  `main.py` の `results_callbacks` 登録 import を除去。`services/session_manager.py` からセッション保存/読込/一覧/削除の
  4 関数を削除（`save_last_settings`/`load_last_settings` は他機能で使用中のため存続）。
- **ヘルプ更新（`templates/help/analysis.html`）**：「結果閲覧」「セッション履歴」の節・目次・タブ一覧・タブ別 URL 表
  （`/app/results`・`/app/history`）・UI モック SVG・各種参照を撤去し、3 タブ構成（解析設定 / インタラクティブ解析 /
  解剖×クラスタ(H&E)）へ更新。節番号を振り直し。
- **ヘルプに不足機能を追記**：スポット透明度による TIC 透過 / 組織像モノクロ表示 / 組織像オーバーレイの Spatial 統合、
  クリック可能な共有凡例、横並び→行数指定、UMAP/Spatial の初期オープン、解剖×クラスタ(H&E)タブ、を追記。
  バージョン履歴表に ver28.0〜32.0 の行を追加。
- version 32.0→32.1。

---

## 2026-06-29_ver32.0

### 機能削除: 「結果閲覧」タブと「セッション履歴」タブを撤去

トップナビゲーションを簡素化。「結果閲覧」「セッション履歴」の 2 タブと、それらへの入口・関連機能を削除した
（サブプロジェクト/プリセット/インタラクティブ解析で代替可能なため）。

- **結果閲覧**：`layouts/main_layout.py` から「結果閲覧」タブと import を削除。プロジェクト画面の
  「結果閲覧」アクションボタン（`callbacks/project_callbacks.py` の `sub_action_results` ボタン＋
  `sub_action_view_results` コールバック）を削除。URL ルーティング `/app/results`（`callbacks/tab_url_routing.py`）も撤去。
- **セッション履歴**：`main_layout.py` から「セッション履歴」タブと import を削除。サイドバーのセッション
  「💾 保存」「📂 読込」ボタン（`layouts/sidebar.py`）を削除し、見出しを「🗂 プリセット・バックアップ」に変更。
  セッション保存/読込/履歴テーブル/削除/履歴タブ切替の各コールバック（`callbacks/session_callbacks.py`）を削除。
  URL ルーティング `/app/history` も撤去。バックアップ一覧モーダルは存続。
- ナビゲーションは「解析設定 / インタラクティブ解析 / 解剖×クラスタ (H&E)」の 3 タブに集約。
- 留意：結果ギャラリー/履歴の旧モジュール（`results_tab.py`/`results_callbacks.py`/`history_tab.py`/
  `session_manager.py`）は描画・登録から外れて不活性化（共有ビューア等への影響回避のためファイル自体は残置）。
  ヘルプ（`templates/help/analysis.html`）の旧タブ記述は別途更新予定。
- version 31.1→32.0。

---

## 2026-06-29_ver31.1

### UI 改善: インタラクティブ解析で UMAP と Spatial Mapping を初期オープンに

インタラクティブ解析のアコーディオン（`interactive_accordion`）は従来 `start_collapsed=True` で全セクション初期閉じ
だったため、各セクションを毎回クリックして開く必要があった。**UMAP と Spatial Mapping の主要 2 ビューだけを初期
オープン**にし、クリック不要で即表示されるようにした。

- `layouts/interactive_tab.py`：`start_collapsed=True` を外し `active_item=["acc_umap", "acc_spatial"]` を指定
  （`always_open=True` のためリスト指定で複数オープン）。
- 残りのセクション（エクスポート/クラスタ情報/Feature Plot/DEG）は従来どおり初期閉じ＝開いた時だけ計算する遅延
  ロードを維持。特に重い Feature Plot（初回 expression_matrix 生成 20–60 秒）は初期オープンに含めないため、
  データロードは重くならない。
- コールバックは無改修（重い描画は既に `active_item` を見てガード済み。`active_item` を書き込む処理は無い）。
- version 31.0→31.1。

---

## 2026-06-29_ver31.0

### 機能追加: Spatial Mapping のスポット透明度で TIC 透過＋組織像モノクロ表示 / Feature リストを折りたたみ化

- **スポット透明度を通常タイル（組織像オーバーレイ OFF / 未登録）にも適用**：通常の Spatial Mapping タイルは
  既に TIC 背景（Greys）を描いているが、クラスタ色スポットが不透明で覆っていたため見えなかった。スポット透明度を
  クラスタ色スポットに適用し、**下げるほど背後の TIC が透けて見える**ように（`_create_single_spatial_fig` に
  `spot_opacity` 引数を追加。embed_legend / discrete / fallback / highlight の各クラスタ色トレースに反映。
  TIC 背景・選択ハイライトは不変）。フルスクリーンの Spatial も同じ透明度に追従。
- **スライダー既定値を 70 → 100（不透明）に変更**：通常表示の既定の見た目は現状どおり。下げた時だけ TIC/H&E が
  透ける。リセットも 100 に統一（`interactive_resets.reset_hne_overlay`）。
- **「組織像をモノクロ表示」トグルを追加**（`hne_overlay_mono`）：H&E オーバーレイ背景の組織像をグレースケール表示
  （`build_hne_overlay_fig(mono=...)`。輝度変換、キャッシュ配列は非破壊）。
- **Feature Plot の「Feature リスト / 共発現」を初期閉じの折りたたみプルダウン化**（`html.Details(open=False)`、
  見出しを `html.Summary` 化）。内部 id は不変のためコールバックは無改修。
- 0% も有効な透明度値として扱う（`or` ではなく `is not None` 判定）。`spot_opacity` の既定は 1.0 のため
  軽量ビューア / 共有 / PPTX 等の他の呼び出し元は従来どおり不透明。
- version 30.2→31.0。

---

## 2026-06-29_ver30.2

### UI 改善: UMAP / Spatial Mapping の「横並び」も行数指定に統一

ver30.1 で Feature Plot の「横並び」を行数指定に変えたが、UMAP と Spatial Mapping は別コントロールのため
「列」指定のままだった。3 つすべてを **行数指定**に統一（`N行` → 1 行あたり `ceil(タイル数 ÷ N)` 枚で折り返し、
最大 N 行。`自動` は従来どおり全タイル横一列）。

- `layouts/interactive_tab.py`：id `umap_columns_per_row`→`umap_rows_per_view`、
  `spatial_columns_per_row`→`spatial_rows_per_view`、ラベル「横並び」→「行数」、選択肢「自動／1行…8行」。
- `callbacks/interactive_umap.py`：`update_umap_per_sample` と `_build_umap_per_sample_graphs` /
  `_build_umap_facet_graphs`（サンプル別・クラスタ／選択グループ facet 共通）を行→列換算
  （`n_cols = ceil(タイル数 / 行数)`、`math` 使用）。`save_umap_display_settings` の保存キーを `rows_per_view` に。
- `callbacks/interactive_spatial.py`：`update_spatial_plots` を行→列換算に。`save_spatial_display_settings` の
  保存キーを `rows_per_view` に。
- `callbacks/interactive_fullscreen.py`：UMAP/Spatial のフルスクリーン 3 箇所も State id 改名＋行→列換算。
- `callbacks/lite_view_callbacks.py`：軽量ビューアの UMAP グリッドが `rows_per_view` を読み、`_calc_col_lg_width` を
  行数＋サンプル数から列幅を出す方式へ変更（Spatial 軽量ビューアは元々固定幅で変更なし）。
- 旧 `interactive_settings.json` の `columns_per_row`（列数）は読まれなくなり軽量ビューアは既定(自動)へフォールバック
  （列数値が行数として誤解釈されない安全な移行）。メイン解析タブのドロップダウンは元々復元されないため影響なし。
- version 30.1→30.2。

---

## 2026-06-29_ver30.1

### UI 改善: Feature Plot の「横並び」を行数指定に変更／「色反転」「log10表示」を撤去

- **横並びを「列数」→「行数」指定に変更**：Feature Plot のタイル配置コントロールを、1 行あたりの列数指定から
  **行数指定**へ変更（より直感的）。`N行` を選ぶと 1 行あたり `ceil(サンプル数 ÷ N)` 枚で折り返し、最大 N 行になる。
  「自動」は従来どおり全サンプルを横一列に並べる。
  - `layouts/interactive_tab.py`：id `feature_columns_per_row` → `feature_rows_per_view`、ラベル「横並び」→「行数」、
    選択肢を「自動／1行…8行」に変更。
  - `callbacks/interactive_deg.py`：`update_feature_plot` の入力・引数を行数(`rows`)に変更し、
    `n_cols = ceil(サンプル数 / 行数)` で列幅を算出（`math` を使用）。
- **「色反転」「log10 表示」トグルを撤去**：Feature Plot のカラースケール行から `feature_reverse_scale`（色反転）と
  `feature_log_scale`（log10 表示）の 2 スイッチを削除し、UI を簡素化。
  - `callbacks/interactive_deg.py`：該当入力・引数、log10 変換処理、`reversescale` 指定、未使用化した
    `log_transform_intensities` の import を削除。
  - `callbacks/interactive_resets.py`：`reset_feature_colorscale` の出力から 2 トグルを除き、配色・強度範囲のみ
    既定へ戻す（リセットボタンの説明も更新）。
- カラースケール選択・強度範囲・リセットは従来どおり存続。UMAP/Spatial Mapping の「横並び（列数）」は変更なし。
- version 30.0→30.1。

---

## 2026-06-29_ver30.0

### 機能追加: 組織像(H&E)オーバーレイを Spatial Mapping 本体に統合（スポット透明度で透過）

従来は Spatial Mapping の下にある独立グラフ（単一サンプル）でのみ H&E を背景表示できたが、これを
**Spatial Mapping 本体の各タイル背景に統合**。登録済みサンプルでは組織像を背景に重ね、
**「スポット透明度」を下げるとスポットが透過して組織像が見える**。

- `callbacks/interactive_hne_bg.py`：描画を純関数 `build_hne_overlay_fig(...)` に切り出し
  （`go.Image` 背景＋`msi_to_hne_px` で射影したクラスタ別スポット、透明度/exclude/灰色化(legend_hidden)反映、
  共有凡例連動のダミー凡例、デコード画像のキャッシュ）。未登録サンプルは `None` を返す。独立グラフ
  `hne_overlay_graph` と `update_hne_overlay` コールバックは廃止。
- `callbacks/interactive_spatial.py`：`update_spatial_plots` に `hne_overlay_show/opacity/marker_size` を
  入力追加。トグル ON かつ登録済みサンプルは `build_hne_overlay_fig` のタイル、それ以外は従来の
  `_create_single_spatial_fig`（MSI 空間）にフォールバック。`facet-tile`/共有凡例/一括保存は従来どおり。
- `layouts/interactive_tab.py`：独立 H&E グラフを削除し、コントロール（トグル/スポット透明度/サイズ/リセット/
  ステータス）を Spatial タイルの上へ移設。トグル文言を「組織像を背景に重ねる（登録済みサンプル）」に更新。
- 留意：オーバーレイ表示は H&E 登録フレーム（H&E の向き）で描画。spatial の回転/反転スライダーは非オーバーレイ時に有効。
  選択ブラッシングのハイライトとラベルのドラッグ位置はオーバーレイタイルでは未対応（クラスタ番号は重心から再計算）。
- version 29.1→30.0。

---

## 2026-06-29_ver29.1

### バグ修正: 凡例で非表示にしたクラスタの灰色背景を残す + 再解析を初期閉じプルダウンに

- **①（灰色背景の復活）**：ver29.0 で共有凡例クリックを「除去(exclude)」に連動させたため、クラスタを
  非表示にすると `df` からセルごと除外され、**背景の灰色スポット（_background_grey / _bg / _background_tic）も
  一緒に消えていた**。凡例は「色だけ消して灰色文脈は残す」のが本来の挙動（ネイティブ Plotly 凡例と同じ）なので、
  専用の **`legend_hidden` ストア**に切り替えた。
  - `callbacks/interactive_facet_legend.py`：4 コールバックの出力を `*_exclude_cluster.value` →
    `umap/spatial_legend_hidden_store.data` に変更（メイン/FS でモダリティ別に共有）。
  - ビルダ（`_build_umap_per_sample_graphs`/`_build_umap_facet_graphs`/`_create_single_spatial_fig`）に
    `legend_hidden` を追加し、**色付き trace のみ skip**（灰色背景トレースは不変）。`update_umap_per_sample`/
    `update_spatial_plots`/フルスクリーン各コールバックが新ストアを受けてビルダ＋凡例グラフへ伝播。
  - `exclude`（「除去するクラスタ」ドロップダウン＝「完全に除去」）は従来どおり独立（灰色も消す）。
  - 結果：凡例の単一クリック＝そのクラスタが**灰色化**（消滅しない）／ダブルクリック＝そのクラスタのみ色・他は灰色。
- **②**：「選択クラスタで再解析」を `html.Details(open=False)` で包み、**初期は閉じたプルダウン**に。
- version 29.0→29.1（バグ修正/UI 微修正）。

---

## 2026-06-29_ver29.0

### 機能追加: 共有クラスタ凡例をクリック可能化 + UMAP 選択ツールのプルダウン化 + 再解析ブロック移動

サンプル別/分割表示の操作性と UMAP セクションの整理。

- **A. 共有クラスタ凡例をクリック可能に**：ver28.0 で統一した上部の共有凡例を「凡例だけの Plotly グラフ」に
  変更し、Plotly ネイティブの **単一クリック＝当該クラスタを非表示 / ダブルクリック＝当該クラスタのみ表示**
  を全タイルに一括反映（従来の per-tile 凡例挙動を復活）。
  - `utils/display_helpers.py` に `cluster_legend_figure`（各クラスタを `meta`=id 付きダミートレース、
    `itemclick="toggle"`/`itemdoubleclick="toggleothers"`）を追加。`facet_block` に `legend_id`/`excluded`
    を追加し、`dcc.Graph` の凡例を描画。
  - 新規 `callbacks/interactive_facet_legend.py`：凡例クリック(`restyleData`)→ `figure` の `visible` を読み、
    非表示(legendonly)クラスタを当該ビューの `*_exclude_cluster.value` に反映（UMAP/Spatial × メイン/フルスクリーンの
    4 ビュー）。既存 exclude パイプラインが全タイル＋凡例を再構築し双方向同期。
- **① UMAP「選択ツール」をプルダウン格納**：ポリゴン選択・選択範囲の統計・選択グループを 1 つの
  折りたたみ（`html.Details`、既定閉）にまとめて整理（`layouts/interactive_tab.py`）。
- **② 「選択クラスタで再解析」を移動**：UMAP セクションから、データ読込部の「再アノテーション」直下へ移設。
  component id 不変で挙動は維持（ver27.1 の遷移修正も維持）。
- version 28.0→29.0。

---

## 2026-06-29_ver28.0

### 機能追加: サンプル別/分割表示を「共有凡例＋縦線区切り」に刷新 + UMAP軸矢印削除 + 出力見切れ防止

サンプル別/分割表示（UMAP・Spatial Mapping）の空間効率と見やすさを改善。

- **画面表示＝共有クラスタ凡例（上部に1つ）＋各図を縦線で区切り（枠なし）**。各図の繰り返し凡例と
  カード枠で生じていた余白を排除し、図そのものを大きく密に表示。
  - 共通部品 `utils/display_helpers.py` に `cluster_legend_row` / `facet_block` を追加。
  - `assets/styles.css` に `.facet-tiles` / `.facet-tile`（左罫線で区切り）/ `.facet-legend` を追加。
  - UMAP（`interactive_umap.py` の per-sample / Split View ビルダ）、Spatial（`interactive_spatial.py`
    の per-sample ループ）、フルスクリーン（`interactive_fullscreen.py` の UMAP/Spatial）をいずれも
    `facet_block` でラップし、表示用 figure は `showlegend=False`（上部共有凡例に集約）。
- **出力（一括保存②-A）は各図に凡例を残す**。ただし各図の凡例は **その図に存在しないクラスタを
  「空白スロット」**（透明マーカー＋空白名）にして位置を保持し、全図で番号の縦位置をそろえる。
- **UMAP 左下の軸矢印（UMAP1/UMAP2）を全 UMAP から削除**（`_add_umap_arrows` 呼び出しを撤去）。
  散布との重なりを解消。
- **PNG 出力の凡例見切れ防止**：`interactive_batch_save.py` の書き出し高さを凡例行数に応じて自動拡張
  （`height = max(base, rows*20 + 70)`）。combined は高さ差を中央寄せで吸収。出力は PNG のみ（変更なし）。
- version 27.1→28.0。

---

## 2026-06-29_ver27.1

### バグ修正: 「再解析フォームへ送る」が再解析フォームを表示するよう修正

インタラクティブ解析でクラスタの抽出/除外を選び「再解析フォームへ送る」
(`btn_send_to_reanalysis`) を押すと、設定タブで**再解析フォームではなく
DESI UMAP 解析の設定画面**が表示される不具合を修正。

- 原因：橋渡し `send_to_reanalysis`（`callbacks/interactive_reanalysis_bridge.py`）が
  対象クラスタ・モード・RDSフォルダの転記と設定タブ移動のみ行い、**解析手法セレクタ
  （`analysis_method` / `analysis_method_tims`）を設定していなかった**ため、既定の
  `desi_v8`(UMAP) のままとなり `toggle_settings_panels` が UMAP フォームを表示していた。
- 修正：橋渡しに `analysis_method` / `analysis_method_tims` の出力を追加し、読込済み結果の
  計測種別（`int_cal_ms_instrument` ＋ 既存ヘルパー `interactive_data_export._resolve_instrument`
  でパス規約から補正）に応じて DESI なら `desi_cluster_filter`、TIMS なら `tims_cluster_filter` を
  自動選択。設定タブで該当モダリティの**再解析フォームが表示**される。
- version 27.0→27.1（バグ修正・パッチ）。

---

## 2026-06-29_ver27.0

### 機能追加: UMAP ポリゴン選択へ統一 + ヘルプ可読性改善 + 説明書最新化

運用フィードバック 3 点に対応。①②は挙動不変の改善、③が機能追加のため版を +1.0。

- **③ UMAP の範囲選択をポリゴン形式に統一**（投げ縄/ボックスを廃止）。H&E オーバーレイと同じ
  「クリックで頂点を置く」方式に揃え、3 頂点以上で「確定」すると囲んだ内側のピクセルを選択する。
  - `callbacks/interactive_umap.py`：`_build_umap_integrated_fig` の `dragmode` を `select`→`pan`
    （クリック=頂点 / ドラッグ=パン / ホイール=ズーム）。末尾に空の下書きオーバーレイ trace
    （`_umap_poly_draft`）を常設。
  - `callbacks/interactive_loupe.py`：`capture_umap_selection`（selectedData 読取）を撤去し、
    `umap_polygon_draft`（クリック/取消/クリア）・`umap_polygon_overlay`（`Patch` で下書き描画）・
    `umap_polygon_draft_info`（状態表示）・`umap_polygon_commit`（`hne_overlay.points_in_polygon` で
    内包セルの CellID を `selected_cell_ids_store` へ）を追加。マージ表示時は `*_merged` 列で判定。
  - `callbacks/interactive_spatial.py`：`update_spatial_plots` の入力を
    `interactive_umap_plot.selectedData`→`selected_cell_ids_store.data` に変更（spatial ハイライトが
    ポリゴン選択に追従）。
  - `layouts/interactive_tab.py`：ポリゴン操作 UI（1点取消/クリア/確定+状態表示）と
    `umap_polygon_draft_store` を追加。UMAP の modebar から `select2d`/`lasso2d` を除去。
  - 選択統計（P1）・選択グループ（P3）・選択DE（P2）はこの選択をそのまま入力として再利用。
  - テスト `tests/test_umap_polygon_selection.py`（内包判定→CellID の純ロジック）、
    `tests/e2e/test_smoke.py` に新ボタン存在チェックを追加。
- **① ヘルプ⊕バッジ（ツールチップ）の可読性改善**：`assets/styles.css` に
  `.tooltip-inner { max-width:360px; text-align:left; white-space:pre-line; ... }` を追加し、
  Bootstrap 既定 200px の細い縦折返しを解消。`layouts/tooltips.py` の長文に意図的な改行（`\n`）を挿入。
- **② 説明書（`templates/help/analysis.html`）の最新化**：§6 に「選択グループ（ポリゴン選択・実務的な
  使用例）」「その場で実行する解析（選択統計/選択DE）」「Feature リスト+共発現」「H&E オーバーレイ/
  分割表示」を追記。TOC アンカー・機能一覧テーブル・バージョン履歴（ver5〜27.0）を更新。
- version 26.2→27.0。

---

## 2026-06-28_ver26.2

### 改善: 「分割基準」「選択グループ」にヘルプ(？)ツールチップを追加

UMAP タブの2か所に、既存の `help_badge`（青丸の「?」）と説明ツールチップを追加（挙動不変）。

- **分割基準 (サンプル別表示時)**：何ごとに UMAP を小図分割するか（サンプル/クラスタ/選択グループ）と
  軸共有の説明。
- **選択グループ**：選択範囲の保存/改名/削除/結合/CSV 入出力、および「現在の選択に読込」で
  選択統計・選択 DE の入力に再利用できる旨の説明。
- 変更: `layouts/interactive_tab.py`（ラベル/見出しに `help_badge`）、`layouts/tooltips.py`
  （`get_interactive_tooltips` に2件追加）。version 26.1→26.2。

---

## 2026-06-28_ver26.1

### 追加: Playwright 自動 E2E テスト基盤 (非機能改善 Inc.3, インフラ)

ブラウザ自動操作でアプリの回帰を検知する E2E テスト基盤を追加（アプリ挙動の変更なし）。
**Dash はコンポーネント `id` がそのまま安定セレクタ**（`#id`）なので testid 追加は不要。

- `App/tests/e2e/conftest.py`：`run_app.py` を試験ポートで起動し `/healthz` 緑を待つ
  `app_server` fixture と、`#analyst_name`+`#password` でログインしてアプリへ入る `page` fixture。
  playwright/Chromium が無い環境では skip。版ズレ時は `PLAYWRIGHT_BROWSERS_PATH` 配下の既設
  Chromium を `executable_path` で自動使用（`PW_CHROMIUM_PATH` で明示可）。
- `test_smoke.py`（**データ不要**）：起動→ログイン成功→**今回追加した主要UI（選択統計/選択グループ/
  選択DE/Featureリスト/共発現/H&E/Split View/リセット/Undo/検索picker/マーカー表）が DOM に存在**
  することを検証（id 欠落の回帰を自動検知）。本環境で実際に緑を確認。
- `test_interactive_data.py`（`@pytest.mark.requires_data`）：`E2E_RESULT_FOLDER` 指定時のみ
  実 RDS を読み込み、クラスタ表示・選択グループ保存を検証。未指定なら skip。
- `pyproject.toml`：`markers=[e2e, requires_data]`、optional-deps `e2e`（playwright/pytest-playwright）。
- 実行例：`pytest -m e2e App/tests/e2e/test_smoke.py` / `E2E_RESULT_FOLDER=... pytest -m requires_data`。
  version 26.0→26.1（インフラ・挙動不変）。

---

## 2026-06-28_ver26.0

### 機能追加: 検索駆動の Feature リスト作成 + エクスポート文言統一 (非機能改善 Inc.2)

Loupe 参考の“機能以外”改善 第2弾。既存挙動は不変（追加・文言整備）。

- **検索駆動の Feature リスト作成**：feature を**検索して複数選択 → リスト化**（Loupe の
  “探して追加”）。サーバ側検索は `interactive_deg.filter_features` と同型（`features_list` を
  キーワード絞り込み、選択済みは表示維持、上限500件）。実 feature 名を使うので共発現で確実に一致。
  既存の「絞り込み/ブックマーク/CSV」に**4つ目の作成手段**。
- **エクスポート文言の統一**：CSV 出力ボタンの表記を「CSV出力」に統一し、各ボタンに
  **書き出し対象を説明するツールチップ**を付与（マーカー表/選択DE/選択グループ/Feature リスト）。
- **変更**：`callbacks/interactive_feature_lists.py`（picker 検索 + 作成）,
  `layouts/interactive_tab.py`（picker + 文言/ツールチップ）。version 25.0→26.0。

---

## 2026-06-28_ver25.0

### 機能追加: 入力バリデーション + リセット/Undo (非機能改善 Inc.1)

Loupe 参考の“機能以外”の改善 第1弾。誤操作の低減と一貫性。既存挙動は不変（追加中心）。

- **範囲バリデーション + インライン警告**：数値入力が有効範囲外だと**入力欄を赤く**する
  （`dbc.Input.invalid`）。範囲は `app/utils/validation.py`（純関数 + 単体テスト14件）に一元管理。
  対象: Volcano の FC/p/Y上限・heatmap Top N・選択DE の FC/p・Feature 強度 min/max(%)。
  Loupe 既定（UMAP min_dist 0–1/既定0.1, n_neighbors≥2/既定15, perplexity≥1/既定30, PCA 10–100）も
  PARAM_BOUNDS に収録（将来の再解析UI用）。
- **リセット（“調整には必ずリセット”）**：Feature 配色（palette/log10/反転/強度範囲）・Volcano
  閾値（FC/p/Y）・H&E オーバーレイ（透明度/サイズ）に「リセット」を追加（既定へ復帰）。
- **Undo（削除の取り消し）**：選択グループ削除時に直前の削除分を退避し「削除を取り消す」で復元。
- **新規**：`utils/validation.py`, `callbacks/interactive_validation.py`,
  `callbacks/interactive_resets.py`, `tests/test_validation.py`。
  変更: `interactive_selection_groups.py`（削除退避）, `interactive_tab.py`（リセット/取消ボタン+Store）。
  version 24.0→25.0。

---

## 2026-06-28_ver24.0

### 機能追加: Split View / Feature リスト+共発現 / 再解析ブリッジ (Phase 5)

Loupe 参考の拡張3種。いずれも既存資産を流用し、既存挙動は不変。

- **Split View（任意カテゴリ分割）**：サンプル別表示に「分割基準」(サンプル/クラスタ/選択グループ)
  を追加。クラスタ・選択グループ分割は「全細胞を淡灰背景＋当該集団を色付け」+**全タイル軸共有**で
  synchronized small-multiples を実現（`_build_umap_facet_graphs`）。サンプル分割は従来通り。
- **Feature リスト + 2リスト共発現散布図**：複数 m/z を名前付きリストとして保存/改名/削除/CSV入出力。
  作成元は既存の m/z 絞り込み結果・ブックマーク・CSV取込（実 feature 名をそのまま使い堅牢）。
  リストA集約(x) vs リストB集約(y) を pixel 単位で散布し Cluster 色分け→右上ほど共局在。
  発現は parquet から複数列一括読込（`SeuratBridge.get_features_matrix`）。
- **選択クラスタで再解析（ブリッジ）**：**部分集合の再クラスタリングは既に本番機能**
  （`run_analysis` + `*_Cluster_Filter_ReUMAP.R`）。インタラクティブで残す/除くクラスタを選び、
  ボタンで設定タブの再解析フォーム（対象クラスタ/モード/RDSフォルダ）へ転記し設定タブへ移動。
  実証済みエンジンを再利用。任意 lasso 部分集合や UMAP パラメータのみ再描画は将来課題。
- **新規**：`services/feature_lists.py`（純CRUD+単体テスト11件）、`callbacks/interactive_feature_lists.py`、
  `callbacks/interactive_reanalysis_bridge.py`、`interactive_umap.py` に `_build_umap_facet_graphs`。
  version 23.0→24.0。

---

## 2026-06-28_ver23.0

### 機能追加: 選択グループ（名前付き永続選択） (Phase 3)

Loupe Browser の Groups/Filters 相当。UMAP の lasso/box 選択を**名前付きの永続
オブジェクト**として保存・改名・削除・結合し、CSV で入出力できる。さらに
「現在の選択に読込」で選択統計(P1)・アプリ内DE(P2) の入力として再利用できる。

- **永続化**：RDS 隣の `selection_groups_state.json`（`hne_persistence` /
  `label_persistence` と同型の FileLock + atomic write）。データロード時に自動復元。
- **CRUD**：現在の選択を名前付き保存／改名／削除／**結合（和集合）**。
- **CSV 入出力**：`CellID,Group` 形式でエクスポート、CSV からインポート
  （ヘッダの別名 cell_id/barcode・cluster/name に寛容）。
- **下流再利用**：「現在の選択に読込」で `selected_cell_ids_store` を上書き
  （P1 capture と同居のため `allow_duplicate`）。→ 選択統計が更新され、
  選択 DE の ident.1 として使える（保存グループ vs 全体 / vs 指定クラスタ）。
- **新規**：`app/services/selection_groups.py`（純 CRUD + 単体テスト 15 件）、
  `app/callbacks/interactive_selection_groups.py`。
- **今回見送り**：spatial/feature→UMAP の双方向「ハイライト」描画は `update_umap_plot`
  への描画分岐が必要で実画像検証が要るため次段に延期（選択の共有 Store 化までは完了）。
  version 22.0→23.0。

---

## 2026-06-28_ver22.0

### 機能追加: 登録済み組織像 (H&E) 背景オーバーレイ + スポット透明度 (Phase 4)

Spatial Mapping セクションに「組織像オーバーレイ」パネルを追加。既に実装済みの
H&E ランドマーク位置合わせ（`hne_overlay` / `hne_persistence`）を再利用し、
**登録済み H&E を背景に MSI クラスタのスポットを重ねて**表示できる。

- **アフィン射影（画像ワープなし）**：MSI スポット座標を、本番の領域割当
  （`regions_from_overlay`）と**同一規約のアフィンの逆**で H&E 画素座標へ射影し、
  ネイティブ H&E 画像（`go.Image`）の上にスポットを散布。画像ワープ不要で堅牢。
  回転/反転は領域割当と同じ `apply_rotation` を適用。
- **スポット透明度スライダー**（Loupe の「組織像に対するスポット不透明度」、既定 70%）
  とスポットサイズ、クラスタ色（既存 color_map）に対応。
- **位置合わせ品質表示**：`affine_residual`（RMS, MSI 単位）とランドマーク点数を表示。
- 単一サンプル表示（Spatial の「サンプル」を選択）。H&E 未登録のサンプルは
  「H&E オーバーレイ」タブでの登録を促すメッセージ。
- **新規**：`hne_overlay.msi_to_hne_px()`（純関数 + 単体テスト 5 件）、
  `app/callbacks/interactive_hne_bg.py`。ミクロンスケールバーは pixel-size メタデータが
  未整備のため今回は見送り。version 21.0→22.0。

---

## 2026-06-28_ver21.0

### 機能追加: アプリ内 on-the-fly 差次発現解析 (DE) (Phase 2)

Loupe Browser の中核機能を再現。事前計算済み DEG の閲覧だけでなく、ユーザーが
UMAP で選んだ任意の選択範囲・群について **その場で DE 検定を実行**できる。既存の
DEG（Volcano/Heatmap/マーカー表）は不変で、専用の「選択 DE」タブに結果を表示する。

- **比較モード（Loupe 準拠）**：
  - **Globally Distinguishing**：現在の選択 vs 残り全体。
  - **Locally Distinguishing**：現在の選択 vs 指定クラスタ(群)。
- **検定**：R `Seurat::FindMarkers`（Wilcoxon, `presto` 高速路）+ **BH 補正**（本体 pipeline と同一）。
  保存 RDS の `JoinLayers → data` layer を使用。1 対比 ≈ 30–60 秒（前景・dcc.Loading 表示）。
- **結果表示**：ソート可能 DataTable（avg_log2FC / p_val_adj / pct.1 / pct.2）+ Volcano（FC・p 閾値可変）+
  現在の並び替え/絞り込みを反映した **Top-N CSV 出力**。
- **キャッシュ**：(mode, CellID集合, パラメータ) のハッシュで cache_dir に保存し再実行は即返す
  （`FileLock` で多重実行を防止、`export_region_cluster_means` と同じ subprocess パターン）。
- **新規ファイル**：`App/Script/helpers/run_findmarkers.R`、`app/callbacks/interactive_de.py`。
  `SeuratBridge.run_differential_expression()` 追加、`selection_utils.cells_in_clusters()` 追加。
  version 20.0→21.0。

---

## 2026-06-28_ver20.0

### 機能追加: Loupe Browser 9 を参考にしたインタラクティブ解析の強化 (Phase 1)

10x Genomics Loupe Browser 9.1.0 の挙動解析（`App/docs/LOUPE_BENCHMARK.md`）をもとに、
UMAP インタラクティブ解析へ「選択 → 即時反応」系の機能を追加する第1弾。**新規追加のみで
既存コールバックの挙動は不変**（feature plot のカラースケールのみ拡張）。

- **lasso/box 選択 + 共有選択 Store**：UMAP の投げ縄/ボックス選択（既定 modebar で利用可能）を
  単一の `selected_cell_ids_store` に集約。今後の逆リンク・選択グループ・選択範囲 DE の土台。
- **ライブ選択統計カード**：選択した瞬間に、選択ピクセル数・全体比・クラスタ別/サンプル別構成・
  表示中 feature の平均強度（parquet 高速路がある時のみ、R 往復なし）を即時表示。
- **feature カラースケール制御**：パレット選択（Plasma/Viridis/Magma 他）、**log10 表示**
  （MSI のダイナミックレンジ対策）、色反転。`update_feature_plot` に Input を追加。
- **violin 分布パネル**：選択 feature の分布をクラスタ別/サンプル別に表示
  （`get_feature_expression_fast` を流用）。
- **ソート可能マーカー DataTable + Top-N CSV 出力**：DEG マーカーを列ソート/絞り込み可能な表で
  表示し、現在の並び替え/絞り込みを反映して Top 10/20/50/100/全件 を CSV 出力。
- **新規ファイル**：`app/utils/selection_utils.py`（純ロジック・単体テスト付）、
  `app/callbacks/interactive_loupe.py`（新規コールバック）、`tests/test_selection_utils.py`、
  `docs/LOUPE_BENCHMARK.md`。version 19.6→20.0。

---

## 2026-06-27_ver19.6

### 改善: PreFlight①(reduction_only) の進捗バーが「準備中」で止まらないように

- **背景**：「① reduction のみ作成」実行中、進捗バーが「準備中」付近で止まり進行が分からなかった。
  UI は進捗を R ログのキーワードで判定する（`_detect_current_step` / `_STEP_DEFINITIONS["tims_v8"]`）が、
  reduction 構築中（Harmony 補正・PCA）は一致キーワードが直近ログ窓に無く「準備中」に落ちていた。
- **変更**：
  - `260623_..._ver6_no-png_slim.R`：reduction 構築直前に `Preprocessing ...` を1行出力（`preprocessing` 段を表示）。
  - `analysis_callbacks.py`：`tims_v8` 段階定義に `("Harmony correction","harmony")` を追加（Harmony 中の
    "Harmony N/10" 出力で段階が進む）。段階検出のログ窓を 200→600 行に拡大し、マーカーの取りこぼしを防止。
- **効果**：reduction_only 実行中、バーが Loading→Preprocessing→Harmony correction→RPCA→Done と進む。
  フル解析でも Harmony 段が増え自然に（後方一致のため回帰なし）。version 19.5→19.6。

---

## 2026-06-27_ver19.5

### 改善: 無補正PCAを Harmony の pca から流用（重複PCA計算を廃止・比較の公平化）

- **背景**：補正(Harmony)使用時、無補正PCA(`Step2_PCA_uncorrected.rds`)を `run_pipeline(FALSE)` で
  **別途フル再計算**していた（ver6）。これは PCA の重複計算であり、さらに無補正側だけ軽い設定
  （`PCA_RETRY_GRID` の 1000変数/20PC）で計算されるため、Harmony の pca（3000変数/30PC）と**設定が
  不一致** → 補正前後比較が交絡（科学的に不適切）だった。
- **変更**（`260623_..._ver6_no-png_slim.R`）：無補正PCAは **`seu_harmony` が内部に持つ入力 pca を
  そのまま流用**するよう変更（`run_pipeline(FALSE)` 廃止）。copy-on-write で複製せず、補正系 reduction を
  除去して pca のみ残し保存。保存形式・ファイル名は不変のため**下流・PreFlight 診断・アプリは無改修**。
- **効果**：(a) 重複 PCA 計算が消え時間・メモリ削減、(b) 無補正PCAが Harmony と同一設定(3000/30)になり
  **補正前後の比較が公平**（診断表で `PCA (uncorrected)` と `Harmony / pca` の推奨値が一致）。
- **不変／対象外**：RPCA は自前の per-batch PCA が必須（reciprocal PCA の本質）のため統一せず維持。
  DESI(v16) は無補正PCAの別途再計算を行っていない（該当パターン無し）ため変更不要。version 19.4→19.5。

---

## 2026-06-26_ver19.4

### 修正: Docker ビルドの恒久安定化（r2u 依存解決崩れによるビルド失敗を防止）

- **背景**：`docker compose up -d --build` の `RUN Rscript install_r_packages.R`（Dockerfile）が、
  `r-cran-gtable` / `r-cran-rlang` / `r-cran-cellranger` 等を**取りこぼし**、依存先（seurat/ggplot2/tidyverse
  /pheatmap 等）が `dependency problems - leaving unconfigured` で全滅 → exit 1。**19.2/19.3 のデプロイが
  ビルド失敗**し、稼働アプリが ver19.1 のまま固定されていた。r2u（apt バイナリ配布元）更新後に、キャッシュ
  された古い apt 索引のまま bspm→apt 解決すると起きる**索引陳腐化**が原因（コード起因ではない）。
- **変更**：`Dockerfile` の R/apt 実行2ステップ（BiocManager、`install_r_packages.R`）の各 `RUN` を
  `apt-get update && <既存コマンド> && rm -rf /var/lib/apt/lists/*` に変更。**install 直前に必ず最新の
  apt 索引**を取得し、依存解決崩れを防止。
- **効果**：以後は通常の `docker compose up -d --build`（キャッシュ有）でも安定ビルド。`--no-cache` 不要化。
  アプリ挙動・解析結果は不変（ビルド工程のみの修正）。version 19.3→19.4。

---

## 2026-06-26_ver19.3

### 修正: RPCA(IntegrateLayers) のメモリ削減で大規模データの OOM を回避（結果不変）

- **背景**：PreFlight①（RPCA, `reduction_only`）が 12GiB コンテナで OOM kill（dmesg `constraint=CONSTRAINT_MEMCG`,
  `anon-rss≈12GiB`、ログ最終行 `Splitting 'counts','data' layers` で停止）。原因は RPCA 直前に **`seu_harmony` を
  抱えたまま** `counts`/`data` を split し、さらに**古い `scale.data`（dense）を保持**したままでピークが上限超過。
  （過去に Windows ネイティブで通っていたのは pagefile 退避で低速完走していたため。Docker は硬い上限で即 kill。）
- **変更**（`260623_..._ver6_no-png_slim.R` の RPCA ブロック、いずれも**解析結果は不変**）：
  1. `seu_rpca` 確保直後に `rm(seu_harmony); gc()` — split 前の二重保持（harmony + rpca コピー）を解消。
  2. split 直前に古い `scale.data` 層を破棄（split 対象外＆後段 `ScaleData` で再計算）— dense 行列ぶんを解放。
  3. split の直前・直後に `gc()` を追加 — 一時ピークを早期解放。
- **不変**：`counts` 層は維持（`FindVariableFeatures` の vst が使用するため破棄不可）。DESI(v16) は旧 v4 方式
  （FindIntegrationAnchors）で本件の split 問題なし＝対象外。
- **運用補足**：`docker-compose.yml` の `memswap_limit: 40g` は稼働コンテナへ未反映だと効かない。
  イメージ再ビルド＋`--force-recreate` で反映（必要に応じてカーネル `swapaccount=1`）。version 19.2→19.3。

---

## 2026-06-26_ver19.2

### 整理: 結果RDSから不要な生データ(counts)層を除去（容量削減・機能不変）

- **背景**：Step2/Step3 等の結果RDSは `data`(正規化)と `counts`(生)の両層を保存していたが、調査の結果
  **保存後に `counts` を読む下流処理は皆無**（DEG=`FindAllMarkers` は data、ビューア抽出/領域平均/cluster filter/
  RESUME/PreFlight 診断も data＋reduction＋メタのみ）。`counts` を読むのは保存前の初期正規化 `apply_input_norm` だけ。
- **変更**：共有ヘルパー `rds_io.R` に `keep_counts`（既定 TRUE＝後方互換）を追加し、ver6 の**結果保存**
  （Step2 Harmony・Step2 無補正PCA・Step3 RPCA・downstream 再保存）で `keep_counts=FALSE` を指定。
  `DietSeurat(counts=FALSE, data=TRUE, ...)` で生counts層のみ除去（data 層は保持＝v5でも有効なオブジェクト）。
- **不変**：Step1（RESUME 時の正規化に counts が必要）は変更なし。無補正PCAの比較解析（UMAP/クラスタ/DEG/作図）は
  **機能・出力とも従来どおり**（ファイルが軽くなるだけ）。ver5・DESI・cluster filter は `keep_counts` 未指定＝
  **counts 維持で完全に不変**。
- **効果**：各結果ファイルの assay 格納が概ね半減（counts ぶん）。重い解析の計算時間は不変（容量・I/O の削減）。
- **対象外**：Harmony/RPCA 間の `data` 重複（2×）解消＝読込時再アタッチ/オンディスク化（別テーマ）。version 19.1→19.2。

## 2026-06-26_ver19.1

### 修正: RPCA(IntegrateLayers) の `future.globals.maxSize` 4GB 上限で RPCA が出ない問題を解消

- **背景**：ver19.0 の v5 移行で PreFlight① の OOM 強制終了は解消（"All Done" まで完走）し Harmony・無補正PCA は完成。
  だが **RPCA だけ未完**。原因はメモリ不足ではなく**設定上限**：`IntegrateLayers`（ver6 :2461）が future に渡す globals が
  **26.25 GiB** で、冒頭 `options(future.globals.maxSize = 4 * 1024^3)`（ver6 :69）の **4GB 上限**に阻まれ、
  nf=2000/1000/500 の3回すべて `... 26.25 GiB ... exceeds 4.00 GiB ... future.globals.maxSize` で失敗
  → `seu_rpca=NULL`（空RDS非生成＝ver18.3 ガード通り）。②に RPCA 行が出なかった。
- **変更**：IntegrateLayers 呼び出しの**直前だけ** `future.globals.maxSize` を一時引き上げ（定数 `RPCA_FGLOBALS_MAXSIZE=64GB`）、
  `tryCatch(finally=)` で**成功/失敗いずれでも直後に 4GB へ復元**。冒頭 :69 の全域既定値は**不変**。
- **安全性**：当該箇所の実行時 plan は `sequential`（:68／`multisession` は FindAllMarkers の :1705-1708 窓だけ）。
  sequential では globals がワーカーへ複製されず in-process 参照のため、**上限を上げてもメモリは多重化しない**
  （前回の実OOM再来リスクは低い）。全域上限を上げると multisession 窓で4重コピーの恐れがあるため**全域は4GB据え置き**。
- **不変**：nfeatures フォールバック（2000→1000→500）・空RDSガード（:2490）・Harmony/無補正PCA・下流/RESUME。
  **ver6（TIMS UMAP）のみ**（ver5/DESI は対象外）。Python 改修なし。R 実行検証は**デプロイ環境（実データ203k）で実施**。version 19.0→19.1。

## 2026-06-25_ver19.0

### 変更: TIMS の RPCA を Seurat v5 `IntegrateLayers(RPCAIntegration)` に移行（大規模OOM解消）

- **背景**：~20万 spots の PreFlight① で、RPCA の v4 `IntegrateData()` が**補正済み発現行列（features×全cell）＋統合ベクトル**を
  実体化して **OOM kill**（ログは `Finding integration vectors` で唐突終了＝R例外なしのSIGKILL）。Harmony は完走するので RPCA だけが原因。
- **変更**：ver6（`260623_..._ver6_no-png_slim.R`）の RPCA 分岐（Step3 :2433-2483）を、
  `split()`＋**`IntegrateLayers(method=RPCAIntegration, new.reduction="rpca")`**＋`JoinLayers` に置換。
  **低次元PCA空間で統合し reduction だけ生成**するため、補正行列を作らず**桁違いに省メモリ**（ピークは Harmony 水準）。
- **整合**：新 reduction 名 `"rpca"` により、下流 `run_downstream_analysis`（reduction 選択 :1562-1566,1584-1587）・
  `run_diagnostics.R`（`names(obj@reductions)`）・PreFlight 表示が**そのまま採用**＝**Python 改修なし**。
  Step2 由来の reduction（harmony 等）を除去し、harmony が誤って優先採用されないようにした。
- **堅牢性**：小バッチ除外＋`k.weight` 自動調整、`nfeatures` フォールバック（2000→1000→500）、失敗時 `seu_rpca=NULL`（空RDS非生成＝ver18.3 ガードと整合）。
- **不変**：gating（`.rpca_section_ok`/`length(seu_list)>=2`）・reduction_only・RESUME・Harmony/無補正PCA(Step2)。**ver6（TIMS UMAP）のみ**（ver5/DESI は別タスク）。**要 Seurat v5**。
- 注意：v4 RPCA と数値完全一致はしない（手法実装差）。R 実行検証は**デプロイ環境（実データ203k）で実施**。version 18.5→19.0。

## 2026-06-25_ver18.5

### 整理: 解析シナリオの統合（同一切片＋群比較）と「行われる処理」の明記

`within_slice`（同一切片）と `condition_compare`（群比較）は補正方針が**完全に同一**（無補正PCA）で、生の値も
`_SCENARIO_MAP` 参照以外で分岐しないことを確認。ドロップダウンで**1項目に統合**（5→4）し、各シナリオで「何が
行われるか（補正手法＋出力reduction）」をヘルプ表に明記、新シナリオ `integrate_correct` も掲載した。**挙動は不変**。

- `settings_tab.py`：初回(`tims_scenario`)・再解析(`reanalysis_tims_scenario`)の両ドロップダウンを
  「同一切片のクラスタ／群比較（Ctrl vs KO 等）：補正なし＝無補正PCA」の1項目に統合（value=`within_slice`）。
  `_norm_scenario()` を追加し、旧セッションに保存された `condition_compare` も復元時に統合項目として選択表示
  （`value=_norm_scenario(ls.get(...))`）。FormText も統合表現に更新。
- `analysis_callbacks.py`：`_SCENARIO_MAP` の `condition_compare` は**後方互換のため残置**（コメント追記）。写像値は不変。
- `analysis.html`：シナリオ表を「シナリオ｜例｜行われる補正と出力」に作り直し、同一切片と群比較を1行に統合、各行に
  出力（RPCA/Harmony は無補正PCA併走）を明記、`integrate_correct` 行を追加。
- 挙動回帰なし（R 改修なし・補正方針の写像も不変）。ツールチップは既に統合表現のため変更なし。version 18.4→18.5。

## 2026-06-25_ver18.4

### 追加: 解析シナリオ「条件比較＋技術差補正（Harmony＋RPCA を適用）」

各条件が1切片ずつ＝生物差と技術差が交絡する設計で、「**測定間の技術差を補正した統合像（条件をまたぐ共有埋め込み/
クラスタ）を得たい**」ニーズに対応。従来のシナリオは過補正防止のため **群比較＝無補正** に固定され、Harmony と RPCA を
1回で両方出す選択肢が無かった（連続切片=RPCAのみ／バッチ補正=Harmonyのみ）。新シナリオ `integrate_correct` を追加し、
単一サンプル・複数アノテーションでも **Harmony＋RPCA＋無補正PCA** をすべて出力できるようにした（前回「RPCA未実行」の真因＝
適切な選択肢が無かった点を解消）。

- `analysis_callbacks.py`：`_SCENARIO_MAP` に `"integrate_correct": ("section_id","slice_id",True)` を追加。
  これにより R 側で Harmony（`group_var=slice_id`; ver6:2307-2310）と RPCA（`.rpca_section_ok=TRUE`; ver6:2430-2432）が
  両方走る。**R 本体は無改修**（既存のゲート条件をシナリオで満たすだけ）。
- `settings_tab.py`：初回(`tims_scenario`)・再解析(`reanalysis_tims_scenario`)の両ドロップダウンに選択肢追加＋FormText 更新。
- `tooltips.py`：`tims_scenario` / `reanalysis_tims_scenario` のツールチップを更新。
- **副作用なし**：`ANNOTATION_ROLE`/`BATCH_VAR` は RPCA/Harmony のゲート専用。DEG・作図は常に `condition` でグループ化
  （ver6:1328,1365、`condition<-slice_id` は :2196 で常時）するため、**条件間DEGは従来どおり**。既存シナリオの挙動も不変。
- **注意（過補正）**：交絡下では技術差と生物差は一括で縮むため、埋め込み上の条件分離は意図的に縮小する。条件差の検出は
  DEG（reduction 非依存）で行う前提。version 18.3→18.4。

## 2026-06-25_ver18.3

### 修正: PreFlight で RPCA が「reduction が検出されませんでした」と誤表示される問題

単一サンプル・群比較（`condition_compare`）など **RPCA が正しくスキップされる**ケースで、PreFlight 診断の
RPCA 行に「reduction が検出されませんでした」と出て、解析失敗のように見えていた。原因は TIMS ver6/ver5 で
RPCA スキップ時にも**空の `Step3_RPCA_Result.rds`（`list(obj=NULL)`）を無条件保存**しており、それを診断が
「reduction 無し」として拾っていたこと。**解析自体（単一サンプル群比較＝無補正PCA）は正常**で、表示のみが誤解を招いていた。
（「Harmony 行が pca」表示も正常：batch=sample が1水準のため Harmony は無補正PCAにフォールバックし、ファイル名
`Step2_HarmonyPCA_Result.rds` のため検出器が便宜上「Harmony」と命名するだけ。reduction 列は正しく pca。）

- **R 根本修正（空RDSをそもそも作らない）**：`260623_..._ver6_no-png_slim.R` / `260619_..._ver5_no-png_slim.R` の
  Step3 保存を `if (!is.null(seu_rpca))` でガード。RPCA スキップ回は `Step3_RPCA_Result.rds` を生成しない
  → PreFlight に余計な RPCA 行・警告が出ない。下流は既存の `if(!is.null(seu_rpca))`、RESUME は `file.exists` で安全。
- **Python 防御（過去実行で既に残っている空RDS対策）**：`preflight_callbacks.py` の reduction 空の行を、警告では
  なく中立表示に変更（reduction=`(スキップ)`／「<手法> は未実行（単一サンプル/生物群では補正不要のためスキップ。問題ありません）」）。
- 解析結果（PCA/Harmony/RPCA 実体）は不変。DESI v16/v15（元々空RPCA非生成）・TIMS ver18（Step3構造なし）に影響なし。
- version 18.2→18.3。

## 2026-06-25_ver18.2

### 改善: ④（reduction再利用）の出力フォルダをUMAPハイパラ値で自動命名

「結果を見てからUMAPハイパラだけ変えて掛け直す」は ④続きを実行（reduction再利用）で対応済みだが、
出力が固定サブフォルダだと試行が上書きされ、複数設定の比較に手動フォルダ管理が必要だった。
④のときだけ出力サブフォルダ名にハイパラ由来サフィックス（例 `_nn15_md0p3_dim20`）を自動付与し、
上書きせず横並び比較できるようにした。

- `analysis_callbacks.py`：`_umap_hp_suffix()`（FS安全な短いサフィックス生成。`.`→`p`、metricはcosine以外のみ）
  と `_strip_hp_suffix()`（多重付与防止）を追加。`downstream_mode` のときだけ `full_output_dir` を
  `output_dir/(base+suffix)` に差し替え（base空は `umap`）。成功メッセージに出力フォルダ名を表示。
- reduction は従来どおり `last_result_dir` から再利用（出力名変更の影響なし）。full解析・①・再解析の命名は不変。
- `settings_tab.py`：④ヘルプに自動命名の一文を追記。version 18.1→18.2。

## 2026-06-25_ver18.1

### 修正: 再解析（クラスターフィルタ）で無視されていた5入力を反映

監査により、再解析では一部のUI入力が収集・保存されるのにRへ注入されず固定値で動く
「機能はあるが数値が反映されない」不具合を確認。フル解析と同じ要領で再解析にも反映するよう修正。

- 対象5件：`reanalysis_p_thresh` / `reanalysis_logfc_thresh`（DESI+TIMS）、
  `reanalysis_ion_mode` / `reanalysis_tolerance_mz` / `reanalysis_adduct_filter`（TIMS専用）。
- 経路（最小・安全に実装）：
  - **DEG閾値**：`analysis_callbacks` で収集 → `generate_cluster_filter_config` が TIMS=`V13_DEG_*` / DESI=`V8_DEG_*` を注入
    → クラスタフィルタRの `make_v13_copy/make_v8_copy` がメインテンプレ copy の `DEG_P_THRESH_VAL`/`DEG_LOGFC_TH_VAL` に伝播。
  - **ion_mode / tolerance（TIMS）**：既存の `V13_ION_MODE`/`V13_TOLERANCE_MZ`（宣言・伝播済み）へ Python 注入を追加。
  - **adduct（TIMS）**：ver6 の env フック `.get_adduct_override`（`ANNOT_ADDUCTS`）を利用し、`start_analysis_process` に
    `env_extra` を追加して再解析起動時のみ環境変数で反映（多行ブロック書換え回避）。
- 非対象：DESI の ion/tolerance/adduct（v16非対応・UI非表示）、UMAP/mz_align の再解析反映（別途）。フル解析は不変。
- version 18.0→18.1。

## 2026-06-25_ver18.0

### 機能: PreFlight 診断結果を「再計算せず再表示」（自動＋ボタン）

PreFlight 結果は `<result_dir>/preflight/diagnostics.json` に保存されるが、従来は②実行直後の一度きりの
描画で、サブプロジェクトを開き直すと再表示には②での再計算が必要だった。json は自己完結なので、
プロセスを起動せず読み込んで既存の描画ロジックを呼ぶことで、**再計算ゼロで表と推奨（③反映用）を復元**する。

- `preflight_callbacks.py`：共通ヘルパー `_load_saved_diagnostics(result_dir)`（json読込→`_render_diagnostics_table`
  →「📂 保存済み」バナー付きで描画＋`preflight_store` を `status="loaded"` で復元）。
  - **自動表示** `autoload_saved_diagnostics`：サブプロジェクト選択時、保存があれば自動再表示（実行中・保存なしは無反応）。
  - **手動ボタン** `load_saved_diagnostics_button`：「📂 前回の診断を表示」。保存が無ければ明示メッセージ。
  - 出力(container/store/poll.disabled)は canonical=poll のまま allow_duplicate で相乗り。
- `settings_tab.py`：PreFlight ボタン列に「📂 前回の診断を表示（再計算なし）」を追加。
- R診断・②実行・③反映の本体は不変。RDS が無く（slim 済み等）ても json から再描画可。version 17.1→18.0。

## 2026-06-25_ver17.1

### 改善: PreFlight「③推奨値を反映」を手法間の最大値に＋UMAPパラメータ説明/注記

手法ごと（Harmony/RPCA/PCA）に推奨 dims/n.neighbors は異なるが、解析は1組の共有UMAP設定で全手法を実行する。
従来は③反映が最確信1手法のみを採用していたため、要求の大きい手法が下限割れし得た。推奨値は「安定/連結に
必要な最小値」なので、**全手法が満たす最小の共通値＝各手法の推奨の最大値（max）**を採用するよう変更。

- `preflight_callbacks.py` `_render_diagnostics_table`: 集約を「最確信1件」→「**手法間 max**」へ。
  dims=各手法推奨の最大、n.neighbors=最大かつ**全手法の許容上限内にクランプ**。単一手法のみ推奨ありなら
  従来同等（その値）。`source` は "max: …"。脚注・docstring を更新。min.dist/metric は既定固定（0.3/cosine）。
- `settings_tab.py`: 各UMAPパラメータ（n.neighbors/min.dist/metric/dims）の入力下に「何に効く値か」の説明を表示。
  ヘルプ文に「自動推奨は dims・n.neighbors のみ／③は手法間 max／min.dist・metric は既定固定」を追記。
- R診断ロジック・解析の共有設計は不変。version 17.0→17.1。

## 2026-06-25_ver17.0

### 機能: 解析実行前に「既存結果あり（上書き注意）」を警告

同じ出力フォルダに新しい解析を出すと、同名ファイルは上書きされ、新解析が作らない別名の旧ファイルは
残る（新旧混在）。事故防止のため、**出力先に既存の解析結果があるときだけ**、解析開始前に確認モーダルを
表示し「実行する／キャンセル」を選べるようにした（既存結果が無ければ従来どおり即実行・警告なし）。

- **外科的なゲート**: `run_analysis` は同期コールバックで、冒頭の no-op return（8×`no_update`）を流用し、
  既存結果検出時は同じ8タプルを返して**実行を差し止める**（他の return／Output は不変）。確認ボタンを
  追加トリガにして「確認後に本実行」。モーダル開閉は `open_overwrite_modal` / `close_overwrite_modal` の
  2コールバックで担当（`analysis_callbacks.py`）。
- **既存結果の検出**は `_detect_integration_methods()` を再利用（reduction RDS / analysis_params.json /
  RDS_Files/*.rds のいずれか）。確認モーダルは `delete_project_modal` と同型。
- **対象**: 「解析実行」(フル) と「reductionのみ」。「reduction再利用(downstream)」は既存前提のため対象外。
- `settings_tab.py` に確認モーダル＋`overwrite_pending_mode` Store を追加。version 16.0→17.0。

## 2026-06-25_ver16.0

### 機能: インタラクティブ解析に「PCA（未補正）」を追加（既存結果でも・UMAP形式）

インタラクティブ解析の手法セレクタが Harmony/RPCA のみだったところに、**未補正PCA**（補正の基準）を
**Harmony/RPCA と同じ UMAP 形式**で並べて比較できるよう追加。**既存の解析結果でも再解析不要**。

- **派生RDSの遅延生成方式**：未補正の `pca` 次元は Harmony RDS に必ず存在するが保存済みUMAPは harmony 由来。
  そこで「PCA」選択時に **Harmony RDS から未補正pca由来のUMAPを計算した派生RDSを生成**し、独立ファイルとして
  通常手法と同様に扱う（キャッシュ/state が rds_path キーのため、別パス＝衝突回避。既存の抽出/特徴量機構を再利用）。
  - 新R helper `App/Script/helpers/derive_uncorrected_pca.R`（`RunUMAP(reduction="pca")`、クラスタは既存維持）。
  - `seurat_bridge.derive_uncorrected_pca()`（冪等・subprocess）。書込先は `SEURAT_CACHE_DIR` 配下＝結果フォルダ非汚染。
  - `_detect_integration_methods(include_derived=True)` で Harmony があり未補正RDSが無いとき「PCA」を選択肢に追加
    （`interactive_callbacks.py`）。選択時に Link A→B で未生成なら生成→抽出。初回のみUMAP計算、以降キャッシュ。
- 後方互換：Harmony/RPCA・既定動作は不変。新規解析の `Step2_PCA_uncorrected.rds` は従来通り「PCA (uncorrected)」として検出。
- スコープ外：軽量ビューア（既定 `include_derived=False` で挙動不変）、派生PCAの事前DEG（随時DEGは可）。
- ツールチップ補足。version 15.0→16.0。

## 2026-06-25_ver15.0

### 機能: TIMS「解析シナリオ」をGUIで選択（切片アノテーションの扱い）＋ 再解析へ引き継ぎ

切片アノテーション（`annotation`→`slice_id`→`condition`）が何を表すかを、技術用語ではなく**実験シナリオ**でGUI選択できるようにし、選択に応じて補正方法を自動切替する。**ver6本体テンプレは無改修**（既存スイッチ `ANNOTATION_ROLE`/`BATCH_VAR`/`ALLOW_CONDITION_CORRECTION` を注入するのみ）。

- **設定画面に「解析シナリオ」プルダウン**（`settings_tab.py`, TIMS UMAP時）。4択 → 補正ポリシーへ変換（`analysis_callbacks.py` の `_SCENARIO_MAP`）→ `analysis_runner.py` で R へ注入:
  - 同一切片の中のクラスタ / 群比較(Ctrl vs KO 等が別アノテーション) → 無補正PCA
  - 連続切片を技術反復としてまとめる → `section_id`（単一sampleでも RPCA を slice_id 統合）
  - 切片間の測定差(バッチ)を補正【非推奨】 → `slice_id` を Harmony 補正（`ALLOW_CONDITION_CORRECTION=TRUE`）
  - 既定 `within_slice` ＝ 従来挙動（後方互換）。
- **再解析(exclusion/inclusion)へ引き継ぎ**: 再解析タブにも同プルダウン（既定=初回値）。`generate_cluster_filter_config` が `V13_ANNOTATION_ROLE/V13_BATCH_VAR/V13_ALLOW_CONDITION_CORRECTION` を注入し、ver18 の `make_v13_copy_with_settings` が ver6 コピーへ伝播（`V13_INPUT_NORMALIZED` と同方式・小改修）。通常/PreFlight 再解析の双方で部分集合の reduction にシナリオが効く。
- **計算の再利用（調査結論・実装なし）**: 部分集合の reduction 再計算は既存 PreFlight ループ（`①reduction_only→④downstream_from_reduction`, ver12）で1回に抑えられる。初回(全体)の reduction を部分集合へ流用するのは科学的に不可のため行わない。説明書(§3「解析シナリオの選び方」)に明記。
- 永続化（`session_manager.py`）・ツールチップ（`tooltips.py`）・推奨バナー③の文言・説明書（表＋再解析/再利用の注記）を追加。

## 2026-06-25_ver14.0

### 機能: 一般的な使用法（標準フロー）を「手法別の推奨バナー」としてアプリ内表示

TIMS/DESI それぞれの標準フローを「推奨」として明示表示し、初見でも正しい使い方が分かるようにする。**解析ロジック・R スクリプト・既定値は無改修**（表示の追加のみ）。

- **設定画面に手法別の推奨バナー（常時表示）を追加**（`settings_tab.py` の UMAP 解析設定 先頭、`dbc.Alert`）:
  - **TIMS × SCiLS RMS**: RMS出力 → 正規化 **OFF** ＋ **log1p**（二重正規化を回避）→ サンプル数で PCAのみ/Harmony・RPCA → UMAP。
  - **DESI（生データ）**: 入力 → 正規化 **ON**（LogNormalize）→ サンプル数で PCAのみ/Harmony・RPCA → UMAP。
- **手法別に分離表示**（`file_handlers.py` の `toggle_settings_panels` に Output 2つ追加）: **TIMS 解析時は TIMS 用のみ／DESI 解析時は DESI 用のみ**を表示（両方同時には出さない／再解析時は非表示）。
- **説明書**（`templates/help/analysis.html`）の §3「解析設定タブ」に「**🚀 標準フロー（手法別）**」節（`id="standard-flow"`）を TIMS/DESI 別ボックスで追加（目次にリンク追加）。バナー内リンクから該当箇所へジャンプ。

## 2026-06-25_ver13.1

### ドキュメント: データの前提と結果の読み方を「アプリ内コメント＋説明書」で共通認識化

1切片/段階・RMS正規化済みデータの解釈（交絡・正規化・バッチ補正・UMAP/PCA の読み方・解剖アンカー）を明文化し、解析の前提と結果の読み方をチームの共通認識にする。**解析ロジック・R スクリプトは無改修**（UI 説明テキストと説明書の追加のみ）。

- **アプリ内コメント（折りたたみ `html.Details` ＋ `?` バッジ）を4箇所に追加**:
  - **正規化設定**（`settings_tab.py`）: RMS済みは「正規化 OFF＋NORM_MODE=log1p」で二重正規化を回避。「正規化 ON」＝LogNormalize＝TIC正規化+log。RMS はバッチ補正ではない（構造的バッチは残る）。
  - **補正手法 `cluster_source`**（`settings_tab.py`）: Harmony/RPCA は共通性を見る統合。各条件1切片（バッチ=条件が交絡）では過補正で生物差も消える。強度差(Harmony>RPCA)は技術/生物の分離器ではない。
  - **結果ビュー `interactive_integration_method`**（`interactive_tab.py`）: 未補正(PCA)＝差／補正＝共通性。UMAP の大域距離は非定量。解剖が同じで色が違う＝生物差の候補（要検証）。
  - **PreFlight**（`settings_tab.py`）: `not_identifiable`＝交絡で技術/生物が分離不能。confidence/iLISI の読み方。確証には反復(≥2切片/段階)・共有QC が必要。
- **ツールチップ**（`tooltips.py`）に上記バッジ（`normalize_input` / `cluster_source` / `interactive_integration_method`）の説明を追加。
- **説明書**（`templates/help/analysis.html`）に新セクション「**7. データの前提と結果の読み方（重要）**」を追加（目次リンク追加、以降の章番号 8〜13 へ繰り下げ）。
- 注: PreFlight 診断表の `not_identifiable` 行の赤強調は既存実装（`preflight_callbacks.py`）をそのまま活用（コールバック無改修）。

## 2026-06-23_ver13.0

### 修正: 監査で見つかった2件（TIMS の dims 入力を有効化 ＋ DESI の重複作図を除去）

「記載はあるのに機能しない/無駄なコード」監査で確定した2件を修正。規約に従い**修正対象の R は版を進め、旧版は温存**。アプリ callbacks は無改修（dims 注入機構は既存）。

- **TIMS の dims 入力が効くように**（新規 `260623_DBSCAN_With_cluster_ver6_no-png_slim.R`、旧 ver5 温存）：
  - TIMS は UMAP 次元をリトライグリッド（`umap_dims` 30→20→15）＋`UMAP_DIMS_MAX`(30) で決め、単一の `UMAP_DIMS_N` が無かったため、アプリが注入する `umap_dims_n` が黙って消えていた（dims 入力欄が TIMS で無効）。
  - ver6 で `UMAP_DIMS_N` 定数を追加し、UIで指定された場合のみリトライグリッドの `umap_dims`／`UMAP_DIMS_MAX`／`MAX_PCS` をその値に上書き（先頭=優先エントリにユーザー値、フォールバックは上限キャップで小データ対応を維持）。**既定30では override せず ver5 と完全に同一挙動**（後方互換）。
  - 効果：PreFlight 推奨 dims を TIMS でも適用可能に。TIMS 再解析④（メイン経路の downstream_from_reduction）でも dims が効く。
- **DESI の重複作図を除去**（新規 `260623_DESI-UMAP_Template_v16.R`、旧 v15 温存）：
  - `create_combined_row_plot` 内で MSI タイル群（`plots_row`）を一度作った直後、未使用のまま作り直して上書きしていた（無駄な二重計算）。1つ目を削除。**出力は完全に不変・高速化のみ**。
- `config.py` を v16 / ver6 へ更新（cluster filter ver3/ver18 への `V8/V13_SCRIPT_PATH` 注入も自動で新版に切替）。
- 注: R 実行検証は解析環境で実施（本リポジトリに R 無し）。新旧 diff で「ver6＝ver5＋override のみ」「v16＝v15－重複ブロックのみ」、括弧バランスを静的確認済み。

## 2026-06-23_ver12.0

### 機能追加: 再解析（exclusion/inclusion）にも PreFlight ループを通す（reduction_only 再解析）

ver11 までで PreFlight ループ（① reduction のみ → ② 診断 → ③ 反映 → ④ 続き）はメイン解析専用だった。本バージョンは**クラスタフィルタ再解析（exclusion=除外 / inclusion=keep）にも同ループを適用**。絞り込んだ部分集合に対し reduction だけを作って診断・チューニングし、tuned param で UMAP 以降を実行できる。

設計の核：**②診断・③反映・④続き は `last_result_dir` の reduction RDS に対して動く汎用機能**なので無改修で再利用。唯一の追加は「**① reduction_only を再解析でも作れること**」。

- **アプリ**（`analysis_callbacks.py`）: ① を再解析（`*_cluster_filter`）でも `reduction_only` に（`pipeline_stage` を cluster_filter params にも設定）。④（btn_run_downstream）は `*_cluster_filter → *_v8` に**リマップ**し、メイン経路の downstream_from_reduction で部分集合 reduction（`last_result_dir`）をロードして UMAP 以降を実行。②③は無改修。
- **アプリ**（`analysis_runner.py`）: `generate_cluster_filter_config` に `pipeline_stage`→`RERUN_PIPELINE_STAGE` 注入を追加（UMAP ハイパラは①では不要・④はメイン経路で注入済み）。
- **R（版を進めて新規作成・旧版温存）**:
  - **DESI `DESI_RDS_ClusterFilter_ver3.R`**（旧 ver2 温存）: `RERUN_PIPELINE_STAGE` 追加、`make_v8_copy_with_settings` が reduction_only 時のみ v15 copy の `PIPELINE_STAGE` を伝播（私の v15 ガードがそのまま機能）、後段 merge を reduction_only でスキップ。
  - **TIMS `260623_DBSCAN_ver18_Cluster_Filter_ReUMAP.R`**（旧 ver17 温存）: 同様の伝播＋`patch_v13_step2_pipeline` の置換 `run_pipeline` を reduction_only 対応（reduction 計算後に即 return）、後段 ReUMAP-replace / merge を reduction_only でスキップ。`run_downstream_analysis`（ver5 の reduction_only ガード）は patch 非対象で生存し DEG/作図をスキップ。
  - `config.py` を ver3 / ver18 へ更新。メインテンプレ v15/ver5 は本バージョン無改修。
- **ワークフロー**: 再解析モードで include/exclude 設定 → ① → ②（部分集合 reduction を診断）→ ③ → ④（部分集合 reduction を再利用し UMAP 以降）。
- **後方互換**: 通常の再解析（`RERUN_PIPELINE_STAGE`="full"／未注入）は全ガードが従来分岐に倒れ**挙動不変**（merge/ReUMAP 含む）。メイン①②③④も不変。
- 注: R 実行検証は解析環境で実施（本リポジトリに R 無し）。括弧バランスは静的検査で確認済み。

## 2026-06-23_ver11.0

### 機能追加: PreFlight「④ 続きを実行（reduction再利用）」＝ `PIPELINE_STAGE="downstream_from_reduction"`

ver10.0 までは PreFlight 後の「④ 解析実行」が**素のフル解析＝reduction(ScaleData/PCA/Harmony/RPCA)を最初から再計算**していた。診断が見るのは reduction の埋め込みだけで、PreFlight が変えるのは UMAP 系 param のみ。本バージョンは予約済み定数 `downstream_from_reduction` を有効化し、**④が①の reduction RDS を読み込んで再計算をスキップ → 決めた param で UMAP→クラスタリング→DEG→作図 だけ実行**するようにした（reduction 通算1回＝最も無駄がない）。**自動連結はしない**（④は手動）。

- **UI**（`settings_tab.py`）: PreFlight セクションに「④ 続きを実行（reduction再利用）」ボタン（`btn_run_downstream`）を追加。フロー文言を更新。
- **配線**（`analysis_callbacks.py`）: `btn_run_downstream` を `run_analysis` の3つ目のトリガーに追加。`downstream_mode` 時に `pipeline_stage="downstream_from_reduction"`＋`resume_from_rds=True`＋`resume_rds_paths`=①の `last_result_dir/RDS_Files` の reduction RDS（`get_sub_project`＋`_detect_integration_methods` で解決）をセット。既存 RESUME 機構が `RESUME_DIR_PATH` を自動解決（runner 変更なし）。①未実行時はエラートースト。
- **R 実装（追加のみ・既存挙動は保持）**:
  - **DESI v15**: ④は raw 読込/seu_list 構築/平滑化/正規化を全スキップ（存在する reduction RDS で分岐 override）。single/Harmony/RPCA の各 branch で RESUME ロード後、reduction-only RDS（UMAP/クラスタ無し）なら **UMAP→FindNeighbors→FindClusters を後付けして再保存**し、既存下流（作図/DEG）へ。下流は当該 reduction が NULL ならスキップ。下流が使う `sample_names` をロード済みオブジェクトに同期。
  - **TIMS ver5**: ④は Step1 再計算/Step2・Step3 の raw 再計算をスキップ（RESUME ロードのみ）。`run_downstream_analysis()` 先頭で `seurat_clusters` 不在を検出したら **FindNeighbors+FindClusters を後付け**（full/classic-resume では no-op）し、④では完成 RDS を新フォルダへ再保存。Step2 のみ/Step3 のみのロードにも NULL ガードで対応。
- **効率**: ①(reduction) ＋ ④(UMAP以降) で実質フル解析1回分。重い reduction の二重計算が無くなる。
- **後方互換**: `full`／`reduction_only`／従来の RESUME-full は不変（新ブロックは「umap/クラスタ既存」検出で no-op）。
- 注: R 実行検証は解析環境で実施（本リポジトリに R 無し）。括弧バランスは静的検査で確認済み。本番取り込み後に ①→②→③→④ の通し動作（④ログに RunHarmony/IntegrateData が無く RunUMAP/FindClusters のみ＝reduction 再利用）を確認のこと。

## 2026-06-23_ver10.0

### 機能追加: PreFlight 診断に「reduction のみ作成（診断用）」を追加（フル解析を先に回す必要を解消）

ver9.0 では PreFlight 診断が**完了済みのフル解析**を前提としており、「離陸前点検」なのに一度フル解析が必要という本末転倒があった。診断が必要とするのは reduction（PCA/Harmony/RPCA）の埋め込みだけ（UMAP/クラスタリング/DEG/作図は不要）であることを利用し、**reduction まで作って即停止する軽量モード `PIPELINE_STAGE=reduction_only`** を実装した。

- **新フロー**: ① reduction のみ作成（診断用）→ ② PreFlight 診断 → ③ 推奨値を反映 → ④ 解析実行（フル）。既に完了済み解析があれば ① は省略可。
- **設定 UI**（`settings_tab.py`）: PreFlight セクションに「① reduction のみ作成（診断用）」ボタン（`btn_make_reduction`）を追加し、4 ステップのフローを明示。
- **配線**（`analysis_callbacks.py`）: `btn_make_reduction` を `run_analysis` のトリガーに追加。`ctx.triggered_id` で判定し、reduction モード時に `params["pipeline_stage"]="reduction_only"` を注入（テンプレ定数 `PIPELINE_STAGE` へは既存の `_hp_str` 機構で反映）。`analysis_params.json` にも記録。通常実行は従来どおり `full`。
- **R テンプレ実装（予約済み定数を有効化）**:
  - **DESI v15**: single/Harmony/RPCA の各分岐で、reduction 計算後に **UMAP/FindNeighbors/FindClusters と 作図/DEG をスキップ**し、reduction RDS（`DESI_SeuratCombined_harmony.rds` / `_RPCA.rds` / `DESI_Seurat_SingleSample.rds`）だけ保存して終了。
  - **TIMS ver5**: `run_pipeline`（Step2 Harmony/PCA）と RPCA（Step3）で reduction 計算後に UMAP/クラスタリングをスキップ。`run_downstream_analysis()` 先頭で reduction_only なら即 return（DEG/作図を全スキップ）。Step2/Step3 RDS は保存。
  - reduction_only で生成した RDS は `RDS_Files/` に従来名で保存され、PreFlight 診断（`_detect_integration_methods`）がそのまま検出可能。デフォルト param の「捨て UMAP」は作らない。
- **効率**: reduction_only ＋ フル解析（推奨 param）の 2 回で済む（従来は「フル解析（無駄）→ 診断 → フル解析」の 2 フル解析が必要だった）。reduction 自体は各回で再計算（reduction の再利用＝`downstream_from_reduction` は別タスクとして保持）。
- 注: R 実行検証は解析環境で実施（本リポジトリ環境に R 無し）。括弧バランスは静的検査で確認済み。本番取り込み後に動作確認のこと。

## 2026-06-23_ver9.0

### 機能追加: PreFlight 診断の UI 化（アプリ内で「見る」＋推奨値を「使う」）

これまで CLI 専用（`App/Script/helpers/run_diagnostics.R`）だった UMAP PreFlight / バッチ補正診断を**アプリ設定タブに配線**し、`analyze → diagnose → 調整 → re-analyze` のループをアプリ内で完結できるようにした。R 側の診断ロジックは変更していない。

- **診断を「見る」**: 設定タブに「🩺 PreFlight 診断」セクションを追加（`settings_tab.py`）。選択中サブプロジェクトの完了済み結果フォルダから reduction RDS（Harmony/RPCA/PCA 等）を `_detect_integration_methods` で検出し、`run_diagnostics.R` を `start_analysis_process` で実行（`<result_dir>/preflight/` へ出力）。完了後 `diagnostics.json` を読み、**reduction 別の表**（手法 / 推奨dims / 推奨n.neighbors / 許容n.neighbors / 推奨度 / 設計(交絡) / iLISI / 警告）を表示。
- **推奨値を「使う」**: UMAP ハイパーパラメータ入力欄（n.neighbors / min.dist / metric / dims）を新設。「推奨値を入力欄へ反映」で最有力（confidence 高優先）の推奨を入力欄へ転記（提案のみ・自動適用なし）。入力値は次回「解析実行」時にテンプレ定数へ注入（既存 `analysis_runner` の `_hp_*` 機構）され、`analysis_params.json` にも記録（再現性）。
- 新規 `App/app/callbacks/preflight_callbacks.py`（起動・ポーリング・表示・推奨反映）。`config.py` に `RUN_DIAGNOSTICS_PATH` を追加、`main.py` でコールバック登録。解析中（他 Rscript 実行中）は診断を起動できない（同時実行ガード＝想定どおり）。
- **既知の非対称（注意）**: `n.neighbors / min.dist / metric` の注入は DESI v15・TIMS ver5 双方で有効。**dims の自動反映は DESI のみ**（TIMS ver5 は `UMAP_DIMS_MAX`＋リトライグリッドで dims を決めるため別タスク）。表示上は両方の推奨 dims を出す。
- 注: R 実行検証は解析環境で実施（本リポジトリ環境に R 無し）。本番取り込み後に動作確認のこと。

## 2026-06-23_ver8.0

### 機能追加: TIMS Parquet の「注釈付き列名（化合物名_m/z | DB | …）」対応 ＋ メタ情報保持

SCiLS/peak-list アノテーションを列名に埋め込んだ Parquet（特徴量列＝`化合物名_m/z | DB | adduct | ppm | formula | SMILES | adduct_family | …`）を、TIMS 解析がそのまま読めるようにした。従来は `mz_*`／純数値列のみ対応で `No mz_ columns found in Parquet` で停止していた。

- `read_desi_data`（`260619_DBSCAN_With_cluster_ver5_no-png_slim.R` の Parquet 分岐）に**注釈付き列名分岐**を追加。特徴量名に「化合物名_m/z」（最初の ` | ` より前）を採用し、m/z は末尾 `_<数値>` から抽出。
- `|` 以降のメタ情報（adduct/ppm/formula/SMILES/adduct_family ＋ **raw 全文**）を per-feature テーブル化し、出力に **`feature_annotations.parquet`** として保存（今後の機能から参照可能）。
- 同テーブルに **`compound`（化合物名のみ）** と **`mz`（数値）** を**分離して**保持。特徴量名は連結の「化合物名_m/z」のまま（同一化合物が複数 m/z を持つためユニーク性確保）だが、`compound` 単独でのソート/検索/グルーピング、`mz` 単独での数値ソートが可能。
- 下流の m/z 抽出（`calibrate_feature_names` / `align_mz_features` / `annotate_mz_with_format`）を新ヘルパ **`.feature_mz()`** に統一し、化合物名に数字を含む場合（例 `CL 74:8_1475.9870`）の誤抽出を防止。
- 既存の `mz_*`／純数値列形式は**後方互換**で従来どおり。
- **ビルド堅牢化**: `install_r_packages.R` に `httpuv`（＋`shiny`/`miniUI`）を明示追加。r2u 再ビルド時に Seurat の間接依存 `httpuv` が取りこぼされ `there is no package called 'httpuv'` で起動不能になる事故を防止。
- 注: R 実行検証は解析環境で実施（本リポジトリ環境に R 無し）。本番（別リポ `umap-webapp-claudecode`）へ取り込み後に動作確認のこと。

## 2026-06-18_ver7.10

### 機能拡張: 正規化トグルを TIMS 再解析にも適用 ＋ DESI正規化の一本化・設定永続化

ver7.9 の正規化トグル（主解析）に続き、コード監査で判明した残課題に対応。

- **TIMS 再解析(cluster filter)の二重正規化を解消**: 従来 `ReUMAP.R` は ver4 の `run_pipeline` を
  `NormalizeData`(LogNormalize) ハードコードで置換しており、RMS入力で二重正規化になっていた。
  `patch_v13_step2_pipeline` を `apply_input_norm` 使用に変更し、`make_v13_copy_with_settings` が
  `V13_INPUT_NORMALIZED`/`V13_NORM_MODE` を ver4 コピーへ注入するよう拡張。
  `generate_cluster_filter_config` がアプリのトグル値を注入。再解析設定UI(`tims_reanalysis_ion_settings`)に
  正規化トグルを追加（TIMSはRMS正規化済みのため既定OFF）。
- **DESI 正規化の一本化**: `DESI v14` の冗長な log1p ループ（下流 `apply_input_norm` で上書きされる
  ため二重ではないが紛らわしい）を `apply_input_norm` に統一。挙動不変。
- **設定の永続化**: `normalize_input`/`norm_mode`（主・再解析）を `save_last_settings` に保存し、
  再起動後も選択を保持。`set_default_normalize` は解析法変更時のみ既定を再適用（`prevent_initial_call`）。

注: R スクリプト変更（ReUMAP.R / DESI v14）は実機での動作確認を推奨。

---

## 2026-06-17_ver7.9

### 機能追加: 正規化(LogNormalize)の on/off をアプリの解析実行時に指定 ＋ TIMS を ver4 に切替

**背景**: TIMS は SCiLS で RMS 正規化済みで取り込むが、従来アプリの TIMS テンプレ(ver3)は常に
LogNormalize を適用しており、RMS と合わせて**二重正規化**になっていた（出力CSVの値域 max≈7.42 で確認）。
DESI は生データのため LogNormalize 1回で適正。

**変更**:
- **TIMS テンプレを ver4(260525)** に切替（`App/app/config.py`）。ver4 は二重正規化回避スイッチ
  `INPUT_NORMALIZED`/`NORM_MODE`（`apply_input_norm`）に加え、過補正の防止（バッチ補正を技術的
  バッチ=sample のみに限定し、生物学的な切片差は温存）、無補正PCAの併走出力 (`PCA (uncorrected)`)、
  マーカーCSVが探索的なピクセル順位である旨の注記、を内蔵する。
- **正規化トグルをアプリUIに追加**（解析設定→「正規化 (LogNormalize)」）。ON=LogNormalize 実行 /
  OFF=正規化済み入力（OFF時の変換 `NORM_MODE` を none/sqrt/log1p から選択・既定 log1p）。
  既定は **TIMS=OFF / DESI=ON**（解析法で自動切替・手動上書き可）。
- `generate_v8_config` が UI の選択を R の `INPUT_NORMALIZED`/`NORM_MODE` に注入
  （既存の `_replace_assign` 機構を流用）。
- **DESI v14** にも同じ `apply_input_norm` を移植（既定 ON＝従来どおり LogNormalize。toggle で OFF 可）。

**影響（挙動変更）**: 今後の TIMS 解析は「正規化1回」かつ「生物学的な切片差を補正で消さない」
（ver4 の過補正防止）ため、過去(ver3)のクラスタリング結果とは変わる（方法論的により適正）。
既存の二重正規化 RDS を単一化するには、正規化 OFF で再解析が必要。

---

## 2026-06-17_ver7.8

### 変更: MetaboAnalyst 出力の群ラベルに `cluster` を明記

全切片統合エクスポートの群ラベルを `{切片}_{ROI}_{クラスタ}`（例 `E14_Brain_14`）から
**`{切片}_{ROI}_cluster{クラスタ}`（例 `E14_Brain_cluster14`）** に変更（後方互換の単一切片
ラベル `{ROI}_cluster{クラスタ}` と表記を統一）。`build_groups_table`（B経路）と
`build_region_cluster_export`（フォールバック）の両方を更新。ラベル様式が変わるため、出力
キャッシュキーに様式タグを付与し旧キャッシュを自動無効化（次回出力で新ラベルに再計算）。
出力の数値・列は不変。

---

## 2026-06-16_ver7.7

### 修正/高速化: 解剖×クラスタの MetaboAnalyst CSV出力（無反応の解消・高速化・進捗表示）

「④ MetaboAnalyst用CSV出力」が押しても無反応だった（callback は発火するが、巨大
`expression_matrix.parquet` の丸読み＋全体集計が重く応答が返らない／進捗UIも無く、さらに
`_get_state` 等が try 外で例外時に500無反応）。以下で解消した。

- **B（高速化）**: 新R `Script/helpers/export_region_cluster_means.R` を追加し、巨大行列を
  作らず RDS の**同一 data layer**（`JoinLayers→LayerData(layer="data")`＝既存と科学的に同一）
  から対象 cell のみ sparse のまま ROI×クラスタ平均を直接計算。`seurat_bridge.export_region_cluster_means()`
  で呼び出す。R 無し/失敗時は従来の parquet 経路へ自動フォールバック（必ず動く）。
- **C（キャッシュ）**: RDS/ROI状態(`hne_overlay_state.json`)/化合物名 が不変なら前回CSVを即返す。
- **UI（進捗）**: 2段プログレス化（押下で即「CSV作成中…」表示＋ボタン無効化→完了/失敗を必ず
  表示）。本体を全体 try で囲み、失敗時も必ずメッセージ＋ボタン復帰（無反応を根絶）。
- 出力内容（群ラベル `{切片}_{ROI}_{クラスタ}`・化合物名列）は従来と同一。`build_groups_table`
  /`rename_export_columns` を `hne_overlay.py` に追加（純ロジック・テスト追加）。`build_region_cluster_export`
  はフォールバック用に温存。Dash 依存グラフは一方向（btn→A→trigger→B）で循環なし。

---

## 2026-06-16_ver7.6

### 修正: リロードのたびに空の「インタラクティブ解析」が開く不具合

タブ↔URL の双方向同期により、インタラクティブ解析タブにすると URL が `/app/interactive` に
書き換わりブラウザが保持するため、**リロードのたびに一瞬プロジェクト一覧→空のインタラクティブ
解析タブへ切り替わる**（解析未ロードのため中身は空）症状が出ていた。永続化ではなく完全に
URL 駆動のルーティング（`tab_url_routing.py`）の副作用。

- `_sync_tab_from_url` を初回ロードでは発火させない（リロードで URL からタブを復元しない）。
- `_route_app_url_to_analysis` で「未ロード状態（リロード/ランディングからの `/app/*`）では
  analysis に遷移させず URL を `/` に正規化」。→ リロードは必ずプロジェクト一覧に戻る・フラッシュ無し。
- タブ切替で URL が変わる機能（ブックマーク用）と、ブラウザ戻る/進むのタブ追従は維持。
- 追加の `url_bar.pathname` 出力は `allow_duplicate`（別ノード扱い）。Dash の依存グラフを
  レンダラ相当で解析し循環が生じないことを検証済み。

---

## 2026-06-16_ver7.5

### 修正: 領域テーブルの循環依存で UI 全体が重くなる/ローディング表示が出ない不具合

解剖×クラスタの「領域テーブル ↔ ポリゴン store」の双方向同期が
`hne_polygon_table.data ↔ hne_polygons_store.data` の**循環依存**を形成しており、ブラウザを
完全リロードすると Dash のクライアント描画器（dash_renderer）が `Dependency Cycle Found` で
クラッシュ → **ローディングスピナーが出ない・操作全体が重くなる**（UMAP 結果の表示も遅延）
症状が出ていた。**ver7.2 以前から潜在**していたもので、ブラウザのバンドルキャッシュが更新
（ハードリロード）されると顕在化する。

- `store→table`（同期）と `table→store`（編集反映）の2コールバックを1つに統合。`table.data`
  の自己参照は dash_renderer が内部で分割して扱うため循環にならない。store を書く commit
  （ポリゴン確定）・restore（個体復元）は自身で表も返すよう変更し、表の再生成を賄う。
- 機能（改名・グループ統合・行削除＋# 振り直し）は不変。
- Dash 2.18.2 の依存グラフを実コードから解析し、循環が解消したことを検証済み。

---

## 2026-06-16_ver7.4

### 機能: 解剖×クラスタの MetaboAnalyst 出力を全切片統合 ＋ ROI グループ統合 ＋ インタラクティブ出力に領域名列

- **A. MetaboAnalyst 用 CSV 出力（④）を全切片1ファイルに統合**: これまで切片（個体）ごとに
  分かれていた出力を、全切片まとめて1ファイルで出力するようにした。群ラベルを
  `{切片}_{ROI名}_{クラスタ}`（例 `E15_Brain_23`）に変更し、複数切片を1つの MetaboAnalyst
  入力にまとめられる。ROI 名が付いていない spot は除外。各切片の ROI は保存済みオーバーレイ
  （`hne_overlay_state.json` の対応点から affine を再計算）で割当てる。あわせて、生成 CSV を
  サーバ（`<RDS隣>/metaboanalyst_exports/`）にも保存して保存先パスを表示し、ブラウザの
  ダウンロードが届かない環境でも結果を取得できるようにした。
- **B. ROI ポリゴンを「グループ」番号で統合**: 領域テーブルに編集可能な「グループ」列を追加。
  同じ番号を入れた複数ポリゴンを1つの ROI として集計・色分け・出力する（名前が違っても可。
  空欄なら従来どおり領域名が同じものを統合）。
- **C. インタラクティブ解析のデータ出力に「領域名」列を追加**: TIMS（CSV/Excel/Parquet）・
  DESI（Excel）出力の**最終列**に各 spot の ROI（領域名）を付与する（未割当は空欄）。

純ロジック（`services/hne_overlay.py`）に `apply_rotation` / `apply_region_groups` /
`regions_from_overlay` を追加し、A・C で共有（テスト追加）。

---

## 2026-06-15_ver7.3

### 改善: 同じ領域名の複数ROIを同色で表示

離れた複数のポリゴンに同じ領域名（例：2か所の「脳」）を付けた場合、割当・集計では1領域に
合算される一方、**表示色がポリゴンの並び順基準**だったため別色で表示されていた。表示色を
**領域名基準**（`color_utils.get_cluster_color_map` 流用）に変更し、同名ROIを同色で表示する
ようにした（TIC・H&E 両図。割当・集計・保存ロジックは不変）。

---

## 2026-06-15_ver7.2

### 修正: 解剖×クラスタで個体を切り替えると ROI（ポリゴン）が消える不具合

ver7.0 の個体別保存/復元で、個体切替時に ROI ポリゴンだけが消えていた（重ね合わせ＝対応点/
アフィンは残存）。原因は、復元コールバック `hne_restore_sample` が `hne_polygons_store` を更新する
一方で DataTable（`hne_polygon_table`）を前個体の行のまま放置し、表→store の主コールバック
`hne_polygon_table_to_store` が**古い行 × 復元後の polys**で発火して復元ポリゴンを上書き →
自動保存が空をディスクに書き戻していたため（対応点は読み戻しループが無いため無傷だった）。

- 復元コールバックで `hne_polygon_table.data` も同時に復元し、store と表を同一ラウンドで整合させた。
- 表→store コールバックに過渡状態ガード（範囲外idx・重複idx・`len(rows) > len(polys)` なら no-op）を
  追加。改名・1行削除・全削除は従来どおり維持。

---

## 2026-06-15_ver7.1

### 修正: 解剖×クラスタで拡大中に点・領域を操作するとズームが戻る不具合

H&E / TIC(MSI) 図を拡大した状態で対応点クリックや領域確定をすると、サーバコールバックが新しい
figure を返すため Plotly がズーム/パンを既定にリセットしていた。両図の `layout.uirevision` を
個体(sample)キーで設定し、**同一個体内の点・領域操作ではズーム/パンを保持**、個体切替時のみ
新しい図にリセットするようにした（`hne_image_figure` には `hne_sample_select` を State 追加）。
明示レンジ（`range`/`scaleanchor`）は据え置き（uirevision 不変時はユーザ操作が優先）。

---

## 2026-06-15_ver7.0

### 機能追加: 解剖×クラスタ（H&E オーバーレイ）の個体別保存・ROI色分け・描画軽量化

- **個体別の永続保存／自動復元**: H&E画像・対応点・アフィン・回転・ROIポリゴンを個体(Sample)
  ごとに `<RDSと同フォルダ>/hne_overlay_state.json`（画像は `hne_overlay/<個体>.png`）へ保存し、
  個体切替・リロード・アプリ再起動で自動復元する（新規 `services/hne_persistence.py`、
  `label_persistence` と同じ atomic＋FileLock 方式）。従来は全個体共有のメモリのみで、個体を
  切り替えると前個体のROI・位置合わせが残る／リロードで消える問題を解消。
- **ROIの色分け＋領域名ラベル**: 確定ポリゴンを領域ごとに配色（`CLUSTER_PRESET_COLORS`）し、
  重心に領域名を表示（TIC・H&E 両図）。全ROIが同色でどの領域か判別できなかった問題を解消。
- **ポリゴン頂点クリックの軽量化**: 下書きを figure 全体の再構築から切り離し、clientside の
  `Plotly.restyle` で下書きトレースのみ部分更新。頂点クリック毎の go.Image 再描画・Loading
  スピナーの「リロード感」を解消（1点ずつクリックして頂点を置く操作は維持）。
- **回転変更時の対応点クリア**を「回転値が実際に変わった時のみ」に変更し、個体別復元と両立。

---

## 2026-06-14_ver6.1

### 修正: 新形式（列名＝化合物名）が `.txt` で登録された場合に解析が停止する不具合
ver6.0 は新形式の `.csv`/`.xlsx` を正規レイアウトへ組み替えるが、**登録/アップロードの過程で
`.csv` がタブ区切り `.txt` 化されてフォルダに入る**運用では、変換器(.csv/.xlsx 経路)を通らず
**新形式 `.txt` が未組み替えのまま R に渡り**、R が「1列目＝x座標」をスポットIDと誤読 →
同一 x がグリッドで重複 → `duplicate row.names` で `Execution halted` となっていた。

- **修正（`desi_converter`）**：`normalize_desi_txt()` を追加。既存の `.txt` が新形式
  （先頭セルが `x`/`y`）なら、その場で正規レイアウト（先頭空行＋3行目=化合物名＋連番ID）へ
  組み替える（区切りは tab/カンマ自動判定）。解析起動前フック `prepare_desi_data_folder` で
  各サンプルの `.txt` に適用。これで **新形式 .txt / .csv / .xlsx の3経路すべて**が解析時に
  正規化される。
- **冪等・後方互換**：組み替え後は先頭行が空になり再検出されない（二重組み替えなし）。
  従来形式 `.txt`（先頭空行）は `_is_named_format=False` で**一切触らない**。
- **R 無改修**（ver6.0 の `read_desi_data` ガードで足りる）。下流処理も無改修。
- **テスト**：新形式 .txt（tab/カンマ）の組み替え・ROIラベル＋空セル混在の保持・冪等・
  従来形式 .txt 不変・`prepare_desi_data_folder` 統合を追加。計78テスト全合格。
  実データ（`E1521_Heart1.csv` をタブ .txt 化）でも組み替え後に化合物名・`ROI=Heart1` を
  保持し `duplicate row.names` が解消することを確認。

---

## 2026-06-14_ver6.0

### 機能追加: DESI 入力に「列名＝化合物名」の1行ヘッダ形式を追加対応
DESI 登録データとして、従来の4行ヘッダ（空/番号/Q1/Q3）形式に加えて、**1行ヘッダで列名が
`x, y, 化合物名_情報1_情報2`（例 `Acetylcholine_15_10`）の形式**でも登録・解析できるようにした。
特徴量名には**化合物名のみ**（列名の最初の `_` より前。例 `Acetylcholine`, `GSH-POS`）を使用する。

- **方式（正規化の拡張）**：`desi_converter` が新形式を**自動検出**し、内部で従来の正規 `.txt`
  （4行ヘッダ）へ**組み替え**る。スポットID列が無いため自動採番し、`x`/`y` 列を座標として扱う。
  末尾に領域ラベル列（例 Tumor/Normal）があれば **ROI** として領域別解析に利用できる。
- **R 側（`260422_DESI-UMAP_Template_v14.R`）**：Q3(プロダクト m/z)行が無い新形式では、
  特徴量名に**化合物名（pre_masses）をそのまま採用**する 1 行分岐を `read_desi_data` に追加。
  従来形式（Q1/Q3 あり）は `Q1-Q3` 名のまま挙動不変。
- **後方互換（最重要）**：先頭行で排他判定（従来＝先頭行が空 / 新＝先頭セルが `x`,`y`）。
  従来の `.txt`・ver5.0 の同一レイアウト Excel/CSV は**無改修で従来どおり**動作。下流
  （Volcano/MSI像/DEG/MRM注釈/Cluster Filter 再解析/キャリブレーション）は名前形式に
  非依存のため**無改修**。
- **テスト**：`tests/test_desi_converter.py` に新形式（組み替え・化合物名抽出・ROIラベル列・
  自動変換経由）と回帰（従来形式は組み替えない）を追加。計72テスト全合格。
- 対応は csv/xlsx（Excel 運用）。x/y 列はヘッダ名 `x`/`y` 前提、重複化合物名は `make.unique` で連番化。

---

## 2026-06-14_ver5.0

### 機能追加: DESI 登録データを Excel(.xlsx)/CSV(.csv) でも受け付け可能に
従来 DESI 解析の登録データはタブ区切りの `.txt` のみ対応していたが、**中身のレイアウト（先頭4行
ヘッダ + ピクセルデータ）が同一であれば Excel(.xlsx/.xls) / CSV(.csv) でも登録・解析できる**ように
した。フォルダに置くだけでサンプル一覧に表示され、解析時に内部で自動変換される（専用 UI の追加なし）。

- **方式（正規化）**：アップロードされた `.csv`/`.xlsx` を内部で正規 `.txt`（タブ区切り・同一
  レイアウト）へ変換してから既存パイプラインに渡す。これにより **R 本解析（`read_desi_data`）も
  Python の各リーダーも無改修**。行・セル構造は一切正規化せず保持（ragged 行・空 1 行目を維持）。
- **新規 `app/services/desi_converter.py`**：
  - CSV はクオート対応でパースしてタブ区切りに再出力（ラベル内カンマを保持／m/z 小数は文字列の
    まま桁落ちゼロ）。
  - Excel は openpyxl 直読でセルを原表現で文字列化（指数表記・桁落ちを抑止）。
  - 冪等（変換済み `.txt` が変換元より新しければ再変換しない）。ユーザーが手で置いた本物の `.txt`
    は上書きしない。読取専用フォルダは staging に集約して R へ渡す。
- **配線**：`list_msi_files` が `.csv`/`.xlsx` の stem も列挙（`.txt` 優先で重複除去）。
  `read_desi_roi_list` は `.txt` 不在時に自動変換して ROI を読む。解析起動
  （`generate_v8_config` / `generate_cluster_filter_config`）直前に全選択サンプルの `.txt` を保証。
- **テスト**：`tests/test_desi_converter.py` を追加（CSV/Excel 変換、ROI 検出、カンマ入りラベル、
  m/z 精度、ragged 行、空 1 行目、冪等性、一覧重複除去、ユーザー .txt 優先、自動変換の統合）。
- **前提**：Excel は数式なし・先頭シートを使用。`.xls`（旧形式）は環境により xlrd 等が必要なため
  `.xlsx` を推奨。

---

## 2026-06-09_ver4.35

### 修正: ヒートマップ生成（DoHeatmap）で scale.data が無く解析が halt する不具合
ver4.34 でマーカー OOM を解消し、解析はマーカー（全クラスタ）まで到達するようになったが、その後の
**`DoHeatmap`（ヒートマップ生成）で停止**していた（`No requested features found in the scale.data
layer for the Spatial assay` → Execution halted）。

- **原因**：`DoHeatmap` は `scale.data` 層を使うが、その層が空だった。slim RDS は容量削減のため
  `scale.data` を捨てて保存する（→ RESUME 時に空）。さらに ver4.32 のメモリ対策 `diet_seurat_safe`
  （PCA 後に scale.data 解放）により**新規実行でも空**になる。tryCatch 無しのため失敗で解析全体が halt。
- **修正（TIMS `260422_..._slim.R`）**：DoHeatmap 直前で、サブセット（最大1000細胞）に対し
  **対象遺伝子（上位マーカーのみ）を `ScaleData` し直して** scale.data を復元（軽量・結果不変）。さらに
  ヒートマップは出力必須でないため **tryCatch で握りつぶし**、失敗してもスキップして解析を継続。
  `run_downstream_analysis` は Harmony/RPCA 両方で共有のため 1 箇所で両方に効く。
- **修正（DESI `260422_..._v14.R`、3箇所）**：各 DoHeatmap 直前に同様の `ScaleData(features=…)` を追加し、
  RESUME 等で scale.data が空でも描画できるように補完。
- ver4.32 の diet（メモリ削減）は維持し、scale.data はヒートマップ用に**必要分だけ復元**する方針。

---

## 2026-06-09_ver4.34

### 修正: 大規模解析が FindAllMarkers（マーカー検出）で OOM 強制終了する不具合
ver4.32 で PCA 段の OOM を解消した結果、解析は PCA/Harmony/UMAP/クラスタリングまで通過するように
なったが、その後の **FindAllMarkers（マーカー検出）で OOM kill** されていた（ログが「Calculating
cluster 3」で無言で途切れ、R の `max used` が 23GB）。

- **原因**：FindAllMarkers を `plan(multisession, workers=4)` で実行しており、**4 ワーカーが各々
  203k spot の Seurat オブジェクトを丸ごとコピー**してメモリが爆発、swap 込み上限(40GB)を超えて
  kill されていた。
- **修正**：FindAllMarkers を**逐次実行（`plan(sequential)`）**に変更。presto 導入済みのため逐次でも
  高速で、**マーカーの結果はワーカー数に依らず不変**。TIMS（`260422_..._slim.R`）と DESI
  （`260422_..._v14.R`、3 箇所）の両方を修正。
- **追加**：Harmony / PCA / RPCA のリトライループに `gc()` を入れ、失敗試行の中間オブジェクトを解放
  して常駐メモリを下げる（リトライ時のメモリ累積を抑制）。

---

## 2026-06-09_ver4.33

### 改善: 解析・エクスポートの高速化（結果不変）
律速調査（解析パイプライン全体）の結果から、結果を変えずに効く安全な高速化を実施。

- **行列計算のスレッド数（P2）**：`docker-compose.yml` の `environment` に `OPENBLAS_NUM_THREADS=5` /
  `OMP_NUM_THREADS=5` / `MKL_NUM_THREADS=5` を追加。`analysis_runner.py` は未設定時 4 を既定にしていた
  ため、PCA/Harmony/RPCA が `cpus:'6'` に対し 4 コアしか使えていなかった。5 に引き上げ（UI 応答に 1 コア
  残す折衷。最速にしたいなら 6、UI 優先なら 4）。
- **PPTX heatmap の無駄な parquet 読み解消（P3）**：`interactive_pptx.py` の片方の heatmap 関数が、
  feature の存在確認のために feature 数ぶん `pd.read_parquet(columns=[g])` を実行して結果を捨てていた。
  もう一方と同様 `pq.read_schema().names` の **1 回の列名チェック**に置換（結果同一、エクスポート短縮）。
- **補足**：`presto`（FindAllMarkers の高速 Wilcoxon backend）は既に `install_r_packages.R`＋`Dockerfile`
  で導入設定済み（要サーバ確認）。`expression_matrix` の全読み箇所はスペクトル平均/全 feature 出力に
  全列が必要なため、列サブセット化は見送り（誤りを避けるため）。

---

## 2026-06-09_ver4.32

### 修正: 大規模解析(203k spot)が PCA で OOM 強制終了する不具合（メモリ対策）
TIMS UMAP 解析が「Centering and scaling data matrix」直後（RunPCA）で**メモリ不足により強制終了
（OOM kill）**していた（ログが無言で途切れ status=error）。16GBホスト・12GBコンテナで、密な MSI
データ（203,078 spot × 約2,700 m/z）の counts/data/scale.data が同時常駐し ~12GB を超えるのが原因。

- **メモリ退避（docker-compose.yml）**：`memswap_limit: 40g` を追加。`mem_limit` 12g を超えた分を
  **ホストスワップ（32GB）へ退避**できるようにし、PCA/UMAP の一時スパイクで kill されないようにした。
- **R ピークメモリ削減（`260422_..._slim.R`）**：Harmony/RPCA 各パスで **RunPCA 直後に
  `diet_seurat_safe()` で `scale.data` を解放**（PCA 後は不要）。counts/data/reductions は保持し、
  失敗時は原本を返す既存ヘルパーを再利用するため**結果は不変・安全**。後続の重い UMAP/クラスタリング
  （203k cell）のメモリを下げる。
- 補足：`ScaleData` は既定で可変 feature のみを対象にするため、「ScaleData を可変 feature に限定」は
  本データでは no-op（適用せず）。実効のある上記2点を採用。

---

## 2026-06-09_ver4.31

### 修正: SCiLS peak-list の Name 内 `;` で化合物注釈が丸ごと欠落する不具合
SCiLS の Feature list（peak-list）を Intensity/Spot と同梱して変換しても、化合物名・分子式などの
注釈が一切付かないことがあった。

- 原因：peak-list の `Name` 欄は `adduct_family=mass_only;n=2;adducts=[M-H]-,[M]-;peaks=12,47` の
  ように**区切り文字 `;` をフィールド内部に含む**（未クオート）。`_read_peaklist`
  （`scils_converter.py`）の `pd.read_csv(sep=";")` が当該行で列数不一致 → ParserError。これが
  呼び出し側の `try/except` で握りつぶされ、**変換は完走するが注釈なし**（数値列名のみ・sidecar
  未出力）になっていた。実ファイルでは 1536 feature 中 269 行が該当し、全注釈が落ちていた。
- 修正：`_read_peaklist` を**ヘッダ列数ベースの手動パース**に変更。「Name より後ろの列数は固定」
  という構造を使い、超過した区切りトークンを Name に再結合して原文を復元（区切りを内部に含み得る
  のは Name 列のみのため安全）。`;`/`,` どちらの区切りでも動作し、既存のカンマ peak-list は不変。
- テスト：`_read_peaklist` の単体（`;`＋adduct_family 行の復元）と、`;` 区切り peak-list での
  変換 E2E（`n_annotated`・sidecar 生成）を追加。

---

## 2026-06-09_ver4.30

### 変更: H&E のポリゴン描画を「クリックで頂点配置」方式に
ポリゴンモードで H&E をクリックしても反応しなかった。原因は Plotly の `drawclosedpath`（ドラッグ
でなぞるフリーハンド）を使っており、クリックでは頂点を置けない仕様だったため（コードのバグでは
ない）。解剖領域を正確に囲めるよう、対応点モードと同じクリック操作に変更した。

- **操作**：ポリゴンモードで H&E をクリック → 頂点を順に配置（下書きを線＋マーカーで表示）→
  「領域を確定」で閉じて登録 → 続けて次の領域を描ける。「頂点を取り消し」「下書きクリア」も追加。
  dragmode を `pan` にし、クリック=頂点・ドラッグ=パン・ホイール=ズームで共存。
- **再利用**：対応点 `hne_capture_landmark` と同じ `clickData` 累積パターン。確定後の判定・集計・
  出力（`transform_polygons`/`assign_regions`/`build_region_cluster_export`）は変更なし。
- **領域の管理**：領域名を `hne_polygons_store`（`{"name","vertices"}`）に保持するよう変更し、表での
  **改名・行削除がインデックスずれなく**反映されるように。旧フリーハンド取り込み（relayout shapes）と
  `_named_polygons` は廃止。
- テスト：確定ポリゴンの store 形が変換→割当を通り名前が保持されることを単体追加。

---

## 2026-06-09_ver4.29

### 修正: H&E タブで TIC をクリックしても対応点が登録されない不具合
TIC 側に対応点を打っても反応せず（TIC 0 のまま、× も出ない）位置合わせができなかった。

- 原因：TIC 主トレースに `hoverinfo="skip"` を指定していたため。Plotly では `skip` は
  **クリック/ホバーイベントを発火させない**（`none` はラベルを出さずイベントは発火する）。
  H&E 側は `go.Image` でこの指定が無いため反応していた（＝非対称な挙動だった）。
- 修正：TIC 主トレースを `hoverinfo="none"` に変更（インタラクティブ解析の clickable
  トレースと同方針）。これで TIC クリックが拾え、対応点が登録され、TIC 上にも × が表示される。

---

## 2026-06-09_ver4.28

### 修正: H&E 画像が表示されない不具合 ＋ 機能: MSI(TIC) 画像の回転
H&E オーバーレイ（H&E タブ）の 2 点を対応。

- **【修正】H&E が真っ白で表示されない**：アップロード画像（data URI）を**生のままブラウザに渡して
  いた**ため、実体が TIFF（拡張子だけ .png に変更したものを含む）や特殊モード PNG（16bit/CMYK/
  パレット）だと**ブラウザがデコードできず真っ白**になっていた。`hne_store_image` で **PIL がデコード
  した画素から 8bit RGB PNG に再エンコード**して格納するよう変更（最大辺 ~2000px に縮小も実施）。
  これで元形式に依らず確実に表示される。縮小は対応点アフィンが吸収するため割当には影響しない。
- **【機能】MSI 回転（粗い向き合わせ）**：左コントロールに**自由角スライダー（0–360°）＋ 左右/上下
  反転**を追加（`hne_rotation_store`）。TIC の (SpatialX, SpatialY) に
  `interactive_spatial._transform_coords`（重心基準・任意角）を適用し、表示・割当・エクスポートへ
  一貫適用。spot とポリゴン双方に同量の回転がかかるため**領域割当は回転不変**。回転・反転を変えると
  旧対応点・アフィンは自動クリア。
- テスト：`assign_regions` の**回転不変性**を単体追加（spot とポリゴンへ同一回転を適用しても割当が
  一致することを確認）。

---

## 2026-06-06_ver4.27

### 機能（フェーズ2-4）: H&E 位置合わせ・ポリゴン領域・領域×クラスタ集計・エクスポート
ver4.26 の H&E タブに、対応点位置合わせ／ポリゴン領域指定／集計／MetaboAnalyst 出力を追加。

- **② 位置合わせ（ランドマーク）**：H&E を `go.Image` トレースとして描画し `clickData` で画素座標を、
  TIC（散布）の `clickData` で MSI 座標を取得。3 ペア以上で `hne_overlay.estimate_affine` により
  アフィン推定（残差 RMS を表示）。操作モード（対応点／領域を描く／操作）を切替。
- **③ ポリゴン領域**：H&E 上で `drawclosedpath` → `relayoutData['shapes']` を取り込み、表で命名。
  「割当」でアフィン変換 → 点-内包判定（`assign_regions`）で各 spot に領域付与し、領域×クラスタの
  構成表（spot 数・領域内%）を表示。変換後ポリゴンは TIC 側にも重畳表示。
- **④ エクスポート**：`region_cluster`（例 脳_cluster1）群ごとの平均強度を、化合物名優先（ver4.21
  アノテーション）の列で CSV 出力（MetaboAnalyst 入力想定）。強度は `expression_matrix.parquet` を使用。
- 座標系：本タブは SpatialX/SpatialY をそのまま使い割当と一致（H&E の向き差はアフィンが吸収）。
- 注意：UI 挙動はブラウザでの確認が必要。エクスポートの強度は既定アッセイ依存（RPCA の integrated は
  補正値のため、元強度が必要なら Harmony 結果を推奨）。1 個体ずつの運用を想定。

---

## 2026-06-06_ver4.26

### 新機能（フェーズ1）: 解剖×クラスタ（H&E オーバーレイ）タブ
UMAP クラスタの**解剖学的局在**を判断し、将来 **MetaboAnalyst** へ「領域×クラスタ」単位で
データを渡すための新タブを追加。インタラクティブ解析で読込済みの `plot_data`（spot ごとに
空間座標・クラスタ・UMAP が同居。実データで 1:1 対応を検証済み）を再利用する。

- **新規 `services/hne_overlay.py`（純ロジック・テスト済み）**：ランドマーク→アフィン位置合わせ
  （`estimate_affine`/`apply_affine`/`invert_affine`）、ベクトル化点-内包判定（`points_in_polygon`、
  matplotlib/shapely 非依存）、領域割当（`assign_regions`、NA 空間は除外）、領域×クラスタ集計
  （`region_cluster_counts`）、MetaboAnalyst 用エクスポート（`build_region_cluster_export`、
  region×cluster 群平均・化合物名優先）。`tests/test_hne_overlay.py`。
- **フェーズ1 UI**：新タブ「解剖×クラスタ (H&E)」（`layouts/hne_overlay_tab.py` /
  `callbacks/hne_overlay_callbacks.py`、`main_layout.py`・`main.py` に登録）。個体(Sample)選択／
  TIC 空間表示（TotalCount 濃淡）／H&E 画像アップロード(`dcc.Upload`)・表示／不透明度。
- 続くフェーズで、対応点による位置合わせ・ポリゴン領域描画・領域×クラスタ集計表・エクスポートを追加予定。

---

## 2026-06-06_ver4.25

### 改善: 領域アノテーション重複エラーのメッセージを具体化
SCiLS 変換で領域アノテーション（複数の Region CSV）に**同一 spot の重複**があるとき、
従来は `同一 spot が複数 annotation に: [27722, …]` という spot 番号の羅列のみで原因が分かり
にくかった。**どのファイルがどの既存領域と重複しているか・件数・全 spot が重複＝複製領域の
可能性**を明示するメッセージに改善（`build_annotation_map`）。

例（E16 と E18 が同一スポット集合だった実ケース）:
> 領域アノテーションが重複しています: 'E18' (E18.csv) の 51946 spot が既存領域 ['E16'] と
> 同一です（'E18' の全 51946 spot が重複＝重複/複製された領域の可能性が高い）。…
> 同じ領域を二重にエクスポートしていないか、SCiLS の ROI 定義をご確認ください。

※ これは検証強化のみで、重複領域は引き続きエラー（データ側で要修正）。

---

## 2026-06-06_ver4.24

### 修正（真因）: SCiLS 変換の途中終了は polars の CPU 非互換クラッシュが原因
ver4.23 で同期化・メモリチェックを入れた後もサーバ実機ログで真因が判明:
**polars がサーバ CPU の必須命令（`lzcnt`）を欠くため Phase A の実行中に
ネイティブクラッシュ（SIGILL）し、アプリプロセスごと落ちて Docker が再起動**していた
（→ 変換が途中終了し 0byte の `*_temp.parquet` が残る）。メモリは潤沢（空き ~14GB / CSV ~2.9GB）で
無関係だった。ネイティブクラッシュのため Python の try/except では捕捉できない。

ログ証跡:
```
polars/_cpu_check.py: Missing required CPU features: lzcnt
Continuing to use this version of Polars on this processor will likely result in a crash.
Phase A エンジン: polars (streaming sink)   ← この直後にプロセスが落ち再起動
```

対応:
- 依存を **`polars` → `polars-lts-cpu`** に変更（`requirements.txt` / `pyproject.toml`）。
  古い CPU 向けにビルドされた版で、`lzcnt`/AVX2 等を要求せずクラッシュしない。**`import polars`
  のままで API 互換**のためコード変更不要。
- 保険として環境変数 **`SCILS_NO_POLARS=1`** を追加。指定時は polars を使わず pyarrow 経路で
  Phase A を実行する（万一 polars が問題でも変換可能）。
- ver4.23 の同期実行・メモリ事前チェック・Phase A の temp 後始末（try/finally）はそのまま維持
  （堅牢性向上として有効）。

デプロイ: `polars-lts-cpu` を入れるため**イメージ再ビルドが必須**
（`docker compose ... up -d --build`）。

---

## 2026-06-06_ver4.23

### 修正: SCiLS 変換が大規模データで「途中終了」する問題（ver4.22 のバックグラウンド化を撤回）
ver4.22 で導入した変換のバックグラウンド実行（DiskcacheManager）が、大きいデータ（例: 3GB の
Intensity CSV）で**途中終了したように見える**原因になっていたため撤回し、確実に動く同期実行へ戻した。

- **原因**: DiskcacheManager は `expire=300`（5分）のため、5分を超える変換ではジョブ追跡キャッシュが
  失効し UI が結果を取りこぼす。さらにワーカープロセスの異常終了（OOM 等）が UI に伝わらず無言で
  終わる。本コードベースは元々この理由で `background=True` を避けていた（interactive_callbacks.py /
  interactive_data_export.py のコメント参照）。
- **対応**:
  - `run_scils_conversion` を**同期実行に戻す**（`background`/`running`/`progress` を撤去）。結果欄の
    スピナー（`dcc.Loading`）は残置。進捗バーは撤去（堅牢な進捗表示は別途、検出済みの
    サブプロセス＋ポーリング方式で再導入予定）。
  - 変換前に**簡易メモリチェック**を追加（`_check_conversion_memory`）。Intensity CSV サイズに対し
    空きメモリが不足する場合は**明示的なエラー**を返し、無言の OOM（途中終了＋0byte 一時ファイル
    残留）を防ぐ。
  - **Phase A を try/finally の内側へ移動**し、Phase A が失敗しても一時 Parquet を確実に削除する
    （0byte の `*_temp.parquet` 残留を解消）。
- ver4.22 の高速化（一時 Parquet を snappy 化／Phase B のキャスト一括化／peak-list 結合の
  searchsorted 化）は**そのまま維持**。

---

## 2026-06-06_ver4.22

### 改善: SCiLS 変換の「無反応」解消（進捗バー）＋ 変換の高速化
「変換実行」を押すと（大きいデータで）反応がないように見える問題を解消し、変換中の進捗を表示、
さらに変換そのものを高速化した。**出力 parquet の内容は不変**。

- **無反応の解消＋進捗バー**: 変換コールバック `run_scils_conversion` を**バックグラウンド化**
  （`background=True`／既存 PPTX 出力と同じ DiskcacheManager）。`running=` で変換中は「変換実行」
  ボタンを無効化、`progress=` で進捗バーを更新。`convert_scils_to_parquet` に `progress_cb` を追加し、
  Phase B のチャンクループで「書き込み中… N/M spot」を逐次表示。ポーリング方式のため、リバース
  プロキシ（Caddy）の 600s タイムアウトによる長時間変換の打ち切りも回避。モーダルに進捗バー＋
  結果欄のスピナー（`dcc.Loading`）を追加。
- **高速化（結果不変）**:
  - 使い捨ての一時 Parquet を `zstd` → **`snappy`** に（削除される中間ファイルなので保存コスト無し、
    Phase A 書込＋Phase B 読込の CPU を削減）。
  - Phase B の転置書き込みで `float64→float32` を**列ごと（m/z 本数ぶん）**に行っていたのを
    **ブロック 1 回**へ集約し、`pa.array` をゼロコピー化。
  - peak-list 結合 `build_feature_annotation_table` を最近傍 `O(F×P)` → **`np.searchsorted` の
    `O(F log P)`** に（大規模 peak-list の変換を高速化、割当結果は同一）。

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
