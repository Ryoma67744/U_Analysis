# エクスポートデータ 形式説明書（インタラクティブ画面 / MetaboAnalyst 用）

本書は、MSI 解析アプリが出力する各データファイルの**形式・列/行の配置・値の意味**を、DESI / TIMS の両方について詳述する。

> **使い方（生成AIに渡す場合）**
> 下の各「■ コピー用ブロック」を**丸ごとコピー**し、出力データファイルと一緒に生成AIへ添付すると、
> AI が各列・各行・各値の意味を正確に解釈できる。ブロックは**それぞれ自己完結**しているので、
> 扱うファイルに応じて必要なブロック（TIMS / DESI / MetaboAnalyst）だけをコピーすればよい。

用語（共通）:
- **スポット / spot / ピクセル** = MSI の1測定点（＝Seurat の1 cell）。
- **統合手法** = UMAP/クラスタリングのバッチ統合手法（`Harmony` / `RPCA` / `PCA`）。**強度には影響せず、クラスタ割当のみが手法で変わる**。
- **UMAP クラスタ** = 各スポットが属するクラスタ番号（例 `cluster3`）。統合手法ごとに変わる。
- **annotation** = 変換済みデータ由来のスポット注釈（SCiLS 由来）。切片/サンプルの識別に使う。
- **領域名 / ROI** = H&E オーバーレイでユーザーが描いた領域ラベル（例 `Brain`）。annotation とは別物。

---

## ■ コピー用ブロック 1 : TIMS / インタラクティブ画面「データ出力」

```text
【MSI解析アプリ 出力ファイル形式：TIMS / インタラクティブ画面「データ出力」】

ファイル名: UMAP_cluster_TIMS.xlsx / UMAP_cluster_TIMS.csv / UMAP_cluster_TIMS.parquet
           （出力時に xlsx / csv / parquet から選択。xlsx はシート名 "Data"）
概要: TIMS の元MSIデータ（全スポット × 全m/z強度）に、UMAPクラスタ列と領域名(ROI)列を付加した
      1つの表。全切片(セクション)を縦に連結した単一テーブル。

■ 行の向き
  ・1行 = 1スポット（ピクセル/測定点）。
  ・1行目 = 列名（ヘッダー行）。2行目以降 = 各スポットのデータ。

■ 列の並び（左から。この順で格納される）
  [1]   id          : スポット連番（整数）
  [2]   x           : 空間X座標（数値）
  [3]   y           : 空間Y座標（数値）
  [4..N]<m/z 列群>   : 各 m/z の強度（数値, float32）。列は数百〜数千。
                       列名は「化合物名付き」(例: Glutathione_611.1439 | [M-H]-)
                       または m/z 数値文字列(例: 611.143900)。値=そのスポットでのその m/z の強度。
  [N+1] annotation   : スポットの注釈ラベル（文字列, SCiLS由来）。切片/サンプルの識別子。
  --- ここから下はアプリが追加する列（右端に付く）---
  [UMAPクラスタ列]   : ・複数の統合手法を出力した場合 → 手法ごとに1列。
                          列名 = 手法名（"RPCA" / "Harmony" / "PCA"）。値 = クラスタ番号(例 cluster3)。
                       ・統合手法が1つの場合 → 列名 "UMAP cluster" の1列。
                       ・未該当スポットは空欄。クラスタ番号は手法ごとに変わる（強度は手法非依存）。
  [最終列] 領域名     : H&Eオーバーレイで割り当てた ROI 領域名（例 Brain）。未割当は空欄。

■ 値・突合の注意
  ・m/z強度 = 測定強度（変換後の生値）。
  ・クラスタ列・領域名は (annotation=サンプル名, x, y) を小数4桁に丸めて照合し付与。
  ・「annotation」(SCiLS由来スポット注釈) と「領域名」(H&E ROI) は別物。
  ・領域名は H&E オーバーレイで ROI を割り当てて初めて値が入る（未設定なら列はあるが全空欄）。
```

---

## ■ コピー用ブロック 2 : DESI / インタラクティブ画面「データ出力」

```text
【MSI解析アプリ 出力ファイル形式：DESI / インタラクティブ画面「データ出力」】

ファイル名: UMAP_cluster_DESI.xlsx （Excel 固定）
概要: DESI の元データ(.txt, SCiLS由来, タブ区切り)を切片ごとに保持し、UMAPクラスタ列と
      領域名(ROI)列を付加した Excel。

■ シート構成
  ・1切片(セクション) = 1シート（シート名 = 元 .txt のファイル名、Excel制限で31文字まで）。
  ・複数切片あれば複数シート。

■ シート内の行構成
  ・先頭にヘッダー行 = 元 .txt のヘッダー（m/z / 化合物名 / Q1-Q3 などの特徴量情報）。
     DESI .txt は「ヘッダー4行 → データ行」の構造（spot_index, x, y, m/z_1…m/z_N, (任意)ROI）。
     アプリはヘッダー最上行(1行目)の右端に、追加列の見出し（手法名 or "UMAP cluster"、および "領域名"）を入れる。
  ・以降 = 1行 = 1スポット（データ行）。各データ行の右端に手法別クラスタ列・領域名が付く。

■ 各データ行の列の並び（左から）
  [1列目]  spot index : スポット連番（数値）
  [2列目]  x          : 空間X座標（数値）
  [3列目]  y          : 空間Y座標（数値）
  [4列目〜] <特徴量強度>: 各 m/z（MRMは "Q1-Q3" 形式）の強度（数値）。
                         特徴量名は上部のヘッダー行に格納されている。
  [(任意)元.txt最終列] ROIラベル : DESIでROIをサンプル扱いする場合の領域文字列（無い場合もある）。
  --- ここから下はアプリが追加する列（各データ行の右端に付く）---
  [UMAPクラスタ列]   : ・複数手法 → 手法ごとに1列（列名 = "RPCA"/"Harmony"/"PCA"）。値=クラスタ番号。
                       ・単一手法 → 列名 "UMAP cluster" の1列。未該当は空欄。
  [最終列] 領域名     : H&Eオーバーレイの ROI 領域名。未割当スポットは空欄。

■ 値・突合の注意
  ・クラスタ列・領域名は (サンプル名, x, y) を小数4桁に丸めて照合し付与。
  ・クラスタ番号は統合手法ごとに変わる（強度は手法非依存）。
  ・元.txt の「ROIラベル列」(DESI固有) と アプリ追加の「領域名」(H&E ROI) は別由来。
```

---

## ■ コピー用ブロック 3 : MetaboAnalyst 用「④解析用データ出力(ZIP)」（DESI・TIMS 共通）

```text
【MSI解析アプリ 出力ファイル形式：MetaboAnalyst用「④解析用データ出力(ZIP)」/ DESI・TIMS 共通】

ZIP名: metaboanalyst_<強度種別>_<単位>[_qea].zip
       強度種別 = linear(線形化,非log) / counts(生counts) / data(現状,log)
       単位     = compound(化合物, 同名m/zを代表イオンで統合) / mz(統合しない)
構造: 手法ごとのサブフォルダ（RPCA/ , Harmony/ , PCA/）。強度・ROIは手法共通、クラスタのみ手法で変化。
      強度は常に「測定アッセイ(Spatial)」から算出（RPCAの補正値は使わないため負値は出ない）。

(1) <手法>/intensity_matrix_compound.csv （単位=m/z のときは intensity_matrix_mz.csv）
  行の向き: 1行 = 1擬似サンプル、列 = 化合物(または m/z)。1行目=ヘッダー。
  [1列目] Group        : {発生ステージ}_{臓器(ROI)}_cluster{番号}（例 E14_Brain_cluster1）
  [2列目以降]           : 化合物名（単位=化合物なら代表イオンの値）または m/z。
                          値 = その群(Group)に属するスポットの平均強度。
                          線形化選択時は log正規化(data層)を expm1 で非log化した平均（≥0）。

(2) <手法>/feature_map.csv
  行の向き: 1行 = 1 m/z特徴量（強度行列の列がどの m/z・注釈に対応するかの対応表）。
  列: feature_id(m/z), compound(化合物名), display_name, adduct, formula, ppm, lipid_class,
      database(注釈ソース), group_key(統合キー=化合物名), is_representative(代表イオンか True/False),
      n_in_group(同名統合に入った m/z 数)。
  ※HMDB/KEGG/LIPID MAPS ID は含まない（名前・式・SMILES のみ）。

(3) エンリッチメント(QEA)用ファイル群（「エンリッチメント(QEA)用も出力」ON、化合物単位のとき）
  <手法>/exploratory_QEA_<class>_<zero>.csv
     <class> = stage(発生ステージ) / anatomy(臓器,原ラベル) / cluster_all(全クラスタ) /
               cluster_ge10(行数≥10のクラスタのみ)
     <zero>  = raw(0を保持) / zeroAsNA(0を空欄=欠測に変換)
     行の向き: 1行 = 1擬似サンプル。1行目=ヘッダー。
       [1列目] Sample   : 英数字の一意ID（S0001, S0002, …）
       [2列目] Class    : 表現型ラベル（stage/anatomy/cluster のいずれか）
       [3列目以降]       : 化合物名。値 = その擬似サンプルの平均強度（zeroAsNAでは0は空欄）。
     → MetaboAnalyst > Enrichment Analysis > 濃度表(samples in rows) にそのままアップロード可。
  <手法>/sample_metadata.csv     : sample_id, original_group, stage, anatomy_original, umap_cluster
                                    （Sample ID と 元Group / stage / anatomy / cluster の対応表）
  <手法>/compound_name_map.csv   : original, normalized, lipid_class, changed
                                    （脂質名の RefMet 保守正規化：原文↔正規化後）
  <手法>/README.txt              : 投入手順・列定義・クラス別サンプル数・除外クラスタ・注意書き

  ■ 重要（科学的前提）: 各行(擬似サンプル)は「1切片×臓器×UMAPクラスタ」のスポット平均＝擬似バルク。
     同一 Class 内の反復は生物学的反復ではなく空間的擬似反復であり、結果は探索的(exploratory)。
     化合物名の照合は MetaboAnalyst の name-check(RefMet smart-match) に依存し、脂質略記は概ね当たるが
     薬剤/外因性名や切詰め名(…)は未マッピングになり得る。
```

---

## 付録: 変換済み MSI parquet（TIMS 出力の元データ）

TIMS インタラクティブ出力の元になる変換済み parquet（`<BASE>_Transform/<BASE>.parquet`, `scils_converter.py` 生成）:
- 1行 = 1スポット。列 = `id`(int64), `x`(float64), `y`(float64), `<各 m/z 列>`(float32, 昇順), `annotation`(string)。
- `id/x/y/annotation` 以外が m/z 特徴量列。スキーマメタデータ `mz_sorted` に全桁 m/z 一覧を保持。
- 特徴量注釈は別ファイル `<BASE>_feature_annotations.parquet`（1行=1 m/z、列: `mz, compound, lipid_class,
  database, adduct, ppm, formula, smiles, adduct_image, adduct_family, raw, display_name`）。

---

## 付録: 解析条件の記録（`analysis_conditions.json` / `provenance/`）— ver47.0

論文の Methods を書くとき、また再現性を問われたときに条件が抜け落ちないよう、
**エクスポートのたびに解析条件が自動記録される**。記録は 2 系統ある。

### (a) サーバ側記録 `<結果フォルダ>/provenance/export_<日時>_<種別>.json`

**全エクスポート経路で必ず 1 件書かれる。** ダウンロードした ZIP や CSV を失っても、
結果フォルダを見れば「いつ・どの設定で・何を出したか」が辿れる。

`<種別>` は `pptx_report` / `batch_zip_umap` / `batch_zip_spatial` / `batch_zip_feature` /
`batch_zip_deg` / `hne_metaboanalyst_zip` / `data_export` / `data_export_api` /
`csv_markers_topN` / `csv_onthefly_DE` / `csv_selection_groups` / `csv_feature_lists`。

### (b) 出力への同梱

| 出力 | 同梱される形 |
|---|---|
| PPTX レポート | 「解析条件」スライド + スピーカーノートに JSON 全量 |
| 一括保存 ZIP（UMAP / Spatial / Feature / DEG） | `analysis_conditions.json` |
| H&E MetaboAnalyst ZIP | `analysis_conditions.json` |
| データ出力 `.xlsx` | `Conditions` シート |
| データ出力 `.csv` / `.parquet`、各種 CSV | 同梱なし（(a) のサーバ側記録で担保） |

CSV の列は一切変更していない（そのまま Supplementary Table として使えるように）。
その代わり、表の**並び替え `sort_by` と絞り込み `filter_query`** を (a) に記録している。
マーカー表・on-the-fly DE の CSV は画面上の並び替え・絞り込み後の内容を書き出すため、
これが無いと同じ表を再現できない。

### `analysis_conditions.json` の構造

```text
conditions_version : スキーマ版（現在 "1"）
generated_at       : 収集日時
generated_by       : 解析者名（ログインセッションから）
integration_method : Harmony / RPCA / PCA など、その図に使った統合手法
rds_path           : 参照した RDS
result_dir         : 結果フォルダ（キャッシュ上の埋め込みでは null）

analysis           : バッチ解析側の条件（receipt.json / analysis_params.json 由来）
  .analysis_type / .data_folder / .started_at / .ended_at / .operator
  .preprocessing   : input_normalized, norm_mode, batch_correction,
                     calibration_enable, calibration_regression_mode ほか
  .umap            : n_neighbors, min_dist, metric, dims, seed
  .clustering      : algorithm, resolution, k_param
  .annotation      : ion_mode, tolerance_mz, adduct_filter, annotation_csv, sources
  .thresholds      : p, logfc  ← **統計判定に使われた閾値はこちら**
  .sample_selection: sample_names, roi_filter, annotation_filter, tims_scenario
  .mz_align_ppm

software           : app_version, r_version, packages{r, python}
pipeline           : template_path / template_sha256（どの R テンプレ版で走ったか）、
                     runtime_script / runtime_script_sha256（全定数が焼き込まれた
                     log/v8_runtime_*.R = 実際に実行されたスクリプト）、pipeline_stage

interactive        : Interactive タブの設定（interactive_settings.json 由来）
  .volcano_display : **表示・ラベル付け専用の閾値**。検定には使われていない
  .heatmap_display : top_n, scale（zscore/raw = データ変換）
  .feature_display : colorscale, intensity_min/max（色スケールのクリップ）ほか
  .onthefly_de     : ユーザーが選んだ mode / 対象クラスタ / 表示閾値
  .umap_display / .umap_view / .spatial_display / .spatial_view
  .hne_export_options : intensity_repr（linear/counts/data）, unit（compound/mz）
  .cluster_name_map::<手法> / .custom_color_map / .sample_name_map
  .selection_groups / .feature_lists : 名前と件数のみ（cell_ids 全体は入れない）

onthefly_de_fixed_params : GUI に出ていない固定値。
                           test=wilcox, min_pct=0.05, logfc_threshold=0.25,
                           p_adjust_method=BH
extra              : そのエクスポート固有の情報（出力ファイル名、Top-N、
                     sort_by / filter_query、選択ピクセル数 など）
warnings           : 再現性に関する警告（キャッシュのみの埋め込み等）
_missing           : **取得できなかった必須項目のパス一覧**
```

> **`_missing` について**: 値が取れなかった項目は既定値で埋めず `null` のまま残し、
> ここにパスを列挙する。もっともらしい値を補うと論文に誤った条件が載るため。
> Methods 下書きの末尾にも「⚠ 未記録の項目」として同じ一覧が出るので、
> そこだけ手で埋めればよい。

### Methods 下書き（`METHODS_ja.md` / `METHODS_en.md`）

Interactive タブ「エクスポート」の **「📋 解析条件をまとめて出力」** で、
`<結果フォルダ>/provenance/` に `analysis_conditions.json` と日英の Methods 下書きが書かれる。
**「📝 Methods 文を表示」** は画面表示・ダウンロード用で、表示には Master Password
（アプリのログインと同じもの）が必要。ダウンロード ZIP には
`receipt.json` / `RECEIPT.md` / `analysis_params.json` / `log/v8_runtime_*.R` も同梱されるので、
その ZIP 単体で第三者が条件を検証できる。

下書きが明示している点（Methods の誤記が起きやすいところ）:

- Volcano / Heatmap の閾値は**表示専用**であり、統計判定に使われたのは
  `analysis.thresholds.p` / `.logfc`（解析設定タブの p 値・log2FC 閾値）である。
- on-the-fly DE は Wilcoxon + BH、`min.pct=0.05`、`logfc.threshold=0.25` で走っている。
- pixel 単位の検定は探索的ランキングであり、群間の統計推論ではない（空間自己相関は未補正）。

### 記録されないもの

Plotly のカメラボタン（modebar）でブラウザ側に保存する単体 PNG には条件を添付できない。
その図の条件は (a) のサーバ側記録か「解析条件をまとめて出力」で確認すること。
また記録するのは**最終状態**であり、操作の時系列ログは残さない。
