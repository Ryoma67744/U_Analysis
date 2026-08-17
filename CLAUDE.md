# U_Analysis 開発ガイド

質量分析イメージング (MSI) データの UMAP 解析アプリケーション。
Dash (Python) + R/Seurat を Docker で動かす。

このファイルは**複数の Claude セッションが並行して作業する**前提で書かれている。
特に「バージョン採番」は過去に実際の事故を起こしているので、必ず目を通すこと。

---

## リポジトリ構成

| パス | 内容 |
|---|---|
| `App/app/` | Dash アプリ本体（`callbacks/` `layouts/` `services/`） |
| `App/Script/` | R 解析スクリプト（Seurat / UMAP / DEG） |
| `App/tests/` | pytest スイート（約 1,700 件） |
| `App/docs/` | デプロイ手順・データ形式・監査レポート |
| `CHANGELOG.md` | 全リリースの記録。**バージョン採番の正** |
| `.github/workflows/` | CI（現状はバージョン検査のみ） |

---

## バージョン採番（最重要）

**リリースのたびに次の 3 点を必ず同期する。**

1. `App/app/version.py` の `APP_VERSION` と `RELEASE_DATE`
2. `CHANGELOG.md` の先頭に新エントリ（見出しは `## <YYYY-MM-DD>_ver<番号>`）
3. コミットタイトルの末尾に `[verX.Y]`

付番: **バグ修正のみ → +0.1** / **機能追加 → +1.0**

### 採番は「作業開始時」ではなく「PR を出す直前」に決める

これが最も重要なルール。過去の事故はすべてここを守らなかったことが原因。

```bash
# PR を出す直前に必ず実行する
git fetch origin main
git show origin/main:App/app/version.py | grep APP_VERSION   # ← 現在の最新
```

### さらに、open PR の番号も確認する

**`main` を見るだけでは不十分。** 他のセッションが未マージの PR で
すでに次の番号を使っている場合があるため、open PR 側の `APP_VERSION` も見る。

```bash
# 全リモートブランチの APP_VERSION を一覧する
for b in $(git branch -r --format='%(refname:short)' | grep -v HEAD); do
  v=$(git show "$b:App/app/version.py" 2>/dev/null | grep -oP 'APP_VERSION = "\K[^"]+')
  echo "$v  <- $b"
done | sort -rV
```

自分が採る番号は、**main と全 open PR のどれよりも大きい**こと。

### 実際に起きた事故（2026-08）

- PR #154 と PR #155 が同じ main (ver56.3) から分岐し、**両方が ver56.4 を名乗った**。
  #155 が先にマージされて main は ver56.8 になり、#154 は「マージすると
  56.8 → 56.7 に後退する」状態で取り残された。
- 同じ重複は過去にもある。`ver38.1` と `ver43.0` は、main と未マージブランチで
  **まったく別の変更**に割り当てられている。

### 衝突してしまったときの解決手順

後からマージする側が番号を採り直す。手順:

1. `git fetch origin main`
2. **`main` を自分のブランチに merge する**（rebase しない）
3. 競合するのは `App/app/version.py` と `CHANGELOG.md` のほぼ 2 ファイルだけ。
   - `version.py`: main より大きい番号に採り直す
   - `CHANGELOG.md`: 両側のエントリを残し、バージョン降順（新しいものが上）に並べる
4. コミットタイトルの `[verX.Y]` も採り直した番号に合わせる

> **rebase ではなく merge を使う理由**: コミット数の多い PR を rebase すると
> force-push が必要になり、他セッションの履歴とレビューの紐付けを壊す。
> merge ならマージコミットを捨てるだけで元に戻せる。

### 自動検査

- `App/tests/test_version_consistency.py` — `APP_VERSION` と CHANGELOG 先頭の一致 /
  番号の重複なし / 見出しが降順。**マージ後**の不整合を検出する
- `.github/workflows/version-guard.yml` — PR の `APP_VERSION` が base より大きいことを
  検査する。**他 PR との衝突をマージ前に**捕まえられるのはこちらだけ

---

## テスト

```bash
cd App
python3 -m pytest tests/ -q                    # 全件
python3 -m pytest tests/test_scils_converter.py -v   # 個別
```

`pyproject.toml` にマーカーを定義してある:

- `e2e` — Playwright によるブラウザテスト
- `requires_data` — 実データ（R/Seurat + 結果フォルダ RDS）が必要

### 既知の失敗（2026-08 時点）

依存を未ピンのまま最新版で入れると、`test_hne_overlay` / `test_render_payload` /
`test_peak_annotation` など **11 件が落ちる**。`requirements.txt` の numpy / pyarrow に
上限ピンが無く、本番の想定より新しい版が入るため。**変更を加えていない main でも
同じ 11 件が落ちる**ので、テスト結果を見るときはこの分を差し引くこと。

この件があるため CI では全テストを回していない（バージョン検査のみ）。
依存をピン留めできたら `.github/workflows/version-guard.yml` に追加する。

---

## ブランチ運用

- `claude/<topic>` から `main` へ PR を出す
- **`main` へ直接 push しない**
- 作業前に必ず `git fetch origin main` して最新から分岐する

### 履歴が独立した古いブランチに注意

リポジトリの履歴は **2026-07-06 頃に作り直されている**（main のルートは `8fef116`）。
それ以前のブランチ（ルート `37f91ef` / 2026-02-14）は main と**共通の祖先を持たず**、
通常の merge では取り込めない。該当ブランチと棚卸し結果は
`App/docs/BRANCH_INVENTORY_2026-08.md` を参照。

---

## コーディング規約

- コメント・ドキュメント・コミットメッセージは**日本語**
- 挙動を変えた箇所には `★ verX.Y:` で始まるコメントを添え、
  **なぜ前の実装では駄目だったのか**を書く（このリポジトリの既存コードがそうなっている）
- 定数には実測値の根拠をコメントで残す
- バグ修正には必ず回帰テストを付け、**修正を戻すとそのテストが落ちること**を確認する

---

## デプロイ

本番は Docker イメージを実行しているため、Python のみの変更でも再ビルドが必要。

```bash
docker compose up -d --build
```

メモリ上限は `docker-compose.yml` の `mem_limit`（既定 `12g`）。
サーバー全体メモリの 75% 以下が目安。詳細は `App/docs/DEPLOY.md`。
