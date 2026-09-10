"""E2E (Playwright) 用 fixture（Inc.3）。

- `app_server`: run_app.py を試験ポートで起動し `/healthz` 緑を待つ。
- `page`: 既設 Chromium を起動し `/login` でログインしてインタラクティブタブへ。

Playwright/ブラウザが無い環境では通常 skip する。検証必須の実行は
`E2E_STRICT=1` とし、起動不能をエラーとして記録する。
`E2E_APP_ROOT=/path/to/baseline/App` で同じ隔離ハーネスから比較元を起動できる。
Dash ではコンポーネント `id` がそのまま安定セレクタ（`#id`）。data-testid は不要。
"""

import collections
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

HOST = "127.0.0.1"
APP_ROOT = Path(__file__).resolve().parents[2]  # .../App
MASTER_PW = "e2e-master-pass"


def _unavailable(reason):
    """★ ver66.3: 検証必須の実行で依存不足を成功相当の skip にしない。"""
    if os.environ.get("E2E_STRICT") == "1":
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason)


class _LogDrain:
    """子プロセスの stdout を読み続けて最後の N 行だけ保持する。

    ver46.1: 以前は stdout=PIPE のまま誰も読んでいなかったため、アプリの
    アクセスログが OS のパイプバッファ (既定 64KiB) を埋めた時点で
    **アプリ側が write でブロックし、無応答になっていた**。
    ブラウザでページを 1 回開くだけで約 9KB 出るため、E2E テストを
    7〜8 本並べると再現する（テストを増やすと直前まで通っていたテストが
    突然 goto タイムアウトする、という形で現れる）。
    """

    def __init__(self, stream, max_lines=400):
        self._lines = collections.deque(maxlen=max_lines)
        self._stream = stream
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self):
        try:
            for line in iter(self._stream.readline, b""):
                self._lines.append(line.decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - プロセス終了時の競合は無視
            pass

    def text(self) -> str:
        return "".join(self._lines)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def app_server(tmp_path_factory):
    """run_app.py を subprocess 起動して base URL と master password を返す。"""
    # ★ ver66.3: 以前は実リポジトリで起動し、前回設定の削除や認証・キャッシュの
    #   書込が利用者データに及び得た。コードと同梱資産だけを一時領域へ複製する。
    #   E2E_APP_ROOT は同じハーネスで未変更版を比較するための読み取り元指定。
    source = Path(os.environ.get("E2E_APP_ROOT", str(APP_ROOT))).resolve()
    if not (source / "run_app.py").exists():
        _unavailable(f"run_app.py が見つかりません: {source}")
    runtime_root = tmp_path_factory.mktemp("msi-e2e")
    app_root = runtime_root / "App"
    app_root.mkdir()
    shutil.copy2(source / "run_app.py", app_root / "run_app.py")
    for folder in ("app", "Script", "DB"):
        shutil.copytree(source / folder, app_root / folder,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    port = _free_port()
    env = dict(os.environ)
    env["FLASK_SECRET_KEY"] = secrets.token_hex(32)
    env["MASTER_PASSWORD"] = MASTER_PW
    env["INITIAL_PASSWORD_A"] = "e2e-a"
    env["INITIAL_PASSWORD_B"] = "e2e-b"
    env["APP_HOST"] = HOST
    env["APP_PORT"] = str(port)
    env["PYTHONPATH"] = str(app_root)
    env["DESI_DATA_DIR"] = str(runtime_root / "Data" / "DESI" / "Data")
    env["TIMS_DATA_DIR"] = str(runtime_root / "Data" / "TIMS" / "Data")
    env["OUTPUT_DATA_DIR"] = str(runtime_root / "Data" / "Other" / "output")
    env["AUTH_CONFIG_PATH"] = str(runtime_root / "Data" / "Other" / "auth.json")
    env["SEURAT_CACHE_DIR"] = str(runtime_root / "Data" / "Other" / "seurat_cache")
    env["DATA_EXPORT_TMP_DIR"] = str(runtime_root / "Data" / "Other" / "exports")
    env["GPT_EXPORT_TMP_DIR"] = str(runtime_root / "Data" / "Other" / "gpt_exports")

    proc = subprocess.Popen(
        [sys.executable, "run_app.py"], cwd=str(app_root), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    # パイプを読み続ける（詰まるとアプリが write でブロックして無応答になる）
    drain = _LogDrain(proc.stdout)
    base = f"http://{HOST}:{port}"

    healthy = False
    for _ in range(60):
        if proc.poll() is not None:
            _unavailable(f"アプリ起動に失敗:\n{drain.text()[-2500:]}")
        try:
            with urllib.request.urlopen(base + "/healthz", timeout=2) as r:
                if r.status == 200:
                    healthy = True
                    break
        except Exception:
            pass
        time.sleep(1)
    if not healthy:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=10)
        _unavailable(f"アプリが /healthz 緑になりませんでした:\n{drain.text()[-2500:]}")

    yield base, MASTER_PW

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()
        proc.wait(timeout=10)


def _launch_chromium(p):
    """Chromium を起動。playwright 同梱版が無い/版ズレの環境では
    PLAYWRIGHT_BROWSERS_PATH 配下の既設バイナリを executable_path で使う
    （`PW_CHROMIUM_PATH` で明示指定も可）。"""
    args = ["--no-sandbox", "--disable-dev-shm-usage"]
    exe = os.environ.get("PW_CHROMIUM_PATH")
    if exe and Path(exe).exists():
        return p.chromium.launch(executable_path=exe, args=args)
    try:
        return p.chromium.launch(args=args)
    except Exception:
        pass
    root = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    patterns = ("chromium-*/chrome-linux/chrome",
                "chromium_headless_shell-*/chrome-linux/headless_shell")
    for pat in patterns:
        for cand in sorted(Path(root).glob(pat)):
            try:
                return p.chromium.launch(executable_path=str(cand), args=args)
            except Exception:
                continue
    raise RuntimeError("Chromium を起動できませんでした")


@pytest.fixture
def page(app_server):
    """ログイン済みの Playwright page を返す。"""
    base, password = app_server
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        _unavailable("playwright が未インストールです")

    with sync_playwright() as p:
        try:
            browser = _launch_chromium(p)
        except Exception as e:  # noqa: BLE001
            _unavailable(f"Chromium の起動に失敗: {e}")
        ctx = browser.new_context()
        pg = ctx.new_page()
        pg.goto(base, wait_until="domcontentloaded", timeout=30000)
        # 認証画面ならログイン（解析者名 + パスワードの2項目）
        if pg.locator("#password").count() > 0:
            if pg.locator("#analyst_name").count() > 0:
                pg.fill("#analyst_name", "e2e-tester")
            pg.fill("#password", password)
            pg.click("button[type=submit]")
            pg.wait_for_load_state("networkidle", timeout=30000)
        pg.base_url = base
        try:
            yield pg
        finally:
            ctx.close()
            browser.close()


def open_interactive_tab(page):
    """インタラクティブ解析タブを開く（タブ role を優先、無ければ main_tabs 内テキスト）。"""
    try:
        tab = page.get_by_role("tab", name="インタラクティブ解析")
        if tab.count() > 0:
            tab.first.click()
            page.wait_for_timeout(1000)
            return
    except Exception:
        pass
    page.locator("#main_tabs").get_by_text("インタラクティブ解析").first.click()
    page.wait_for_timeout(1000)
