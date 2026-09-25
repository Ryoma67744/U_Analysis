# imzML 入出力（初期実装）

サイドバーの「imzML 入出力」から、同じ stem の `.imzML` / `.ibd` ペアを
TIMS 互換の Parquet に登録できます。元ファイルは読み取りのみです。
Parquet と同じ場所に `.imzml.json` が生成され、元の精密 m/z 軸、画素順、
元座標、強度精度を記録します。保存値の正規化状態は `unknown` です。
SCiLS 側で RMS 正規化済みの場合もファイル形式のみから判定しません。

登録済みの Parquet を入力として imzML と ibd の ZIP を出力できます。
出力画素 ID は登録時に付与した Parquet の `id`（1 始まり）です。
2×2 ROI のように画素を限定する場合は `1,2,3,4` のように指定します。
空欄では全画素を出力します。付属する `export_manifest.json` に範囲を記録します。
imzML の値は保存済みの強度です。解析後 RDS の正規化・平均化値は含みません。

対象は共通 m/z 軸、単一 z 面、float32/float64 強度のスペクトルです。
m/z の6桁列名または R の5桁表示で衝突する軸、個別軸、非有限値、
負の強度、重複座標、既存の出力名は理由を示して拒否します。
対応しないデータを補間やゼロ埋めで変換しません。

CLI: `python -u tools/imzml_io_cli.py import input.imzML output.parquet`
および `python -u tools/imzml_io_cli.py export output.parquet output.zip --pixel-ids 1,2,3,4`。
ログと status JSON は `Data/Other/logs/imzml_io` に保存します。

この初期実装は、プロジェクトの ROI/cluster 選択画面から画素 ID を直接渡す操作、
GPT API での形式指定、個別軸の再標本化、SCiLS 実データでの往復確認を含みません。
GUI はサーバー上の絶対パスを入力する方式です。出力 ZIP を既存の
データ出力ダウンロードへ組み込む前に、対象範囲の共通検証と元画素 ID の
対応を確認してください。
