# MVP4 実装状況（科学的信頼性の強化）

対象: `App/docs/EVALUATION_AND_ROADMAP_2026-06.md` の科学優先 MVP4 項目
版: 2026-06-30 / ver33.0

この文書は、MVP4 のうち**何が実装・テスト済みで、何が残っているか**を明確にするためのもの。
本コンテナには R と稼働中の Dash サーバが無いため、純 Python の中核は単体テストで検証済み、
R スクリプトと Dash 配線は静的レビュー＋インポート確認に留まる（実機検証は後続）。

## 実装・テスト済み（中核 / データ / 記録）

| 項目 | 追加/変更 | テスト |
|---|---|---|
| P1 由来取込・対応づけ・由来表示 | `services/annotation_sources.py`（新）、`peak_annotation.py` に `source`/`source_metrics` | `test_annotation_sources.py`（6）+ 後方互換 |
| P2 解析レシート集約 | `services/receipt.py`（新）、`analysis_callbacks.py` で完了時 `finalize_receipt`、`rds_io.R` に `write_receipt_sidecar()`、各アクティブ R テンプレ末尾から guarded 呼出 | `test_receipt.py`（7） |
| P3-c1 注意書きの一元化 | `services/caveats.py`（新） | `test_caveats.py`（4） |
| P3-c1 注意書き伝播（R） | DESI v16（PCA/Harmony/RPCA の all_markers CSV）、`run_findmarkers.R` に `ranking_type`/`inference_note` 列 | — (R) |
| P3-c1 注意書き表示（UI） | `interactive_tab.py`（DEG 先頭バナー）、`shared_view.py`（DEG バナー）、`interactive_pptx.py`（Volcano 注釈） | インポート確認 |
| P3-c2 空間自己相関 | `services/spatial_stats.py`（新, Moran's I numpy） | `test_spatial_stats.py`（7） |
| P3-c3 pseudobulk + sample-level | `services/pseudobulk.py`（新, Welch t + BH、t 分布 p は自前実装） | `test_pseudobulk.py`（6） |
| P4 安定性メトリクス | `services/stability.py`（新, ARI/Jaccard/silhouette/trustworthiness/旗） | `test_stability.py`（10） |
| P4 再クラスタリング計算 | `services/stability_runner.py`（新）、`Script/helpers/stability_diagnostics.R`（新, 独立追加） | `test_stability_runner.py`（3） |

`pytest -m "not e2e"`：全 **395 件パス**。新規依存なし（numpy/pandas/標準ライブラリのみ）。

## 残り（対話 UI / R 実機検証を要する後続作業）

いずれも既存機能を壊さない追加配線。`App/docs/EVALUATION_AND_ROADMAP_2026-06.md` と本リポジトリの
プラン（接続点付き）に沿って、R + Dash 稼働環境で実装・目視検証するのが安全。

- **P1 由来の対話表示・取込 UI**：`annotation_sources.build_feature_source_map()` の結果を RDS 隣の
  サイドカー JSON に保存し、DEG 表（`utils/deg_utils.standardize_deg_df` + `interactive_tab` の DataTable に
  `source` 列）、feature ピッカー（`interactive_feature_lists.py`）、Volcano ラベル（`interactive_deg.py`）で併記。
  取込モーダルは `scils_converter_modal.py` を雛形に。
- **P2 レシート閲覧 UI**：結果画面に `RECEIPT.md`/`receipt.json` の表示と DL（`results_viewer.py` を再利用）。
- **P3-c2/c3 結果パネル**：`spatial_stats.spatial_autocorr_table()` と `pseudobulk` の結果を出す新パネル
  （`interactive_tab.py` のアコーディオン節 + 新コールバック）。入力は `seurat_bridge.get_features_matrix()` と
  `plot_data.parquet`（`Sample`/`SpatialX`/`SpatialY`）。
- **P4 安定性パネル**：`interactive_tab.py` に節を追加、新 `callbacks/interactive_stability.py` を
  `callbacks/__init__.py` に登録、`stability_runner.run_stability()` を非同期ジョブで実行し、
  各クラスタに安定/要注意/不安定の旗を表示、不安定クラスタを DE 候補から除外する選択肢。
- **再現モード**：`analysis_runner.py` のスレッド設定（`OMP_NUM_THREADS`）を 1 に切替えるトグル + seed 記録。

## 検証メモ
- 本コンテナでは `pip install -r App/requirements.txt` 済みで Dash 各レイアウトの**インポート**は確認した。
- R は未インストールのため、R テンプレ変更（注意書き列・レシートサイドカー・安定性スクリプト）は
  実行未検証。いずれも `try()`/`get0()` で防御し、失敗しても既存解析を止めない設計。
