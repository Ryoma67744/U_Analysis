# MSI Analysis Application - クラウドデプロイ手順書

本手順書では、MSI Analysis Application をクラウドサーバーにデプロイし、
チームメンバーがインターネット経由でアクセスできるようにする手順を説明します。

---

## 目次

1. [前提条件](#1-前提条件)
2. [選択肢A: Oracle Cloud Always Free（永久無料）](#2-選択肢a-oracle-cloud-always-free永久無料)
3. [選択肢B: GCP Free Trial（90日間無料）](#3-選択肢b-gcp-free-trial90日間無料)
4. [選択肢C: さくらVPS（推奨：長期運用・大容量データ）](#4-選択肢c-さくらvps推奨長期運用大容量データ)
5. [共通: サーバー初期設定](#5-共通-サーバー初期設定)
6. [アプリケーションのデプロイ](#6-アプリケーションのデプロイ)
7. [HTTPS対応（推奨：チーム共有用）](#7-https対応推奨チーム共有用)
8. [データのアップロード](#8-データのアップロード)
9. [運用・メンテナンス](#9-運用メンテナンス)
10. [トラブルシューティング](#10-トラブルシューティング)

---

## どのプランを選ぶか（ユースケース別早見表）

| 利用シーン | 推奨 | 理由 |
|---|---|---|
| 短期検証・無料で試したい | 選択肢A: Oracle Cloud | 永久無料、24GB メモリまで使える |
| 90日以内の検証 | 選択肢B: GCP | $300 クレジット、起動が早い |
| **長期運用 (年単位) ・数十プロジェクト・数百GB データ** | **選択肢C: さくらVPS** | **月額固定で予算明確、国内サポート、初学者向け資料が豊富** |

---

## 1. 前提条件

- SSH クライアント（Windows: PowerShell / WSL / PuTTY、Mac/Linux: Terminal）
- Git がインストール済み
- クラウドアカウント（Oracle Cloud または Google Cloud）

---

## 2. 選択肢A: Oracle Cloud Always Free（永久無料）

### 2.1 アカウント作成

1. https://www.oracle.com/cloud/free/ にアクセス
2. 「Start for Free」をクリック
3. アカウント情報を入力（クレジットカード登録が必要だが、Always Free では課金されない）
4. リージョンを選択（日本の場合: `ap-tokyo-1` または `ap-osaka-1`）

### 2.2 インスタンス作成

1. Oracle Cloud Console にログイン
2. 「コンピュート」→「インスタンスの作成」
3. 以下の設定で作成:

| 項目 | 設定値 |
|------|--------|
| 名前 | `msi-analysis-app` |
| イメージ | Ubuntu 22.04 (aarch64) |
| シェイプ | VM.Standard.A1.Flex |
| OCPU | 4 |
| メモリ | 24 GB |
| ブートボリューム | 200 GB |
| SSH キー | 自分の公開鍵をアップロード |

4. 「作成」をクリック（数分で起動）

### 2.3 ネットワーク設定

1. 「ネットワーキング」→「仮想クラウド・ネットワーク」→ 作成した VCN を選択
2. 「セキュリティ・リスト」→ デフォルトセキュリティリスト
3. 「イングレス・ルールの追加」:
   - ソースCIDR: `0.0.0.0/0`
   - 宛先ポート範囲: `3838`
   - 説明: MSI Analysis App
4. HTTPS を使う場合はポート `80` と `443` も追加

### 2.4 SSH 接続

```bash
ssh -i ~/.ssh/your_private_key ubuntu@<パブリックIPアドレス>
```

→ [5. 共通: サーバー初期設定](#5-共通-サーバー初期設定) へ進む

---

## 3. 選択肢B: GCP Free Trial（90日間無料）

### 3.1 アカウント作成

1. https://cloud.google.com/free にアクセス
2. 「無料で開始」をクリック
3. Google アカウントでログイン
4. クレジットカード登録（$300 クレジットが付与、90日間有効）

### 3.2 VM インスタンス作成

1. Google Cloud Console → 「Compute Engine」→「VM インスタンス」
2. 「インスタンスを作成」をクリック:

| 項目 | 設定値 |
|------|--------|
| 名前 | `msi-analysis-app` |
| リージョン | `asia-northeast1` (東京) |
| マシンタイプ | `e2-standard-4` (4 vCPU, 16 GB RAM) |
| ブートディスク | Ubuntu 22.04 LTS, 50 GB SSD |

3. 「ファイアウォール」→「HTTP トラフィックを許可」にチェック
4. 「作成」をクリック

### 3.3 ファイアウォール設定

1. 「VPC ネットワーク」→「ファイアウォール」→「ファイアウォール ルールを作成」:
   - 名前: `allow-msi-app`
   - ターゲット: すべてのインスタンス
   - ソース IP: `0.0.0.0/0`
   - プロトコルとポート: TCP `3838`

### 3.4 SSH 接続

```bash
gcloud compute ssh msi-analysis-app --zone=asia-northeast1-b
```

または Google Cloud Console の「SSH」ボタンからブラウザ経由で接続

→ [5. 共通: サーバー初期設定](#5-共通-サーバー初期設定) へ進む

---

## 4. 選択肢C: さくらVPS（推奨：長期運用・大容量データ）

長期運用（年単位）・大容量データ（数百GB〜）・初学者を主対象とした
**月額固定料金**の国内サービス。料金がシンプルで、Docker がそのまま動く。

**目安料金（2026年時点）**:
- メモリ16GB プラン: 月額数千円台後半
- 独自ドメイン: 年間 1,000〜2,000 円
- 合計: 年間 5〜10 万円程度

> 価格は変動するため、契約前に [https://vps.sakura.ad.jp/](https://vps.sakura.ad.jp/) で要確認。

### 4.1 さくらアカウント作成

1. [https://vps.sakura.ad.jp/](https://vps.sakura.ad.jp/) にアクセス
2. 「会員登録」をクリック → メール・住所・支払い方法（クレジットカード or 銀行振込）を入力
3. 会員ID 発行のメールが届く

### 4.2 SSH 鍵の準備（ローカル PC で実施）

未作成の場合、ローカル PC で SSH 鍵を生成します。

```bash
# Mac / Linux / WSL
ssh-keygen -t ed25519 -C "your-email@example.com"
# → ~/.ssh/id_ed25519（秘密鍵）と ~/.ssh/id_ed25519.pub（公開鍵）が生成

# 公開鍵の中身を表示（コピーして 4.3 で貼り付ける）
cat ~/.ssh/id_ed25519.pub
```

> Windows の場合は WSL2 か Git Bash で同じコマンドが使えます。

### 4.3 VPS インスタンス作成

1. さくらVPS 公式サイト → 「お申し込み」
2. 以下の設定で申し込み:

| 項目 | 推奨設定 | 備考 |
|------|----------|------|
| プラン | **メモリ 16GB プラン** | CPU 8コア / SSD 800GB 相当 |
| リージョン | 東京 または 石狩 | 東京の方がレイテンシが低い |
| OS テンプレート | Ubuntu 22.04 amd64 | 本手順書と整合 |
| 認証方式 | **SSH 公開鍵認証のみ**（パスワード認証は無効） | セキュリティのため必須 |
| 公開鍵 | 4.2 で取得した `id_ed25519.pub` の内容 | コピペで登録 |
| スタートアップスクリプト | なし | 後で手動セットアップ |

> **データが 1TB を超える見込みの場合**: メモリ 32GB プラン（SSD 1.6TB 相当）を検討。
> または 16GB プラン + 追加 SSD オプション。

3. 「お申し込み完了」後、コントロールパネルに**サーバーのグローバル IP アドレス**が表示される（例: `133.242.x.x`）→ メモする

### 4.4 パケットフィルタ（ファイアウォール）設定

さくらVPS コントロールパネル → 対象サーバー → 「パケットフィルタ設定」で
以下のポートを許可:

| プロトコル | ポート | 用途 |
|----------|--------|------|
| TCP | 22 | SSH |
| TCP | 80 | HTTP（Let's Encrypt 認証用） |
| TCP | 443 | HTTPS |

> **3838 番ポートは開放しない**。Caddy が 443 → 3838 に内部転送するため、
> 外部から直接アクセスする必要がない。直接開放するとパスワードが平文で
> 流れてしまう。

### 4.5 SSH 接続

```bash
ssh ubuntu@<サーバーのグローバル IP>
# 初回接続時:
#   "The authenticity of host '...' can't be established."
#   "Are you sure you want to continue connecting?" → yes
```

接続できれば、サーバー上のシェル（プロンプトが `ubuntu@os3-...` などになる）に入ります。

→ [5. 共通: サーバー初期設定](#5-共通-サーバー初期設定) へ進む

---

## 5. 共通: サーバー初期設定

SSH でサーバーに接続後、以下のコマンドを順に実行します。

### 5.1 Docker のインストール

```bash
# パッケージを更新
sudo apt-get update && sudo apt-get upgrade -y

# Docker の公式インストールスクリプトを実行
curl -fsSL https://get.docker.com | sudo sh

# 現在のユーザーを docker グループに追加（sudo 不要にする）
sudo usermod -aG docker $USER

# 一度ログアウトして再接続（グループ変更を反映）
exit
```

再度 SSH 接続後:

```bash
# Docker が動作することを確認
docker --version
docker compose version
```

### 5.2 リポジトリの取得

```bash
# ホームディレクトリに移動
cd ~

# リポジトリをクローン
git clone https://github.com/ryoma67744/umap-webapp-claudecode.git
cd umap-webapp-claudecode

# v2 ブランチに切り替え
git checkout v2
```

---

## 6. アプリケーションのデプロイ

### 6.1 環境変数の設定

```bash
# テンプレートから .env を作成
cp .env.docker .env

# パスワードを設定（エディタで編集）
nano .env
```

`.env` ファイルの `APP_PASSWORD` にチーム共有パスワードを設定:
```
APP_PASSWORD=your-secure-password-here
```

### 6.2 メモリ上限の調整（VPSプランに合わせる）

`docker-compose.yml` の `mem_limit` は既定 `12g`。**サーバー全体メモリの 75% 以下**を
目安に、OS と Caddy 用の余裕を残してください。

| サーバーメモリ | 推奨 `mem_limit` | 備考 |
|---|---|---|
| 16 GB（さくらVPS 16GB プラン） | **`10g`** | OS + Caddy 用に 6GB 残す |
| 24 GB（Oracle Cloud A1 Flex） | `18g` | 既定の `12g` でも可 |
| 32 GB 以上 | `24g` | 大規模解析用 |

```bash
# 例: 16GB サーバーで 10g に下げる
sed -i 's/mem_limit: 12g/mem_limit: 10g/' docker-compose.yml
```

### 6.3 ビルドと起動

```bash
# Docker イメージをビルド（初回は15〜30分かかります）
docker compose up -d --build

# ログを確認（起動完了まで待機）
docker compose logs -f
```

`Starting MSI Analysis Application on http://0.0.0.0:3838` が表示されたら起動完了です。
`Ctrl+C` でログ表示を終了。

### 6.4 アクセス確認

ブラウザで以下にアクセス:
```
http://<サーバーのパブリックIP>:3838
```

- Basic Auth のダイアログが表示されたら:
  - ユーザー名: `msi`
  - パスワード: `.env` で設定したパスワード

---

## 7. HTTPS対応（推奨：チーム共有用）

チームでの長期共有運用では HTTPS 必須。Caddy が Let's Encrypt から
証明書を自動取得・自動更新するため、設定は数行で済みます。

> **HTTP のみの直接アクセス（IPアドレス）でも動作はしますが、ブラウザ警告
> が出る・共有リンクが信頼されない・パスワードが平文で流れるため、
> チーム共有運用ではこの章の手順を強く推奨します。**

### 7.1 ドメインの取得（持っていない場合）

| 業者 | URL | 特徴 |
|------|-----|------|
| お名前.com | https://www.onamae.com/ | 国内最大手、日本語UI |
| Value-Domain | https://www.value-domain.com/ | 安価、国内 |
| Cloudflare Registrar | https://www.cloudflare.com/products/registrar/ | 卸売価格、要英語 |

**目安**: `.com` で年間 1,000〜2,000 円程度。例: `msi-yourlab.com`

### 7.2 ドメインの DNS 設定

ドメイン取得業者の DNS 管理画面で、A レコードを追加します。

| 項目 | 設定値 |
|------|--------|
| ホスト名 (サブドメイン) | `msi`（→ `msi.yourlab.com` でアクセス）または `@`（→ `yourlab.com`）|
| タイプ | A |
| 値 | サーバーのグローバル IP アドレス |
| TTL | 3600 (1時間) |

#### DNS 反映の確認

ローカル PC で:
```bash
# Mac / Linux / WSL
dig msi.yourlab.com +short
# → 設定した IP アドレスが返ってくれば反映済み

# Windows (PowerShell)
nslookup msi.yourlab.com
```

通常 10 分〜1 時間で反映。世界全体への伝搬には最大 24〜48 時間かかる場合あり。

### 7.3 Caddyfile の編集（実ドメインに置換）

```bash
# サーバー上のリポジトリ直下で
nano Caddyfile
```

テンプレートの `msi.example.com` を実際のドメインに置換:
```
msi.yourlab.com {
    reverse_proxy msi-app:3838
}
```

> **シェルで一括置換する場合**:
> ```bash
> sed -i 's/msi\.example\.com/msi.yourlab.com/g' Caddyfile
> ```

### 7.4 `.env` の SHARE_BASE_URL を本番URLに更新

共有リンク（`/share/<token>`）を正しい URL で生成するため、`.env` の
`SHARE_BASE_URL` を **HTTPS の本番ドメイン**に書き換えます:

```bash
nano .env
```

```
SHARE_BASE_URL=https://msi.yourlab.com
```

> 未設定だとリクエスト Host から推定されますが、リバースプロキシ越しでは
> 不正確になり得るため、本番運用では必ず明示してください。

### 7.5 本番構成で起動

```bash
# 一度停止
docker compose down

# HTTPS 対応の本番構成で起動（Caddy + アプリ）
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# 起動ログを確認（Let's Encrypt の証明書取得が完了するまで待つ）
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs -f caddy
# → "certificate obtained successfully" が出れば取得成功
# Ctrl+C で抜ける
```

### 7.6 確認

- ブラウザで `https://msi.yourlab.com` にアクセス
- ブラウザの鍵マークが緑（証明書有効）になっていれば成功
- BasicAuth ダイアログ → ユーザー名 `msi` + `.env` のパスワード
- アプリのトップ画面が出れば完了

> **証明書が取得できない場合**:
> - ポート 80 が開いていない（Let's Encrypt は HTTP-01 認証で 80 を使用）
> - DNS が反映されていない（7.2 の dig で確認）
> - `caddy` コンテナのログを確認: `docker compose logs caddy`

---

## 8. データのアップロード

### 8.1 SCP でファイルを転送

ローカル PC からサーバーへデータを転送します。

**DESI データの場合:**
```bash
# Docker ボリュームのパスを確認
docker volume inspect msi-desi-data

# データを転送（ローカル PC で実行）
scp -r ./my_desi_data/* user@<サーバーIP>:/tmp/desi_upload/

# サーバー上でボリュームにコピー
docker cp /tmp/desi_upload/. msi-analysis-app:/app/Data/DESI/Data/
```

**TIMS データの場合:**
```bash
scp -r ./my_tims_data/* user@<サーバーIP>:/tmp/tims_upload/
docker cp /tmp/tims_upload/. msi-analysis-app:/app/Data/TIMS/Data/
```

### 8.2 データの確認

```bash
# コンテナ内のデータを確認
docker exec msi-analysis-app ls -la /app/Data/DESI/Data/
docker exec msi-analysis-app ls -la /app/Data/TIMS/Data/
```

### 8.3 既存デプロイの移行（`Data/Other/` 再編）

`Data/` 直下を `DESI/ / TIMS/ / Other/` の 3 枠に集約した際、Docker ボリューム
（`msi-sessions` / `msi-projects` / `msi-presets` / `msi-shares` / `msi-cache` /
`msi-output`）のコンテナ内マウント先が
`/app/Data/<name>/` から `/app/Data/Other/<name>/` に変わりました。
ボリューム名は従来通りのため、ボリューム自体のデータは保持されます。

**移行が不要なケース**: 新規デプロイ、または上記ボリュームに既存データがない場合。

**移行が必要なケース**: 旧バージョンで既に projects / sessions / presets /
shares / cache / output にデータが蓄積されている環境。以下の手順で退避 →
再投入します。

```bash
# 1) コンテナを停止
docker compose down

# 2) 旧パスのデータを一時ディレクトリへエクスポート (ボリュームごと)
for name in sessions projects presets shares cache output; do
    mkdir -p /tmp/msi-migration/$name
    docker run --rm -v msi-$name:/data -v /tmp/msi-migration/$name:/backup \
        alpine sh -c 'cp -a /data/. /backup/ 2>/dev/null || true'
done

# 3) ボリュームを一旦削除 (中身を空にして新マウントと整合させる)
docker volume rm msi-sessions msi-projects msi-presets msi-shares msi-cache msi-output

# 4) 新バージョンで起動 (/app/Data/Other/ 配下にマウントされる)
docker compose up -d

# 5) 退避データを新マウントへ投入
for name in sessions projects presets shares cache output; do
    docker cp /tmp/msi-migration/$name/. \
        msi-analysis-app:/app/Data/Other/$name/
done

# 6) 権限を復元
docker exec -u root msi-analysis-app chown -R msiapp:msiapp /app/Data/Other/

# 7) 再起動
docker compose restart

# 8) 動作確認
docker exec msi-analysis-app ls /app/Data/Other/
# → Common  cache  logs  output  presets  projects  sessions  shares
```

DESI/TIMS 入力データ（`msi-desi-data` / `msi-tims-data`）は `/app/Data/DESI/Data/`
および `/app/Data/TIMS/Data/` のままで変更ありません。

---

## 9. 運用・メンテナンス

### 9.1 よく使うコマンド

```bash
# 状態確認
docker compose ps

# ログ確認（リアルタイム）
docker compose logs -f

# 再起動
docker compose restart

# 停止
docker compose down

# 更新（コードを pull して再ビルド）
git pull origin v2
docker compose up -d --build
```

### 9.2 バックアップ

このアプリには**2 層のバックアップ**機構があります：

| 層 | 仕組み | 対象 | 頻度 |
|---|---|---|---|
| **アプリ内** | `App/app/services/backup_manager.py`（実装済み） | `projects.json` / `shares.json` / `last_settings.json` のみ | アプリ起動毎 + 保存毎、5世代保持 |
| **Volume 全体** | `backup.sh`（リポジトリ直下、cron 用） | `msi-projects` / `msi-sessions` / `msi-presets` / `msi-shares` / `msi-output` の Docker Volume 全体 | cron で週次など、30日保持 |

アプリ内バックアップは JSON ファイル単位の破損対策、`backup.sh` は災害復旧
（Volume 削除事故・サーバー丸ごと故障）用。両方が補完的に動作します。

#### 手動でバックアップを実行する

```bash
# 出力先は ./backups/ (環境変数 BACKUP_DIR で上書き可)
./backup.sh

# 任意の場所に出力する場合
BACKUP_DIR=/mnt/external_disk/msi-backup ./backup.sh
```

出力例：
```
backups/
├── backup.log
├── msi-projects-20260507-030000.tar.gz
├── msi-sessions-20260507-030000.tar.gz
├── msi-presets-20260507-030000.tar.gz
├── msi-shares-20260507-030000.tar.gz
└── msi-output-20260507-030000.tar.gz
```

#### cron での定期実行（推奨）

```bash
# crontab -e で以下を追加（毎週日曜 3:00 にバックアップ）
0 3 * * 0 cd /home/ubuntu/umap-webapp-claudecode && ./backup.sh

# 別ディスクへ保存する場合
0 3 * * 0 cd /home/ubuntu/umap-webapp-claudecode && BACKUP_DIR=/mnt/backup ./backup.sh
```

`backup.sh` は 30 日以上古い `*.tar.gz` を自動削除します（環境変数 `RETENTION_DAYS` で調整可）。
ログは `${BACKUP_DIR}/backup.log` に追記。

#### バックアップから復元する

```bash
# 例: msi-projects を復元
docker compose down
docker volume rm msi-projects
docker volume create msi-projects
docker run --rm -v msi-projects:/data -v $(pwd)/backups:/backup \
    alpine tar xzf /backup/msi-projects-20260507-030000.tar.gz -C /data
docker compose up -d
```

### 9.3 リソース監視

```bash
# コンテナの CPU/メモリ使用量をリアルタイム表示
docker stats msi-analysis-app

# ホストのディスク使用量を確認（80% 超は要整理、95% 超は危険）
df -h | grep -v tmpfs

# Docker 関連の使用量を確認（イメージ・ボリューム・キャッシュ）
docker system df

# 不要な Docker キャッシュ削除（ディスク逼迫時の応急処置）
docker system prune -a --volumes  # ⚠ named volume も対象。実行前にバックアップ推奨
```

> **アプリ内のディスク監視**: 解析開始時に `psutil.disk_usage()` でチェックされ、
> 残 10 GB 未満で解析を拒否、残 30 GB 未満で警告ログ出力（`Data/Other/logs/msi_app.log`）。

---

## 10. トラブルシューティング

### アプリが起動しない

```bash
# ログを確認
docker compose logs msi-app

# コンテナ内に入って確認
docker exec -it msi-analysis-app bash
```

### R の解析が失敗する

```bash
# コンテナ内で R が動作するか確認
docker exec msi-analysis-app Rscript -e "library(Seurat); cat('OK\n')"
```

### メモリ不足

解析開始時に「空きメモリが不足しています（残 X.X GB）」と表示される場合：

```bash
# ホストのメモリ使用量を確認
free -h

# docker-compose.yml の mem_limit を調整（6.2 のガイド参照）
#   Oracle Cloud A1 Flex: 最大 24GB → mem_limit: 18g
#   さくらVPS 16GB プラン: mem_limit: 10g
#   さくらVPS 32GB プラン: mem_limit: 24g
```

### ディスク容量不足

解析開始時に「ディスク空き容量が不足しています（残 X.X GB）」と表示される場合：

```bash
# 1. ホストのディスク使用量を確認
df -h | grep -v tmpfs
# → 80% 超なら要整理、95% 超は危険

# 2. アプリ側で大きいプロジェクトを特定（Docker volume のサイズ確認）
docker run --rm -v msi-projects:/data alpine du -sh /data
docker run --rm -v msi-output:/data alpine du -sh /data

# 3. 不要なプロジェクトを UI から削除、または手動で:
docker exec -u root msi-analysis-app rm -rf /app/Data/Other/projects/<旧プロジェクト名>

# 4. 古いバックアップアーカイブを削除
ls -la backups/
rm backups/msi-*-202[0-5]*.tar.gz

# 5. Docker キャッシュを掃除（named volume は除外）
docker image prune -a
docker builder prune -a
```

> 残 30 GB 未満では警告ログのみが出ます（解析は継続）。
> ログ位置: `Data/Other/logs/msi_app.log`（または `docker compose logs msi-app`）。

### ポートにアクセスできない

- HTTP 直接アクセス（IP:3838）の場合: ポート 3838 が開放されているか確認
- HTTPS 経由（推奨）の場合: ポート 80 / 443 が開放されているか確認
- さくらVPS の場合: コントロールパネルの「パケットフィルタ」と OS の `iptables` 両方を確認
- Oracle Cloud: セキュリティリスト + OS の iptables 両方を確認
  ```bash
  sudo iptables -I INPUT -p tcp --dport 3838 -j ACCEPT
  ```
- GCP: VPC ファイアウォールルールを確認

### コンテナの再ビルドが遅い

R パッケージのインストールは Docker レイヤーキャッシュで高速化されます。
コードのみの変更であれば、再ビルドは数分で完了します。

```bash
# キャッシュを活用した再ビルド
docker compose build
docker compose up -d
```
