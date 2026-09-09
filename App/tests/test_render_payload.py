"""描画ペイロード/レンダリング経路の回帰テスト (ver46.1)。

「画像の切り替え・パンが重い」への対策で入れた変更が、後から静かに巻き戻らない
ようにするためのテスト。狙いは主に以下 4 点。

1. Feature Plot が SVG (`scatter`) ではなく WebGL (`scattergl`) で描かれること。
   SVG は 1 点 = 1 DOM ノードのため、数万 spot で描画・パンが破綻する。
2. H&E オーバーレイが生の RGB 配列 (`go.Image.z`) ではなく圧縮画像
   (`go.Image.source`) を運ぶこと。2000px 画像で 60MB 以上の差が出る。
3. 全点に同じ文字列を並べた `text` 配列が復活しないこと。
4. 一括保存用の figure が dcc.Store 経由でブラウザへ往復しないこと。

dash / plotly / PIL が無い環境ではスキップする。
"""

import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("dash")
pytest.importorskip("plotly")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.graph_objects as go  # noqa: E402
import plotly.io as pio  # noqa: E402

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


# ---------------------------------------------------------------------------
# 合成データ
# ---------------------------------------------------------------------------

def _make_plot_data(n_side=40, samples=("S1", "S2"), n_clusters=5):
    """Spatial/UMAP/Feature の各描画が要求する列を備えた合成 plot_data。"""
    rows = []
    for si, s in enumerate(samples):
        gx, gy = np.meshgrid(np.arange(n_side), np.arange(n_side))
        gx = gx.ravel()
        gy = gy.ravel()
        n = gx.size
        rows.append(pd.DataFrame({
            "Sample": s,
            "CellID": [f"{s}_{i}" for i in range(n)],
            "SpatialX": gx.astype(float),
            "SpatialY": gy.astype(float),
            "UMAP_1": (gx / n_side + si).astype(float),
            "UMAP_2": (gy / n_side).astype(float),
            "Cluster": ((gx + gy) % n_clusters).astype(str),
            "TotalCount": (gx + gy).astype(float),
        }))
    return pd.concat(rows, ignore_index=True)


def _all_traces(fig):
    d = fig.to_dict() if isinstance(fig, go.Figure) else fig
    return d.get("data", []) or []


# ---------------------------------------------------------------------------
# 1. 座標の丸め (表示専用)
# ---------------------------------------------------------------------------

def test_round_for_display_is_scale_relative():
    """量子化はデータ範囲に対する相対量。単位 (px / µm / mm) に依存しない。"""
    from app.callbacks.interactive_spatial import _round_for_display

    for span in (1e-3, 1.0, 300.0, 1e5):
        x = np.linspace(0, span, 500) + 0.123456789
        y = np.linspace(0, span, 500)
        rx, ry = _round_for_display(x, y)
        max_err = float(np.max(np.abs(rx - x)))
        # 誤差は範囲の 1/10000 未満（表示上は視認不能）
        assert max_err < span / 1e4, f"span={span}: err={max_err}"
        # 潰れて同一値になっていない（情報を失っていない）
        assert len(np.unique(rx)) == len(np.unique(x)), f"span={span} で座標が潰れた"


def test_round_for_display_shrinks_json():
    """回転後の float64 座標は丸めで実際に小さくなる。"""
    from app.callbacks.interactive_spatial import _round_for_display

    rng = np.random.default_rng(0)
    x = rng.uniform(0, 300, 20000) * np.pi
    y = rng.uniform(0, 300, 20000) * np.e
    raw = len(pio.to_json(go.Figure(go.Scattergl(x=x, y=y))))
    rx, ry = _round_for_display(x, y)
    rounded = len(pio.to_json(go.Figure(go.Scattergl(x=rx, y=ry))))
    assert rounded < raw * 0.65, f"raw={raw} rounded={rounded}"


def test_round_for_display_handles_degenerate_input():
    from app.callbacks.interactive_spatial import _round_for_display

    for x, y in [(np.array([]), np.array([])),
                 (np.array([1.0]), np.array([2.0])),
                 (np.array([np.nan, np.nan]), np.array([np.nan, np.nan]))]:
        rx, ry = _round_for_display(x, y)
        assert rx.shape == x.shape and ry.shape == y.shape


# ---------------------------------------------------------------------------
# 1b. 強度 (marker.color) の丸め (表示専用、ver51.3)
# ---------------------------------------------------------------------------
# 座標だけ丸めて色を丸めていなかったため、強度が float64 の 17 桁表記のまま
# 流れていた。強度は桁が大きく振れるので固定小数では丸められない。

def test_round_values_is_scale_relative_and_hover_safe():
    """量子化は範囲に対する相対量。hover 表示 (.4f) への影響は最小桁 1 つ以内。

    ★ ここが要点。範囲だけで桁を決めると、強度が大きいデータ (範囲 2e4 なら
      小数 1 桁) で hover が「1234.5000」のように**存在しない桁をゼロで捏造**
      する。丸めを小数 4 桁より粗くしない下限を入れてこれを防いでいる。
      実測では scale >= 1 で hover は完全一致、影響点 0%。

    残る 1 ULP のズレは丸めに本質的なもの (表示境界ちょうどに乗った値が
    二重丸めで最小桁 1 つ動く) で、これを消すには丸めをやめるしかない。
    出るのは値が 1e-4 より十分小さく、.4f がそもそも 1 桁しか表示していない
    領域だけ。
    """
    from app.callbacks.interactive_spatial import _round_values_for_display

    rng = np.random.default_rng(0)
    for scale in (1e-3, 1.0, 1e3, 1e6):
        v = rng.lognormal(0, 1, 2000) * scale
        r = _round_values_for_display(v)
        span = float(v.max() - v.min())
        diff = np.abs(np.round(v, 4) - np.round(r, 4))
        # ① 値の誤差は範囲の 1/10000 未満
        assert float(np.max(np.abs(r - v))) < span / 1e4, f"scale={scale}"
        # ② hover 表示のズレは最小桁 1 つぶんを超えない
        #    (下限が無いと scale=1e3 でここが 0.05 = 500 ULP になる)
        assert float(diff.max()) <= 1e-4 + 1e-12, \
            f"scale={scale}: hover が最小桁 1 つ以上ずれた ({diff.max()})"
        # ③ ずれるのは表示境界に乗った点だけ (1% 未満)
        assert float((diff > 0).mean()) < 0.01, \
            f"scale={scale}: {100 * (diff > 0).mean():.1f}% の点で hover がずれた"
        # ④ 通常の強度スケールでは完全一致
        if scale >= 1.0:
            assert float(diff.max()) == 0.0, \
                f"scale={scale} では hover が完全一致すべき"


def test_round_values_never_shifts_the_displayed_color():
    """色は colorscale の 256 段階に落ちるので、丸めても 1 段も動かない。"""
    from app.callbacks.interactive_spatial import _round_values_for_display

    rng = np.random.default_rng(1)
    for scale in (1e-3, 1.0, 1e6):
        v = rng.lognormal(0, 1, 5000) * scale
        r = _round_values_for_display(v)
        lo, hi = float(v.min()), float(v.max())
        step = np.floor(255 * (v - lo) / (hi - lo)).astype(int)
        step_r = np.floor(255 * (r - lo) / (hi - lo)).astype(int)
        assert int(np.max(np.abs(step - step_r))) <= 1, \
            f"scale={scale} で色段階が 2 段以上動いた"


def test_round_values_shrinks_json():
    """生の float64 強度は丸めで実際に小さくなる。"""
    from app.callbacks.interactive_spatial import _round_values_for_display

    rng = np.random.default_rng(0)
    v = rng.lognormal(0, 1, 20000)
    raw = len(pio.to_json(go.Figure(go.Scattergl(y=v))))
    rounded = len(pio.to_json(go.Figure(go.Scattergl(
        y=_round_values_for_display(v)))))
    assert rounded < raw * 0.7, f"raw={raw} rounded={rounded}"


def test_round_values_keeps_integer_columns_intact():
    """整数列 (TotalCount 等) は float 化しない。

    12345 を 12345.0 にすると JSON では**むしろ長くなる**ので、
    丸めが逆効果になる。
    """
    from app.callbacks.interactive_spatial import _round_values_for_display

    ints = np.arange(1000, dtype=np.int64)
    out = _round_values_for_display(ints)
    assert out.dtype.kind == "i", f"整数列が {out.dtype} に変換された"
    assert np.array_equal(out, ints)


def test_round_values_handles_degenerate_input():
    from app.callbacks.interactive_spatial import _round_values_for_display

    for v in (np.array([]), np.array([1.0]), np.array([np.nan, np.nan]),
              np.array([5.0, 5.0, 5.0]), np.array([np.inf, 1.0])):
        out = _round_values_for_display(v)
        assert out.shape == v.shape


# ---------------------------------------------------------------------------
# 2. クラスタ名の解決
# ---------------------------------------------------------------------------

def test_cluster_names_for_matches_naive_comprehension():
    """高速化しても出力は従来の内包表記と完全一致すること。"""
    from app.callbacks.interactive_spatial import _cluster_names_for
    from app.utils.color_utils import cluster_display_name

    s = pd.Series(["1", "2", "10", "1", "2"], dtype=object)
    name_map = {"2": "Tumor"}
    assert (_cluster_names_for(s, name_map)
            == [cluster_display_name(c, name_map) for c in s])
    assert _cluster_names_for(s, None) == [cluster_display_name(c, None) for c in s]


# ---------------------------------------------------------------------------
# 3. Spatial figure
# ---------------------------------------------------------------------------

def _spatial_fig(**kw):
    from app.callbacks.interactive_spatial import _create_single_spatial_fig
    from app.utils.color_utils import get_cluster_color_map, get_cluster_colorscale

    df = _make_plot_data(n_side=30, samples=("S1",))
    cmap = get_cluster_color_map(df["Cluster"], None)
    c2i, cscale = get_cluster_colorscale(df["Cluster"], None)
    params = dict(embed_legend=True, cluster_to_idx=c2i, discrete_cscale=cscale,
                  marker_size=3)
    params.update(kw)
    return _create_single_spatial_fig(df, cmap, None, set(), **params), df


def test_spatial_draws_cells_not_points():
    """★ ver66.0: Spatial は MSI の画素をデータ座標の矩形として敷き詰めて描く。

    従来 (散布) はマーカーの大きさが **画面ピクセル単位**だったため、拡大率が
    変わるたびに隣接スポットの間に隙間が出たり重なったりし、利用者が毎回
    サイズスライダーで直していた。データ座標のセルにすればその調整が要らない。

    scattergl が残っていてよいのは凡例ダミー (x=[None]) だけ。データを持つ
    散布トレースが復活したら落ちる。SVG トレース (`scatter`) の混入も許さない
    ＝ 1 点 = 1 DOM ノードで数万 spot が破綻する退行の番人 (ver46.1)。
    """
    fig, _ = _spatial_fig()
    traces = _all_traces(fig)
    types = {t.get("type") for t in traces}
    assert "heatmap" in types, f"ラスターで描いていない: {types}"
    assert types <= {"heatmap", "scattergl"}, f"SVG トレースが混ざっている: {types}"
    for t in traces:
        if t.get("type") != "scattergl":
            continue
        assert list(t.get("x") or []) == [None], (
            "凡例ダミー以外の散布トレースが残っている（サイズ依存が復活する）")


def test_spatial_cells_have_no_pixel_size_and_no_gaps():
    """★ セルに画面 px のサイズ概念が無く、隣接セルの間に隙間が空かないこと。

    `marker` が生えたらサイズ依存が戻ったということ。`xgap`/`ygap` が 0 で
    なくなったら、まさに本改修が解決した「隙間」が再発する。
    """
    fig, _ = _spatial_fig()
    heatmaps = [t for t in _all_traces(fig) if t.get("type") == "heatmap"]
    assert heatmaps, "heatmap が 1 つも無い（テストが無意味）"
    for t in heatmaps:
        assert "marker" not in t, f"heatmap に marker がある: {t.get('name')}"
        assert t.get("xgap", 0) == 0 and t.get("ygap", 0) == 0, "セルの間に隙間がある"
        assert t.get("zsmooth") is False, "補間が入るとピクセルの境界がぼける"
        assert t.get("hoverongaps") is False, (
            "空セル (組織の外側) でもホバーが出る。散布では点が無い場所に"
            "ホバーは出なかったので退行になる")


def test_spatial_falls_back_to_scatter_for_irregular_coords():
    """★ 過剰修正の番人: 格子と判定できない座標では従来の散布に落ちること。

    x/y は利用者の CSV/Excel をそのまま通すので、不規則座標もあり得る。
    無理に格子へ押し込むと複数点が同じセルに潰れた「それらしく見える別物」が出る。
    """
    from app.callbacks.interactive_spatial import _create_single_spatial_fig
    from app.utils.color_utils import get_cluster_color_map

    rng = np.random.RandomState(0)
    n = 200
    df = pd.DataFrame({
        "CellID": [f"c{i}" for i in range(n)],
        "Cluster": [str(i % 3) for i in range(n)],
        "Sample": "S1",
        "SpatialX": rng.rand(n) * 50,
        "SpatialY": rng.rand(n) * 50,
        "TotalCount": np.arange(n, dtype=float),
    })
    cmap = get_cluster_color_map(df["Cluster"], None)
    fig = _create_single_spatial_fig(df, cmap, None, set(), embed_legend=True)
    d = fig.to_dict()
    types = {t.get("type") for t in d["data"]}
    assert types == {"scattergl"}, f"格子でないのにラスター化している: {types}"
    assert d["layout"]["meta"]["raster"] is False


def test_spatial_raster_can_be_disabled():
    """★ SPATIAL_RASTER=0 で従来の散布に戻せること（実データでの逃げ道）。"""
    import app.utils.raster as R

    original = R.SCREEN_RASTER_ENABLED
    try:
        R.SCREEN_RASTER_ENABLED = False
        fig, _ = _spatial_fig()
        types = {t.get("type") for t in _all_traces(fig)}
        assert types == {"scattergl"}, f"無効化が効いていない: {types}"
    finally:
        R.SCREEN_RASTER_ENABLED = original


def test_spatial_cluster_cells_match_the_color_map():
    """★ セルごとに「そのクラスタの色」で塗られていること。

    離散カラースケールの段境界が 1 段ずれると、全セルが隣のクラスタの色になる。
    「それらしく見える別物」なので、目視では気づけない。1 セルずつ照合する。
    """
    from app.callbacks.interactive_spatial import _create_single_spatial_fig
    from app.utils.color_utils import get_cluster_color_map

    df = _make_plot_data(n_side=12, samples=("S1",), n_clusters=5)
    cmap = get_cluster_color_map(df["Cluster"], None)
    fig = _create_single_spatial_fig(df, cmap, None, set(), embed_legend=True)
    hm = [t for t in fig.to_dict()["data"]
          if t.get("type") == "heatmap" and t.get("customdata") is not None]
    assert hm, "クラスタ層 (customdata つき heatmap) が無い"
    tr = hm[0]
    z = np.asarray(tr["z"], dtype=float)
    cs, zmin, zmax = tr["colorscale"], tr["zmin"], tr["zmax"]
    cd = np.asarray(tr["customdata"], dtype=object)

    def color_at(v):
        """plotly と同じ規則で z 値 → 実際に描かれる色を逆算する。"""
        t = (v - zmin) / (zmax - zmin)
        prev = cs[0][1]
        for stop, col in cs:
            if t <= stop + 1e-12:
                return col if abs(t - stop) < 1e-12 or col == prev else prev
            prev = col
        return cs[-1][1]

    want = {(float(r.SpatialX), -float(r.SpatialY)): str(r.Cluster)
            for r in df.itertuples()}
    xc = np.asarray(tr["x"], dtype=float)
    yc = np.asarray(tr["y"], dtype=float)
    checked = 0
    for j, yv in enumerate(yc):
        for i, xv in enumerate(xc):
            if not np.isfinite(z[j, i]):
                continue
            checked += 1
            cluster = want[(xv, yv)]
            assert color_at(z[j, i]).lower() == cmap[cluster].lower(), (
                f"({xv},{yv}) の色がクラスタ {cluster} と食い違う")
            assert cd[j, i] == cluster, f"({xv},{yv}) のホバー名が違う"
    assert checked == len(df), f"検査できたセルが足りない: {checked}/{len(df)}"


def test_spatial_hover_name_is_not_expanded_as_a_template():
    """★ クラスタ表示名に "%{x}" が含まれてもテンプレート展開されないこと。

    表示名は利用者が付けるもので、Plotly のテンプレート記法を含みうる
    (ver46.2/46.3 の教訓)。ラスターでも値として customdata で渡すこと。
    """
    from app.callbacks.interactive_spatial import _create_single_spatial_fig
    from app.utils.color_utils import get_cluster_color_map

    df = _make_plot_data(n_side=8, samples=("S1",), n_clusters=2)
    cmap = get_cluster_color_map(df["Cluster"], None)
    fig = _create_single_spatial_fig(
        df, cmap, None, set(), embed_legend=True,
        cluster_name_map={"0": "%{x} 腫瘍", "1": "正常"})
    hm = [t for t in fig.to_dict()["data"]
          if t.get("type") == "heatmap" and t.get("customdata") is not None]
    assert hm
    tmpl = hm[0].get("hovertemplate") or ""
    assert "%{customdata}" in tmpl, "ホバーが customdata 経由になっていない"
    assert "腫瘍" not in tmpl, "表示名をテンプレートに直接埋めている"
    flat = [v for row in np.asarray(hm[0]["customdata"], dtype=object)
            for v in row if v]
    assert "%{x} 腫瘍" in flat, "表示名が値として渡っていない"


def test_spatial_hover_text_is_scalar_not_per_point_array():
    """全点に同じ文字列を並べた text 配列 (5 万点で ~0.7MB の無駄) を作らない。"""
    fig, df = _spatial_fig()
    n_points = len(df)
    for t in _all_traces(fig):
        for key in ("text", "hovertext"):
            val = t.get(key)
            if isinstance(val, (list, tuple, np.ndarray)) and len(val) > 1:
                # 配列で持ってよいのは「点ごとに中身が違う」場合だけ
                assert len(set(map(str, val))) > 1, (
                    f"全要素が同一の {key} 配列が復活している（スカラーにできるはず）")
                assert len(val) == n_points


def test_spatial_scalar_hover_uses_hovertext_not_template():
    """スカラーのホバー文字列は hovertext + hoverinfo で持つこと (ver46.2)。

    `text=<スカラー>` + `hovertemplate="%{text}"` は plotly.py の直列化は通るが、
    plotly.js が scattergl のスカラー text から %{text} を解決できず、
    ツールチップに "%{text}" がそのまま出る（実際に ver46.1 で発生した回帰）。
    ブラウザ側の検証は tests/e2e/test_render_perf.py にある。
    """
    fig, _ = _spatial_fig()
    for t in _all_traces(fig):
        tmpl = t.get("hovertemplate") or ""
        if "%{text}" not in tmpl:
            continue
        txt = t.get("text")
        assert isinstance(txt, (list, tuple, np.ndarray)), (
            "hovertemplate の %{text} は配列 text でしか解決されない。"
            "スカラーで済ませたい場合は hovertext + hoverinfo='text' を使うこと")


def test_spatial_uirevision_ignores_cosmetic_changes():
    """マーカーサイズ・ラベルの変更で uirevision が変わらない = ズームが保たれる。"""
    from app.utils.display_helpers import transform_uirevision

    rev = transform_uirevision("S1", {"angle": 0, "flip_h": False, "flip_v": False})
    a, _ = _spatial_fig(marker_size=3, label_size=10, uirevision=rev)
    b, _ = _spatial_fig(marker_size=9, label_size=22, show_labels=True, uirevision=rev)
    assert a.layout.uirevision == b.layout.uirevision is not None


def test_spatial_uirevision_resets_on_geometry_change():
    """サンプル・回転・反転が変われば uirevision も変わる = 正しくリセットされる。"""
    from app.utils.display_helpers import transform_uirevision

    base = {"angle": 0, "flip_h": False, "flip_v": False}
    ref = transform_uirevision("S1", base)
    assert transform_uirevision("S2", base) != ref
    assert transform_uirevision("S1", {**base, "angle": 90}) != ref
    assert transform_uirevision("S1", {**base, "flip_h": True}) != ref
    assert transform_uirevision("S1", {**base, "flip_v": True}) != ref
    assert transform_uirevision("S1", base, extra="hne") != ref
    # 旧形式 (int) も受け付ける
    assert transform_uirevision("S1", 0) == ref


def test_spatial_fig_defaults_to_no_uirevision():
    """uirevision を渡さない呼び出し元 (PPTX/共有ビュー等) の挙動は変えない。"""
    fig, _ = _spatial_fig()
    assert fig.layout.uirevision is None


# ---------------------------------------------------------------------------
# 4. H&E オーバーレイ
# ---------------------------------------------------------------------------

def _png_data_uri(w=800, h=600):
    import base64
    import io
    Image = pytest.importorskip("PIL.Image")
    rng = np.random.default_rng(1)
    arr = rng.integers(120, 250, (h, w, 3)).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode(), arr


def test_hne_display_image_is_compressed_not_raw_array():
    """go.Image は圧縮画像 (source) を運ぶ。生配列 (z) は桁違いに大きい。"""
    from app.callbacks.interactive_hne_bg import _build_display_image

    uri, arr = _png_data_uri()
    out = _build_display_image(uri, mono=False)
    assert out is not None
    disp_uri, scale, w, h = out
    assert disp_uri.startswith("data:image/")

    raw_bytes = len(pio.to_json(go.Figure(go.Image(z=arr))))
    new_bytes = len(pio.to_json(go.Figure(go.Image(source=disp_uri))))
    assert new_bytes < raw_bytes / 10, f"raw={raw_bytes} new={new_bytes}"


def test_hne_display_image_downscales_and_reports_scale():
    """長辺が上限を超える画像は縮小され、倍率が返る（スポット座標の換算に使う）。"""
    from app.callbacks import interactive_hne_bg as H

    uri, _ = _png_data_uri(w=H.HNE_DISPLAY_MAX_DIM * 2, h=H.HNE_DISPLAY_MAX_DIM)
    disp_uri, scale, w, h = H._build_display_image(uri, mono=False)
    assert max(w, h) == H.HNE_DISPLAY_MAX_DIM
    assert scale == pytest.approx(0.5, rel=1e-3)
    # 縮小後の画像サイズと倍率が整合していること（座標がずれない条件）
    assert w == pytest.approx(H.HNE_DISPLAY_MAX_DIM * 2 * scale, abs=1)


def test_hne_display_image_not_upscaled_when_small():
    """上限以下の画像は拡大しない（scale=1.0 でスポット座標も素通し）。"""
    from app.callbacks import interactive_hne_bg as H

    uri, _ = _png_data_uri(w=200, h=150)
    disp_uri, scale, w, h = H._build_display_image(uri, mono=False)
    assert (w, h) == (200, 150)
    assert scale == 1.0


def test_hne_mono_is_greyscale():
    """モノクロ指定で R=G=B になる（旧実装の輝度変換と同じ見た目）。"""
    import base64
    import io
    Image = pytest.importorskip("PIL.Image")
    from app.callbacks import interactive_hne_bg as H

    uri, _ = _png_data_uri(w=64, h=64)
    disp_uri, _, _, _ = H._build_display_image(uri, mono=True)
    raw = base64.b64decode(disp_uri.split(",", 1)[1])
    arr = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB")).astype(int)
    # JPEG のクロマサブサンプリング分の誤差を許容しつつ、無彩色であることを確認
    assert np.abs(arr[..., 0] - arr[..., 1]).max() <= 4
    assert np.abs(arr[..., 1] - arr[..., 2]).max() <= 4


def test_hne_image_cache_is_bounded():
    """キャッシュは件数上限つき（旧 _HNE_ARR_CACHE は無制限だった）。"""
    from app.callbacks import interactive_hne_bg as H

    H._HNE_IMG_CACHE.clear()
    for i in range(H._HNE_CACHE_MAX_ENTRIES + 5):
        H._HNE_IMG_CACHE[(f"p{i}", "img.png", False)] = ("uri", 1.0, 1, 1)
        while len(H._HNE_IMG_CACHE) > H._HNE_CACHE_MAX_ENTRIES:
            H._HNE_IMG_CACHE.popitem(last=False)
    assert len(H._HNE_IMG_CACHE) <= H._HNE_CACHE_MAX_ENTRIES
    H._HNE_IMG_CACHE.clear()


# ---------------------------------------------------------------------------
# 5. エクスポート用 figure のサーバ側保持
# ---------------------------------------------------------------------------

def test_export_figures_roundtrip_and_session_isolation():
    from app.callbacks.interactive_callbacks import (
        get_export_figures, set_export_figures)

    figs_a = [("Spatial_S1", {"data": [], "layout": {}})]
    figs_b = [("Spatial_S1", {"data": [{"x": [1]}], "layout": {}})]
    set_export_figures("spatial", "sessA", "/rds/x.rds", figs_a)
    set_export_figures("spatial", "sessB", "/rds/x.rds", figs_b)

    assert get_export_figures("spatial", "sessA", "/rds/x.rds") == figs_a
    assert get_export_figures("spatial", "sessB", "/rds/x.rds") == figs_b
    # 種類・プロジェクトでも分離される
    assert get_export_figures("umap", "sessA", "/rds/x.rds") == []
    assert get_export_figures("spatial", "sessA", "/rds/other.rds") == []
    # 未知のセッションは空（例外ではなく空リスト）
    assert get_export_figures("spatial", None, "/rds/x.rds") == []


def test_export_figures_entry_count_is_bounded():
    from app.callbacks import interactive_callbacks as IC

    IC._export_figures.clear()
    IC._export_figures_time.clear()
    for i in range(IC._MAX_EXPORT_FIG_ENTRIES + 10):
        IC.set_export_figures("spatial", f"sess{i}", "/rds/x.rds", [("f", {})])
    assert len(IC._export_figures) <= IC._MAX_EXPORT_FIG_ENTRIES
    # 直近のものは残っている（LRU）
    last = IC._MAX_EXPORT_FIG_ENTRIES + 9
    assert IC.get_export_figures("spatial", f"sess{last}", "/rds/x.rds") == [("f", {})]
    IC._export_figures.clear()
    IC._export_figures_time.clear()


def test_batch_figure_stores_are_gone_from_layout():
    """図の実体を往復させていた Store が復活していないこと。"""
    import app.layouts.interactive_tab as tab

    src = Path(tab.__file__).read_text(encoding="utf-8")
    for name in ("batch_umap_figures_store", "batch_spatial_figures_store",
                 "batch_feature_figures_store"):
        assert f'dcc.Store(id="{name}"' not in src, f"{name} が復活している"


# ---------------------------------------------------------------------------
# 6. アコーディオン開閉による無駄な再描画の抑制
# ---------------------------------------------------------------------------

def test_accordion_guard_skips_unrelated_toggle():
    """他セクションの開閉では再描画しないが、判断がつかない場合は必ず描画する。"""
    from app.callbacks import interactive_callbacks as IC

    IC._accordion_seen.clear()
    kw = dict(section="acc_spatial", session_id="s1", rds_path="/rds/a.rds")

    # 初回は記録が無い → 必ず描画する（安全側）
    assert IC.accordion_toggle_is_noop(
        active_items=["acc_umap", "acc_spatial"],
        triggered_id="interactive_accordion", **kw) is False

    # 別セクション (Feature) を開いただけ → acc_spatial の状態は不変 → 抑制
    assert IC.accordion_toggle_is_noop(
        active_items=["acc_umap", "acc_spatial", "acc_feature"],
        triggered_id="interactive_accordion", **kw) is True

    # acc_spatial 自体が閉じた → 状態が変わったので抑制しない
    assert IC.accordion_toggle_is_noop(
        active_items=["acc_umap"],
        triggered_id="interactive_accordion", **kw) is False

    # 再度開いた → 状態が変わったので抑制しない
    assert IC.accordion_toggle_is_noop(
        active_items=["acc_umap", "acc_spatial"],
        triggered_id="interactive_accordion", **kw) is False

    # accordion 以外がトリガー (マーカーサイズ等) → 常に描画する
    assert IC.accordion_toggle_is_noop(
        active_items=["acc_umap", "acc_spatial"],
        triggered_id="spatial_marker_size", **kw) is False
    IC._accordion_seen.clear()


def test_accordion_guard_is_isolated_per_section_and_session():
    from app.callbacks import interactive_callbacks as IC

    IC._accordion_seen.clear()
    common = dict(active_items=["acc_spatial"], triggered_id="interactive_accordion")
    # 別セクション / 別セッション / 別プロジェクトは互いに影響しない
    assert IC.accordion_toggle_is_noop("acc_spatial", "s1", "/a", **common) is False
    assert IC.accordion_toggle_is_noop("acc_umap_facet", "s1", "/a", **common) is False
    assert IC.accordion_toggle_is_noop("acc_spatial", "s2", "/a", **common) is False
    assert IC.accordion_toggle_is_noop("acc_spatial", "s1", "/b", **common) is False
    # 2 回目は同一キーなので抑制される
    assert IC.accordion_toggle_is_noop("acc_spatial", "s1", "/a", **common) is True
    IC._accordion_seen.clear()


def test_accordion_guard_memory_is_bounded():
    from app.callbacks import interactive_callbacks as IC

    IC._accordion_seen.clear()
    for i in range(IC._MAX_ACCORDION_SEEN + 50):
        IC.accordion_toggle_is_noop("acc_spatial", f"s{i}", "/a",
                                    ["acc_spatial"], "interactive_accordion")
    assert len(IC._accordion_seen) <= IC._MAX_ACCORDION_SEEN
    IC._accordion_seen.clear()


# ---------------------------------------------------------------------------
# 7. parquet スキーマのキャッシュ
# ---------------------------------------------------------------------------

def test_parquet_schema_cache_avoids_refooter_parse(tmp_path):
    """列名判定は 1 回だけフッタを読み、以降はキャッシュから返すこと。"""
    pytest.importorskip("pyarrow")
    from app.services.seurat_bridge import SeuratBridge
    import app.services.seurat_bridge as SB

    path = tmp_path / "expression_matrix.parquet"
    pd.DataFrame({"mz_100": [1.0, 2.0], "mz_200": [3.0, 4.0]}).to_parquet(path)

    SB._PARQUET_SCHEMA_CACHE.clear()
    names = SeuratBridge._parquet_column_names(path)
    assert names == {"mz_100", "mz_200"}
    assert len(SB._PARQUET_SCHEMA_CACHE) == 1

    calls = {"n": 0}
    real = SB.pd.read_parquet

    import pyarrow.parquet as pq
    orig_pf = pq.ParquetFile

    def counting_pf(*a, **k):
        calls["n"] += 1
        return orig_pf(*a, **k)

    pq.ParquetFile = counting_pf
    try:
        for _ in range(5):
            assert SeuratBridge._parquet_column_names(path) == names
        assert calls["n"] == 0, "キャッシュが効かずフッタを再パースしている"
    finally:
        pq.ParquetFile = orig_pf
    SB._PARQUET_SCHEMA_CACHE.clear()


def test_parquet_schema_cache_invalidates_on_file_change(tmp_path):
    """ファイルが差し替わったらキャッシュが無効化されること。"""
    pytest.importorskip("pyarrow")
    import time as _time
    from app.services.seurat_bridge import SeuratBridge
    import app.services.seurat_bridge as SB

    path = tmp_path / "expression_matrix.parquet"
    SB._PARQUET_SCHEMA_CACHE.clear()
    pd.DataFrame({"mz_100": [1.0]}).to_parquet(path)
    assert SeuratBridge._parquet_column_names(path) == {"mz_100"}

    _time.sleep(0.01)
    pd.DataFrame({"mz_999": [1.0], "mz_888": [2.0]}).to_parquet(path)
    assert SeuratBridge._parquet_column_names(path) == {"mz_999", "mz_888"}
    SB._PARQUET_SCHEMA_CACHE.clear()


def test_missing_feature_column_returns_none_and_logs(tmp_path, caplog):
    """列名不一致は R フォールバック(30〜300秒)へ黙って落ちず、警告を残すこと。"""
    pytest.importorskip("pyarrow")
    import logging

    from app.services.seurat_bridge import SeuratBridge

    path = tmp_path / "expression_matrix.parquet"
    pd.DataFrame({"mz_100": [1.0, 2.0]}).to_parquet(path)

    bridge = SeuratBridge()
    with caplog.at_level(logging.WARNING, logger="msi.seurat_bridge"):
        assert bridge.get_feature_expression_fast(tmp_path, "mz_NOT_THERE") is None
    assert any("mz_NOT_THERE" in r.getMessage() for r in caplog.records), \
        "列名不一致が無言で握りつぶされている"
    # 実在する列は従来どおり取得できる
    got = bridge.get_feature_expression_fast(tmp_path, "mz_100")
    assert got is not None and list(got) == [1.0, 2.0]


# ---------------------------------------------------------------------------
# 8. 実コールバックの実行 (出力数の整合 + 生成された figure の中身)
# ---------------------------------------------------------------------------
# Dash は呼び出し時に outputs_list と戻り値の個数を照合するため、
# ここを通ることが「Output を減らした改修で戻り値の数がズレていない」証明になる。

@pytest.fixture
def dash_app(monkeypatch):
    """認証用の env を立ててから app を import する。"""
    import secrets

    monkeypatch.setenv("FLASK_SECRET_KEY", secrets.token_hex(32))
    monkeypatch.setenv("MASTER_PASSWORD", "test-master")
    monkeypatch.setenv("INITIAL_PASSWORD_A", "test-a")
    monkeypatch.setenv("INITIAL_PASSWORD_B", "test-b")
    try:
        from app.main import app
    except Exception as exc:  # pragma: no cover - 環境依存
        pytest.skip(f"app を import できない: {exc}")
    return app


def _call_callback(dash_app, output_marker, args, triggered_prop):
    """登録済みコールバックを Dash の dispatch 経路で呼び、応答 dict を返す。"""
    import json

    import dash._callback as dc
    from dash._utils import AttributeDict

    keys = [k for k in dc.GLOBAL_CALLBACK_MAP if output_marker in k]
    # ★ ver66.1: 同じ Output を複数のコールバックが持つようになった
    #   (`interactive_resets.clear_stale_figures` がデータセット切替で図を空にする)。
    #   出力 id だけでは絞れないので、**発火元の Input を持つ方**を選ぶ。
    #   Dash の dispatch も出力と入力の組で決まるので、これが実挙動に一致する。
    trigger_id = triggered_prop.split(".")[0]
    if len(keys) > 1:
        keys = [k for k in keys
                if any(i.get("id") == trigger_id
                       for i in dc.GLOBAL_CALLBACK_MAP[k]["inputs"])]
    assert len(keys) == 1, (
        f"{output_marker} / {triggered_prop} に一致するコールバックが {len(keys)} 件")
    spec = dc.GLOBAL_CALLBACK_MAP[keys[0]]
    # Output が 1 個のコールバックは spec["output"] が Output 単体で、
    # Dash も outputs_list を list ではなく dict 単体として受け取る。
    out = spec["output"]
    if isinstance(out, (list, tuple)):
        outputs_list = [{"id": o.component_id, "property": o.component_property}
                        for o in out]
    else:
        outputs_list = {"id": out.component_id, "property": out.component_property}
    assert len(args) == len(spec["inputs"]) + len(spec["state"]), (
        f"引数 {len(args)} 個 != Input {len(spec['inputs'])} + State {len(spec['state'])}")
    ctx = AttributeDict({
        "updated_props": {},
        "triggered_inputs": [{"prop_id": triggered_prop, "value": args[0]}],
        "inputs_list": [], "states_list": [], "outputs_list": outputs_list,
        "args_grouping": [],
    })
    raw = spec["callback"](*args, outputs_list=outputs_list,
                           callback_context=ctx, app=dash_app)
    return json.loads(raw)["response"]


def _install_synthetic_state(monkeypatch, rds_path="/rds/test.rds"):
    import app.callbacks.interactive_callbacks as IC

    # サンプル別 UMAP は 2 サンプル以上でないと描画しないので 2 つ用意する
    df = _make_plot_data(n_side=25, samples=("S1", "S2"))
    IC._set_active_key(rds_path)
    IC._interactive_data["plot_data"] = df
    IC._interactive_data["method"] = "testmethod"
    IC._interactive_data["rds_path"] = rds_path
    monkeypatch.setattr(IC._bridge, "ensure_expression_matrix",
                        lambda p: None, raising=False)
    monkeypatch.setattr(
        IC._bridge, "get_feature_expression_fast",
        lambda cache_dir, feat: pd.Series(np.linspace(0.0, 1.0, len(df))),
        raising=False)
    return df, rds_path


def _graph_figures(node, id_type=None):
    """応答の component ツリーから dcc.Graph の figure を集める。

    id_type を指定するとパターンマッチ id (例 {"type": "spatial_graph"}) の
    グラフだけに絞る。共有凡例 (点数 = クラスタ数の小さなダミー図) を
    データタイルと混同しないために使う。
    """
    figs = []
    if isinstance(node, dict):
        props = node.get("props", {})
        if node.get("type") == "Graph" and "figure" in props:
            gid = props.get("id")
            if id_type is None or (isinstance(gid, dict)
                                   and gid.get("type") == id_type):
                figs.append(props["figure"])
        for v in props.values():
            figs.extend(_graph_figures(v, id_type))
    elif isinstance(node, (list, tuple)):
        for v in node:
            figs.extend(_graph_figures(v, id_type))
    return figs


def test_feature_plot_renders_webgl_and_stores_figures_serverside(
        dash_app, monkeypatch):
    """Feature Plot の実コールバックを回して、ラスターで描かれることを確認する。

    ★ ver66.0: 散布 (scattergl) からラスター (heatmap) へ移した。SVG (scatter) に
      戻ると 1 点 = 1 DOM ノードで数万 spot が破綻するので、そちらの番人も兼ねる。
    """
    from app.callbacks.interactive_callbacks import get_export_figures

    df, rds_path = _install_synthetic_state(monkeypatch)
    session_id = "sess-feature"

    resp = _call_callback(
        dash_app, "feature_plot_container",
        # ver51.3: 配色は clientside restyle へ移したので State にある。
        # ★ ver66.0: marker_size は撤去したので State が 1 つ減った。
        # Inputs(8): feature, sample, imin, imax, name_map, fs_trigger,
        #            rows, show_compound
        # States(6): colorscale, rds_path, cache_dir, rotation_store,
        #            deg_data, session_id
        args=["mz_100", "S1", None, None, {}, 0, 0, False,
              "Plasma", [], rds_path, "/tmp/cache", {}, None, session_id],
        triggered_prop="feature_select.value")

    figs = _graph_figures(resp["feature_plot_container"]["children"],
                          id_type="feature_graph")
    assert figs, "Feature Plot の figure が生成されていない"
    for fig in figs:
        types = {t.get("type") for t in fig.get("data", [])}
        assert types == {"heatmap"}, f"ラスターで描いていない: {types}"
        assert fig["layout"].get("uirevision"), "uirevision が設定されていない"

    # 一括保存用の figure はレスポンスではなくサーバ側に置かれる
    stored = get_export_figures("feature", session_id, rds_path)
    assert stored and stored[0][0].startswith("Feature_")


def test_feature_geometry_is_stable_across_intensity_range(
        dash_app, monkeypatch):
    """★ ver51.5: 幾何は強度レンジで変わらない（可視性だけが変わる）。

    従来は閾値未満の点を visible_mask で **トレースから除外** していたため、
    点の集合が m/z・強度レンジごとに変わり、x/y/CellID を毎回送り直していた
    (1 タイル 1.12MB gzip のうち 0.44MB がこの再送)。

    ★ ver66.0: ラスター化に伴い、可視性の表現が marker.opacity == 0 から
      z == NaN（透明）に変わった。格子の形と CellID は強度レンジによらず一定、
      という不変条件はそのまま維持する。

      あわせて「閾値未満」の注記 (customdata) は不要になった。散布では
      opacity=0 の点にも hover が発生してしまうので注記が要ったが、
      ラスターは hoverongaps=False で空セルが hover 対象から外れるため。
    """
    df, rds_path = _install_synthetic_state(monkeypatch)
    n_tile = int((df["Sample"] == "S1").sum())

    def _fg_trace(imin):
        resp = _call_callback(
            dash_app, "feature_plot_container",
            args=["mz_100", "S1", imin, None, {}, 0, 0, False,
                  "Plasma", [], rds_path, "/tmp/cache", {}, None, "sess-mask"],
            triggered_prop="feature_intensity_min.value")
        figs = _graph_figures(resp["feature_plot_container"]["children"],
                              id_type="feature_graph")
        # trace[0] は TIC 背景(全点)、trace[-1] が発現量オーバーレイ
        return figs[0]["data"][-1]

    t0 = _fg_trace(0)
    t50 = _fg_trace(50)
    t100 = _fg_trace(100)

    def _z(t):
        return np.asarray(t["z"], dtype=float)

    # ① セル数（格子の形）は強度レンジによらず一定
    for label, t in (("0%", t0), ("50%", t50), ("100%", t100)):
        assert _z(t).size == _z(t0).size, f"{label}: 格子の大きさが変わっている"
        assert int(np.isfinite(_z(t)).sum()) + int(np.isnan(_z(t)).sum()) \
            == _z(t).size

    # ② 座標と CellID は 1 バイトも変わらない (差分更新できる前提)
    assert list(t50["x"]) == list(t0["x"])
    assert list(t50["y"]) == list(t0["y"])
    assert [list(r) for r in t50["text"]] == [list(r) for r in t0["text"]]

    # ③ 可視性は z=NaN で表現される。下限を上げれば隠れるセルが増える
    assert int(np.isnan(_z(t50)).sum()) > int(np.isnan(_z(t0)).sum()), \
        "下限を上げても隠れるセルが増えていない"

    # ④ 値が入っているセル数は「閾値以上の点数」と一致する
    assert int(np.isfinite(_z(t0)).sum()) <= n_tile

    # ⑤ 下限 100% = 全セルが閾値未満 → 全部 NaN（カラーバーは残る）
    assert bool(np.all(np.isnan(_z(t100)))), "下限 100% で残っているセルがある"


def test_feature_hovertemplate_uses_customdata_for_note():
    """注記は hovertemplate に直接埋めず customdata 経由であること。

    ver46.3 と同じ理由: テンプレート記法を含む文字列を直接埋めると展開される。
    """
    src = (APP_ROOT / "app" / "callbacks" / "interactive_deg.py").read_text(
        encoding="utf-8")
    assert "%{customdata}" in src
    assert "_BELOW_THRESHOLD_NOTE" in src


def test_spatial_and_umap_callbacks_return_expected_output_counts(
        dash_app, monkeypatch):
    """Output を減らした改修で戻り値の数がズレていないこと。

    Dash は outputs_list と戻り値の個数を照合するので、例外なく通れば整合している。
    """
    df, rds_path = _install_synthetic_state(monkeypatch)

    spatial = _call_callback(
        dash_app, "spatial_plots_container",
        # Inputs(18): sample, highlight, selected, rotation, show_labels,
        #   exclude, rds_path, name_map, fs_trigger, colors, rows,
        #   cluster_names, merge_toggle, merge_mode, accordion, legend_hidden,
        #   hne_show, hne_mono
        # ★ ver66.0: サイズ系 State 2 つ (marker_size / hne_marker_size) を撤去。
        # States(4): label_positions, session_id, label_size, hne_opacity
        args=["S1", None, [], {}, False, None, rds_path, {}, 0, {}, 0,
              {}, "separate", "shade", ["acc_spatial"], [], False, False,
              {}, "sess-spatial", 10, 100],
        triggered_prop="interactive_sample.value")
    assert set(spatial) == {"spatial_plots_container", "last_spatial_figure_store"}

    # データタイルのみ (共有凡例のダミー図は対象外)
    figs = _graph_figures(spatial["spatial_plots_container"]["children"],
                          id_type="spatial_graph")
    assert figs, "Spatial の figure が生成されていない"
    for f in figs:
        types = {t.get("type") for t in f.get("data", [])}
        # ★ ver66.0: ラスター (heatmap) + 凡例ダミー (scattergl)。SVG は許さない。
        assert "heatmap" in types, f"ラスターで描いていない: {types}"
        assert types <= {"heatmap", "scattergl"}, f"SVG が混ざっている: {types}"
        assert f["layout"].get("uirevision"), "uirevision が設定されていない"

    umap = _call_callback(
        dash_app, "umap_per_sample_container",
        # Inputs(16) + States(3)
        args=["per_sample", None, False, 2, None, 11, rds_path, True, {}, 0,
              {}, 0, {}, ["acc_umap"], "Sample", [],
              {}, {"groups": []}, "sess-umap"],
        triggered_prop="umap_display_mode.value")
    assert set(umap) == {"umap_per_sample_container"}
    umap_figs = _graph_figures(umap["umap_per_sample_container"]["children"],
                               id_type="umap_per_sample_graph")
    assert umap_figs, "サンプル別 UMAP の figure が生成されていない"
    for f in umap_figs:
        assert f["layout"].get("uirevision"), "uirevision が設定されていない"


# ---------------------------------------------------------------------------
# 9. 見た目パラメータの後付け適用が「最初からその値で作った図」と一致すること
# ---------------------------------------------------------------------------
# マーカーサイズ等は clientside の Plotly.restyle で画面だけを更新し、
# 一括保存の直前にサーバ側 figure へ同じ変換を掛けている。
# ここが食い違うと「画面と保存した PNG が違う」という最悪の壊れ方をするため、
# 後付け適用の結果が新規ビルドと一致することを固定する。

def _spot_opacities(fig_dict):
    """スポット不透明度スライダーの対象トレースの不透明度を集める。

    ★ ver66.0: 書き先がトレース種で変わる。ラスター化した通常タイルは heatmap の
      **トレース直下** opacity、H&E タイルは散布のままで marker.opacity。
      Python 側 (apply_display_overrides) と JS 側 (spatial_restyle.js) が
      同じ規則であることを、この関数を通して検証する。
    """
    out = []
    for t in fig_dict.get("data", []):
        if not (isinstance(t.get("meta"), dict) and t["meta"].get("op")):
            continue
        out.append(t.get("opacity") if t.get("type") == "heatmap"
                   else t.get("marker", {}).get("opacity"))
    return out


def _label_sizes(fig_dict):
    return [a.get("font", {}).get("size")
            for a in fig_dict.get("layout", {}).get("annotations", [])]


def test_display_overrides_match_fresh_build_opacity_and_label():
    """後付け適用 == 最初からその値でビルド（不透明度・ラベルサイズ）。"""
    from app.utils.display_helpers import apply_display_overrides

    fresh, _ = _spatial_fig(spot_opacity=0.4, label_size=18, show_labels=True)
    built, _ = _spatial_fig(spot_opacity=1.0, label_size=10, show_labels=True)
    patched = apply_display_overrides(built.to_dict(),
                                      spot_opacity=0.4, label_size=18)

    fresh_d = fresh.to_dict()
    assert _spot_opacities(patched) == _spot_opacities(fresh_d)
    assert _spot_opacities(patched), "不透明度の対象トレースが 1 つも無い"
    assert _label_sizes(patched) == _label_sizes(fresh_d)
    assert _label_sizes(patched), "ラベル注記が 1 つも無い"


def test_display_overrides_respects_tile_kind():
    """kinds に合わない図は一切変更しない（通常用スライダーが H&E に効かない）。

    ★ ver66.0: 以前は空リスト同士の比較で素通りしていた（`[] == []` で PASS）。
      比較対象が非空であることを先に確かめる。
    """
    from app.utils.display_helpers import apply_display_overrides

    fig, _ = _spatial_fig(spot_opacity=1.0)
    d = fig.to_dict()
    before = _spot_opacities(d)
    assert before, "比較対象が空（テストが無意味）"
    apply_display_overrides(d, spot_opacity=0.2, kinds=("hne",))
    assert _spot_opacities(d) == before


def test_display_overrides_ignores_untagged_traces():
    """meta を持たないトレース（凡例ダミー等）は触らない。"""
    from app.utils.display_helpers import apply_display_overrides

    fig, _ = _spatial_fig(spot_opacity=1.0)
    d = fig.to_dict()

    def _untagged():
        return [(t.get("opacity"), t.get("marker", {}).get("opacity"))
                for t in d["data"] if not isinstance(t.get("meta"), dict)]

    untagged_before = _untagged()
    apply_display_overrides(d, spot_opacity=0.1)
    assert untagged_before == _untagged()
    assert untagged_before, "凡例ダミートレースが存在しない"


def test_cosmetic_sliders_are_not_inputs_of_spatial_callback(dash_app):
    """見た目スライダーが Input に戻っていないこと（戻ると全図再構築が復活する）。"""
    import dash._callback as dc

    key = [k for k in dc.GLOBAL_CALLBACK_MAP if "spatial_plots_container" in k][0]
    spec = dc.GLOBAL_CALLBACK_MAP[key]
    input_ids = {i["id"] for i in spec["inputs"]}
    state_ids = {s["id"] for s in spec["state"]}
    # ★ ver66.0: サイズ系スライダーは撤去したので、残る 2 つを見る。
    for cid in ("spatial_label_size", "hne_overlay_opacity"):
        assert cid not in input_ids, f"{cid} が Input に戻っている"
        assert cid in state_ids, f"{cid} が State から消えている"


def test_perf_callbacks_are_registered_clientside(dash_app):
    """パン/ズームのフィルタと見た目 restyle が **ブラウザ側** で動くこと。

    ここがサーバ側コールバックとして登録されてしまうと、無音のまま
    「ホイールを回すたびに POST」「スライダーのたびに全図再構築」に逆戻りする。
    """
    import dash._callback as dc

    expected = {
        "annotation_relayout_signal": ("relayout", "filter_annotations"),
        "fs_annotation_relayout_signal": ("relayout", "filter_annotations"),
    }
    found = {}
    restyle_fns = set()
    for cb in dc.GLOBAL_CALLBACK_LIST:
        out = str(cb.get("output"))
        fn = cb.get("clientside_function")
        for name in expected:
            if out.startswith(name):
                found[name] = (fn or {}).get("namespace"), (fn or {}).get("function_name")
        if out.startswith("spatial_restyle_dummy"):
            assert fn, "見た目 restyle がサーバ側コールバックになっている"
            restyle_fns.add(fn["function_name"])

    assert found == expected, f"relayout フィルタが clientside でない: {found}"
    # ★ ver66.0: サイズ系 (marker_size / hne_marker_size) を撤去した。
    #   完全一致なので、復活したらここで落ちる。
    assert restyle_fns == {"label_size", "spot_opacity"}, restyle_fns


def test_no_server_callback_takes_relayoutdata_directly(dash_app):
    """relayoutData をサーバ側 Input に直結したコールバックが復活しないこと。"""
    import dash._callback as dc

    offenders = []
    clientside_outputs = {
        str(cb.get("output")) for cb in dc.GLOBAL_CALLBACK_LIST
        if cb.get("clientside_function")
    }
    for key, spec in dc.GLOBAL_CALLBACK_MAP.items():
        if not any(i.get("property") == "relayoutData" for i in spec["inputs"]):
            continue
        if not any(o.startswith(key.split("@")[0]) for o in clientside_outputs):
            offenders.append(key)
    assert not offenders, f"サーバ側で relayoutData を受けている: {offenders}"


# ---------------------------------------------------------------------------
# 10. heartbeat の扇形抑制 (ver46.3)
# ---------------------------------------------------------------------------

def test_edit_lock_heartbeat_is_noop_when_unchanged(monkeypatch):
    """ロック状態が変わらない限り Store を更新しない。

    更新すると edit_lock_state を Input にする 6 コールバック（うち 4 つは
    MATCH でサンプル別/クラスタ別に展開）へ 10 秒ごとに扇形配信され、
    描画やパンと同じサーバに数十件が積まれる。
    """
    from dash import no_update

    import app.callbacks.edit_lock_callbacks as EL

    locks = {"cluster_rename:0": {"user_id": "u1", "user_display": "alice"}}
    monkeypatch.setattr(EL.elm, "cleanup_expired", lambda: None)
    monkeypatch.setattr(EL.elm, "get_locks_for_project", lambda p: dict(locks))

    # 現在値と同じ -> 更新しない
    assert EL.refresh_edit_lock_state(1, "/rds/a.rds", "s1", dict(locks)) is no_update
    # 現在値が未設定（初回）-> 更新する
    assert EL.refresh_edit_lock_state(1, "/rds/a.rds", "s1", None) == locks
    # 内容が変わった -> 更新する
    assert EL.refresh_edit_lock_state(1, "/rds/a.rds", "s1", {}) == locks
    # プロジェクト未選択で現在値も空 -> 更新しない
    assert EL.refresh_edit_lock_state(1, None, "s1", {}) is no_update


def test_edit_lock_heartbeat_still_evicts_stale_state(monkeypatch):
    """抑制しても、heartbeat が担っている stale eviction は必ず走ること。"""
    import app.callbacks.edit_lock_callbacks as EL
    import app.callbacks.interactive_callbacks as IC

    calls = {"cleanup": 0, "evict": 0}
    monkeypatch.setattr(EL.elm, "cleanup_expired",
                        lambda: calls.__setitem__("cleanup", calls["cleanup"] + 1))
    monkeypatch.setattr(EL.elm, "get_locks_for_project", lambda p: {})
    monkeypatch.setattr(IC, "evict_stale_project_states",
                        lambda: calls.__setitem__("evict", calls["evict"] + 1))

    EL.refresh_edit_lock_state(1, "/rds/a.rds", "s1", {})   # 抑制されるケース
    assert calls == {"cleanup": 1, "evict": 1}


# ---------------------------------------------------------------------------
# 11. WSGI サーバ設定 (ver46.3)
# ---------------------------------------------------------------------------

def test_wsgi_server_defaults_to_waitress_single_worker():
    """本番は waitress。ワーカーは 1 固定（プロセス内メモリ前提を壊さないため）。"""
    src = (APP_ROOT / "run_app.py").read_text(encoding="utf-8")
    assert 'MSI_WSGI_SERVER", "waitress"' in src
    # ワーカー数を増やす設定を足していないこと（増やすと plot_data 等が分断される）
    assert "workers=" not in src.replace("workers=1", "")
    # 切り戻し手段が残っていること
    assert "Werkzeug development server" in src


def test_wsgi_dependency_is_declared():
    for path in ("requirements.txt", "pyproject.toml"):
        assert "waitress" in (APP_ROOT / path).read_text(encoding="utf-8"), path


# ---------------------------------------------------------------------------
# 12. hovertemplate へのテキスト直接埋め込みの禁止 (ver46.3)
# ---------------------------------------------------------------------------

def test_no_hovertemplate_embeds_dynamic_text():
    """動的な文字列を hovertemplate に f-string で埋め込まないこと。

    クラスタ表示名や化合物名はユーザー（またはユーザー提供のアノテーション
    ファイル）由来で、"%{x}" のような Plotly のテンプレート記法を含み得る。
    直接埋め込むとホバー時に展開されてしまうため、meta 経由で値として渡す。
    ブラウザでの挙動は tests/e2e/test_render_perf.py で検証している。
    """
    import re

    offenders = []
    for path in sorted((APP_ROOT / "app" / "callbacks").glob("*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = re.search(r'hovertemplate=f"([^"]*)"', line)
            if not m:
                continue
            # f-string 内の {...} のうち、Plotly の %{...} ではないもの＝Python の補間
            body = m.group(1)
            interpolations = [x for x in re.findall(r'(?<!%)\{([^}]*)\}', body)]
            # 固定の安全な識別子（"Cluster"/"Sample" しか入らない color_col）だけ許可
            risky = [x for x in interpolations if x not in ("color_col",)]
            if risky:
                offenders.append(f"{path.name}:{i}: {risky}")
    assert not offenders, (
        "hovertemplate に動的文字列を埋め込んでいる箇所がある "
        "(meta 経由にすること):\n" + "\n".join(offenders))


# ---------------------------------------------------------------------------
# 11. Feature Plot の見た目パラメータ後付け適用 (ver51.3)
# ---------------------------------------------------------------------------
# マーカーサイズと配色を clientside restyle へ移したので、サーバが一括保存用に
# 保持している figure は操作前の値のままになる。保存直前に同じ変換を掛けて
# 「画面と保存 PNG が一致する」ことを担保するのがここのテスト。
# 食い違うと、画面では Viridis なのに保存 PNG は Plasma、という最悪の壊れ方をする。

def _feature_figs(dash_app, monkeypatch, colorscale):
    """実コールバックを回して Feature タイルの figure dict 群を返す。"""
    _df, rds_path = _install_synthetic_state(monkeypatch)
    resp = _call_callback(
        dash_app, "feature_plot_container",
        args=["mz_100", "S1", None, None, {}, 0, 0, False,
              colorscale, [], rds_path, "/tmp/cache", {}, None,
              "sess-ovr"],
        triggered_prop="feature_select.value")
    return _graph_figures(resp["feature_plot_container"]["children"],
                          id_type="feature_graph")


def _feature_colorscales(fig_dict):
    """配色を **正規化して** 取り出す。

    plotly は figure を組み立てる時点で "Viridis" のような名前を色停止点の配列へ
    展開する。一方 apply_feature_display_overrides / Plotly.restyle は名前の
    文字列を入れる（plotly も plotly.js も名前を受け付ける）。素の dict のまま
    比べると「名前 vs 展開済み配列」で必ず食い違うので、両者を go.Figure に
    通してから比較する。名前が fresh と同じ配列に解決されることまで確認できる。
    """
    fig = go.Figure(fig_dict)
    d = fig.to_dict()
    meta = (d.get("layout") or {}).get("meta") or {}
    data = d.get("data") or []
    # ★ ver66.0: 書き先がトレース種で変わる（heatmap はトレース直下）。
    out = []
    for i in meta.get("cs") or []:
        tr = data[i]
        out.append(tr.get("colorscale") if tr.get("type") == "heatmap"
                   else tr.get("marker", {}).get("colorscale"))
    return out


def test_feature_figure_carries_restyle_meta(dash_app, monkeypatch):
    """clientside restyle が必要とする layout.meta が載っていること。

    ★ ver66.0: サイズ系 (auto_msz / sz) は撤去した。配色 (cs) だけが残る。
    """
    figs = _feature_figs(dash_app, monkeypatch, "Plasma")
    assert figs, "Feature タイルが生成されていない"
    for f in figs:
        meta = (f.get("layout") or {}).get("meta") or {}
        assert meta.get("kind") == "feature", f"kind が違う: {meta.get('kind')}"
        assert "auto_msz" not in meta, "撤去したはずの auto_msz が残っている"
        assert "sz" not in meta, "撤去したはずの sz が残っている"
        assert meta.get("cs"), "配色対象トレースの索引が無い"
        n = len(f.get("data") or [])
        assert all(0 <= i < n for i in meta["cs"]), "索引が範囲外"
        # ★ 背景 TIC は常に Greys。配色プルダウンの対象に入れてはいけない。
        for i in meta["cs"]:
            assert f["data"][i].get("colorscale") != "Greys", \
                "TIC 背景が配色対象に入っている"


def test_feature_display_overrides_match_fresh_build_colorscale(dash_app,
                                                                monkeypatch):
    """後付け適用 == 最初からその値でビルド（配色）。"""
    import copy

    from app.utils.display_helpers import apply_feature_display_overrides

    fresh = _feature_figs(dash_app, monkeypatch, "Viridis")
    built = _feature_figs(dash_app, monkeypatch, "Plasma")

    for f_fresh, f_built in zip(fresh, built):
        assert _feature_colorscales(f_built) != _feature_colorscales(f_fresh), \
            "配色を変えても figure が変わっていない（テストが無意味）"
        patched = apply_feature_display_overrides(
            copy.deepcopy(f_built), colorscale="Viridis")
        assert _feature_colorscales(patched) == _feature_colorscales(f_fresh)


def test_feature_overrides_ignore_other_tile_kinds(dash_app, monkeypatch):
    """kind が feature でない図は一切変更しない。

    ★ ver66.0: 以前は Spatial の図を渡していたので、比較対象 (`meta.cs` 由来) が
      空リスト同士になり **何も検証せずに PASS** していた。実際の Feature 図の
      kind だけを書き換えて渡し、比較対象が非空であることを先に確かめる。
    """
    import copy

    from app.utils.display_helpers import apply_feature_display_overrides

    figs = _feature_figs(dash_app, monkeypatch, "Plasma")
    assert figs, "Feature タイルが生成されていない"
    d = copy.deepcopy(figs[0])
    before = _feature_colorscales(d)
    assert before and before[0] is not None, "比較対象が空（テストが無意味）"

    d["layout"]["meta"] = dict(d["layout"]["meta"], kind="msi")
    apply_feature_display_overrides(d, colorscale="Viridis")
    assert _feature_colorscales(d) == before


def test_feature_restyle_js_matches_python_contract():
    """JS 側が Python と同じ layout.meta のキーを見ていること。

    両者は同じ規則の二重実装なので、片方でキー名を変えたら気付ける必要がある。
    ブラウザ上の実挙動は e2e に譲り、ここは契約（キー名）だけを固定する。
    """
    js = (APP_ROOT / "app" / "assets" / "feature_restyle.js").read_text(
        encoding="utf-8")
    for token in ('meta.kind !== "feature"', "meta.cs",
                  '"colorscale"', "marker.colorscale"):
        assert token in js, f"feature_restyle.js に {token!r} が無い"
    # ★ ver66.0: サイズ系は撤去した。復活したらここで落ちる。
    for gone in ("meta.auto_msz", "meta.sz", "marker.size"):
        assert gone not in js, f"撤去したはずの {gone!r} が feature_restyle.js に残っている"
    # Python 側の実装が同じキーを使っていること
    src = (APP_ROOT / "app" / "utils" / "display_helpers.py").read_text(
        encoding="utf-8")
    assert "apply_feature_display_overrides" in src
    for token in ('"feature"', '"cs"', '"colorscale"'):
        assert token in src, f"display_helpers.py に {token} が無い"


# ---------------------------------------------------------------------------
# 12. UMAP / Spatial / violin の座標・色の丸め (ver51.4)
# ---------------------------------------------------------------------------
# ver46.1 が Spatial の座標、ver51.3 が Feature の色を丸めたが、
#   - UMAP は `_round_for_display` を import すらしていなかった
#   - Spatial の TIC 色は丸めていなかった
#   - violin の発現値は丸めていなかった
# の 3 つが残っていた。

def test_rounded_umap_does_not_mutate_input():
    """★ 元の df を書き換えないこと。

    `interactive_loupe.umap_polygon_commit` は _interactive_data["plot_data"] の
    **生の** 座標で点内外判定をする。丸めがそこへ漏れると、選択が表示座標基準に
    静かに変わってしまう。
    """
    from app.callbacks.interactive_umap import _rounded_umap

    df = _make_plot_data(n_side=20, samples=("S1",))
    df["UMAP_1"] = df["UMAP_1"] * np.pi
    before = df["UMAP_1"].to_numpy().copy()

    out = _rounded_umap(df)

    assert np.array_equal(df["UMAP_1"].to_numpy(), before), "入力の df が書き換えられた"
    assert out is not df
    assert not np.array_equal(out["UMAP_1"].to_numpy(), before), "丸めが効いていない"
    # 他の列は共有 (浅いコピー) で、値は一致する
    assert list(out["CellID"]) == list(df["CellID"])


def test_rounded_umap_selection_is_unchanged():
    """★ 丸めても投げ縄/ポリゴン選択の結果が 1 点も変わらないこと。

    量子化幅は範囲の 1/100000。手でクリックする精度 (範囲の 1/500 程度) より
    約 200 倍細かいので、表示座標と判定座標の食い違いが選択を変えることは無い
    —— という主張を実際に確かめる。
    """
    from app.callbacks.interactive_umap import _rounded_umap
    from app.services.hne_overlay import points_in_polygon

    rng = np.random.default_rng(0)
    df = _make_plot_data(n_side=40, samples=("S1",))
    n = len(df)
    df["UMAP_1"] = rng.normal(0, 3, n) * np.pi
    df["UMAP_2"] = rng.normal(0, 3, n) * np.e

    out = _rounded_umap(df)
    xs_raw = df["UMAP_1"].to_numpy(float)
    ys_raw = df["UMAP_2"].to_numpy(float)
    xs_r = out["UMAP_1"].to_numpy(float)
    ys_r = out["UMAP_2"].to_numpy(float)

    # 中央付近を横切る多角形をいくつか試す
    polys = [
        [(-2.0, -2.0), (2.0, -2.0), (2.0, 2.0), (-2.0, 2.0)],
        [(-5.0, 0.0), (0.0, -5.0), (5.0, 0.0), (0.0, 5.0)],
        [(0.13, 0.27), (4.7, -1.1), (3.3, 4.9)],
    ]
    for poly in polys:
        a = points_in_polygon(xs_raw, ys_raw, poly)
        b = points_in_polygon(xs_r, ys_r, poly)
        assert int((a != b).sum()) == 0, \
            f"丸めで選択が {int((a != b).sum())} 点変わった (poly={poly})"


def test_rounded_umap_shrinks_json():
    """UMAP 座標の丸めで実際に転送量が減ること。"""
    from app.callbacks.interactive_umap import _rounded_umap

    rng = np.random.default_rng(0)
    df = _make_plot_data(n_side=60, samples=("S1",))
    df["UMAP_1"] = rng.normal(0, 3, len(df)) * np.pi
    df["UMAP_2"] = rng.normal(0, 3, len(df)) * np.e

    raw = len(pio.to_json(go.Figure(go.Scattergl(
        x=df["UMAP_1"], y=df["UMAP_2"]))))
    out = _rounded_umap(df)
    rounded = len(pio.to_json(go.Figure(go.Scattergl(
        x=out["UMAP_1"], y=out["UMAP_2"]))))
    assert rounded < raw * 0.7, f"raw={raw} rounded={rounded}"


def test_rounded_umap_handles_missing_columns():
    """UMAP 列が無い / 空の df でも落ちないこと。"""
    from app.callbacks.interactive_umap import _rounded_umap

    assert _rounded_umap(None) is None
    empty = pd.DataFrame({"UMAP_1": [], "UMAP_2": []})
    assert len(_rounded_umap(empty)) == 0
    no_cols = pd.DataFrame({"CellID": ["a", "b"]})
    assert list(_rounded_umap(no_cols)["CellID"]) == ["a", "b"]


def test_spatial_tic_color_is_rounded():
    """Spatial の TIC 背景の色 (marker.color) が丸められていること。

    3 経路とも hoverinfo="skip" なので、Feature 側で 4 桁下限を入れる原因に
    なった hover 表示の問題はここでは起きない。

    ★ 合成データの TotalCount は整数値なので、そのままでは丸めが no-op になり
      テストが素通りする。無理数倍して桁を持たせてから確かめる。
    """
    from app.callbacks.interactive_spatial import (
        _create_single_spatial_fig, _round_values_for_display)
    from app.utils.color_utils import get_cluster_color_map, get_cluster_colorscale

    df = _make_plot_data(n_side=30, samples=("S1",))
    df["TotalCount"] = df["TotalCount"].to_numpy(dtype=float) * np.pi + 0.123456789
    raw = df["TotalCount"].to_numpy(dtype=float)

    cmap = get_cluster_color_map(df["Cluster"], None)
    c2i, cscale = get_cluster_colorscale(df["Cluster"], None)
    fig = _create_single_spatial_fig(
        df, cmap, None, set(), embed_legend=True, cluster_to_idx=c2i,
        discrete_cscale=cscale, marker_size=3)

    tic = [t for t in fig.to_dict().get("data", [])
           if t.get("name") == "_background_tic"]
    assert tic, "TIC 背景トレースが見つからない"
    # ★ ver66.0: 背景も heatmap になったので、色は marker.color ではなく z。
    #   空セルは NaN なので、値が入っているセルだけを取り出して比べる。
    z = np.asarray(tic[0]["z"], dtype=float)
    arr = z[np.isfinite(z)]
    arr = np.sort(arr)
    raw = np.sort(raw)

    # 生値がそのまま入っていない (= 丸めを通っている)
    assert not np.array_equal(arr, raw), "z が生の float64 のまま"
    # 丸め関数の出力と一致する
    assert np.array_equal(arr, np.sort(_round_values_for_display(raw)))
    # 値としては同じもの (範囲の 1/10000 未満のずれ)
    span = float(raw.max() - raw.min())
    assert float(np.max(np.abs(arr - raw))) < span / 1e4
    # JSON が実際に縮む
    shrunk = len(pio.to_json(go.Figure(go.Scattergl(y=arr))))
    full = len(pio.to_json(go.Figure(go.Scattergl(y=raw))))
    assert shrunk < full * 0.8, f"full={full} shrunk={shrunk}"


def test_violin_values_are_rounded_once_for_all_clusters():
    """violin の発現値は **クラスタへ分ける前に 1 回だけ** 丸めること。

    分けた後に各サブセットで丸めると、クラスタごとに量子化幅が変わる。
    ★ クラスタ間で強度の桁が違うときに顕在化する (実データではふつうに起きる)。
      値の幅が狭いクラスタだけ細かく丸められ、分布の見え方が揃わなくなる。
    """
    from app.callbacks.interactive_spatial import _round_values_for_display

    rng = np.random.default_rng(3)
    # 桁の違う 2 群: 狭い群は単独で丸めると細かい桁が残る
    narrow = rng.uniform(1.0, 1.1, 2000) + 1e-7 * rng.random(2000)
    wide = rng.uniform(1e6, 1e7, 2000)
    vals = np.concatenate([narrow, wide])
    groups = np.concatenate([np.zeros(2000, int), np.ones(2000, int)])

    whole = _round_values_for_display(vals)
    per_group = np.empty_like(vals)
    for g in (0, 1):
        m = groups == g
        per_group[m] = _round_values_for_display(vals[m])

    assert not np.array_equal(whole, per_group), \
        "この合成データでは差が出ないのでテストとして無意味"

    # 実装が「分ける前に 1 回」であること (呼び出し位置を固定する)
    src = (APP_ROOT / "app" / "callbacks" / "interactive_loupe.py").read_text(
        encoding="utf-8")
    assert "_round_values_for_display(np.asarray(expr, dtype=float))" in src, \
        "violin の丸めが「分ける前に 1 回」になっていない"
    # 丸めた後にクラスタへ分けているので、dfp へ入るのは全体基準の値
    assert src.index("_round_values_for_display(np.asarray") < src.index('dfp["_expr"] = arr'), \
        "丸めがクラスタ分割より後に来ている"


# ---------------------------------------------------------------------------
# 13. 図に直結した入力の debounce / 死んだ計算 (ver51.4)
# ---------------------------------------------------------------------------
# ver46.1 の E2E が「見た目スライダーが Input に戻っていないこと」を見張っている
# のと同じ趣旨。ここは静的に見張る。

def _layout_sources():
    out = {}
    for rel in ("app/layouts/interactive_tab.py",
                "app/callbacks/lite_view_callbacks.py"):
        out[rel] = (APP_ROOT / rel).read_text(encoding="utf-8")
    return out


def test_figure_bound_numeric_inputs_have_debounce():
    """図のコールバックに直結した入力は debounce 付きであること。

    無いと 1 打鍵ごとにフル再構築が走る。とくに
      volcano_highlight_mz … 打鍵ごとに全行 Python 正規表現 + apply(axis=1)
      heatmap_top_n        … 打鍵ごとに parquet 読み + merge + groupby
      lv_*_panel_size      … 打鍵ごとに全サンプルのタイル再構築
    """
    import re

    srcs = _layout_sources()
    required = [
        "feature_intensity_min", "feature_intensity_max",   # ver51.3
        "volcano_fc_threshold", "volcano_p_threshold", "volcano_y_max",
        "volcano_label_top_n", "volcano_highlight_mz",
        "heatmap_top_n", "onthefly_de_fc", "onthefly_de_p",
    ]
    joined = "\n".join(srcs.values())
    missing = []
    for cid in required:
        # id=... から次の閉じ括弧までの間に debounce=True があるか
        m = re.search(r'id="%s"(.{0,400}?)\)' % re.escape(cid), joined, re.S)
        if not m or "debounce=True" not in m.group(1):
            missing.append(cid)
    assert not missing, f"debounce が付いていない入力: {missing}"


def test_lite_view_size_inputs_have_debounce():
    """lite viewer のラベル/パネル高もパネル全体を作り直すので debounce 必須。"""
    src = (APP_ROOT / "app" / "callbacks" / "lite_view_callbacks.py").read_text(
        encoding="utf-8")
    # _size_toolbar が作る 2 つの Input (id は引数で渡されるので構造で見る)
    block = src[src.index("def _size_toolbar"):]
    block = block[:block.index("\ndef ")] if "\ndef " in block[10:] else block
    assert block.count("debounce=True") >= 2, \
        "lite viewer の 2 つのサイズ入力に debounce が付いていない"


def test_no_dead_cluster_colorscale_computation():
    """embed_legend=True の経路で get_cluster_colorscale を呼ばないこと。

    _create_single_spatial_fig の cluster_to_idx / discrete_cscale 分岐は
    embed_legend=False のときしか通らないので、embed_legend=True の呼び出し元で
    計算しても捨てるだけ (実測 15.9ms / 20 万行を毎回)。
    """
    for rel in ("app/callbacks/interactive_spatial.py",
                "app/callbacks/interactive_fullscreen.py",
                "app/callbacks/share_callbacks.py"):
        src = (APP_ROOT / rel).read_text(encoding="utf-8")
        assert "_get_cluster_colorscale(" not in src, \
            f"{rel} に死んだ colorscale 計算が復活している"


# ---------------------------------------------------------------------------
# 13. サイズスライダーの撤去 (ver66.0)
# ---------------------------------------------------------------------------
# MSI の画素をデータ座標の矩形として敷き詰めて描くようにしたので、
# 「画面 px でのマーカーサイズ」という概念そのものが無くなった。
# 復活させると、また拡大率ごとに隙間・重なりが出る状態に戻る。

def test_size_sliders_are_gone(dash_app):
    """★ サイズ系スライダーがレイアウトから消えていること。"""
    import dash._callback as dc

    ids = set()
    for spec in dc.GLOBAL_CALLBACK_MAP.values():
        for dep in list(spec["inputs"]) + list(spec["state"]):
            if isinstance(dep.get("id"), str):
                ids.add(dep["id"])
    for gone in ("spatial_marker_size", "feature_marker_size",
                 "hne_overlay_marker_size", "fs_spatial_marker_size"):
        assert gone not in ids, f"{gone} が復活している"
    # 撤去しないもの（Volcano は散布のままなので残る）
    assert "volcano_marker_size" in ids, "無関係な Volcano まで消している"


def test_display_override_helpers_have_no_marker_size():
    """★ 後付け適用のヘルパーからも marker_size が消えていること。"""
    import inspect

    from app.utils.display_helpers import (apply_display_overrides,
                                           apply_feature_display_overrides)

    for fn in (apply_display_overrides, apply_feature_display_overrides):
        assert "marker_size" not in inspect.signature(fn).parameters, \
            f"{fn.__name__} に marker_size が残っている"


def test_spatial_restyle_js_has_no_size_functions():
    """★ clientside 側からもサイズ系が消えていること（Python 側と対）。"""
    js = (APP_ROOT / "app" / "assets" / "spatial_restyle.js").read_text(
        encoding="utf-8")
    # コメントは対象外（撤去の経緯を書いてあるので語そのものは残る）
    code = "\n".join(re.sub(r"//.*$", "", ln) for ln in js.splitlines())
    for gone in ("marker_size", "hne_marker_size", "marker.size", "auto_msz"):
        assert gone not in code, \
            f"撤去したはずの {gone!r} が spatial_restyle.js に残っている"
    # 残すもの
    for token in ("label_size", "spot_opacity", "marker.opacity"):
        assert token in code, f"spatial_restyle.js から {token!r} が消えている"
