# imzML 変換資産の共有保存先への移行（ver80.0）

この文書は管理者が別途実施する手順であり、アプリ更新だけでは移行しません。移行先は `/srv/msi/imzml_assets`、コンテナ内のパスは従来の `/app/Data/Other/imzml_assets` を維持します。RDS、保存済み入力 manifest、file_id、conversion_key、解析プロジェクトの保存先は書き換えません。表示用の `MSI_HOST_*` と共有権限は変換仕様やキャッシュキーに含めません。

共有グループは完成品を閲覧・ダウンロードできます。完成品の上書き権限は付与しません。共有先を SFTP から編集すると旧解析の検証に失敗するため、管理対象の Parquet、対応 JSON、完了記録はアプリ以外から編集しません。

## 切替前の必須条件

**コピーの一致だけでは移行成功とは判定しません。元から欠損していた資産は、コピー先にもありません。** 以下が一つでも未確認なら切替を止め、既存のコンテナ・volume・bind・保存結果を保全します。

- 全プロジェクト（旧解析、インポート結果、保存済み共有結果を含む）の一覧、結果 RDS と保存済み入力 manifest の所在・参照先を棚卸しする。通常の画面で見える最新結果だけに限定しない。
- 各 imzML 結果に対し既存の `tools/restore_imzml_assets.py --rds '<結果>.rds'` を **`--restore` なし**で実行し、保存済み receipt・Parquet・対応 JSON の検証結果が全件 `healthy` になることを確認する。imzML を使わない結果の「対象なし」は別分類で記録する。manifest 自体が見つからない場合は正常扱いにしない。
- `needs_restore` / `blocked` / 所在不明の結果は、当時のバックアップや残存コンテナを調査する。新規変換結果を旧結果へ結び付けたり検証を省略したりしない。先に独立した復旧作業を完了する。共有保存先への移行は欠損資産の復元手段ではない。
- 全 mount の Type / Name / Source / Destination / RW と Compose project / config files / working directory、使用 image ID、app UID/GID/補助 group を保存する。projects、sessions、presets、shares、common、logs、cache、seurat_cache、TIMS、DESI、output を含む。移行後に変更を許す mount は imzml_assets の Source/Type だけ。
- アプリ状態と全永続データを別媒体へバックアップし、アーカイブの読取検査を完了する。現行 `tools/volume_backup.py` / `backup.sh` の対象名 `msi-imzml-assets` は、実コンテナの Destination から named volume と bind の双方を解決する。対象外の独自 mount も別途保全する。
- コピー、独立バックアップ、照合ログ、新規解析に必要な容量を確保する。新しいアプリ image の UID が旧 app UID と一致することも確認する。UID が変わる更新と保存先移行を同時に行わない。

次の情報は 2026-10-15 の読み取り調査値です。実行前に再確認してください。

| 項目 | 確認値 |
| --- | --- |
| Compose project | `umap-webapp-claudecode` |
| 作業ディレクトリ | `/home/ubuntu/UMAP-WebApp-ClaudeCode` |
| Compose files | `docker-compose.yml` と `docker-compose.prod.yml` |
| app UID / primary GID | `1002 / 1002` |
| app 共有補助 GID | `1001` (`msi-lab`) |
| SFTP 利用者 | `ubuntu` UID 1000、補助 GID 1001 |
| 現 imzML volume | `umap-webapp-claudecode_msi-imzml-assets` |
| 現 volume の Source | `/var/lib/docker/volumes/umap-webapp-claudecode_msi-imzml-assets/_data` |
| 維持する Destination | `/app/Data/Other/imzml_assets` |
| 既存 bind | `/srv/msi/tims_data`、`/srv/msi/desi_data`、`/srv/msi/output` |

旧 revision に mode 0700 のディレクトリ、0600 の完了 JSON が存在するため、umask の変更だけでは SFTP から読めません。新コードは明示的な `MSI_IMZML_SHARED_READ=1` のときだけ、新規公開時にディレクトリ 2750 / 通常ファイル 0640 を設定します。既存 revision は自動変更しません。

## 1. 記録して受付を停止する

調査記録はデータ領域の外側へ保存し、第三者に公開しません。認証環境変数全体をログに出す必要はありません。

```bash
docker inspect --format '{{json .Mounts}}' msi-analysis-app
docker inspect --format '{{json .Config.Labels}}' msi-analysis-app
docker inspect --format '{{.Image}}' msi-analysis-app
docker exec msi-analysis-app id
```

新規受付を止め、解析・登録検査・変換・出力・バックアップの実行が終了したことを確認します。上記の結果別診断とバックアップを完了した後、同じコンテナだけを停止します。

```bash
docker stop msi-analysis-app
```

`docker compose down -v`、volume の削除・prune、空保存先を先にマウントする操作は使用しません。既存 `.env` は秘密を含むので権限を維持して管理者領域へ保全し、他の設定を置換しません。

## 2. 未使用の領域へ全 tree をコピーして照合する

以下は確認済み Source を使う例です。Docker の実 Source が異なる環境では値を実際の mount に合わせます。全てのコマンドの終了コードを確認し、失敗時は次へ進みません。停止後は Source へ一切書き込みません。

```bash
IMZML_COPY=/srv/msi/.imzml_assets_migration_20261015
IMZML_SOURCE=/var/lib/docker/volumes/umap-webapp-claudecode_msi-imzml-assets/_data
# 既存領域へコピーを重ねない。mkdir が失敗したら停止する。
sudo mkdir "$IMZML_COPY"
sudo docker cp msi-analysis-app:/app/Data/Other/imzml_assets/. "$IMZML_COPY/"
# 更新版 repository の読み取り専用照合ツール。ログは Source/Copy の外へ保存する。
sudo python3 App/tools/verify_imzml_asset_copy.py \
  --source "$IMZML_SOURCE" --copy "$IMZML_COPY" > imzml-copy-before-permissions.json
```

ツールは隠しファイル、空ディレクトリ、全ファイルの相対パス・サイズ・SHA256 を比較します。シンボリックリンク、特殊ファイル、ハードリンク、読み取り中の変更、空 tree は自動承認せず停止します。`tree_equal:true` はコピーの同一性だけを表し、旧結果の完全性は前節の全件診断で別途確認します。`saved_result_completeness_checked:false` はその区別です。元資産が欠損していたら、同じ欠損 tree 同士の一致を移行許可に使いません。

## 3. コピー先だけを共有読取可能にする

照合済みの実ディレクトリで、リンクを含まないことを確認してからコピー先だけを変更します。UID/GID は前節で再確認した値を使用します。**Docker volume の Source や `/srv/msi` 全体へ再帰 chown/chmod を行いません。**

```bash
test "$(sudo realpath -- "$IMZML_COPY")" = /srv/msi/.imzml_assets_migration_20261015
sudo chown -R 1002:1001 -- "$IMZML_COPY"
sudo find "$IMZML_COPY" -type d -exec chmod 2750 {} +
sudo find "$IMZML_COPY" -type f -exec chmod 0640 {} +
sudo python3 App/tools/verify_imzml_asset_copy.py \
  --source "$IMZML_SOURCE" --copy "$IMZML_COPY" > imzml-copy-after-permissions.json
test ! -e /srv/msi/imzml_assets
test ! -L /srv/msi/imzml_assets
sudo mv -T -- "$IMZML_COPY" /srv/msi/imzml_assets
```

上記は一括貼り付けで失敗を無視するスクリプトではありません。各 `test` / 照合が成功した後だけ次へ進みます。既に移行先がある場合、混在させず別途調査します。共有 root までの親ディレクトリは app と SFTP 利用者が traverse でき、リンクを経由しないことが必要です。共有 root と既存 project/file/revision ディレクトリは app UID / shared GID / 2750 に揃えます。group や other への書き込み権限は与えません。

## 4. imzML mount だけを切り替える

既存 `.env` の他の設定・秘密を維持し、次を反映します。

```dotenv
IMZML_ASSETS_HOST=/srv/msi/imzml_assets
MSI_SHARED_GID=1001
MSI_IMZML_SHARED_READ=1
```

`IMZML_CACHE_DIR`、TIMS/DESI/output の内部パス、プロジェクト保存場所、保存済み RDS/manifest は変更しません。Compose は `MSI_HOST_IMZML_ASSETS_DIR` 等を host 値から渡すため、表示用環境変数を別に手書きする必要はありません。named volume 名は公開ホストパスとして扱いません。

検証済みの更新 image を使い、**同じ project 名・同じ Compose files・同じ作業ディレクトリ**で app だけを再作成します。新しい project 名で起動すると、既存 projects 等とは別の空 volume が作られて解析が見えなくなるため、停止条件です。

```bash
cd /home/ubuntu/UMAP-WebApp-ClaudeCode
docker compose -p umap-webapp-claudecode \
  -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps msi-app
docker inspect --format '{{json .Mounts}}' msi-analysis-app
docker exec msi-analysis-app id
```

全 mount を移行前の記録と比較し、imzml_assets の Source/Type 以外は同一であることを確認します。imzml_assets の Destination は `/app/Data/Other/imzml_assets` のままで、Source が `/srv/msi/imzml_assets` になっていることを確認します。

## 5. 受付再開前の受入確認

- healthz、旧プロジェクト件数・ID・結果 RDS 一覧・保存済み manifest の内容と SHA256 が移行前と一致する。
- 全対象結果の読み取り専用資産診断が再び `healthy`。代表の旧結果で再表示・数値出力が成功し、保存済み参照先に変更がない。
- SFTP 利用者の実セッションで既存 Parquet・対応 JSON・完了記録を一覧・ダウンロードでき、SHA256 が一致する。ディレクトリ・ファイルへ group write が付いていない。
- 小さい試験入力の新規登録で新 revision が作られ、全階層が app UID / GID 1001、dir 2750 / file 0640 となる。SFTP ダウンロードでき、アプリ再起動後も同じ key/bytes を再利用する。元データを編集して試験しない。
- 実 mount を解決するバックアップで `msi-imzml-assets` が新 bind から取得され、読み取り検査に成功する。

ローカル Windows 単体試験だけでは Linux 所有者・setgid・実 SFTP・Docker 再作成は保証できません。POSIX 権限は Linux CI の試験に加え、この受入確認で実環境を検証します。

## 復旧と旧 volume の保持

旧 named volume と移行前バックアップは削除・権限変更せず保持します。切替直後で新規書き込みがない場合は、停止して旧 `.env` の `IMZML_ASSETS_HOST` と `MSI_IMZML_SHARED_READ` を戻し、同じ Compose project/files で app だけを再作成します。移行先も削除せず調査用に保持します。

新規登録・解析・状態変更が一件でもあった場合、旧 volume へ単純に戻すと新しい解析の参照資産が欠損します。受付停止後に全 mount の状態と新規 revision を別途保全し、差分一覧・保存結果参照・全 SHA256 を照合してから復旧範囲を決めます。新しい revision を旧領域へ無条件上書きしたり、旧 projects volume だけを復元したりしません。`restore.sh` は現在の対象を置換する復元用であり、この保存先移行には使用しません。
