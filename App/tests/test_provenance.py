"""provenance（解析条件の収集・記録）の単体テスト。

要点は 2 つ:
  - 結果フォルダの解決が、キャッシュ配下（derived_pca）で None になること。
  - 欠損値を捏造せず `_missing` に載せること。
"""
import json

from app.services import provenance as pv

PARAMS = {
    "analysis_type": "tims_v8",
    "data_folder": "/data/TIMS",
    "output_dir": "/out/run1",
    "ion_mode": "Positive",
    "tolerance_mz": 0.01,
    "p_thresh": 0.05,
    "logfc_thresh": 0.25,
    "umap_n_neighbors": 30,
    "umap_min_dist": 0.3,
    "umap_metric": "cosine",
    "umap_dims_n": 30,
    "template_path": "/app/Script/TIMS/tmpl_v6.R",
    "template_sha256": "abc123",
    "norm_mode": "log1p",
    "input_normalized": False,
    "sample_names": ["S1", "S2"],
    "operator": "tanaka",
}

RECEIPT = {
    "instrument": {"app_version": "2026-07-28_ver47.0", "r_version": "4.3",
                   "packages": {"r": {"Seurat": "5.0.1"}, "python": {"python": "3.11.5"}}},
    "object": {
        "analysis_type": "tims_v8",
        "umap": {"n_neighbors": 30, "min_dist": 0.3, "metric": "cosine",
                 "dims": 30, "seed": 42},
        "clustering": {"algorithm": "leiden", "resolution": 0.8, "k_param": 20},
        "preprocessing": {"norm_mode": "log1p", "input_normalized": False,
                          "batch_correction": "harmony"},
        "annotation": {"ion_mode": "Positive", "tolerance_mz": 0.01},
        "thresholds": {"p": 0.05, "logfc": 0.25},
        "pipeline": {"template_path": "/app/Script/TIMS/tmpl_v6.R"},
    },
    "startTime": "2026-07-28T10:00:00",
    "endTime": "2026-07-28T11:00:00",
    "agent": {"operator": "tanaka"},
    "result": {"output_dir": "/out/run1"},
}


def _make_result_dir(tmp_path):
    out = tmp_path / "Analysis_20260728"
    (out / "RDS_Files").mkdir(parents=True)
    (out / "log").mkdir()
    (out / "analysis_params.json").write_text(
        json.dumps(PARAMS), encoding="utf-8")
    (out / "receipt.json").write_text(json.dumps(RECEIPT), encoding="utf-8")
    (out / "log" / "v8_runtime_20260728_100000.R").write_text(
        "OUTPUT_DIR <- 'x'\n", encoding="utf-8")
    rds = out / "RDS_Files" / "Step2_harmony.rds"
    rds.write_bytes(b"not-a-real-rds")
    return out, rds


# ---------------------------------------------------------------------------
# results_dir_for_rds
# ---------------------------------------------------------------------------

def test_results_dir_from_rds_files_subdir(tmp_path):
    out, rds = _make_result_dir(tmp_path)
    assert pv.results_dir_for_rds(str(rds)) == out


def test_results_dir_when_rds_sits_directly_in_result_dir(tmp_path):
    out = tmp_path / "run"
    out.mkdir()
    rds = out / "obj.rds"
    rds.write_bytes(b"x")
    assert pv.results_dir_for_rds(str(rds)) == out


def test_results_dir_explicit_folder_wins(tmp_path):
    out, rds = _make_result_dir(tmp_path)
    other = tmp_path / "explicit"
    other.mkdir()
    assert pv.results_dir_for_rds(str(rds), str(other)) == other


def test_results_dir_is_none_for_cache_derived_rds(tmp_path, monkeypatch):
    """derived_pca はキャッシュ上にしか無いので結果フォルダを持たない。"""
    cache = tmp_path / "cache"
    (cache / "derived_pca").mkdir(parents=True)
    rds = cache / "derived_pca" / "derived.rds"
    rds.write_bytes(b"x")
    monkeypatch.setattr("app.config.SEURAT_CACHE_DIR", cache, raising=False)
    assert pv.results_dir_for_rds(str(rds)) is None


def test_results_dir_none_input():
    assert pv.results_dir_for_rds(None) is None


# ---------------------------------------------------------------------------
# collect_conditions
# ---------------------------------------------------------------------------

def test_collect_conditions_reads_receipt(tmp_path):
    out, rds = _make_result_dir(tmp_path)
    c = pv.collect_conditions(rds_path=str(rds), integration_method="Harmony")
    assert c["conditions_version"] == pv.CONDITIONS_VERSION
    assert c["integration_method"] == "Harmony"
    assert c["analysis"]["umap"]["seed"] == 42
    assert c["analysis"]["clustering"]["algorithm"] == "leiden"
    assert c["software"]["r_version"] == "4.3"
    assert c["pipeline"]["template_path"] == "/app/Script/TIMS/tmpl_v6.R"
    # 実行スクリプトが sha256 付きで拾えている
    assert c["pipeline"]["runtime_script"].endswith("v8_runtime_20260728_100000.R")
    assert c["pipeline"]["runtime_script_sha256"]
    assert c["_missing"] == []


def test_collect_conditions_lists_missing_without_inventing(tmp_path):
    """条件が無い状態でも既定値をでっち上げず、_missing に列挙する。"""
    c = pv.collect_conditions(rds_path=None)
    assert "analysis.umap.n_neighbors" in c["_missing"]
    assert "analysis.clustering.algorithm" in c["_missing"]
    assert c["analysis"]["umap"]["n_neighbors"] is None
    assert c["analysis"]["thresholds"]["p"] is None


def test_collect_conditions_warns_for_cache_only_embedding(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    (cache / "derived_pca").mkdir(parents=True)
    rds = cache / "derived_pca" / "derived.rds"
    rds.write_bytes(b"x")
    monkeypatch.setattr("app.config.SEURAT_CACHE_DIR", cache, raising=False)
    c = pv.collect_conditions(rds_path=str(rds), integration_method="PCA")
    assert c["result_dir"] is None
    # ver48.0: 警告はコードで持つ（英語 Methods に日本語が出ないように）
    codes = {w["code"] for w in c["warnings"]}
    assert "cache_only_embedding" in codes
    assert "derived_pca_not_persisted" in codes


def test_collect_conditions_includes_fixed_onthefly_params(tmp_path):
    c = pv.collect_conditions(rds_path=None)
    fixed = c["onthefly_de_fixed_params"]
    assert fixed["test"] == "wilcox"
    assert fixed["min_pct"] == 0.05
    assert fixed["logfc_threshold"] == 0.25


# ---------------------------------------------------------------------------
# write_export_record
# ---------------------------------------------------------------------------

def test_write_export_record_creates_file(tmp_path):
    out, rds = _make_result_dir(tmp_path)
    c = pv.collect_conditions(rds_path=str(rds))
    path = pv.write_export_record(out, "pptx", c)
    assert path is not None and path.exists()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["export_kind"] == "pptx"
    assert data["exported_at"]
    assert path.parent.name == pv.PROVENANCE_DIRNAME


def test_write_export_record_noop_without_result_dir():
    assert pv.write_export_record(None, "csv", {"a": 1}) is None


def test_write_export_record_sanitizes_kind(tmp_path):
    out, _ = _make_result_dir(tmp_path)
    path = pv.write_export_record(out, "batch/zip umap", {})
    assert path is not None
    assert "/" not in path.name and " " not in path.name


def test_conditions_json_bytes_roundtrip():
    payload = pv.conditions_json_bytes({"a": 1, "b": "日本語"})
    assert json.loads(payload.decode("utf-8"))["b"] == "日本語"


def test_write_conditions_bundle(tmp_path):
    out, rds = _make_result_dir(tmp_path)
    c = pv.collect_conditions(rds_path=str(rds))
    paths = pv.write_conditions_bundle(out, c)
    assert paths["conditions"].exists()
    assert paths["methods_ja"].exists()
    assert paths["methods_en"].exists()
    assert "解析条件" in paths["methods_ja"].read_text(encoding="utf-8")
    assert "Methods draft" in paths["methods_en"].read_text(encoding="utf-8")


def test_latest_runtime_script_picks_newest(tmp_path):
    out = tmp_path / "run"
    (out / "log").mkdir(parents=True)
    (out / "log" / "v8_runtime_20260101_000000.R").write_text("a", encoding="utf-8")
    (out / "log" / "v8_runtime_20260202_000000.R").write_text("b", encoding="utf-8")
    assert pv.latest_runtime_script(out).name == "v8_runtime_20260202_000000.R"


def test_latest_runtime_script_missing_dir(tmp_path):
    assert pv.latest_runtime_script(tmp_path / "nope") is None


# ---------------------------------------------------------------------------
# ver52.3 ②: 再解析の実行スクリプトを証跡として拾う
# ---------------------------------------------------------------------------
# 従来は `v8_runtime_*.R` しか見ておらず、再解析が書く
# `cluster_filter_runtime_*.R` (analysis_runner.py:754) を **収集側 2 箇所とも**
# 取りこぼしていた。その結果、再解析の結果だけ Methods の
# 「実際に走った条件」の裏付けが丸ごと欠けていた。

def test_latest_runtime_script_finds_reanalysis_script(tmp_path):
    """★ 再解析だけが走った結果でも証跡が付くこと（修正前は None）。"""
    out = tmp_path / "rerun"
    (out / "log").mkdir(parents=True)
    (out / "log" / "cluster_filter_runtime_20260808_120000.R").write_text(
        "x", encoding="utf-8")
    got = pv.latest_runtime_script(out)
    assert got is not None, "再解析の実行スクリプトが証跡として拾われていない"
    assert got.name == "cluster_filter_runtime_20260808_120000.R"


def test_latest_runtime_script_prefers_newest_across_prefixes(tmp_path):
    """★★ 名前順で選ぶ実装だと、古い通常解析が新しい再解析に勝ってしまう。

    `sorted()` は文字列順なので `c` < `v`、つまり `cluster_filter_…` は
    どれだけ新しくても常に `v8_…` に負ける。これを踏むと
    「証跡が無い」が「**間違った証跡を出す**」へ格下げされる
    （＝直そうとした型を新たに作る）。mtime で選べば起きない。
    """
    import os

    out = tmp_path / "mixed"
    (out / "log").mkdir(parents=True)
    old = out / "log" / "v8_runtime_20260101_090000.R"
    new = out / "log" / "cluster_filter_runtime_20260808_180000.R"
    old.write_text("old", encoding="utf-8")
    new.write_text("new", encoding="utf-8")

    # 名前順では old が最後に来る（= 誤って選ばれる側）
    assert sorted(p.name for p in (out / "log").iterdir())[-1] == old.name

    # mtime は new のほうが後
    os.utime(old, (1_700_000_000, 1_700_000_000))
    os.utime(new, (1_800_000_000, 1_800_000_000))

    got = pv.latest_runtime_script(out)
    assert got is not None and got.name == new.name, (
        f"名前順で選んでいる（{got.name if got else None} が選ばれた）。"
        "接頭辞が順序を決めるので、古い通常解析が新しい再解析に勝ってしまう")


def test_receipt_reuses_the_same_resolver(tmp_path):
    """★ 収集ロジックの写しを 2 つ持たないこと。

    従来は receipt.py にも `sorted(glob("v8_runtime_*.R"))[-1]` の写しがあり、
    **両方とも**再解析を取りこぼしていた。写しを持つと必ず片方が古くなる。
    """
    import ast
    import inspect

    from app.services import receipt as rc

    src = inspect.getsource(rc)
    assert "latest_runtime_script" in src, \
        "receipt が provenance の解決関数を再利用していない"

    # ★ ベタ書きの検出は **AST** で見る。ソースの文字列一致だと
    #   「なぜ写しを持ってはいけないか」を説明したコメント自身に当たる
    #   （実際そうなって落ちた）。型を文字列で近似しない。
    hardcoded = [
        node.args[0].value
        for node in ast.walk(ast.parse(src))
        if (isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "glob"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
            and "runtime" in node.args[0].value)
    ]
    assert not hardcoded, f"receipt にベタ書きの glob が残っている: {hardcoded}"
