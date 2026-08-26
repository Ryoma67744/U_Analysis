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

ファイル名: UMAP_cluster_TIMS.parquet / .csv / .xlsx
           （出力時に選択。**既定は Parquet**。xlsx はシート名 "Data"）

■ 出力形式の選び方（ver62.1 の実測）
  Parquet が最速で、ファイルも最小。特別な理由が無ければ Parquet を使う。

  | 形式 | 20,000 spot × 2,000 m/z | 実データ規模(9.28 億セル)への外挿 |
  |---|---|---|
  | Parquet | 4.8 秒 / 0.22 GB | 約 2 分 |
  | CSV     | 58.7 秒 / 0.40 GB | 約 23 分 |
  | xlsx    | 約 19 秒・0.30 GB / 百万セル | **約 4.9 時間・約 278 GB（完走しない）** |

  xlsx はセル数が `EXPORT_XLSX_MAX_CELLS`（既定 500 万）を超えると、走り出す前に
  エラーで止まり Parquet / CSV を案内する。従来は列数(16,384)しか見ておらず、
  4,566 m/z はガードを通り抜けて終わらないまま走り続けていた。
  R や Python で読むなら Parquet、Excel で開きたい小さな出力だけ xlsx にする。
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
  ・ver59.0: 「UMAP 解析に使っていない切片(annotation)を除外」(既定 ON) のとき、
    UMAP に含めなかった切片の行は出力されない。残った空欄は「クラスタ未割当」だけを意味する。
    除外した切片名と行数は生成時のステータスに必ず表示される。OFF で全行出力。
  ・ver58.3: annotation が解析のサンプル名と1件も一致しない場合はファイル名で引き直す
    （領域アノテーションCSV無しで変換すると annotation が全行 'Unannotated' になるため）。
    それでも一致しないときは、生成時のステータスに「何行が空欄か・なぜか」を必ず表示する。
  ・「annotation」(SCiLS由来スポット注釈) と「領域名」(H&E ROI) は別物。
  ・領域名は H&E オーバーレイで ROI を割り当てて初めて値が入る（未設定なら列はあるが全空欄）。
```

---

## ■ コピー用ブロック 1b : TIMS / 「出力内容の設定」で絞った出力（ver61.0）

```text
【MSI解析アプリ 出力ファイル形式：TIMS / 出力内容を絞った場合】

エクスポート欄の「⚙ 出力内容の設定」で、出力する列と集計単位を選べる。
**何も触らなければブロック 1 と完全に同一の出力**（既定＝全項目・1 ピクセル単位）。

■ 出力する列（カテゴリ単位で ON/OFF）
  識別子        : id
  空間座標      : x, y
  強度          : 全 m/z 列
  切片          : annotation
  UMAP 座標     : UMAP_1, UMAP_2          ← 既定 OFF（ver61.0 で追加）
  品質指標      : TotalCount, nFeature    ← 既定 OFF（ver61.0 で追加・ある場合のみ）
  クラスタ      : 手法別クラスタ列
  領域名 (ROI)  : 領域名
  m/z 一覧      : 別表（1 行 = 1 m/z）      ← 既定 OFF（ver62.0 で追加）

  ・列の並びはブロック 1 と同じで、選ばなかった列が抜けるだけ。
    UMAP 座標・品質指標は annotation の後ろ、クラスタ列の前に入る。
  ・「強度」を外すと m/z 列を parquet から読み込まない。出力サイズも
    所要時間も桁で小さくなる。
  ・空間座標や切片を出力から外しても、クラスタ・領域名の突合には内部で使うため
    結果は変わらない（読むが出さない）。

■ m/z 一覧（強度とは独立した選択肢）
  「このデータにどの m/z が入っているか」を知るための別表。
  本体は 1 行 = 1 スポットだが、これだけ **1 行 = 1 m/z**（数千行）。

  列: mz / 列名 / compound / adduct / formula / ppm / lipid_class / database
    ・`列名` は強度行列で使われる見出しと**文字列一致**する。
      両方を出したときの突き合わせの鍵になる。
    ・化合物名などの注釈はサイドカー `*_feature_annotations.parquet` から引く。
      分子情報を登録していないデータでも **m/z 列は埋まる**（注釈列だけ空欄）。
    ・突合の許容差は 0.005 Da。強度行列の列名リネームと同じ値。

  出力先:
    ・**m/z 一覧だけ**を選んだ場合
        → どの形式でも一覧表が本体になる。
          ファイル名: mz_list_TIMS.xlsx / .csv / .parquet
          スポットの行は 1 行も読まないので高速。
    ・**他の項目と併用**した場合
        → xlsx なら別シート `m_z` になる（本体は `Data` シート）。
        → csv / parquet は 1 ファイルに 1 表しか持てないため**エラーになる**。
          黙って片方を落とさず、xlsx を選ぶよう案内する。

■ 集計単位
  (a) 1 ピクセル単位  … 1 行 = 1 スポット（既定・ブロック 1 と同じ）
  (b) グループ平均    … 1 行 = 1 グループ

■ (b) グループ平均のときのファイル名と列
  ファイル名: UMAP_cluster_TIMS_grouped.xlsx / .csv / .parquet
             （1 ピクセル単位とはファイル名が変わる。取り違え防止）

  [1..k]  グループキー列 : 選んだキー。以下から 1 つ以上を組み合わせる。
            切片 (annotation) / 領域名 (ROI) / クラスタ
            → 「クラスタのみ」「切片×クラスタ」「切片×ROI」
              「切片×ROI×クラスタ」が作れる。
            ※ 領域名をキーに含めなければ H&E の設定は不要。
  [k+1]   n            : そのグループに入ったスポット数（整数）
  [k+2..] <m/z>_mean   : そのグループの平均強度
          <m/z>_sd     : そのグループの標準偏差（不偏, ddof=1）
            → mean と sd は m/z ごとに隣り合わせで並ぶ。

  ・複数手法を選んだ場合は Method 列 + Cluster 列を持つ**縦持ち**になる
    （手法ごとに行が増える）。手法間でクラスタ番号の意味が違うため、
    横に並べると 1 行に別物が同居してしまう。
  ・n=1 のグループの sd は**空欄**。ばらつきが 0 なのではなく「不明」。
  ・n の合計は、同じ条件の 1 ピクセル単位出力の行数と一致する。

■ 値の意味
  ・強度は**生の測定値**（変換済み parquet の値）。Seurat の正規化は通らない。
    TIMS 入力は SCiLS の RMS 等で正規化済みのことが多く、そこへ LogNormalize を
    重ねると RMS×TIC の二重正規化になる。この出力はその手前の値なので無関係。
  ・「④ 解析用データ出力(ZIP)」（ブロック 3）は RDS 由来の**正規化済み**強度で、
    ROI 割当済みスポットのみが対象。用途が違うので混同しないこと。
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
    サンプル名は .txt のファイル名で照合する（ROIをサンプル扱いした解析では
    サンプル名が `<ファイル名>_<ROI>` になるため、ROIが2つ以上あると照合できない）。
  ・ver59.0: 「UMAP 解析に使っていない切片(annotation)を除外」(既定 ON) のとき、
    解析に使わなかったサンプルのシートは作られない（理由は "Skipped" シートに載る）。
  ・ver58.3: ヘッダ行数(4行/5行)は内容から自動判定する（従来は5行決め打ち）。
    照合できずクラスタ列が空欄になった場合は、生成時のステータスに理由を表示する。
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

### 何を何に変換しているか

#### 入力: SCiLS Lab から Export した CSV 群（同一フォルダに同居）

| ファイル | 必須 | 形 | 中身 |
|---|---|---|---|
| `<BASE>_Intensity.csv` | 必須 | **m/z 行 × spot 列** | 先頭列 = m/z 値、2 列目以降 = `Spot NNNNN` |
| `<BASE>_Spot.csv` | 必須 | spot 行 | `SpotIndex, X, Y`（マスター座標） |
| `<LABEL>_Annotation.csv` | 任意・複数可 | spot 行 | `SpotIndex, X, Y`。ファイル名の `<LABEL>` が組織ラベルになる |
| peak list CSV | 任意 | m/z 行 | `m/z, Name, …`。列名に化合物名を埋め込むために使う |

役割は**ファイル名ではなくヘッダの中身で自動判定**する（`classify_csv_role`）。
`Spot NNNNN` 形式の列が 5 個以上あれば intensity、`SpotIndex`/`X`/`Y` を持てば spot_like
（そのうち最大サイズが Spot、残りが Annotation）、`Name` と `m/z` を持てば peak_list。

#### 出力: 1 つの Parquet ファイル

**最も大きな形の変化は転置。** SCiLS の Intensity CSV は「1 行 = 1 化合物」だが、
解析は spot 単位で行うため「1 行 = 1 spot」に入れ替える。

| | 入力 Intensity CSV | 出力 Parquet |
|---|---|---|
| 1 行 | 1 つの m/z | **1 つの spot** |
| 1 列 | 1 つの spot | **1 つの m/z** |
| 値の型 | 文字列（CSV） | float32（既定）/ float64 |
| 座標 | 別ファイル（`_Spot.csv`） | `x` / `y` 列として同居 |
| 組織ラベル | 別ファイル（`_Annotation.csv`） | `annotation` 列として同居 |

スキーマ（この順で固定）:

| 列 | 型 | 内容 |
|---|---|---|
| `id` | int64 | spot 番号 |
| `x` | float64 | マスター座標 X |
| `y` | float64 | マスター座標 Y |
| `<m/z 列>` × n | float32（既定）/ float64 | 強度。**m/z 昇順** |
| `annotation` | string | 組織ラベル。該当なしは空文字 |

- **行の並び**: `(y, x)` の昇順（`np.lexsort((x, y))`）。画像の走査順に対応する。
- **列名**: peak list があれば `化合物名_<m/z 4桁> | データベース | アダクト`、
  無ければ m/z を小数 6 桁で文字列化した `419.257200` 形式。
  重複した場合は末尾に ` #2`, ` #3` … を付けて一意化する。
- **圧縮**: zstd
- **エンコード**: 強度列・`id`/`x`/`y` は PLAIN、`annotation` のみ辞書エンコード
  （`use_dictionary=["annotation"]`）。強度は連続量なので辞書にすると書き込みが 3.2 倍
  遅くなり、ファイルも大きくなる（ver60.0 で変更。値は不変）
- **row group**: 全行 1 つ（下記「row group レイアウト」参照）

スキーマ key-value メタデータ（3 キー。いずれもキー・値とも bytes）:

| キー | 内容 | 常に存在するか |
|---|---|---|
| `mz_sorted` | 全 m/z をフル桁（`%.10g`）でカンマ区切り。**列名が化合物名になっても m/z を確実に復元できる正** | 常に |
| `annotation_files` | 使った Annotation CSV のファイル名をセミコロン区切り | 常に（空のことはある） |
| `peak_list` | 使った peak list CSV のファイル名 | peak list があるときだけ |

#### 中間ファイルと副産物

- **Phase A の一時ファイル** `<BASE>_temp.parquet`（snappy、1024 行/row group、
  m/z 列は float64・強度列は出力と同じ幅（既定 float32））。
  CSV をストリーミングで一旦 parquet 化し、Phase B で転置しながら最終ファイルへ書く。
  変換成功後に削除される。
- **注釈サイドカー** `<BASE>_feature_annotations.parquet`（1 行 = 1 m/z）。
  上記のとおり本体とは別ファイル。**再パックの対象外**。

### row group レイアウト

**全行が 1 row group**（既定）。1 列（= 1 化合物）がファイル上で連続するため、特定の m/z を
読むときに 1 回の連続読みで済む。

| | 旧レイアウト（200 行/row group） | 現行（全行 1 つ） |
|---|---|---|
| row group 数（203,078 spot の例） | 1,016 | 1 |
| フッタ | 約 736 MB | 約 2 MB |
| `pq.ParquetFile()` で開く時間 | 約 3.3 秒 | 約 15 ms |

フッタは `row group 数 × 列数` 個の column chunk メタデータからなり、列名（peak-list 由来だと
長い化合物名）が chunk ごとに繰り返し格納されるため、row group を細かく刻むと肥大する。

**旧レイアウトのファイルもそのまま読める。再変換は不要。** Python（pyarrow / pandas）も
R（arrow）も列指定・全表読みのみで row group 単位の API を使っていないため、両レイアウトで
同じ結果が得られる。ただし旧ファイルは開くたびにフッタ解析のコストがかかる。

メモリが不足する場合（`spot 数 × m/z 数 × 4 バイト` のバッファが載らない場合）に限り、
変換器が自動的に複数 row group へ分割し、変換結果パネルに警告を表示する。
なお変換モーダルの「spot ブロックサイズ」は**読み込み単位**であり、row group サイズとは無関係。

#### 旧レイアウトの再パック（ver50.0）

旧レイアウトのファイルは**再変換せずレイアウトだけ作り直せる**。
GUI の「📦 Parquet 再パック」、または
`python -u App/tools/repack_parquet_rowgroups.py <フォルダ> [--dry-run] [--no-backup]`。

これは **Parquet → Parquet** の変換で、**論理的な内容は一切変えない**。
変えるのは同じデータのファイル上の並べ方（物理レイアウト）だけ。

| | 変わらない | 変わる |
|---|---|---|
| 行 | 行数・行の並び順 | — |
| 列 | 列数・列順・列名・型 | — |
| 値 | **ビットパターン**（NaN のペイロード・`±0.0` の符号を含む） | — |
| スキーマ | key-value メタデータ 3 キー | — |
| 圧縮 | コーデック（zstd なら zstd のまま） | 物理エンコーディング（辞書 ⇄ PLAIN） |
| 物理配置 | — | row group 数（1,016 → 1）、フッタ（735MB → 約 2MB） |
| ファイル | — | 全体のバイト列、サイズ（**小さくなる**）、mtime |

- **値は 1 ビットも変わらない。** 全列を整数ビュー（`uint32`/`uint64`）で突き合わせてから
  置換する。`pa.Array.equals()` は `NaN == NaN` を False、`+0.0 == -0.0` を True と
  判定するため使えない。
- スキーマメタデータ 3 キー（`mz_sorted` / `annotation_files` / `peak_list`）は
  元の schema オブジェクトをそのまま writer へ渡すことで保持する。
  **`pd.read_parquet().to_parquet()` ではメタデータが落ちる**（欠けてもエラーにならず、
  列名の正規表現パースへ静かに退避するため事故が無言で通過する）。
- **ファイルサイズはむしろ小さくなる。** フッタが消えるうえ、200 行だと 1 ページ約 800 バイトで
  zstd がほとんど効かないのに対し、全行 1 つなら大きなページでゼロの連続をまとめて潰せる。
  実測（20,000 行 × 300 列）: 疎データ −46%、量子化データ −72%、乱数（最悪）−9%。
- 既に 1 row group のファイル、`mz_sorted` を持たない parquet、注釈サイドカーは自動スキップ。
  何度実行しても安全（冪等）。
- 必要メモリは `フッタ + 全行 × 1 行のバイト数`。フッタの常駐は on-disk サイズの約 7.6 倍
  （= column chunk 1 個あたり約 1 KB）で、実データ規模ではピーク約 8 GB。
  足りない場合はスキップして必要量を報告する（`--allow-split` で分割も可能）。
- 唯一の副作用は R 取り込みキャッシュ（`<OUTPUT_DIR>/_csv_rds_cache`）が
  ファイルの `size`/`mtime` を見ているため 1 回だけミスすること。結果は変わらない。

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

### 論文用の平文 Methods（ver48.0）

「📝 Methods 文を表示」のモーダル上部で **「論文用（平文）」／「表形式（条件一覧）」** を切替える。
平文は論文の Methods にそのまま貼れる連続した文章で、日英とも同じ conditions から生成するので
2 言語で値がずれない。

**3 色で値の出どころを示す。**

| 色 | 意味 |
|---|---|
| 黒 | `receipt.json` / `analysis_params.json` に直接記録されていた値 |
| 青 | `log/v8_runtime_*.R`（実際に実行されたスクリプト）から復元した値。**要確認** |
| 赤 | 未記録。`〔要記入: …〕` / `[TO BE FILLED: …]` として手で埋める |

赤の目印は色だけでなく**文字としても入っている**ので、書式を落として貼り直しても
埋め残しが消えない。本文を選択してコピーするか「📋 書式つきでコピー」を押せば、
色のまま Word / Google Docs に貼れる。どの項目が赤かは本文末尾の「補うべき項目」にも一覧される。

`_sources` に各項目の出どころ（`recorded` / `runtime_script`）が入るので、
青い値が本当に実行スクリプト由来かは `analysis_conditions.json` で検証できる。

**ver47.0 より前の結果フォルダについて**: 当時は `analysis_params.json` の新キーも
R サイドカーも無いため多くが未記録になるが、`log/v8_runtime_*.R` には UI の値が定数として
焼き込まれているので、UMAP パラメータ・正規化モード・クラスタリング設定・seed は
そこから復元される（青で表示）。

**事実でないことは書かない。** 記録が無い、あるいは無効だった処理について断定しないよう
分岐している。主なもの:

- キャリブレーションが無効なら、回帰モードの記録が残っていてもその段落を出さない
- 再解析（`*_cluster_filter`）は「新規測定」ではなく「一次解析のクラスタを絞り込んだ再解析」と書く
- `reduction_only` の実行ではクラスタリングと DEG の段落を出さない（回していないため）
- 投げ縄 DE / MetaboAnalyst 出力は、実際に行ったときだけ段落を出す
- `mz_align_ppm` が 0 のときは「アライメントを行わなかった」と書く（0 は記録された値）

**GUI に出ていない検定条件も本文に書く。** バッチ側の DEG は
`FindAllMarkers(only.pos=FALSE, min.pct=0.05, test.use="wilcox")` ＋ `p.adjust(method="BH")`
（Seurat 既定の Bonferroni ではない）で走っており、投げ縄 DE は
`FindMarkers`（wilcox, min.pct=0.05, logfc.threshold=0.25, BH）で走っている。
またクラスタリングの近傍探索距離（既定 euclidean）は UMAP の距離尺度（既定 cosine）とは
別物なので、本文では書き分けている。

**Methods 本文に入れないもの**: チェックサム、絶対パス、実行者名、開始/終了時刻。
これらは `analysis_conditions.json` に残っているので情報は失われない。
代謝物データベースはファイル名だけを本文に出す（パスは出さない）。

「Methods をダウンロード」の ZIP には以下が入る。

```
analysis_conditions.json     機械可読な全条件
METHODS_prose_ja.html        論文用平文（色つき。ブラウザで開いてコピーすると赤青が残る）
METHODS_prose_en.html
METHODS_prose_ja.md          論文用平文（色なし。赤の位置は〔要記入: …〕で残る）
METHODS_prose_en.md
METHODS_ja.md / METHODS_en.md   条件一覧（表形式）
receipt.json / RECEIPT.md / analysis_params.json / analysis_receipt_r.json
log/v8_runtime_<日時>.R      実際に実行されたスクリプト
```
