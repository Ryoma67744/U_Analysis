# imzML 通常解析への自動取り込み・座標切片（ver73.0）

## 操作

TIMS を選択し、同じ stem の imzML と ibd を同じフォルダへ転送し終えてから、そのフォルダを
通常解析画面で選択します。imzML をチェックして通常の解析開始ボタンを押します。
入力検査・必要な変換・保存後検証がすべて成功した場合だけ、既存 R 解析へ進みます。
Parquet 入力のために手動で変換・フォルダ選択を繰り返す必要はありません。

同フォルダ同 stem の別形式を同時に選ぶことは禁止します。初期選択は Parquet を優先し、
ユーザーが解除した状態は保持します。別フォルダの同名ファイルは別の file_id です。
imzML の候補一覧では強度・SHA256を読みません。不足した ibd は無効表示し、補完しません。

## 保存・再現性

原本の `path` / `file_id` と、R に渡す `runtime_path` を区別します。
選択後、XML・ibd の全ファイル SHA256、依存ライブラリの版、変換・検証コードのSHA256、
変換ブロック設定から conversion_key を決定します。
元の画素IDとの対応・精密 m/z 軸・座標は元の変換JSONに保存します。
変換済みファイルを実体検証した後に完了JSONを含むディレクトリを原子的に公開します。

保存場所は `IMZML_CACHE_DIR`。未設定時は `OTHER_DIR/imzml_assets` です。
標準の Docker 構成では Data/Other の永続ボリューム内です。独自設定の場合はこのディレクトリも
永続マウントし、結果フォルダと一緒にバックアップしてください。キャッシュ自動削除は実装していません。
入力一覧にはこの管理領域を出しません。原本フォルダが読み取り専用でも保存先が書込み可能なら動作します。

新規解析は現在の原本内容を確認し、変更があれば別 revision を作成します。
旧結果の再解析・出力は、旧結果が固定した revision のファイルサイズ・SHA256を確認して利用します。
原本が移動・更新されても、完成済みの旧資産があればそれを使います。旧資産が破損・欠落している場合、
原本内容・変換仕様・再生成後のSHA256が保存時とすべて一致する場合だけ復元します。
一致しない原本への自動差替えはしません。破損品は `.corrupt-*` に隔離し、過去の revision は残します。

`section_manifest.json`、`analysis_params.json` の `runtime_parameters` に対応表を保存します。
元画素に戻る出力処理は runtime を検証し、原本パスを file_id へ結び付けます。
既存の手動 imzML 出力には、固定された `runtime_path` の Parquet を指定します。
ROI/クラスタを imzML 出力欄へ自動反映する新しい UI は今回の対象外です。

## 対応境界と数値

MS1 と確認または高信頼に推定でき、単一 z 面・float32/float64 強度を持つデータが対象です。
UUID、宣言されたMD5/SHA-1/SHA-256、外部offset・配列長・ファイル境界、
画素数・重複座標、MS level・スペクトル種別・極性混在、配列型と精度を検査します。
全画素共通m/z軸は元の値・強度・dtype・画素対応を保持してlosslessに変換します。
processed-centroidの可変長peak listは、指定ppmでmaster feature辞書を作り、記録されていない
pixel-featureを0とする行列へ変換します。0はexported spectrumに対応peakが記録されなかった
ことを表し、生体内での絶対的不在を意味しません。

これは完全な imzML XSD/CV validator ではありません。追加配列・イオンモビリティ次元・
MS/MS・複数scan・混合精度は自動集約せず停止します。
正規化の状態は unknown と記録し、既存の解析設定は変更しません。
SCiLS 側で RMS 正規化済みの場合は利用者が追加正規化の設定を確認してください。
装置メタデータの完全な往復複製を保証する機能ではありません。

参考CV: https://github.com/imzML/imzML/blob/master/imagingMS.obo

## ジョブ・停止・再接続

通常解析では親 Python worker 1件が入力準備・R実行を管理し、既存ジョブ台帳と終了監視へ登録します。
台帳の受付完了を確認するまでworkerは開始しません。ブラウザを閉じてもサーバ側の終了監視が継続します。
入力ごとの検査・変換のエラーではRを起動せず、他の成功した試料だけに減らして解析を続行しません。

処理段階は `log/input_pipeline.json`。変換中の進捗をRのETAとして表示しません。
`R_ANALYSIS_TIMEOUT_SEC` はR開始後から計測します。停止は親と子孫プロセスを対象とし、
再接続時はPIDだけでなく作成時刻も確認します。次回同revisionのロック取得時に未完成stageを清掃します。
完全に停止したworkerを自動再開する機能ではありません。再実行時に完成cacheは再利用し、未完成分は再処理します。

手動入出力も同じ受付ロックを使用し、二度押しで現在の監視を無効にしません。
手動処理のログは `OTHER_DIR/logs/imzml_io/<job_id>/`。アプリプロセス自体が落ちて
終了コードを記録できなかった手動ジョブは、done JSONだけを根拠に成功扱いしません。
CLIをアプリ外から直接実行する場合、このGUI受付制御を迂回するため、並列実行しないでください。

## リソース・検証上の制限

`IMZML_BLOCK_MB` 既定64を使い、matrix・Arrow copy・一時バッファの3本分を見積もって
最大128画素からブロックサイズを下げます。ただしXMLの画素情報・対応表は画素数に比例します。
総メモリの厳密な一定上限を保証するものではありません。空きメモリ・cgroup・ディスクも検査し、
不足時は失敗とします。原本および出力SHA256の確認は全ファイルを読みます。
キャッシュ再利用でも検証I/Oが必要なので「瞬時」や速度改善率は保証しません。

配布した TEST_REPORT.md を必ず確認してください。管理層の代替converter/代替Rによる試験を、
実Parquet・実SCiLS・R/Seurat/UMAP・ブラウザでの試験と同一視しないでください。

## 今回拡張していない補助機能

未変換imzMLに対する「解析前の生スペクトルからキャリブレーションピークを自動検出」は
未対応です。未変換XMLをCSVと誤認しないよう停止します。手入力の補正条件や、解析後の
既存expression cacheを使う経路は従来設定を維持します。この補助機能を検証済みとは扱いません。

## 座標からの物理切片候補（ver71.0）

imzML に切片名・ROI名が無くても、XML に保存された x–y 測定座標が複数の非連結領域に
分かれている場合は、8近傍の連結成分を物理切片候補として検出します。強度を持つ ibd は、
通常画面でこの候補とプレビューを作る段階では読みません。小さい成分も自動削除せず、
画素数を付けて表示します。

通常画面には切片名と画素数だけをコンパクトに表示します。「配置・切片情報を編集」を押すと、
背景の設定を操作できる大型のモデルレスフローティングパネルが開きます。パネルは移動・
サイズ変更・最小化ができ、位置とサイズは同じブラウザタブ内で保持されます。ver71.1では
初期サイズを920×760 CSS pxへ拡大し、旧サイズのsessionStorageは引き継ぎません。図は座標の
測定フットプリントで、TICまたは特定イオンの強度画像ではありません。

初期名は上から下、同じ高さでは左から右の順で `Section 01`、`Section 02`…です。float上段で
座標配置を確認し、下段の編集表で切片名、個体／独立試料ID、群を直接編集します。群は選択行へ
一括設定できます。成分の結合・1切片化・除外とメタデータ編集は同じ「適用」で一括確定します。
名称・個体・群の変更だけでは数値解析の署名を変えません。成分の結合、1切片化、解析対象からの
除外は画素の所属を変えるため、数値解析条件の変更として扱います。

変換後Parquetには `ua_coordinate_component` を保存し、既存の `annotation` とは分けます。
前者は座標由来の物理領域、後者はSCiLS ROI・臓器・組織領域のための列です。
manifestには全componentの所属と、解析対象として選択したsectionの両方を保存します。
R側は `ua_coordinate_component` を最優先して各画素へ `section_id` と
`integration_unit_id` を付与します。

座標間の背景まで連続して測定されている場合、座標上は1つの連結領域になるため、この機能だけで
複数切片へ自動分割できません。その場合は「座標上は1領域」と表示します。TIC閾値による組織マスク、
自由矩形・ポリゴン分割、複数z面、3D、MS/MS、イオンモビリティ次元は今回の対象外です。


## MS level推定とm/z軸事前検査（ver72.0）

明示的な `MS level=1` または `MS1 spectrum` がある場合は `explicit_ms1` と記録します。
明示タグが無くても、mass spectrum、centroid/profile、m/z+intensity配列、1 pixel 1 scan、
一貫したpolarityを確認し、MSn、precursor、product、selected ion、activation、fragmentationの
証拠が無い場合は `inferred_ms1` として受け入れます。元imzMLへタグは追加せず、判定根拠を
変換manifestへ保存します。MS level>1またはMSn構造がある入力は通常MS1解析では停止します。

ファイル選択時にはXMLとibdの境界情報から、pixelごとのpeak数を確認します。peak数が異なる場合は
`individual_axis` と判定し、理由・peak数範囲・科学的注意を画面へ表示します。同じpeak数であっても
m/z値の完全一致は解析開始時にibdを読んで確認し、一致した場合だけ `common_axis` とします。

processed-centroid MSIでも、mass alignment後に共通consensus feature行列を作ればPCA・UMAPは可能です。
ただしSCiLS等のTop-N／閾値付きpeak listでは、あるpeakの欠落が真の未検出か、順位・閾値で
出力されなかっただけかを区別できません。そのため現在のU_Analysisは個別m/z軸に対して
自動union、0補完、広いbinning、mass alignmentを行いません。全pixelのintersectionだけを使う方法も、
局在分子を除外するため推奨しません。共通feature listに対する全pixel強度行列、または
共通m/z軸を持つParquetを使用してください。

入力準備中に停止した場合は、ログを「入力準備プロセス」と表示し、R解析が未開始であることを明記します。

## processed-centroid疎ピークリストの共通行列化（ver73.0）

pixelごとにピーク数が異なることは、processed imzMLでは疎な表現として正常です。全pixelの
m/zを集め、画面の `m/zアライメント（ppm）` に従ってmaster featureを決定します。0 ppmは
完全一致m/zのunion、正値は小さいm/zから決定論的にppm範囲をまとめ、各groupの中央値を
代表m/zとします。現在のTIMS R readerが5桁feature名を使うため、5桁で衝突する隣接groupは
サブ0.00001 Daの互換処理として統合します。

同一pixelで複数peakが同じfeatureへ対応した場合は強度を合計します。原peak数、master feature数、
collision数、検出pixel数、m/z spread、変換前後の強度合計、0割合をsidecarへ保存します。
全pixelに共通するpeakのintersectionへ縮約しないため、局在featureを保持できます。

内部Parquetは既存R loaderと互換なwide tableですが、0が多いデータです。R側はfeature列をblockで
読み、`dgCMatrix`へ逐次変換します。変換時の密blockは `IMZML_BLOCK_MB` で制限し、master feature数は
`IMZML_PROCESSED_MAX_FEATURES`（既定100,000）でfail-closedに制限します。ppmを変更するとcache keyが
変わり、既存revisionを上書きしません。

processed入力から再出力するimzMLは、alignment後の共通軸と0補完値を持つ派生データです。元の
可変長centroid listへのlossless round-tripではないことをexport receiptへ記録します。
