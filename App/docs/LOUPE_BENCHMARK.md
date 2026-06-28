# Loupe Browser 9 ベンチマーク & 機能ギャップ分析

10x Genomics **Loupe Browser 9.1.0**（ローカル配布版 `bundle.js`）を解析し、本アプリの
UMAP インタラクティブ解析機能の更新指針としてまとめたもの。詳細な実装ロードマップは
リポジトリの計画ファイルおよび `CHANGELOG.md` を参照。

## Loupe Browser 9 の挙動（要点）

Loupe 9 は `.cloupe`/`.vloupe` を読むデスクトップ型エクスプローラ。中核思想は
**「選択 → 統計が即時更新 → 選択に名前を付けて再利用 → その場で検定/再計算 → 出力」** を
外部パイプラインに戻らずアプリ内で完結させること。

- **選択がすべての起点**: 矩形/ポリゴン/投げ縄/ペイントブラシで選んだ瞬間に下流
  （クラスタ統計・violin・heatmap・DE）がライブ再計算。選択は名前付き Group/Filter として
  永続化でき、結合・改名・barcode↔group の CSV 入出力、再クラスタへの再投入が可能。
- **アプリ内 on-the-fly DE**: 選んだクラスタ/群に対し **Globally Distinguishing**（選択 vs 全体）/
  **Locally Distinguishing**（選択 vs 指定群）をその場で実行。Log2FC・p値・MNA 列のソート可能
  テーブル → Top 10/20/50/100/All を CSV 出力。
- **Reanalyze/Recluster**: 部分集合化 + QC 閾値（violin ドラッグ）+ PCA/UMAP/t-SNE
  パラメータ露出（PCA 10–100, UMAP min_dist 既定 0.1 / n_neighbors 既定 15, t-SNE perplexity 既定 30）。
- **可視化**: feature 別カラースケール（パレット/min-max/log/反転）、Split View（カテゴリ分割）、
  2リスト共発現散布図、CSV バッチアップロードの feature 検索。
- **空間**: H&E/CytAssist 画像を fiducial/landmark で位置合わせ、組織レイヤー不透明度、
  ミクロンスケールバー、LDA スポット分解。

## 現アプリ（MSI）とのギャップ

| 優先 | Loupe 機能 | 現状 |
|---|---|---|
| 高 | アプリ内 on-the-fly DE | DEG は事前計算・閲覧専用。閾値は表示フィルタで検定ではない |
| 高 | 選択の永続化（Selection Groups）+ 双方向リンク | 矩形選択は一時的。UMAP→Spatial の一方向のみ |
| 高 | ライブ選択統計 | 任意選択の即時集計が無かった → **P1 で実装** |
| 中 | 投げ縄/ポリゴン選択, violin, カラースケール調整, ソート可能マーカー表 | **P1 で実装**（投げ縄=既定 modebar / violin / log・パレット / DataTable+Top-N） |
| 中 | 登録済み H&E を背景レイヤー重畳 + スポット透明度 | 位置合わせは実装済みだが背景表示なし |

## 適用外（MSI に対応概念なし）

VDJ/clonotype、ATAC/peak、Antibody/CRISPR feature-barcode、Visium fiducial アライメント、
LDA 分解。ただし「相関ペアのリンクテーブル」UX は将来の MSI 共局在エクスプローラに転用可能。

## 実装フェーズ

- **P1（実装済み, ver20.0）**: lasso/box 選択基盤 + 共有選択 Store、ライブ選択統計、
  feature カラースケール（log10/パレット/反転）、violin 分布、ソート可能マーカー表 + Top-N CSV。
- **P2**: アプリ内 on-the-fly DE（`run_findmarkers.R` + SeuratBridge + 背景callback、Globally/Locally）。
- **P3**: 選択グループ永続化 + 双方向リンクブラッシング。
- **P4**: H&E 背景重畳 + スポット透明度 + ミクロンスケールバー。
- **P5（任意）**: Split View、feature リスト + 共発現散布図、部分集合 再クラスタリング。
