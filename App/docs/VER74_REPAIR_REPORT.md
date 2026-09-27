# ver74.0 修正・検証記録

2026-09-28。対象は main `732ff0207dffff7875515ac65b245d02970d170b`（ver73.0）と、同SHAを適用元とする添付ver74.0候補。元ZIP・既存データ・mainは変更せず、候補の機能と監査修正を作業ブランチへ反映した。

## 動作変更

- 全切片を登録したParquetを保持し、解析選択は`selected_section_ids`で指定する。未選択切片の必須情報も検査する。空選択・未知ID・重複IDは暗黙に全選択へ変えない。
- 固定registry、UI draft、解析後の表示/群overlay、数値処理の署名を分離する。raw登録編集は新revision、直接Parquet/解析後の編集は明示overlayとする。原本・旧revision・元解析の条件記録を上書きしない。
- 再解析の選択上限を元RDSに含まれる切片へ固定する。全登録情報を保持していても、元RDSに含まれない切片の追加、IDの置換、結合・分割による別切片への変更はPython/UI/Rの各段で拒否する。
- 新規TIMS feature IDは6桁。`100.000001`と`100.000002`は区別する。真の6桁名衝突は明示拒否し、丸めだけで合算しない。既存RDSの5桁IDは変更せず、旧形式export読込に互換経路を残す。
- 共有リンクは対象結果の**閲覧専用**になる。保存、元データexport、変換、再解析、管理操作は解析者ログインが必要。server側でcallbackの実関数・token・対象結果・RDS・手法を照合し、画面にも制限を示す。
- `backup.sh`/`restore.sh`にPython 3が必要。実containerのmountからCompose接頭辞付きvolumeとhost bindを解決する。全対象取得成功前に旧backupを期限削除しない。復元は事前展開→停止→旧内容をrecovery volumeへ保全→置換→同じcontainerの再開とする。保全volumeは自動削除しない。稼働中backupはファイル単位なので、解析の止まった時間帯で取得する。
- 監視CLIの利用者停止はexit 6、実行中/完了は0。export診断は`--plot-data`、`--rds`、`--instrument`で1解析を指定し、全cacheの名前を混ぜない。

## 計画との対応

| 項目 | 実装と回帰の要点 |
|---|---|
| A01 | 全registryから選択IDで派生表を生成。subset/cache/空選択/未知・重複IDを実I/Oで検査 |
| A02–A03 | 編集→確認→適用→再openで同じdraft/確定値。背景revision競合検出、手動変換override復元 |
| A04 | 共通metadata更新と固定registry/overlay分離。結果群編集と再解析、raw新revisionで旧bytes不変 |
| A05–A06 | 低レベルAPI・CLI・worker・直接Parquetの共通完全性gate。破損footerをlegacyへ降格しない。false文字列を厳密解釈 |
| A07 | Python/R/CSV/export/校正の6桁契約、校正観測値を保存前に丸めない、旧RDS互換 |
| A08–A09 | schema2 export receipt、processed/float64→32の非lossless区分、MS1再読込、診断キー保存 |
| A10 | reduction/metadata/full署名。明示したcheckpointが一致する場合だけ数値処理を再利用し、metadata・下流統計を更新。別runを自動探索しない |
| A11 | ppmの有限非負検証、手動既定5/通常0の説明、HELP、依存、実I/O/R必須CI |
| B01 | callback POST認証、共有のサーバスコープ制限と閲覧allowlist。実HTTP試験 |
| B02–B03 | 自アプリ出力MS1の再取込。管理assetの再パックを列挙時/直接指定時に拒否 |
| B04–B05 | wrapper Seurat抽出、元sourceの校正キーでKEEP/EXCL再解析。適用不能な係数は明示停止 |
| B06 | RDS同dir一時書込・readback・rename確認、backup copy/hash確認。失敗時は旧成果物保持 |
| B07 | DESI空欄名の列位置保持。末尾省略だけ0補完、途中の欠損は拒否。Python表示とR読込を同期 |
| B08 | 実mount/絶対bind/事前展開/旧内容保全/rollback。同じcontainerを再開し、変更後設定で再作成しない |
| B09–B11 | 後付け分子名overlay、要求手法とDEG index照合、H&E保存名の衝突防止 |
| B12–B13 | Methodsは実出力receiptと対象RDSのDE記録だけ。QEA投入を推測しない。OpenAPI全体validator |
| B14–B15 | R比較でcell/feature/reductionの欠落を拒否。小ROI安定性診断の標本/近傍上限と明示skip |
| B16–B17 | 監視終了コード・source ID/manifestでのexport診断。SCiLS非有限値/overflow/重複6桁列名拒否 |
| 潜在3件 | 非格子の誤snap、同一試料の独立反復扱い、trustworthinessの無効kを拒否するguard |

旧Shiny保管版の全面移植や大規模実データのメモリ最適化は含めない。

## 検証

最終全体試験（RをPATHへ設定、ブラウザ/実データマーカーも除外せず収集）は **3,471 passed / 10 failed / 34 skipped / 2 xfailed**、278.22秒だった。全件成功とは扱わない。

| 範囲 | 最終結果 |
|---|---|
| 追加Python CIの対象 | 312件成功、失敗・skipなし（全体JUnitから対象moduleを集計） |
| 追加R/process CIの対象 | 102件成功、下記の実プロセス10件失敗、skipなし（同集計） |
| `test_imzml_autoimport_pipeline.py` 実プロセス | lease/PID identityと子孫停止の2件失敗 |
| `test_job_registry.py` 実プロセス | watcher/同時起動/既存R検出の8件失敗 |
| スキップ | Chromiumなし31件、任意のpyflakes未導入3件 |
| 期待失敗 | 既存のMRMパス注入/必須置換検査2件 |
| 静的検査 | Python 575ファイル（テストを含む）AST、assetsのJavaScript 10本、shell 5本に構文エラーなし。R 35本parse成功 |
| JavaScript動作検査 | Node 1テスト、26検査・14 Plotly操作が成功 |

10件の実プロセス試験では、Popenが返した子PIDをpsutilが参照できず、NoSuchProcess、lease作成失敗、停止時timeoutが発生した。製品のPID安全検査を緩和せず、同じ試験を通常LinuxのCIで再確認する。

認証HTTP試験がDashのグローバルcallback登録を消費していた試験隔離の問題も全体実行で発見し、隔離fixtureを修正した。修正後の認証→既存callback試験の順序実行178件と、上記最終全体実行で解消を確認した。

再現コマンド（Appディレクトリ。記載バージョンのPython環境を使用し、RscriptをPATHへ追加）:

```bash
PYTHONPATH=. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests -q --junitxml=full.xml
node --test tests/js/*.js
Rscript -e 'invisible(lapply(list.files("Script", pattern="[.]R$", recursive=TRUE, full.names=TRUE), parse))'
```

以下は実測した検証範囲。

- Python 3.12、NumPy 2.3.5、pandas 2.2.3、pyarrow 19.0.1、pyimzML 1.5.5、Dash 2.18.2を使用。
- Rは隔離環境の4.5.3、Seurat 5.5.1、Arrow 25.0.0、Matrix 1.7.6、qs 0.27.3、qs2 0.2.1、Harmony 2.0.5。本番Dockerの完全再現ではない。
- 合成imzML/ibdとParquetを実際に読書きし、全pixel登録、subset選択、登録revision変更、破損拒否、6桁質量、MS1再出力を検証。
- 実TIMSスクリプトの合成240画素/60featureから120画素/59featureを解析し、PCA・UMAP・cluster・DEG・CSV出力を完走。metadataのみ変えた明示checkpoint再開でcounts/data/PCA/UMAP/cluster一致、群・表示名更新、DEG再計算を検証。
- qs2/qs/gzipのSeurat往復でcounts/metadata一致。rename/copy失敗、破損writer、DESI列位置、小ROIを実Rで試験。App/ScriptのR 35本をparse。
- callback実関数の状態遷移、認証は隔離Flask/DashへのHTTP POSTで検証。Docker復旧は記録mockで操作順序・rollbackを検証。
- 新規回帰を原版/修正を戻した隔離コピーへ適用して、旧不具合で失敗することを確認。全suite成功数と個別suite成功数は重複するため合算しない。

## 未検証と受入条件

- **ブラウザE2E未実施。** Playwright導入は成功したがChromium/headless-shellの公式CDN取得がZIP不完全で失敗した。callback試験は実ブラウザ描画/操作の代替とは扱わない。
- 229 MB実imzML、本番Dockerの変換→解析→画面操作、Windows/PowerShell、本物のvolumeバックアップ復元、大規模RDS保存readbackの速度/peak RSSは未測定。
- DESI全6切片のHarmony/RPCA数値E2Eは未実施。reader、分岐、保存、最小Seurat試験は実施した。
- 本実行環境では一部の子プロセスPIDがpsutilから見えず、process identity/treeの実プロセス試験が失敗する。この制約を製品側のPID検査緩和で回避しない。通常Linux CIで同試験を必須にする。
- CIはPython実I/O/認証契約とR/Seurat/process契約を追加し、必須試験のskipを成功扱いしない。CIが未実行/失敗、ブラウザ・本番復元gate未完の間は配備可能とは判断しない。
- 実データ・旧revision・既存解析結果を今回の作業で変更していない。PRのmerge/配備も別操作である。
