"""エクスポートの直列化コストに関する回帰テスト（ver62.1）。

「クラスタ列と ROI 列を足すだけ」の操作に長時間かかっていた原因は 2 つある。

1. **バイト列を組み立ててから書いていた**
   `to_csv()` の str → `.encode()` の bytes → `BytesIO` → `getvalue()` と 4 重に
   複製し、最後に呼び出し側が `write_bytes` していた。実測（20,000 spot ×
   2,000 m/z）で RSS 増分 **+1.94 GB**（DataFrame 実体 0.15 GB の 13 倍）。
   実データ規模へ外挿すると増分だけで 45 GB になり、`mem_limit: 12g` に対して
   ほぼ全部がホストスワップへ落ちる。

2. **xlsx のガードが列数しか見ていなかった**
   openpyxl は実測で約 19 秒・0.30 GB / 百万セル。実データ規模の 9.28 億セルなら
   約 4.9 時間・約 278 GB で完走しない。4,566 m/z は列数上限(16,384)を通り抜けるため、
   止まらずに走り出して延々と終わらなかった。

出力の**中身**は変えていない。速くなっただけで値は同じであることを固定する。
"""

import io
from collections import OrderedDict
from pathlib import Path

import pandas as pd
import pytest

from app.callbacks import interactive_data_export as de

_MZ = ["100.0001", "200.0002", "300.0003"]


@pytest.fixture
def tims_parquet(tmp_path):
    df = pd.DataFrame({
        "id": range(1, 9),
        "x": [10.0, 11.0, 12.0, 13.0] * 2,
        "y": [20.0, 21.0, 22.0, 23.0] * 2,
        _MZ[0]: [1.0, 3.0, 5.0, 7.0, 11.0, 13.0, 15.0, 17.0],
        _MZ[1]: [2.0, 4.0, 6.0, 8.0, 12.0, 14.0, 16.0, 18.0],
        _MZ[2]: [0.5, 0.5, 0.5, 0.5, 1.5, 1.5, 1.5, 1.5],
        "annotation": ["S1"] * 4 + ["S2"] * 4,
    })
    d = tmp_path / "data"
    d.mkdir()
    df.to_parquet(d / "S.parquet", index=False)
    return d


@pytest.fixture
def lookups():
    return OrderedDict([("Harmony", {
        ("S1", 10.0, 20.0): "cluster1", ("S1", 11.0, 21.0): "cluster1",
        ("S1", 12.0, 22.0): "cluster2", ("S1", 13.0, 23.0): "cluster2",
        ("S2", 10.0, 20.0): "cluster1", ("S2", 11.0, 21.0): "cluster1",
        ("S2", 12.0, 22.0): "cluster2", ("S2", 13.0, 23.0): "cluster2",
    })])


# ---------------------------------------------------------------------------
# 1. パスへ直接書く（巨大な中間オブジェクトを作らない）
# ---------------------------------------------------------------------------
def test_returns_a_written_path_not_bytes(tims_parquet, lookups, tmp_path):
    """戻り値は**書き出し済みファイルのパス**。バイト列ではない。"""
    out, filename = de._export_tims(str(tims_parquet), lookups, "csv",
                                    out_dir=tmp_path / "out")
    assert isinstance(out, Path), f"パスであるべき: {type(out)}"
    assert out.exists() and out.stat().st_size > 0
    assert out.name == filename


def test_csv_is_written_straight_to_the_path(tims_parquet, lookups, tmp_path,
                                             monkeypatch):
    """`to_csv` がパスを受け取って呼ばれる（巨大 str を作っていない）。

    出力ファイルを見るだけでは「str を作ってから書いた」と区別が付かない。
    実データではここがメモリの支配項なので、呼び出し引数そのものを固定する。
    """
    seen = {}
    real = pd.DataFrame.to_csv

    def spy(self, path_or_buf=None, *a, **kw):
        seen["target"] = path_or_buf
        return real(self, path_or_buf, *a, **kw)

    monkeypatch.setattr(pd.DataFrame, "to_csv", spy, raising=True)
    de._export_tims(str(tims_parquet), lookups, "csv", out_dir=tmp_path / "o")

    assert seen["target"] is not None, (
        "to_csv() が引数なしで呼ばれている＝巨大な str を組み立てている")
    assert not isinstance(seen["target"], io.IOBase), (
        "BytesIO 経由になっている")


def test_parquet_is_written_straight_to_the_path(tims_parquet, lookups, tmp_path,
                                                 monkeypatch):
    seen = {}
    real = pd.DataFrame.to_parquet

    def spy(self, path=None, *a, **kw):
        seen["target"] = path
        return real(self, path, *a, **kw)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", spy, raising=True)
    de._export_tims(str(tims_parquet), lookups, "parquet", out_dir=tmp_path / "o")

    assert seen["target"] is not None
    assert not isinstance(seen["target"], io.IOBase), "BytesIO 経由になっている"


def test_prefix_keeps_outputs_from_colliding(tims_parquet, lookups, tmp_path):
    """prefix でジョブごとにファイル名が衝突しない（本番は job_id を渡す）。"""
    a, _ = de._export_tims(str(tims_parquet), lookups, "csv",
                           out_dir=tmp_path, prefix="job1__")
    b, _ = de._export_tims(str(tims_parquet), lookups, "csv",
                           out_dir=tmp_path, prefix="job2__")
    assert a != b and a.exists() and b.exists()
    assert a.name.startswith("job1__") and b.name.startswith("job2__")


def test_out_dir_none_still_works(tims_parquet, lookups):
    """out_dir 未指定でも動く（テスト・アドホック用の逃げ道）。"""
    out, _ = de._export_tims(str(tims_parquet), lookups, "csv")
    assert out.exists()


# ---------------------------------------------------------------------------
# 2. 出力の中身は変わっていない
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("fmt", ["csv", "parquet"])
def test_content_is_unchanged(tims_parquet, lookups, tmp_path, fmt):
    """速くなっただけで値は同じ。列・行・値が期待どおり。"""
    out, _ = de._export_tims(str(tims_parquet), lookups, fmt,
                             out_dir=tmp_path / fmt)
    got = pd.read_parquet(out) if fmt == "parquet" else pd.read_csv(out)
    assert list(got.columns) == ["id", "x", "y", *_MZ, "annotation", "UMAP cluster"]
    assert len(got) == 8
    assert set(got["UMAP cluster"]) == {"cluster1", "cluster2"}


def test_same_values_across_formats(tims_parquet, lookups, tmp_path):
    """csv と parquet で同じ値が出る（形式を変えても解析結果は変わらない）。"""
    c, _ = de._export_tims(str(tims_parquet), lookups, "csv", out_dir=tmp_path / "c")
    p, _ = de._export_tims(str(tims_parquet), lookups, "parquet",
                           out_dir=tmp_path / "p")
    pd.testing.assert_frame_equal(
        pd.read_csv(c), pd.read_parquet(p), check_dtype=False)


# ---------------------------------------------------------------------------
# 3. xlsx のサイズガード
# ---------------------------------------------------------------------------
def test_xlsx_cell_guard_stops_before_running(tims_parquet, lookups, tmp_path,
                                              monkeypatch):
    """セル数が上限を超えたら**走り出す前に**止め、代替を案内する。

    従来は列数しか見ておらず、終わらないまま走り続けていた。
    """
    monkeypatch.setattr(de, "XLSX_MAX_CELLS", 10)   # 8 行 × 7 列 = 56 > 10
    with pytest.raises(ValueError) as e:
        de._export_tims(str(tims_parquet), lookups, "xlsx", out_dir=tmp_path)
    msg = str(e.value)
    assert "セル" in msg, msg
    for hint in ("Parquet", "CSV", "強度"):
        assert hint in msg, f"代替の案内 '{hint}' が無い: {msg}"


def test_xlsx_passes_under_the_limit(tims_parquet, lookups, tmp_path):
    """上限内なら従来どおり xlsx を書ける。"""
    out, filename = de._export_tims(str(tims_parquet), lookups, "xlsx",
                                    out_dir=tmp_path)
    assert filename.endswith(".xlsx") and out.exists()
    assert len(pd.read_excel(out, sheet_name="Data")) == 8


def test_column_limit_guard_still_applies():
    """既存の列数上限(16,384)の検査が残っている。"""
    with pytest.raises(ValueError, match="16,384"):
        de._guard_xlsx_size(10, 20_000)


def test_cell_guard_message_names_the_numbers():
    """メッセージに実際の行数・列数・セル数・上限が入る。

    「大きすぎます」だけだと、利用者はどこまで減らせばよいか分からない。
    """
    with pytest.raises(ValueError) as e:
        de._guard_xlsx_size(200_000, 4_570)
    msg = str(e.value)
    for token in ("200,000", "4,570", "914,000,000"):
        assert token in msg, f"{token} がメッセージに無い: {msg}"


# ---------------------------------------------------------------------------
# 4. 直列化中も進捗が出る
# ---------------------------------------------------------------------------
def test_progress_is_reported_during_serialization(tims_parquet, lookups, tmp_path):
    """書き出し工程でも進捗が報告される。

    従来は読み込みループでしか報告されず、読み終えた時点で 98% に達して
    **最も長い直列化の間バーが動かなかった**。「止まった」ように見えていた。
    """
    seen = []
    de._export_tims(str(tims_parquet), lookups, "csv", out_dir=tmp_path,
                    progress_cb=lambda p, l="": seen.append((p, l)),
                    base=58, span=40)

    labels = [l for _, l in seen]
    assert any("読み込み" in l for l in labels), f"読み込みの報告が無い: {labels}"
    assert any("書き出し" in l for l in labels), (
        f"書き出しの報告が無い（98% で固まって見える）: {labels}")


def test_reading_does_not_consume_the_whole_progress_range(tims_parquet, lookups,
                                                           tmp_path):
    """読み込みだけで 98% まで進めない（書き出しに幅を残す）。"""
    seen = []
    de._export_tims(str(tims_parquet), lookups, "csv", out_dir=tmp_path,
                    progress_cb=lambda p, l="": seen.append((p, l)),
                    base=58, span=40)
    read_pcts = [p for p, l in seen if "読み込み" in l]
    assert read_pcts, "読み込みの進捗が無い"
    assert max(read_pcts) <= 92, (
        f"読み込みだけで {max(read_pcts)}% まで進んでいる（書き出しの幅が無い）")


# ---------------------------------------------------------------------------
# 5. 書き込み途中のファイルが見えない（PR #169 のレビュー指摘 P1）
# ---------------------------------------------------------------------------
# ChatGPT API の状態窓口は `<job_id>__*` の glob が当たるだけで、ジョブ記録を
# 見る前に status: done を返す（レジストリが上限掃除で消えても解決できるよう、
# 仕様として意図的に残されている）。バイト列を 1 回で書いていた頃は窓が数ミリ秒
# だったが、pandas が数分かけて書くようになると、その間のポーリングが
# **切り詰められた CSV / 壊れた Parquet** をダウンロードさせてしまう。
_GLOB_PREFIX = "job123__"


def test_pandas_never_writes_to_the_api_visible_name(tims_parquet, lookups,
                                                     tmp_path, monkeypatch):
    """pandas に渡す書き込み先が `<job_id>__*` に当たらない。

    「書き込み中にディレクトリを覗く」形の検査は、覗く瞬間の取り方で結果が変わり
    退行を取り逃がす（実際に取り逃がした）。**pandas が受け取るパスそのもの**を
    見れば、書き込みの進み具合に関係なく性質を固定できる。
    """
    import fnmatch
    targets = []
    real = pd.DataFrame.to_csv

    def spy(self, path_or_buf=None, *a, **kw):
        targets.append(Path(str(path_or_buf)))
        return real(self, path_or_buf, *a, **kw)

    monkeypatch.setattr(pd.DataFrame, "to_csv", spy, raising=True)
    out, _ = de._export_tims(str(tims_parquet), lookups, "csv",
                             out_dir=tmp_path, prefix=_GLOB_PREFIX)

    assert targets, "to_csv が呼ばれていない"
    for t in targets:
        assert not fnmatch.fnmatch(t.name, f"{_GLOB_PREFIX}*"), (
            f"書き込み先が API の glob に当たる: {t.name}")
    # 差し替え後は最終名で見える
    assert [q.name for q in tmp_path.glob(f"{_GLOB_PREFIX}*")] == [out.name]


def test_failed_serialization_leaves_no_file(tims_parquet, lookups, tmp_path,
                                             monkeypatch):
    """直列化が途中まで書いて失敗しても、ファイルを一切残さない。

    残すと「存在＝完了」の窓口が壊れたファイルを成功として配信する。
    途中まで書いてから落ちる実際の失敗を模す（何も書かずに落ちるだけだと
    後始末が働いているのか、そもそも書いていないだけなのか区別が付かない）。
    """
    def boom(self, path_or_buf=None, *a, **kw):
        Path(str(path_or_buf)).write_text("途中まで書いた", encoding="utf-8")
        raise RuntimeError("書き込み失敗")

    monkeypatch.setattr(pd.DataFrame, "to_csv", boom, raising=True)
    with pytest.raises(RuntimeError):
        de._export_tims(str(tims_parquet), lookups, "csv",
                        out_dir=tmp_path, prefix=_GLOB_PREFIX)

    assert list(tmp_path.glob(f"{_GLOB_PREFIX}*")) == [], "最終名のファイルが残った"
    assert list(tmp_path.glob(".*")) == [], "一時ファイルが残った"


def test_temp_name_cannot_match_the_api_glob(tmp_path):
    """一時ファイル名が API の glob に当たらない（先頭 `.` で始まる）。"""
    final = tmp_path / f"{_GLOB_PREFIX}UMAP_cluster_TIMS.csv"
    with de._atomic_output(final) as tmp:
        tmp.write_text("x", encoding="utf-8")
        during = [q.name for q in tmp_path.glob(f"{_GLOB_PREFIX}*")]
    assert during == [], f"一時名が glob に当たっている: {during}"
    assert tmp.name.startswith("."), tmp.name
    assert tmp.suffix == ".csv", "拡張子が温存されていない（形式推測が壊れる）"
    assert final.exists() and final.read_text(encoding="utf-8") == "x"


@pytest.mark.parametrize("fmt", ["csv", "parquet", "xlsx"])
def test_all_formats_go_through_atomic_replace(tims_parquet, lookups, tmp_path, fmt):
    """3 形式とも最終的に完全なファイルが 1 つだけ残る。"""
    out, _ = de._export_tims(str(tims_parquet), lookups, fmt,
                             out_dir=tmp_path, prefix=_GLOB_PREFIX)
    assert out.exists()
    assert list(tmp_path.glob("*.partial*")) == [], "一時ファイルが残った"
    if fmt == "csv":
        assert len(pd.read_csv(out)) == 8
    elif fmt == "parquet":
        assert len(pd.read_parquet(out)) == 8
    else:
        assert len(pd.read_excel(out, sheet_name="Data")) == 8
