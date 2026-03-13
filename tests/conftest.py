import pytest
import numpy as np
import pandas as pd


@pytest.fixture
def sample_df():
    """20-row DataFrame with UMAP_1, UMAP_2, Cluster, Sample columns."""
    rng = np.random.RandomState(42)
    n = 20
    clusters = [0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 2, 2]
    samples = ["S1", "S2"] * 10
    return pd.DataFrame({
        "UMAP_1": rng.randn(n),
        "UMAP_2": rng.randn(n),
        "Cluster": clusters,
        "Sample": samples,
    })


@pytest.fixture
def sample_deg_data():
    """Dict mapping cluster IDs to DataFrames with gene, avg_log2FC, p_val_adj columns."""
    return {
        "0": pd.DataFrame({
            "gene": [f"mz_{100+i}.{i:03d}" for i in range(10)],
            "avg_log2FC": [2.5, 1.8, 1.2, 0.9, 0.5, -0.3, -0.8, -1.5, -2.0, -2.5],
            "p_val_adj": [1e-10, 1e-8, 1e-6, 1e-4, 0.01, 0.02, 1e-5, 1e-7, 1e-9, 1e-11],
        }),
        "1": pd.DataFrame({
            "gene": [f"mz_{200+i}.{i:03d}" for i in range(6)],
            "avg_log2FC": [3.0, 1.5, 0.8, -0.5, -1.2, -2.0],
            "p_val_adj": [1e-12, 1e-6, 0.05, 0.01, 1e-4, 1e-8],
        }),
    }


@pytest.fixture
def deg_records():
    """DEG data as a flat list[dict] (the format returned by standardize_deg_df)."""
    records = []
    for cluster_id in ["0", "1"]:
        genes = [f"gene_{cluster_id}_{i}" for i in range(8)]
        fcs = [3.0, 2.0, 1.0, 0.5, -0.5, -1.0, -2.0, -3.0]
        pvals = [1e-10, 1e-8, 1e-5, 0.01, 0.02, 1e-6, 1e-9, 1e-11]
        for g, fc, p in zip(genes, fcs, pvals):
            records.append({
                "gene": g,
                "cluster": cluster_id,
                "avg_log2FC": fc,
                "p_val_adj": f"{p:.2e}",
                "p_val_adj_raw": p,
            })
    return records
