# imzML 入出力（ver69.0 の初期実装）

解析設定タブのサイドバー「🗂 プリセット・バックアップ」にある
**「🧬 imzML 入出力」**から開きます。共通 m/z 軸の `.imzML` / `.ibd` ペアを
TIMS 互換の Parquet に登録し、登録したスペクトルを imzML / ibd の ZIP として出力する機能です。
既存のインタラクティブ画面「データ出力」とは別の操作です。

## 登録手順

1. 同じ名前のペア（例: `sample.imzML` と `sample.ibd`）を同じフォルダに配置します。
   サーバー運用では SFTP 等で両方の転送を完了してください。`.d` フォルダの直接登録には対応していません。
2. 「🧬 imzML 入出力」で操作 **「imzML + ibd → Parquet 登録」**を選びます。
3. 入力に `.imzML`、出力に新しい `.parquet` ファイルの**絶対パス**を入力します。
   ibd は同じフォルダから自動で探します。
4. 「実行」を押し、完了表示を確認します。出力先に次の2ファイルが生成されます。
5. TIMS の「データフォルダ」に登録先フォルダを指定し、対象ファイルと切片を選んで解析します。
   登録だけでは UMAP・クラスタ解析は始まりません。

| ファイル | 内容 |
|---|---|
| `sample.parquet` | 1行＝1画素。`id`, `x`, `y`, m/z強度列, `annotation`（初期値 `Unannotated`） |
| `sample.imzml.json` | 元の m/z 軸・数値精度・元座標・元スペクトル順との対応など。imzML 再出力に必要 |

元の imzML / ibd は変更しません。登録先に同名の Parquet または JSON があれば停止するため、
再登録には別名（例: `sample_v2.parquet`）を指定します。登録した Parquet と JSON は同じ名前・同じ場所で保管してください。

入力するのは**アプリが動いている環境から見えるパス**です。標準の Docker マウント構成なら、
SFTP の `/srv/msi/tims_data/sample/sample.imzML` はアプリでは
`/app/Data/TIMS/Data/sample/sample.imzML` になります。出力例は
`/app/Data/TIMS/Data/sample/sample.parquet` です。実際の対応は管理者の設定に従ってください。
ブラウザを開いている PC の `C:\...` を入力しても、別サーバーのアプリからは読み込めません。

## imzML として出力する手順

1. 操作 **「登録済み Parquet → imzML + ibd ZIP」**を選びます。
2. 入力に上記の登録済み Parquet、出力に新しい `.zip` の絶対パスを入力します。
   CSV から変換した Parquet や解析結果表には、必要な `.imzml.json` がないため使えません。
3. 「出力画素 ID」を空欄にすると全画素を出力します。絞る場合は、対象 Parquet の
   `id` をカンマ区切りで指定します。
4. 「実行」を押し、完了後に指定先の ZIP を SFTP 等で取得します。
   現時点では、この画面にブラウザのダウンロードボタンはありません。

**画素 ID は登録後の Parquet 内の連番です。** 登録時に画素を `(y, x)` の順に並べ、
1 から採番します。元 imzML のスペクトル順や元の SpotIndex、UMAP の CellID と同一とは限りません。
例えば `1,2,3,4` は「登録後の ID が1～4の画素」を意味し、**2×2 ROI を自動で指定するものではありません**。
Parquet の `id/x/y` と対象位置の対応を確認して指定してください。ROI やクラスタを画面で選択しても、
この入力欄には自動反映されません。

| ZIP 内のファイル | 内容 |
|---|---|
| `sample.imzML` | 選択画素のスペクトル情報。元の m/z 軸・座標に基づいて生成 |
| `sample.ibd` | 選択画素の保存済み強度データ |
| `export_manifest.json` | 元 Parquet、画素数、m/z数、指定 ID（全画素の場合 `all`）、正規化状態 |

スペクトルの強度は**登録時に保存した値**です。解析後 RDS の正規化値、クラスタ平均、
ROI 名、クラスタ列、UMAP 座標はこの ZIP に追加しません。元 imzML の全メタデータを
複製する機能ではなく、極性や装置情報等の完全な引き継ぎは保証していません。
既存の「データ出力」で選ぶ Parquet / CSV / xlsx の形式・集計設定は、この操作には適用されません。

## 対応範囲と確認事項

- 対象は、全画素で同一の m/z 軸、単一 z 面、float32 / float64 強度のスペクトルです。
  画素ごとに異なる m/z 軸の補間・再標本化は行いません。
- 不正なスペクトル（非有限値、負の強度、m/z が昇順でない等）、重複座標、
  小数6桁の列名または R の小数5桁の特徴量名で区別できない m/z は、理由を示して停止します。
  出力時には profile / centroid の種別が認識できることも必要です。
- 保存値の正規化状態は `unknown` と記録します。SCiLS 側の RMS 等の有無は自動判定しません。
  元データの出力条件を確認し、解析設定の正規化を選んでください。登録自体では強度を正規化しません。
- 登録時は全スペクトルの読み込み・検査・Parquet 書き込みを行います。出力も強度表を読み、
  imzML / ibd を新規生成・再読込検査するため、単なる拡張子変更や座標の並べ替えではありません。
  大容量データの所要時間は画素数・m/z数・環境に依存し、実データでの性能は未確認です。
- 「停止」で中断した場合、未完成の出力は解析・再出力に使用しないでください。

ver69.0 は初期実装です。ROI / クラスタ選択画面からの直接出力、GPT API での形式指定、
既存データ出力への imzML 追加は未実装です。SCiLS 実データでの往復検証も未完了です。

## CLI とログ

リポジトリの `App` ディレクトリから実行する例です。

```bash
python -u tools/imzml_io_cli.py inspect /path/to/sample.imzML
python -u tools/imzml_io_cli.py import /path/to/sample.imzML /path/to/sample.parquet
python -u tools/imzml_io_cli.py export /path/to/sample.parquet /path/to/sample.zip
```

選択出力は export に `--pixel-ids` を追加します（例: `--pixel-ids 12,13,22,23`。
この例も対象座標を保証する ID ではありません）。CLI は進捗を標準出力へ表示し、
`--status /path/to/status.json` を指定すると JSON にも記録します。
GUI から実行した場合のログと status JSON は、アプリの `OTHER_DIR/logs/imzml_io`
（通常 `Data/Other/logs/imzml_io`）に保存します。
