# ブランチ棚卸し（2026-08-17 時点）

`main` = `17cf913` / ver56.8 を基準に、リモートブランチ全 33 本を調査した結果。

**24 本は取り込み済み**（`main` の祖先）。残り **9 本が未取り込み**で、性質は 3 種類に分かれる。

> 本ドキュメントは**判断材料の提示のみ**。ブランチ削除・PR クローズは実行していない。

---

## サマリ

| # | ブランチ | PR | ver | 最終更新 | 分類 | 推奨アクション |
|---|---|---|---|---|---|---|
| 1 | `app-debug-investigation-kbq4lz` | #154 | 56.7 | 2026-08-16 | A. 未リリース | 番号を採り直してマージ |
| 2 | `intensity-matrix-negative-values-oic4u6` | #113 | 43.0 | 2026-07-09 | A. 未リリース | 要否を判断 → 採番し直し |
| 3 | `export-progress-check-wuofcu` | #103 | 38.1 | 2026-07-03 | A. 未リリース | 要否を判断 → 採番し直し |
| 4 | `parquet-format-inconsistencies-nzfwh3` | **なし** | 42.3 | 2026-07-15 | A. 未リリース | **要確認**（PR すら無い） |
| 5 | `reumap-resume` | なし | 46.0 | 2026-07-27 | B. 取り込み済み | 削除可 |
| 6 | `gracious-heisenberg-jfyp8q` | なし | 33.0 | 2026-06-30 | C. 孤立履歴 | 削除可 |
| 7 | `loupe-browser-analysis-lmfd9p` | なし | 32.1 | 2026-06-29 | C. 孤立履歴 | 削除可 |
| 8 | `lucid-darwin-fqxm71` | なし | 19.6 | 2026-06-27 | C. 孤立履歴 | 削除可 |
| 9 | `rds-hyperparameter-reflection-judetq` | #80 | 19.5 | 2026-06-28 | C. 孤立履歴 | **PR #80 はクローズ**（マージ不能） |

---

## A. 未リリースの作業（4 本）

`main` と共通の祖先を持ち、内容が `main` に入っていないもの。

### 1. `app-debug-investigation-kbq4lz` — PR #154（22 コミット / 72 ファイル）

デバッグ監査レポート（`DEBUG_AUDIT_2026-08.md` ほか）と、auth パスの可搬性修正・
コールバック出力先の欠落修正。`ver56.4` → `ver56.7` と 4 段階でバージョンを上げている。

**問題**: base が古い `69b8cf1` のままで、`APP_VERSION = 56.7` は現在の `main`（56.8）より小さい。
**このままマージするとバージョンが後退する。**

競合するのは `App/app/version.py` と `CHANGELOG.md` の **2 ファイルだけ**（試算で確認済み）。
残り 70 ファイルは自動マージされ、ソースコードの実体は `main` の ver56.8 の変更
（`scils_converter.py` / `parquet_repack.py`）と重ならない。

**推奨**: `main` を**マージ**（rebase しない）して `APP_VERSION` を 57.0 以降に採り直す。
22 コミットのタイトル `[ver56.4]`〜`[ver56.7]` と CHANGELOG の各エントリはそのまま残せば、
現在空いている 56.4〜56.7 の欠番も埋まる。

### 2. `intensity-matrix-negative-values-oic4u6` — PR #113（1 コミット / 7 ファイル）

ChatGPT 連携の応答に「フル解析画面を開くリンク」(`view_url`) を追加。
`App/app/services/deeplink.py` を新設する。

**`main` に `deeplink.py` は存在しない** ＝ 内容は未取り込み。

⚠️ **番号が重複している**: このブランチは `ver43.0` を名乗るが、`main` の `ver43.0` は
「化合物名表示を全表示面に統一」という**別の変更**。マージするなら採番し直しが必須。

### 3. `export-progress-check-wuofcu` — PR #103（1 コミット / 3 ファイル）

MetaboAnalyst 出力の強度選択の意味を UI に明記（挙動不変のドキュメント修正）。

⚠️ **番号が重複している**: `ver38.1` は `main` では
「強度/発現の下流を常に測定アッセイ (Spatial) から読む」という**別の変更**。

### 4. `parquet-format-inconsistencies-nzfwh3` — **PR なし**（2 コミット / 6 ファイル）

インタラクティブ Parquet 出力を登録入力と同一構造に揃える修正（`ver42.3`）。
`interactive_data_export.py` / `data_manager.py` を変更し、`test_data_export.py` に
**184 行のテストを追加**している。

**PR が存在しないため、誰の目にも触れないまま放置されている。** 内容が Parquet 出力構造と
`data_manager.py` で、ver56.8 で修正した SCiLS 変換の出力仕様と隣接するため、
**このリストの中で最も確認の優先度が高い**。

`main` の CHANGELOG に `ver42.3` のエントリは無く、未リリースであることが確認できる。

---

## B. 実質的に取り込み済み（1 本）

### 5. `reumap-resume`

「再解析（クラスタ除外→再 UMAP）の途中から再開」を UI に追加する `ver46.0`。

同機能を含む **`reumap-resume-ui` がすでにマージ済み**で、`main` の `ver46.0` は
「機能: 再解析（クラスタ除外→再UMAP）を Step1/Step2 から再開できるようにした」。
`main` の `settings_tab.py` に「再開」の実装が 5 箇所ある。

**重複ブランチなので削除して差し支えない。**

---

## C. 履歴が独立していてマージ不能（4 本）

### 6-9. `gracious-heisenberg-jfyp8q` / `loupe-browser-analysis-lmfd9p` / `lucid-darwin-fqxm71` / `rds-hyperparameter-reflection-judetq`

これら 4 本は **`main` と共通の祖先を 1 つも持たない**。

```
main のルートコミット     : 8fef116  (2026-07-06)
これらのブランチのルート  : 37f91ef  (2026-02-14)
```

2026-07-06 頃にリポジトリの履歴が作り直されており、これらは**作り直し前の旧履歴**に属する。
`git merge-base` が空を返すため通常の merge / rebase では取り込めず、
`git diff` で比べると 200〜242 ファイルが「差分」として出る（実際には別系統の履歴）。

バージョンは `ver19.5` / `ver19.6` / `ver32.1` / `ver33.0` と古く、`main` はすでに ver56.8。
内容は履歴の作り直し時に移行済みと考えられる。

**推奨**:
- 4 本とも削除可（必要なら削除前にタグを打って参照可能な形で残す）
- **PR #80 (`rds-hyperparameter-reflection-judetq`) は原理的にマージできないためクローズする**

---

## 付録: 調査に使ったコマンド

```bash
git fetch origin --prune

# 取り込み済み / 未取り込みの判定
git branch -r --merged   origin/main
git branch -r --no-merged origin/main

# 各ブランチの ahead/behind・バージョン・孤立判定
for b in $(git branch -r --no-merged origin/main --format='%(refname:short)' | grep -v HEAD); do
  ab=$(git rev-list --left-right --count origin/main..."$b")
  v=$(git show "$b:App/app/version.py" 2>/dev/null | grep -oP 'APP_VERSION = "\K[^"]+')
  mb=$(git merge-base origin/main "$b" 2>/dev/null)
  echo "$b | ahead/behind=$ab | ver=$v | $([ -z "$mb" ] && echo 孤立 || echo 通常)"
done

# マージ時の競合を非破壊で試算する
git merge-tree --write-tree --name-only <branch> origin/main
```

---

## 再発防止

バージョン番号の重複（`ver38.1` / `ver43.0` / `ver56.4`）は、複数のセッションが
並行して `main` から分岐し、それぞれ作業開始時点の番号を採ったために起きている。
ver56.9 で以下を導入した:

- `App/tests/test_version_consistency.py` — マージ後の不整合を検出
- `.github/workflows/version-guard.yml` — PR の `APP_VERSION` が base より大きいことを検査
- `CLAUDE.md` — 採番は PR 提出直前に行い、open PR の番号も確認するルールを明文化
