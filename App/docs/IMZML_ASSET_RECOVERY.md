# imzML変換資産の退避・永続化・復元（ver75.2）

## 原因と影響

ver75.1までの標準Compose構成は、`/app/Data/Other/imzml_assets`を永続volumeに含めていませんでした。このため、コンテナの書き込み層に保存されたimzML変換資産は、コンテナを再作成すると失われます。解析結果のRDSと入力のimzML／ibdは別のvolumeに残るため、UMAP図は表示できても数値出力時に停止する場合があります。

出力処理は解析時のParquet・変換対応情報・`conversion_complete.json`を検証します。完了記録が見つからないエラーだけでは、記録のみの欠損か資産一式の欠損かは判別できません。ver75.2では不足した種類を区別して表示します。切片の選択を変えても、欠けた資産は復元されません。

ver75.2は専用volume `msi-imzml-assets`を追加し、バックアップ対象にも含めます。ホスト側の固定フォルダを使用する場合は `.env` の `IMZML_ASSETS_HOST` を指定します。コンテナ内の保存先は従来のままです。通常のコンテナ再作成に耐える保存構成であり、volume自体の削除やディスク故障には別のバックアップが必要です。

## 更新前に残存資産を保全する

既存環境では、**先に退避してからコンテナを再作成**してください。新しい空のvolumeを先にマウントすると、旧コンテナ内に残っていた資産も見えなくなります。実行中の解析・変換・出力を終了してから、管理者がサーバー上で実施します。

まず、実際のmountを確認します。ホストの認証環境変数を表示する必要はありません。

```bash
docker inspect --format '{{json .Mounts}}' msi-analysis-app
docker exec msi-analysis-app sh -c 'test -d /app/Data/Other/imzml_assets && find /app/Data/Other/imzml_assets -name conversion_complete.json -print'
```

既にこの保存先または親ディレクトリが永続mountになっている独自構成では、既存データをバックアップし、同じ保存先を維持します。新しい空フォルダへ切り替えないでください。

標準構成で資産が旧コンテナ内に残っている場合は、次のように未使用のホストフォルダへ退避できます。以下はLinuxホストの例です。保存先は環境に合わせ、十分な空き容量を確保してください。

```bash
IMZML_RESCUE_DIR=/srv/msi/imzml_assets_rescue_20260929
IMZML_APP_UID=$(docker exec msi-analysis-app id -u)
IMZML_APP_GID=$(docker exec msi-analysis-app id -g)
# mkdirは既存フォルダなら失敗する。既存データへ重ねてコピーしない。
sudo mkdir "$IMZML_RESCUE_DIR"
docker stop msi-analysis-app
sudo docker cp msi-analysis-app:/app/Data/Other/imzml_assets/. "$IMZML_RESCUE_DIR/"
sudo chown -R "$IMZML_APP_UID:$IMZML_APP_GID" "$IMZML_RESCUE_DIR"
```

各コマンドの成功を確認して進めます。コピーが失敗した場合は、コンテナを削除・再作成せず原因を確認します。必要なら `docker start msi-analysis-app` で同じコンテナを再開できます。資産が既にない場合、コピー成功として扱わず次の復元手順を検討します。

退避後、既存の `.env` の認証情報等を保持したまま、次の1項目を設定します。

```dotenv
IMZML_ASSETS_HOST=/srv/msi/imzml_assets_rescue_20260929
```

修正版を取得し、従来と同じComposeプロジェクト名・本番overlayを用いて更新します。以下は標準構成の例です。本番overlay使用中なら同じ `-f docker-compose.yml -f docker-compose.prod.yml` を付けます。

```bash
docker compose up -d --build
docker inspect --format '{{json .Mounts}}' msi-analysis-app
```

`/app/Data/Other/imzml_assets`のmount sourceが設定した保存先になっていることを確認します。下記の診断で、実際に使う解析結果の資産も照合してください。退避元のコンテナが既に失われた場合でも、バックアップや元のrawデータから同一資産を復元できる可能性があります。

## 既に消失している資産を診断・復元する

`--rds`には、出力しようとしている解析結果の既存RDSファイルを指定します。`<解析結果>`等の部分を実際のパスに置き換えます。ツールはRDS周辺の保存済みmanifestを読み、RやUMAPを起動しません。

```bash
# 読み取り専用の診断。正常なら終了コード0、欠損・復元不可なら2。
docker exec msi-analysis-app python3 tools/restore_imzml_assets.py \
  --rds '/app/Data/Other/output/<解析結果>/RDS_Files/<結果>.rds'
```

診断JSONには、対象の元ファイル・変換資産パスと状態を出力します。`--file-id '<保存済みfile_id>'`で対象を限定できます（複数指定可）。対象外のファイルを新しい入力で置換する機能ではありません。

既存のバックアップに資産がある場合は、そちらからの復元を優先します。`restore.sh`の対象名は `msi-imzml-assets` です。他の解析の健全な資産まで置き換えないよう、復元範囲と旧内容の保全を確認してください。

バックアップがなく、解析時の元imzML／ibdが同じ場所に残っている場合は、次のコマンドで同一revisionの復元を試行できます。

```bash
docker exec msi-analysis-app python3 tools/restore_imzml_assets.py \
  --rds '/app/Data/Other/output/<解析結果>/RDS_Files/<結果>.rds' --restore
```

復元は以下の全条件を満たす場合だけ成功します。

- 元imzML／ibdのサイズとSHA-256が保存時と一致する。
- 現在の変換コード・依存版・変換条件が保存時と一致する。
- 復元したParquetと変換対応情報のサイズ・SHA-256が保存時と一致する。

保存済み仕様を現在の仕様に偽装して渡すことはしません。解析後の群名編集は結果側に保持し、元Parquetの復元には解析時の固定登録情報を用います。検証不一致・原本不足・不完全な記録では停止し、別の数値を旧解析へ結び付けません。RDS・解析条件・群編集manifestは更新しません。

再変換と全ファイルのハッシュ照合には、元データ量に応じた時間と空き容量が必要です。完了後、診断が正常になったことを確認し、アプリで同じ対象を再出力します。復元できない場合は、当時のバックアップ・変換環境を確認します。検証を外して強制出力したり、新規解析結果で旧結果を上書きしたりしないでください。

## 検証範囲

永続mount・バックアップへの包含、資産欠損の診断、元データ／仕様／復元バイト列の一致条件を回帰試験に含めています。この修正作業環境にはDocker実行環境がないため、実コンテナの再作成試験と稼働サーバー上の資産復元は未実施です。
