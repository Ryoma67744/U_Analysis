# MSI Analysis Application - クラウドデプロイ手順書

本手順書では、MSI Analysis Application をクラウドサーバーにデプロイし、
チームメンバーがインターネット経由でアクセスできるようにする手順を説明します。

---

## 目次

1. [前提条件](#1-前提条件)
2. [選択肢A: Oracle Cloud Always Free（永久無料）](#2-選択肢a-oracle-cloud-always-free永久無料)
3. [選択肢B: GCP Free Trial（90日間無料）](#3-選択肢b-gcp-free-trial90日間無料)
4. [共通: サーバー初期設定](#4-共通-サーバー初期設定)
5. [アプリケーションのデプロイ](#5-アプリケーションのデプロイ)
6. [HTTPS対応（任意）](#6-https対応任意)
7. [データのアップロード](#7-データのアップロード)
8. [運用・メンテナンス](#8-運用メンテナンス)
9. [トラブルシューティング](#9-トラブルシューティング)

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

→ [4. 共通: サーバー初期設定](#4-共通-サーバー初期設定) へ進む

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

→ [4. 共通: サーバー初期設定](#4-共通-サーバー初期設定) へ進む

---

## 4. 共通: サーバー初期設定

SSH でサーバーに接続後、以下のコマンドを順に実行します。

### 4.1 Docker のインストール

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

### 4.2 リポジトリの取得

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

## 5. アプリケーションのデプロイ

### 5.1 環境変数の設定

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

### 5.2 ビルドと起動

```bash
# Docker イメージをビルド（初回は15〜30分かかります）
docker compose up -d --build

# ログを確認（起動完了まで待機）
docker compose logs -f
```

`Starting MSI Analysis Application on http://0.0.0.0:3838` が表示されたら起動完了です。
`Ctrl+C` でログ表示を終了。

### 5.3 アクセス確認

ブラウザで以下にアクセス:
```
http://<サーバーのパブリックIP>:3838
```

- Basic Auth のダイアログが表示されたら:
  - ユーザー名: `msi`
  - パスワード: `.env` で設定したパスワード

---

## 6. HTTPS対応（任意）

ドメインを取得済みの場合、Let's Encrypt で自動HTTPS化できます。

### 6.1 ドメインの DNS 設定

ドメインの A レコードをサーバーの IP アドレスに向けてください。

### 6.2 Caddyfile の編集

```bash
nano Caddyfile
```

`msi.example.com` を実際のドメインに置換:
```
msi.yourdomain.com {
    reverse_proxy msi-app:3838
}
```

### 6.3 本番構成で起動

```bash
# 一度停止
docker compose down

# HTTPS 対応の本番構成で起動
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

ブラウザで `https://msi.yourdomain.com` にアクセスして確認。

---

## 7. データのアップロード

### 7.1 SCP でファイルを転送

ローカル PC からサーバーへデータを転送します。

**DESI データの場合:**
```bash
# Docker ボリュームのパスを確認
docker volume inspect msi-desi-data

# データを転送（ローカル PC で実行）
scp -r ./my_desi_data/* user@<サーバーIP>:/tmp/desi_upload/

# サーバー上でボリュームにコピー
docker cp /tmp/desi_upload/. msi-analysis-app:/app/data/DESI/Data/
```

**TIMS データの場合:**
```bash
scp -r ./my_tims_data/* user@<サーバーIP>:/tmp/tims_upload/
docker cp /tmp/tims_upload/. msi-analysis-app:/app/data/TIMS/Data/
```

### 7.2 データの確認

```bash
# コンテナ内のデータを確認
docker exec msi-analysis-app ls -la /app/data/DESI/Data/
docker exec msi-analysis-app ls -la /app/data/TIMS/Data/
```

---

## 8. 運用・メンテナンス

### 8.1 よく使うコマンド

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

### 8.2 バックアップ

```bash
# プロジェクトデータのバックアップ
docker run --rm -v msi-projects:/data -v $(pwd)/backups:/backup \
    alpine tar czf /backup/projects-$(date +%Y%m%d).tar.gz -C /data .

# セッションデータのバックアップ
docker run --rm -v msi-sessions:/data -v $(pwd)/backups:/backup \
    alpine tar czf /backup/sessions-$(date +%Y%m%d).tar.gz -C /data .
```

### 8.3 リソース監視

```bash
# コンテナの CPU/メモリ使用量をリアルタイム表示
docker stats msi-analysis-app
```

---

## 9. トラブルシューティング

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

```bash
# ホストのメモリ使用量を確認
free -h

# docker-compose.yml の mem_limit を調整
# Oracle Cloud の場合: 最大 24GB まで利用可能
```

### ポートにアクセスできない

- ファイアウォール設定でポート 3838 が開放されているか確認
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
