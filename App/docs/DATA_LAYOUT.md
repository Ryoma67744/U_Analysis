# データ配置ガイド（さくらVPS デプロイ版）

ラボのMSI解析アプリでは、**生データ・解析出力・アプリ内部データ**を3つの場所に明確に分離して管理します。本ドキュメントは「どのファイルがどこに置かれるか」と「アプリ更新時にデータが消えない仕組み」を解説します。

---

## 1. サーバー上のディレクトリ構成

さくらVPSの推奨配置:

```
/srv/msi/
├── desi_data/        ← DESI 生データ (SFTP で各PCから直接アップロード)
├── tims_data/        ← TIMS 生データ (SFTP で各PCから直接アップロード)
├── output/           ← 解析出力 (アプリが書き込み・SFTPでダウンロード可)
└── app_internal/     ← (任意) Data/Other/ の永続化先
```

| パス | 所有 | 書き込み主体 | 用途 |
|---|---|---|---|
| `/srv/msi/desi_data/` | `msi-lab` グループ | ラボメンバー (SFTP) | DESI 生データの保管 |
| `/srv/msi/tims_data/` | `msi-lab` グループ | ラボメンバー (SFTP) | TIMS 生データの保管 |
| `/srv/msi/output/` | `msi-lab` グループ | アプリ + ラボメンバー | 解析出力 (`_project_meta.json` 含む) |
| `/srv/msi/app_internal/` | アプリ実行ユーザー | アプリのみ | セッション・プリセット等 (任意) |

---

## 2. コンテナとホストのマウント対応

`docker-compose.yml` は、以下のホストパスをコンテナ内パスにマウントします。

| ホスト側 (`.env` で設定) | コンテナ内パス | フォールバック (未設定時) |
|---|---|---|
| `${DESI_DATA_HOST}` | `/app/Data/DESI/Data` | `msi-desi-data` (named volume) |
| `${TIMS_DATA_HOST}` | `/app/Data/TIMS/Data` | `msi-tims-data` (named volume) |
| `${OUTPUT_DATA_HOST}` | `/app/Data/Other/output` | `msi-output` (named volume) |
| (固定マウント) | `/app/Data/Other/{sessions,projects,presets,shares,cache,common}` | named volume |

**ポイント**: コンテナを `docker compose down` で破棄しても、ホストマウント上のデータは消えません。アプリのバージョン更新は「コンテナ差し替え」で完結します。

---

## 3. アプリ内の環境変数マップ

`App/app/config.py` の参照変数:

| 環境変数 | Python 変数 | デフォルト |
|---|---|---|
| `DESI_DATA_DIR` | `DESI_DATA_DIR` | `<repo>/Data/DESI/Data` |
| `TIMS_DATA_DIR` | `TIMS_DATA_DIR` | `<repo>/Data/TIMS/Data` |
| `OUTPUT_DATA_DIR` | `OUTPUT_DATA_DIR` | `<repo>/Data/Other/output` |

これらは `path_resolver.py` と「データ管理」サブタブで参照されます。

---

## 4. アプリ更新時のデータ保持

```
docker compose down       # コンテナ停止 → 削除
git pull                  # 新コード取得
docker compose up -d --build   # コンテナ再構築・起動
```

上記操作で **`/srv/msi/` 配下のデータは一切影響を受けません**。コンテナ内の `/app/Data/Other/projects/projects.json` も `msi-projects` named volume に永続化されています。

万一プロジェクト一覧 (`projects.json`) が消えた場合は、Webアプリの「解析設定」→「データ管理」サブタブから「出力フォルダをスキャン」→「ワンクリック復元」で `_project_meta.json` から自動復元できます。

---

## 5. VPS 初期セットアップ（一度だけ実行）

```bash
# ディレクトリ作成
sudo mkdir -p /srv/msi/{desi_data,tims_data,output,app_internal}

# ラボメンバー用グループ作成
sudo groupadd msi-lab

# 所有者・権限設定 (setgid + 共有書き込み)
sudo chown -R root:msi-lab /srv/msi
sudo chmod -R 2775 /srv/msi

# 各メンバーをグループに追加
sudo usermod -aG msi-lab <username>
```

SFTP 接続設定（OpenSSH ChrootDirectory）は `App/docs/SFTP_GUIDE.md` を参照。

---

## 6. 関連ドキュメント

- `App/docs/SFTP_GUIDE.md` — SFTPクライアント設定とアップロード手順
- `App/docs/DEPLOY.md` — デプロイ全体手順
- `.env.docker` — 環境変数のテンプレート
