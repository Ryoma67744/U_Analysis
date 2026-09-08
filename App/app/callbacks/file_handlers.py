# =============================================================================
# MSI Analysis Application - File Handler Callbacks
# ファイル選択・ファイルブラウザ コールバック
# =============================================================================

from pathlib import Path

from dash import Input, Output, State, callback, clientside_callback, ctx, no_update, html, ALL, MATCH
import dash_bootstrap_components as dbc
from dash import dcc

from app.config import (
    DEFAULT_DESI_DATA_FOLDER, DEFAULT_ANNOTATION_FILE_PATH,
    DEFAULT_TIMS_DATA_FOLDER, DEFAULT_ANNOTATION_CSV_PATH,
    DESI_DATA_DIR, TIMS_DATA_DIR, OUTPUT_DATA_DIR, APP_BASE_DIR,
    DESI_V8_TEMPLATE_PATH, DESI_CLUSTER_FILTER_PATH,
    TIMS_V8_TEMPLATE_PATH, TIMS_CLUSTER_FILTER_PATH,
)
from app.layouts.file_browser_modal import (
    get_available_drives, list_directory, build_breadcrumb_parts,
)
from app.services.data_manager import (
    list_msi_files, list_tims_files, list_tims_folder_groups,
    find_tims_file_path, read_parquet_annotations, read_desi_roi_list,
    validate_data_folder,
)
from app.services.session_manager import save_last_settings
from app.services.notify import warn_user


# ---------------------------------------------------------------------------
# DESI / TIMS 排他選択
# ---------------------------------------------------------------------------

@callback(
    Output("analysis_method_tims", "value", allow_duplicate=True),
    Input("analysis_method", "value"),
    prevent_initial_call=True,
)
def clear_tims_on_desi_select(desi_val):
    """DESI が選択されたら TIMS の選択をクリア"""
    if desi_val:
        return None
    return no_update


@callback(
    Output("analysis_method", "value", allow_duplicate=True),
    Input("analysis_method_tims", "value"),
    prevent_initial_call=True,
)
def clear_desi_on_tims_select(tims_val):
    """TIMS が選択されたら DESI の選択をクリア"""
    if tims_val:
        return None
    return no_update


# ---------------------------------------------------------------------------
# 解析設定パネルの表示切替
# ---------------------------------------------------------------------------

@callback(
    [Output("umap_settings_panel", "style"),
     Output("reanalysis_settings_panel", "style"),
     Output("tims_ion_settings", "style"),
     Output("tims_reanalysis_ion_settings", "style"),
     Output("extra_folders_section", "style"),
     Output("desi_recommended_banner", "style"),
     Output("tims_recommended_banner", "style")],
    [Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
)
def toggle_settings_panels(desi_val, tims_val):
    active = desi_val or tims_val or "desi_v8"
    is_umap = active in ("desi_v8", "tims_v8")
    is_reanalysis = active in ("desi_cluster_filter", "tims_cluster_filter")
    is_desi_umap = active == "desi_v8"
    is_tims_umap = active == "tims_v8"
    is_tims_reanalysis = active == "tims_cluster_filter"

    umap_style = {} if is_umap else {"display": "none"}
    reanalysis_style = {} if is_reanalysis else {"display": "none"}
    tims_ion_style = {} if is_tims_umap else {"display": "none"}
    tims_reanalysis_ion_style = {} if is_tims_reanalysis else {"display": "none"}
    extra_folders_style = {"marginTop": "10px"} if is_tims_umap else {"display": "none"}
    # 標準フロー推奨バナー: 選択中の手法のものだけ表示（両方同時には出さない）
    desi_banner_style = {} if is_desi_umap else {"display": "none"}
    tims_banner_style = {} if is_tims_umap else {"display": "none"}

    return (umap_style, reanalysis_style, tims_ion_style, tims_reanalysis_ion_style,
            extra_folders_style, desi_banner_style, tims_banner_style)


# ---------------------------------------------------------------------------
# 再解析で実際に使う条件の表示
# ---------------------------------------------------------------------------

@callback(
    [Output("reanalysis_inherited_note", "children"),
     Output("reanalysis_inherited_note", "style")],
    [Input("analysis_method", "value"),
     Input("analysis_method_tims", "value"),
     Input("normalize_input", "value"),
     Input("norm_mode", "value"),
     Input("mz_align_ppm", "value"),
     Input("use_annotation_check", "value"),
     Input("desi_use_roi_as_sample", "value"),
     Input("desi_roi_filter_store", "data")],
)
def update_reanalysis_inherited_note(desi_val, tims_val, normalize_input,
                                     norm_mode, mz_align_ppm,
                                     use_annotation_check,
                                     desi_use_roi_as_sample=False,
                                     desi_roi_filter=None):
    """再解析で実際に使われる前処理・UMAP 条件を再解析パネルに出す。

    ★ ver58.0 (デバッグ総点検 A-6/A-7/A-10): これらを決める入力欄は
      `umap_settings_panel` の中にあり、**再解析中は画面から隠れる**。
      隠れた欄の値がそのまま計算に使われるので、何が使われるのかを
      ここに実値で出す。表示専用（value を持たない）ため保存経路は不要。

      正規化の行は DESI 再解析のときだけ出す。TIMS 再解析には専用の
      正規化欄（`normalize_input_reanalysis`）があり、両方を出すと
      画面に矛盾する 2 つの主張が並ぶため。
    """
    active = desi_val or tims_val or "desi_v8"
    if active not in ("desi_cluster_filter", "tims_cluster_filter"):
        return "", {"display": "none"}

    rows = ["この再解析で使う条件（画面の値がそのまま使われます）"]
    if active == "desi_cluster_filter":
        if normalize_input == "OFF":
            rows.append(f"正規化: OFF（正規化済み入力）／変換: {norm_mode or 'log1p'}")
        else:
            rows.append("正規化: ON（LogNormalize を実行）")
        # ★ ver58.0 (A-5): ROI 分割は再解析でもやり直す。何が使われるかを出す。
        if desi_use_roi_as_sample:
            _roi = list(desi_roi_filter or [])
            rows.append("ROI: ROI ごとに別サンプルとして分割し直す"
                        + (f"（対象: {', '.join(_roi)}）" if _roi else "（全 ROI）"))
        else:
            rows.append("ROI: 分割しない（ファイル全体を 1 サンプルとする）")
    else:
        # `x or 0` にしない: 0 は「無効」という正当な指定で、既定値と区別が要る
        # （既定も 0 なので結果は同じだが、この型を広げないこと自体が規約）。
        _ppm = mz_align_ppm if mz_align_ppm is not None else 0
        rows.append(f"m/z アライメント: {_ppm} ppm"
                    + ("（0 = 無効）" if not _ppm else ""))
        rows.append("化合物名: "
                    + ("変換元 CSV 由来を使う"
                       if "embedded" in list(use_annotation_check or [])
                       else "変換元 CSV 由来は使わない"))

    children = [
        html.Div(rows[0], style={"fontWeight": "600"}),
        *[html.Div(t) for t in rows[1:]],
        html.Div("UMAP: 上の「PreFlight 診断 / UMAP ハイパーパラメータ」の値"
                 "（n.neighbors / min.dist / metric=cosine 等）を使います。",
                 style={"fontSize": "0.85em"}),
    ]
    style = {"background": "#eef5ff", "padding": "8px 10px",
             "borderRadius": "5px", "marginBottom": "10px",
             "fontSize": "0.85rem"}
    return children, style


# ---------------------------------------------------------------------------
# 正規化トグルの既定切替・NORM_MODE有効化
# ---------------------------------------------------------------------------

@callback(
    Output("normalize_input", "value"),
    Output("normalize_default_owner", "data"),
    Input("analysis_method", "value"),
    Input("analysis_method_tims", "value"),
    State("normalize_input", "value"),
    State("normalize_default_owner", "data"),
    prevent_initial_call=True,
)
def set_default_normalize(desi_val, tims_val, current, last_default):
    """解析法に応じて正規化の既定を切替。**ただし手動変更は尊重する**。

    TIMS(SCiLS RMS等で正規化済み入力)は既定 OFF＝二重正規化を回避。
    DESI(生データ)は既定 ON。
    （active 判定は toggle_settings_panels と同じ desi 優先ロジック）

    ★ ver57.5 (デバッグ総点検 §5.3): 従来は現在値を見ずに方式既定を
      書き込んでいた。画面には「解析法に応じて自動切替（**手動変更可**）」と
      書いてあるのに、**手動変更が一度も残らなかった**。
      とくに「再解析へ送る」では、再解析は `tims_v8` ではないため
      「生データ扱い」で ON に化け、正規化済みの入力へ二重正規化がかかる。
      しかも書き戻された値は run_analysis の自動保存で last_settings.json に
      保存し直されるので、再起動しても手動選択が戻らなかった。
      （ver56.5 で直したのは「保存されない」側。書き戻しはこちら）

      直前に自動で入れた既定値を控え、**現在値がその既定のままのときだけ**
      切り替える。違っていれば利用者が選んだ値なので触らない。
    """
    # ★ ver57.5: 既定の判定に TIMS 再解析を含める。従来は `== "tims_v8"` だけを
    #   見ていたため、**再解析へ切り替えた瞬間に「生データ扱い」で ON へ化けて**
    #   いた。同じファイルの update_sample_selector / toggle_settings_panels は
    #   元から ("tims_v8", "tims_cluster_filter") で括っており、ここだけ漏れていた。
    #   TIMS は SCiLS の RMS で正規化済みなので、再解析でも既定は OFF が正しい。
    active = desi_val or tims_val or "desi_v8"
    new_default = "OFF" if active in ("tims_v8", "tims_cluster_filter") else "ON"
    if current is not None and last_default is not None and current != last_default:
        return no_update, no_update      # 手で選んだ値・復元した値は守る
    return new_default, new_default


@callback(
    Output("norm_mode", "disabled"),
    Input("normalize_input", "value"),
)
def toggle_norm_mode_enabled(normalize_input):
    """NORM_MODE は正規化 OFF のときのみ有効。"""
    return normalize_input != "OFF"


@callback(
    Output("norm_mode_reanalysis", "disabled"),
    Input("normalize_input_reanalysis", "value"),
)
def toggle_norm_mode_reanalysis_enabled(normalize_input_reanalysis):
    """再解析の NORM_MODE は正規化 OFF のときのみ有効。"""
    return normalize_input_reanalysis != "OFF"


# ---------------------------------------------------------------------------
# RDS途中再開パネル表示
# ---------------------------------------------------------------------------

@callback(
    Output("resume_rds_panel", "style"),
    Input("resume_rds", "value"),
)
def toggle_resume_panel(resume):
    if resume:
        return {"marginTop": "10px"}
    return {"display": "none"}


# ---------------------------------------------------------------------------
# 再解析の途中再開パネル表示 (ver46.0)
# ---------------------------------------------------------------------------

@callback(
    Output("resume_reanalysis_panel", "style"),
    Input("resume_reanalysis", "value"),
)
def toggle_resume_reanalysis_panel(resume):
    if resume:
        return {"marginTop": "10px"}
    return {"display": "none"}


# ---------------------------------------------------------------------------
# データフォルダ → サンプル一覧
# ---------------------------------------------------------------------------

def _sample_group_block(folder: str, options: list[dict], values: list[str],
                        show_header: bool) -> "html.Div":
    """フォルダ 1 つ分のチェックリストを見出し付きで組み立てる。"""
    children = []
    if show_header:
        children.append(html.Small(
            f"\U0001f4c1 {Path(folder).name or folder}",
            className="fw-bold d-block text-muted",
            title=folder,
            style={"fontSize": "0.75rem"},
        ))
    children.append(dbc.Checklist(
        # index はフォルダのフルパス。フォルダ名だけだと別階層の同名フォルダで
        # ID が衝突し、Dash のパターンマッチングが両方まとめて拾ってしまう。
        id={"type": "sample_check", "index": folder},
        options=options,
        value=values,          # デフォルト全選択（従来どおり）
        className="ms-2" if show_header else "",
    ))
    return html.Div(children, className="mb-1")


@callback(
    Output("sample_selector", "children"),
    [Input("data_folder", "value"),
     Input("analysis_method", "value"),
     Input("analysis_method_tims", "value"),
     Input("extra_data_folders_store", "data")],
)
def update_sample_selector(data_folder, desi_method, tims_method, extra_folders):
    """データフォルダ + 追加データフォルダのサンプル一覧を作る。

    ★ ver64.0: TIMS はフォルダごとに区切って並べ、チェックの値を
      **ファイルのフルパス**にした。従来は `list_tims_files_multi` が stem で
      重複排除した名前の一覧を 1 本のチェックリストで出していたため、
      別フォルダに同名ファイルがあると

        - 画面には 1 個しか出ないのに INPUT_PATHS には 2 本入る
        - 片方だけチェックを外せない（外すと両方消える）
        - 切片(annotation)の候補は先に並ぶフォルダ側しか解決されず、
          もう一方は `ANNOTATION_FILTER に一致する spot がありません` で
          解析ごと落ちる

      という 3 つの食い違いが同時に起きていた。どのフォルダのどのファイルを
      選んだのかが画面と解析で 1 対 1 に対応するようにする。
      DESI は従来どおり 1 フォルダ・stem 単位（R が data_folder/<stem>.txt を
      決め打ちで読むため、パスを値にすると逆に対応が取れない）。
    """
    if not data_folder or not Path(data_folder).is_dir():
        return html.Div("データフォルダを指定してください", className="text-muted")

    active = desi_method or tims_method or "desi_v8"

    if active in ("tims_v8", "tims_cluster_filter"):
        groups = list_tims_folder_groups([data_folder] + list(extra_folders or []))
        blocks, total = [], 0
        # 見出しは「フォルダが 2 つ以上あるとき」だけ出す。1 つのときに出すと
        # 従来の画面に余計な行が増えるだけで情報が増えない。
        show_header = len([g for g in groups if g["paths"]]) > 1
        for g in groups:
            if not g["paths"]:
                continue
            total += len(g["paths"])
            opts = [{"label": f" {Path(p).stem}", "value": p} for p in g["paths"]]
            blocks.append(_sample_group_block(
                g["folder"], opts, [o["value"] for o in opts], show_header))
        if not blocks:
            return html.Div("対応ファイルが見つかりません", className="text-warning")
        if show_header:
            # ★ ver64.0: 上の「N ファイル検出」バッジは基準フォルダしか数えない。
            #   追加フォルダを足しても数字が動かないので「追加できていない」ように
            #   見える、というのが利用者からの申告そのものだった。合計をここに出す。
            blocks.append(html.Small(
                f"合計 {total} ファイル（{len(blocks)} フォルダ）",
                className="text-muted d-block",
                style={"fontSize": "0.75rem", "marginTop": "2px"},
            ))
        return blocks

    samples = list_msi_files(data_folder)
    if not samples:
        return html.Div("対応ファイルが見つかりません", className="text-warning")
    return _sample_group_block(
        data_folder,
        [{"label": f" {s}", "value": s} for s in samples],
        samples,
        False,
    )


# ---------------------------------------------------------------------------
# サンプルのチェック → selected_samples_store / selected_sample_paths_store 同期
# 動的生成の Checklist を静的 Store にブリッジ
# ---------------------------------------------------------------------------

def _sample_value_is_path(value: str) -> bool:
    """チェックリストの値がフルパスか（TIMS）サンプル名か（DESI）を見分ける。

    `Path.stem` には区切り文字が入らないので、区切り文字の有無で判別できる。
    解析手法を State で受け取る方式にすると、手法を切り替えた直後の 1 回だけ
    「新しい手法 × 古いチェックリスト」の組で解釈してしまう。
    """
    return "/" in value or "\\" in value


@callback(
    [Output("selected_samples_store", "data"),
     Output("selected_sample_paths_store", "data")],
    Input({"type": "sample_check", "index": ALL}, "value"),
)
def sync_selected_samples(all_values):
    """全フォルダ分のチェックを 1 つにまとめる。

    - `selected_samples_store`      … サンプル名 (stem)。既存の読み手
      （キャリブレーションのサンプル選択・DESI・再解析）はこちらを使う。
    - `selected_sample_paths_store` … 選んだファイルのフルパス。TIMS の
      INPUT_PATHS と切片選択はこちらを正とする（同名ファイルを区別するため）。
    """
    paths, names = [], []
    for vals in all_values or []:
        for v in (vals or []):
            if _sample_value_is_path(v):
                paths.append(v)
                names.append(Path(v).stem)
            else:
                names.append(v)
    # 同名ファイルを 2 フォルダから選んだ場合、名前は 1 つに畳む（R 側の
    # サンプル名は basename 由来なので、名前の一覧としては重複させない）。
    uniq_names = list(dict.fromkeys(names))
    return uniq_names, paths


# ---------------------------------------------------------------------------
# Annotation（切片）選択 — TIMS Parquet 内の annotation 列から生成
# ---------------------------------------------------------------------------

@callback(
    [Output("annotation_selector", "children"),
     Output("annotation_filter_store", "data")],
    [Input("selected_sample_paths_store", "data"),
     Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
    prevent_initial_call=True,
)
def update_annotation_selector(selected_paths, desi_method, tims_method):
    """選択されたTIMSファイルごとにannotation一覧をチェックボックスで表示

    ★ ver64.0: 入力をサンプル名 (stem) からファイルのフルパスに変えた。
      従来は `find_tims_file_path_multi(全フォルダ, stem)` で**先に見つかった
      1 本**を解決していたため、別フォルダの同名ファイルは切片の候補に一度も
      現れなかった。ANNOTATION_FILTER は R 側で全ファイルに一律で適用される
      ので、候補に出なかったファイルは 0 件一致となり
      `ANNOTATION_FILTER に一致する spot がありません` で解析ごと停止する。
      （ver56.5 の F-C02-1 は「追加フォルダを見ていない」側だけを直しており、
      同名ファイルの取りこぼしは残っていた。）
      チェックボックスの id にもフルパスを使う。stem を使うと同名ファイルで
      **Dash の ID が重複**し、2 つのチェックが 1 つとして扱われる。
    """
    active = desi_method or tims_method or "desi_v8"

    # TIMS UMAP以外では非表示
    if active != "tims_v8":
        return [], None

    if not selected_paths:
        return [], None

    children = []
    all_annotations = []

    for file_path in selected_paths:
        annotations = read_parquet_annotations(file_path)
        if not annotations:
            continue

        all_annotations.extend(annotations)

        # ファイル名ラベル + チェックボックス
        children.append(html.Div([
            html.Small(f"\U0001F4C4 {Path(file_path).stem}",
                       className="fw-bold", title=file_path),
            dbc.Checklist(
                id={"type": "annotation_check", "index": file_path},
                options=[{"label": f" {a}", "value": a} for a in annotations],
                value=annotations,  # デフォルト全選択
                inline=True,
                className="ms-2",
            ),
        ], className="mb-1"))

    if not children:
        return [], None

    ui = [
        html.Hr(className="my-1"),
        html.Small("Annotation（切片）選択:", className="fw-bold"),
    ] + children

    return ui, sorted(set(all_annotations))


@callback(
    Output("annotation_filter_store", "data", allow_duplicate=True),
    Input({"type": "annotation_check", "index": ALL}, "value"),
    prevent_initial_call=True,
)
def sync_annotation_to_store(all_values):
    """パターンマッチング: 全annotation_checkの選択値をStoreに集約。

    ★ ver58.1 (デバッグ総点検 B-4): 「部品が無い」と「部品はあるが全部外した」を
      型で区別する。従来はどちらも None を返しており、下流の truthy 判定が
      None を「フィルタ指定なし＝全採用」と読むため、**全部外すと逆に全部が
      対象になっていた**。空リストは「1 つも選んでいない」を意味し、
      実行前のチェックがこれを見て止める。
    """
    if not all_values:
        return None
    merged = []
    for vals in all_values:
        if vals:
            merged.extend(vals)
    return sorted(set(merged))


# ---------------------------------------------------------------------------
# DESI ROI 選択 UI (.txt の最終列が ROI 文字列の場合)
# ---------------------------------------------------------------------------

@callback(
    [Output("desi_roi_selector", "children"),
     Output("desi_roi_filter_store", "data")],
    # ★ ver64.0: サンプルのチェックリストはフォルダごとのパターンマッチング
    #   部品になったので、静的 ID `selected_samples` は存在しない。集約済みの
    #   Store を見る（DESI は 1 フォルダなので値は従来どおり stem）。
    [Input("selected_samples_store", "data"),
     Input("data_folder", "value"),
     Input("analysis_method", "value")],
    prevent_initial_call=True,
)
def update_desi_roi_selector(selected_samples, data_folder, desi_method):
    """選択された DESI ファイルごとに ROI 一覧をチェックボックスで表示。

    TIMS の annotation_selector と同じ pattern-matching 構造。
    各ファイルの最終列 ROI を読み取り、見つかった ROI をチェックボックスとして
    並べる。デフォルトは全選択。
    """
    # DESI モード以外では非表示 (空)
    if desi_method != "desi_v8":
        return [], None
    if not selected_samples or not data_folder or not Path(data_folder).is_dir():
        return [], None

    children = []
    all_rois = []

    for sample in selected_samples:
        # `.txt` が無くても read_desi_roi_list 側で Excel/CSV から自動変換して読む。
        file_path = Path(data_folder) / f"{sample}.txt"
        rois = read_desi_roi_list(str(file_path))
        if not rois:
            continue

        all_rois.extend(rois)
        children.append(html.Div([
            html.Small(f"\U0001F4C4 {sample}", className="fw-bold"),
            dbc.Checklist(
                id={"type": "desi_roi_check", "index": sample},
                options=[{"label": f" {r}", "value": r} for r in rois],
                value=rois,  # デフォルト全選択
                inline=True,
                className="ms-2",
            ),
        ], className="mb-1"))

    if not children:
        return [], None

    ui = [
        html.Hr(className="my-1"),
        html.Small("ROI 選択 (DESI):", className="fw-bold"),
    ] + children

    return ui, sorted(set(all_rois))


@callback(
    Output("desi_roi_filter_store", "data", allow_duplicate=True),
    Input({"type": "desi_roi_check", "index": ALL}, "value"),
    prevent_initial_call=True,
)
def sync_desi_roi_to_store(all_values):
    """パターンマッチング: 全 desi_roi_check の選択値を Store に集約。

    ★ ver58.1 (デバッグ総点検 B-4): 「部品が無い」と「部品はあるが全部外した」を
      型で区別する。従来はどちらも None を返しており、下流の truthy 判定が
      None を「フィルタ指定なし＝全採用」と読むため、**全部外すと逆に全部が
      対象になっていた**。空リストは「1 つも選んでいない」を意味し、
      実行前のチェックがこれを見て止める。
    """
    if not all_values:
        return None
    merged = []
    for vals in all_values:
        if vals:
            merged.extend(vals)
    return sorted(set(merged))


@callback(
    Output("sample_selector_reanalysis", "children"),
    [Input("reanalysis_data_folder", "value"),
     Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
)
def update_reanalysis_sample_selector(data_folder, desi_method, tims_method):
    if not data_folder or not Path(data_folder).is_dir():
        return html.Div("データフォルダを指定してください", className="text-muted")

    active = desi_method or tims_method or "desi_v8"
    if active in ("tims_v8", "tims_cluster_filter"):
        samples = list_tims_files(data_folder)
    else:
        samples = list_msi_files(data_folder)

    if not samples:
        return html.Div("対応ファイルが見つかりません", className="text-warning")

    return dbc.Checklist(
        id="selected_samples_reanalysis",
        options=[{"label": s, "value": s} for s in samples],
        value=samples,
    )


# ---------------------------------------------------------------------------
# 再解析用 Annotation（切片）選択 — TIMS Cluster Filter のみ
# ---------------------------------------------------------------------------

@callback(
    [Output("annotation_selector_reanalysis", "children"),
     Output("annotation_filter_store_reanalysis", "data")],
    [Input("selected_samples_reanalysis", "value"),
     Input("reanalysis_data_folder", "value"),
     Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
    prevent_initial_call=True,
)
def update_reanalysis_annotation_selector(selected_samples, data_folder,
                                           desi_method, tims_method):
    """再解析側: 選択されたTIMSファイルごとにannotation一覧をチェックボックスで表示"""
    active = desi_method or tims_method or "desi_v8"

    if active != "tims_cluster_filter":
        return [], None

    if not selected_samples or not data_folder or not Path(data_folder).is_dir():
        return [], None

    children = []
    all_annotations = []

    for sample in selected_samples:
        file_path = find_tims_file_path(data_folder, sample)
        if not file_path:
            continue
        annotations = read_parquet_annotations(file_path)
        if not annotations:
            continue

        all_annotations.extend(annotations)

        children.append(html.Div([
            html.Small(f"\U0001F4C4 {sample}", className="fw-bold"),
            dbc.Checklist(
                id={"type": "annotation_check_reanalysis", "index": sample},
                options=[{"label": f" {a}", "value": a} for a in annotations],
                value=annotations,
                inline=True,
                className="ms-2",
            ),
        ], className="mb-1"))

    if not children:
        return [], None

    ui = [
        html.Hr(className="my-1"),
        html.Small("Annotation（切片）選択:", className="fw-bold"),
    ] + children

    return ui, sorted(set(all_annotations))


@callback(
    Output("annotation_filter_store_reanalysis", "data", allow_duplicate=True),
    Input({"type": "annotation_check_reanalysis", "index": ALL}, "value"),
    prevent_initial_call=True,
)
def sync_reanalysis_annotation_to_store(all_values):
    """再解析側: 全annotation_check_reanalysisの選択値をStoreに集約。

    ★ ver58.1 (デバッグ総点検 B-4): 「部品が無い」と「部品はあるが全部外した」を
      型で区別する。従来はどちらも None を返しており、下流の truthy 判定が
      None を「フィルタ指定なし＝全採用」と読むため、**全部外すと逆に全部が
      対象になっていた**。空リストは「1 つも選んでいない」を意味し、
      実行前のチェックがこれを見て止める。
    """
    if not all_values:
        return None
    merged = []
    for vals in all_values:
        if vals:
            merged.extend(vals)
    return sorted(set(merged))


# ---------------------------------------------------------------------------
# TIMS/DESI モード切替時に再解析パラメータをデフォルトにリセット
# ---------------------------------------------------------------------------

@callback(
    [Output("reanalysis_ion_mode", "value", allow_duplicate=True),
     Output("reanalysis_tolerance_mz", "value", allow_duplicate=True)],
    [Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
    State("settings_restore_pending", "data"),
    prevent_initial_call=True,
)
def reset_reanalysis_defaults(desi_val, tims_val, restore_pending=False):
    """TIMS/DESIモード切替時に再解析パラメータをデフォルトにリセット。

    ★ ver58.1 (デバッグ総点検 B-2): 復元中は何もしない。
      サブプロジェクトの「解析」やプリセット読込は、保存値を書き戻すのと
      同じレスポンスで analysis_method も書く。従来はここにガードが無く、
      **再解析のイオンモードと m/z 許容誤差だけが既定 (Positive / 0.01) に
      戻っていた**。他の項目は正しく戻るので、この 2 つだけ戻っていることに
      気づかないまま、保存時と違う条件で再解析が走る。
    """
    if restore_pending:
        return no_update, no_update
    from app.config import DEFAULT_ION_MODE, DEFAULT_TOLERANCE_MZ
    return DEFAULT_ION_MODE, DEFAULT_TOLERANCE_MZ


# ---------------------------------------------------------------------------
# データフォルダ自動切替（DESI/TIMS）
# ---------------------------------------------------------------------------

def _current_trigger_id():
    """発火元の component id を返す。callback の外から呼ばれたら None。

    ★ ver62.4: `ctx.triggered_id` は callback 実行中以外では
      `MissingCallbackContextException` を投げる。`auto_switch_data_folder` は
      テストから直接呼ばれる契約があり
      （`test_restore_is_not_overwritten_by_defaults`）、素で参照すると
      **その契約を壊す**。しかも全件実行では他のテストが張ったコンテキストに
      救われて通ってしまい、単独実行でしか落ちない（PR #172 のレビュー指摘）。
      読めないときは None にして、従来どおりの解釈へフォールバックする。
    """
    try:
        return ctx.triggered_id
    except Exception:  # noqa: BLE001 — callback 外／未確定はすべて「不明」
        return None


def _selected_analysis_method(desi_val, tims_val, trigger_id):
    """利用者が実際に触った方の解析手法を返す（純関数）。

    ★ ver62.4: 従来は `desi_val or tims_val` だった。しかし選択欄の既定値は
      非対称で、DESI 側 (`analysis_method`) は `"desi_v8"`、TIMS 側
      (`analysis_method_tims`) は `None`（`sidebar.py`）。DESI は常に真なので、
      **両方に値が入っている瞬間は必ず DESI が勝つ**。排他クリア
      (`clear_desi_on_tims_select`) は別の callback なので、TIMS を選んだ直後の
      1 周目ではまだ DESI 側が残っており、**TIMS の作業中に DESI のデータ
      フォルダが書き込まれる**。訂正は 2 周目に入るが、
      `settings_restore_pending` が立っているとその 2 周目は降りるため
      誤った値が残る。正しい値になるかが callback の実行順に依存していた。

      発火元で決めれば順序に依存しない。発火元が分からないとき
      （callback 外からの直接呼び出し等）は従来の解釈に倒す。
    """
    if trigger_id == "analysis_method":
        return desi_val
    if trigger_id == "analysis_method_tims":
        return tims_val
    return desi_val or tims_val


@callback(
    Output("data_folder", "value", allow_duplicate=True),
    [Input("analysis_method", "value"),
     Input("analysis_method_tims", "value")],
    [State("default_desi_data_folder", "value"),
     State("default_tims_data_folder", "value"),
     State("settings_restore_pending", "data")],
    prevent_initial_call=True,
)
def auto_switch_data_folder(desi_val, tims_val, desi_default, tims_default,
                            restore_pending=False):
    """解析手法に応じてデータフォルダを既定へ振り直す。

    ★ ver58.1 (デバッグ総点検 B-3): 復元中は何もしない。
      サブプロジェクトの「解析」・プリセット読込・「再解析へ送る」は
      analysis_method を書くため、従来はここが必ず発火して
      **復元されたデータフォルダをサイドバーの既定で上書き**していた。
      出力先やしきい値は正しく戻るので気づきにくく、そのまま実行すると
      **別の場所のデータを解析してしまう**。

    ★ ver62.4: どちらが選ばれているかの判定を `_selected_analysis_method` へ
      切り出した（経緯はそちらの docstring）。
    """
    if restore_pending:
        return no_update
    active = _selected_analysis_method(desi_val, tims_val, _current_trigger_id())
    if active in ("desi_v8", "desi_cluster_filter"):
        return desi_default or DEFAULT_DESI_DATA_FOLDER
    elif active in ("tims_v8", "tims_cluster_filter"):
        return tims_default or DEFAULT_TIMS_DATA_FOLDER
    return no_update


# ---------------------------------------------------------------------------
# Adductフィルター自動切替（イオンモード変更時）
# ---------------------------------------------------------------------------

@callback(
    Output("adduct_filter", "value"),
    Input("ion_mode", "value"),
    State("settings_restore_pending", "data"),
    prevent_initial_call=True,
)
def auto_switch_adduct(ion_mode, restore_pending=False):
    """イオンモードに応じて付加イオンの既定を入れ直す。

    ★ ver58.1 (デバッグ総点検 B-1): 復元中は何もしない。
      プリセットは ion_mode を必ず含むので、読み込むと必ずここが発火し、
      **保存しておいた付加イオンの組み合わせが既定で塗り潰されていた**。
      画面には「✅ 読み込みました」と出るため、違う条件で m/z 照合が
      行われていることに気づけない。
    """
    if restore_pending:
        return no_update
    from app.config import adducts_for_ion_mode
    return adducts_for_ion_mode(ion_mode)


@callback(
    Output("reanalysis_adduct_filter", "value"),
    Input("reanalysis_ion_mode", "value"),
    State("settings_restore_pending", "data"),
    prevent_initial_call=True,
)
def auto_switch_reanalysis_adduct(ion_mode, restore_pending=False):
    """再解析側も同じ（★ ver58.1 / B-1）。"""
    if restore_pending:
        return no_update
    from app.config import adducts_for_ion_mode
    return adducts_for_ion_mode(ion_mode)


# ---------------------------------------------------------------------------
# 復元中フラグを降ろす
# ---------------------------------------------------------------------------

@callback(
    Output("settings_restore_pending", "data", allow_duplicate=True),
    Input("settings_restore_pending", "data"),
    prevent_initial_call=True,
)
def clear_settings_restore_pending(pending):
    """復元の直後にフラグを降ろす。

    ★ ver58.1: 降ろし忘れると、以後の**手動の**イオンモード変更や
      解析法の切替まで効かなくなる（復元を守るつもりで手動操作を殺す）。
      復元は 1 レスポンスで終わる一過性の出来事なので、その次の周回で降ろす。
      降りている旗に False を書き直すと往復し続けるため no_update を返す。
    """
    if pending:
        return False
    return no_update


# ---------------------------------------------------------------------------
# デフォルト設定リセット
# ---------------------------------------------------------------------------

@callback(
    [Output("desi_v8_script_path", "value", allow_duplicate=True),
     Output("desi_cluster_filter_script_path", "value", allow_duplicate=True),
     Output("tims_v8_script_path", "value", allow_duplicate=True),
     Output("tims_cluster_filter_script_path", "value", allow_duplicate=True)],
    Input("reset_script_paths", "n_clicks"),
    prevent_initial_call=True,
)
def reset_script_paths(n):
    return (
        str(DESI_V8_TEMPLATE_PATH),
        str(DESI_CLUSTER_FILTER_PATH),
        str(TIMS_V8_TEMPLATE_PATH),
        str(TIMS_CLUSTER_FILTER_PATH),
    )


@callback(
    [Output("default_desi_data_folder", "value", allow_duplicate=True),
     Output("default_annotation_file", "value", allow_duplicate=True),
     Output("default_desi_output_dir", "value", allow_duplicate=True)],
    Input("reset_desi_defaults", "n_clicks"),
    prevent_initial_call=True,
)
def reset_desi_defaults(n):
    return DEFAULT_DESI_DATA_FOLDER, DEFAULT_ANNOTATION_FILE_PATH, str(DESI_DATA_DIR)


@callback(
    [Output("default_tims_data_folder", "value", allow_duplicate=True),
     Output("default_annotation_csv", "value", allow_duplicate=True),
     Output("default_tims_output_dir", "value", allow_duplicate=True)],
    Input("reset_tims_defaults", "n_clicks"),
    prevent_initial_call=True,
)
def reset_tims_defaults(n):
    return DEFAULT_TIMS_DATA_FOLDER, DEFAULT_ANNOTATION_CSV_PATH, str(TIMS_DATA_DIR)


@callback(
    Output("default_output_dir", "value", allow_duplicate=True),
    Input("reset_output_defaults", "n_clicks"),
    prevent_initial_call=True,
)
def reset_output_defaults(n):
    return str(APP_BASE_DIR)


# ---------------------------------------------------------------------------
# デフォルト設定適用
# ---------------------------------------------------------------------------

# ver56.7: 「適用」の結果を必ず伝える。
#   欄が空のまま適用すると `x or no_update` が空文字を飲んで何も起きず、
#   成功メッセージもエラーも出ないため **ボタンが壊れているように見えていた**
#   (欄に値が入っていれば正常に動く)。
#   「空欄で既存の指定を潰さない」方針自体は正しいので変えない。変えるのは
#   「黙って終わる」ことだけ。欄を空へ戻したい場合は明示的な操作を別に用意する。
def _apply_defaults_status(applied, skipped):
    """適用した項目数・空欄で見送った項目数から文言を作る。"""
    if applied and skipped:
        return html.Span(
            f"✓ {applied} 項目を適用しました（{skipped} 項目は空欄のため据え置き）",
            style={"color": "#e67e22"})
    if applied:
        return html.Span(f"✓ {applied} 項目を適用しました",
                         style={"color": "#28a745"})
    return html.Span("空欄のため適用しませんでした（値を入れてから押してください）",
                     style={"color": "#e67e22"})


def _apply_values(values):
    """(出力値のタプル, 状況表示) を返す。空欄は no_update のまま。"""
    outs, applied, skipped = [], 0, 0
    for v in values:
        if v and str(v).strip():
            outs.append(v)
            applied += 1
        else:
            outs.append(no_update)
            skipped += 1
    return tuple(outs), _apply_defaults_status(applied, skipped)


@callback(
    [Output("data_folder", "value", allow_duplicate=True),
     Output("annotation_path", "value", allow_duplicate=True),
     Output("output_dir", "value", allow_duplicate=True),
     Output("apply_defaults_status", "children", allow_duplicate=True)],
    Input("apply_desi_defaults", "n_clicks"),
    [State("default_desi_data_folder", "value"),
     State("default_annotation_file", "value"),
     State("default_desi_output_dir", "value")],
    prevent_initial_call=True,
)
def apply_desi_defaults(n, desi_folder, annotation_file, desi_output):
    if not n:
        return no_update, no_update, no_update, no_update
    try:
        save_last_settings({
            "default_desi_data_folder": desi_folder,
            "default_annotation_file": annotation_file,
            "default_desi_output_dir": desi_output,
        })
    except Exception as e:
        warn_user(f"DESI初期設定の保存に失敗: {e}")
    outs, status = _apply_values([desi_folder, annotation_file, desi_output])
    return outs + (status,)


@callback(
    [Output("data_folder", "value", allow_duplicate=True),
     Output("annotation_path", "value", allow_duplicate=True),
     Output("output_dir", "value", allow_duplicate=True),
     Output("apply_defaults_status", "children", allow_duplicate=True)],
    Input("apply_tims_defaults", "n_clicks"),
    [State("default_tims_data_folder", "value"),
     State("default_annotation_csv", "value"),
     State("default_tims_output_dir", "value")],
    prevent_initial_call=True,
)
def apply_tims_defaults(n, tims_folder, annotation_csv, tims_output):
    if not n:
        return no_update, no_update, no_update, no_update
    try:
        save_last_settings({
            "default_tims_data_folder": tims_folder,
            "default_annotation_csv": annotation_csv,
            "default_tims_output_dir": tims_output,
        })
    except Exception as e:
        warn_user(f"TIMS初期設定の保存に失敗: {e}")
    outs, status = _apply_values([tims_folder, annotation_csv, tims_output])
    return outs + (status,)


@callback(
    [Output("output_dir", "value", allow_duplicate=True),
     Output("apply_defaults_status", "children", allow_duplicate=True)],
    Input("apply_output_defaults", "n_clicks"),
    State("default_output_dir", "value"),
    prevent_initial_call=True,
)
def apply_output_defaults(n, output_dir):
    if not n:
        return no_update, no_update
    try:
        save_last_settings({"default_output_dir": output_dir})
    except Exception as e:
        warn_user(f"出力設定の保存に失敗: {e}")
    outs, status = _apply_values([output_dir])
    return outs + (status,)


# ---------------------------------------------------------------------------
# ファイルブラウザモーダル
# ---------------------------------------------------------------------------

# ブラウズボタン ID → (mode, target_input_id) のマッピング
_BROWSE_BUTTONS = {
    "browse_folder": ("folder", "data_folder"),
    "browse_annotation": ("file", "annotation_path"),
    "browse_rds_folder": ("folder", "rds_folder"),
    "browse_rds_folder_reanalysis": ("folder", "rds_folder_reanalysis"),
    "browse_resume_reanalysis_dir": ("folder", "resume_reanalysis_dir"),
    "browse_reanalysis_folder": ("folder", "reanalysis_data_folder"),
    "browse_reanalysis_annotation": ("file", "reanalysis_annotation_path"),
    "browse_output": ("folder", "output_dir"),
    "browse_interactive_result": ("folder", "interactive_result_folder"),
    "browse_interactive_msi": ("folder", "interactive_msi_folder"),
    # サイドバーの参照ボタン
    "browse_desi_v8_script": ("file", "desi_v8_script_path"),
    "browse_desi_cluster_script": ("file", "desi_cluster_filter_script_path"),
    "browse_tims_v8_script": ("file", "tims_v8_script_path"),
    "browse_tims_cluster_script": ("file", "tims_cluster_filter_script_path"),
    "browse_default_desi_folder": ("folder", "default_desi_data_folder"),
    "browse_default_annotation_desi": ("file", "default_annotation_file"),
    "browse_default_desi_output": ("folder", "default_desi_output_dir"),
    "browse_default_tims_folder": ("folder", "default_tims_data_folder"),
    "browse_default_annotation": ("file", "default_annotation_csv"),
    "browse_default_tims_output": ("folder", "default_tims_output_dir"),
    "browse_default_output": ("folder", "default_output_dir"),
    "browse_int_cal_annotation": ("file", "int_cal_annotation_path"),
    # 再アノテーション
    "browse_reann_annotation": ("file", "reann_annotation_path"),
    # プロジェクト復元スキャンフォルダ
    "browse_restore_scan_folder": ("folder", "restore_scan_folder"),
    # データ管理サブタブ: 移動元フォルダ (退避したい /app 直下も選べるよう起点は APP_BASE_DIR)
    "dm_browse_move_src": ("folder", "dm_move_src"),
    # データ管理サブタブ: 移動先フォルダ (起点は解析出力。上部ショートカットで 4 か所へ飛べる)
    "dm_browse_move_dest": ("folder", "dm_move_dest_path"),
    # ver3.9: プロジェクト編集モーダルのサムネ画像パス
    "browse_edit_thumbnail": ("file", "edit_project_thumbnail"),
    # TIMS 追加データフォルダ
    "btn_add_extra_folder": ("folder", "extra_folder_pending_store"),
    # SCiLS 変換モーダル
    "browse_scils_input_folder": ("folder", "scils_input_folder"),
    "browse_scils_output_folder": ("folder", "scils_output_folder"),
    # 環境設定 (.env) モーダル
    "browse_env_tims_data_dir": ("folder", "env_tims_data_dir"),
    "browse_env_desi_data_dir": ("folder", "env_desi_data_dir"),
    "browse_env_r_home": ("folder", "env_r_home"),
    # RDS メンテナンスモーダル
    "browse_rds_maint_folder": ("folder", "rds_maint_folder"),
    # Parquet 再パックモーダル
    "browse_parquet_maint_folder": ("folder", "parquet_maint_folder"),
}

# 全対象入力フィールドIDの一覧（_BROWSE_BUTTONSのvalue[1]を収集）
_ALL_TARGET_IDS = list(dict.fromkeys(v[1] for v in _BROWSE_BUTTONS.values()))

# target_id → デフォルト起点ディレクトリのマッピング
# (環境変数 DESI_DATA_DIR / TIMS_DATA_DIR / OUTPUT_DATA_DIR が優先される)
_DEFAULT_START_DIR = {
    "data_folder": DESI_DATA_DIR,
    "default_desi_data_folder": DESI_DATA_DIR,
    "default_desi_output_dir": OUTPUT_DATA_DIR,
    "reanalysis_data_folder": DESI_DATA_DIR,
    "default_tims_data_folder": TIMS_DATA_DIR,
    "default_tims_output_dir": OUTPUT_DATA_DIR,
    "scils_output_folder": TIMS_DATA_DIR,
    "env_tims_data_dir": TIMS_DATA_DIR,
    "env_desi_data_dir": DESI_DATA_DIR,
    "output_dir": OUTPUT_DATA_DIR,
    "default_output_dir": OUTPUT_DATA_DIR,
    "restore_scan_folder": OUTPUT_DATA_DIR,
    "dm_move_dest_path": OUTPUT_DATA_DIR,
    # ver3.9: サムネ用 PNG は output 配下にあることが多い
    "edit_project_thumbnail": OUTPUT_DATA_DIR,
}

# dcc.Store は "data" プロパティ、dbc.Input/dcc.Input は "value" プロパティ
# NOTE: 旧「手動結果フォルダ」機能の孤児参照 result_folder_manual / browse_result_folder は
#       レイアウト未生成のため削除済み（存在しない Input/Output は共有コールバック
#       open_file_browser / apply_file_browser_selection を丸ごと停止させ、全参照ボタンを
#       無反応にしていた）。
_STORE_TARGETS = {"extra_folder_pending_store"}

# ★ ver64.0: 追加データフォルダの行から開いたときの caller_id。
#   `_ALL_TARGET_IDS` のどれとも一致しない番兵にしておくと、共有の
#   `apply_file_browser_selection` は「どの入力欄も更新せずモーダルを閉じる」
#   だけになり、行への書き戻しは専用の `apply_extra_folder_selection` が担う。
#   （静的 ID を 1 つ増やす方式にすると、行が増減するたびに Output の数が
#     変わる = Dash では表現できない。）
_EXTRA_ROW_CALLER = "__extra_folder_row__"


def _target_property(tid):
    return "data" if tid in _STORE_TARGETS else "value"


def _browser_start_dir(current_val, default_start):
    """ファイルブラウザの初期ディレクトリを決める（入力欄の現在値を優先）。"""
    initial_dir = (str(default_start) if default_start and default_start.is_dir()
                   else str(APP_BASE_DIR))
    if current_val:
        p = Path(current_val)
        if p.is_dir():
            return str(p)
        if p.parent.is_dir():
            # ファイルパスの場合は親ディレクトリを使用
            return str(p.parent)
    return initial_dir


# すべてのブラウズボタンからモーダルを開く
@callback(
    [Output("file_browser_modal", "is_open", allow_duplicate=True),
     Output("fb_state", "data", allow_duplicate=True),
     Output("fb_drive_selector", "options"),
     Output("fb_selected_path", "children", allow_duplicate=True)],
    [Input(btn_id, "n_clicks") for btn_id in _BROWSE_BUTTONS]
    # ★ ver64.0: 追加データフォルダの各行の「参照...」。別 callback に分けると
    #   fb_drive_selector.options を 2 つの callback が書くことになるため、
    #   モーダルを開く経路はここ 1 本のままにする。
    + [Input({"type": "btn_browse_extra_folder", "index": ALL}, "n_clicks")],
    [State("fb_state", "data")]
    + [State(tid, _target_property(tid)) for tid in _ALL_TARGET_IDS]
    + [State("extra_data_folders_store", "data")],
    prevent_initial_call=True,
)
def open_file_browser(*args):
    # args: [btn_clicks..., extra_browse_clicks, fb_state, target_values..., extra_folders]
    n_buttons = len(_BROWSE_BUTTONS)
    extra_clicks = args[n_buttons]        # パターンマッチング分は 1 引数=リスト
    state = args[n_buttons + 1]           # fb_state
    target_values = args[n_buttons + 2:n_buttons + 2 + len(_ALL_TARGET_IDS)]
    extra_folders = args[-1]              # 追加データフォルダの現在値

    triggered = ctx.triggered_id
    if triggered is None:
        return no_update, no_update, no_update, no_update

    # 追加データフォルダの行から開いた場合
    if isinstance(triggered, dict) and triggered.get("type") == "btn_browse_extra_folder":
        if not any(c for c in (extra_clicks or []) if c):
            return no_update, no_update, no_update, no_update
        idx = triggered.get("index")
        folders = list(extra_folders or [])
        current_val = folders[idx] if isinstance(idx, int) and 0 <= idx < len(folders) else ""
        new_state = {
            "current_dir": _browser_start_dir(current_val, TIMS_DATA_DIR),
            "mode": "folder",
            "caller_id": _EXTRA_ROW_CALLER,
            "extra_index": idx,
            "selected_path": "",
        }
        return True, new_state, get_available_drives(), ""

    if triggered in _BROWSE_BUTTONS:
        mode, target_id = _BROWSE_BUTTONS[triggered]
        drives = get_available_drives()

        # 対応する入力欄の現在値を取得し、初期ディレクトリを決定
        # 優先順: 入力欄の現在値 → target_id 既定 (DESI/TIMS DATA_DIR) → APP_BASE_DIR
        try:
            current_val = target_values[_ALL_TARGET_IDS.index(target_id)]
        except (ValueError, IndexError):
            current_val = ""
        initial_dir = _browser_start_dir(
            current_val, _DEFAULT_START_DIR.get(target_id))

        new_state = {
            "current_dir": initial_dir,
            "mode": mode,
            "caller_id": target_id,
            "selected_path": "",
        }
        return True, new_state, drives, ""

    return no_update, no_update, no_update, no_update


# ディレクトリ内容の表示
@callback(
    [Output("fb_file_list", "children"),
     Output("fb_breadcrumb", "children"),
     Output("fb_path_input", "value")],
    [Input("fb_state", "data"),
     Input("fb_drive_selector", "value"),
     Input("fb_go_btn", "n_clicks")],
    [State("fb_path_input", "value")],
    prevent_initial_call=True,
)
def update_file_browser(state, drive_val, go_clicks, path_input):
    if not state:
        return no_update, no_update, no_update

    triggered = ctx.triggered_id

    if triggered == "fb_drive_selector" and drive_val:
        current_dir = drive_val
    elif triggered == "fb_go_btn" and path_input:
        current_dir = path_input
    else:
        current_dir = state.get("current_dir", "")

    if not current_dir or not Path(current_dir).is_dir():
        return (
            [html.Div("有効なパスを入力してください", style={"padding": "20px", "color": "#999"})],
            [],
            current_dir,
        )

    mode = state.get("mode", "folder")
    show_files = (mode == "file")
    items = list_directory(current_dir, show_files=show_files)

    # ファイルリスト構築
    file_items = []
    # 親ディレクトリへの「..」
    parent = str(Path(current_dir).parent)
    if parent != current_dir:
        file_items.append(
            html.Div(
                className="file-browser-item",
                children=["📁 .."],
                id={"type": "fb_item", "path": parent},
                n_clicks=0,
            )
        )
    for item in items:
        file_items.append(
            html.Div(
                className="file-browser-item",
                children=[f"{item['icon']} {item['name']}"],
                id={"type": "fb_item", "path": item["path"]},
                n_clicks=0,
            )
        )

    if not file_items:
        file_items = [html.Div("空のフォルダです", style={"padding": "20px", "color": "#999"})]

    # パンくず
    parts = build_breadcrumb_parts(current_dir)
    breadcrumb = []
    for i, part in enumerate(parts):
        if i > 0:
            breadcrumb.append(html.Span(" / "))
        breadcrumb.append(html.Span(part["name"]))

    return file_items, breadcrumb, current_dir


# ファイルブラウザ内のアイテムクリック
@callback(
    [Output("fb_state", "data", allow_duplicate=True),
     Output("fb_selected_path", "children", allow_duplicate=True)],
    Input({"type": "fb_item", "path": ALL}, "n_clicks"),
    State("fb_state", "data"),
    prevent_initial_call=True,
)
def handle_fb_item_click(clicks, state):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update, no_update

    clicked_path = ctx.triggered_id["path"]
    path = Path(clicked_path)

    if path.is_dir():
        state["current_dir"] = str(path)
        # folder モードの場合のみディレクトリを selected_path に設定
        if state.get("mode") == "folder":
            state["selected_path"] = str(path)
    else:
        state["selected_path"] = str(path)
    return state, state.get("selected_path", "")


# モーダルの「選択」ボタン → 対応するInputに値を設定
# Dashでは動的にOutput先を変えることが難しいため、
# fb_state の caller_id を使って全対象フィールドの Output を一括定義し、
# 該当する1つだけ値を更新、残りは no_update を返す。

@callback(
    [Output(tid, _target_property(tid), allow_duplicate=True) for tid in _ALL_TARGET_IDS]
    + [Output("file_browser_modal", "is_open", allow_duplicate=True)],
    Input("fb_select_btn", "n_clicks"),
    State("fb_state", "data"),
    prevent_initial_call=True,
)
def apply_file_browser_selection(n_clicks, state):
    if not n_clicks or not state or not state.get("selected_path"):
        return [no_update] * (len(_ALL_TARGET_IDS) + 1)

    caller_id = state.get("caller_id", "")
    selected_path = state["selected_path"]

    results = []
    for tid in _ALL_TARGET_IDS:
        if tid == caller_id:
            results.append(selected_path)
        else:
            results.append(no_update)
    results.append(False)  # close modal
    return results


@callback(
    Output("extra_data_folders_store", "data", allow_duplicate=True),
    Input("fb_select_btn", "n_clicks"),
    [State("fb_state", "data"),
     State("extra_data_folders_store", "data")],
    prevent_initial_call=True,
)
def apply_extra_folder_selection(n_clicks, state, folders):
    """追加データフォルダの行から開いた「選択」を、その行に書き戻す。

    共有の `apply_file_browser_selection` は Output が静的 ID の一覧で固定
    されているため、行数が変わる追加フォルダには書けない。caller_id が
    番兵のときだけこちらが該当行を差し替える（それ以外は no_update なので、
    従来の参照ボタンの挙動は一切変わらない）。
    """
    if not n_clicks or not state:
        return no_update
    if state.get("caller_id") != _EXTRA_ROW_CALLER:
        return no_update
    selected_path = state.get("selected_path") or ""
    if not selected_path:
        return no_update
    idx = state.get("extra_index")
    new_folders = list(folders or [])
    if not isinstance(idx, int) or not (0 <= idx < len(new_folders)):
        return no_update
    if new_folders[idx] == selected_path:
        return no_update
    new_folders[idx] = selected_path
    return new_folders


# キャンセルボタン
@callback(
    Output("file_browser_modal", "is_open", allow_duplicate=True),
    Input("fb_cancel_btn", "n_clicks"),
    prevent_initial_call=True,
)
def close_file_browser(n):
    return False


# ショートカットボタン押下 → fb_state.current_dir を指定パスに切替
# 既存の update_file_browser が fb_state を Input にしているため、
# state を更新するだけで一覧が再描画される。
@callback(
    Output("fb_state", "data", allow_duplicate=True),
    Input({"type": "fb_shortcut", "path": ALL}, "n_clicks"),
    State("fb_state", "data"),
    prevent_initial_call=True,
)
def handle_fb_shortcut(clicks, state):
    if not ctx.triggered_id or not any(c for c in clicks if c):
        return no_update
    target_path = ctx.triggered_id.get("path")
    if not target_path or not Path(target_path).is_dir():
        return no_update
    new_state = dict(state or {})
    new_state["current_dir"] = target_path
    return new_state


# ---------------------------------------------------------------------------
# パス入力欄のファイル名バッジ（クライアントサイドコールバック）
# ---------------------------------------------------------------------------

_PATH_INPUT_IDS = [
    "data_folder", "rds_folder", "annotation_path", "output_dir",
    "reanalysis_data_folder", "rds_folder_reanalysis", "reanalysis_annotation_path",
    "resume_reanalysis_dir",
    "desi_v8_script_path", "desi_cluster_filter_script_path",
    "tims_v8_script_path", "tims_cluster_filter_script_path",
    "default_desi_data_folder", "default_annotation_file", "default_desi_output_dir",
    "default_tims_data_folder", "default_annotation_csv", "default_tims_output_dir",
    "default_output_dir",
    "reann_annotation_path",
]

clientside_callback(
    """function() {
        var args = Array.prototype.slice.call(arguments);
        return args.map(function(v) {
            if (!v) return '';
            var p = v.replace(/\\\\/g, '/').split('/');
            var name = p[p.length - 1] || p[p.length - 2] || '';
            return '\\ud83d\\udcc1 ' + name;
        });
    }""",
    [Output(f"{pid}_path_hint", "children") for pid in _PATH_INPUT_IDS],
    [Input(pid, "value") for pid in _PATH_INPUT_IDS],
)


# ---------------------------------------------------------------------------
# TIMS 追加データフォルダ管理
# ---------------------------------------------------------------------------

@callback(
    Output("extra_data_folders_store", "data"),
    Input("extra_folder_pending_store", "data"),
    State("extra_data_folders_store", "data"),
    prevent_initial_call=True,
)
def add_extra_folder(pending_path, current_folders):
    """ファイルブラウザで選択されたフォルダをリストに追加"""
    if not pending_path or not Path(pending_path).is_dir():
        return no_update
    folders = list(current_folders or [])
    if pending_path not in folders:
        folders.append(pending_path)
    return folders


def _extra_folder_badge(folder: str):
    """追加フォルダ 1 行分の件数バッジ。上の `data_folder_badge` と同じ見た目。"""
    if not folder or not folder.strip():
        return html.Span("パスを入力するか「参照...」で選んでください",
                         style={"color": "#6c757d", "fontSize": "0.75rem"})
    result = validate_data_folder(folder, is_tims=True)
    color = "#28a745" if result["ok"] else "#dc3545"
    mark = "\u2713" if result["ok"] else "\u2717"
    return html.Span(f"{mark} {result['msg']}",
                     style={"color": color, "fontSize": "0.8rem"})


@callback(
    Output("extra_data_folders_container", "children"),
    Input("extra_data_folders_store", "data"),
)
def render_extra_folders(folders):
    """追加フォルダを「パス入力欄 + 参照... + 件数バッジ + ×」の行で並べる。

    ★ ver64.0: 従来はフォルダ名を出すだけの読み取り専用リストで、
      パスを直したいときは一度 × で消して選び直すしかなかった。上の
      「データフォルダ」欄とは操作方法も見た目も違ううえ、**件数バッジが
      基準フォルダの分しか無い**ため、追加したフォルダに実際に何ファイル
      入っているのかを画面から確かめる手段がまったく無かった。
      基準フォルダの欄と同じ形に揃える。
    """
    if not folders:
        return []
    rows = []
    for i, folder in enumerate(folders):
        rows.append(html.Div(
            className="mb-2",
            style={"marginTop": "5px"},
            children=[
                html.Div(
                    style={"display": "flex", "gap": "5px"},
                    children=[
                        dbc.Input(
                            id={"type": "extra_folder_path", "index": i},
                            value=folder,
                            placeholder="データフォルダのパス",
                            size="sm",
                            # 1 文字ごとに Store を書き換えると、その都度この行が
                            # 描き直されて入力欄のカーソルが飛ぶ。確定時だけ送る。
                            debounce=True,
                        ),
                        dbc.Button(
                            "参照...", size="sm", color="secondary",
                            id={"type": "btn_browse_extra_folder", "index": i},
                        ),
                        dbc.Button(
                            "\u00d7", size="sm", color="danger", outline=True,
                            id={"type": "btn_remove_extra_folder", "index": i},
                            style={"padding": "0 8px", "lineHeight": "1.2"},
                        ),
                    ],
                ),
                _extra_folder_badge(folder),
            ],
        ))
    return rows


@callback(
    Output("extra_data_folders_store", "data", allow_duplicate=True),
    Input({"type": "extra_folder_path", "index": ALL}, "value"),
    State("extra_data_folders_store", "data"),
    prevent_initial_call=True,
)
def edit_extra_folder_path(values, current):
    """入力欄に直接書かれた / 貼り付けられたパスを Store に反映する。"""
    if values is None:
        return no_update
    current = list(current or [])
    if len(values) != len(current):
        # 行を足した / 消した直後は、まだ描き直し前の値が届くことがある。
        # 件数が合わないうちは触らない（古い値で上書きしてしまうため）。
        return no_update
    new = [(v or "").strip() for v in values]
    if new == current:
        return no_update
    return new


@callback(
    Output("extra_data_folders_store", "data", allow_duplicate=True),
    Input({"type": "btn_remove_extra_folder", "index": ALL}, "n_clicks"),
    State("extra_data_folders_store", "data"),
    prevent_initial_call=True,
)
def remove_extra_folder(n_clicks_list, current_folders):
    """×ボタンで追加フォルダを削除"""
    if not current_folders or not any(n for n in n_clicks_list if n):
        return no_update
    triggered = ctx.triggered_id
    if triggered and isinstance(triggered, dict):
        idx = triggered.get("index")
        if idx is not None and 0 <= idx < len(current_folders):
            folders = list(current_folders)
            folders.pop(idx)
            return folders
    return no_update
