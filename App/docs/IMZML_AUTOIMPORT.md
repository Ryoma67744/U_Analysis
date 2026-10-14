# imzML 全切片登録・標準 Parquet 変換・UMAP 解析（ver78.0）

ver78.0では[一括登録画面](IMZML_IO.md)から複数ファイルを選び、全切片の表を確認して変換できます。
通常解析からの自動変換と一括登録は、同じ`prepare_imzml()`の検証・キャッシュ・登録revisionを使用します。

## 基本原則

imzML は解析時だけの一時入力ではなく、最初に **全切片・全 pixel を含む標準 TIMS Parquet**
へ登録します。変換後は原則として Parquet を使用し、今回解析する切片の選択は Parquet の内容と
分離します。

```text
imzML + ibd
  → x-y 座標から全切片候補を検出
  → 一括登録表またはfloatで全切片の登録情報を入力・確認
  → 全切片・全 pixel を標準 Parquet へ保存
  → 今回解析する切片だけを選択
  → 選択 pixel で正規化・PCA・UMAP
```

解析に使用しない切片であっても、Parquet 内に存在する全切片について、切片名、個体／独立試料 ID、
群、登録確認が必要です。1 切片でも不完全なら、変換および解析を開始しません。概念が存在しない
場合は空欄ではなく `該当なし` と明示します。

## 変換前の全切片登録

imzML XML の x-y 座標を 8 近傍で連結し、非連結領域を `Section 01`, `Section 02`…として
候補化します。ibd の強度は配置確認時には読みません。小さい孤立領域も自動削除しません。

「全切片の登録情報・解析選択を編集」または手動変換画面の「座標切片を読み込む」からモデルレス
float を開きます。各切片について次を登録します。

- 切片名：必須、同一 imzML 内で一意
- 個体／独立試料 ID：必須
- 群：必須
- 登録確認：必須
- component の結合：必要な場合のみ

変換専用画面では解析選択を行いません。通常解析画面では「今回の解析に使用」を指定できますが、
この選択は Parquet へ保存する pixel を減らしません。

## 標準 Parquet 契約

新規 imzML 変換の主 Parquet は、従来の SCiLS CSV 変換と同じ論理表形式です。

```text
id, x, y, <m/z feature columns>, annotation
```

- 1 行 = 1 pixel
- 行順 = y 昇順、同じ y では x 昇順
- m/z 列 = 数値昇順、小数 6 桁の一意な列名
- 強度 = float32
- `annotation` = float で登録した切片名
- 未記録 feature = 0
- Zstandard 圧縮、`annotation` は辞書圧縮
- 新規主 Parquet には `ua_coordinate_component` 列を置かない

component ID、元 spectrum index、component→section 対応、変換 QC は監査用 sidecar に保存します。
Parquet footer には全切片 registry を保存するため、sidecar を移動しても通常の切片選択・解析は
Parquet 単体で行えます。原本対応、再出力、監査には sidecar が必要です。

## processed-centroid imzML

pixel ごとに長さが異なる centroid peak list は、異常ではなく疎な表現として受け入れます。
全 pixel の observed m/z から master feature dictionary を作り、各 pixel に記録されていない
feature を 0 として標準 Parquet へ展開します。

- ppm = 0：完全一致 m/z の union
- ppm > 0：指定 ppm 内で決定論的に grouping
- 代表 m/z：group の中央値
- 同一 pixel の複数 peak が同一 feature に入る場合：強度を合計し QC へ記録
- 0 の意味：exported centroid spectrum に対応 peak が記録されていない
- 0 は生体内での絶対的不在を意味しない

master feature は今回選択した切片だけでなく、imzML 内の **全切片・全 pixel** から作成します。
解析時は選択切片の行を先に抽出し、その切片群で全て 0 の feature を除外してから正規化・PCA・UMAP
へ進みます。

## MS level 判定

明示的な `MS level=1`／MS1 term がある入力は explicit MS1 とします。明示タグがなくても、
MSn term、precursor、product、selected ion、activation、fragmentation 情報がなく、mass spectrum、
centroid/profile、m/z+intensity 配列、単一 scan、極性一貫という full-scan MSI 構造を満たす場合は
inferred MS1 として処理します。MS level >1、MSn、追加次元、複数 z 面等は停止します。

## 順序補正と性能設定

m/zに大小の逆転がある場合、m/zと強度を同じ安定ソートで並べ替えます。正常な昇順入力は並べ替えません。
厳密重複、非有限値、非正m/z、負強度、配列長不一致、6桁列名衝突は別の不正として停止します。
最終書込passだけで各source_indexを1回数え、`mz_order_policy=stable_ascending_v1`、
`duplicate_mz_policy=reject`、並替え画素数・移動ピーク数・元順序保存の有無をsidecarへ記録します。
元順序への逆置換は保存しないため、逆転入力を原本へ完全復元できるとは表示しません。

辞書は完全一致のsorted uniqueを分割作成し、段階的にマージします。保持量が予算を超える場合だけ
固定長ブロックで外部マージします。`IMZML_DICTIONARY_MB`の既定は128 MiBです。
この値は作業用予算で、XML parserなどを含むプロセス全体の上限ではありません。
辞書の予算を変えてもm/zと強度の計算結果を変えません。ppmは結果を変えるため、高速化目的で自動変更しません。

保存profile `tims-f32-512-v1`は最大512行を使い、feature数・`IMZML_BLOCK_MB`（既定64 MiB）・
Arrow用の余裕から決定論的にブロックを縮小します。検証・登録情報だけの書替えも同じ規則を使います。
実行時の空きRAM不足では停止し、同じrevisionの保存形式を黙って変更しません。
進捗の保存は原則1秒間隔とし、段階・ファイル変更と終了・エラー・停止は即時保存します。
停止確認自体はこの間引きから独立しています。

## 変換のみと通常解析の統一

手動の `imzML → Parquet` と通常 UMAP 解析時の自動変換は、同じ `checked_import()` と同じ
標準 Parquet writer／validator を使用します。自動解析側は converter が返す全量検証 summary を
再利用し、同じ Parquet を二重に全走査しません。

同一の raw SHA-256、m/z 条件、変換コード、全切片登録情報を持つ完成済み asset は再利用します。
切片名・個体 ID・群だけを修正した場合は、既存 spectral core を再利用して annotation と登録 metadata
を新 revision へ書き直し、imzML の再読込・master feature 再生成を行いません。今回の解析対象切片の
変更だけでは Parquet を再変換しません。

## 解析開始前の完全性ゲート

UI、worker、API/CLI、再解析のすべてで、Parquet 内の全切片 registry を実ファイルから読み直して
検証します。クライアント manifest だけを信頼しません。

- 全 section ID が一意
- 全切片名が空欄でなく一意
- 全 subject ID と group が空欄でない
- 全切片が確認済み
- 全 component がちょうど 1 切片へ所属
- annotation 別 pixel 数が registry と一致
- selected section ID が registry の部分集合

不足時は未完成切片と不足項目を全件表示し、R・PCA・UMAPは開始しません。

## 保存・再現性

原本 imzML／ibd は読み取り専用で、移動・変更しません。source SHA-256、依存版、変換コード hash、
alignment ppm、登録 hash、標準 schema、検証結果を conversion receipt に保存します。

新しい変換契約は`v7`です。順序補正・辞書・ブロックhelperのコードhashと保存profileを含めます。
旧解析に固定した検証済み完成品は再利用できますが、古い完成品が失われた場合、新しい変換実装で
同じ旧revisionを復元したとは扱いません。新しいrevisionを作るか、保存時と一致する実行環境が必要です。

新規 Parquet の schema metadata には次を含めます。

- `ua_dataset_schema=tims-standard-parquet-v1`
- `ua_annotation_role=section`
- `ua_section_registry`
- `ua_section_registry_hash`
- `ua_registration_hash`
- `mz_sorted`

監査 sidecar は `<sample>.imzml.json`、cache 完了記録は `conversion_complete.json` です。

## 後方互換

ver71–73 の旧 imzML Parquet

```text
id, x, y, ua_coordinate_component, <m/z>, annotation
```

は legacy 経路で読み込みます。旧資産を無断で上書きしません。全切片 metadata が不足している旧資産は
解析開始前に停止し、新しい登録 revision の作成を要求します。

## 対象外・未検証

- MS/MS、DIA、複数 z 面、3D、ion mobility、追加配列の自動集約
- 座標上連続する複数組織の強度閾値・ポリゴンによる自動分割
- 未記録 0 と検出限界未満／Top-N 打切りの生物学的区別
- SCiLS 内部の非公開 feature 集約と完全に同一な強度生成の保証

実データでは master feature 数、変換時間、Parquet 容量、メモリ、適切な ppm を確認してください。
