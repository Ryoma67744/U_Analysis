"""ver60.0: SCiLS 変換のサブプロセス実行と進捗表示の契約を固定する。

守りたいのは「GUI と CLI がまたいで壊れる」種類の欠陥で、片方だけ直しても
気づけないもの:

  - CLI が出す進捗行と、コールバックが読む正規表現が**対**になっていること
  - CLI が書く結果 JSON が `ConversionResult` に**そのまま戻せる**こと
  - 失敗時に、変換器が投げた**利用者が直せる指示**が画面まで届くこと
  - 各コールバックの return 要素数が Output 数と一致すること
    （ずれても Dash は起動時ではなく**実行時**に落ちるので、押すまで判らない）

これらは E2E でしか見えないが、Playwright を立てずに CLI を実プロセスとして
起動すれば同じ経路を通せる。
"""

import dataclasses
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.services.scils_converter import ConversionResult

_CLI = Path(__file__).resolve().parents[1] / "tools" / "convert_scils_cli.py"


def _make_folder(folder: Path, n_mz: int = 3, n_spots: int = 6) -> Path:
    """最小の SCiLS フォルダ (Intensity + Spot) を作る"""
    folder.mkdir(parents=True, exist_ok=True)
    spots = list(range(1, n_spots + 1))
    headers = ["m/z"] + [f"Spot {n}" for n in spots]
    # m/z は昇順にしない（order_mz 経路を必ず通す）
    mz = [300.5, 100.25, 200.75][:n_mz] or [100.0]
    rows = []
    for i in range(n_mz):
        rows.append(",".join([str(mz[i])] + [f"{i * 10 + j}.5" for j in range(n_spots)]))
    (folder / "S_Intensity.csv").write_text(
        ",".join(headers) + "\n" + "\n".join(rows) + "\n", encoding="utf-8")
    pd.DataFrame({"Spot index": spots,
                  "X": [j % 3 for j in range(n_spots)],
                  "Y": [j // 3 for j in range(n_spots)]}).to_csv(
        folder / "S_Spot.csv", index=False)
    return folder


def _run_cli(*args) -> subprocess.CompletedProcess:
    # -u は必須。stdout がパイプ/ファイルだと CPython はブロックバッファになり、
    # 進捗が終了まで 1 行も出ない（GUI 側は行が出ないとハングに見える）。
    return subprocess.run(
        [sys.executable, "-u", str(_CLI), *args],
        capture_output=True, text=True, timeout=300,
        cwd=str(_CLI.parents[1]),
    )


class TestCliContract:
    def test_success_writes_result_json_that_rebuilds_conversion_result(self, tmp_path):
        src = _make_folder(tmp_path / "in")
        out = tmp_path / "out.parquet"
        rj = tmp_path / "result.json"
        p = _run_cli(str(src), str(out), "--no-organize", f"--result-json={rj}")

        assert p.returncode == 0, p.stdout + p.stderr
        assert out.exists()
        data = json.loads(rj.read_text(encoding="utf-8"))
        # 余りも欠けもなく ConversionResult に戻せること。GUI はこれを
        # そのまま _render_success に渡すので、フィールドがずれると成功表示が壊れる。
        assert set(data) == {f.name for f in dataclasses.fields(ConversionResult)}
        result = ConversionResult(**data)
        assert result.n_spots == 6
        assert result.n_mz_features == 3
        assert result.output_path == str(out)

    def test_progress_lines_match_the_callback_regex(self, tmp_path):
        """CLI の進捗行とコールバックの正規表現が対であること。

        ここが噛み合わなくなると進捗バーは**例外を出さずに 0% のまま**になる。
        """
        from app.callbacks.scils_converter_callbacks import _PROGRESS_RE

        src = _make_folder(tmp_path / "in")
        p = _run_cli(str(src), str(tmp_path / "out.parquet"), "--no-organize")
        assert p.returncode == 0, p.stdout + p.stderr

        matched = [_PROGRESS_RE.match(ln) for ln in p.stdout.splitlines()
                   if ln.startswith("進捗")]
        assert matched, f"進捗行が 1 行も出ていない:\n{p.stdout}"
        assert all(m is not None for m in matched), "進捗行が正規表現に一致しない"
        pcts = [int(m.group(1)) for m in matched if m]
        assert pcts == sorted(pcts), f"進捗が逆行している: {pcts}"
        assert pcts[-1] == 100, f"最後が 100% で終わっていない: {pcts[-1]}"

    def test_failure_exits_2_and_keeps_the_actionable_message(self, tmp_path):
        """入力不備のとき、変換器の指示文が画面まで届くこと。"""
        from app.callbacks.scils_converter_callbacks import _error_excerpt

        empty = tmp_path / "empty"
        empty.mkdir()
        rj = tmp_path / "result.json"
        p = _run_cli(str(empty), str(tmp_path / "out.parquet"),
                     "--no-organize", f"--result-json={rj}")

        assert p.returncode == 2, p.stdout
        assert not (tmp_path / "out.parquet").exists()
        excerpt = _error_excerpt(p.stdout)
        assert "CSV" in excerpt, f"利用者向けの説明が抜き出せていない: {excerpt!r}"
        # traceback の File 行を混ぜない（利用者には読めない）
        assert "Traceback" not in excerpt and "File \"" not in excerpt
        assert "error" in json.loads(rj.read_text(encoding="utf-8"))

    def test_float64_flag_is_honoured(self, tmp_path):
        import pyarrow.parquet as pq
        src = _make_folder(tmp_path / "in")
        out = tmp_path / "out.parquet"
        assert _run_cli(str(src), str(out), "--no-organize", "--float64").returncode == 0
        types = {str(f.type) for f in pq.read_schema(str(out)) if f.name not in
                 ("id", "x", "y", "annotation")}
        assert types == {"double"}, types

    def test_bad_option_exits_1_without_running(self, tmp_path):
        src = _make_folder(tmp_path / "in")
        out = tmp_path / "out.parquet"
        p = _run_cli(str(src), str(out), "--no-such-option")
        assert p.returncode == 1
        assert not out.exists()


class TestCallbackArity:
    """return の要素数が Output 数と一致すること。

    ずれても Dash は起動時に検出せず、**ボタンを押した瞬間**に落ちる。
    ver60.0 で run/stop/poll の Output を 1 つずつ増やしたので固定しておく。
    """

    def _n_outputs(self, fn):
        """登録済みコールバックの Output 数を名前で引く。

        GLOBAL_CALLBACK_MAP には clientside コールバックなど `callback` キーを
        持たない spec も混ざる（他のテストモジュールを一緒に読むと出てくる）。
        `__wrapped__` の有無も Dash のバージョンと登録経路で変わるので、
        どちらも決め打ちしない。
        """
        from dash._callback import GLOBAL_CALLBACK_MAP
        for spec in GLOBAL_CALLBACK_MAP.values():
            func = spec.get("callback")
            if func is None:
                continue
            name = getattr(getattr(func, "__wrapped__", func), "__name__", "")
            if name == fn:
                outs = spec["output"]
                return len(outs) if isinstance(outs, list) else 1
        raise AssertionError(f"コールバック {fn} が登録されていない")

    def test_alert_only_matches_run_output_count(self):
        from app.callbacks import scils_converter_callbacks as cb
        assert len(cb._alert_only("x")) == cb._RUN_OUTPUTS
        assert cb._RUN_OUTPUTS == self._n_outputs("run_scils_conversion")

    @pytest.mark.parametrize("fn,expected", [
        ("stop_scils_conversion", 4),
        ("poll_scils_conversion", 8),
    ])
    def test_output_counts_are_pinned(self, fn, expected):
        assert self._n_outputs(fn) == expected

    def test_poll_returns_no_update_tuple_of_right_length(self):
        from app.callbacks import scils_converter_callbacks as cb
        # @callback はこの Dash 版では元の関数をそのまま返すので直接呼べる
        out = cb.poll_scils_conversion(1, None)
        assert len(out) == self._n_outputs("poll_scils_conversion")


class TestPollTermination:
    """完了後もポーリングし続けない / 結果表示を消さないこと。

    poll は完了処理で `_convert_process_state["process"] = None` にするが、
    そのとき既に飛んでいた次の `dcc.Interval` tick がもう 1 回入ってくる。
    ここで「まだ実行中」と答えると **interval が再開され、完了後も永久に
    ポーリングし続ける**（run ボタンも無効のまま戻らない）。
    アプリが変換中に再起動して状態を見失った場合も同じ経路を通る。
    """

    def test_lost_process_stops_the_interval(self, tmp_path, monkeypatch):
        from app.callbacks import scils_converter_callbacks as cb

        log = tmp_path / "log.txt"
        log.write_text("進捗: 100% 完了\n", encoding="utf-8")
        monkeypatch.setattr(cb, "_convert_process_state", {"process": None})

        out = cb.poll_scils_conversion(
            1, {"log_file": str(log), "status_file": str(tmp_path / "st.txt")})
        interval_disabled, stop_disabled, run_disabled = out[5], out[6], out[7]
        assert interval_disabled is True, "完了後も interval が回り続ける"
        assert stop_disabled is True
        assert run_disabled is False, "run ボタンが無効のまま戻らない"

    def test_lost_process_does_not_wipe_the_result_panel(self, tmp_path, monkeypatch):
        """直前の tick が描いた成功パネルを消さない（no_update で触らない）。"""
        from dash import no_update
        from app.callbacks import scils_converter_callbacks as cb

        log = tmp_path / "log.txt"
        log.write_text("進捗: 100% 完了\n", encoding="utf-8")
        monkeypatch.setattr(cb, "_convert_process_state", {"process": None})
        out = cb.poll_scils_conversion(
            1, {"log_file": str(log), "status_file": str(tmp_path / "st.txt")})
        assert out[4] is no_update


class TestErrorExcerpt:
    def test_traceback_is_not_mixed_into_the_message(self):
        """stderr は stdout へ合流するので、本文の直後に traceback が続く。

        traceback のコード行も字下げされているため「字下げ行を拾う」だけでは
        混ざる。利用者に読めないものを見せないこと。
        """
        from app.callbacks.scils_converter_callbacks import _error_excerpt

        log = (
            "Phase A 開始\n"
            "変換エラー:\n"
            "  Intensity と Spot テーブルの spot 番号が一致しません。\n"
            "  SCiLS Lab で両ファイルの spot 番号が一致するよう再エクスポートしてください。\n"
            "Traceback (most recent call last):\n"
            '  File "/app/App/app/services/scils_converter.py", line 391, in convert\n'
            "    raise ValueError(chr(10).join(msg))\n"
            "ValueError: Intensity と Spot テーブルの spot 番号が一致しません。\n"
        )
        got = _error_excerpt(log)
        assert "再エクスポート" in got
        assert "Traceback" not in got
        assert "File \"" not in got
        assert "raise ValueError" not in got

    def test_falls_back_to_log_tail_when_no_marker(self):
        from app.callbacks.scils_converter_callbacks import _error_excerpt
        assert "最後の行" in _error_excerpt("a\nb\n最後の行\n")


class TestConcurrencyGuard:
    """★ ver60.0: 変換の二重起動を止める。

    1 変換あたり数 GB を使うので、複数人が同時に押すと 12GB コンテナを圧迫する
    （CHANGELOG ver49.0 が「変換に同時実行ガードが無い」と挙げていた課題）。
    `start_analysis_process` 側のガードはジョブ台帳に載る**解析**しか見ないため、
    変換同士はコールバックで止める必要がある。
    """

    def test_second_run_is_refused_while_one_is_alive(self, tmp_path, monkeypatch):
        from app.callbacks import scils_converter_callbacks as cb

        class _Alive:
            def poll(self):
                return None          # まだ実行中

        started = []
        monkeypatch.setattr(cb, "_convert_process_state",
                            {"process": _Alive(), "output_dir": str(tmp_path)})
        monkeypatch.setattr(cb, "start_analysis_process",
                            lambda *a, **k: started.append(a) or {"success": False})

        out = cb.run_scils_conversion(1, str(tmp_path), str(tmp_path), "s",
                                      [], ["on"], [], 200)
        assert "実行中" in str(out[0])
        assert not started, "実行中なのに 2 本目のプロセスを起動している"

    def test_finished_process_does_not_block_the_next_run(self, tmp_path, monkeypatch):
        from app.callbacks import scils_converter_callbacks as cb

        class _Done:
            def poll(self):
                return 0             # 終了済み

        monkeypatch.setattr(cb, "_convert_process_state",
                            {"process": _Done(), "output_dir": str(tmp_path)})
        monkeypatch.setattr(cb, "start_analysis_process",
                            lambda *a, **k: {"success": False, "message": "起動テスト"})

        out = cb.run_scils_conversion(1, str(tmp_path), str(tmp_path), "s",
                                      [], ["on"], [], 200)
        # 前回のプロセスが終わっていれば起動まで進む（ここでは起動失敗を返させている）
        assert "実行中です" not in str(out[0])
        assert "起動テスト" in str(out[0])


class TestResultRendering:
    def test_missing_result_json_still_reports_success(self, tmp_path):
        """JSON が読めなくても「失敗」と出さない。

        Parquet は書き終わっているので、詳細が出せないだけで失敗ではない
        （ver55.4 が organize の失敗で成功した変換を失敗と誤認させた件と同じ判断）。
        """
        from app.callbacks.scils_converter_callbacks import _render_from_result_json
        node = _render_from_result_json(str(tmp_path / "nope.json"), "")
        assert "danger" not in str(node)

    def test_error_json_is_reported_as_failure(self, tmp_path):
        from app.callbacks.scils_converter_callbacks import _render_from_result_json
        rj = tmp_path / "r.json"
        rj.write_text(json.dumps({"error": "spot 番号が一致しません"}), encoding="utf-8")
        assert "danger" in str(_render_from_result_json(str(rj), ""))

    def test_unknown_field_in_json_does_not_break_success_panel(self, tmp_path):
        """CLI 側にフィールドが増えても成功表示が TypeError で壊れないこと。"""
        from app.callbacks.scils_converter_callbacks import _render_from_result_json
        rj = tmp_path / "r.json"
        payload = dataclasses.asdict(ConversionResult(
            output_path="/tmp/a.parquet", source_intensity="i.csv",
            source_spot="s.csv", n_spots=5, n_mz_features=3))
        payload["brand_new_field"] = 1
        rj.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        assert "変換完了" in str(_render_from_result_json(str(rj), ""))
