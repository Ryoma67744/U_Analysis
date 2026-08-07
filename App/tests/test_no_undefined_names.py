"""未定義名（NameError 予備軍）が無いこと (ver51.8)。

■ なぜこのテストが要るか

ver51.8 で `_shared_data` を LRU 化したとき、`lite_view_callbacks.py` で
`_shared_data_get` / `_shared_data_put` を **import せずに使った**。

  - モジュールの import は成功する（関数の中で参照しているだけなので）
  - 単体テストも E2E も落ちない（その関数を実行するテストが無かった）
  - 実際に `/lite/<project>/<sub>` を開くと NameError でページが真っ白

つまり「import が通る = 名前が解決できる」ではない。同じ穴を塞ぐため、
静的に未定義名を検出する。

★ このテストを書いたら、**私が入れたもの以外にもう 1 件**見つかった:
  `analysis_callbacks.reset_calibration_list` が `load_last_settings` を
  import せずに呼んでおり、キャリブレーションの「リセット」ボタンは
  押すと必ず NameError になっていた。

■ なぜ AST 自作ではなく pyflakes か

スコープ解決（関数引数・内包表記・walrus・globals・条件付き import）を
正しく扱う必要があり、自作すると誤検出だらけになる。pyflakes は
「未定義名」だけを高精度で出せる既製品なので、それを使う。
"""

import subprocess
import sys
from pathlib import Path

import pytest

APP_PKG = Path(__file__).resolve().parent.parent / "app"


def _pyflakes_lines():
    try:
        import pyflakes  # noqa: F401
    except ImportError:
        pytest.skip("pyflakes が無い環境")
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", str(APP_PKG)],
        capture_output=True, text=True, timeout=300,
    )
    # pyflakes は指摘があると exit 1。出力自体が結果なので returncode は見ない。
    return (proc.stdout or "").splitlines()


def test_no_undefined_names_in_app_package():
    """★ アプリ本体に未定義名が 1 つも無いこと。

    これは「実行して初めて NameError になる」型の不具合を、実行せずに捕まえる。
    import が通ることは名前が解決できることを意味しない。
    """
    bad = [ln for ln in _pyflakes_lines() if "undefined name" in ln.lower()]
    assert not bad, (
        "未定義名がある（実行時に NameError になる）:\n  " + "\n  ".join(bad))


def test_no_undefined_names_in_local_scope():
    """ローカル変数の使用前参照（`local variable ... referenced before assignment`）。

    未定義名と同じく実行時まで表面化しない型。
    """
    bad = [ln for ln in _pyflakes_lines()
           if "referenced before assignment" in ln.lower()]
    assert not bad, "使用前参照がある:\n  " + "\n  ".join(bad)


def test_the_guard_actually_reports_something(tmp_path):
    """★ 番人が空振りでないことの確認。

    わざと未定義名を含むファイルを pyflakes にかけ、検出されることを見る。
    これが無いと「pyflakes が動いていないだけ」でも通ってしまう。
    """
    pytest.importorskip("pyflakes")
    broken = tmp_path / "broken_module.py"
    broken.write_text("def f():\n    return _never_imported_helper()\n",
                      encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, "-m", "pyflakes", str(broken)],
        capture_output=True, text=True, timeout=60,
    )
    assert "undefined name" in (proc.stdout or "").lower(), proc.stdout
