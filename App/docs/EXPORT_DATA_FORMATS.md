# エクスポートデータ 形式説明書（インタラクティブ画面 / MetaboAnalyst 用）

本書は、MSI 解析アプリが出力する各データファイルの**形式・列構成・値の意味**を、DESI / TIMS の両方について詳述する。

> **この文書の使い方（生成AIに渡す場合）**
> 出力ファイル（`.xlsx` / `.csv` / `.parquet` / ZIP）と本書を一緒に生成AIへ添付すると、
> AI がファイルの各列・行・値の意味を正確に解釈できるように書いてある。
> ファイル名・列名・行/列の向き・値の単位（log か 線形か 等）・DESI と TIMS の差異を明示している。

用語:
- **スポット / spot / ピクセル**: MSI の1測定点（＝Seurat の1 cell）。
- **手法（統合手法）**: UMAP/クラスタリングのバッチ統合手法。`Harmony` / `RPCA` / `PCA`。**強度には影響せず、クラスタ割当のみが手法で変わる**。
- **ROI / 領域名**: H&E オーバーレイタブでユーザーが描いた領域ラベル（例 `Brain`, `Liver`）。
- **化合物アノテーション**: m/z → 化合物名（peak-list 由来）。HMDB/KEGG 等の ID は保持しない（名前・式・SMILES のみ）。

---

# A. インタラクティブ画面の「データ出力」

**目的**: 元の MSI データ（全 m/z 強度＋座標）に、**手法別の UMAP クラスタ列**と **ROI 領域名列**を付加して返す。
**生成元**: `App/app/callbacks/interactive_data_export.py`。
**クラスタ/領域の突合**: 各行を `(サンプル名, round(x,4), round(y,4))` で照合して値を付与する。未該当は空欄。
**DESI と TIMS で出力構造が異なる**（下記）。

## A-1. DESI: `UMAP_cluster_DESI.xlsx`
- **形式**: Excel。**切片（セクション）ごとに1シート**（シート名＝元 `.txt` のファイル名、Excel 制限で31文字まで）。
- **各シートの中身**: 元の DESI `.txt`（SCiLS 由来、タブ区切り）の内容を**そのまま保持**する。
  - 先頭 **5 行 = ヘッダーブロック**（1行目＝ラベル行、2〜5行目＝付随ヘッダー）。
  - **6 行目以降 = スポット行**。各データ行は `2列目 = x 座標`, `3列目 = y 座標`, 残りが各 m/z の強度等（元 .txt の並びのまま）。
- **アプリが右端に追加する列**（ヘッダーは1行目に入る）:
  - 統合手法が**複数**選択されている場合: **手法名を列ヘッダー**にした列を手法数だけ追加（例 `RPCA`, `Harmony`, `PCA`）。値＝そのスポットのクラスタ番号（例 `cluster3`）。
  - 統合手法が**1つ**の場合: 列ヘッダー `UMAP cluster` の1列。
  - **最終列 `領域名`**: ROI（H&E オーバーレイ）由来の領域ラベル。ROI 未割当のスポットは空欄。
- **例（1シート＝1切片、右側の3列がアプリ追加分）**:
  ```
  (1行目) …元の.txtラベル…    RPCA        Harmony     PCA         領域名
  (2-5行目) …ヘッダー…
  (6行目〜) label  12.34  56.78  …intensities…   cluster3   cluster5   cluster3   Brain
  ```

## A-2. TIMS: `UMAP_cluster_TIMS.xlsx` / `.csv` / `.parquet`（出力形式を選択可）
- **形式**: **1つのフラットな表**（全切片を縦に連結）。UI で xlsx / csv / parquet を選ぶ。xlsx はシート名 `Data`。
- **元の列**: TIMS 入力（変換済み MSI **parquet**）の列をそのまま保持:
  - `id`（スポット連番, int）, `x`（float）, `y`（float）, `<各 m/z 列…>`（float32、列名＝化合物名付き or m/z 数値）, `annotation`（string, SCiLS 由来のスポット注釈）。
- **アプリが追加する列**:
  - 手法**複数**: 手法名を列名にした列（`RPCA`, `Harmony`, `PCA` …）。値＝クラスタ番号。
  - 手法**1つ**: `UMAP cluster` の1列。
  - **`領域名`**（最終列）: ROI 領域ラベル（未割当は空欄）。
- **例（先頭数列と追加列。実際は m/z 列が数百〜千あり間に入る）**:
  ```
  id, x,     y,     Glutathione_611.1439 | [M-H]-, …(多数の m/z 列)…, annotation, RPCA,     Harmony,  PCA,      領域名
  1,  12.34, 56.78, 10234.5,               …,       Section_A,  cluster3, cluster5, cluster3, Brain
  ```

### A. まとめ（DESI vs TIMS）
| | DESI | TIMS |
|---|---|---|
| 出力形式 | Excel 固定（切片ごと別シート） | xlsx / csv / parquet 選択、単一表（全切片連結） |
| 元データの保持 | 元 .txt の5行ヘッダー＋行をそのまま | parquet の列（id/x/y/m/z/annotation）をそのまま |
| 追加列 | 手法別クラスタ列 ＋ `領域名` | 手法別クラスタ列 ＋ `領域名` |
| クラスタ/領域の突合キー | (サンプル, x, y) 4桁丸め | (サンプル, x, y) 4桁丸め |

**注意**: `領域名` は H&E オーバーレイで ROI を割り当てて初めて値が入る（未設定なら列はあるが全空欄）。
`annotation`（TIMS parquet の列）は SCiLS 由来のスポット注釈で、ROI（`領域名`）とは別物。

---

# B. MetaboAnalyst 用の「④解析用データ出力（ZIP）」

**目的**: H&E ROI × クラスタ群ごとの平均強度を、MetaboAnalyst 入力向けに出力する。
**生成元**: `App/app/callbacks/hne_overlay_callbacks.py` ＋ R 集計 `export_region_cluster_means.R` ／ QEA 変換 `metaboanalyst_qea.py`。
**DESI / TIMS 共通**（Seurat RDS から生成するため、装置に依らず同じ形式）。
**ZIP 構造**: 手法ごとのサブフォルダ（`RPCA/`, `Harmony/`, `PCA/`）に格納。**強度・ROI は手法共通、クラスタのみ手法で変化**。

出力時に選べるオプション:
- **強度の種類**: `線形化(非log)`（既定・log 正規化を expm1 で線形化）/ `生counts` / `現状(log)`。
- **集約単位**: `化合物`（同名 m/z を代表イオン=最大強度で1列に統合）/ `m/z`（統合しない）。
- **強度は常に測定アッセイ（Spatial）から算出**（ver38.1 以降。RPCA の補正値は使わないため負値は出ない）。

## B-1. `<手法>/intensity_matrix_compound.csv`（または `intensity_matrix_mz.csv`）
- **向き**: **1行 = 1擬似サンプル**（`Group`）、**列 = 化合物（または m/z）**。
- **1列目 `Group`**: `{発生ステージ}_{臓器(ROI)}_cluster{番号}`（例 `E14_Brain_cluster1`）。
- **2列目以降**: 化合物名（集約単位=化合物のとき、代表イオンの値）または m/z。
- **値**: そのグループに属するスポットの**平均強度**。`線形化` 選択時は `data` 層（log 正規化）を `expm1` で非log化した値の平均（≥0）。
- **例**:
  ```
  Group,               Glutathione, PG 32:1, ATP, …
  E14_Brain_cluster1,  10.2,        1.3,     8.7
  E16_Liver_cluster2,  4.1,         0.0,     2.2
  ```

## B-2. `<手法>/feature_map.csv`
- **向き**: **1行 = 1 m/z 特徴量**。強度行列の列（化合物/m/z）が「どの m/z・どの注釈」に対応するかの対応表。
- **列**: `feature_id`（m/z）, `compound`（化合物名）, `display_name`, `adduct`, `formula`, `ppm`, `lipid_class`, `database`（注釈ソース）, `group_key`（統合キー＝化合物名）, `is_representative`（代表イオンか True/False）, `n_in_group`（同名統合に入った m/z 数）。
- **注意**: HMDB/KEGG/LIPID MAPS ID は含まない（名前・式のみ）。代表化の方法は「全群平均が最大の m/z を代表に採用（値は素通し）」。

## B-3. エンリッチメント（QEA）用ファイル群（「エンリッチメント(QEA)用も出力」ON のとき／化合物単位）
MetaboAnalyst の **Quantitative Enrichment Analysis（Enrichment > 濃度表, samples in rows）** に**そのままアップロード可能**な濃度表。各手法フォルダに生成。

- **`exploratory_QEA_<class>_<zero>.csv`**（class × zero の組合せ）
  - `<class>` = `stage`（発生ステージ）/ `anatomy`（臓器・原ラベル）/ `cluster_all`（全 UMAP クラスタ）/ `cluster_ge10`（行数≥10 のクラスタのみ）。
  - `<zero>` = `raw`（0 を保持）/ `zeroAsNA`（0 を空欄=欠測に変換。MetaboAnalyst の欠測処理に委ねる）。
  - **向き**: **1行 = 1擬似サンプル**。**1列目 `Sample`**（`S0001` 等の英数字ID）、**2列目 `Class`**（表現型ラベル）、**3列目以降 = 化合物**。
  - **例（`exploratory_QEA_stage_zeroAsNA.csv`）**:
    ```
    Sample, Class, Glutathione, PG 32:1, ATP
    S0001,  E14,   10.2,        1.3,     8.7
    S0002,  E16,   ,            ,        2.2
    ```
- **`sample_metadata.csv`**: `sample_id, original_group, stage, anatomy_original, umap_cluster`。Sample ID と元の `Group`／stage/anatomy/cluster の対応表。
- **`compound_name_map.csv`**: `original, normalized, lipid_class, changed`。化合物名の原文と RefMet 正規化後（脂質のみ、保守的）。
- **`README.txt`**: 投入手順・列定義・クラス別サンプル数・除外クラスタ、および**探索的（擬似バルク）解析である旨**。

**重要（科学的前提）**: 各行は「1切片×臓器×クラスタ」のスポット平均＝**擬似バルク**（1個体運用）。同一 Class 内の反復は
生物学的反復ではなく**空間的擬似反復**で、QEA の p 値は個体間差でなく切片内ばらつきを反映する。結果は**探索的**であり、
ファイル名接頭 `exploratory_` と README に明記している。化合物名の照合は MetaboAnalyst の name-check（RefMet smart-match）に依存し、
脂質略記は概ね当たるが、薬剤/外因性名や切詰め名（`…`）は未マッピングになり得る。

---

# C. 参考: 元データ（変換済み parquet）の形式
インタラクティブ TIMS 出力の元になる変換済み MSI parquet（`<BASE>_Transform/<BASE>.parquet`）は次の通り。
- **1行 = 1スポット**。列 = `id`(int64), `x`(float64), `y`(float64), `<各 m/z 列>`(float32, 昇順・列名は化合物名付き or `"611.143900"`), `annotation`(string)。
- スキーマメタデータに `mz_sorted`（全桁 m/z 一覧）を保持。`id/x/y/annotation` 以外が m/z 特徴量列。
- 生成元 `App/app/services/scils_converter.py`。特徴量注釈は別ファイル `<BASE>_feature_annotations.parquet`
  （1行=1 m/z、列: `mz, compound, lipid_class, database, adduct, ppm, formula, smiles, adduct_image, adduct_family, raw, display_name`）。
