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


def test_spatial_traces_are_all_webgl():
    fig, _ = _spatial_fig()
    types = {t.get("type") for t in _all_traces(fig)}
    assert types == {"scattergl"}, f"SVG トレースが混ざっている: {types}"


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
    assert len(keys) == 1, f"{output_marker} に一致するコールバックが {len(keys)} 件"
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
    """Feature Plot の実コールバックを回して、SVG に戻っていないことを確認する。"""
    from app.callbacks.interactive_callbacks import get_export_figures

    df, rds_path = _install_synthetic_state(monkeypatch)
    session_id = "sess-feature"

    resp = _call_callback(
        dash_app, "feature_plot_container",
        # Inputs(10): feature, sample, marker_size, imin, imax, name_map,
        #             fs_trigger, rows, show_compound, colorscale
        # States(5): rds_path, cache_dir, rotation_store, deg_data, session_id
        args=["mz_100", "S1", 0, None, None, {}, 0, 0, False, "Plasma",
              rds_path, "/tmp/cache", {}, None, session_id],
        triggered_prop="feature_select.value")

    figs = _graph_figures(resp["feature_plot_container"]["children"],
                          id_type="feature_graph")
    assert figs, "Feature Plot の figure が生成されていない"
    for fig in figs:
        types = {t.get("type") for t in fig.get("data", [])}
        assert types == {"scattergl"}, f"SVG に戻っている: {types}"
        assert fig["layout"].get("uirevision"), "uirevision が設定されていない"

    # 一括保存用の figure はレスポンスではなくサーバ側に置かれる
    stored = get_export_figures("feature", session_id, rds_path)
    assert stored and stored[0][0].startswith("Feature_")


def test_feature_plot_drops_invisible_points_but_keeps_colorbar(
        dash_app, monkeypatch):
    """しきい値未満の点は描かない。ただし全点が対象外なら従来どおり全点残す。"""
    df, rds_path = _install_synthetic_state(monkeypatch)
    # 描画するのは 1 サンプル分のタイルなので、比較対象はそのサンプルの点数
    n_tile = int((df["Sample"] == "S1").sum())

    def _feature_points(imin):
        resp = _call_callback(
            dash_app, "feature_plot_container",
            args=["mz_100", "S1", 0, imin, None, {}, 0, 0, False, "Plasma",
                  rds_path, "/tmp/cache", {}, None, "sess-mask"],
            triggered_prop="feature_intensity_min.value")
        figs = _graph_figures(resp["feature_plot_container"]["children"],
                              id_type="feature_graph")
        # trace[0] は TIC 背景(全点)、trace[-1] が発現量オーバーレイ
        return len(figs[0]["data"][-1]["x"])

    # 下限 0% ならほぼ全点が可視（強度 0 近傍のごく一部だけが落ちる）
    at_zero = _feature_points(0)
    assert at_zero > n_tile * 0.9, f"{at_zero} / {n_tile}"
    assert at_zero <= n_tile
    # 下限 50% なら描画点数が明確に減る
    assert _feature_points(50) < at_zero
    # 下限 100% = 全点がしきい値未満 → カラーバーを残すため全点を描く（従来と同じ図）
    assert _feature_points(100) == n_tile


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
        # States(6): label_positions, session_id, marker_size, label_size,
        #   hne_opacity, hne_marker_size
        args=["S1", None, [], {}, False, None, rds_path, {}, 0, {}, 0,
              {}, "separate", "shade", ["acc_spatial"], [], False, False,
              {}, "sess-spatial", 3, 10, 100, 5],
        triggered_prop="interactive_sample.value")
    assert set(spatial) == {"spatial_plots_container", "last_spatial_figure_store"}

    # データタイルのみ (共有凡例のダミー図は対象外)
    figs = _graph_figures(spatial["spatial_plots_container"]["children"],
                          id_type="spatial_graph")
    assert figs, "Spatial の figure が生成されていない"
    for f in figs:
        types = {t.get("type") for t in f.get("data", [])}
        assert types == {"scattergl"}, f"SVG トレースが混ざっている: {types}"
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

def _marker_sizes(fig_dict):
    return [t.get("marker", {}).get("size")
            for t in fig_dict.get("data", [])
            if isinstance(t.get("meta"), dict) and "dsz" in t["meta"]]


def _marker_opacities(fig_dict):
    return [t.get("marker", {}).get("opacity")
            for t in fig_dict.get("data", [])
            if isinstance(t.get("meta"), dict) and t["meta"].get("op")]


def _label_sizes(fig_dict):
    return [a.get("font", {}).get("size")
            for a in fig_dict.get("layout", {}).get("annotations", [])]


@pytest.mark.parametrize("marker_size", [3, 9, 0])
def test_display_overrides_match_fresh_build(marker_size):
    """後付け適用 == 最初からその値でビルド（マーカーサイズ）。"""
    from app.utils.display_helpers import apply_display_overrides

    fresh, _ = _spatial_fig(marker_size=marker_size)
    built, _ = _spatial_fig(marker_size=1)          # 別の値で作ってから
    patched = apply_display_overrides(built.to_dict(), marker_size=marker_size)

    assert _marker_sizes(patched) == _marker_sizes(fresh.to_dict())
    assert _marker_sizes(patched), "meta タグ付きトレースが 1 つも無い"


def test_display_overrides_match_fresh_build_opacity_and_label():
    """後付け適用 == 最初からその値でビルド（不透明度・ラベルサイズ）。"""
    from app.utils.display_helpers import apply_display_overrides

    fresh, _ = _spatial_fig(marker_size=4, spot_opacity=0.4,
                            label_size=18, show_labels=True)
    built, _ = _spatial_fig(marker_size=4, spot_opacity=1.0,
                            label_size=10, show_labels=True)
    patched = apply_display_overrides(built.to_dict(), marker_size=4,
                                      spot_opacity=0.4, label_size=18)

    fresh_d = fresh.to_dict()
    assert _marker_opacities(patched) == _marker_opacities(fresh_d)
    assert _label_sizes(patched) == _label_sizes(fresh_d)
    assert _label_sizes(patched), "ラベル注記が 1 つも無い"


def test_display_overrides_auto_uses_layout_meta():
    """marker_size=0（自動）は layout.meta.auto_msz を使う。"""
    from app.utils.display_helpers import apply_display_overrides

    fig, _ = _spatial_fig(marker_size=7)
    d = fig.to_dict()
    auto = d["layout"]["meta"]["auto_msz"]
    assert auto > 0
    patched = apply_display_overrides(d, marker_size=0)
    # dsz=0 のトレースはちょうど auto、dsz=1 のトレースは auto+1
    sizes = set(_marker_sizes(patched))
    assert sizes <= {auto, auto + 1} and auto in sizes


def test_display_overrides_respects_tile_kind():
    """kinds に合わない図は一切変更しない（通常用スライダーが H&E に効かない）。"""
    from app.utils.display_helpers import apply_display_overrides

    fig, _ = _spatial_fig(marker_size=4)
    d = fig.to_dict()
    before = _marker_sizes(d)
    apply_display_overrides(d, marker_size=20, kinds=("hne",))
    assert _marker_sizes(d) == before


def test_display_overrides_ignores_untagged_traces():
    """meta を持たないトレース（凡例ダミー等）は触らない。"""
    from app.utils.display_helpers import apply_display_overrides

    fig, _ = _spatial_fig(marker_size=4)
    d = fig.to_dict()
    untagged_before = [t.get("marker", {}).get("size")
                       for t in d["data"] if not isinstance(t.get("meta"), dict)]
    apply_display_overrides(d, marker_size=25, spot_opacity=0.1)
    untagged_after = [t.get("marker", {}).get("size")
                      for t in d["data"] if not isinstance(t.get("meta"), dict)]
    assert untagged_before == untagged_after
    assert untagged_before, "凡例ダミートレースが存在しない"


def test_cosmetic_sliders_are_not_inputs_of_spatial_callback(dash_app):
    """見た目スライダーが Input に戻っていないこと（戻ると全図再構築が復活する）。"""
    import dash._callback as dc

    key = [k for k in dc.GLOBAL_CALLBACK_MAP if "spatial_plots_container" in k][0]
    spec = dc.GLOBAL_CALLBACK_MAP[key]
    input_ids = {i["id"] for i in spec["inputs"]}
    state_ids = {s["id"] for s in spec["state"]}
    for cid in ("spatial_marker_size", "spatial_label_size",
                "hne_overlay_opacity", "hne_overlay_marker_size"):
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
    assert restyle_fns == {"marker_size", "label_size",
                           "spot_opacity", "hne_marker_size"}, restyle_fns


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
