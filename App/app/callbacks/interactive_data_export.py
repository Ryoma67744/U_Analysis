"""データ出力コールバック — 元データに UMAP cluster 列を追加してエクスポート。

DESI: .txt → Excel（サンプル別シート）
TIMS: Parquet/CSV → 選択形式（Excel / CSV / Parquet）

複数手法（Harmony / RPCA 等）が存在する場合は、全手法のクラスター情報を
1つのファイルにまとめて出力する（Method 列 + UMAP cluster 列）。
"""

import contextlib
import contextvars
import logging
import os
import re
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path

import pandas as pd
from dash import (
    Input, Output, State, callback, clientside_callback, html, no_update,
)
from dash.exceptions import PreventUpdate

from app.callbacks.interactive_callbacks import _bridge, _interactive_data
from app.utils.color_utils import cluster_display_name
from app.utils.label_persistence import load_cluster_name_map
from app.services import hne_overlay as hn
from app.services import hne_persistence as hp
from app.services.data_manager import (
    _PARQUET_EXTS,
    build_tims_input_paths,
    find_msi_txt,
    has_msi_data,
    list_msi_files,
)
from app.services.desi_header import is_data_line as _is_data_line
from app.services import export_aggregate as _agg
from app.services import export_mzlist as _mzlist
from app.services import export_options as _eo
from app.services.export_transform import (
    append_cluster_region_columns as _append_cluster_region_columns,
    plan_exclusions as _plan_exclusions,
    summarize_coverage as _summarize_coverage,
    summarize_exclusions as _summarize_exclusions,
    unanalyzed_stems as _unanalyzed_stems,
)

logger = logging.getLogger(__name__)
logger.info("[DataExport] モジュール読み込み完了 (v2)")


# 進捗ジョブレジストリ（Dash 非依存の services モジュールへ分離＝単体テスト可）。
from app.services.export_progress import (  # noqa: E402
    new_job as _new_job,
    update_job as _update_job,
    finish_job as _finish_job,
    fail_job as _fail_job,
    get_job as _get_job,
    sweep_old_files as _sweep_old_files,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_instrument(ms_instrument, *paths) -> str:
    """エクスポート経路を決める ms_instrument を確定する。

    サブプロジェクト metadata の ms_instrument は未設定時に "TIMS" へフォールバック
    するため、DESIプロジェクトが誤って TIMS 経路に入ることがある。そこで明示 "DESI" を
    優先しつつ、未指定/曖昧な場合はパス規約 (Data/DESI/Data・Data/TIMS/Data) から判定する。

    Returns: "DESI" または "TIMS"
    """
    mi = (ms_instrument or "").strip().upper()
    if mi == "DESI":
        return "DESI"
    joined = "/".join(str(p) for p in paths if p).replace("\\", "/")
    if "/DESI/" in joined and "/TIMS/" not in joined:
        return "DESI"
    if "/TIMS/" in joined:
        return "TIMS"
    return mi or "TIMS"


# 装置を**断定できる**拡張子。どちらの経路でも使われる .txt / .csv / .tsv は
# ここに入れない。断定できないものを断定すると、逆向きの誤判定を静かに固定する。
_DESI_ONLY_EXTS = {".xlsx", ".xls"}     # TIMS の入力候補に Excel は無い


def _instrument_from_folder(data_folder) -> "str | None":
    """データフォルダの**中身**から装置を判定する。決められなければ None。

    ★ ver62.2: 従来は `_resolve_instrument` のパス判定だけで経路を決めていた。
      しかしパスは置き場所でしかなく、metadata の "TIMS" も
      「利用者が選んだ」のか「保存モーダルの既定 (`sap_ms_instrument` の
      value="TIMS") のまま」なのか区別が付かない。どちらも根拠として弱い。

      実際に起きた不具合: parquet を `Data/DESI/Data/…` に置いた TIMS
      プロジェクトが DESI 経路 (`_export_desi`) へ入り、`list_msi_files` は
      parquet を 1 件も拾わないため
      **「DESI .txt ファイルが見つかりません」で出力そのものができなかった**。
      metadata に "TIMS" と書いてあっても、パス判定がそれを上書きしていた。

      中身は食い違いようがないので中身を信じる。ただし断定できるものだけ:
        - parquet がある   → TIMS（DESI に parquet 入力は存在しない）
        - .xlsx/.xls がある → DESI（TIMS の入力候補に Excel は無い）
      `.txt` / `.csv` / `.tsv` は両者で使われるので None を返し、従来の
      metadata → パス判定に委ねる。
    """
    if not data_folder:
        return None
    folder = Path(data_folder)
    if not folder.is_dir():
        return None
    try:
        # サイドカー除外と parquet 優先の規則は data_manager を唯一の出典にする。
        # ここで書き直すと、解析側が見ているサンプル集合と食い違う。
        if any(Path(fp).suffix.lower() in _PARQUET_EXTS
               for fp in build_tims_input_paths(str(folder))):
            return "TIMS"
        if any(f.is_file() and f.suffix.lower() in _DESI_ONLY_EXTS
               for f in folder.iterdir()):
            return "DESI"
    except OSError as e:  # noqa: BLE001 — 読めないフォルダは「判定不能」に倒す
        logger.debug("[DataExport] 中身による装置判定を断念: %s (%s)", data_folder, e)
    return None


def _decide_instrument(ms_instrument, data_folder, result_folder) -> tuple:
    """`(装置, 判断根拠)` を返す。根拠はログとエラーメッセージに出す。

    ★ ver62.2: 根拠を返すのは、判定を誤ったときに利用者が気づけるようにするため。
      従来のエラーは「DESI .txt ファイルが見つかりません」だけで、
      **なぜ DESI だと思ったのか**がどこにも出なかった。
    """
    path_based = _resolve_instrument(ms_instrument, data_folder, result_folder)
    by_content = _instrument_from_folder(data_folder)
    if by_content:
        if by_content != path_based:
            logger.warning(
                "[DataExport] 装置判定を %s から %s へ訂正しました"
                "（パスや設定より中身を優先）: data_folder=%s",
                path_based, by_content, data_folder)
        return by_content, "データフォルダの中身"

    if (ms_instrument or "").strip().upper() == "DESI":
        return path_based, "プロジェクト設定の ms_instrument"
    joined = "/".join(str(p) for p in (data_folder, result_folder) if p
                      ).replace("\\", "/")
    if "/DESI/" in joined or "/TIMS/" in joined:
        return path_based, "フォルダのパス"
    return path_based, "既定"


# `_decide_instrument` の根拠のうち、**推測ではない**もの。
# ★ ver62.3: 画面から機能を消してよいのはこの 2 つのときだけ。
#   「フォルダのパス」「既定」は当て推量で、外すと利用者が形式も列も
#   選べなくなる（ver62.2 で実際に起きた）。
_DESI_HIDE_REASONS = ("データフォルダの中身", "プロジェクト設定の ms_instrument")


def _describe_folder_contents(folder: Path, limit: int = 6) -> str:
    """フォルダ直下にあるものを「拡張子 × 件数」で要約する。

    ★ ver62.2: 「見つかりません」だけでは、フォルダを間違えたのか・中身が
      消えたのか・装置判定を誤ったのかが区別できない。実際に何があったかを見せる。
    """
    counts: dict = {}
    n_dirs = 0
    try:
        for f in folder.iterdir():
            if f.is_dir():
                n_dirs += 1
                continue
            counts[f.suffix.lower() or "(拡張子なし)"] = (
                counts.get(f.suffix.lower() or "(拡張子なし)", 0) + 1)
    except OSError:
        return "フォルダを読めませんでした"
    parts = [f"{ext} {n} 件" for ext, n
             in sorted(counts.items(), key=lambda kv: -kv[1])[:limit]]
    if n_dirs:
        parts.append(f"サブフォルダ {n_dirs} 個")
    return "・".join(parts) if parts else "空"


def _no_input_message(data_folder, instrument: str, reason: str = "") -> str:
    """入力ファイルが 1 件も無いときの説明文を作る（DESI / TIMS 共通）。

    ★ ver62.2: 従来は `"DESI .txt ファイルが見つかりません"` /
      `"TIMS 入力ファイルが見つかりません"` の一行だけで、
      **どのフォルダを見たのか・そこに何があったのか・なぜその装置だと
      判断したのか**をどこにも出していなかった。同じ文言が
      「フォルダが消えた」「パスが 1 階層ずれている」「装置判定を誤った」の
      すべてで出るため、利用者もログも原因にたどり着けなかった。
      改行は画面（div）で潰れるので 1 行に収める。
    """
    inst = (instrument or "").upper() or "MSI"
    want = ".txt / .csv / .xlsx" if inst == "DESI" else ".parquet / .csv / .tsv"
    head = f"{inst} 用の入力ファイル ({want}) が見つかりません。"
    if not data_folder:
        return head + "MSIデータフォルダが指定されていません。"
    folder = Path(data_folder)
    if not folder.exists():
        return (head + f"フォルダが存在しません: {folder}。"
                "移動・削除されたか、コンテナから見えないパスの可能性があります。")
    if not folder.is_dir():
        return head + f"フォルダではありません: {folder}。"
    msg = (head + f"探した場所: {folder}"
           f"（{_describe_folder_contents(folder)}）。")
    if reason:
        msg += f"{inst} と判断した根拠: {reason}。"
    # ★ ver62.7: 「図は出ているのに、なぜフォルダを直せと言われるのか」が
    #   分からない、という報告があった。画面の図は**結果フォルダの RDS だけ**で
    #   描いていて（`interactive_callbacks` の `_bridge.extract_data`）、
    #   この生データフォルダを一切見ない。一方データ出力はクラスタ番号を
    #   生データの強度に結合し直すので必須になる。この非対称が画面のどこにも
    #   書かれておらず、利用者は「表示が正常＝データは揃っている」と読む。
    #   直す場所も併せて名指しする（ver62.7 で欄を表示するようにした）。
    return msg + ("なお画面の図は結果フォルダの解析結果 (RDS) だけで描いているため、"
                  "図が正常に見えていてもこの出力だけ失敗します。"
                  "インタラクティブ画面の「MSIデータフォルダ」欄"
                  "（1 階層ずれていないか）と、"
                  "サブプロジェクトの「装置」設定を確認してください。")


def _validate_data_folder(data_folder, instrument: str, reason: str = "") -> "str | None":
    """出力に入る前にデータフォルダを検査する。問題があれば説明文、無ければ None。

    ★ ver62.2: 従来 `_do_export` は `data_folder` の**存在も中身も見ずに**
      DESI/TIMS の分岐へ入っていた（真偽値チェックのみ）。存在しないパスでも
      分岐の奥まで進み、そこで出る一行のエラーだけが利用者に見えていた。
    """
    folder = Path(data_folder) if data_folder else None
    if folder is None or not folder.is_dir():
        return _no_input_message(data_folder, instrument, reason)
    if (instrument or "").upper() == "DESI":
        found = list_msi_files(str(folder))
    else:
        found = build_tims_input_paths(str(folder))
    if not found:
        return _no_input_message(data_folder, instrument, reason)
    return None


def _build_cluster_lookup(plot_data: pd.DataFrame, cluster_name_map: dict | None = None) -> dict:
    """plot_data 全体から {(sample, round(x,4), round(y,4)): クラスタ表示名} dict を構築。

    cluster_name_map（クラスタ名変更）があれば、番号ではなく変更名を値にする。
    """
    if plot_data is None or plot_data.empty:
        return {}
    lookup = {}
    for _, row in plot_data.iterrows():
        sx = row.get("SpatialX")
        sy = row.get("SpatialY")
        sample = str(row.get("Sample", ""))
        cluster = row.get("Cluster", "")
        if pd.notna(sx) and pd.notna(sy):
            key = (sample, round(float(sx), 4), round(float(sy), 4))
            lookup[key] = cluster_display_name(cluster, cluster_name_map)
    return lookup


def _build_extra_lookups(plot_data: pd.DataFrame, options) -> dict:
    """plot_data 由来の追加列を `{列名: {(sample, rx, ry): 値}}` にする（★ ver61.0）。

    対象は UMAP 座標 (UMAP_1 / UMAP_2) と品質指標 (TotalCount / nFeature)。
    いずれも `extract_seurat_data.R` が plot_data に入れているのに、これまで
    データ出力には含まれていなかった。列選択を作るなら同時に出せるのが自然。

    キーの作り方は `_build_cluster_lookup` と**同一**（元 SpatialX/Y を Python の
    round で 4 桁）。ここがずれると同じ行に別々の値が乗る。

    `_build_cluster_lookup` は iterrows を使っているが、ここでは使わない。
    20 万行 × 複数列で iterrows は桁違いに遅く、`append_cluster_region_columns` が
    「iterrows 撤廃＝軽い」としているのと同じ理由。
    """
    out: dict = {}
    if plot_data is None or plot_data.empty:
        return out
    if not all(c in plot_data.columns for c in ("SpatialX", "SpatialY", "Sample")):
        return out

    import numpy as np

    need: list = []
    if _eo.wants(options, "umap"):
        need += [c for c in _eo.UMAP_COLUMNS if c in plot_data.columns]
    if _eo.wants(options, "quality"):
        need += [c for c in _eo.QUALITY_COLUMNS if c in plot_data.columns]
    if not need:
        return out

    sx = pd.to_numeric(plot_data["SpatialX"], errors="coerce").to_numpy(dtype=float)
    sy = pd.to_numeric(plot_data["SpatialY"], errors="coerce").to_numpy(dtype=float)
    samples = plot_data["Sample"].astype(str).to_numpy()
    ok = ~(np.isnan(sx) | np.isnan(sy))
    keys = [(s, round(float(x), 4), round(float(y), 4))
            for s, x, y in zip(samples[ok], sx[ok], sy[ok])]
    for col in need:
        out[col] = dict(zip(keys, plot_data[col].to_numpy()[ok]))
    return out


def _build_region_lookup(plot_data: pd.DataFrame, rds_path):
    """plot_data 全体から {(sample, round(x,4), round(y,4)): 領域名(ROI)} を構築。

    各切片(sample)の H&E オーバーレイ保存状態（hne_overlay_state.json）から ROI を
    割当てる（`hn.regions_from_overlay`）。overlay 未設定／ROI 未割当の spot は
    キーを作らない（出力では空欄になる）。キーは `_build_cluster_lookup` と同方式
    （元 SpatialX/Y を 4 桁丸め）で、クラスタ列と同じ行に突合される。

    ★ ver51.9 / B-10: **ROI を 1 つも割り当てられなかったときは None を返す**。
      従来は常に dict を返していたため、呼び出し側の
      `add_region = region_lookup is not None` が必ず True になり、
      H&E を一度も設定していないプロジェクトでも **常に空の「領域名」列** が付いた。
      利用者から見ると「ROI 機能を使っていない」と「どの ROI にも入らなかった」の
      区別が付かない（後者は解析の見落としを意味する）。
    """
    lookup: dict = {}
    failed: list = []
    if (plot_data is None or not rds_path
            or "SpatialX" not in plot_data.columns
            or "SpatialY" not in plot_data.columns
            or "Sample" not in plot_data.columns):
        return None, failed
    for sample in plot_data["Sample"].dropna().astype(str).unique():
        sub = plot_data[plot_data["Sample"].astype(str) == sample]
        if sub.empty:
            continue
        try:
            entry = hp.load_hne_sample(rds_path, sample)
            region = hn.regions_from_overlay(sub, entry)
        except Exception as e:  # noqa: BLE001
            # ★ ver52.3: 従来はログだけで飛ばしていた。そのスライスだけ
            #   「領域名」が空欄になり、利用者には
            #   **「どの ROI にも入らなかった」（＝実データ上の所見）**
            #   と読める。全サンプルで失敗すると列ごと消えて
            #   「ROI 未使用」と区別できない。呼び出し側へ返して報告させる。
            logger.warning("[DataExport] %s: 領域割当に失敗: %s", sample, e)
            failed.append(str(sample))
            continue
        sx = pd.to_numeric(sub["SpatialX"], errors="coerce").to_numpy(float)
        sy = pd.to_numeric(sub["SpatialY"], errors="coerce").to_numpy(float)
        for x, y, r in zip(sx, sy, region.to_numpy()):
            if r is None or pd.isna(x) or pd.isna(y):
                continue
            lookup[(sample, round(float(x), 4), round(float(y), 4))] = str(r)
    # ROI が 1 つも取れなければ「ROI 未使用」。空の列を足さない。
    # ★ ver52.3: 戻り値を (lookup, 失敗したサンプル名) に変えた。
    #   失敗を呼び出し側へ伝えないと「ROI 未使用」と「割当に失敗」を
    #   利用者が区別できない（後者は解析の見落としを意味する）。
    return (lookup or None), failed


def _safe_prefix(name: str) -> str:
    """R の safe_prefix 変換を再現: [^A-Za-z0-9_-] → '_'"""
    return re.sub(r"[^A-Za-z0-9_\-]", "_", name)


def _match_sample_name(file_stem: str, sample_names: list[str]) -> str | None:
    """ファイル名ステムと plot_data の Sample 名をマッチング。

    1. 完全一致
    2. safe_prefix 変換後一致
    3. 部分一致（file_stem が Sample に含まれる or 逆）— **一意のときだけ**

    ★ ver51.8: 部分一致で「最初に見つかったもの」を返していた。
      サンプルが ["brain_A", "brain_B"] で生ファイルが brain.txt のとき、
      並び順だけの理由で brain_A が選ばれる。返り値は
      `key = (matched_sample, x, y)` の第 1 要素として使われ、TIMS のように
      サンプル間で座標グリッドが共通だと **別サンプルのクラスタ名・ROI 名が
      エクスポートに書き出される**（行ごとに、無言で）。
      曖昧なら選ばない = None を返し、クラスタ列を空にする方が安全。
    """
    # 完全一致
    if file_stem in sample_names:
        return file_stem
    # safe_prefix 変換後一致
    safe = _safe_prefix(file_stem)
    safe_hits = [sn for sn in sample_names if _safe_prefix(sn) == safe]
    if len(safe_hits) == 1:
        return safe_hits[0]
    if len(safe_hits) > 1:
        logger.warning(
            "サンプル名の safe_prefix 一致が曖昧です (%s -> %s)。対応付けません。",
            file_stem, safe_hits)
        return None
    # 部分一致（候補が 1 つに定まるときだけ採用）
    partial = [sn for sn in sample_names if file_stem in sn or sn in file_stem]
    if len(partial) == 1:
        return partial[0]
    if len(partial) > 1:
        logger.warning(
            "サンプル名の部分一致が曖昧です (%s -> %s)。対応付けません。",
            file_stem, partial)
    return None


# 復旧して出力したときに完了メッセージへ添える一文を作る。
# `_resolve_data_folder` が返す note を見て、登録値をそのまま使ったときは何も出さない。
#
# ★ ver62.8: 復旧した値を台帳へ書き戻さないと決めたので、**画面の欄には壊れた値が
#   出たまま、出力は別のフォルダを使う**という食い違いが残る。黙って解決すると
#   「表示と実際が違う」ことに誰も気づけない（このリポジトリが繰り返し戒めている
#   静かな不一致）。台帳を自動で書き換える代わりに、実際にそうしたときに事実を述べる。
#   ver62.3 が DESI 経路で採ったのと同じ方針。
_RECOVERED_NOTES = ("解析記録", "推定")


def _folder_note_message(folder, note: str) -> str:
    """復旧して出力したときだけ、使ったフォルダを述べる一文を返す。"""
    if not note or not any(note.startswith(k) for k in _RECOVERED_NOTES):
        return ""
    src = "解析記録" if note.startswith("解析記録") else "結果フォルダ周辺の走査"
    return (f"登録された MSIデータフォルダではなく{src}のフォルダを使いました: "
            f"{folder}")


def _resolve_data_folder(data_folder, result_folder, project_id, sub_project_id,
                         ms_instrument, tag: str = "DataExport"):
    """出力に使う MSI データフォルダを確定する。GUI / API の両経路から呼ぶ。

    Returns: (folder|None, note) — note は採用理由（ログとテスト用の短い文字列）。

    ★ ver62.7: 従来は両経路とも `if not data_folder:` のときだけ推定していた。
      つまり**登録値が入ってさえいれば中身を見ずに使って**いた。
      ところが `_infer_data_folder` の branch (a) には
      「登録済み data_folder に生データが無ければ (b) の走査へ落とす」という
      判断が既に書いてある（ver62.2）。呼び出し側が短絡するのでそこへ到達せず、
      **書いてあるのに働かない**状態だった。

      実害: 台帳の `data_folder` が壊れると（ver62.4 で塞いだ経路で実際に起きた）、
      結果フォルダの隣に生データがあっても自力で復帰できない。
      登録値の中身を見て、空振りなら推定へ落とす。

      推定も空振りしたときは**登録値をそのまま返す**。None にすると
      「MSIデータフォルダが見つかりません」という一般論になってしまい、
      `_no_input_message` が「どのフォルダを見て何があったか」を出せなくなる。
      診断に効くのは後者なので、登録値を残す。

    ★ ver62.8: 登録値が使えないとき、兄弟走査（推測）の**前に解析記録**を見る。

      ver62.7 の兄弟走査は「結果フォルダの隣に生データがある」レイアウトでしか
      効かない。ところが既定の出力先は `Data/Other/output/Analysis_*` で、
      その隣には他の解析結果しか無い。**既定の運用ほど自力復帰できない**という
      逆立ちした状態だった。

      一方、解析は自分が読んだフォルダを結果フォルダに書き残している
      （`provenance.recorded_data_folder`）。推測ではなく記録された事実なので、
      レイアウトに関係なく引ける。記録 > 推測 の順に並べる。

      登録値より後にするのは、利用者が明示的に入れた値を尊重するため
      （データを意図的に移した場合に勝手に巻き戻さない）。
      記録も空振りなら兄弟走査へ落ちる（データを改名・移動した場合）。

    二重に持たず 1 箇所に置くのは、同じ判断を 2 経路に書くと必ず片方だけ直るため
    （ver62.2 の `_resolve_instrument` で実際にそうなりかけた）。
    """
    def _from_record():
        """解析記録に書かれたフォルダ。生データが実在するときだけ返す。"""
        from app.services.provenance import recorded_data_folder
        rec = recorded_data_folder(result_folder)
        if rec and _has_any_msi_files(Path(rec)):
            return rec
        if rec:
            logger.warning(
                "[%s] 解析記録の data_folder にも生データがありません: %s", tag, rec)
        return None

    if not data_folder:
        # ★ ver62.8: 未指定は実機の監査で最多（15 件中 11 件）だった。
        #   ここでも記録を先に見る。
        recorded = _from_record()
        if recorded:
            logger.info("[%s] data_folder 未指定のため解析記録から補完: %s",
                        tag, recorded)
            return recorded, "解析記録(登録値なし)"
        inferred = _infer_data_folder(
            result_folder, project_id, sub_project_id, ms_instrument)
        logger.info("[%s] data_folder 未指定のため自動推定: %s", tag, inferred)
        return inferred, ("推定" if inferred else "見つからず")

    if _has_any_msi_files(Path(data_folder)):
        return data_folder, "登録値"

    # ★ ver62.8: 推測（兄弟走査）より先に、記録された事実を見る。
    recorded = _from_record()
    if recorded and str(recorded) != str(data_folder):
        logger.warning(
            "[%s] 登録された data_folder に生データが無いため解析記録の値を使います: "
            "%s → %s", tag, data_folder, recorded)
        return recorded, "解析記録(登録値に生データ無し)"

    inferred = _infer_data_folder(
        result_folder, project_id, sub_project_id, ms_instrument)
    if inferred and str(inferred) != str(data_folder):
        logger.warning(
            "[%s] 登録された data_folder に生データが無いため推定へ切り替えます: "
            "%s → %s", tag, data_folder, inferred)
        return inferred, "推定(登録値に生データ無し)"

    logger.warning(
        "[%s] 登録された data_folder に生データが無く、推定でも見つかりません: %s",
        tag, data_folder)
    return data_folder, "登録値(生データ無し)"


def _has_msi_files(folder: Path, ms_instrument: str | None) -> bool:
    """指定フォルダに MSI データファイルが存在するか判定。

    ★ ver62.2: 判定の出典を `data_manager` の 1 本にそろえた。従来は
      - DESI: `glob("*.txt")` だけ → `.csv` / `.xlsx` で登録した DESI
        （解析時に `.txt` へ変換される運用）を**見落とす**
      - TIMS: ext 集合に `.txt` が無く `_filter_tims_candidates`（.csv/.tsv/.txt を
        受ける）と食い違う
      と、実際に解析が読むファイル集合とずれていた。ずれたぶんだけ
      `_infer_data_folder` が「データフォルダが見つかりません」を誤って返す。
    """
    if not folder.is_dir():
        return False
    if (ms_instrument or "").upper() == "DESI":
        return bool(list_msi_files(str(folder)))
    return bool(build_tims_input_paths(str(folder)))


def _has_any_msi_files(folder: Path) -> bool:
    """装置を問わず MSI 入力を持つか。

    ★ ver62.2: フォルダの推定は**暫定**装置で走る（パス由来なので誤り得る —
      それがこの修正で直している不具合そのもの）。暫定装置で絞り込むと、
      `Data/DESI/…` に置かれた parquet フォルダを「DESI の入力が無い」と弾き、
      `_decide_instrument` が中身で訂正する前に「データフォルダが
      見つかりません」で終わる。装置は後段で確定するので、推定の段階では
      「生データがあるか」だけを見ればよい。

    ★ ver62.4: 実体は `data_manager.has_msi_data` へ移した。同じ判定を
      サービス層 (`analysis_finalizer`) からも使う必要が出たが、この
      モジュールは dash に依存していて import できないため。判定が 2 つに
      分かれると、保存する側と読む側で「生データがある」の意味がずれる。
    """
    return has_msi_data(folder)


def _is_within(path: Path, root: Path) -> bool:
    """path が root と等しい、または root の配下にあるか。"""
    try:
        p = path.resolve()
        r = root.resolve()
    except Exception:
        p, r = path, root
    return p == r or r in p.parents


def _project_root_for(result_path: Path):
    """result_path が属する『プロジェクトルート』(データ/出力ルート直下のディレクトリ)を返す。

    別プロジェクト混入を防ぐため、データフォルダ推定の走査をこのルート配下に限定する用途。
    既知のデータ/出力ルート配下でない場合は None。
    """
    try:
        from app.config import (
            DESI_DATA_CANDIDATES, TIMS_DATA_CANDIDATES, OUTPUT_DATA_CANDIDATES,
        )
        roots = (list(DESI_DATA_CANDIDATES) + list(TIMS_DATA_CANDIDATES)
                 + list(OUTPUT_DATA_CANDIDATES))
    except Exception:
        roots = []
    try:
        rp = result_path.resolve()
    except Exception:
        rp = result_path
    for root in roots:
        try:
            root_r = Path(root).resolve()
        except Exception:
            continue
        if root_r in rp.parents:
            rel = rp.relative_to(root_r)
            if rel.parts:
                return root_r / rel.parts[0]
    return None


def _infer_data_folder(
    result_folder: str | None,
    project_id: str | None,
    sub_project_id: str | None,
    ms_instrument: str | None,
) -> str | None:
    """MSI データフォルダを自動推定する。

    推定順: (a) サブプロジェクトメタデータ → (b) 結果フォルダ兄弟ディレクトリスキャン
    """
    # (a) サブプロジェクトメタデータから取得
    if project_id and sub_project_id:
        try:
            from app.services.project_manager import get_sub_project

            sub = get_sub_project(project_id, sub_project_id)
            if sub and sub.get("data_folder"):
                candidate = Path(sub["data_folder"])
                # ★ ver62.2: 従来は `is_dir()` だけで返していた。中身を見る (b) の
                #   走査と非対称で、**生データが 1 つも無い古いパスが登録に残って
                #   いると、正しい兄弟フォルダを覆い隠して**しまう。
                #   （フォルダごと移した / 別データで解析し直した場合に起きる。）
                #   中身が無ければ握り潰さず (b) の走査へ落とす。
                # 利用者が明示的に登録したフォルダなので、装置では絞らない
                # （暫定装置は誤り得る。`_has_any_msi_files` 参照）。
                if candidate.is_dir():
                    if _has_any_msi_files(candidate):
                        return str(candidate)
                    logger.warning(
                        "[DataExport] 登録済み data_folder に生データが無いため"
                        "推定へ切り替えます: %s", candidate)
        except Exception:
            pass

    # (b) 結果フォルダ配下のスキャン（当該プロジェクト内に限定）。
    # 別プロジェクト混入を防ぐため、全プロジェクト共通のデータルート
    # (例: Data/DESI/Data) は走査せず、プロジェクトルート配下のみを探索する。
    if not result_folder:
        return None

    result_path = Path(result_folder)
    if not result_path.is_dir():
        return None

    _skip = {"RDS_Files", "log", "__pycache__", ".git"}
    project_root = _project_root_for(result_path)

    search_roots = [result_path.parent]
    if project_root and project_root.is_dir() and project_root != result_path.parent:
        search_roots.append(project_root)

    # ★ ver62.2: 走査は 2 周する。1 周目は暫定装置に一致するフォルダ（従来どおり。
    #   DESI と TIMS の生データが同居していても取り違えない）、2 周目は装置を問わず
    #   生データを持つフォルダ。1 周目で見つからないことを「生データが無い」と
    #   決めつけないため（暫定装置はパス由来で誤り得る）。どちらで見つけても、
    #   装置は後段の `_decide_instrument` が中身で確定する。
    for accepts in (lambda f: _has_msi_files(f, ms_instrument), _has_any_msi_files):
        for root in search_roots:
            # プロジェクトルートが判明している場合、その配下以外は走査しない
            if project_root is not None and not _is_within(root, project_root):
                continue
            # 生データが「データセットフォルダ直下」にあるケース（結果フォルダの親に
            # .txt 等を直接置く運用）。サブフォルダだけでなくルート自身も確認する。
            if root != result_path and accepts(root):
                return str(root)
            for child in sorted(root.iterdir()):
                if not child.is_dir():
                    continue
                if child == result_path or child.name in _skip:
                    continue
                # 別プロジェクトのディレクトリは除外
                if project_root is not None and not _is_within(child, project_root):
                    continue
                if accepts(child):
                    return str(child)

    return None


def ensure_sub_project_data_folder(project_id, sub_id, result_folder, ms_instrument):
    """サブプロジェクトの data_folder が空なら、プロジェクト内限定推定で解決して保存する。

    生データ登録パスが空欄のサブプロジェクト（推定フォールバックに落ちる）を自己修復し、
    以後の出力が推定に頼らず確実にそのフォルダを使えるようにする。
    Returns: 解決/既存の data_folder (str) または None。
    """
    if not project_id or not sub_id:
        return None
    try:
        from app.services.project_manager import get_sub_project, update_sub_project
        sub = get_sub_project(project_id, sub_id)
        if not sub:
            return None
        existing = sub.get("data_folder")
        if existing:
            return existing  # 既に設定済みなら触らない
        inst = _resolve_instrument(ms_instrument, result_folder)
        resolved = _infer_data_folder(result_folder, project_id, sub_id, inst)
        if resolved:
            update_sub_project(project_id, sub_id, {"data_folder": resolved})
        return resolved
    except Exception:
        logger.exception("[DataExport] data_folder バックフィルに失敗")
        return None


def _build_all_method_lookups(
    rds_map: dict | None,
    current_method: str | None,
    cluster_name_map: dict | None = None,
    selected_methods: list | None = None,
    progress_cb=None,
    base: int = 0,
    span: int = 0,
) -> OrderedDict:
    """選択手法のクラスタールックアップを構築。

    selected_methods: 出力対象の手法名リスト（None/空 → rds_map の全手法）。
    現在の手法は ``_interactive_data["plot_data"]`` を再利用し、それ以外は
    ``_bridge.extract_data()`` で動的ロードする。派生 PCA は Harmony から遅延生成。
    Returns:
        OrderedDict {method_name: cluster_lookup_dict}
    """
    method_lookups: OrderedDict[str, dict] = OrderedDict()
    full_map = rds_map if isinstance(rds_map, dict) else {}

    # 選択手法でフィルタ（None/空 → 全手法）
    sel = set(str(m) for m in (selected_methods or []))
    rmap = {m: p for m, p in full_map.items() if (not sel or m in sel)}

    if not rmap:
        # rds_map 無し → 現在の plot_data のみ
        plot_data = _interactive_data.get("plot_data")
        if plot_data is not None:
            method_name = _interactive_data.get("method") or "Unknown"
            method_lookups[method_name] = _build_cluster_lookup(plot_data, cluster_name_map)
        return method_lookups

    # 現在の手法を先頭に配置
    ordered_methods = []
    if current_method and current_method in rmap:
        ordered_methods.append(current_method)
    for m in rmap:
        if m not in ordered_methods:
            ordered_methods.append(m)

    n_methods = max(1, len(ordered_methods))
    for i_m, method_name in enumerate(ordered_methods):
        if progress_cb:
            progress_cb(int(base + span * i_m / n_methods),
                        f"手法クラスタを準備中… ({method_name})")
        if method_name == current_method and _interactive_data.get("plot_data") is not None:
            # 現在の手法は再読込不要
            method_lookups[method_name] = _build_cluster_lookup(
                _interactive_data.get("plot_data"), cluster_name_map)
            continue
        rds_path = rmap[method_name]
        # 派生 PCA（未補正）はディスク未生成のことがある → Harmony から遅延生成
        if method_name == "PCA" and rds_path and not Path(rds_path).exists():
            harmony_rds = full_map.get("Harmony")
            if harmony_rds and Path(harmony_rds).exists():
                try:
                    _bridge.derive_uncorrected_pca(harmony_rds, rds_path)
                except Exception as e:
                    logger.warning("[DataExport] PCA 派生生成失敗: %s", e)
        if not rds_path or not Path(rds_path).exists():
            logger.warning("[DataExport] %s: RDS が見つかりません → スキップ", method_name)
            continue
        try:
            result = _bridge.extract_data(rds_path)
            # 手法ごとにクラスタ名変更マップは独立。他手法はその手法の保存分を読む。
            other_map = load_cluster_name_map(rds_path, method_name)
            method_lookups[method_name] = _build_cluster_lookup(
                result["plot_data"], other_map)
        except Exception as e:
            logger.warning("[DataExport] %s: データ抽出エラー: %s", method_name, e)

    return method_lookups


# ---------------------------------------------------------------------------
# DESI エクスポート
# ---------------------------------------------------------------------------

def _conditions_sheet_df(conditions: dict):
    """解析条件を xlsx の "Conditions" シート用に 2 列へ平坦化する。

    xlsx は 1 ファイル完結で人手に渡ることが多いので、表と同じブックに
    条件を入れておくと「この表はどの設定か」が失われない。
    csv / parquet は同梱できないため、サーバ側 provenance/ の記録で担保する。
    """
    from app.services.methods_text import render_conditions_rows
    rows = render_conditions_rows(conditions, lang="ja")
    missing = conditions.get("_missing") or []
    if missing:
        rows = rows + [("未記録の項目", ", ".join(missing))]
    return pd.DataFrame(rows, columns=["項目", "値"])


# Excel のシート名に使えない文字。openpyxl はこれらを含む名前で例外を投げる
# （1 サンプルのせいでエクスポート全体が落ちる）。
_SHEET_FORBIDDEN = str.maketrans({c: "_" for c in "[]:*?/\\"})
_SHEET_MAX = 31


def _unique_sheet_name(stem: str, used: dict) -> str:
    """Excel のシート名を無害化し、衝突しないよう一意化する (ver51.9)。

    ★ 従来は `sheet_name = stem[:31]` だけだった。openpyxl は同名シートへの
      `to_excel` を **例外にせず上書き**するため、先頭 31 文字が同じ 2 サンプルが
      **1 枚のシートに混ざって**出る。MSI の測定ファイル名は長い共通接頭辞を
      持ちやすく (`20260807_MouseBrain_Section01_Neg_DHB_run1/2` など)、
      31 文字での衝突はむしろ普通に起きる。出力を見ても混ざったとは分からない。

    `used` は呼び出し側が持ち回る dict（小文字化した名前 → 使用回数）。
    Excel のシート名比較は大文字小文字を区別しないので小文字で持つ。
    """
    base = (str(stem or "").translate(_SHEET_FORBIDDEN)
            .strip().strip("'"))
    if not base:
        base = "Sheet"
    name = base[:_SHEET_MAX]

    key = name.lower()
    if key not in used:
        used[key] = 1
        return name

    # 衝突: `_2`, `_3`, ... を足す。31 文字に収めるため base 側を削る。
    n = used[key] + 1
    while True:
        suffix = f"_{n}"
        cand = base[:_SHEET_MAX - len(suffix)] + suffix
        if cand.lower() not in used:
            used[key] = n
            used[cand.lower()] = 1
            return cand
        n += 1


# ---------------------------------------------------------------------------
# 出力先の解決とサイズガード（★ ver62.1）
# ---------------------------------------------------------------------------
# 従来は各書き出し関数が **バイト列を返し**、呼び出し側が `path.write_bytes()` して
# いた。これだと直列化した出力が丸ごとメモリに乗る。しかも
#   to_csv() の str → .encode() の bytes → BytesIO → getvalue()
# と 4 重に複製される。実測（20,000 spot × 2,000 m/z）:
#
#   現行チェーン : 62.1 秒 / RSS 増分 +1.94 GB  ← DataFrame 実体 0.15 GB の 13 倍
#   パス直接書き : 58.7 秒 / RSS 増分 +0.00 GB
#
# 実データ規模（203,078 spot × 4,566 m/z）へ外挿すると増分だけで 45 GB になり、
# `mem_limit: 12g` に対してほぼ全部がホストスワップへ落ちる。待ち時間の正体はこれ。
#
# そこで **出力先のパスを渡して pandas に直接書かせる**。pandas はチャンクで
# 書くので巨大な中間オブジェクトを作らない。

# xlsx のセル数上限。超えたら走り出す前に止めて CSV / Parquet を案内する。
#
# 実測: openpyxl は約 19 秒/百万セル・0.30 GB/百万セル（252K / 1.0M / 4.0M セルの
# 3 点で線形を確認）。既定 500 万セルで約 95 秒・約 1.5 GB。
# 実データ規模の 9.28 億セルなら **約 4.9 時間・約 278 GB** で完走しない。
# 従来は列数(16,384)しか見ておらず、4,566 m/z はガードを通り抜けて
# 「終わらないまま走り続ける」状態になっていた。
XLSX_MAX_CELLS = int(os.environ.get("EXPORT_XLSX_MAX_CELLS", 5_000_000))


@contextlib.contextmanager
def _atomic_output(final_path: Path):
    """書き込み中のファイルが見えないよう、別名で書いてから原子的に差し替える。

    ★ ver62.1: パスへ直接書くようにした副作用で、**書き込み途中のファイルが
      最終ファイル名で見えてしまう**問題が出た（PR #169 のレビューで指摘）。

      ChatGPT API の状態窓口 `gpt_api._export_job_status` は
      `_find_export_job_file`（`<job_id>__*` の glob）が当たるだけで
      **ジョブ記録を見る前に `status: done` を返す**（仕様として意図的に残されている:
      レジストリが上限掃除で消えてもファイルから解決できるようにするため）。
      従来は完成済みのバイト列を 1 回で書いていたので窓は数ミリ秒だったが、
      pandas が数分かけて書くようになると、その間のポーリングが
      **切り詰められた CSV / 壊れた Parquet をダウンロードさせる**。

      さらに、直列化が途中で失敗して部分ファイルが残ると、それが「成功」に見える。

      そこで先頭に `.` を付けた別名で書く。glob `<job_id>__*` は名前が
      `<job_id>__` で始まることを要求するので、`.` 始まりの名前には当たらない。
      書き終えてから `os.replace` で差し替える（同一ディレクトリなので原子的）。
      失敗時は部分ファイルを消す。**成功したファイルだけが最終名で存在する**。

      拡張子は温存する（pandas / openpyxl が拡張子から形式を推測する経路を壊さない）。
    """
    tmp = final_path.with_name(
        f".{final_path.stem}.partial{final_path.suffix}")
    try:
        yield tmp
        os.replace(tmp, final_path)
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _resolve_out_path(out_dir, prefix: str, filename: str) -> Path:
    """出力先の実パスを決める。out_dir が None なら一時ディレクトリを作る。

    None を許すのはテストとアドホック実行のため。本番（`_run_export_job`）は
    必ず `DATA_EXPORT_TMP_DIR` と job_id 由来の prefix を渡す。
    """
    d = Path(out_dir) if out_dir is not None else Path(tempfile.mkdtemp(
        prefix="msi_export_"))
    d.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r'[\\/:*?"<>|]+', "_", str(filename)) or "export.bin"
    return d / f"{prefix}{safe}"


def _guard_xlsx_size(n_rows: int, n_cols: int) -> None:
    """xlsx が現実的な規模に収まるか検査する。超えたら理由を添えて止める。

    黙って走らせると「終わらない」だけで理由が分からない。何が超えたかと、
    どうすればよいかを必ず言う。
    """
    if n_cols > 16384:
        raise ValueError(
            f"xlsx は列数上限(16,384)を超えます（{n_cols:,} 列）。"
            "出力形式で CSV または Parquet を選択してください。")
    cells = n_rows * n_cols
    if cells > XLSX_MAX_CELLS:
        raise ValueError(
            f"xlsx には大きすぎます（{n_rows:,} 行 × {n_cols:,} 列 = "
            f"{cells:,} セル / 上限 {XLSX_MAX_CELLS:,}）。"
            "この規模の Excel 書き出しは実測で約 19 秒・0.3 GB / 百万セルかかり、"
            "現実的な時間で終わりません。"
            "出力形式を Parquet（最速）または CSV にするか、"
            "「出力内容の設定」で強度(m/z 全列)を外してください。")


def _export_desi(
    data_folder: str, method_lookups: OrderedDict, region_lookup: dict | None = None,
    progress_cb=None, base: int = 0, span: int = 0, conditions: dict | None = None,
    roi_failed: list | None = None, report: list | None = None,
    exclude_unused: bool = False, out_dir=None, prefix: str = "",
) -> tuple[Path, str]:
    """DESI .txt → Excel バイト列（サンプル別シート + 手法別クラスター列）。

    複数手法の場合は手法名を列ヘッダーにして横並びで配置する。
    単一手法の場合は従来通り「UMAP cluster」列1つ。
    region_lookup を渡すと最終列に「領域名」(ROI) を付与する（未割当は空欄）。
    report にリストを渡すとシート別の突合内訳（summarize_coverage 用）を追記する。
    exclude_unused=True で「UMAP 解析に使っていないサンプル」のシートを作らない。

    ★ ver59.0: 既定は False。既存の呼び出し（テスト含む）の挙動を変えないため、
      既定 ON は UI / API 側で持つ。

    Returns (out_path, filename)。★ ver62.1: バイト列ではなくパスを返す。
    """
    add_region = region_lookup is not None
    file_stems = list_msi_files(data_folder)
    if not file_stems:
        # ★ ver62.2: 一行の「見つかりません」では原因を追えなかった。
        #   呼び出し側 (`_do_export`) は判断根拠付きで先に検査するので、
        #   ここへ来るのは直接呼び出し / API 経路。
        raise ValueError(_no_input_message(data_folder, "DESI"))

    is_multi = len(method_lookups) > 1
    method_names = list(method_lookups.keys())

    # 全手法から Sample 名を収集
    all_sample_names: set[str] = set()
    for lookup in method_lookups.values():
        all_sample_names.update(k[0] for k in lookup.keys())
    sample_names = sorted(all_sample_names)

    # ★ ver51.9 / B-9: `.txt` が無いサンプルを従来は**無言で飛ばして**いた。
    #   `list_msi_files` は `.csv` / `.xlsx` 由来の stem も返す（解析時に
    #   `.txt` へ変換される前提）ため、変換前にエクスポートすると
    #   **シートが 1 枚も無い「成功した」Excel** が出る。
    #   1 枚も書けないと分かっているならここで止める（openpyxl は
    #   シート 0 枚のブックを保存できず、別の分かりにくい例外になる）。
    # ★ ver62.2: `.TXT` でも同じ stem を指すので、大文字小文字を問わず解決する
    #   （組み立て直すと Linux で見つからず「.txt が未生成」と誤報していた）。
    txt_by_stem = {s: find_msi_txt(data_folder, s) for s in file_stems}
    skipped_stems = [s for s in file_stems if txt_by_stem[s] is None]
    if len(skipped_stems) == len(file_stems):
        raise ValueError(
            "書き出せるサンプルがありません。"
            f"{len(skipped_stems)} 件すべて .txt が未生成です "
            f"({', '.join(skipped_stems[:5])}"
            f"{' …' if len(skipped_stems) > 5 else ''})。"
            "解析を実行して .csv / .xlsx を .txt へ変換してください。")
    for _s in skipped_stems:
        logger.warning(
            "[DataExport] %s.txt が無いためスキップ "
            "(解析を実行して .txt へ変換してください)", _s)

    # ★ ver52.5: 解析のサンプル名と照合できなかった stem（下のループで集める）。
    unmatched_stems: list[str] = []

    # ★ ver59.0: サンプル名の突合は stem だけで決まる（.txt の中身は要らない）ので、
    #   ループより前にまとめて解決する。除外の全滅ガードを ExcelWriter に入る前へ
    #   置けるようにするため。ループ内はこの dict を引くだけにして二度呼びを避ける
    #   （`_match_sample_name` は曖昧なとき warning を出すので、二度呼ぶと二重に出る）。
    _writable = [s_ for s_ in file_stems if s_ not in set(skipped_stems)]
    matched_by_stem = {s_: _match_sample_name(s_, sample_names) for s_ in _writable}

    # 「UMAP 解析に使っていない」と判断してよいサンプル。
    #   TIMS 側 `unanalyzed_groups` と同じ思想:
    #     - 少なくとも 1 つの stem が一致していること（= 解析対象を絞っただけの署名）
    #     - 解析サンプルが 1 つも無いときは何もしない
    #     - 全部を除外することにはならない
    #   1 つも一致しないのは「解析対象を絞った」ではなく **サンプル名の付け方が違う**
    #   （＝直すべき不具合）可能性が高いので、その場合は除外せず従来どおり
    #   Skipped シートで報告する。黙って消すとバグの証拠が消える。
    excluded_stems: list[str] = []
    blocked_samples: list[str] = []
    if exclude_unused and sample_names:
        excluded_stems, blocked_samples = _unanalyzed_stems(
            matched_by_stem, sample_names)
        if blocked_samples:
            logger.warning("[DataExport] 解析サンプル %s に対応する .txt が"
                           "見つからないため除外を見送りました", blocked_samples)
    if excluded_stems and len(set(skipped_stems) | set(excluded_stems)) >= len(file_stems):
        # ExcelWriter に入ってしまうと Conditions / Skipped シートだけで保存が
        # 成功し、「データシートが 1 枚も無いのに ✅ で返る xlsx」になる。
        raise ValueError(
            "出力するサンプルがありません。"
            "「解析に使っていない切片を除外」で全てのサンプルが除外されました。"
            "チェックを外すか、解析対象のサンプルを確認してください。")

    n_files = max(1, len(file_stems))
    # ★ ver62.2: ver62.1 の「バイト列を組み立てずパスへ直接書く」が DESI 側に
    #   届いていなかった。`io.BytesIO` に全量を組み立て、さらに `getvalue()` で
    #   もう 1 部複製していたので、ブック実体の約 2 倍が常駐していた
    #   （CHANGELOG は _export_tims / _export_desi の両方を直したと書いている）。
    #   TIMS 側と同じく `_atomic_output` の一時パスへ openpyxl に直接書かせる。
    filename = "UMAP_cluster_DESI.xlsx"
    out_path = _resolve_out_path(out_dir, prefix, filename)
    # "Conditions" と "Skipped" は後から必ず追加し得るので、先に予約して奪われないようにする。
    # ★ ver59.0: 従来 "skipped" は予約されておらず、`Skipped.txt` という生ファイルが
    #   あると `_unique_sheet_name` が "Skipped" を返して報告シートと**無言で混ざる**
    #   （openpyxl は同名シートへの to_excel を例外にせず上書きする）。
    used_sheet_names = {"conditions": 1, "skipped": 1}
    with _atomic_output(out_path) as tmp, \
            pd.ExcelWriter(tmp, engine="openpyxl") as writer:
        for i_f, stem in enumerate(file_stems):
            if progress_cb:
                progress_cb(int(base + span * i_f / n_files),
                            f"書き込み中… {i_f + 1}/{n_files} ({stem})")
            txt_path = txt_by_stem.get(stem)
            if txt_path is None:
                continue        # 上で列挙・記録済み (ver51.9 / B-9)
            if stem in excluded_stems:
                # ★ ver59.0: 解析に使っていないサンプル。シートを作らない。
                #   report にも積まない — 積むと summarize_coverage が
                #   resolver="no-sample" として ⚠️ を出し、意図的に除外したのに
                #   不具合のように報告されてしまう。
                logger.info("[DataExport] %s: 解析に使っていないため出力から除外", stem)
                if report is not None:
                    # rows=0 なので summarize_coverage の集計・⚠️ 判定には影響せず、
                    # summarize_exclusions だけがこれを拾う。
                    report.append({"stem": stem, "rows": 0, "matched": 0,
                                   "excluded": {stem: 0}})
                continue

            # 行単位で読み込み（ヘッダー20列/データ21列の不一致に対応）
            with open(txt_path, "r", encoding="utf-8", errors="replace") as fh:
                raw_lines = fh.readlines()

            if not raw_lines:
                continue

            rows = [line.rstrip("\r\n").split("\t") for line in raw_lines]
            max_cols = max(len(r) for r in rows)
            matched_sample = matched_by_stem.get(stem)
            # ★ ver52.5: 一致しないと下の座標引きが丸ごと飛ばされ、
            #   **そのシートの全行でクラスタ列と領域名列が空**になる。
            #   従来はどこにも報告されず、出力された Excel は一見完全なので、
            #   「クラスタに属さない」のか「照合できなかった」のか区別できなかった。
            #   実測: 大文字小文字違い ('wt_liver_01') や区切り違い
            #   ('WT-liver-01') で `_match_sample_name` は None を返す。
            #   ★ 解析サンプルが 1 つも無いときは報告しない。
            #     「一致しなかった」と言っても情報量が無いうえ、
            #     既存の番人 (test_no_skipped_sheet_when_nothing_was_skipped) が
            #     空の lookup で「Skipped シートを出さないこと」を検査している。
            if matched_sample is None and sample_names:
                unmatched_stems.append(stem)
                logger.warning(
                    "[DataExport] %s は解析のサンプル名と一致しません "
                    "(クラスタ列は空欄になります): 候補 %s",
                    stem, sample_names[:5])

            output_rows: list[list[str]] = []

            # ★ ver58.3: ヘッダー行数を自動判定する。
            #   従来は 5 行決め打ちだった。ヘッダは 5 行 (装置の実データ) のほかに
            #   4 行 (`desi_converter` が組み替えたもの / 化合物名を持たない形) が
            #   実在し、4 行のファイルでは **データ 1 行目がヘッダー扱いされて**
            #   その画素だけクラスタ列が空欄のまま出ていた。
            #   ver55.2 は R と `desi_header.py` を自動判定に直したが、
            #   このエクスポータだけ取り残されていた。判定基準は同じ
            #   (列1 が整数の PixelID・列2/3 が数値)。
            _hit = next((i for i, ln in enumerate(raw_lines[:12])
                         if _is_data_line(ln)), None)
            n_header = _hit if (_hit is not None and _hit >= 1) else 5
            n_header = min(n_header, len(rows))

            # ヘッダー行
            for i in range(n_header):
                padded = rows[i] + [""] * (max_cols - len(rows[i]))
                if i == 0:
                    # 行1: ラベル行 → 最右に手法名を列ヘッダーとして追加
                    if is_multi:
                        padded.extend(method_names)
                    else:
                        padded.append("UMAP cluster")
                    if add_region:
                        padded.append("領域名")  # 最終列に ROI
                else:
                    # 2 行目以降のヘッダー行 → 空セル
                    padded.extend([""] * len(method_names) if is_multi else [""])
                    if add_region:
                        padded.append("")
                output_rows.append(padded)

            # データ行 — 各行に全手法のクラスター値を横並びで追加
            data_rows = rows[n_header:] if len(rows) > n_header else []
            n_matched = 0

            for row in data_rows:
                padded = row + [""] * (max_cols - len(row))

                # 座標を一度だけ取得
                x_val, y_val = None, None
                if matched_sample and len(row) >= 3:
                    try:
                        x_val = round(float(row[1]), 4)
                        y_val = round(float(row[2]), 4)
                    except (ValueError, IndexError):
                        pass

                # 各手法のクラスター値を列として追加
                _hit_row = False
                for method_name in method_names:
                    cluster_val = ""
                    if x_val is not None and y_val is not None:
                        key = (matched_sample, x_val, y_val)
                        cluster_val = method_lookups[method_name].get(key, "")
                    if cluster_val != "":
                        _hit_row = True
                    padded.append(cluster_val)
                if _hit_row:
                    n_matched += 1

                # 最終列に領域名(ROI)
                if add_region:
                    region_val = ""
                    if x_val is not None and y_val is not None:
                        region_val = region_lookup.get((matched_sample, x_val, y_val), "")
                    padded.append(region_val)

                output_rows.append(padded)

            if report is not None:
                report.append({
                    "stem": stem,
                    "rows": len(data_rows),
                    "keyed": len(data_rows) if matched_sample else 0,
                    "matched": n_matched,
                    "resolver": "stem" if matched_sample else "no-sample",
                    "unresolved_samples": [] if matched_sample else [stem],
                })

            df_out = pd.DataFrame(output_rows)

            # シート名（31 文字制限 + 禁止文字 + 衝突対策）
            sheet_name = _unique_sheet_name(stem, used_sheet_names)
            df_out.to_excel(
                writer, sheet_name=sheet_name, header=False, index=False
            )

        # 解析条件シート（論文の Methods 用）
        if conditions is not None:
            try:
                _conditions_sheet_df(conditions).to_excel(
                    writer, sheet_name="Conditions", index=False)
            except Exception:
                logger.warning("Conditions シートの追加に失敗", exc_info=True)

        # 一部だけ落ちた場合は資料の中に理由を残す（後から «なぜ足りない» を辿れる）
        # ★ ver52.3: ROI 割当に失敗したサンプルも同じシートに載せる。
        #   従来はログだけだったので、そのスライスの「領域名」が空欄になり
        #   利用者には「どの ROI にも入らなかった」（＝実データ上の所見）と
        #   区別が付かなかった。既にある報告先を使い、新しい仕組みは作らない。
        _skip_names = list(skipped_stems)
        _skip_reasons = [".txt が未生成 (解析前)"] * len(skipped_stems)
        for _s in (roi_failed or []):
            _skip_names.append(_s)
            _skip_reasons.append("ROI(領域名)の割当に失敗 — 領域名は空欄")
        # ★ ver52.5: 解析のサンプル名と照合できなかったシート。
        #   ROI 失敗と同じく「出力はされたが列が空」なので、同じ表に載せる。
        for _s in unmatched_stems:
            _skip_names.append(_s)
            _skip_reasons.append(
                "解析のサンプル名と一致せず — クラスタ列・領域名は空欄")
        # ★ ver59.0: オプションで意図的に外したサンプル。「一致せず」とは
        #   別物なので、オプションで落としたと分かる文言にする。
        for _s in excluded_stems:
            _skip_names.append(_s)
            _skip_reasons.append(
                "UMAP 解析に使っていないため出力から除外（オプション指定）")
        if blocked_samples and report is not None:
            report.append({"stem": None, "rows": 0, "matched": 0,
                           "blocked_samples": blocked_samples})
        if _skip_names:
            try:
                # 見出しは「未出力」ではなく「注意」。出力はされたが列が空、
                # という行がここに並ぶようになったため。
                pd.DataFrame({"サンプル": _skip_names,
                              "理由": _skip_reasons}
                             ).to_excel(writer, sheet_name="Skipped", index=False)
            except Exception:
                logger.warning("Skipped シートの追加に失敗", exc_info=True)

    return out_path, filename


# ---------------------------------------------------------------------------
# TIMS エクスポート
# ---------------------------------------------------------------------------

def _make_unique(names: list[str]) -> list[str]:
    """R の make.unique 相当（2 個目以降に .1 / .2 … を付ける）。"""
    seen: dict = {}
    out = []
    for n in names:
        if n in seen:
            seen[n] += 1
            out.append(f"{n}.{seen[n]}")
        else:
            seen[n] = 0
            out.append(n)
    return out


def _read_tims_transform_csv(p: Path) -> pd.DataFrame | None:
    """SCiLS Transform CSV (legacy) を R の読み方に合わせて読む。

    R 側 `read_desi_data` の Case 2（TIMS スクリプト）と同じ規約:
      - 先頭 4 行がヘッダ。区切りは 1 行目に `,` があればカンマ、無ければタブ
      - 特徴量名は 3 行目の 4 列目以降にある m/z を `m/z %.5f` にしたもの
      - x/y は**列名ではなく位置**。末尾に annotation 列があるかで 1 つずれる
          annotation 有: … 強度 …, x, y, annotation
          annotation 無: … 強度 …, x, y

    ★ ver58.3: 従来はこの形式でも `pd.read_csv`（既定＝カンマ・1 行目ヘッダ）で
      読んでいた。ヘッダ行を読み飛ばさないので **データ 1 行目が列名になり**、
      `x` / `y` という列は 1 つも現れない。その結果 `append_cluster_region_columns`
      が座標キーを 1 つも作れず、クラスタ列が全行空欄になっていた（列名も 1 行分
      ずれた無意味なものになる）。判定できなければ None を返し、呼び出し側が
      従来どおりの読み方に戻す。
    """
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            hdr = [fh.readline() for _ in range(4)]
        if not hdr[2].strip():
            return None
        sep = "," if "," in hdr[0] else "\t"
        tok3 = [t.strip() for t in hdr[2].rstrip("\r\n").split(sep)]
        if len(tok3) < 6:
            return None
        # 4 列目以降の「先頭から連続して数値として読める分」を m/z とみなす。
        # R は `tokens3[4:(length-2)]` と位置で切っているが、annotation 列を
        # 後付けしたファイルはヘッダ行の列数がデータ行と揃わないことがある。
        # 先頭からの連続で取れば、どちらの体裁でも同じ列に対応する。
        mz_vals = []
        for t in tok3[3:]:
            v = pd.to_numeric(t, errors="coerce")
            if pd.isna(v):
                break
            mz_vals.append(float(v))
        if not mz_vals:
            return None
        names = _make_unique([f"m/z {v:.5f}" for v in mz_vals])

        df = pd.read_csv(p, sep=sep, skiprows=4, header=None)
        ncol = df.shape[1]
        if ncol < 6:
            return None
        # 末尾が数値化できない列なら annotation（R は colClasses="numeric" で
        # 読んで NA 率 >0.9 を見ている。ここでは同じことを直接判定する）。
        last_nan = pd.to_numeric(df.iloc[:, -1], errors="coerce").isna().mean()
        has_ann = bool(last_nan > 0.90)
        if has_ann:
            x_i, y_i, feat_end = ncol - 3, ncol - 2, ncol - 4
        else:
            x_i, y_i, feat_end = ncol - 2, ncol - 1, ncol - 3
        if feat_end < 3:
            return None

        feats = df.iloc[:, 3:feat_end + 1].copy()
        # ヘッダから取れた名前が足りない/多い場合に落とさない（多い分は捨て、
        # 足りない分は位置で名前を作る）。R も長さ違いを切り詰めて続行する。
        cols = list(names[:feats.shape[1]])
        cols += [f"feature_{i}" for i in range(len(cols), feats.shape[1])]
        feats.columns = cols
        out = pd.DataFrame({
            "id": df.iloc[:, 0].to_numpy(),
            "x": pd.to_numeric(df.iloc[:, x_i], errors="coerce").to_numpy(),
            "y": pd.to_numeric(df.iloc[:, y_i], errors="coerce").to_numpy(),
        })
        out = pd.concat([out, feats.reset_index(drop=True)], axis=1)
        if has_ann:
            out["annotation"] = df.iloc[:, -1].astype(str).to_numpy()
        return out
    except Exception as e:  # noqa: BLE001 — 判定できなければ従来の読み方へ戻す
        logger.warning("[DataExport] Transform CSV として読めませんでした (%s): %s",
                       p.name, e)
        return None


def _tims_available_columns(file_path: str) -> "list | None":
    """parquet のフッタ（スキーマ）だけを読んで列名を返す。

    ★ ver61.0: 「出力内容の設定」で強度を外したときに m/z 列を **読まずに済ませる**
      ために、先に列名だけ知る必要がある。フッタだけなので実データは読まない。
      CSV/TSV は事前に列名を知る安価な手段が無いので None（＝絞らない）。
    """
    p = Path(file_path)
    if p.suffix.lower() not in (".parquet", ".pq"):
        return None
    try:
        import pyarrow.parquet as pq
        return list(pq.read_schema(str(p)).names)
    except Exception as e:  # noqa: BLE001 — 読めなければ従来どおり全列読む
        logger.debug("[DataExport] スキーマ取得に失敗（全列読みで継続）: %s", e)
        return None


def _read_tims_file(file_path: str, columns: "list | None" = None) -> pd.DataFrame:
    """TIMS 入力ファイルを読み込む（Parquet/CSV/TSV 自動判定）。

    見出し付き CSV（`x` / `y` 列を持つ）は従来どおりそのまま読む。
    そうでなければ legacy SCiLS Transform CSV として読み直す（★ ver58.3）。
    ヘッダ行がデータ行より狭い（annotation 列を後付けした）ファイルは
    既定の `pd.read_csv` が ParserError で落ちるため、その場合も読み直す。

    columns: parquet のとき読む列を絞る（★ ver61.0）。None なら従来どおり全列。
        CSV 経路には効かない（行を全部パースする必要があるため）。呼び出し側が
        読み込み後に列を落とす。
    """
    p = Path(file_path)
    ext = p.suffix.lower()
    if ext in (".parquet", ".pq"):
        return pd.read_parquet(file_path, columns=columns)
    sep = "\t" if ext == ".tsv" else ","
    try:
        df = pd.read_csv(file_path, sep=sep)
    except Exception as e:  # noqa: BLE001 — 列数不揃い等。legacy として読み直す
        alt = _read_tims_transform_csv(p)
        if alt is None:
            raise
        logger.info("[DataExport] %s を Transform CSV として読み直しました (%s)",
                    p.name, e)
        return alt
    if "x" in df.columns and "y" in df.columns:
        return df
    alt = _read_tims_transform_csv(p)
    return alt if alt is not None else df


def _apply_feature_annotation_columns(df: pd.DataFrame, data_folder: str) -> pd.DataFrame:
    """サイドカーがあれば m/z 特徴量列を埋め込み名（`化合物名_<m/z> | …`）へリネームする。

    本体 parquet（数 GB）は書き換えず、エクスポート時に列名だけを差し替える。これにより
    「分子情報を後から登録」した（サイドカーのみ付与した）データでも、通常登録と同じ
    化合物名付き列名で出力できる。サイドカー無しなら無変換。非特徴量列（id/x/y/annotation）
    は対象外。列名が既に埋め込み済みでも m/z を再抽出して同名に解決するため冪等。
    """
    try:
        import numpy as np

        from app.services.annotation_inspect import find_annotation_sidecar
        from app.services.peak_annotation import make_column_name
        from app.utils.deg_utils import extract_mz_numeric

        sidecar = find_annotation_sidecar([Path(data_folder)])
        if sidecar is None:
            return df
        side = pd.read_parquet(sidecar)
        if "mz" not in side.columns or "raw" not in side.columns:
            return df
        side_mz = side["mz"].to_numpy(dtype=float)
        raws = side["raw"].tolist()
        if side_mz.size == 0:
            return df

        non_meta = {"id", "x", "y", "annotation"}
        tol = 0.005
        rename: dict = {}
        for col in df.columns:
            if col in non_meta:
                continue
            mz = extract_mz_numeric(col)
            if mz is None or mz == float("inf"):
                continue
            j = int(np.argmin(np.abs(side_mz - mz)))
            if abs(side_mz[j] - mz) > tol:
                continue
            raw = raws[j]
            if not isinstance(raw, str) or not raw.strip():
                continue
            new = make_column_name(raw, float(mz))
            if new and new != col and new not in rename.values():
                rename[col] = new
        if rename:
            df = df.rename(columns=rename)
    except Exception as e:  # noqa: BLE001 — 変換失敗時はそのまま出力
        logger.warning("エクスポート列名のアノテーション変換に失敗（未変換で出力）: %s", e)
    return df


def _write_mz_list_only(mz_df: pd.DataFrame, fmt: str,
                        conditions: dict | None,
                        out_dir=None, prefix: str = "") -> tuple[Path, str]:
    """m/z 一覧だけを出力する（★ ver62.0）。

    スポット単位の項目が 1 つも選ばれていないときの経路。表が 1 つしかないので
    csv / parquet でも問題なく出せる。
    """
    if mz_df is None or mz_df.empty:
        raise ValueError(
            "m/z 列が 1 つも見つかりませんでした。"
            "MSI データフォルダに変換済みの parquet があるか確認してください。")

    # 一覧表は数千行しかないのでサイズガードは要らない。それでも
    # パスへ直接書くのは、経路を 1 つに揃えて分岐を減らすため。
    ext = {"xlsx": "xlsx", "parquet": "parquet"}.get(fmt, "csv")
    filename = f"mz_list_TIMS.{ext}"
    out_path = _resolve_out_path(out_dir, prefix, filename)
    with _atomic_output(out_path) as tmp:
        if ext == "xlsx":
            with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
                mz_df.to_excel(writer, index=False, sheet_name=_mzlist.SHEET_NAME)
                if conditions is not None:
                    try:
                        _conditions_sheet_df(conditions).to_excel(
                            writer, sheet_name="Conditions", index=False)
                    except Exception:
                        logger.warning("Conditions シートの追加に失敗", exc_info=True)
        elif ext == "parquet":
            mz_df.to_parquet(tmp, index=False)
        else:
            mz_df.to_csv(tmp, index=False)
    return out_path, filename


def _tims_header_columns(file_path: str) -> list:
    """入力ファイルの列名だけを安価に取る（★ ver62.0）。

    m/z 一覧はスポットの行を 1 行も要らないので、ここで実データを読まない。
    parquet はフッタだけ、CSV/TSV は `nrows=0` でヘッダだけ読む。
    """
    cols = _tims_available_columns(file_path)
    if cols is not None:
        return cols
    p = Path(file_path)
    sep = "\t" if p.suffix.lower() == ".tsv" else ","
    try:
        return list(pd.read_csv(file_path, sep=sep, nrows=0).columns)
    except Exception as e:  # noqa: BLE001 — legacy CSV は読み直しが要る
        logger.debug("[DataExport] ヘッダ取得に失敗: %s (%s)", p.name, e)
        alt = _read_tims_transform_csv(p)
        return list(alt.columns) if alt is not None else []


def _build_mz_list_table(input_paths: list, data_folder: str) -> pd.DataFrame:
    """m/z 一覧表を作る（★ ver62.0）。スポットの行は 1 行も読まない。

    列名は `_apply_feature_annotation_columns` を通した**後**のものを渡す。
    そうしないと `列名` が実際の出力の見出しと食い違い、強度行列と
    突き合わせられなくなる。実データは読まず、空の DataFrame の列名だけを
    リネームさせている。
    """
    from app.services.annotation_inspect import find_annotation_sidecar

    names: list = []
    seen: set = set()
    for fp in input_paths:
        for c in _tims_header_columns(fp):
            if c not in seen:
                seen.add(c)
                names.append(c)

    renamed = _apply_feature_annotation_columns(
        pd.DataFrame(columns=names), data_folder)
    sidecar = find_annotation_sidecar([Path(data_folder)])
    return _mzlist.build_mz_list(list(renamed.columns), sidecar_path=sidecar)


def _tims_cluster_columns(method_lookups: OrderedDict) -> list:
    """この出力に現れるクラスタ列名。単一手法なら "UMAP cluster"、複数なら手法名。

    `append_cluster_region_columns` の `col_name = method_name if is_multi else
    "UMAP cluster"` と同じ規則。強度列の判定と集計キーの解決で使う。
    手法名は任意の文字列を取り得るので、推測せずここで一元的に決める。
    """
    return (list(method_lookups.keys()) if len(method_lookups) > 1
            else [_eo.SINGLE_METHOD_CLUSTER_COLUMN])


def _aggregate_tims(dfs: list, method_lookups: OrderedDict,
                    options) -> pd.DataFrame:
    """ピクセル行を集計してグループ平均の表にする（★ ver61.0）。

    クラスタを集計キーに含める場合だけ手法ごとに集計し、`Method` 列を持つ
    **縦持ち**にする。手法ごとに列を横へ並べる（ピクセル出力の流儀）と、
    手法間で意味の違うクラスタ番号が 1 行に同居して読めなくなるうえ、
    csv / parquet では 1 表に収まらない。

    クラスタをキーに含めないなら手法によって結果が変わらないので 1 回だけ集計する。
    手法の数だけ同じ表を繰り返しても情報が増えない。
    """
    cluster_cols = _tims_cluster_columns(method_lookups)
    all_cols = dfs[0].columns if dfs else []
    value_cols = (_eo.intensity_columns(all_cols, cluster_columns=cluster_cols)
                  if _eo.wants(options, "intensity") else [])

    if "cluster" not in _eo.normalize(options)["group_keys"]:
        group_cols = _eo.resolve_group_columns(options, [])
        partials = [_agg.accumulate_partial(d, group_cols, value_cols) for d in dfs]
        return _agg.combine_partials(partials, group_cols)

    frames = []
    for col in cluster_cols:
        group_cols = _eo.resolve_group_columns(options, [col])
        partials = [_agg.accumulate_partial(d, group_cols, value_cols) for d in dfs]
        out = _agg.combine_partials(partials, group_cols)
        if out.empty:
            continue
        # クラスタ列名は手法ごとに違うので、縦持ちのために共通名へ寄せる。
        out = out.rename(columns={col: "Cluster"})
        out.insert(0, "Method", col)
        frames.append(out)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _export_tims(
    data_folder: str, method_lookups: OrderedDict, fmt: str,
    region_lookup: dict | None = None,
    progress_cb=None, base: int = 0, span: int = 0, conditions: dict | None = None,
    report: list | None = None, exclude_unused: bool = False,
    options=None, extra_lookups: dict | None = None,
    out_dir=None, prefix: str = "",
) -> tuple[Path, str]:
    """TIMS 入力ファイルに手法別クラスター列を追加してエクスポート。

    複数手法の場合は手法名を列名にして横並びで配置する。
    単一手法の場合は従来通り「UMAP cluster」列1つ。
    region_lookup を渡すと最終列に「領域名」(ROI) を付与する（未割当は空欄）。
    report にリストを渡すとファイル別の突合内訳（summarize_coverage 用）を追記する。
    exclude_unused=True で「UMAP 解析に使っていない切片」の行を出力から除く。

    ★ ver59.0: 既定は False。既存の呼び出し（テスト含む）の挙動を変えないため、
      既定 ON は UI / API 側で持つ。

    Returns (out_path, filename)。★ ver62.1: バイト列ではなくパスを返す。
    """
    input_paths = build_tims_input_paths(data_folder)
    if not input_paths:
        # ★ ver62.2: DESI 側と同じ説明文にそろえる（どちらの経路でも理由が出る）。
        raise ValueError(_no_input_message(data_folder, "TIMS"))

    is_multi = len(method_lookups) > 1

    # ★ ver62.0: m/z 一覧（1 行 = 1 m/z）はスポットの表とは行の単位が違う。
    #   スポット単位の項目が 1 つも選ばれていなければ、**スポットを 1 行も読まずに**
    #   一覧表だけを出す。「どの m/z が入っているか知りたいだけ」で数 GB を読むのは
    #   本末転倒なため。
    want_spot = _eo.wants_spot_table(options)
    want_mz = _eo.wants_mzlist(options)
    if not want_spot and not want_mz:
        raise ValueError(
            "出力する項目が 1 つもありません。"
            "「出力内容の設定」で列か m/z 一覧を選んでください。")

    mz_df = (_build_mz_list_table(input_paths, data_folder) if want_mz else None)

    if not want_spot:
        return _write_mz_list_only(mz_df, fmt, conditions, out_dir, prefix)

    # csv / parquet は 1 ファイルに 1 表しか入らない。黙って片方を落とすと、
    # 利用者は選んだはずの表が無い理由を追えない。xlsx を案内して止める
    # （列数上限超過時に CSV/Parquet を案内するのと同じ流儀）。
    if want_mz and fmt in ("csv", "parquet"):
        raise ValueError(
            f"{fmt} は 1 ファイルに 1 つの表しか持てないため、"
            "スポット単位の列と「m/z 一覧」を同時に出力できません。"
            "出力形式で Excel (.xlsx) を選ぶ（別シートになります）か、"
            "「m/z 一覧」だけを選んでください。")

    # 全手法の Sample 名を統合
    all_sample_names: set[str] = set()
    for lookup in method_lookups.values():
        all_sample_names.update(k[0] for k in lookup.keys())
    all_sample_list = sorted(all_sample_names)

    n_files = max(1, len(input_paths))
    add_region_col = "領域名"
    dfs_out: list = []
    stats_out: list = []
    for i_f, fp in enumerate(input_paths):
        if progress_cb:
            # ★ ver62.1: 読み込みには span の 0.8 までしか使わない。従来は読み終えた
            #   時点で 98% に達し、**最も長い直列化の間バーが動かなかった**ため
            #   「止まった」ように見えていた。残りを書き出しに割り当てる。
            progress_cb(int(base + span * 0.8 * i_f / n_files),
                        f"読み込み中… {i_f + 1}/{n_files} ({Path(fp).stem})")
        # ★ ver61.0: 「出力内容の設定」で強度を外したら m/z 列を読まない。
        #   読んでから捨てるのでは意味がない（実データは m/z が数千列あり、
        #   float32 の実体だけで数 GB になる）。x/y/annotation は突合に要るので
        #   出力に出さなくても必ず読む（`export_options.parquet_columns`）。
        avail = _tims_available_columns(fp)
        read_cols = _eo.parquet_columns(avail, options) if avail else None
        df = _read_tims_file(fp, columns=read_cols)
        df = _apply_feature_annotation_columns(df, data_folder)
        stem = Path(fp).stem
        # 右端に手法別クラスタ列・領域名列をベクトル付与（iterrows 撤廃＝軽い）。
        # ★ ver58.3: 突合の内訳を stats で受け取り、呼び出し側から利用者へ報告する。
        stats: dict = {}
        df = _append_cluster_region_columns(
            df, method_lookups, region_lookup, all_sample_list, is_multi, stem,
            _match_sample_name, stats=stats, extra_lookups=extra_lookups)
        if stats.get("matched", 0) < stats.get("rows", 0):
            logger.warning("[DataExport] %s: クラスタ突合 %s/%s 行 (resolver=%s, 未一致=%s)",
                           stem, stats.get("matched"), stats.get("rows"),
                           stats.get("resolver"), stats.get("unresolved_samples"))

        stats_out.append(stats)
        if report is not None:
            report.append(stats)
        dfs_out.append(df)

    # ★ ver59.0: 解析に使っていない切片の行を落とす。
    #   判定は **全ファイルを見終わってから**（`plan_exclusions`）。1 ファイルだけ見ると
    #   「解析済みのサンプルがこの出力のどこにも現れない」を検出できず、
    #   解析済みの切片を「使っていない」と誤判定して消してしまう。
    #   落とすのは行フィルタで行う。ファイルを丸ごと飛ばすと全ファイル除外時に
    #   下の `dfs_out[0]` が IndexError になり、意味不明な例外として出る。
    if exclude_unused and all_sample_list:
        plan, blocked = _plan_exclusions(stats_out, all_sample_list)
        if blocked:
            logger.warning("[DataExport] 解析サンプル %s が生データに見つからないため"
                           "除外を見送りました", blocked)
            if report is not None and report:
                report[0]["blocked_samples"] = blocked
        for i_d, drop in enumerate(plan):
            if not drop or "annotation" not in dfs_out[i_d].columns:
                continue
            d, st = dfs_out[i_d], stats_out[i_d]
            # 値が入っている行は絶対に落とさない（手法ごとに lookup が違い得るため、
            # 代表 1 手法の一致状況だけで消すと別手法の値ごと消える）。
            new_cols = [c for c in (list(method_lookups.keys()) if is_multi
                                    else ["UMAP cluster"]) if c in d.columns]
            if add_region_col in d.columns:
                new_cols.append(add_region_col)
            blank = (d[new_cols].astype(str) == "").all(axis=1) if new_cols else True
            kill = d["annotation"].astype(str).isin(set(drop)) & blank
            if not bool(kill.any()):
                continue
            st["excluded"] = {g: int(n) for g, n in
                              d.loc[kill, "annotation"].astype(str)
                              .value_counts().items()}
            dfs_out[i_d] = d[~kill].reset_index(drop=True)
            # 落としたら stats も落とした後の姿に直す。直さないと
            # summarize_coverage が「もう出力に無い行」を空欄として報告し続ける。
            by_group = dict(st.get("by_group") or {})
            for g in drop:
                by_group.pop(g, None)
            st["by_group"] = by_group
            st["rows_before_exclude"] = st.get("rows")
            st["rows"] = len(dfs_out[i_d])
            logger.info("[DataExport] %s: 解析対象外の切片を除外: %s",
                        st.get("stem"), st["excluded"])

    # ★ ver61.0: 集計 / 列選択。既定 (options=None) は素通しで従来と完全に同じ。
    if _eo.is_group_mode(options):
        # 集計は「除外」を済ませた後に行う。先に集計すると、解析に使っていない切片の
        # 行が平均に混ざったまま消せなくなる（n も水増しされる）。
        df_all = _aggregate_tims(dfs_out, method_lookups, options)
        dfs_out = None                                   # 元の行はもう要らない
        if df_all.empty:
            raise ValueError(
                "集計した結果が 0 行になりました。"
                "集計キー（切片 / 領域名 / クラスタ）の選択を確認してください。")
    else:
        df_all = (
            pd.concat(dfs_out, ignore_index=True) if len(dfs_out) > 1 else dfs_out[0]
        )
        keep = _eo.select_output_columns(
            list(df_all.columns), options, _tims_cluster_columns(method_lookups))
        if keep != list(df_all.columns):
            if not keep:
                raise ValueError(
                    "出力する列が 1 つもありません。"
                    "「出力内容の設定」で項目を 1 つ以上選んでください。")
            df_all = df_all[keep]

    # ★ ver59.0: 0 行になったら止める。to_excel / to_csv / to_parquet はいずれも
    #   例外を出さず **「ヘッダだけの、成功したファイル」** を返すため
    #   （実測確認済み）、ここで止めないと ver58.3 が潰した「無音の成功」に戻る。
    if df_all.empty:
        raise ValueError(
            "出力する行がありません。"
            "「解析に使っていない切片を除外」で全ての行が除外されました。"
            "チェックを外すか、解析対象の切片を確認してください。")

    # ★ ver61.0: 集計した出力は別のファイル名にする。中身が「1 行 = 1 スポット」から
    #   「1 行 = 1 グループ」に変わるのに同じ名前だと、取り違えたまま解析に回される。
    stem_name = ("UMAP_cluster_TIMS_grouped" if _eo.is_group_mode(options)
                 else "UMAP_cluster_TIMS")

    # ★ ver62.1: 走り出す前に xlsx の規模を検査する。従来は列数しか見ておらず、
    #   4,566 m/z はガードを通り抜けて「終わらないまま走り続ける」状態になっていた。
    if fmt == "xlsx":
        _guard_xlsx_size(df_all.shape[0], df_all.shape[1])

    ext = {"xlsx": "xlsx", "parquet": "parquet"}.get(fmt, "csv")
    filename = f"{stem_name}.{ext}"
    out_path = _resolve_out_path(out_dir, prefix, filename)

    # ★ ver62.1: ここが最も長い工程なのに、従来は進捗を 1 度も報告していなかった。
    #   読み込みが終わった時点でバーが 98% に達し、そのまま動かないので
    #   「止まった」ように見えていた。せめて工程名は出す。
    if progress_cb:
        hint = "（Excel は時間がかかります）" if ext == "xlsx" else ""
        progress_cb(int(base + span * 0.9),
                    f"ファイルを書き出し中… {filename}{hint}")

    # ★ ver62.1: バイト列を組み立てずパスへ直接書く。pandas がチャンクで書くので
    #   巨大な中間オブジェクトを作らない（実測で RSS 増分 +1.94 GB → +0.00 GB）。
    with _atomic_output(out_path) as tmp:
        if ext == "xlsx":
            with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
                df_all.to_excel(writer, index=False, sheet_name="Data")
                # ★ ver62.0: m/z 一覧は別シート。行の単位が違う（1 行 = 1 m/z）ので
                #   同じシートには入れられない。
                if mz_df is not None and not mz_df.empty:
                    mz_df.to_excel(writer, index=False,
                                   sheet_name=_mzlist.SHEET_NAME)
                # 解析条件シート（論文の Methods 用）
                if conditions is not None:
                    try:
                        _conditions_sheet_df(conditions).to_excel(
                            writer, sheet_name="Conditions", index=False)
                    except Exception:
                        logger.warning("Conditions シートの追加に失敗",
                                       exc_info=True)
        elif ext == "parquet":
            df_all.to_parquet(tmp, index=False)
        else:
            df_all.to_csv(tmp, index=False)

    return out_path, filename


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def _do_export(
    data_folder, ms_instrument, export_format,
    rds_map, current_method, result_folder, project_id, sub_project_id,
    loaded_rds, cluster_name_map=None, selected_methods=None,
    exclude_unused=False, options=None, out_dir=None, prefix="",
    progress_cb=None,
):
    """データ出力の本体。開いている(読み込み済みの)プロジェクトにスコープを固定して、
    元データに UMAP cluster 列を付与したファイルを生成する。

    progress_cb: progress_cb(pct:int, label:str) を渡すと 0-100 の進捗を報告する（任意）。
    Returns: (out_path|None, filename|None, status_message)。失敗時は (None, None, msg)。
    ★ ver62.1: バイト列ではなく **書き出し済みファイルのパス** を返す。
    """
    def _p(pct, label=""):
        if progress_cb:
            try:
                progress_cb(int(pct), label)
            except Exception:  # noqa: BLE001
                pass

    from app.callbacks.interactive_callbacks import _set_active_key
    # 開いているプロジェクト(= 実際に読み込んだ RDS)にアクティブキーを固定する。
    # 別プロジェクトの plot_data / クラスタを読まないよう loaded_rds を最優先・無条件に設定。
    if loaded_rds:
        _set_active_key(loaded_rds)
    elif rds_map and current_method and current_method in rds_map:
        _set_active_key(rds_map[current_method])

    logger.info(
        "[DataExport] _do_export: loaded_rds=%s data_folder=%s", loaded_rds, data_folder
    )

    try:
        # ms_instrument を確定（DESIプロジェクトが既定の "TIMS" に落ち、誤って TIMS 経路に
        # 入る事象への対策）。metadata が "DESI" でなくてもパス規約から DESI を判定する。
        # ここは**暫定**。データフォルダの推定がこの値に依存するため先に決める。
        ms_instrument_raw = ms_instrument
        ms_instrument = _resolve_instrument(ms_instrument, data_folder, result_folder)
        _p(5, "準備中…")

        # MSI データフォルダを確定する（当該プロジェクト内に限定して推定する）。
        # ★ ver62.7: 登録値の**中身**も見る。`_resolve_data_folder` の docstring 参照。
        data_folder, folder_note = _resolve_data_folder(
            data_folder, result_folder, project_id, sub_project_id,
            ms_instrument, tag="DataExport")
        logger.info("[DataExport] data_folder 確定: %s (根拠=%s)",
                    data_folder, folder_note)
        if not data_folder:
            return None, None, (
                "❌ MSIデータフォルダが見つかりません。"
                "サブプロジェクト設定でMSIデータフォルダを指定してください。"
            )

        # ★ ver62.2: データフォルダが決まったので、**中身**で装置を確定し直す。
        #   パスも metadata も根拠として弱く（`_instrument_from_folder` 参照）、
        #   parquet の TIMS データが `Data/DESI/…` にあるだけで DESI 経路へ入り、
        #   「DESI .txt ファイルが見つかりません」で出力できなくなっていた。
        ms_instrument, inst_reason = _decide_instrument(
            ms_instrument_raw, data_folder, result_folder)
        logger.info("[DataExport] instrument 確定: %s (根拠=%s) data_folder=%s",
                    ms_instrument, inst_reason, data_folder)

        # ★ ver62.2: 分岐に入る前に検査する。従来は存在しないパスでも
        #   分岐の奥まで進み、理由の分からない一行だけが利用者に見えていた。
        bad = _validate_data_folder(data_folder, ms_instrument, inst_reason)
        if bad:
            return None, None, "❌ " + bad

        plot_data = _interactive_data.get("plot_data")
        if plot_data is None or plot_data.empty:
            return None, None, "データが読み込まれていません。先にデータを読み込んでください。"

        # SpatialX/SpatialY が必要
        if "SpatialX" not in plot_data.columns or "SpatialY" not in plot_data.columns:
            return None, None, "空間座標データ (SpatialX/SpatialY) がありません。"
        # 選択手法のクラスタールックアップを構築（未選択なら全手法）: 進捗 10→50%
        method_lookups = _build_all_method_lookups(
            rds_map, current_method, cluster_name_map, selected_methods,
            progress_cb=progress_cb, base=10, span=40)
        if not method_lookups:
            return None, None, "クラスターデータを構築できませんでした。"

        # 領域名(ROI) ルックアップ（読込中 RDS の H&E オーバーレイ保存状態から）。
        # 設定が無ければ空 dict（最終列は空欄）。
        _p(52, "ROI(領域名)を割当中…")
        region_lookup, roi_failed = _build_region_lookup(plot_data, loaded_rds)

        is_desi = (ms_instrument or "").upper() == "DESI"

        # 解析条件を収集し、サーバ側にも記録する。
        # xlsx なら "Conditions" シートとして同梱、csv/parquet は同梱できないので
        # <result-dir>/provenance/ の記録だけが頼りになる。
        conditions = None
        try:
            from app.services.provenance import (collect_conditions,
                                                 results_dir_for_rds,
                                                 write_export_record)
            conditions = collect_conditions(
                rds_path=loaded_rds, result_folder=result_folder,
                integration_method=current_method,
                extra={"export_format": export_format,
                       "exported_methods": list(method_lookups.keys()),
                       "ms_instrument": ms_instrument,
                       "data_folder": data_folder,
                       "exclude_unused_annotations": bool(exclude_unused),
                       # ★ ver61.0: 何を出したのかを来歴に残す。集計した出力は
                       #   ファイルを見ただけでは「どのキーで平均したか」が分からず、
                       #   後から論文の Methods を書き起こせなくなる。
                       "export_options": _eo.describe(options),
                       "export_categories": sorted(
                           _eo.normalize(options)["categories"]),
                       "export_group_keys": _eo.normalize(options)["group_keys"]})
            write_export_record(results_dir_for_rds(loaded_rds, result_folder),
                                "data_export", conditions)
        except Exception as e:  # noqa: BLE001
            logger.warning("[DataExport] 条件記録に失敗: %s", e)

        # ファイル書き込み: 進捗 58→98%
        report: list = []
        if is_desi:
            file_path, filename = _export_desi(
                data_folder, method_lookups, region_lookup,
                progress_cb=progress_cb, base=58, span=40, conditions=conditions,
                roi_failed=roi_failed, report=report,
                exclude_unused=exclude_unused, out_dir=out_dir, prefix=prefix)
        else:
            fmt = export_format or "xlsx"
            # ★ ver61.0: plot_data 由来の追加列（UMAP 座標・品質指標）。
            #   選ばれていなければ空 dict で、従来と同じ列構成のまま。
            extra_lookups = _build_extra_lookups(plot_data, options)
            file_path, filename = _export_tims(
                data_folder, method_lookups, fmt, region_lookup,
                progress_cb=progress_cb, base=58, span=40, conditions=conditions,
                report=report, exclude_unused=exclude_unused,
                options=options, extra_lookups=extra_lookups,
                out_dir=out_dir, prefix=prefix)
        _p(99, "仕上げ中…")

        # ステータスメッセージ
        n_methods = len(method_lookups)
        methods_str = " / ".join(method_lookups.keys())
        msg = f"✅ {filename} を生成しました"
        if n_methods > 1:
            msg += f" ({methods_str})"
        # ★ ver58.3: 突合が成立しなかったら「成功」で終わらせない。
        #   出力自体は返す（生データ部分は使えるため）が、なぜ空欄なのかを必ず言う。
        # ★ ver59.0: 除外した行は「空欄」ではなくなり summarize_coverage の
        #   対象から外れる。黙って行が消えたように見えないよう必ず言う。
        blocked = [b for s_ in report for b in (s_.get("blocked_samples") or [])]
        note = _summarize_exclusions(report, blocked)
        if note:
            msg += "  " + note
        warn = _summarize_coverage(report)
        if warn:
            msg += "  " + warn
        # ★ ver62.3: DESI 経路は `options` も `export_format` も使わない。
        #   ver62.2 は画面から設定を隠すことで辻褄を合わせようとしたが、
        #   隠す判断が当て推量になり得るので**先回りして消すのをやめた**
        #   （`toggle_format_selector` 参照）。代わりに、実際に無視したときだけ
        #   ここで事実を述べる。設定した本人が結果を見て気づける。
        if is_desi:
            msg += ("  DESI 出力のため、出力形式（Excel 固定）と"
                    "「出力内容の設定」は適用していません。")
        # ★ ver62.8: 登録値以外を使ったときは必ず言う（`_folder_note_message` 参照）。
        folder_msg = _folder_note_message(data_folder, folder_note)
        if folder_msg:
            msg += "  " + folder_msg

        return file_path, filename, msg

    except Exception as e:
        logger.exception("データ出力エラー")
        return None, None, f"❌ エラー: {e}"


# ---------------------------------------------------------------------------
# セッション非依存ドライバ（API / バッチから駆動）
# ---------------------------------------------------------------------------

def _pick_primary_rds(rds_map: dict):
    """ROI(領域名) 割当の基準にする RDS を選ぶ。UI 既定の Harmony を優先。"""
    for m in ("Harmony", "RPCA", "PCA", "PCA (uncorrected)"):
        p = (rds_map or {}).get(m)
        if p and Path(p).exists():
            return p
    for p in (rds_map or {}).values():
        if p and Path(p).exists():
            return p
    return None


def build_interactive_export_for_project(
    data_folder, ms_instrument, export_format,
    rds_map, result_folder, project_id, sub_project_id,
    selected_methods=None, exclude_unused=False, out_dir=None, prefix="",
    progress_cb=None,
):
    """ライブ session に依存せず UMAP_cluster エクスポートを生成する（API / バッチ用）。

    `_do_export` から `_interactive_data`（ブラウザ session のライブ状態）依存を取り除いた版。
    全手法のクラスタは `_build_all_method_lookups(current_method=None)` でディスクから読む
    （current_method=None のとき同関数は `_interactive_data` を一切参照しない）。
    ROI(領域名) は primary RDS の plot_data + `hne_overlay_state.json`（ディスク）から割り当てる。

    Returns: ``(out_path|None, filename|None, message)``。失敗時は ``(None, None, msg)``。
    ★ ver62.1: バイト列ではなく **書き出し済みファイルのパス** を返す。
    抽出キャッシュが cold の場合は内部で R 抽出が走り得る（＝重い処理）。
    """
    def _p(pct, label=""):
        if progress_cb:
            try:
                progress_cb(int(pct), label)
            except Exception:  # noqa: BLE001
                pass

    try:
        # ★ ver62.2: GUI 経路 (`_do_export`) と同じ確定手順。API だけ古い判定を
        #   残すと、同じプロジェクトが経路によって別の装置になる。
        ms_instrument_raw = ms_instrument
        ms_instrument = _resolve_instrument(ms_instrument, data_folder, result_folder)
        _p(5, "準備中…")

        # ★ ver62.7: GUI 経路と同じヘルパーを通す（判断を 2 箇所に書かない）。
        data_folder, folder_note = _resolve_data_folder(
            data_folder, result_folder, project_id, sub_project_id,
            ms_instrument, tag="APIExport")
        logger.info("[APIExport] data_folder 確定: %s (根拠=%s)",
                    data_folder, folder_note)
        if not data_folder:
            return None, None, (
                "❌ MSIデータフォルダが見つかりません。"
                "サブプロジェクト設定でMSIデータフォルダを指定してください。")

        ms_instrument, inst_reason = _decide_instrument(
            ms_instrument_raw, data_folder, result_folder)
        logger.info("[APIExport] instrument 確定: %s (根拠=%s) data_folder=%s",
                    ms_instrument, inst_reason, data_folder)
        bad = _validate_data_folder(data_folder, ms_instrument, inst_reason)
        if bad:
            return None, None, "❌ " + bad

        rmap = rds_map if isinstance(rds_map, dict) else {}
        if not rmap:
            return None, None, "❌ 解析済み RDS が見つかりません。"

        # selected_methods を rds_map 内に正規化する。
        # ★ ver52.1: 従来は `if not sel: sel = list(rmap.keys())` があり、
        #   **指定が全部無効だと全手法へ膨らんで**いた。重い R 抽出を指定外の
        #   手法にも走らせ、成果物の由来を誤らせる（監査 API-07）。
        #   呼び出し側 (gpt_api.resolve_export_methods) でも検証するが、
        #   ここは API 以外からも通るので安全網として残す。
        #   大小文字は吸収する（完全一致だと `harmony` が「無効」になっていた）。
        if selected_methods:
            lookup = {str(m).lower(): m for m in rmap}
            sel, bad = [], []
            for m in selected_methods:
                canon = lookup.get(str(m).lower())
                if canon is None:
                    bad.append(str(m))
                elif canon not in sel:
                    sel.append(canon)
            if bad:
                raise ValueError(
                    "この解析結果に無い手法が指定されました: "
                    + ", ".join(bad)
                    + f"（利用可能: {', '.join(rmap.keys())}）")
        else:
            sel = list(rmap.keys())

        # 全手法のクラスタルックアップをディスクから構築（current_method=None → session 非参照）
        method_lookups = _build_all_method_lookups(
            rmap, None, None, sel, progress_cb=progress_cb, base=10, span=40)
        if not method_lookups:
            return None, None, "クラスターデータを構築できませんでした。"

        # ROI(領域名) ルックアップ（primary RDS の plot_data + H&E オーバーレイ保存状態）
        _p(52, "ROI(領域名)を割当中…")
        region_lookup = {}
        roi_failed: list = []
        primary_rds = _pick_primary_rds(rmap)
        if primary_rds:
            try:
                pdat = _bridge.extract_data(primary_rds).get("plot_data")
                region_lookup, roi_failed = _build_region_lookup(pdat, primary_rds)
            except Exception as e:  # noqa: BLE001
                logger.warning("[APIExport] ROI 割当をスキップ: %s", e)

        is_desi = (ms_instrument or "").upper() == "DESI"
        # API 経由でも条件記録は同じ扱いにする（GUI と API で記録に差を作らない）
        conditions = None
        try:
            from app.services.provenance import (collect_conditions,
                                                 results_dir_for_rds,
                                                 write_export_record)
            conditions = collect_conditions(
                rds_path=primary_rds, result_folder=result_folder,
                extra={"export_format": export_format,
                       "exported_methods": list(method_lookups.keys()),
                       "ms_instrument": ms_instrument,
                       "driver": "api",
                       "exclude_unused_annotations": bool(exclude_unused)})
            write_export_record(results_dir_for_rds(primary_rds, result_folder),
                                "data_export_api", conditions)
        except Exception as e:  # noqa: BLE001
            logger.warning("[APIExport] 条件記録に失敗: %s", e)
        report: list = []
        if is_desi:
            file_path, filename = _export_desi(
                data_folder, method_lookups, region_lookup,
                progress_cb=progress_cb, base=58, span=40, conditions=conditions,
                roi_failed=roi_failed, report=report,
                exclude_unused=exclude_unused, out_dir=out_dir, prefix=prefix)
        else:
            fmt = export_format or "parquet"
            file_path, filename = _export_tims(
                data_folder, method_lookups, fmt, region_lookup,
                progress_cb=progress_cb, base=58, span=40, conditions=conditions,
                report=report, exclude_unused=exclude_unused,
                out_dir=out_dir, prefix=prefix)
        _p(99, "仕上げ中…")

        msg = f"✅ {filename} を生成しました"
        if len(method_lookups) > 1:
            msg += " (" + " / ".join(method_lookups.keys()) + ")"
        # ★ ver58.3: GUI 経路と同じく、突合が成立しなかったことを必ず伝える。
        blocked = [b for s_ in report for b in (s_.get("blocked_samples") or [])]
        note = _summarize_exclusions(report, blocked)
        if note:
            msg += "  " + note
        warn = _summarize_coverage(report)
        if warn:
            msg += "  " + warn
        # ★ ver62.8: 画面経路と同じ一文。API から使った側も、どのフォルダを
        #   読んだのかが分からないと成果物の由来を追えない。
        folder_msg = _folder_note_message(data_folder, folder_note)
        if folder_msg:
            msg += "  " + folder_msg
        return file_path, filename, msg

    except Exception as e:  # noqa: BLE001
        logger.exception("[APIExport] エクスポート生成エラー")
        return None, None, f"❌ エラー: {e}"


# ---------------------------------------------------------------------------
# 進捗 % 表示（インプロセス作業スレッド + dcc.Interval ポーリング）。
#  start : ボタン押下で作業スレッドを起動し、進捗UI(0%)表示・ボタン無効・Interval 有効化。
#  poll  : Interval ごとにジョブレジストリを読み、バー%/ラベル更新。完了でダウンロード配信。
# background=True(set_progress) は使わない（_do_export が _interactive_data のインプロセス
# 状態＝セッションの plot_data 等を参照するため、DiskcacheManager の fork worker では共有されない）。
# サーバは単一プロセス・マルチスレッドなので、作業スレッドと poll は同一プロセス＝レジストリ共有可。
# ---------------------------------------------------------------------------

_PROG_SHOW = {"display": "block", "marginTop": "8px"}
_PROG_HIDE = {"display": "none"}

# ★ ver62.6: ボタン押下直後に出すラベル。`data_export_poll` が running の
#   ジョブに対して出す文字列 (`f"{job['label']}  {pct}%"` = "準備中…  0%") と
#   **必ず違う文字列**にする。
#   従来はどちらも "準備中…  0%" で 1 バイトも違わなかった。そのため画面を見ても
#   「ポーリングが返ってきて 0% を描いている」のか「ポーリングが一度も返って
#   きていない」のかが区別できず、実際に**この 2 つを取り違えて別の原因を
#   追いかける**ことになった。画面は切り分けの一次情報なので、
#   別の状態は別の文字で出す。
_START_LABEL = "開始しています…"


@callback(
    [Output("data_export_method_selector", "options"),
     Output("data_export_method_selector", "value")],
    Input("interactive_rds_map", "data"),
    prevent_initial_call=True,
)
def update_data_export_method_options(rds_map):
    """rds_map から出力手法チェックリストを更新（既定で全手法チェック）。"""
    if not rds_map or not isinstance(rds_map, dict):
        return [], []
    methods = list(rds_map.keys())
    return [{"label": m, "value": m} for m in methods], methods


def _run_export_job(job_id, args):
    """作業スレッド本体: _do_export を実行し、出力を一時ファイルへ保存して進捗を反映する。

    base64 でブラウザに載せる（＝タブ落ちの原因）代わりに、
    DATA_EXPORT_TMP_DIR に保存し、Flask の send_file ルートでストリーム配信する。

    ★ ver62.1: 出力先を `_do_export` へ渡し、**書き出しを pandas に直接やらせる**。
      従来はバイト列を受け取ってからここで `write_bytes` していたため、直列化した
      出力が丸ごとメモリに乗っていた（実測で DataFrame 実体の 13 倍）。
    """
    try:
        from app.config import DATA_EXPORT_TMP_DIR
        DATA_EXPORT_TMP_DIR.mkdir(parents=True, exist_ok=True)
        _sweep_old_files(DATA_EXPORT_TMP_DIR, max_age_sec=3600)  # 古い一時ファイルを掃除
        file_path, filename, msg = _do_export(
            *args, out_dir=DATA_EXPORT_TMP_DIR, prefix=f"{job_id}__",
            progress_cb=lambda p, l="": _update_job(job_id, p, l))
        if not file_path or not filename:
            _fail_job(job_id, msg or "出力に失敗しました")
            return
        _finish_job(job_id, str(file_path), filename, msg)
    except Exception as e:  # noqa: BLE001
        logger.exception("[DataExport] ジョブ実行エラー")
        _fail_job(job_id, f"❌ エラー: {e}")


@callback(
    [Output("data_export_progress_container", "style"),
     Output("data_export_progress_label", "children"),
     Output("data_export_progress_bar", "value"),
     Output("data_export_progress_bar", "animated"),
     Output("btn_export_data", "disabled"),
     Output("data_export_job", "data"),
     Output("data_export_poll", "disabled")],
    Input("btn_export_data", "n_clicks"),
    [State("interactive_msi_folder", "value"),
     State("int_cal_ms_instrument", "data"),
     State("data_export_format", "value"),
     State("interactive_rds_map", "data"),
     State("interactive_integration_method", "value"),
     State("interactive_result_folder", "value"),
     State("interactive_project_select", "value"),
     State("interactive_sub_project_select", "value"),
     State("seurat_rds_path_store", "data"),
     State("cluster_name_map_store", "data"),
     State("data_export_method_selector", "value"),
     State("data_export_exclude_unused", "value"),
     State("data_export_options", "data")],
    prevent_initial_call=True,
)
def data_export_start(n_clicks, data_folder, ms_instrument, export_format,
                      rds_map, current_method, result_folder,
                      project_id, sub_project_id, loaded_rds, cluster_name_map,
                      selected_methods, exclude_unused, export_options):
    """出力開始: 作業スレッドを起動し、進捗UI(0%)表示・ボタン無効・Interval 有効化。"""
    if not n_clicks:
        raise PreventUpdate
    # ★ ver62.2: 手法を全部外すと `_build_all_method_lookups` の「空 = 全指定」に
    #   落ち、**外したはずの手法まで出ていた**。列カテゴリ側は空のとき
    #   `_export_tims` が理由付きで止めるのに、ここだけ黙って逆の結果になる。
    #   （「空 = 全部」自体は手法を指定しない API 経路の既定として残す。）
    #   失敗はジョブ記録に載せる。この callback には状態表示の Output が無く
    #   （`div_data_export_status` は `data_export_poll` の持ち物）、
    #   ここで黙って return するとボタンが効かないだけに見えるため。
    job_id = _new_job()
    if not selected_methods:
        _fail_job(job_id, "❌ 出力手法が 1 つも選ばれていません。"
                          "「出力手法 (UMAP)」で 1 つ以上チェックしてください。")
        return (_PROG_SHOW, _START_LABEL, 0, False, True, {"job": job_id}, False)
    # 並びは `_do_export` の位置引数と 1:1（`_run_export_job` が *args で展開する）。
    # ここを崩すと progress_cb に別の値が入って静かに壊れるので、足す位置に注意。
    # ★ ver61.0: 「出力内容の設定」は **dict 1 個を末尾に** 足す。項目ごとに引数を
    #   足していくと、この 1:1 の並びが増えるたびに崩れやすくなる。
    args = (data_folder, ms_instrument, export_format, rds_map, current_method,
            result_folder, project_id, sub_project_id, loaded_rds, cluster_name_map,
            selected_methods, bool(exclude_unused), export_options)
    # 親コンテキスト(ContextVar の active key 等)を引き継いでスレッド実行。
    ctx = contextvars.copy_context()
    threading.Thread(
        target=ctx.run, args=(_run_export_job, job_id, args), daemon=True
    ).start()
    return (_PROG_SHOW, _START_LABEL, 0, False, True, {"job": job_id}, False)


@callback(
    [Output("data_export_download_url", "data"),
     Output("div_data_export_status", "children"),
     Output("data_export_progress_container", "style", allow_duplicate=True),
     Output("data_export_progress_label", "children", allow_duplicate=True),
     Output("data_export_progress_bar", "value", allow_duplicate=True),
     Output("data_export_progress_bar", "animated", allow_duplicate=True),
     Output("btn_export_data", "disabled", allow_duplicate=True),
     Output("data_export_poll", "disabled", allow_duplicate=True)],
    Input("data_export_poll", "n_intervals"),
    State("data_export_job", "data"),
    prevent_initial_call=True,
)
def data_export_poll(n_intervals, job_store):
    """Interval ごとにジョブ進捗を読み、バー/ラベル更新。完了で DL URL を配信して停止する。

    完了時はブラウザに base64 を載せず、`/api/data_export/<job_id>` を配信して
    clientside で自動DL＋ステータスに明示リンクを出す（send_file ストリーム）。
    ジョブは pop しない（ルートがファイル解決に使うため。掃除は TTL / 上限で行う）。
    """
    job_id = (job_store or {}).get("job")
    if not job_id:
        raise PreventUpdate
    job = _get_job(job_id)
    if job is None:
        # ★ ver62.6: ジョブが見つからない = **アプリのプロセスが再起動した**
        #   （`export_progress._JOBS` はモジュールグローバルなので、プロセスが
        #   替われば消える。ブラウザのタブは開いたままなので、`data_export_job`
        #   ストアには前プロセスの job_id が残る）。
        #
        #   従来はここで `(no_update,) * 7 + (True,)` を返していた。つまり
        #     - ラベル … no_update  → 「準備中…  0%」のまま
        #     - 進捗バー … no_update → 出したまま
        #     - ボタン … no_update  → **無効のまま**
        #     - ポーリング … 停止
        #   となり、**画面が永久に固まって理由もどこにも出ない**。押し直すことも
        #   できない。実際にこの状態が「準備中… 0% から進まない」として報告され、
        #   原因の切り分けに何日もかかった。
        #   起きたことを述べて、操作を戻す。
        return ("", html.Span(
                    "⚠ 出力ジョブの情報が失われました"
                    "（アプリが再起動した可能性があります）。"
                    "もう一度「データ出力」を実行してください。",
                    className="text-warning"),
                _PROG_HIDE, "中断", no_update, False, False, True)
    status = job["status"]
    if status == "running":
        pct = job["pct"]
        label = f"{job['label']}  {pct}%"
        return (no_update, no_update, no_update, label, pct,
                False, no_update, no_update)
    if status == "done":
        url = f"/api/data_export/{job_id}"
        link = html.A("⬇ ダウンロード", href=url,
                      className="fw-bold text-decoration-none")
        status_children = html.Span([f"{job['msg']} → ", link])
        # url を store に出して clientside が自動DL。リンクはフォールバック。
        return (url, status_children, _PROG_HIDE, "完了", 100,
                False, False, True)
    # error
    # ★ ver62.6: 従来はここで `_pop_job` していた。しかし停止指示を出しても
    #   **既に飛んでいるポーリング**が 1 回遅れて到着することがあり、そのときには
    #   ジョブが消えているので上の「情報が失われました」に落ちて、**本当のエラー
    #   文言を上書きしてしまう**。done 側は元々 pop していない（配信ルートが使う）。
    #   error も残す。上限 32 件を超えれば `new_job` が running でないものから
    #   掃除するので溜まり続けない。
    return ("", job["msg"], _PROG_HIDE, "失敗", no_update,
            False, False, True)


# 完了時、DL URL が入ったら clientside で自動ダウンロード（attachment のため画面遷移しない）。
clientside_callback(
    """
    function(url) {
        if (url) { window.location.href = url; }
        return '';
    }
    """,
    Output("data_export_download_sink", "data"),
    Input("data_export_download_url", "data"),
    prevent_initial_call=True,
)


@callback(
    [Output("data_export_format_wrapper", "style"),
     Output("data_export_options_wrapper", "style"),
     Output("div_data_export_options_summary", "children", allow_duplicate=True)],
    Input("int_cal_ms_instrument", "data"),
    # DESI から TIMS へ切り替えたとき、要約行を「DESI は固定」のまま残さない。
    [State("data_export_options", "data"),
     # ★ ver62.2: 表示も出力と同じ根拠で決める（下の docstring 参照）。
     State("interactive_msi_folder", "value"),
     State("interactive_result_folder", "value")],
    prevent_initial_call=True,
)
def toggle_format_selector(ms_instrument, options=None,
                           data_folder=None, result_folder=None):
    """DESI → フォーマット/出力内容の設定を非表示 / TIMS → 表示。

    ★ ver62.2: 従来は形式セレクタしか隠していなかった。しかし DESI 経路
      (`_export_desi`) は `options` を**受け取っていない**ので、
      「⚙ 出力内容の設定」で選んだ列・集計単位・m/z 一覧は
      **黙って無視される**。ボタンと要約行が出たままだと、
      設定したつもりの出力が出ない理由が画面のどこにも無い。
      DESI 出力は元 `.txt` のレイアウトをそのまま保つ形式なので、
      選べないことを見せる方が正しい。

    ★ ver62.3: **確信があるときだけ隠す**。ver62.2 は `_decide_instrument` の
      結果だけを見ていたが、その判定には「フォルダのパスに `DESI` という階層が
      ある」「既定」という**推測**が混ざっている。推測を「隠す」方向に使ったのが
      誤りで、パスにたまたま `DESI` が入っている TIMS プロジェクトで
      **形式も列も選べなくなった**（実際に報告があった）。

      外したときの損害が釣り合っていない:
        - 推測を外して表示 … 効かない設定が見えるだけで、出力自体はできる
        - 推測を外して非表示 … 利用者の作業が止まる

      そこで判断**根拠**で振り分ける。断定できるもの（データフォルダの中身）と
      利用者の明示（プロジェクト設定の ms_instrument）だけを隠す理由にし、
      パス由来・既定では隠さない。隠さなかった結果 DESI 経路に入った場合は、
      `_do_export` が出力後に「適用していません」と事実を述べる
      （先回りして機能を消すより、無視したときに言う方が害が小さい）。
    """
    inst, reason = _decide_instrument(ms_instrument, data_folder, result_folder)
    if (inst or "").upper() == "DESI" and reason in _DESI_HIDE_REASONS:
        return ({"display": "none"}, {"display": "none"},
                "DESI は元データ (.txt) の形をそのまま保つため、"
                "出力形式は Excel 固定・列の選択はできません。")
    return {"display": "block"}, {"display": "block"}, _eo.describe(options)
