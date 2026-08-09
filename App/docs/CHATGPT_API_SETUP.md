# ChatGPT 連携 API (`/api/gpt/*`) セットアップ手順

MSI 解析アプリの**読み取り専用 API** を ChatGPT の Custom GPT (Action) から
呼べるようにする手順と、繋がらないときの切り分け。

対象構成: さくら VPS + Docker Compose + Caddy リバースプロキシ + DuckDNS
（`App/docs/DEPLOY.md` の「選択肢C」でデプロイ済みであること）

---

## 0. 仕組み（1 分で読める分）

| 要素 | 実体 |
|---|---|
| 認証 | HTTP ヘッダ **`X-API-Key`** と `GPT_API_KEY` の定数時間比較 |
| 鍵の置き場 | **サーバ側 `.env` のみ**。OpenAPI 仕様書にも URL にも出さない |
| 鍵が未設定のとき | `/api/gpt/*` は **503**（fail-closed。無防備公開を防ぐ） |
| 鍵が要らない窓口 | `/api/gpt/health` と `/api/gpt/openapi.json` の 2 本だけ |
| ChatGPT が最初に読むもの | `/api/gpt/openapi.json`（この中の `servers` URL へ以後アクセスする） |

★ **`servers` の URL が https でないと ChatGPT は取り込めません。** ver52.6 より前は
ここが `http://` になる不具合があり、「ブラウザでは開けるのに ChatGPT からだけ
繋がらない」状態でした（詳細は末尾の「既知の不具合」）。

---

## 1. サーバ側の設定

`.env`（リポジトリ直下）に 2 行。**鍵は毎回生成してください**（使い回さない）。

```bash
cd ~/U_Analysis          # デプロイ先のリポジトリ

# 合言葉を生成して追記（生成した値は後で ChatGPT 側に貼るので控えておく）
echo "GPT_API_KEY=$(openssl rand -hex 32)" >> .env

# 公開アドレスを明示（共有リンクと OpenAPI の servers が両方これを使う）
echo "SHARE_BASE_URL=https://cciiumap.duckdns.org" >> .env
```

反映（再起動が要ります。`.env` は起動時にしか読まれません）:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

鍵を確認したいとき（画面には出ません。サーバ上でだけ見えます）:

```bash
grep '^GPT_API_KEY=' .env | cut -d= -f2
```

> **`SHARE_BASE_URL` を省略してもよいか**
> Caddyfile が `header_up X-Forwarded-Proto https` を送っているので、省略しても
> https に解決されます。ただし明示しておくほうが、プロキシ設定を変えたときに
> 巻き添えで壊れません。**推奨は明示。**

---

## 2. 確認 3 本（この順に通れば必ず繋がります）

サーバ上でも手元の PC でも構いません。

### ① 窓口が開いているか・何を名乗るか

```bash
curl -s https://cciiumap.duckdns.org/api/gpt/health | jq
```

```json
{
  "ok": true,
  "app_version": "2026-08-09_ver52.7",
  "gpt_api": "enabled",
  "public_base_url": "https://cciiumap.duckdns.org",
  "openapi_url": "https://cciiumap.duckdns.org/api/gpt/openapi.json",
  "https": true,
  "key_header_received": false,
  "authenticated": false
}
```

- `"gpt_api": "disabled"` → **鍵が未設定**。手順 1 に戻る
- `"https": false` → **仕様書が http を名乗る**。ChatGPT は取り込めない。`SHARE_BASE_URL`
  を設定するか、リバースプロキシが `X-Forwarded-Proto` を送っているか確認する

★ `key_header_received` / `authenticated` は**そのリクエストが鍵を持っていたか**を
表します。上の `curl` は鍵を付けていないのでどちらも `false` で正常です。
**この 2 つは ChatGPT 側の設定を切り分けるためにあります**（下記 4 節）。

### ② 仕様書が正しいアドレスを指しているか

```bash
curl -s https://cciiumap.duckdns.org/api/gpt/openapi.json | jq .servers
```

```json
[{"url": "https://cciiumap.duckdns.org"}]
```

### ③ 鍵が通るか

```bash
KEY=$(ssh <サーバ> "grep '^GPT_API_KEY=' ~/U_Analysis/.env | cut -d= -f2")
curl -s -H "X-API-Key: $KEY" https://cciiumap.duckdns.org/api/gpt/projects | jq
```

`{"ok": true, "projects": [...]}` が返れば、サーバ側の準備は完了です。

---

## 3. ChatGPT 側の設定

1. ChatGPT → **Explore GPTs** → **＋ Create** → **Configure** タブ
2. 一番下の **Actions** → **Create new action**
3. **Import from URL** に貼る:
   ```
   https://cciiumap.duckdns.org/api/gpt/openapi.json
   ```
   → 取り込むと `listProjects` / `getProject` / `getClusters` / `getMarkers` /
   `searchCompounds` / `listOutputs` / `listExports` /
   `startInteractiveExport` / `getExportJob` / `health` が並びます
4. **Authentication** → **API Key**
   - Auth Type: **API Key**
   - API Key: 手順 1 で生成した値
   - Auth Type(詳細): **Custom**
   - Custom Header Name: **`X-API-Key`** ← ここを `Authorization` にすると 401 です
5. **Available actions** の `health` の **Test** を押す → `"ok": true` が返れば結線完了

### Instructions に書いておくとよいこと

```
- 解析手法 (method) を明示しない場合、既定順の先頭が使われます。
  指定した手法の結果が無ければ 409 が返り、別手法で代用はされません。
- マーカー取得 (getMarkers) の top は既定 10・上限 50 です。
  応答が大きいときはサーバ側で件数を削り、その旨を応答に明示します。
```

---

## 4. 繋がらないときの切り分け

| 症状 | 原因 | 対処 |
|---|---|---|
| Import from URL が弾かれる／Action が動かない | `servers` が `http://` | 確認①の `"https"` を見る。`SHARE_BASE_URL` を設定して再起動 |
| `health` は 200 だが他が全部 **503** | `GPT_API_KEY` が未設定 | 手順 1。`.env` 編集後は**再起動が必要** |
| **401** が返る | ヘッダ名違い **or** 鍵不一致 | **下記「401 の切り分け」で必ず区別してから直す** |
| ブラウザでは開けるのに ChatGPT からは繋がらず、**アクセスログにも残らない** | 空 SNI での TLS 握手拒否 | `Caddyfile` の `default_sni cciiumap.duckdns.org`（設定済み）。消さないこと |
| **404** が返る | パスの綴り | すべて `/api/gpt/` 配下。末尾スラッシュは付けない |
| `ResponseTooLargeError` | 応答が Actions の上限超 | `top` を指定する。サーバ側でも削って明示するが、指定するほうが確実 |
| **409** が返る | 指定した解析手法の結果が無い | **別手法で代用はされません**。409 の応答本文に `available_methods` が入るので、その中から選び直す |
| 一時的に **502/504** | 解析実行中でアプリが重い | 解析完了を待つ。Caddy 側は 600s まで待つ設定 |

### 401 の切り分け（ヘッダ名違いか、鍵不一致か）

サーバが返す 401 は `invalid **or** missing API key` の 1 文で、**どちらかを
区別しません**。区別は `/api/gpt/health` にさせます。この窓口は鍵不要なので、
**認証を設定した Action なら鍵ヘッダが届き**、その事実だけが返ります。

ChatGPT に `health` を呼ばせて（Available actions の `health` → Test）、
応答の 2 つの真偽値を見ます:

| `key_header_received` | `authenticated` | 意味 | 直すところ |
|---|---|---|---|
| `false` | `false` | **鍵が届いていない** | Action の Authentication が未設定、または Custom Header Name が `X-API-Key` になっていない |
| `true` | `false` | **届いているが値が違う** | ChatGPT に貼った鍵が `.env` と不一致。貼り直す（欄はマスク表示なので**全選択してから**貼り替える） |
| `true` | `true` | 鍵は正しい | 401 は別原因。アプリのログを見る |

★ 鍵そのものは応答にもログにも出ません。出るのは上の真偽値だけです。

サーバ側のログにも拒否が 1 行残ります（**値は出ません。有無だけ**）:

```bash
docker compose logs --tail=100 msi-app | grep "GPT API 拒否"
#   GPT API 拒否: path=/api/gpt/projects status=401 X-API-Key=なし   ← 未設定
#   GPT API 拒否: path=/api/gpt/projects status=401 X-API-Key=あり   ← 値が違う
```

鍵を貼り直したのに直らないときは、**GPT 編集画面右上の「Update」を押したか**を
確認してください。認証ダイアログの Save だけでは反映されないことがあります。

サーバ側の値を確認する（値を表示せず指紋で比べる）:

```bash
cd ~/UMAP-WebApp-ClaudeCode
grep '^GPT_API_KEY=' .env | cut -d= -f2- | tr -d '\n\r' | sha256sum | cut -c1-8
docker compose exec msi-app printenv GPT_API_KEY | tr -d '\n\r' | sha256sum | cut -c1-8
```

2 つが違えば `.env` 編集後に**再起動していません**。

### ログの見方

```bash
docker compose logs -f msi-app  | grep -i "gpt\|openapi"   # アプリ側
docker compose logs -f caddy                               # プロキシ側 (json)
```

★ リバースプロキシ (Caddy) のアクセスログは `Cookie` と `Authorization` は
伏せますが、**`X-API-Key` は伏せません**。`docker logs msi-caddy` を素で開くと
鍵が平文で見えるので、共有する前に必ず確認してください。

`servers` が https でないときは、アプリのログに次の警告が 1 行出ます:

```
OpenAPI の servers が http://... で https ではない。ChatGPT の Action は
https を要求するため取り込めない。SHARE_BASE_URL を設定するか、
リバースプロキシが X-Forwarded-Proto を送っているか確認すること。
```

---

## 5. 鍵の取り扱い

- **リポジトリに実値を書かない。** `.env` は `.gitignore` 済み。
  `.env.docker` の `# GPT_API_KEY=CHANGE_ME_TO_RANDOM_HEX_64` は
  **コメントのまま**にすること（`#` を外すと、公開リポジトリに書かれている
  文字列が有効な鍵になり、窓口が無防備に開きます）
- **画面には出ません。** 設定 UI からは編集できず、`/api/gpt/health` も
  「設定済みか（`enabled`/`disabled`）」しか返しません
- **鍵を替えるとき**は `.env` を書き換えて再起動し、ChatGPT の Action 側の
  API Key も同じ値に更新する（両方替えないと 401 になります）

---

## 6. 既知の不具合（ver52.6 で修正済み）

ver52.5 以前は、`/api/gpt/openapi.json` が `request.url_root` から `servers` を
組み立てていました。Flask の `request.scheme` は `X-Forwarded-Proto` を読まない
ため、Caddy 配下では `http://cciiumap.duckdns.org` を名乗り、https を要求する
ChatGPT の Action から使えませんでした。

共有リンク側は `SHARE_BASE_URL → external_base_url()` を通していて正しく https
だったため、**同じアプリが 1 リクエストから 2 通りの公開 URL を作っている**状態でした。
ver52.6 で出どころを 1 つに揃え、`tests/test_public_url_is_consistent.py` で
「公開 URL を組み立ててよいのは `url_utils` だけ」を全数検査しています。

---

## 関連

- `App/docs/DEPLOY.md` — デプロイ全体手順（Caddy / HTTPS を含む）
- `App/docs/DATA_LAYOUT.md` — サーバー上のディレクトリ構成
- `App/.env.example` — 設定値の雛形
- `Caddyfile` — リバースプロキシ設定（`default_sni` の理由もここに記載）
