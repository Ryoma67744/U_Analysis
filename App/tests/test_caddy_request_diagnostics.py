"""Exercise the production proxy rules on loopback, including upstream failure.

Run with CADDY_BINARY pointing to a Caddy 2 executable. CADDY_STRICT=1 makes a
missing executable a failure, so release validation cannot silently skip this.
No Docker, production hostname, certificate issuance, or real data is used.
"""

import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import UUID

import pytest


REPO = Path(__file__).resolve().parents[2]


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize("expected_status", [200, 500, 502])
def test_request_id_survives_proxy_success_and_failure(tmp_path, expected_status):
    binary = os.environ.get("CADDY_BINARY") or shutil.which("caddy")
    if not binary or not Path(binary).is_file():
        if os.environ.get("CADDY_STRICT") == "1":
            pytest.fail("CADDY_BINARY is required for strict proxy validation")
        pytest.skip("Set CADDY_BINARY to run the real proxy integration test")

    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            received.append(self.headers.get("X-Request-ID"))
            payload = json.dumps({"received_request_id": received[-1]}).encode()
            self.send_response(expected_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            # The real Flask app also echoes the ID. Caddy must set one final
            # header rather than append a second copy of the upstream value.
            self.send_header("X-Request-ID", received[-1])
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    backend.daemon_threads = True
    backend_port = backend.server_port
    worker = None
    if expected_status == 502:
        # Explicit connection refusal: no backend accepted this POST.
        backend.server_close()
    else:
        worker = threading.Thread(target=backend.serve_forever, daemon=True)
        worker.start()

    port = _free_port()
    config = (REPO / "Caddyfile").read_text(encoding="utf-8")
    config = config.replace("{\n", "{\n\tadmin off\n\tauto_https off\n", 1)
    config = config.replace("cciiumap.duckdns.org {", f"http://127.0.0.1:{port} {{")
    config = config.replace("msi-app:3838", f"127.0.0.1:{backend_port}")
    config = config.replace("header_up X-Forwarded-Proto https", "header_up X-Forwarded-Proto http")
    config_path = tmp_path / "Caddyfile"
    config_path.write_text(config, encoding="utf-8")
    log_path = tmp_path / "caddy.log"
    env = dict(os.environ, XDG_DATA_HOME=str(tmp_path / "data"),
               XDG_CONFIG_HOME=str(tmp_path / "config"))
    proc = None
    try:
        validation = subprocess.run(
            [binary, "validate", "--config", str(config_path), "--adapter", "caddyfile"],
            env=env, capture_output=True, timeout=20)
        assert validation.returncode == 0, validation.stderr.decode("utf-8", "replace")
        with log_path.open("wb") as stream:
            proc = subprocess.Popen(
                [binary, "run", "--config", str(config_path), "--adapter", "caddyfile"],
                env=env, stdout=stream, stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                assert proc.poll() is None, log_path.read_text(encoding="utf-8")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                        break
                except OSError:
                    time.sleep(0.05)
            else:
                pytest.fail("Caddy did not bind its loopback test port")

            supplied_id = "00000000-0000-0000-0000-000000000001"
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/_dash-update-component", data=b"{}",
                headers={"Content-Type": "application/json", "X-Request-ID": supplied_id})
            try:
                response = urllib.request.urlopen(request, timeout=15)
            except urllib.error.HTTPError as error:
                response = error
            with response:
                assert response.status == expected_status
                identity = response.headers["X-Request-ID"]
                assert response.headers.get_all("X-Request-ID") == [identity]
                assert identity and str(UUID(identity)) == identity
                assert identity != supplied_id
                if expected_status != 502:
                    assert json.load(response)["received_request_id"] == identity
            assert received == ([] if expected_status == 502 else [identity])

            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                records = []
                for line in log_path.read_text(encoding="utf-8").splitlines():
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                matches = [r for r in records if r.get("request_id") == identity
                           and r.get("status") == expected_status]
                if matches:
                    break
                time.sleep(0.05)
            assert matches, log_path.read_text(encoding="utf-8")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)
        if worker:
            backend.shutdown()
            backend.server_close()
            worker.join(timeout=5)
