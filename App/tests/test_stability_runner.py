"""stability_runner（R 再計算の橋渡し・ラベル集約）の単体テスト。"""
import pandas as pd

from app.services import stability_runner as sr


def test_build_rscript_cmd():
    cmd = sr.build_rscript_cmd("/x/a.rds", "/out", "/helpers",
                               seeds=[42, 1, 2], subsample_frac=0.8)
    assert cmd[0] == "Rscript"
    assert "/helpers/stability_diagnostics.R" in cmd
    assert "--rds" in cmd and "/x/a.rds" in cmd
    assert "42,1,2" in cmd
    assert "0.8" in cmd


def test_parse_and_aggregate_labels(tmp_path):
    # ref と一致する seed、cluster1 が一部割れる seed
    df = pd.DataFrame({
        "CellID": ["c1", "c2", "c3", "c4", "c5", "c6"],
        "ref":     ["0", "0", "0", "1", "1", "1"],
        "seed_42": ["0", "0", "0", "1", "1", "1"],
        "seed_101": ["0", "0", "0", "1", "1", "2"],
    })
    p = tmp_path / "stability_labels.csv"
    df.to_csv(p, index=False)
    labels = sr.parse_labels_csv(str(p))
    summary = sr.aggregate_from_labels(labels)
    assert summary["n_runs"] == 2
    assert summary["n_cells"] == 6
    assert summary["cluster_flags"]["0"] == "stable"
    assert set(summary["cluster_jaccard_mean"].keys()) == {"0", "1"}


def test_run_stability_without_r_returns_error(tmp_path, monkeypatch):
    # Rscript が無い環境ではエラー dict を返す（例外を投げない）
    import subprocess

    def _boom(*a, **k):
        raise FileNotFoundError("Rscript")

    monkeypatch.setattr(subprocess, "run", _boom)
    out = sr.run_stability("x.rds", str(tmp_path), "/helpers")
    assert "error" in out
