"""E2E (Playwright) 用 fixture（Inc.3）。

- `app_server`: run_app.py を試験ポートで起動し `/healthz` 緑を待つ。
- `page`: 既設 Chromium を起動し `/login` でログインしてインタラクティブタブへ。

Playwright/ブラウザが無い環境では通常 skip する。検証必須の実行は
`E2E_STRICT=1` とし、起動不能をエラーとして記録する。
`E2E_APP_ROOT=/path/to/baseline/App` で同じ隔離ハーネスから比較元を起動できる。
`E2E_CADDY_BINARY=/path/to/caddy` でリポジトリの Caddyfile を使う loopback HTTP 経由にする。
Dash ではコンポーネント `id` がそのまま安定セレクタ（`#id`）。data-testid は不要。
"""

import collections
import json
import os
import re
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


class _BrowserDiagnostics:
    """初回 navigation 前から Dash の console と HTTP を別々に収集する。"""

    def __init__(self, page, base_url):
        self.base_url = base_url
        self.console_errors = []
        self.page_errors = []
        self.failed_requests = []
        self.completed_204_cancellations = []
        self.server_errors = []
        self.callback_responses = []
        self._responses_by_request = {}
        page.on("console", self._console)
        page.on("pageerror", lambda error: self.page_errors.append(str(error)))
        page.on("requestfailed", self._request_failed)
        page.on("response", self._response)

    def _console(self, message):
        if message.type != "error":
            return
        arguments = []
        for argument in message.args:
            try:
                # Error の message/stack は JSON.stringify 単独では {} になる。
                arguments.append(argument.evaluate("""value => {
                    if (value instanceof Error) {
                        return {name: value.name, message: value.message, stack: value.stack};
                    }
                    try { return JSON.parse(JSON.stringify(value)); }
                    catch (_) { return String(value); }
                }"""))
            except Exception:
                arguments.append(str(argument))
        self.console_errors.append({
            "text": message.text, "arguments": arguments, "location": message.location,
        })

    def _is_dash_callback(self, request):
        return (request.method == "POST"
                and request.url.split("?", 1)[0] == self.base_url + "/_dash-update-component")

    @staticmethod
    def _callback_output(request):
        try:
            return request.post_data_json.get("output")
        except Exception:
            return None

    def _request_failed(self, request):
        response = self._responses_by_request.get(request)
        entry = {"url": request.url, "failure": request.failure,
                 "method": request.method, "time": time.time(),
                 "output": self._callback_output(request) if self._is_dash_callback(request) else None,
                 "response_status": response["status"] if response else None,
                 "response_time": response["time"] if response else None,
                 "request_id": response["request_id"] if response else None}
        # Dash 2.18.2 は 204 (PreventUpdate) を body を読まずに処理する。
        # Chromium の ERR_ABORTED でも、この同一 request の 204 受信を確認済み
        # の場合だけ別記録にする。未応答・200・5xx・別 request の成功では除外しない。
        if (self._is_dash_callback(request) and request.failure == "net::ERR_ABORTED"
                and response is not None and response["status"] == 204):
            self.completed_204_cancellations.append(entry)
        else:
            self.failed_requests.append(entry)

    def _response(self, response):
        entry = {"url": response.url, "status": response.status, "time": time.time(),
                 "request_id": response.headers.get("x-request-id")}
        if self._is_dash_callback(response.request):
            # Input/State の内容やログイン情報を保存せず、callback の出力先だけ記録。
            entry["output"] = self._callback_output(response.request)
            self._responses_by_request[response.request] = entry
            self.callback_responses.append(entry)
        if response.status >= 500:
            # HTTP 502 は応答済みなので requestfailed だけでは捕まらない。
            self.server_errors.append(entry)

    def application_errors(self):
        def same_origin(url):
            return url == self.base_url or url.startswith(self.base_url + "/")

        return {
            "console": [item for item in self.console_errors
                        if not item["location"].get("url")
                        or same_origin(item["location"]["url"])],
            "pageerror": self.page_errors,
            "requestfailed": [item for item in self.failed_requests if same_origin(item["url"])],
            "http_5xx": [item for item in self.server_errors if same_origin(item["url"])],
        }

    def assert_clean(self):
        errors = {kind: items for kind, items in self.application_errors().items() if items}
        assert not errors, json.dumps(errors, ensure_ascii=False, indent=2)

    def save(self, directory, page, server_logs="", proxy_logs=""):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "browser.json").write_text(json.dumps({
            "console_errors": self.console_errors, "page_errors": self.page_errors,
            "failed_requests": self.failed_requests, "server_errors": self.server_errors,
            "completed_204_cancellations": self.completed_204_cancellations,
            "callback_responses": self.callback_responses,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        (directory / "server.log").write_text(server_logs, encoding="utf-8")
        if proxy_logs:
            (directory / "caddy.log").write_text(proxy_logs, encoding="utf-8")
        try:
            page.screenshot(path=str(directory / "page.png"), full_page=True, timeout=5000)
        except Exception:
            pass  # 元の失敗をスクリーンショットのタイムアウトで隠さない。


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


def _stop_process(proc):
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)


def _local_caddy_config(source, app_port, proxy_port):
    """本番 proxy の処理を残し、待受・upstream・TLS だけを試験環境へ置き換える。"""
    config = (source.parent / "Caddyfile").read_text(encoding="utf-8")
    config, sites = re.subn(r"(?m)^cciiumap\.duckdns\.org\s*\{",
                           f"http://{HOST}:{proxy_port} {{", config)
    config, upstreams = re.subn(r"reverse_proxy msi-app:3838\s*\{",
                               f"reverse_proxy {HOST}:{app_port} {{", config)
    assert sites == upstreams == 1, "Caddyfile の site/upstream 変更を fixture に反映してください"
    config = config.replace("\tdefault_sni cciiumap.duckdns.org", "\tadmin off\n\tauto_https off", 1)
    config = config.replace("header_up X-Forwarded-Proto https", "header_up X-Forwarded-Proto http")
    assert "\tadmin off" in config and "\tauto_https off" in config
    return config


@pytest.fixture(scope="session")
def app_server_logs():
    return {}


@pytest.fixture(scope="session")
def app_server(tmp_path_factory, app_server_logs):
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
    app_server_logs["drain"] = drain
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

    proxy = None
    try:
        caddy_binary = os.environ.get("E2E_CADDY_BINARY")
        if caddy_binary:
            if not Path(caddy_binary).is_file():
                _unavailable(f"Caddy が見つかりません: {caddy_binary}")
            proxy_port = _free_port()
            config_path = runtime_root / "Caddyfile.e2e"
            config_path.write_text(_local_caddy_config(source, port, proxy_port), encoding="utf-8")
            proxy_env = dict(os.environ)
            proxy_env["XDG_CONFIG_HOME"] = str(runtime_root / "caddy-config")
            proxy_env["XDG_DATA_HOME"] = str(runtime_root / "caddy-data")
            proxy = subprocess.Popen(
                [caddy_binary, "run", "--config", str(config_path), "--adapter", "caddyfile"],
                cwd=str(runtime_root), env=proxy_env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            proxy_drain = _LogDrain(proxy.stdout)
            app_server_logs["proxy"] = proxy_drain
            base = f"http://{HOST}:{proxy_port}"
            proxy_ready = False
            for _ in range(30):
                if proxy.poll() is not None:
                    _unavailable(f"Caddy の起動に失敗:\n{proxy_drain.text()[-2500:]}")
                try:
                    with urllib.request.urlopen(base + "/healthz", timeout=2) as response:
                        if response.status == 200:
                            proxy_ready = True
                            break
                except Exception:
                    pass
                time.sleep(1)
            if not proxy_ready:
                _unavailable(f"Caddy 経由の /healthz が応答しません:\n{proxy_drain.text()[-2500:]}")

        yield base, MASTER_PW
    finally:
        _stop_process(proxy)
        _stop_process(proc)


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
def page(app_server, app_server_logs, tmp_path):
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
        pg.diagnostics = _BrowserDiagnostics(pg, base)
        ready = False
        try:
            pg.goto(base, wait_until="domcontentloaded", timeout=30000)
            # 認証画面ならログイン（解析者名 + パスワードの2項目）
            if pg.locator("#password").count() > 0:
                if pg.locator("#analyst_name").count() > 0:
                    pg.fill("#analyst_name", "e2e-tester")
                pg.fill("#password", password)
                pg.click("button[type=submit]")
            # Dash は定期 callback が続くため、networkidle は準備完了を表さない。
            # 初回の大きな JS 配信を含む proxy 試験では余裕を持って実 layout を待つ。
            pg.locator("#main_tabs").wait_for(
                state="attached", timeout=90000 if "proxy" in app_server_logs else 30000)
            pg.base_url = base
            ready = True
            yield pg
        finally:
            if not ready:
                drain, proxy = app_server_logs.get("drain"), app_server_logs.get("proxy")
                pg.diagnostics.save(tmp_path / "setup-diagnostics", pg,
                                    drain.text() if drain else "", proxy.text() if proxy else "")
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
