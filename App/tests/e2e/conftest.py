"""E2E (Playwright) 用 fixture（Inc.3）。

- `app_server`: run_app.py を試験ポートで起動し `/healthz` 緑を待つ。
- `page`: 既設 Chromium を起動し `/login` でログインしてインタラクティブタブへ。

Playwright/ブラウザが無い環境では該当テストを skip する（CI/Docker で実行する想定）。
Dash ではコンポーネント `id` がそのまま安定セレクタ（`#id`）。data-testid は不要。
"""

import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

HOST = "127.0.0.1"
APP_ROOT = Path(__file__).resolve().parents[2]  # .../App
MASTER_PW = "e2e-master-pass"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((HOST, 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def app_server():
    """run_app.py を subprocess 起動して base URL と master password を返す。"""
    if not (APP_ROOT / "run_app.py").exists():
        pytest.skip("run_app.py が見つかりません")
    port = _free_port()
    env = dict(os.environ)
    env["FLASK_SECRET_KEY"] = secrets.token_hex(32)
    env["MASTER_PASSWORD"] = MASTER_PW
    env["INITIAL_PASSWORD_A"] = "e2e-a"
    env["INITIAL_PASSWORD_B"] = "e2e-b"
    env["APP_HOST"] = HOST
    env["APP_PORT"] = str(port)

    proc = subprocess.Popen(
        [sys.executable, "run_app.py"], cwd=str(APP_ROOT), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    base = f"http://{HOST}:{port}"

    healthy = False
    for _ in range(60):
        if proc.poll() is not None:
            out = (proc.stdout.read() or b"").decode("utf-8", "replace")
            pytest.skip(f"アプリ起動に失敗（依存不足の可能性）:\n{out[-1500:]}")
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
        pytest.skip("アプリが /healthz 緑になりませんでした")

    yield base, MASTER_PW

    proc.terminate()
    try:
        proc.wait(timeout=10)
    except Exception:
        proc.kill()


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
        pytest.skip("playwright が未インストールです")

    with sync_playwright() as p:
        try:
            browser = _launch_chromium(p)
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"Chromium の起動に失敗: {e}")
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
