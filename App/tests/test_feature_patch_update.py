"""m/z 切替の差分更新 (ver51.5)。

従来は m/z を 1 つ変えるたびに、変わらない座標・CellID・TIC 背景まで含めて
グラフ全体を作り直して送っていた (4 サンプルで gzip 後 約 4.5MB)。

幾何を m/z 非依存にしたうえで、
  - 殻 (グラフの枠・座標・TIC 背景) は update_feature_plot が作る
  - m/z / 強度レンジの変更は patch_feature_intensity が figure を差分更新する
の 2 段に分けた。ここではその分担が壊れないことを固定する。

★ いちばん怖い壊れ方は「作り直すべき時に差分だけ送って画面が更新されない」と
  「画面と一括保存 PNG で m/z が食い違う」の 2 つ。両方テストする。
"""

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("dash")
pytest.importorskip("plotly")

import app.callbacks.interactive_deg as ID  # noqa: E402


def _ops(patch_obj):
    """Patch の代入操作を {場所: 値} に均す。"""
    out = {}
    for op in patch_obj._operations:
        out[tuple(op["location"])] = op["params"]["value"]
    return out


@pytest.fixture
def synthetic(monkeypatch):
    """plot_data と発現量を差し込んだ状態を作る。"""
    import app.callbacks.interactive_callbacks as IC

    n_side = 12
    gx, gy = np.meshgrid(np.arange(n_side), np.arange(n_side))
    gx, gy = gx.ravel(), gy.ravel()
    rows = []
    for s in ("S1", "S2"):
        rows.append(pd.DataFrame({
            "Sample": s,
            "CellID": [f"{s}_{i}" for i in range(gx.size)],
            "SpatialX": gx.astype(float),
            "SpatialY": gy.astype(float),
            "Cluster": ((gx + gy) % 3).astype(str),
            "TotalCount": (gx + gy).astype(float),
        }))
    df = pd.concat(rows, ignore_index=True)

    rds = "/rds/patch.rds"
    IC._set_active_key(rds)
    IC._interactive_data["plot_data"] = df
    IC._interactive_data["rds_path"] = rds
    monkeypatch.setattr(IC._bridge, "ensure_expression_matrix",
                        lambda p: None, raising=False)
    monkeypatch.setattr(
        IC._bridge, "get_feature_expression_fast",
        lambda c, f: pd.Series(np.linspace(0.0, 1.0, len(df))), raising=False)
    return df, rds


def _set_outputs(monkeypatch, indices):
    """ctx.outputs_list を差し替える (pattern-matching Output の実体)。"""
    class _Ctx:
        outputs_list = [
            [{"id": {"type": "feature_graph", "index": i}, "property": "figure"}
             for i in indices],
            [{"id": {"type": "feature_graph", "index": i}, "property": "config"}
             for i in indices],
        ]
        triggered_id = "feature_select"
    monkeypatch.setattr(ID, "ctx", _Ctx)


def test_returns_nothing_when_no_graphs_exist(synthetic, monkeypatch):
    """殻がまだ無いときは何も返さない (update_feature_plot が作る)。"""
    _df, rds = synthetic
    _set_outputs(monkeypatch, [])
    figs, cfgs = ID.patch_feature_intensity(
        "mz_100", None, None, False, None, {}, rds, "/tmp/cache", "sess")
    assert figs == [] and cfgs == []


def test_patches_only_color_opacity_and_note(synthetic, monkeypatch):
    """★ 差し替えるのは色・不透明度・注記・色域だけ。座標と CellID は触らない。"""
    _df, rds = synthetic
    _set_outputs(monkeypatch, ["S1", "S2"])
    figs, cfgs = ID.patch_feature_intensity(
        "mz_100", None, None, False, None, {}, rds, "/tmp/cache", "sess")

    assert len(figs) == 2 and len(cfgs) == 2
    for f in figs:
        loc = _ops(f)
        keys = set(loc)
        assert ("data", -1, "marker", "color") in keys
        assert ("data", -1, "marker", "opacity") in keys
        assert ("data", -1, "marker", "cmin") in keys
        assert ("data", -1, "marker", "cmax") in keys
        assert ("data", -1, "customdata") in keys
        # ★ 幾何は差分に含めない (含めたら削減効果が消える)
        for forbidden in ("x", "y", "text"):
            assert not any(k[-1] == forbidden for k in keys), \
                f"幾何 ({forbidden}) を送っている: {keys}"


def test_config_filename_follows_the_feature(synthetic, monkeypatch):
    """★ PNG ダウンロードのファイル名も一緒に更新されること。

    図だけ更新すると、古い m/z の名前で保存される。
    """
    _df, rds = synthetic
    _set_outputs(monkeypatch, ["S1"])
    _f, cfgs = ID.patch_feature_intensity(
        "mz_777", None, None, False, None, {}, rds, "/tmp/cache", "sess")
    fname = cfgs[0]["toImageButtonOptions"]["filename"]
    assert "mz_777" in fname and fname.endswith("_S1"), fname


def test_config_filename_uses_display_name(synthetic, monkeypatch):
    """サンプル表示名 (name_map) が反映されること。"""
    _df, rds = synthetic
    _set_outputs(monkeypatch, ["S1"])
    _f, cfgs = ID.patch_feature_intensity(
        "mz_100", None, None, False, None, {"S1": "腫瘍部"},
        rds, "/tmp/cache", "sess")
    assert cfgs[0]["toImageButtonOptions"]["filename"].endswith("_腫瘍部")


def test_intensity_range_changes_opacity_not_geometry(synthetic, monkeypatch):
    """強度レンジを上げると隠れる点が増える (点は消えない)。"""
    _df, rds = synthetic
    _set_outputs(monkeypatch, ["S1"])

    def _alpha(imin):
        figs, _ = ID.patch_feature_intensity(
            "mz_100", imin, None, False, None, {}, rds, "/tmp/cache", "sess")
        return np.asarray(_ops(figs[0])[("data", -1, "marker", "opacity")])

    a0, a50 = _alpha(0), _alpha(50)
    assert len(a0) == len(a50), "点数が変わっている"
    assert int((a50 == 0).sum()) > int((a0 == 0).sum())


def test_below_threshold_points_get_a_note(synthetic, monkeypatch):
    """★ 隠れた点には注記が付く (opacity=0 でも hover は出るため)。"""
    _df, rds = synthetic
    _set_outputs(monkeypatch, ["S1"])
    figs, _ = ID.patch_feature_intensity(
        "mz_100", 50, None, False, None, {}, rds, "/tmp/cache", "sess")
    loc = _ops(figs[0])
    alpha = np.asarray(loc[("data", -1, "marker", "opacity")])
    note = np.asarray(loc[("data", -1, "customdata")], dtype=object)
    hidden = alpha == 0
    assert hidden.any()
    assert all(note[i] for i in np.flatnonzero(hidden))
    assert not any(note[i] for i in np.flatnonzero(~hidden))


def test_stored_export_figures_follow_the_screen(synthetic, monkeypatch):
    """★ 一括保存用の figure にも同じ差分が当たること。

    ここが抜けると「画面は新しい m/z、保存 PNG は古い m/z」になる。
    """
    from app.callbacks.interactive_callbacks import (
        get_export_figures, set_export_figures)

    _df, rds = synthetic
    # update_feature_plot が置いたのと同じ形の figure を用意する
    n = 144
    stored = [("Feature_mz_OLD_S1", {"data": [
        {"marker": {"color": [0.0] * n, "opacity": [1.0] * n,
                    "colorbar": {"ticktext": ["0%", "100%"]}},
         "customdata": [""] * n, "meta": "old"},
    ]})]
    set_export_figures("feature", "sess", rds, stored)

    _set_outputs(monkeypatch, ["S1"])
    figs, _ = ID.patch_feature_intensity(
        "mz_NEW", 50, None, False, None, {}, rds, "/tmp/cache", "sess")

    after = get_export_figures("feature", "sess", rds)
    name, fig_d = after[0]
    assert "mz_NEW" in name, f"保存名が古いまま: {name}"

    screen = _ops(figs[0])
    tr = fig_d["data"][-1]
    assert list(tr["marker"]["color"]) == list(
        screen[("data", -1, "marker", "color")]), "画面と保存で色が違う"
    assert list(tr["marker"]["opacity"]) == list(
        screen[("data", -1, "marker", "opacity")]), "画面と保存で不透明度が違う"
    assert tr["marker"]["cmin"] == screen[("data", -1, "marker", "cmin")]


def test_shell_is_reused_only_for_data_only_triggers(synthetic, monkeypatch):
    """★ 殻を作り直すかの判断。

    - m/z を変えただけ かつ 実在するグラフが揃っている → 作り直さない
    - グラフが実在しない (リロード直後など) → **必ず作り直す**
      これが無いと、サーバ側だけ「作った」と思い込んで画面が白いまま残る。
    """
    import app.callbacks.interactive_callbacks as IC
    _df, rds = synthetic

    calls = {"built": 0}
    real = ID._feature_intensity_style

    def counting(*a, **kw):
        calls["built"] += 1
        return real(*a, **kw)

    class _Ctx:
        triggered_id = "feature_select"
        outputs_list = []
    monkeypatch.setattr(ID, "ctx", _Ctx)
    monkeypatch.setattr(ID, "_feature_intensity_style", counting)

    existing = [{"type": "feature_graph", "index": s} for s in ("S1", "S2")]

    # ① グラフが揃っている → 殻は no_update (作り直さない)
    children, heading, _p1, _p2 = ID.update_feature_plot(
        "mz_100", None, None, None, {}, 0, 0, False,
        0, "Plasma", existing, rds, "/tmp/cache", {}, None, "sess")
    from dash import no_update as _NU
    assert children is _NU, "作り直す必要が無いのに殻を送っている"
    assert heading is not _NU, "見出しは常に更新すべき"
    assert calls["built"] == 0, "殻を作らないはずが図を組んでいる"

    # ② グラフが実在しない → 作り直す
    calls["built"] = 0
    children2, _h, _p1, _p2 = ID.update_feature_plot(
        "mz_100", None, None, None, {}, 0, 0, False,
        0, "Plasma", [], rds, "/tmp/cache", {}, None, "sess")
    assert children2 is not _NU, "グラフが無いのに作り直していない"
    assert calls["built"] > 0


# ---------------------------------------------------------------------------
# カラーバーの同期 (ver51.6)
# ---------------------------------------------------------------------------
# 外部監査が拾った取りこぼし。差分更新は色域 (cmin/cmax) を動かすのに、
# カラーバーの目盛りを一緒に動かしていなかった。ラベル ("0%"〜"100%") は
# 強度レンジに、目盛り位置は cmin/cmax に対応するので、放置すると
# **カラーバーの読み方が狂う**。しかも保存用 figure 側だけは ticktext を
# 直していたので、画面と PNG で食い違う状態だった。

def test_colorbar_ticks_follow_the_intensity_range(synthetic, monkeypatch):
    """★ 強度レンジを変えると目盛りの位置とラベルの両方が追従すること。"""
    _df, rds = synthetic
    _set_outputs(monkeypatch, ["S1"])
    figs, _c = ID.patch_feature_intensity(
        "mz_100", 20, 80, False, None, {}, rds, "/tmp/cache", "sess")
    loc = _ops(figs[0])

    tickvals = loc[("data", -1, "marker", "colorbar", "tickvals")]
    ticktext = loc[("data", -1, "marker", "colorbar", "ticktext")]
    assert ticktext == ["20%", "80%"]
    # 目盛りの位置は色域そのもの。ずれるとバーの読み方が狂う。
    assert tickvals == [loc[("data", -1, "marker", "cmin")],
                        loc[("data", -1, "marker", "cmax")]]


def test_colorbar_is_only_touched_on_the_tile_that_has_one(synthetic, monkeypatch):
    """★ カラーバーを持たないタイルには書かないこと。

    殻は最後のタイルにだけカラーバーを付けている。全タイルに書くと、
    Patch がそこに **新しいカラーバーを生やす**。
    """
    _df, rds = synthetic
    _set_outputs(monkeypatch, ["S1", "S2"])
    figs, _c = ID.patch_feature_intensity(
        "mz_100", None, None, False, None, {}, rds, "/tmp/cache", "sess")

    def has_colorbar(f):
        return any("colorbar" in k for k in _ops(f))

    assert not has_colorbar(figs[0]), "先頭タイルにカラーバーを書いている"
    assert has_colorbar(figs[-1]), "最後のタイルのカラーバーが更新されていない"


def test_stored_export_colorbar_matches_the_screen(synthetic, monkeypatch):
    """★ 保存用 figure のカラーバーも画面と一致すること。"""
    from app.callbacks.interactive_callbacks import (
        get_export_figures, set_export_figures)
    _df, rds = synthetic
    n = 144
    stored = [("Feature_mz_OLD_S1", {"data": [
        {"marker": {"color": [0.0] * n, "opacity": [1.0] * n,
                    "colorbar": {"tickvals": [0, 1],
                                 "ticktext": ["0%", "100%"]}},
         "customdata": [""] * n, "meta": "old"}]})]
    set_export_figures("feature", "sess", rds, stored)

    _set_outputs(monkeypatch, ["S1"])
    figs, _c = ID.patch_feature_intensity(
        "mz_NEW", 20, 80, False, None, {}, rds, "/tmp/cache", "sess")

    screen = _ops(figs[0])
    cb = get_export_figures("feature", "sess", rds)[0][1]["data"][-1]["marker"]["colorbar"]
    assert cb["ticktext"] == screen[("data", -1, "marker", "colorbar", "ticktext")]
    assert cb["tickvals"] == screen[("data", -1, "marker", "colorbar", "tickvals")]


# ---------------------------------------------------------------------------
# 発現量が壊れているときの番人 (ver51.6)
# ---------------------------------------------------------------------------
# 外部監査の指摘。差分更新は発現量を検証せずに np.asarray して DataFrame へ
# 代入していた。None や長さ不一致だと pandas が例外を投げ、**m/z を変える
# たびにコールバックが落ちる**。R フォールバックはヘッダ 1 行ぶん長い Series を
# 返しうるので、長さのずれは実際に起こる。

@pytest.mark.parametrize("broken", ["none", "too_long", "too_short"])
def test_patch_does_nothing_when_expression_is_unusable(synthetic, monkeypatch,
                                                        broken):
    """★ 落ちずに no_update を返すこと (画面は前の m/z のまま残る)。"""
    import app.callbacks.interactive_callbacks as IC
    df, rds = synthetic
    _set_outputs(monkeypatch, ["S1", "S2"])

    bad = {"none": None,
           "too_long": pd.Series(np.zeros(len(df) + 1)),
           "too_short": pd.Series(np.zeros(len(df) - 1))}[broken]
    monkeypatch.setattr(IC._bridge, "get_feature_expression_fast",
                        lambda c, f: bad, raising=False)
    monkeypatch.setattr(IC._bridge, "get_feature_expression",
                        lambda p, f: bad, raising=False)

    figs, cfgs = ID.patch_feature_intensity(
        "mz_100", None, None, False, None, {}, rds, "/tmp/cache", "sess")
    from dash import no_update as _NU
    assert figs == [_NU, _NU] and cfgs == [_NU, _NU]


def test_shell_shows_a_message_when_expression_is_unusable(synthetic, monkeypatch):
    """殻の側も同じ条件で落ちず、理由を表示すること。"""
    import app.callbacks.interactive_callbacks as IC
    _df, rds = synthetic

    class _Ctx:
        triggered_id = "feature_select"
        outputs_list = []
    monkeypatch.setattr(ID, "ctx", _Ctx)
    monkeypatch.setattr(IC._bridge, "get_feature_expression_fast",
                        lambda c, f: None, raising=False)
    monkeypatch.setattr(IC._bridge, "get_feature_expression",
                        lambda p, f: None, raising=False)

    children, _h, _p1, _p2 = ID.update_feature_plot(
        "mz_100", None, None, None, {}, 0, 0, False,
        0, "Plasma", [], rds, "/tmp/cache", {}, None, "sess")
    assert "発現量" in str(children)
