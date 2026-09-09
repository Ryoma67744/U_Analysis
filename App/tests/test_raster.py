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


# ---------------------------------------------------------------------------
# 画面ラスター化 (ver66.0) で足したもの
# ---------------------------------------------------------------------------

def test_screen_raster_enabled_reflects_flag(monkeypatch):
    """★ SPATIAL_RASTER で画面だけ散布へ戻せること（PPTX とは別スイッチ）。"""
    monkeypatch.setattr(R, "SCREEN_RASTER_ENABLED", False)
    assert R.screen_raster_enabled() is False
    monkeypatch.setattr(R, "SCREEN_RASTER_ENABLED", True)
    assert R.screen_raster_enabled() is True
    # PPTX 側と独立していること（片方を切ってももう片方は生きる）
    monkeypatch.setattr(R, "RASTER_ENABLED", False)
    assert R.screen_raster_enabled() is True


def test_grid_index_survives_quarter_turns_and_flips():
    """★ 90 度刻みの回転と反転を通しても格子として検出できること。

    画面の回転スライダーは 0/90/180/270 のみ（`interactive_spatial.py`）。
    軸平行のままなので格子は保たれるはずだが、cos(90°) が厳密に 0 に
    ならない浮動小数の都合で間隔推定が壊れうる。回転を入れた実座標で確かめる。
    """
    from app.callbacks.interactive_spatial import _transform_coords

    px, py = _regular_grid(9, 7, step=50.0)
    for angle in (0, 90, 180, 270):
        for flip_h in (False, True):
            tx, ty = _transform_coords(px, -py, angle, flip_h=flip_h)
            gi = R.grid_index(tx, ty)
            assert gi is not None, f"angle={angle} flip_h={flip_h} で格子と判定できない"
            ix, iy, xc, yc = gi
            assert sorted((len(xc), len(yc))) == [7, 9], (angle, len(xc), len(yc))
            # 全点が別々のセルに入る（潰れていない）
            assert len(set(zip(ix.tolist(), iy.tolist()))) == px.size


def test_grid_index_handles_anisotropic_pitch():
    """★ x と y でピクセル間隔が違う格子も正しく扱えること。

    正方マーカー 1 種では原理的に埋められなかったケース。
    """
    xs = np.arange(6) * 1.0
    ys = np.arange(5) * 3.0
    gx, gy = np.meshgrid(xs, ys)
    gi = R.grid_index(gx.ravel(), gy.ravel())
    assert gi is not None
    _ix, _iy, xc, yc = gi
    assert np.allclose(np.diff(xc), 1.0)
    assert np.allclose(np.diff(yc), 3.0)


def test_grid_index_rejects_duplicated_coordinates():
    """★ 別々の点が同じセルに潰れるなら格子とみなさない（散布へ落とす）。

    同じ座標の点が複数あると、ラスターでは後の点が前の点を **上書きする**。
    例外も None も出ないので「それらしく見えるが実際は別物」の図が黙って出る
    （ver52.5 の行順ずれと同じ、いちばん気づけない壊れ方）。散布なら両方描かれる。

    MSI は 1 スポット = 1 座標なので、正しいデータで衝突は起きない。
    """
    px, py = _regular_grid(6, 5)
    # 半数の点を別の点にぴったり重ねる（座標の重複）
    px = np.concatenate([px, px[:15]])
    py = np.concatenate([py, py[:15]])
    assert R.grid_index(px, py) is None, "重複座標をそのまま採用している"
    # ガードを外せば（従来動作）格子として通ってしまうことも確かめる
    assert R.grid_index(px, py, min_unique_ratio=0) is not None


def test_fill_grid_and_labels():
    """レイヤ合成ヘルパー: 値と文字列を同じ添字で撒けること。"""
    ix = np.array([0, 2, 1])
    iy = np.array([1, 0, 1])
    z = R.fill_grid((2, 3), ix, iy, np.array([5.0, 6.0, 7.0]))
    assert z[1, 0] == 5.0 and z[0, 2] == 6.0 and z[1, 1] == 7.0
    assert np.isnan(z[0, 0])
    lab = R.fill_grid_labels((2, 3), ix, iy, ["a", "b", "c"])
    assert lab[1, 0] == "a" and lab[0, 2] == "b" and lab[1, 1] == "c"
    assert lab[0, 0] is None
    assert R.grid_shape((ix, iy, np.arange(3), np.arange(2))) == (2, 3)


def test_heatmap_trace_hover_and_gaps():
    """★ ホバーを出す層では hoverinfo="skip" を上書きすること。

    `hovertemplate` を渡したのに skip が残ると **何も出ない**（skip が優先）。
    また空セルでホバーが出ないこと・セルの間に隙間が空かないことを固定する。
    """
    z = np.array([[0.0, np.nan], [1.0, 0.0]])
    quiet = R.heatmap_trace(z, [0, 1], [0, 1], "Greys", 0.0, 1.0)
    assert quiet.hoverinfo == "skip"
    assert quiet.hoverongaps is False
    assert quiet.xgap == 0 and quiet.ygap == 0

    loud = R.heatmap_trace(z, [0, 1], [0, 1], "Plasma", 0.0, 1.0,
                           customdata=np.array([["a", None], ["b", "c"]],
                                               dtype=object),
                           hovertemplate="%{customdata}<extra></extra>",
                           opacity=0.4, meta={"op": True})
    assert loud.hoverinfo is None, "hovertemplate を渡したのに skip のまま"
    assert loud.hovertemplate == "%{customdata}<extra></extra>"
    assert loud.opacity == 0.4 and loud.meta == {"op": True}
