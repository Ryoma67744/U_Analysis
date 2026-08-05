"""RDS 抽出キャンセル機構のテスト（ver4.19）。

`_popen_with_cancel` がサブプロセスを cancel_event で即時 kill できること、
通常終了・非ゼロ終了が正しく返ることを、実プロセス（python）で検証する。
また interactive_callbacks のキャンセル token registry の挙動も確認する。
"""

import sys
import time
import threading

import pytest

from app.services.seurat_bridge import _popen_with_cancel, ExtractionCancelled


def test_popen_completes_normally():
    rc, out, err = _popen_with_cancel(
        [sys.executable, "-c", "print('ok')"], threading.Event()
    )
    assert rc == 0
    # [ver50.1] stdout も返す。R 側の [extract] 行（各段の所要時間）を
    # 捨てるとキャンセル可能パスだけ内訳が追えなくなるため。
    assert out.decode().strip() == "ok"


def test_popen_nonzero_returncode():
    rc, out, err = _popen_with_cancel(
        [sys.executable, "-c", "import sys; sys.exit(3)"], threading.Event()
    )
    assert rc == 3


def test_popen_returns_stderr_separately():
    """stdout と stderr が混ざらないこと"""
    rc, out, err = _popen_with_cancel(
        [sys.executable, "-c",
         "import sys; print('OUT'); print('ERR', file=sys.stderr); sys.exit(0)"],
        threading.Event(),
    )
    assert rc == 0
    assert out.decode().strip() == "OUT"
    assert err.decode().strip() == "ERR"


def test_popen_cancel_kills_process_quickly():
    ev = threading.Event()

    def _cancel_soon():
        time.sleep(0.5)
        ev.set()

    t = threading.Thread(target=_cancel_soon)
    t.start()
    start = time.monotonic()
    with pytest.raises(ExtractionCancelled):
        _popen_with_cancel(
            [sys.executable, "-c", "import time; time.sleep(30)"], ev
        )
    elapsed = time.monotonic() - start
    t.join()
    # 30 秒待たず、cancel から ~1 秒以内に中断されること
    assert elapsed < 5


def test_cancel_event_registry():
    from app.callbacks.interactive_callbacks import (
        _get_or_create_cancel_event, _clear_cancel_event,
    )
    # token 偽値 → None
    assert _get_or_create_cancel_event(None) is None
    # 同一 token は同一 Event を返す
    ev = _get_or_create_cancel_event("tokA")
    assert ev is not None and not ev.is_set()
    assert _get_or_create_cancel_event("tokA") is ev
    # cancel が先に set されても、後続 get は set 済みを観測（取りこぼし防止）
    ev.set()
    assert _get_or_create_cancel_event("tokA").is_set()
    # clear 後は新しい未 set の Event
    _clear_cancel_event("tokA")
    ev2 = _get_or_create_cancel_event("tokA")
    assert ev2 is not ev and not ev2.is_set()
    _clear_cancel_event("tokA")
