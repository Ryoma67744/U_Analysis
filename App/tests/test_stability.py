"""stability（ARI/Jaccard/silhouette/trustworthiness/集約）の単体テスト。"""
import numpy as np

from app.services import stability as st


def test_ari_identical_is_one():
    a = [0, 0, 1, 1, 2, 2]
    assert np.isclose(st.adjusted_rand_index(a, a), 1.0)


def test_ari_label_invariant():
    a = [0, 0, 1, 1]
    b = [1, 1, 0, 0]
    assert np.isclose(st.adjusted_rand_index(a, b), 1.0)


def test_ari_known_negative_value():
    # 既知値: [0,0,1,1] vs [0,1,0,1] -> ARI = -0.5
    a = [0, 0, 1, 1]
    b = [0, 1, 0, 1]
    assert np.isclose(st.adjusted_rand_index(a, b), -0.5)


def test_cluster_jaccard_and_match():
    ref = np.array([0, 0, 0, 1, 1, 1])
    alt = np.array([0, 0, 0, 1, 1, 2])  # cluster1 が割れた
    m = st.match_clusters_jaccard(ref, alt)
    assert np.isclose(m[0], 1.0)
    # ref=1 (idx3,4,5) vs alt=1 (idx3,4) -> 2/3
    assert np.isclose(m[1], 2.0 / 3.0)


def test_stability_flag_thresholds():
    assert st.stability_flag(0.9) == "stable"
    assert st.stability_flag(0.7) == "borderline"
    assert st.stability_flag(0.5) == "unstable"
    assert st.stability_flag(float("nan")) == "unknown"


def test_trustworthiness_identity_is_one():
    rng = np.random.default_rng(0)
    X = rng.random((60, 5))
    t = st.trustworthiness(X, X, n_neighbors=5)
    assert np.isclose(t, 1.0)


def test_trustworthiness_random_embedding_low():
    rng = np.random.default_rng(0)
    X = rng.random((80, 8))
    Y = rng.random((80, 2))  # 無関係な埋め込み
    t = st.trustworthiness(X, Y, n_neighbors=5)
    assert t < 0.9


def test_silhouette_separated_blobs_high():
    rng = np.random.default_rng(0)
    a = rng.normal(0, 0.1, size=(50, 2))
    b = rng.normal(10, 0.1, size=(50, 2))
    X = np.vstack([a, b])
    labels = np.array([0] * 50 + [1] * 50)
    s = st.silhouette_score(X, labels, seed=0)
    assert s > 0.8


def test_silhouette_single_cluster_nan():
    X = np.random.default_rng(0).random((10, 2))
    assert np.isnan(st.silhouette_score(X, np.zeros(10)))


def test_aggregate_seed_stability():
    ref = np.array([0, 0, 0, 1, 1, 1])
    alts = [
        np.array([0, 0, 0, 1, 1, 1]),   # 完全一致
        np.array([0, 0, 0, 1, 1, 2]),   # cluster1 が一部割れ
    ]
    out = st.aggregate_seed_stability(ref, alts)
    assert out["mean_ari"] <= 1.0
    assert out["cluster_flags"][0] == "stable"        # cluster0 は常に一致
    assert set(out["cluster_jaccard_mean"].keys()) == {0, 1}
    # cluster1 の平均 Jaccard = (1.0 + 2/3)/2 ~ 0.833 -> borderline
    assert out["cluster_flags"][1] in ("borderline", "stable")
