"""コンテナの init 挟み込みと /metrics 記録スクリプトの回帰テスト（ver60.1）。

2026-08-25 の本番障害で見つかった 2 点を固定する。

1. **PID 1 問題**
   `docker-compose.yml` に `init:` が無いと、コンテナの PID 1 は Dockerfile の
   `CMD ["python3", "run_app.py"]`、つまり Dash アプリ本体になる。Linux では
   親を失った子は PID 1 に再ペアレントされ、PID 1 には wait() で回収する義務が
   あるが、Python は自分が起動していない子を回収しない。結果、R が fork した
   並列ワーカーの孤児が永久にゾンビとして残る。
   実際に本番で R のゾンビが 8 個、19 時間放置されていた。

2. **/metrics 記録スクリプト**
   同じ障害で、アプリ本体の RSS が 22 時間で 11.8GB まで膨らんでいた。しかし
   プロセス内キャッシュはいずれも件数上限付きで、静的にコードを読むだけでは
   犯人を特定できなかった。増え方を実測する手段が必要になったため
   `record_metrics.sh` を追加した。

   このスクリプトが壊れていると「計測しているつもりで何も残っていない」という
   最悪の形で失敗する（次の障害時に手ぶらになる）ので、出力フォーマットと
   取得失敗時の挙動をテストで固定する。

docker / コンテナは CI に無いので、`docker` コマンドを PATH 上のスタブに
差し替えて検証する。
"""

import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

# App/tests/test_container_init_and_metrics_recorder.py → リポジトリルート
_REPO_ROOT = Path(__file__).resolve().parents[2]
_COMPOSE = _REPO_ROOT / "docker-compose.yml"
_RECORDER = _REPO_ROOT / "record_metrics.sh"

# 本番アプリのサービス名。docker-compose.prod.yml もこの名前を上書き対象にしている。
_APP_SERVICE = "msi-app"


# ---------------------------------------------------------------------------
# 1. PID 1 問題
# ---------------------------------------------------------------------------
def _app_service() -> dict:
    compose = yaml.safe_load(_COMPOSE.read_text(encoding="utf-8"))
    assert _APP_SERVICE in compose["services"], (
        f"docker-compose.yml に {_APP_SERVICE} サービスが無い"
    )
    return compose["services"][_APP_SERVICE]


def test_app_service_declares_init():
    """`init: true` が無いとアプリ本体が PID 1 になり孤児を回収できない。

    この修正を戻すとゾンビが再び溜まる。
    """
    svc = _app_service()
    assert svc.get("init") is True, (
        "docker-compose.yml の msi-app に `init: true` が無い。\n"
        "これが無いと PID 1 が `python3 run_app.py` になり、R の孤児プロセスが\n"
        "永久にゾンビとして残る（2026-08-25 の本番障害で 8 個 / 19 時間放置）。"
    )


def test_dockerfile_has_no_entrypoint_wrapper():
    """`init: true` が効く前提（ENTRYPOINT で独自の init を挟んでいない）を確認。

    将来 ENTRYPOINT が追加されると PID 1 の持ち主が変わり、上のテストが通っていても
    ゾンビ回収の担当が曖昧になる。前提が崩れたらここで気付けるようにする。
    """
    dockerfile = (_REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    entrypoints = [
        ln for ln in dockerfile.splitlines()
        if ln.strip().upper().startswith("ENTRYPOINT")
    ]
    assert not entrypoints, (
        "Dockerfile に ENTRYPOINT が追加されている。PID 1 が誰になるか再確認すること:\n"
        + "\n".join(entrypoints)
    )


# ---------------------------------------------------------------------------
# 2. /metrics 記録スクリプト
# ---------------------------------------------------------------------------
_FAKE_METRICS = (
    "rss_bytes=627863552\n"
    "vms_bytes=2956660736\n"
    "num_fds=15\n"
    "num_threads=22\n"
    "cpu_percent=0.0\n"
    "project_states_size=3\n"
    "diskcache_mb=29.7\n"
)


def _run_recorder(tmp_path: Path, docker_stub: str) -> Path:
    """`docker` をスタブに差し替えて record_metrics.sh を 1 回走らせる。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(docker_stub, encoding="utf-8")
    stub.chmod(0o755)

    log = tmp_path / "metrics.log"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["METRICS_LOG"] = str(log)

    subprocess.run(
        ["bash", str(_RECORDER)], env=env, check=True,
        capture_output=True, timeout=60,
    )
    return log


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash が無い")
def test_recorder_writes_single_tab_separated_line(tmp_path):
    """正常時: 1 回の実行で 1 行、タブ区切りで全項目が残る。

    複数行に散ると時系列の突き合わせができなくなるため、1 レコード 1 行を固定する。
    """
    log = _run_recorder(
        tmp_path,
        "#!/usr/bin/env bash\ncat <<'EOF'\n" + _FAKE_METRICS + "EOF\n",
    )
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, f"1 行であるべきだが {len(lines)} 行: {lines}"

    fields = lines[0].split("\t")
    # 先頭はタイムスタンプ、以降が key=value
    assert fields[0][:2] == "20", f"先頭がタイムスタンプでない: {fields[0]!r}"
    recorded = dict(f.split("=", 1) for f in fields[1:])
    # リーク調査で実際に突き合わせる 4 項目が欠けないこと
    for key in ("rss_bytes", "num_fds", "num_threads", "project_states_size"):
        assert key in recorded, f"{key} が記録されていない: {recorded}"
    assert recorded["rss_bytes"] == "627863552"
    assert recorded["project_states_size"] == "3"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash が無い")
def test_recorder_records_failure_instead_of_silently_skipping(tmp_path):
    """取得失敗も 1 行として残す。

    コンテナ停止・ハング・再起動中は「値が取れないこと自体」が最も重要な情報なので、
    無言で何も書かずに終わると障害の時間帯だけログが歯抜けになる。
    """
    log = _run_recorder(
        tmp_path,
        "#!/usr/bin/env bash\necho 'Error: No such container' >&2\nexit 1\n",
    )
    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, f"失敗時も 1 行残すべき: {lines}"
    assert "ERROR" in lines[0], f"ERROR が記録されていない: {lines[0]!r}"
    # 改行を畳んでフォーマットを崩していないこと
    assert "No such container" in lines[0]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash が無い")
def test_recorder_appends_across_runs(tmp_path):
    """複数回の実行が追記される（上書きで前回分を失わない）。

    時系列で増え方を見るのが目的なので、上書きしてしまうと存在意義が無くなる。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(
        "#!/usr/bin/env bash\ncat <<'EOF'\n" + _FAKE_METRICS + "EOF\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    log = tmp_path / "metrics.log"
    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["METRICS_LOG"] = str(log)

    for _ in range(3):
        subprocess.run(
            ["bash", str(_RECORDER)], env=env, check=True,
            capture_output=True, timeout=60,
        )

    lines = log.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3, f"3 行追記されるべきだが {len(lines)} 行"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash が無い")
def test_recorder_caps_log_size(tmp_path):
    """ログ自体が新たなディスク圧迫源にならないよう上限で切り詰める。

    障害の再発防止に入れた仕組みが別の障害を生んでは本末転倒なため。
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "docker"
    stub.write_text(
        "#!/usr/bin/env bash\ncat <<'EOF'\n" + _FAKE_METRICS + "EOF\n",
        encoding="utf-8",
    )
    stub.chmod(0o755)

    log = tmp_path / "metrics.log"
    # 上限を超える既存ログを用意する
    log.write_text("x" * 5000 + "\n" + ("y" * 100 + "\n") * 50, encoding="utf-8")
    before = log.stat().st_size

    env = dict(os.environ)
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["METRICS_LOG"] = str(log)
    env["METRICS_LOG_MAX_BYTES"] = "1024"

    subprocess.run(
        ["bash", str(_RECORDER)], env=env, check=True,
        capture_output=True, timeout=60,
    )

    after = log.stat().st_size
    assert after < before, f"切り詰められていない: {before} -> {after}"
