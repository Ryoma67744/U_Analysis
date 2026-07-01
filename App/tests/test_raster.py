"""ラスター描画ヘルパー (app.utils.raster) の単体テスト。

視覚的な正しさ（実際の PPTX 画像）は kaleido/実データを要するため別途実機確認だが、
ビニング・格子割当・UMAP 集約・離散カラースケール・フォールバックの数値ロジックは
ここで担保する。
"""

import numpy as np

import app.utils.raster as R


def _regular_grid(nx, ny, step=1.0):
    xs = np.arange(nx) * step
    ys = np.arange(ny) * step
    gx, gy = np.meshgrid(xs, ys)  # (ny, nx)
    return gx.ravel(), gy.ravel()


# ---------------------------------------------------------------------------
# bin_to_grid（連続値）
# ---------------------------------------------------------------------------

def test_bin_to_grid_shape_centers_values():
    px, py = _regular_grid(5, 4)
    vals = px + 10 * py
    res = R.bin_to_grid(px, py, vals, agg="mean")
    assert res is not None
    z, xc, yc = res
    assert z.shape == (4, 5)                        # (ny, nx)
    assert list(xc) == [0, 1, 2, 3, 4]
    assert list(yc) == [0, 1, 2, 3]
    assert z[0, 0] == 0.0                           # (px0, py0)
    assert z[3, 4] == 34.0                          # (px4, py3): 4 + 30


def test_bin_to_grid_mean_and_empty():
    # 3x3 範囲に L 字型で 5 点
    px = np.array([0.0, 1.0, 2.0, 0.0, 0.0])
    py = np.array([0.0, 0.0, 0.0, 1.0, 2.0])
    vals = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    res = R.bin_to_grid(px, py, vals, agg="mean")
    assert res is not None
    z, xc, yc = res
    assert z.shape == (3, 3)
    assert z[0, 0] == 1.0
    assert z[2, 0] == 5.0                           # (px0, py2)
    assert np.isnan(z[2, 2])                        # 点なし


def test_bin_to_grid_mean_averages_duplicates():
    px = np.array([0.0, 0.0, 1.0, 1.0])
    py = np.array([0.0, 0.0, 1.0, 0.0])
    vals = np.array([10.0, 20.0, 7.0, 3.0])
    z, xc, yc = R.bin_to_grid(px, py, vals, agg="mean")
    assert z[0, 0] == 15.0                          # mean(10,20)


def test_bin_to_grid_irregular_and_too_few():
    rng = np.random.RandomState(0)
    assert R.bin_to_grid(rng.rand(200) * 100, rng.rand(200) * 100,
                         rng.rand(200)) is None
    assert R.bin_to_grid([0.0, 1.0], [0.0, 0.0], [1.0, 2.0]) is None


# ---------------------------------------------------------------------------
# grid_index（合成用）
# ---------------------------------------------------------------------------

def test_grid_index_basic():
    px, py = _regular_grid(3, 3)
    gi = R.grid_index(px, py)
    assert gi is not None
    ix, iy, xc, yc = gi
    assert len(xc) == 3 and len(yc) == 3
    # ravel 順: (0,0),(1,0),(2,0),(0,1),...
    assert ix[0] == 0 and iy[0] == 0
    assert ix[2] == 2 and iy[2] == 0
    assert ix[3] == 0 and iy[3] == 1


def test_grid_index_nan_returns_none():
    px, py = _regular_grid(3, 3)
    px = px.astype(float)
    px[0] = np.nan
    assert R.grid_index(px, py) is None             # NaN 混入 → フォールバック


# ---------------------------------------------------------------------------
# umap_hist_grid（非格子）
# ---------------------------------------------------------------------------

def test_umap_hist_grid_highlight_wins_and_empty():
    # bg(cat0): (0,0),(1,0)  hl(cat1): (0,1),(1,1)  さらに bg を (0,1) にも置く
    x = np.array([0.0, 1.0, 0.0, 1.0, 0.0])
    y = np.array([0.0, 0.0, 1.0, 1.0, 1.0])
    cat = np.array([0.0, 0.0, 1.0, 1.0, 0.0])
    res = R.umap_hist_grid(x, y, cat, (0.0, 1.0), (0.0, 1.0), max_dim=2)
    assert res is not None
    z, xc, yc = res
    assert z.shape == (2, 2)
    assert z[0, 0] == 0.0                            # bg
    assert z[0, 1] == 0.0                            # bg
    assert z[1, 0] == 1.0                            # hl が bg を上書き
    assert z[1, 1] == 1.0                            # hl


def test_umap_hist_grid_bad_range():
    assert R.umap_hist_grid([0, 1], [0, 1], [0, 1], (0, 0), (0, 1)) is None


# ---------------------------------------------------------------------------
# 離散カラースケール & トレース
# ---------------------------------------------------------------------------

def test_build_discrete_colorscale():
    cs, zmin, zmax = R.build_discrete_colorscale(["#aaaaaa", "#ff0000"])
    assert zmin == -0.5 and zmax == 1.5
    assert cs[0] == [0.0, "#aaaaaa"]
    assert cs[-1] == [1.0, "#ff0000"]
    assert R.build_discrete_colorscale([]) == (None, 0.0, 1.0)


def test_heatmap_trace_builds():
    z = np.array([[0.0, np.nan], [1.0, 0.0]])
    tr = R.heatmap_trace(z, [0, 1], [0, 1], "Plasma", 0.0, 1.0,
                         showscale=True, colorbar=dict(title="Intensity"))
    assert tr.type == "heatmap"
    assert tr.zsmooth is False
    assert tr.showscale is True


def test_raster_enabled_reflects_flag(monkeypatch):
    monkeypatch.setattr(R, "RASTER_ENABLED", False)
    assert R.raster_enabled() is False
    monkeypatch.setattr(R, "RASTER_ENABLED", True)
    assert R.raster_enabled() is True
