"""切片/ROI のチェックを全部外すと、逆に全部が対象になる (B-4)。

切片 (Annotation) や ROI のチェックを全部外して解析を実行すると、
「1 つも選ばない＝何も使わない」つもりなのに **逆に全部が解析対象**になる。
画面にも記録にも「フィルタなしで実行しました」としか残らないので、
外したはずのデータが混ざったまま結果が出る。

原因は、集約コールバックが 2 つの状況を **どちらも None** で返すこと:

    if not all_values:            return None      # 部品そのものが無い
    return sorted(set(merged)) if merged else None # 部品はあるが全解除  ← 同じ None

下流は truthy 判定なので None は「フィルタ指定なし」＝全採用と解釈される。
しかも 1 サンプル分だけ全解除した場合は R 側が「該当 spot なし」で落ちるため、
**挙動が正反対**になる。

ご指定の方針: **実行前に止める**。黙って反対のことをする状態を無くす。
そのために「部品が無い (None)」と「部品はあるが全解除 ([])」を型で区別する。
"""

import pytest

import app.callbacks.analysis_callbacks as ac
import app.callbacks.file_handlers as fh


# ---------------------------------------------------------------------------
# ① 「部品が無い」と「全部外した」を区別する
# ---------------------------------------------------------------------------

_SYNCS = [
    ("sync_annotation_to_store", "切片 (本解析)"),
    ("sync_desi_roi_to_store", "ROI"),
    ("sync_reanalysis_annotation_to_store", "切片 (再解析)"),
]


@pytest.mark.parametrize("fn_name,label", _SYNCS)
def test_no_parts_still_means_no_filter(fn_name, label):
    """部品自体が無いときは従来どおり None（＝フィルタ指定なし）。"""
    assert getattr(fh, fn_name)([]) is None, f"{label}: 部品が無いのに None でない"


@pytest.mark.parametrize("fn_name,label", _SYNCS)
def test_unchecking_everything_is_not_the_same_as_no_parts(fn_name, label):
    """★ 本丸: 全部外したときは空リストを返し、None と区別すること。"""
    assert getattr(fh, fn_name)([[], []]) == [], (
        f"{label}: 全解除が None になっている。"
        "下流は None を「フィルタ指定なし＝全採用」と読むので、"
        "**外したはずのデータが全部入る**")


@pytest.mark.parametrize("fn_name,label", _SYNCS)
def test_a_normal_selection_is_unchanged(fn_name, label):
    """選んでいるときの結果は変えないこと。"""
    assert getattr(fh, fn_name)([["b", "a"], ["a"]]) == ["a", "b"], label


# ---------------------------------------------------------------------------
# ② 全解除なら実行前に止める
# ---------------------------------------------------------------------------

@pytest.fixture
def good(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    (data / "s1.txt").write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    return dict(
        desi_method="desi_v8", tims_method=None,
        data_folder=str(data), reanalysis_data_folder="",
        output_dir=str(out),
        p_thresh=0.05, logfc_thresh=0.25, tolerance_mz=0.01,
        resume_rds=False, rds_folder="", rds_folder_reanalysis="",
    )


def test_all_annotations_unchecked_blocks_the_run(good, tmp_path):
    """★ TIMS 本解析: 切片を 1 つも選んでいないなら止めること。"""
    good.update(desi_method=None, tims_method="tims_v8")
    blocking, _ = ac._collect_preflight_errors(**good, annotation_filter=[])
    assert any("切片" in e for e in blocking), (
        f"切片を全解除しても止まらない（全件が対象になる）: {blocking}")


def test_all_annotations_unchecked_blocks_the_reanalysis(good, tmp_path):
    """★ 再解析側も同じ（ver57.5 で再解析にも切片フィルタが効くようになった）。"""
    data = tmp_path / "redata"
    data.mkdir()
    (data / "s1.parquet").write_text("x", encoding="utf-8")
    good.update(desi_method=None, tims_method="tims_cluster_filter",
                reanalysis_data_folder=str(data),
                rds_folder_reanalysis=str(tmp_path / "out"))
    blocking, _ = ac._collect_preflight_errors(
        **good, annotation_filter_reanalysis=[])
    assert any("切片" in e for e in blocking), blocking


def test_all_rois_unchecked_blocks_when_roi_mode_is_on(good):
    """★ DESI: ROI をサンプルとして扱うのに 1 つも選んでいないなら止めること。"""
    blocking, _ = ac._collect_preflight_errors(
        **good, roi_filter=[], use_roi_as_sample=True)
    assert any("ROI" in e for e in blocking), (
        f"ROI を全解除しても止まらない: {blocking}")


def test_all_rois_unchecked_is_harmless_when_roi_mode_is_off(good):
    """ROI モードが OFF なら ROI フィルタは使われない。止めすぎない。"""
    blocking, _ = ac._collect_preflight_errors(
        **good, roi_filter=[], use_roi_as_sample=False)
    assert not blocking, f"ROI を使わない設定なのに止めている: {blocking}"


def test_no_filter_parts_does_not_block(good):
    """部品が無い（None）ときは従来どおり通すこと。"""
    good.update(desi_method=None, tims_method="tims_v8")
    blocking, _ = ac._collect_preflight_errors(
        **good, annotation_filter=None, roi_filter=None)
    assert not blocking, f"フィルタ部品が無いのに止めている: {blocking}"


def test_a_normal_selection_does_not_block(good):
    good.update(desi_method=None, tims_method="tims_v8")
    blocking, _ = ac._collect_preflight_errors(**good, annotation_filter=["s1"])
    assert not blocking, blocking


def test_the_message_says_what_to_do(good):
    """理由と次の一手が分かる文言にすること。"""
    good.update(desi_method=None, tims_method="tims_v8")
    blocking, _ = ac._collect_preflight_errors(**good, annotation_filter=[])
    msg = " ".join(blocking)
    assert "1 つも" in msg or "すべて" in msg, msg
    assert "全件" in msg or "チェック" in msg, (
        f"どうすれば良いかが書かれていない: {msg}")


# ---------------------------------------------------------------------------
# ③ 実行側も同じ判断をする（表示だけで終わらせない）
# ---------------------------------------------------------------------------

def test_the_run_gate_receives_the_filters():
    """★ run_analysis のゲートがフィルタ Store を渡していること。

    渡していなければ、表示側だけが赤くなって解析は走ってしまう
    （ver56.7 で一度直した「赤と緑が同時に出る」型の再発）。
    """
    import inspect

    src = inspect.getsource(ac.run_analysis)
    head = src[:src.index("# 現在の設定を自動保存")]
    # 先頭側にはコメント中の言及があるので、実際の呼び出し（最後の出現）を見る
    i = head.rindex("_collect_preflight_errors(")
    call = head[i:i + 700]
    for kw in ("annotation_filter", "roi_filter", "use_roi_as_sample"):
        assert kw in call, f"実行側のゲートに {kw} を渡していない"


def test_the_display_gate_receives_the_filters():
    """表示側も同じ引数で呼ぶこと（表示と実行が食い違わないように）。"""
    import inspect

    src = inspect.getsource(ac.preflight_validation)
    for kw in ("annotation_filter", "roi_filter", "use_roi_as_sample"):
        assert kw in src, f"表示側のゲートに {kw} を渡していない"
