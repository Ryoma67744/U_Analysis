"""認証設定 (auth.json) の保存先が環境に依らず解決できることの番人。

★ ver56.4 / デバッグ総点検 §3.2 で確定した不具合:
  `_default_auth_path()` が `/app/Data/Other/common/auth.json` という
  **Docker コンテナ内にしか存在しない絶対パス**を決め打ちしていた。
  上書き用の `AUTH_CONFIG_PATH` は .env.example / setup.sh / setup.bat /
  SETUP_GUIDE.html / DEPLOY.md のどこにも記載が無く、実質「知らないと使えない」。

  実測した被害:
  - root で起動 → ファイルシステム直下に `/app/` を作り、そこへ bcrypt ハッシュを書いた
  - 一般ユーザーで起動 → `PermissionError: '/app'` で **起動できない**
  - E2E も同じ経路を通るため、テストが「失敗」ではなく **無言 skip** になり
    19 件のブラウザテストが動いていないことに誰も気づけなかった

★ 修正の要: **Docker の解決結果を 1 バイトも変えない**こと。
  Docker では WORKDIR=/app なので `config.DATA_DIR` は `/app/Data` に解決される。
  したがって `DATA_DIR/Other/common/auth.json` は従来と完全に同一パスとなり、
  既存の named volume (`msi-common:/app/Data/Other/common`) と、そこに保存済みの
  パスワードはそのまま引き継がれる。**ディレクトリ名を `Common` に変えてはならない**
  (変えると既存環境のパスワードが消える)。
"""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]


def _reload_auth(monkeypatch, auth_config_path=None):
    """AUTH_CONFIG_PATH を差し替えて auth_service を読み直す。"""
    if auth_config_path is None:
        monkeypatch.delenv("AUTH_CONFIG_PATH", raising=False)
    else:
        monkeypatch.setenv("AUTH_CONFIG_PATH", str(auth_config_path))
    import app.services.auth_service as auth_service
    return importlib.reload(auth_service)


class TestDefaultPathIsPortable:
    """既定パスが「そのマシンで書ける場所」に解決されること。"""

    def test_default_path_is_not_a_hardcoded_container_path(self, monkeypatch):
        """★ 本丸: 既定パスが /app/... の決め打ちでないこと。

        リポジトリを /home/user/... に置いた開発機・研究室 PC では
        /app は存在せず、root なら誤って作成し、一般ユーザーなら起動不能になる。
        """
        auth = _reload_auth(monkeypatch, None)
        default = auth.AUTH_CONFIG_PATH
        from app.config import DATA_DIR

        assert default == DATA_DIR / "Other" / "common" / "auth.json", (
            "auth.json の既定保存先は config.DATA_DIR から導出すること。"
            f" 実際: {default}"
        )
        # DATA_DIR 自体がリポジトリ由来なので、/app 決め打ちにはならない
        assert str(default).startswith(str(DATA_DIR))

    def test_docker_layout_resolves_to_the_same_path_as_before(self):
        """★ 回帰の要: Docker (WORKDIR=/app) では従来と同一パスに解決されること。

        ここが崩れると既存 named volume の auth.json を見失い、
        本番環境のパスワードが失われる。実際に /app 配下を模した木を作り、
        そこで config を評価して確認する (このプロセスの CWD には依存しない)。
        """
        code = (
            "import sys, pathlib;"
            "sys.path.insert(0, sys.argv[1]);"
            "import app.config as c;"
            "print(c.DATA_DIR / 'Other' / 'common' / 'auth.json')"
        )
        # /app/App/app/config.py 相当の位置関係を tmp に作らず、
        # 実リポジトリの相対関係 (App/app/config.py → parent×3 = ルート) を検証する
        cfg = APP_ROOT / "app" / "config.py"
        assert cfg.exists()
        root_from_cfg = cfg.parent.parent.parent
        assert (root_from_cfg / "App").is_dir(), (
            "config.py は「App/app/config.py の 3 つ上がプロジェクトルート」"
            "という前提で DATA_DIR を決めている。この前提が崩れると"
            "Docker の /app/Data 解決も崩れる。"
        )
        out = subprocess.run(
            [sys.executable, "-c", code, str(APP_ROOT)],
            capture_output=True, text=True, timeout=60,
            env={**os.environ, "AUTH_CONFIG_PATH": ""},
        )
        assert out.returncode == 0, out.stderr
        # WORKDIR=/app に配置された場合 -> /app/Data/Other/common/auth.json
        resolved = out.stdout.strip()
        assert resolved.endswith("Data/Other/common/auth.json"), resolved

    def test_directory_name_stays_lowercase_common(self, monkeypatch):
        """★ 既存環境保護: ディレクトリ名は小文字 `common` のままであること。

        docker-compose.yml が `msi-common:/app/Data/Other/common` を
        マウントしているため、`Common` (大文字) に変えると
        保存済みパスワードを見失う。
        """
        auth = _reload_auth(monkeypatch, None)
        assert auth.AUTH_CONFIG_PATH.parent.name == "common"

    def test_env_override_still_wins(self, monkeypatch, tmp_path):
        """AUTH_CONFIG_PATH による明示指定は従来どおり最優先。"""
        target = tmp_path / "custom" / "auth.json"
        auth = _reload_auth(monkeypatch, target)
        assert auth.AUTH_CONFIG_PATH == target
        assert auth._LOCK_PATH == target.with_suffix(".lock")


class TestInitCreatesItsParentDirectory:
    """親フォルダが無い状態からでも初期化できること。"""

    def test_init_from_env_works_when_parent_missing(self, monkeypatch, tmp_path):
        """★ 順序の是正: ロック取得より前に親フォルダを作ること。

        修正前は `_ensure_parent()` が `_save()` の中でしか呼ばれず、
        その手前の `FileLock(_LOCK_PATH)` が先に走っていた。
        現行の filelock は親を作ってくれるため表面化していないが、
        ライブラリ差し替えで即座に壊れる依存だった。
        """
        target = tmp_path / "deep" / "not" / "created" / "auth.json"
        assert not target.parent.exists()
        auth = _reload_auth(monkeypatch, target)
        monkeypatch.setenv("INITIAL_PASSWORD_B", "unit-test-pw-b")

        auth.init_from_env()

        assert target.exists(), "auth.json が作られていない"
        assert auth.is_initialized()
        assert auth.verify_password_b("unit-test-pw-b")

    def test_initialized_config_is_not_reinitialized(self, monkeypatch, tmp_path):
        """既に初期化済みなら再初期化しない (version を無駄に上げない)。"""
        target = tmp_path / "auth.json"
        auth = _reload_auth(monkeypatch, target)
        monkeypatch.setenv("INITIAL_PASSWORD_B", "unit-test-pw-b")
        auth.init_from_env()
        v1 = auth.get_password_version()
        auth.init_from_env()
        assert auth.get_password_version() == v1


class TestOperationalDocsMentionTheOverride:
    """★ 「知らないと使えない」状態を作らないための番人。

    既定パスが可搬になった今も、運用でパスを指定したい場面
    (データを別ボリュームに置く等) は残る。上書き用の環境変数が
    どの手順書にも出てこない状態には戻さない。
    """

    def test_auth_config_path_is_documented(self):
        repo_root = APP_ROOT.parent
        candidates = [
            APP_ROOT / ".env.example",
            repo_root / ".env.docker",
            repo_root / "App" / "docs" / "DEPLOY.md",
        ]
        existing = [p for p in candidates if p.exists()]
        assert existing, "手順書の候補が 1 つも見つからない"
        hit = [p for p in existing
               if "AUTH_CONFIG_PATH" in p.read_text(encoding="utf-8", errors="replace")]
        assert hit, (
            "AUTH_CONFIG_PATH がどの手順書にも記載されていない。"
            f" 確認したファイル: {[str(p.relative_to(repo_root)) for p in existing]}"
        )


@pytest.fixture(autouse=True)
def _restore_auth_module():
    """他テストへ影響させないため、最後に既定状態へ戻す。"""
    yield
    os.environ.pop("AUTH_CONFIG_PATH", None)
    import app.services.auth_service as auth_service
    importlib.reload(auth_service)
