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

**逆に、上の表に載っていないコンテナ内パス（`/app` 直下など）は消えます。** そこはコンテナの書き込み層で、ホストにも SFTP にも現れません（実体は `/var/lib/docker/overlay2/<hash>/diff/` 配下で、root 専用かつハッシュは毎ビルド変わります）。アプリは自分のコンテナ内から直接読むため解析も閲覧も正常に動いてしまい、再ビルドまで気づけません。

確認コマンド:

```bash
docker inspect msi-analysis-app --format '{{range .Mounts}}{{.Type}} {{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
```

---

## 2-1. 結果フォルダを移す（データ管理サブタブ）

出力先を誤って `/app` 直下などにしてしまった結果は、アプリの
**「解析設定」→「データ管理」→「📦 フォルダの移動」** から永続化先へ退避できます。

1. **移動元**: 「参照...」でコンテナ内のフォルダを選ぶ（`/app` 直下も選べます）
2. **移動先**: 「参照...」で着地させたいフォルダを選ぶ（既定は `/app/Data/Other/output`）。
   ファイルブラウザ上部のショートカットで 4 か所へ飛べるので、そこから目的の階層まで潜ります
3. **「📦 移動する」** → 確認モーダルでファイル数・容量を確認して実行

移動先は上表の 4 か所の配下に限定されます。範囲外を指定するとエラーになります。
上書きは行わず、同名フォルダがある場合もエラーです。**存在しないフォルダは作成しません**ので、
新しい階層に移したいときは SFTP 等で先に作っておいてください。
解析の実行中は移動できません。

移動後、結果フォルダ内の `_project_meta.json` をもとに `projects.json` と
移動先の `_project_meta.json` の参照先パスを自動更新します（登録済みならパス更新、未登録なら復元）。
次にそのサブプロジェクトを開いたときは、最初から新しいパスが使われます。

移動した時点でインタラクティブ解析タブがそのフォルダを開いていた場合は、結果フォルダ欄も
新しいパスへ差し替えます。ただし**データの再読込は自動では行いません** —— 移動直後は
Seurat 抽出キャッシュが効かず数分の再抽出になるためで、必要になったときに
「データを読み込む」を押してください。2 回目以降は通常速度に戻ります。

> shell で `docker exec msi-analysis-app mv ...` のように手動で移動した場合は、パスの貼り替えが
> 走りません。ランディングページの「復元」→ プロジェクト復元モーダルでスキャンし、対象を
> **「パス更新」** にして実行してください（データ管理サブタブの「↩ 復元」ボタンは既存プロジェクトの
> パスを更新しません）。

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
