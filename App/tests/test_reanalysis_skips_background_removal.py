"""クラスタを選んだ再解析で、背景除去 (Otsu) が二度かからないこと (S1)。

--------------------------------------------------------------------------
症状: 選んだスポットの約半分が黙って消える
--------------------------------------------------------------------------
DESI の再解析は、本解析テンプレートを**文章として書き換えて**実行する。
その書き換えのひとつが「背景除去はもう済んでいるので飛ばせ」だが、
目印 (アンカー) が本解析側の記述変更に追従できず**空振り**している。

    再解析側が探す目印 : seu_list[[ii]] <- filtering_result_otsu$filtered_seurat
    本解析側の実際の行 : seu_list[[length(seu_list) + 1]] <- filtering_result_otsu$filtered_seurat

開始側の目印は当たるので「壊れている」ようには見えないが、終了側が
見つからないと書き換え関数は**元のコードをそのまま返す** (無言)。
結果、すでに背景除去済みのデータへ自動しきい値処理 (Otsu 法) が
もう一度かかる。

Otsu 法は「与えられたデータを 2 群に分ける境目」を毎回計算し直すので、
既に選別済みのデータに再適用すると**残った中でさらに信号の弱い側が
切り捨てられる**。脂肪や壊死部など信号の低い組織が選択的に失われる。
画面にもログにも警告は出ない。

--------------------------------------------------------------------------
直し方: 文字列手術をやめ、定数の注入に変える
--------------------------------------------------------------------------
アンカーを現在の記述に合わせるだけでは、**同じ事故がまた起きる**
(本解析側の 1 行を書き換えた瞬間に無言で死ぬ)。しかも今の置換文
`seu_list[[ii]] <- seurat_obj` は、ROI 分割ループを持つ現在の本解析では
**別の事故を生む** — 内側ループが同じ `ii` で上書きし合い、
ROI ごとのサンプルが 1 つしか残らない。

そこで本解析側に `SKIP_BACKGROUND_FILTER` という定数を置き、再解析は
その値を差し替えるだけにする。差し替えは `replace_assign_line` が担い、
この関数は対象行が無ければ `.stopif` で**停止する** (fail-closed)。
既に 9 件以上の設定がこの形で注入されており、同じ作法に揃う。
"""

import re
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "Script"
DESI_V16 = SCRIPT / "DESI" / "260623_DESI-UMAP_Template_v16.R"
DESI_FILTER = SCRIPT / "DESI" / "DESI_RDS_ClusterFilter_ver3.R"


# ---------------------------------------------------------------------------
# 本解析テンプレート側: スイッチを持つこと
# ---------------------------------------------------------------------------

def test_the_template_has_a_switch_for_background_removal():
    """★ 本解析側に既定 FALSE のスイッチがあること。"""
    src = DESI_V16.read_text(encoding="utf-8")
    m = re.search(r"^SKIP_BACKGROUND_FILTER\s*<-\s*(\S+)", src, re.M)
    assert m, (
        "本解析テンプレートに SKIP_BACKGROUND_FILTER が無い。"
        "文字列手術ではなく定数注入で背景除去を切り替える")
    assert m.group(1) == "FALSE", (
        f"既定が {m.group(1)} になっている。**本解析では必ず背景除去を行う**ので "
        "既定は FALSE でなければならない")


def test_the_otsu_call_is_guarded_by_the_switch():
    """★ スイッチが立っているとき Otsu を呼ばないこと。"""
    lines = DESI_V16.read_text(encoding="utf-8").splitlines()
    call = [i for i, ln in enumerate(lines)
            if re.search(r"filtering_result_otsu\s*<-\s*filter_low_count_spots", ln)]
    assert call, "Otsu の呼び出しが見つからない（前提が変わった）"
    # 呼び出しの手前 10 行以内にスイッチの分岐があること
    head = "\n".join(lines[max(0, call[0] - 10):call[0]])
    assert "SKIP_BACKGROUND_FILTER" in head, (
        f"v16:{call[0] + 1} の Otsu 呼び出しがスイッチで囲われていない")


def test_the_skip_branch_keeps_every_sub_sample():
    """★ 飛ばすときも ROI ごとのサンプルを取りこぼさないこと。

    旧い置換文は `seu_list[[ii]] <- seurat_obj` だった。`ii` は
    **ファイルの番号**なので、1 ファイルを複数 ROI に分ける現在の作りでは
    内側ループが同じ位置を上書きし、ROI が 1 つしか残らない。
    追加する側 (`length(seu_list) + 1`) でなければならない。
    """
    src = DESI_V16.read_text(encoding="utf-8")
    # 定義行ではなく **Otsu 呼び出しの周辺** を見る
    i = src.index("filter_low_count_spots(\n") if "filter_low_count_spots(\n" in src \
        else src.index("filtering_result_otsu <- filter_low_count_spots")
    block = src[max(0, i - 1200):i + 1200]
    assert "seu_list[[length(seu_list) + 1]] <- seurat_obj" in block, (
        "背景除去を飛ばす側が `seu_list[[length(seu_list) + 1]]` で"
        "追加していない。`[[ii]]` だと ROI 分割時に上書き事故になる")


# ---------------------------------------------------------------------------
# 再解析側: スイッチを立てること・空振りする手術を残さないこと
# ---------------------------------------------------------------------------

def test_the_reanalysis_turns_the_switch_on():
    """★ 本丸: 再解析が背景除去を飛ばすよう指示すること。"""
    src = DESI_FILTER.read_text(encoding="utf-8")
    assert re.search(
        r'replace_assign_line\(\s*code\s*,\s*"SKIP_BACKGROUND_FILTER"\s*,\s*"TRUE"',
        src), (
        "再解析が SKIP_BACKGROUND_FILTER を TRUE にしていない。"
        "背景除去済みのデータに Otsu 法がもう一度かかり、"
        "**信号の弱い組織が選択的に失われる**")


def test_the_dead_text_surgery_is_gone():
    """★ 空振りしていた書き換えを残さないこと（二重管理の防止）。"""
    src = DESI_FILTER.read_text(encoding="utf-8")
    assert "patch_v8_disable_otsu" not in src, (
        "空振りする文字列手術 patch_v8_disable_otsu が残っている。"
        "定数注入に置き換えたので、両方あると次の改版でまた食い違う")


def test_the_injection_fails_loudly_when_the_target_moves():
    """★ 目印が消えたら黙って通さないこと（今回の事故の再発防止）。

    `replace_assign_line` は対象行が 0 件なら `.stopif` で停止する。
    「無言で元コードを返す」形に戻っていないことを見張る。
    """
    src = DESI_FILTER.read_text(encoding="utf-8")
    m = re.search(r"replace_assign_line\s*<-\s*function[^{]*\{(.*?)\n  \}", src, re.S)
    assert m, "replace_assign_line の定義が見つからない"
    assert ".stopif" in m.group(1), (
        "replace_assign_line が対象行 0 件で停止しなくなった。"
        "無言で素通りすると、注入されていないまま解析が完走する")
