"""Violin の等質量ダウンサンプル (ver51.6)。

m/z を 1 つ変えるたびに violin も全 spot 値を群ごとに送っていた
(10 万点 / 30 群で gzip 後 0.286MB)。`points=False` なので個々の点は描かれず、
使われるのは KDE 曲線・箱ひげ・平均線だけ。これらは値の順序に依存しないので、
分布さえ保てば点数は落とせる。

★ ここで守りたいのは「軽くなったこと」ではなく **「見た目と統計が変わらないこと」**。
  軽くするだけなら間引けばよいが、それをやると分布が別物になる。だから本テストの
  大半は削減率ではなく忠実度を固定している。

いちばん怖い壊れ方は次の 3 つで、すべてテストしている:
  1. 帯域幅を渡し忘れる → KDE が n^-0.2 で約 1.7 倍に広がり分布がなまる
  2. 分位点で送る       → 裾の重い分布 (MSI の発現量) で平均が 1 割ずれる
  3. span を渡し忘れる  → violin の縦の広がりが縮む
"""

import json

import numpy as np
import pytest

pytest.importorskip("plotly")

from app.utils.display_helpers import (  # noqa: E402
    VIOLIN_MAX_POINTS,
    violin_equal_mass_sample,
)

M = VIOLIN_MAX_POINTS


def _stats(v):
    """plotly が violin から描くもの (平均線・箱ひげ) と同じ量を出す。"""
    v = np.asarray(v, dtype=float)
    q1, med, q3 = np.percentile(v, [25, 50, 75])
    iqr = q3 - q1
    inside = v[(v >= q1 - 1.5 * iqr) & (v <= q3 + 1.5 * iqr)]
    return {
        "mean": v.mean(), "med": med, "q1": q1, "q3": q3,
        "wlo": inside.min() if inside.size else q1,
        "whi": inside.max() if inside.size else q3,
    }


def _distributions():
    """実データで起こる形を並べる。MSI の発現量は「大量のゼロ + 重い右裾」。"""
    rng = np.random.default_rng(0)
    return {
        "MSI風_60%ゼロ": np.concatenate(
            [np.zeros(60000), rng.lognormal(3, 1.6, 40000)]),
        "95%ゼロ": np.concatenate(
            [np.zeros(95000), rng.lognormal(2, 1, 5000)]),
        "正規": rng.normal(50, 12, 100000),
        "二峰性": np.concatenate(
            [rng.normal(10, 2, 50000), rng.normal(80, 5, 50000)]),
        "一様": rng.uniform(0, 100, 100000),
        "パレート_重い裾": rng.pareto(1.5, 100000) * 100,
    }


# ---------------------------------------------------------------------------
# 忠実度
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", list(_distributions()))
def test_statistics_survive_the_downsample(name):
    """★ 平均・中央値・四分位・ひげが、軸の高さに対して十分小さい誤差に収まる。

    「%」ではなく **軸の高さ比**で見るのが正しい。violin は縦軸に描かれるので、
    目に見えるずれは軸の高さに対する割合であって値そのものの割合ではない
    (真値がほぼ 0 の下ひげは、相対誤差では 1 万%でも 1 画素も動かない)。
    """
    v = _distributions()[name]
    ys, _bw, _span = violin_equal_mass_sample(v)
    axis = float(v.max() - v.min())
    truth, got = _stats(v), _stats(ys)
    for k in truth:
        err = abs(got[k] - truth[k]) / axis
        assert err < 0.025, f"{name}: {k} が軸の {err:.1%} ずれた"


def test_mean_is_preserved_even_for_heavy_tails():
    """★ 等質量「ビン平均」でなければ落ちるテスト。

    分位点で送ると、裾の重い分布では最上位ビンの代表値が実際の質量中心より
    ずっと小さくなり、平均線が目に見えてずれる (実測 7〜11%)。
    ビン平均なら全ビンが同質量なので標本平均は定義上一致する。
    """
    rng = np.random.default_rng(1)
    for v in (rng.pareto(1.5, 100000) * 100,
              np.concatenate([np.zeros(60000), rng.lognormal(3, 1.6, 40000)])):
        ys, _bw, _span = violin_equal_mass_sample(v)
        assert abs(ys.mean() - v.mean()) / abs(v.mean()) < 1e-3

        # 参考: 分位点方式なら同じ許容値を通らないことを示す (方式選択の根拠)
        naive = np.quantile(v, (np.arange(M) + 0.5) / M)
        assert abs(naive.mean() - v.mean()) / abs(v.mean()) > 1e-2


def test_bandwidth_matches_what_plotly_would_have_chosen():
    """★ 帯域幅は **元の点数**で計算されること。

    plotly.js の既定は 1.059*min(sd, IQR/1.349)*n^-0.2 (plotly.min.js より)。
    n を 10 万 → 256 にすると n^-0.2 だけで約 1.7 倍に広がる。渡す値が
    「元の n で計算したもの」でなければ、この差がそのまま KDE のなまりになる。
    """
    v = _distributions()["正規"]
    _ys, bw, _span = violin_equal_mass_sample(v)

    n = v.size
    mean = v.mean()
    sd = np.sqrt(((v - mean) ** 2).sum() / (n - 1))   # 分母 n-1 (plotly と同じ)
    q1, q3 = np.percentile(v, [25, 75])
    expect = max(1.059 * min(sd, (q3 - q1) / 1.349) * n ** -0.2,
                 (v.max() - v.min()) / 100.0)
    assert bw == pytest.approx(expect, rel=1e-12)

    # 落とした後の点数で計算していたら約 1.7 倍になる = はっきり落ちる
    naive = 1.059 * min(sd, (q3 - q1) / 1.349) * M ** -0.2
    assert naive > bw * 1.5


def test_span_covers_the_true_extent():
    """★ 縦の広がりは真の min/max から作ること (plotly 既定 "soft" と同じ式)。"""
    v = _distributions()["パレート_重い裾"]
    ys, bw, span = violin_equal_mass_sample(v)
    assert span == pytest.approx([v.min() - 2 * bw, v.max() + 2 * bw])
    # 送った配列だけから作ると裾が切れる = span を明示する意味
    assert ys.max() < v.max()


def test_true_extremes_are_kept_when_the_mean_allows_it():
    """コンパクトな分布ではひげ＝最大値なので、真の端を入れて一致させる。

    逆に裾が重いときは入れない (入れると平均が壊れるため)。この出し分けが
    ひげの精度と平均の精度を両立させている。
    """
    compact = np.random.default_rng(2).uniform(0, 100, 100000)
    ys, _bw, _s = violin_equal_mass_sample(compact)
    assert ys[0] == compact.min() and ys[-1] == compact.max()

    heavy = np.random.default_rng(2).pareto(1.5, 100000) * 100
    ys_h, _bw, _s = violin_equal_mass_sample(heavy)
    assert ys_h[-1] < heavy.max(), "裾が重いのに最大値を入れている (平均が壊れる)"


# ---------------------------------------------------------------------------
# 契約 / 境界
# ---------------------------------------------------------------------------

def test_small_groups_are_sent_untouched():
    """落として得が無い規模では元配列をそのまま返す (統計は完全一致)。"""
    v = np.random.default_rng(3).normal(size=M * 2)
    ys, bw, span = violin_equal_mass_sample(v)
    assert bw is None and span is None
    assert np.array_equal(ys, v)


def test_constant_group_falls_back_to_plotly_default():
    """全点が同じ値なら plotly も帯域幅 0 になるので、指定せず既定に任せる。"""
    ys, bw, span = violin_equal_mass_sample(np.full(100000, 7.0))
    assert bw is None and span is None
    assert np.all(ys == 7.0)


def test_non_finite_values_are_dropped():
    """NaN/inf は plotly も無視するので、分位計算の前に落とす。"""
    v = np.concatenate([np.random.default_rng(4).normal(size=100000),
                        [np.nan, np.inf, -np.inf]])
    ys, bw, _span = violin_equal_mass_sample(v)
    assert np.isfinite(ys).all() and bw is not None


def test_point_count_is_capped():
    for n in (1000, 100000, 500000):
        ys, _bw, _s = violin_equal_mass_sample(
            np.random.default_rng(5).normal(size=n))
        assert len(ys) == M


@pytest.mark.parametrize("n", [M * 7 + 13, M * 3 + 1, M * 11 - 1, 100000])
def test_mass_is_exact_even_when_n_is_not_divisible(n, monkeypatch):
    """★ 点数がビン数で割り切れなくても平均が厳密に保たれること。

    整数割りでビンを区切ると点数が 1 個違うビンができ、「ビン平均の平均」が
    全体平均からずれる (実測: n=1805 で 3.4% ずれた)。境界を点の途中でも
    切れるものとして扱えば、割り切れなくても総質量が一致する。

    見たいのはビン分割そのものの厳密さなので、真の端の差し込み (別テスト) は
    許容量を 0 にして止めておく。
    """
    import app.utils.display_helpers as DH
    monkeypatch.setattr(DH, "_VIOLIN_MEAN_SHIFT_TOL", 0.0)

    v = np.random.default_rng(6).standard_cauchy(n)
    ys, _bw, _s = violin_equal_mass_sample(v)
    assert ys.mean() == pytest.approx(v.mean(), rel=1e-9)


def test_mean_shift_from_the_extremes_is_bounded():
    """真の端を差し込む場合でも、平均のずれは軸の 0.1% 以内に収まる契約。"""
    for v in _distributions().values():
        ys, _bw, _s = violin_equal_mass_sample(v)
        span = float(v.max() - v.min())
        if span > 0:
            assert abs(ys.mean() - v.mean()) <= 1e-3 * span


def test_payload_actually_shrinks():
    """削減が実際に起きていること (監査の 87% 削減の主張に対応)。"""
    import gzip
    v = np.concatenate([np.zeros(60000),
                        np.random.default_rng(7).lognormal(3, 1.6, 40000)])
    before = len(gzip.compress(json.dumps(np.round(v, 4).tolist()).encode()))
    ys, _bw, _s = violin_equal_mass_sample(v)
    after = len(gzip.compress(json.dumps(np.round(ys, 4).tolist()).encode()))
    assert after < before * 0.05, f"{before} -> {after}"


# ---------------------------------------------------------------------------
# コールバック側の結線
# ---------------------------------------------------------------------------

def test_callback_sets_bandwidth_and_span_together(monkeypatch):
    """★ figure に bandwidth / span / spanmode が揃って乗ること。

    どれか 1 つでも欠けると分布の見え方が変わる。ヘルパーが正しくても
    trace に渡し忘れれば同じことなので、ここで結線を固定する。
    """
    pytest.importorskip("dash")
    import pandas as pd
    import app.callbacks.interactive_callbacks as IC
    import app.callbacks.interactive_loupe as IL

    rng = np.random.default_rng(8)
    n = 3000
    df = pd.DataFrame({
        "Sample": "S1",
        "CellID": [f"c{i}" for i in range(n)],
        "Cluster": (np.arange(n) % 2).astype(str),
        "SpatialX": rng.random(n), "SpatialY": rng.random(n),
    })
    rds = "/rds/violin.rds"
    IC._set_active_key(rds)
    IC._interactive_data["plot_data"] = df
    monkeypatch.setattr(IC._bridge, "get_feature_expression_fast",
                        lambda c, f: pd.Series(rng.lognormal(3, 1.6, n)),
                        raising=False)

    fig = IL.update_feature_violin("mz_100", "Cluster", ["acc_feature"],
                                   rds, "/tmp/cache", {})
    assert len(fig.data) == 2
    for tr in fig.data:
        assert len(tr.y) == M, "ダウンサンプルされていない"
        assert tr.bandwidth is not None and tr.bandwidth > 0
        assert tr.spanmode == "manual" and len(tr.span) == 2
        assert tr.span[0] < tr.span[1]
