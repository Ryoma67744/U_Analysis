#!/usr/bin/env python3
# =============================================================================
# MSI Analysis Application - SCiLS 変換 CLI（サブプロセス実行のエントリポイント）
#
# GUI (scils_converter_callbacks.py) から Popen で起動される。
#
# Usage:
#   python -u App/tools/convert_scils_cli.py <input_folder> <output_parquet> [options]
#
# Options:
#   --spot-block=N     1 回に読み込む spot 列数（既定 200）
#   --float64          m/z 列を float64 で格納する（既定は float32）
#   --no-organize      元 CSV を <BASE>_Transform へ移動しない
#   --drop-uncovered   座標 CSV に無い spot 列を除外して変換する
#   --result-json=PATH 変換結果を JSON で書き出す先
#
# 終了コード: 0 = 正常 / 1 = 引数エラー / 2 = 変換失敗
#
# なぜサブプロセスなのか:
#   ver4.22 は変換を Dash の background コールバックにしたが、DiskcacheManager の
#   expire=300（5 分）で長い変換の追跡が切れるため ver4.23 で同期実行へ戻した。
#   その結果 **Caddy の read_timeout 600s を超える変換は HTTP 側で打ち切られる**
#   状態が残っていた。CHANGELOG (ver4.23) 自身が代替案として
#   「サブプロセス＋ポーリング」を挙げており、Parquet 再パックで既に動いている。
#   同じ枠組み（start_analysis_process）に乗せることで、同時実行ブロック・
#   空きメモリ/ディスクチェック・ログ退避もそのまま効く。
#
# 注意: 必ず `-u` を付けて起動すること。stdout がファイルだと CPython は
#       ブロックバッファになり、進捗が終了まで 1 行も出ない
#       （repack_parquet_rowgroups.py と同じ理由）。
# =============================================================================

import dataclasses
import json
import logging
import sys
import traceback
from pathlib import Path

# App/ を import path に載せる（App/tools/x.py → parents[1] == App/）。
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.scils_converter import convert_scils_to_parquet  # noqa: E402

# GUI 側が拾う進捗行。値だけでなくラベルも出すので、そのまま人にも読める。
# 対になる正規表現は scils_converter_callbacks._PROGRESS_RE。片方だけ変えないこと。
_PROGRESS_PREFIX = "進捗"


def setup_cli_logging() -> None:
    """変換器の診断ログ (INFO) をこのプロセスの stdout に出す。

    ★ ver63.1: これが無いと変換の診断ログが**本番のどこにも出ない**。
      ver60.0 で変換をサブプロセス化したとき、`setup_logging()` を呼ぶのは
      `App/run_app.py`（Dash 本体）だけなので、この CLI プロセスでは
        1. 有効レベルが root 既定の WARNING になり `logger.info(...)` が
           ハンドラに届く前にレベル判定で捨てられる
        2. ハンドラが 1 つも無く、WARNING 以上だけが `logging.lastResort` で
           stderr へ出る
      の 2 段で INFO が消えていた。`Phase A エンジン:` / `Phase A 完了: N 秒` /
      `row group 計画:` / `変換メモリ確認:` が丸ごと失われており、
      **どのフェーズに何秒かかったかを本番で知る手段が無かった**。
      WARNING 以上とこの CLI の `print` は出るためログはそれらしく見え、
      `App/docs/DEPLOY.md` の調査手順だけが黙って空振りしていた。

    `log_config.setup_logging()` は呼ばない。あちらは `RotatingFileHandler` で
    `msi_app.log` に書くが、**RotatingFileHandler はマルチプロセス安全ではない**。
    Dash 本体が開いているのと同じファイルをこのサブプロセスからも開くと、
    ローテーションが競合してログを落としうる。変換ログ
    (`Data/Other/logs/scils_convert/log/`) に出せば GUI のログ欄にもそのまま載る。

    ハンドラは `msi.scils_converter` ではなく親の `msi` に付ける。変換経路は
    `sef_peaklist` / `peak_annotation` など他モジュールも通り、いずれも
    `logging.getLogger("msi.*")` を使うので、親に 1 つ付ければ全部拾える。

    書式は既存の契約を壊さないよう次の 3 つを満たすこと:
      - 行頭を `進捗` にしない — `test_progress_lines_match_the_callback_regex` が
        `startswith("進捗")` の行を全部 `_PROGRESS_RE` に通す
      - 本文に `進捗: NN% ` を含めない — コールバックはログ全体を `finditer` で
        走査して % の最大値を採る (`scils_converter_callbacks.poll_scils_conversion`)
      - 行頭を 2 スペース字下げにしない — `_error_excerpt` が字下げ行を
        エラー本文として拾う
    """
    logger = logging.getLogger("msi")
    logger.setLevel(logging.INFO)
    # 重複ハンドラを防止する（`log_config.setup_logging` が同じ理由で同じことをしている）。
    # 通常この CLI は 1 プロセス 1 変換だが、main() を直接呼ぶテストから 2 回来ると
    # 全行が二重に出て、進捗の重複除去が効いているのか判らなくなる。
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
        logger.addHandler(handler)
    # root にハンドラが付いた場合の二重出力を防ぐ（今は root は素だが、
    # 将来 basicConfig を足した人が二重に悩まないようにしておく）。
    logger.propagate = False


def parse_args(argv):
    positional = []
    opts = {"spot_block": 200, "store_float32": True, "organize": True,
            "drop_uncovered": False, "result_json": None}
    for a in argv:
        if a.startswith("--spot-block="):
            opts["spot_block"] = int(a.split("=", 1)[1])
        elif a == "--float64":
            opts["store_float32"] = False
        elif a == "--no-organize":
            opts["organize"] = False
        elif a == "--drop-uncovered":
            opts["drop_uncovered"] = True
        elif a.startswith("--result-json="):
            opts["result_json"] = a.split("=", 1)[1]
        elif a.startswith("-"):
            raise ValueError(f"不明なオプション: {a}")
        else:
            positional.append(a)
    if len(positional) != 2:
        raise ValueError("入力フォルダと出力 .parquet の 2 つを指定してください")
    return positional[0], positional[1], opts


def main(argv):
    try:
        input_folder, output_path, opts = parse_args(argv)
    except ValueError as e:
        print(f"引数エラー: {e}", flush=True)
        return 1

    setup_cli_logging()
    result_json = opts.pop("result_json")

    print(f"入力フォルダ: {input_folder}", flush=True)
    print(f"出力ファイル: {output_path}", flush=True)
    print(f"設定: spot_block={opts['spot_block']} / "
          f"{'float32' if opts['store_float32'] else 'float64'} / "
          f"整理={'ON' if opts['organize'] else 'OFF'} / "
          f"座標なし spot 除外={'ON' if opts['drop_uncovered'] else 'OFF'}", flush=True)

    last = {"pct": -1}

    def progress_cb(value, maximum, label):
        # 変換器は spot_block ごとに呼ぶので、実データ規模では 1,000 回以上来る。
        # そのまま出すとログが進捗行で埋まるため、**% か作業種別が変わったときだけ**
        # 出す。ラベル末尾の「18,600/20,000 spot」は毎回変わるので、
        # 「…」より前（＝作業の種類）で比べる。
        pct = int(value)
        kind = label.split("…")[0]
        if pct != last["pct"] or kind != last.get("kind"):
            print(f"{_PROGRESS_PREFIX}: {pct}% {label}", flush=True)
            last["pct"], last["kind"] = pct, kind

    try:
        result = convert_scils_to_parquet(
            input_folder, output_path, progress_cb=progress_cb, **opts)
    except Exception as e:
        # 変換器は入力不備を ValueError / FileNotFoundError で投げ、そこには
        # 利用者が直せる具体的な指示が入っている。握り潰さず全文を出す。
        print("変換エラー:", flush=True)
        for line in str(e).splitlines():
            print(f"  {line}", flush=True)
        traceback.print_exc()
        if result_json:
            try:
                Path(result_json).write_text(
                    json.dumps({"error": str(e)}, ensure_ascii=False), encoding="utf-8")
            except Exception as werr:
                print(f"結果 JSON の書き出しに失敗: {werr}", flush=True)
        return 2

    if result_json:
        try:
            Path(result_json).write_text(
                json.dumps(dataclasses.asdict(result), ensure_ascii=False),
                encoding="utf-8")
        except Exception as e:
            # 出力 Parquet は書き終わっている。JSON が無いと GUI が詳細を出せないだけ
            # なので、変換自体は成功として扱う（ver55.4 が organize の失敗で
            # 「成功した変換を失敗と誤認させる」欠陥を直したのと同じ判断）。
            print(f"結果 JSON の書き出しに失敗（変換は成功しています）: {e}", flush=True)

    print(f"変換完了: {result.n_spots:,} spot × {result.n_mz_features:,} m/z "
          f"({result.duration_sec:.1f} 秒)", flush=True)
    for w in result.warnings:
        print(f"警告: {w}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
