"""データ出力の装置判定 (DESI / TIMS) の回帰テスト。

ver62.2 修正:
  TIMS の parquet を `Data/DESI/Data/…` に置いたプロジェクトで
  「データ出力 (UMAP cluster)」が
  `❌ エラー: DESI .txt ファイルが見つかりません` になっていた。

  `_resolve_instrument` がパス文字列だけで装置を決めており、
  パスに `DESI` という階層があると **metadata の明示 "TIMS" ごと上書き**して
  DESI 経路 (`_export_desi`) へ入れていた。その先の `list_msi_files` は
  `.txt/.csv/.xlsx` しか拾わないので parquet は 0 件になり、出力できなかった。

  metadata も弱い根拠でしかない（保存モーダル `sap_ms_instrument` の既定が
  "TIMS" なので、DESI 利用者が触らずに保存すると "TIMS" が入る）。
  そこで**データフォルダの中身**で確定するようにした。

ver4.9 の修正（DESI が TIMS 経路に落ちる）を壊していないことも同時に見る。
"""

from collections import OrderedDict

import pandas as pd
import pytest

import app.callbacks.interactive_data_export as de
from app.services.data_manager import find_msi_txt, list_msi_files


# ---------------------------------------------------------------------------
# フォルダ作成ヘルパー
# ---------------------------------------------------------------------------

def _parquet_folder(root, name="Data/DESI/Data/proj"):
    """parquet だけが入った（＝TIMS の）データフォルダを作る。"""
    d = root / name
    d.mkdir(parents=True)
    pd.DataFrame({"id": [1, 2], "x": [1.0, 2.0], "y": [1.0, 2.0],
                  "700.1234": [10.0, 20.0],
                  "annotation": ["s1", "s1"]}).to_parquet(d / "s1.parquet")
    return d


def _desi_txt_folder(root, name="Data/DESI/Data/proj", stem="s1", ext=".txt"):
    """DESI 生データ (.txt) が入ったデータフォルダを作る。"""
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}{ext}").write_text(
        "h0a\th0b\th0c\n" * 5 + "1\t1.0\t1.0\n2\t2.0\t2.0\n", encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# _instrument_from_folder — 中身から断定できるものだけ断定する
# ---------------------------------------------------------------------------

def test_parquet_folder_is_tims(tmp_path):
    assert de._instrument_from_folder(_parquet_folder(tmp_path)) == "TIMS"


def test_excel_folder_is_desi(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    pd.DataFrame({"a": [1]}).to_excel(d / "s1.xlsx", index=False)
    assert de._instrument_from_folder(d) == "DESI"


def test_txt_only_folder_is_undecidable(tmp_path):
    # .txt は DESI でも TIMS(legacy) でも使われる。断定しない。
    assert de._instrument_from_folder(_desi_txt_folder(tmp_path)) is None


def test_csv_only_folder_is_undecidable(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    (d / "s1.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert de._instrument_from_folder(d) is None


def test_missing_folder_is_undecidable(tmp_path):
    assert de._instrument_from_folder(tmp_path / "nope") is None
    assert de._instrument_from_folder("") is None


def test_annotation_sidecar_alone_is_not_tims(tmp_path):
    # `*_feature_annotations.parquet` は解析の「サンプル」ではない。
    # これ 1 つで TIMS と断定すると、注釈だけ置いた DESI フォルダを取り違える。
    d = tmp_path / "raw"
    d.mkdir()
    pd.DataFrame({"mz": [700.1], "raw": ["x"]}).to_parquet(
        d / "s1_feature_annotations.parquet")
    assert de._instrument_from_folder(d) is None


# ---------------------------------------------------------------------------
# _decide_instrument — 本バグの本丸
# ---------------------------------------------------------------------------

def test_parquet_under_desi_path_resolves_to_tims(tmp_path):
    """パスが /DESI/ でも、中身が parquet なら TIMS。"""
    d = _parquet_folder(tmp_path)
    assert de._resolve_instrument("", str(d)) == "DESI"        # 従来の判定
    inst, reason = de._decide_instrument("", str(d), "")
    assert inst == "TIMS"
    assert reason == "データフォルダの中身"


def test_explicit_tims_is_not_overridden_by_path(tmp_path):
    """metadata が "TIMS" なのにパスで DESI へ倒されていた（旧挙動）。"""
    d = _parquet_folder(tmp_path)
    assert de._decide_instrument("TIMS", str(d), "")[0] == "TIMS"


def test_result_folder_under_desi_does_not_flip_parquet_project(tmp_path):
    d = _parquet_folder(tmp_path, "raw")
    inst, _ = de._decide_instrument("", str(d), "/app/Data/DESI/Data/proj/out")
    assert inst == "TIMS"


def test_desi_txt_under_desi_path_still_desi(tmp_path):
    """ver4.9 の修正を壊していないこと。"""
    d = _desi_txt_folder(tmp_path)
    inst, reason = de._decide_instrument("", str(d), "")
    assert inst == "DESI"
    assert reason == "フォルダのパス"


def test_explicit_desi_still_wins_when_content_undecidable(tmp_path):
    d = _desi_txt_folder(tmp_path, "raw")
    inst, reason = de._decide_instrument("DESI", str(d), "")
    assert inst == "DESI"
    assert reason == "プロジェクト設定の ms_instrument"


def test_unknown_path_defaults_to_tims(tmp_path):
    d = _desi_txt_folder(tmp_path, "raw")
    assert de._decide_instrument("", str(d), "")[0] == "TIMS"


# ---------------------------------------------------------------------------
# _validate_data_folder / エラーメッセージ — 原因が分かること
# ---------------------------------------------------------------------------

def test_validate_passes_for_matching_folder(tmp_path):
    d = _parquet_folder(tmp_path)
    assert de._validate_data_folder(str(d), "TIMS", "データフォルダの中身") is None


def test_validate_names_the_missing_folder(tmp_path):
    missing = tmp_path / "gone"
    msg = de._validate_data_folder(str(missing), "TIMS", "既定")
    assert msg is not None
    assert str(missing) in msg
    assert "存在しません" in msg


def test_validate_lists_what_was_actually_there(tmp_path):
    """DESI と判断したのに parquet しか無い、が読み取れること。"""
    d = _parquet_folder(tmp_path)
    msg = de._validate_data_folder(str(d), "DESI", "フォルダのパス")
    assert msg is not None
    assert str(d) in msg              # どこを見たか
    assert ".parquet" in msg          # 何があったか
    assert "フォルダのパス" in msg     # なぜ DESI と思ったか


def test_export_desi_error_explains_itself(tmp_path):
    d = _parquet_folder(tmp_path)
    with pytest.raises(ValueError) as e:
        de._export_desi(str(d), OrderedDict([("Harmony", {})]))
    assert str(d) in str(e.value)
    assert ".parquet" in str(e.value)


def test_export_tims_error_explains_itself(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    with pytest.raises(ValueError) as e:
        de._export_tims(str(d), OrderedDict([("Harmony", {})]), "parquet")
    assert str(d) in str(e.value)


# ---------------------------------------------------------------------------
# list_msi_files / find_msi_txt — 拡張子の大文字小文字
# ---------------------------------------------------------------------------

def test_list_msi_files_finds_uppercase_txt(tmp_path):
    _desi_txt_folder(tmp_path, "raw", stem="SAMPLE", ext=".TXT")
    assert list_msi_files(str(tmp_path / "raw")) == ["SAMPLE"]


def test_find_msi_txt_is_case_insensitive(tmp_path):
    d = _desi_txt_folder(tmp_path, "raw", stem="SAMPLE", ext=".TXT")
    assert find_msi_txt(str(d), "SAMPLE") == d / "SAMPLE.TXT"
    assert find_msi_txt(str(d), "NOPE") is None


def test_export_desi_writes_uppercase_txt_sample(tmp_path):
    """`.TXT` が「.txt が未生成」として飛ばされないこと。"""
    d = _desi_txt_folder(tmp_path, "raw", stem="SAMPLE", ext=".TXT")
    lookups = OrderedDict([("Harmony", {("SAMPLE", 1.0, 1.0): "cluster1"})])
    out_path, filename = de._export_desi(str(d), lookups, out_dir=tmp_path / "out")
    sheets = pd.read_excel(out_path, sheet_name=None, header=None)
    assert "SAMPLE" in sheets
    assert "Skipped" not in sheets


# ---------------------------------------------------------------------------
# _infer_data_folder — 中身の無い登録済みパスが正しい兄弟を覆い隠さない
# ---------------------------------------------------------------------------

def test_infer_skips_registered_folder_without_data(tmp_path, monkeypatch):
    proj = tmp_path / "Data" / "TIMS" / "Data" / "proj"
    result = proj / "result"
    result.mkdir(parents=True)
    stale = proj / "old_raw"
    stale.mkdir()                                   # 実在するが中身が無い
    good = _parquet_folder(proj, "raw")

    monkeypatch.setattr(
        "app.services.project_manager.get_sub_project",
        lambda pid, sid: {"data_folder": str(stale)})
    monkeypatch.setattr(de, "_project_root_for", lambda p: proj)

    assert de._infer_data_folder(str(result), "p1", "s1", "TIMS") == str(good)


def test_infer_uses_registered_folder_when_it_has_data(tmp_path, monkeypatch):
    proj = tmp_path / "Data" / "TIMS" / "Data" / "proj"
    (proj / "result").mkdir(parents=True)
    good = _parquet_folder(proj, "raw")
    monkeypatch.setattr(
        "app.services.project_manager.get_sub_project",
        lambda pid, sid: {"data_folder": str(good)})
    assert de._infer_data_folder(
        str(proj / "result"), "p1", "s1", "TIMS") == str(good)


# ---------------------------------------------------------------------------
# _has_msi_files — 解析が読む集合とそろっていること
# ---------------------------------------------------------------------------

def test_has_msi_files_desi_accepts_csv_registration(tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    (d / "s1.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    assert de._has_msi_files(d, "DESI") is True


def test_has_msi_files_tims_accepts_txt(tmp_path):
    d = _desi_txt_folder(tmp_path, "raw")
    assert de._has_msi_files(d, "TIMS") is True


# ---------------------------------------------------------------------------
# 「再解析へ送る」— データ出力と同じ誤判定をしないこと（欠陥 G）
# ---------------------------------------------------------------------------

def test_send_to_reanalysis_uses_tims_template_for_parquet(tmp_path):
    """RDS が /DESI/ 配下でも、生データが parquet なら TIMS 再解析。

    こちらはエラーにならず**黙って違うフォームに転記される**ので、
    データ出力より気づきにくい。
    """
    import app.callbacks.interactive_reanalysis_bridge as br

    d = _parquet_folder(tmp_path)
    rds = tmp_path / "Data" / "DESI" / "Data" / "proj" / "out" / "seu.rds"
    rds.parent.mkdir(parents=True)
    out = br.send_to_reanalysis(1, "keep", ["3"], str(rds), "TIMS", str(d))
    desi_method, tims_method = out[3], out[4]
    assert desi_method is None
    assert tims_method == "tims_cluster_filter"


def test_send_to_reanalysis_still_picks_desi_for_txt(tmp_path):
    """ver58.1 以来の DESI 経路を壊していないこと。"""
    import app.callbacks.interactive_reanalysis_bridge as br

    d = _desi_txt_folder(tmp_path)
    rds = tmp_path / "Data" / "DESI" / "Data" / "proj" / "out" / "seu.rds"
    rds.parent.mkdir(parents=True)
    out = br.send_to_reanalysis(1, "keep", ["3"], str(rds), "", str(d))
    assert out[3] == "desi_cluster_filter"
    assert out[4] is None


# ---------------------------------------------------------------------------
# 出力手法が空のとき（欠陥 I）
# ---------------------------------------------------------------------------

def test_no_method_selected_fails_with_a_reason():
    """全部外したら「全手法を出す」のではなく理由を出して止まること。"""
    from app.services import export_progress as ep

    out = de.data_export_start(1, "/tmp/d", "TIMS", "parquet", {"Harmony": "a"},
                               "Harmony", "/tmp/r", "p1", "s1", "/tmp/a.rds",
                               None, [], False, None)
    job = ep.get_job(out[5]["job"])
    assert job["status"] == "error"
    assert "出力手法" in job["msg"]


# ---------------------------------------------------------------------------
# DESI では出力内容の設定を見せない（欠陥 D）
# ---------------------------------------------------------------------------

def test_desi_hides_both_format_and_options():
    """プロジェクト設定で明示的に DESI を選んでいる場合は隠す（従来どおり）。"""
    fmt, opts, summary = de.toggle_format_selector("DESI", None)
    assert fmt == {"display": "none"}
    assert opts == {"display": "none"}, (
        "DESI では出力内容の設定が効かない（_export_desi は options を"
        "受け取らない）のに、設定ボタンが出たままになっている")
    assert "Excel 固定" in summary


def test_tims_shows_both_and_restores_the_summary():
    fmt, opts, summary = de.toggle_format_selector("TIMS", None)
    assert fmt == {"display": "block"}
    assert opts == {"display": "block"}
    # DESI の注記が残らず、実際の設定の要約に戻ること
    assert "Excel 固定" not in summary


# ---------------------------------------------------------------------------
# link_existing が生データフォルダも更新すること（欠陥 F）
# ---------------------------------------------------------------------------

def test_link_existing_updates_data_folder(monkeypatch):
    import app.callbacks.interactive_project as ip

    seen = {}
    monkeypatch.setattr("app.services.project_manager.get_sub_project",
                        lambda pid, sid: {"id": sid})
    monkeypatch.setattr("app.services.project_manager.update_sub_project",
                        lambda pid, sid, updates: seen.update(updates))
    monkeypatch.setattr("app.services.project_manager.list_projects", lambda: [])
    ip.execute_save_as_project(
        1, "link_existing", None, None, "p1", None, None, None, None, None,
        "s1", "/res", "/new_raw")
    assert seen.get("data_folder") == "/new_raw", (
        "別の生データで解析し直して紐付けても古い data_folder が残る")


def test_link_existing_does_not_clear_data_folder_when_blank(monkeypatch):
    import app.callbacks.interactive_project as ip

    seen = {}
    monkeypatch.setattr("app.services.project_manager.get_sub_project",
                        lambda pid, sid: {"id": sid})
    monkeypatch.setattr("app.services.project_manager.update_sub_project",
                        lambda pid, sid, updates: seen.update(updates))
    monkeypatch.setattr("app.services.project_manager.list_projects", lambda: [])
    ip.execute_save_as_project(
        1, "link_existing", None, None, "p1", None, None, None, None, None,
        "s1", "/res", "")
    assert "data_folder" not in seen


# ---------------------------------------------------------------------------
# PR #170 レビュー指摘 (codex): 暫定装置でフォルダ推定を絞り込まないこと
# ---------------------------------------------------------------------------

def test_infer_returns_registered_parquet_folder_under_desi_path(
        tmp_path, monkeypatch):
    """MSI データフォルダ欄が空のときも、登録済み parquet フォルダを返すこと。

    推定は**暫定**装置（パス由来なので誤り得る）で走る。パスが /DESI/ だと
    暫定 DESI になり、登録済みの parquet フォルダを「DESI の入力が無い」と
    弾いてしまう。すると `_decide_instrument` が中身で TIMS へ訂正する前に
    「MSIデータフォルダが見つかりません」で終わる — 直したはずの経路が
    別のエラーに化けるだけになる。
    """
    proj = tmp_path / "Data" / "DESI" / "Data" / "proj"
    raw = _parquet_folder(proj, "raw")
    monkeypatch.setattr(
        "app.services.project_manager.get_sub_project",
        lambda pid, sid: {"data_folder": str(raw)})

    # 結果フォルダが失われている状況（移動・コンテナ再作成）にして、
    # 兄弟走査に救われないようにする。登録済みフォルダだけが頼り。
    got = de._infer_data_folder(str(proj / "gone"), "p1", "s1", "DESI")
    assert got == str(raw)


def test_infer_scans_siblings_for_parquet_even_when_provisional_is_desi(
        tmp_path, monkeypatch):
    """登録が無い場合の兄弟走査でも、暫定装置で取りこぼさないこと。"""
    proj = tmp_path / "Data" / "DESI" / "Data" / "proj"
    (proj / "result").mkdir(parents=True)
    raw = _parquet_folder(proj, "raw")
    monkeypatch.setattr("app.services.project_manager.get_sub_project",
                        lambda pid, sid: None)
    monkeypatch.setattr(de, "_project_root_for", lambda p: proj)

    got = de._infer_data_folder(str(proj / "result"), "p1", "s1", "DESI")
    assert got == str(raw)


def test_infer_still_prefers_the_folder_matching_the_instrument(
        tmp_path, monkeypatch):
    """暫定装置に一致するフォルダがあれば、そちらを優先すること。"""
    proj = tmp_path / "proj"
    (proj / "result").mkdir(parents=True)
    _parquet_folder(proj, "a_parquet")          # 名前順では先
    txt = _desi_txt_folder(proj, "b_txt")
    monkeypatch.setattr("app.services.project_manager.get_sub_project",
                        lambda pid, sid: None)
    monkeypatch.setattr(de, "_project_root_for", lambda p: proj)

    assert de._infer_data_folder(str(proj / "result"), "p1", "s1", "DESI") == str(txt)


# ---------------------------------------------------------------------------
# PR #170 レビュー指摘 (codex): 表示の判定も中身に合わせること
# ---------------------------------------------------------------------------

def test_controls_follow_the_resolved_instrument_not_stale_metadata(tmp_path):
    """metadata が古く "DESI" でも、中身が parquet なら操作できること。

    出力は中身で TIMS 経路へ行くのに、形式と出力内容の設定だけが
    metadata で隠れると、利用者は形式も列も選べないまま TIMS の出力を
    受け取ることになる。
    """
    d = _parquet_folder(tmp_path)
    fmt, opts, _summary = de.toggle_format_selector("DESI", None, str(d), "")
    assert fmt == {"display": "block"}
    assert opts == {"display": "block"}


def test_controls_hide_when_the_folder_contents_say_desi(tmp_path):
    """中身が DESI 固有 (.xlsx) なら断定できるので隠すこと。"""
    d = tmp_path / "raw"
    d.mkdir()
    pd.DataFrame({"a": [1]}).to_excel(d / "s1.xlsx", index=False)
    fmt, opts, summary = de.toggle_format_selector("", None, str(d), "")
    assert fmt == {"display": "none"}
    assert opts == {"display": "none"}
    assert "Excel 固定" in summary


# ---------------------------------------------------------------------------
# ver62.3: 推測で機能を消さない（利用者からの報告）
# ---------------------------------------------------------------------------

def test_controls_stay_visible_when_desi_is_only_a_path_guess(tmp_path):
    """パスに DESI があるだけでは隠さないこと。

    ver62.2 はここで隠していたため、`Data/DESI/…` に置かれた TIMS
    プロジェクトで**出力形式と「出力内容の設定」が画面から消え**、
    利用者が形式も列も選べなくなった。判定を外したときの損害が
    「効かない設定が見える」と「作業が止まる」で釣り合っていない。
    """
    d = _desi_txt_folder(tmp_path)          # .txt のみ = 中身では断定できない
    assert de._decide_instrument("", str(d), "")[0] == "DESI"   # 判定自体は DESI
    fmt, opts, summary = de.toggle_format_selector("", None, str(d), "")
    assert fmt == {"display": "block"}
    assert opts == {"display": "block"}
    assert "Excel 固定" not in summary


def test_controls_stay_visible_when_nothing_identifies_the_instrument(tmp_path):
    """根拠が「既定」しかないときも隠さないこと。"""
    fmt, opts, _summary = de.toggle_format_selector("", None, "", "")
    assert fmt == {"display": "block"}
    assert opts == {"display": "block"}


def test_desi_export_says_the_settings_were_not_applied(tmp_path, monkeypatch):
    """隠さない代わりに、実際に無視したときは結果にそう書くこと。"""
    from app.callbacks.interactive_callbacks import (
        _interactive_data, _set_active_key)

    proj = tmp_path / "proj"
    result = proj / "result"
    result.mkdir(parents=True)
    raw = _desi_txt_folder(proj, "raw")
    rds = str(result / "seu.rds")
    monkeypatch.setattr("app.services.project_manager.get_sub_project",
                        lambda pid, sid: {"data_folder": str(raw)})
    try:
        _set_active_key(rds)
        _interactive_data["plot_data"] = pd.DataFrame({
            "SpatialX": [1.0, 2.0], "SpatialY": [1.0, 2.0],
            "Sample": ["s1", "s1"], "Cluster": [1, 2]})
        _interactive_data["method"] = "Harmony"
        _, filename, msg = de._do_export(
            str(raw), "DESI", "parquet", {"Harmony": rds}, "Harmony",
            str(result), "p1", "s1", rds, out_dir=tmp_path / "out")
    finally:
        _set_active_key(None)

    assert filename == "UMAP_cluster_DESI.xlsx", msg
    assert "適用していません" in msg


def test_tims_export_does_not_add_the_desi_note(tmp_path, monkeypatch):
    from app.callbacks.interactive_callbacks import (
        _interactive_data, _set_active_key)

    proj = tmp_path / "proj"
    result = proj / "result"
    result.mkdir(parents=True)
    raw = _parquet_folder(proj, "raw")
    rds = str(result / "seu.rds")
    monkeypatch.setattr("app.services.project_manager.get_sub_project",
                        lambda pid, sid: {"data_folder": str(raw)})
    try:
        _set_active_key(rds)
        _interactive_data["plot_data"] = pd.DataFrame({
            "SpatialX": [1.0, 2.0], "SpatialY": [1.0, 2.0],
            "Sample": ["s1", "s1"], "Cluster": [1, 2]})
        _interactive_data["method"] = "Harmony"
        _, filename, msg = de._do_export(
            str(raw), "TIMS", "parquet", {"Harmony": rds}, "Harmony",
            str(result), "p1", "s1", rds, out_dir=tmp_path / "out")
    finally:
        _set_active_key(None)

    assert filename == "UMAP_cluster_TIMS.parquet", msg
    assert "適用していません" not in msg


def test_export_of_parquet_under_desi_path_produces_a_tims_file(
        tmp_path, monkeypatch):
    """報告された不具合そのものを、出力ファイルまで通して確認する。

    MSI データフォルダ欄が空・登録済みは `Data/DESI/…` の parquet フォルダ・
    metadata は "TIMS"。従来はパス判定が metadata を上書きして DESI 経路へ入り、
    `list_msi_files` が parquet を拾わないため
    「DESI .txt ファイルが見つかりません」で終わっていた。
    """
    from app.callbacks.interactive_callbacks import (
        _interactive_data, _set_active_key)

    proj = tmp_path / "Data" / "DESI" / "Data" / "proj"
    result = proj / "result"
    result.mkdir(parents=True)
    raw = _parquet_folder(proj, "raw")
    rds = str(result / "seu.rds")

    monkeypatch.setattr("app.services.project_manager.get_sub_project",
                        lambda pid, sid: {"data_folder": str(raw)})
    try:
        _set_active_key(rds)
        _interactive_data["plot_data"] = pd.DataFrame({
            "SpatialX": [1.0, 2.0], "SpatialY": [1.0, 2.0],
            "Sample": ["s1", "s1"], "Cluster": [1, 2]})
        _interactive_data["method"] = "Harmony"

        out_path, filename, msg = de._do_export(
            "",                          # MSI データフォルダ欄は空
            "TIMS",                      # metadata（パス判定に負けていた）
            "parquet", {"Harmony": rds}, "Harmony",
            str(result), "p1", "s1", rds,
            out_dir=tmp_path / "out")
    finally:
        _set_active_key(None)

    assert filename == "UMAP_cluster_TIMS.parquet", msg
    df = pd.read_parquet(out_path)
    assert "UMAP cluster" in df.columns
    assert (df["UMAP cluster"].astype(str) != "").all(), (
        f"クラスタが 1 件も突合していない: {df['UMAP cluster'].tolist()}")
