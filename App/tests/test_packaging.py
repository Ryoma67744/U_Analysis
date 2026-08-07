"""パッケージ配布経路が成立していること (ver51.8)。

■ なぜこのテストが要るか

`pyproject.toml` の build backend が
`"setuptools.backends._legacy:_Backend"` になっていた。**`setuptools.backends` という
モジュールは宣言している setuptools 68 に存在しない**（実機で ModuleNotFoundError を
確認、外部監査環境の Python 3.13 でも同じ失敗）。つまり `pip install .` も
wheel ビルドも **一切開始できない** 状態が続いていた。

本番 Docker は `requirements.txt` を使うためランタイムには影響しておらず、
**誰も気づかないまま壊れていた**。同じことを繰り返さないよう、
「実際にビルドできるか」をテストで押さえる。

あわせて、ビルドが通るようになってから初めて見える問題も押さえる:
  - `[project.scripts] msi-app = "run_app:main"` の `run_app` が wheel に入っていない
  - `assets` (clientside JS) と `templates` (ログイン画面) が wheel に入っていない
  - `requirements.txt` にあって pyproject に無い依存 (bcrypt / filelock)
"""

import re
import subprocess
import sys
import tomllib
import zipfile
from pathlib import Path

import pytest

APP_DIR = Path(__file__).resolve().parent.parent
PYPROJECT = APP_DIR / "pyproject.toml"


@pytest.fixture(scope="module")
def spec():
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


class TestBuildBackend:
    def test_declared_backend_is_importable(self, spec):
        """★ 宣言したビルドバックエンドが実際に import できること。

        これが本丸。`setuptools.backends._legacy:_Backend` は存在しないので、
        このテストがあれば当時すぐ落ちていた。
        """
        backend = spec["build-system"]["build-backend"]
        module = backend.split(":", 1)[0]
        __import__(module)

    def test_requires_are_satisfiable_names(self, spec):
        """requires に書いたパッケージ名が実在すること（typo 検出）。"""
        for req in spec["build-system"]["requires"]:
            name = re.split(r"[<>=!\[]", req)[0].strip()
            __import__(name)


class TestDependencySync:
    def test_requirements_txt_and_pyproject_agree(self, spec):
        """★ requirements.txt にあって pyproject に無い依存が無いこと。

        wheel 経由で入れた場合、欠けていると import 時に落ちる
        （bcrypt=認証、filelock=排他制御。どちらも起動経路上）。
        """
        def _name(line):
            return re.split(r"[<>=!\[]", line.split("#")[0].strip())[0].strip().lower()

        req = set()
        for line in (APP_DIR / "requirements.txt").read_text(
                encoding="utf-8").splitlines():
            line = line.split("#")[0].strip()
            if line and not line.startswith("-"):
                req.add(_name(line))
        have = {_name(d) for d in spec["project"]["dependencies"]}
        missing = sorted(req - have)
        assert not missing, f"pyproject に無い依存: {missing}"


class TestWheelContents:
    """★ 実際に wheel をビルドして中身を見る。

    メタデータの読み合わせだけでは「run_app が入らない」「assets が入らない」は
    検出できない。ビルドが重いので module スコープで 1 回だけ行う。
    """

    @staticmethod
    @pytest.fixture(scope="class")
    def wheel(tmp_path_factory):
        # ビルド分離あり (既定) で行う。--no-build-isolation だと Debian 系の
        # パッチ済み setuptools が pip と噛み合わず AttributeError: install_layout
        # で落ちる（環境側の問題で、この pyproject の問題ではない）。
        out = tmp_path_factory.mktemp("wheel")
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "-w", str(out),
             str(APP_DIR)],
            capture_output=True, text=True, timeout=900,
        )
        if proc.returncode != 0:
            tail = (proc.stderr or proc.stdout or "")[-2000:]
            # ビルド分離はビルド依存を取りに行くので、オフラインだと必ず失敗する。
            # それはこのリポジトリの欠陥ではないので skip する。
            offline = any(s in tail for s in (
                "Network is unreachable", "Temporary failure in name resolution",
                "ProxyError", "Could not find a version", "No matching distribution",
                "Read timed out", "SSLError",
            ))
            if offline or "No module named pip" in tail:
                pytest.skip(f"ビルド依存を取得できない環境のため skip:\n{tail[-400:]}")
            pytest.fail(f"wheel ビルドに失敗:\n{tail}")
        wheels = list(out.glob("msi_analysis_app-*.whl"))
        assert wheels, f"wheel が生成されていない: {list(out.iterdir())}"
        return wheels[0]

    def test_wheel_builds(self, wheel):
        """★ そもそもビルドが通ること。"""
        assert wheel.exists() and wheel.stat().st_size > 0

    def test_entry_point_target_is_included(self, wheel, spec):
        """★ `msi-app` が指すモジュールが wheel に入っていること。

        `msi-app = "run_app:main"` なのに packages は app* だけだったため、
        インストールしても `msi-app` は ModuleNotFoundError になっていた。
        """
        target = spec["project"]["scripts"]["msi-app"].split(":", 1)[0]
        names = zipfile.ZipFile(wheel).namelist()
        assert any(n == f"{target}.py" or n.startswith(f"{target}/")
                   for n in names), \
            f"{target} が wheel に無い（entry point が壊れる）"

    @pytest.mark.parametrize("needle", ["app/assets/", "app/templates/"])
    def test_frontend_resources_are_included(self, wheel, needle):
        """★ clientside JS とログイン画面テンプレートが wheel に入ること。"""
        names = zipfile.ZipFile(wheel).namelist()
        assert any(n.startswith(needle) for n in names), \
            f"{needle} が wheel に無い（インストール後に画面が壊れる）"
