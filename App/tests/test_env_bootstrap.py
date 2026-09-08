"""`.env` 自動生成の番人（ver64.1）。

2026-09-08、Windows で ZIP を展開して `run_app.bat` を叩いた利用者が

    RuntimeError: FLASK_SECRET_KEY env var is required (32+ random bytes).

で起動できなかった。`.env` は `.gitignore` 済みで配布物に含まれず、
`setup.bat` も `run_app.bat` も `.env` を作らず、`SETUP_GUIDE.html` にも
「作れ」と書いていなかったため、**配布物のデスクトップ起動は誰がやっても
必ずここで止まる**状態だった（Docker はリポジトリ直下の `.env` から
環境変数を渡すので、サーバ運用では表面化しない）。

しかも壁は 1 枚ではない:

    FLASK_SECRET_KEY   未設定 → main.py が RuntimeError
    INITIAL_PASSWORD_B 未設定 → auth_service.init_from_env() が RuntimeError
    MASTER_PASSWORD    未設定 → ログイン画面を突破できない (verify_master が常に False)

本ファイルは「生成された `.env` がこの 3 枚を同時に越えられること」と
「ランチャがその生成処理を呼んでいること」を固定する。後者が抜けると
利用者から見た症状（ダブルクリックで起動しない）がそのまま再発するため、
モジュール単体のテストだけでは足りない。
"""

import re
from pathlib import Path

import pytest

from app.services import env_bootstrap as eb

# App/tests/test_env_bootstrap.py → リポジトリルート
_REPO_ROOT = Path(__file__).resolve().parents[2]
_APP_DIR = _REPO_ROOT / "App"

# 起動〜ログインに必須の 3 キー（欠けると上記のどれかで止まる）
_REQUIRED_TO_START = ("FLASK_SECRET_KEY", "INITIAL_PASSWORD_B", "MASTER_PASSWORD")


@pytest.fixture
def env_pair(tmp_path):
    """本物の `.env.example` を複製した一時ディレクトリを返す。

    雛形そのものを入力にするのは、`.env.example` 側のプレースホルダ表記が
    変わったときに気づけるようにするため。
    """
    example = tmp_path / ".env.example"
    example.write_text(
        (_APP_DIR / ".env.example").read_text(encoding="utf-8"), encoding="utf-8"
    )
    return tmp_path / ".env", example


class TestPlaceholderDetection:
    @pytest.mark.parametrize("value", [
        "CHANGE_ME_TO_RANDOM_HEX_64",
        "CHANGE_ME_MASTER_PASSWORD",
        "ChangeMe_A_FirstRun",
        "ChangeMe_B_FirstRun",
        '"CHANGE_ME_TO_RANDOM_HEX_64"',
        "  CHANGE_ME_TO_RANDOM_HEX_64  ",
    ])
    def test_template_values_are_placeholders(self, value):
        assert eb.is_placeholder(value)

    @pytest.mark.parametrize("value", ["", None, "a" * 64, "kfd3-92mx-7ptr"])
    def test_real_values_are_not_placeholders(self, value):
        assert not eb.is_placeholder(value)

    def test_env_example_still_ships_placeholders(self):
        """雛形の値が「未設定」と判定されること（表記が変わったら気づく）。"""
        text = (_APP_DIR / ".env.example").read_text(encoding="utf-8")
        for key in _REQUIRED_TO_START:
            assert eb.needs_generation(eb.read_key(text, key)), (
                f".env.example の {key} が生成対象と判定されません"
            )


class TestValidateSecretKey:
    """main.py の起動時チェック本体（ここが緩むと公開鍵のまま起動する）。"""

    def test_missing_key_raises_with_howto(self):
        with pytest.raises(RuntimeError) as e:
            eb.validate_secret_key("")
        assert "env_bootstrap" in str(e.value), "直し方が書かれていません"

    def test_placeholder_key_is_rejected(self):
        # ★ ここが本丸: 公開リポジトリに載っている固定文字列で起動させない
        with pytest.raises(RuntimeError) as e:
            eb.validate_secret_key("CHANGE_ME_TO_RANDOM_HEX_64")
        assert "placeholder" in str(e.value)

    def test_generated_key_passes(self):
        key = eb.generate_secret_key()
        assert eb.validate_secret_key(key) == key

    def test_short_key_warns_but_starts(self, caplog):
        """既存デプロイを止めないため、短い鍵は警告どまり。"""
        with caplog.at_level("WARNING"):
            assert eb.validate_secret_key("short-key") == "short-key"
        assert any("FLASK_SECRET_KEY" in r.message for r in caplog.records)


class TestBootstrapCreatesUsableEnv:
    def test_creates_env_that_clears_all_three_walls(self, env_pair):
        env_path, example = env_pair
        assert not env_path.exists()

        result = eb.bootstrap(env_path, example, is_windows=True)

        assert result["created"] is True
        assert env_path.exists()
        text = env_path.read_text(encoding="utf-8")
        for key in _REQUIRED_TO_START:
            value = eb.read_key(text, key)
            assert value and not eb.is_placeholder(value), f"{key} が埋まっていません"
        # main.py の検証を実際に通ること
        eb.validate_secret_key(eb.read_key(text, "FLASK_SECRET_KEY"))

    def test_secret_key_is_64_hex_chars(self, env_pair):
        env_path, example = env_pair
        generated = eb.bootstrap(env_path, example, is_windows=True)["generated"]
        # openssl rand -hex 32 と同じ形式
        assert re.fullmatch(r"[0-9a-f]{64}", generated["FLASK_SECRET_KEY"])

    def test_login_password_is_typeable(self, env_pair):
        """ログインで人が手打ちするので、紛らわしい文字を含めない。"""
        env_path, example = env_pair
        pw = eb.bootstrap(env_path, example, is_windows=True)["generated"]["MASTER_PASSWORD"]
        assert re.fullmatch(r"[a-z0-9]{4}(-[a-z0-9]{4})+", pw)
        assert not set(pw) & set("lo01I"), f"読み違えやすい文字が入っています: {pw}"

    def test_generated_values_are_unique_per_run(self, tmp_path, env_pair):
        _, example = env_pair
        a = eb.bootstrap(tmp_path / "a.env", example, is_windows=True)["generated"]
        b = eb.bootstrap(tmp_path / "b.env", example, is_windows=True)["generated"]
        assert a["FLASK_SECRET_KEY"] != b["FLASK_SECRET_KEY"]
        assert a["MASTER_PASSWORD"] != b["MASTER_PASSWORD"]

    def test_comments_from_example_are_kept(self, env_pair):
        env_path, example = env_pair
        eb.bootstrap(env_path, example, is_windows=True)
        text = env_path.read_text(encoding="utf-8")
        assert "# 認証設定 (Tier A / Tier B / Master)" in text

    def test_works_without_example(self, tmp_path):
        """`.env.example` ごと欠けていても起動できる最小構成を作る。"""
        env_path = tmp_path / ".env"
        eb.bootstrap(env_path, tmp_path / "missing.example", is_windows=True)
        text = env_path.read_text(encoding="utf-8")
        for key in _REQUIRED_TO_START:
            assert eb.read_key(text, key)


class TestIdempotency:
    def test_second_run_generates_nothing(self, env_pair):
        env_path, example = env_pair
        first = eb.bootstrap(env_path, example, is_windows=True)
        before = env_path.read_text(encoding="utf-8")

        second = eb.bootstrap(env_path, example, is_windows=True)

        assert second["generated"] == {}
        assert second["created"] is False
        assert env_path.read_text(encoding="utf-8") == before, (
            "再実行で値が変わると、次回起動時に既存セッションと auth.json が食い違う"
        )
        assert first["generated"]["MASTER_PASSWORD"] == eb.read_key(before, "MASTER_PASSWORD")

    def test_user_supplied_values_are_never_overwritten(self, env_pair):
        env_path, example = env_pair
        env_path.write_text(
            "FLASK_SECRET_KEY=" + "ab" * 32 + "\n"
            "MASTER_PASSWORD=my-own-password\n",
            encoding="utf-8",
        )

        result = eb.bootstrap(env_path, example, is_windows=True)

        text = env_path.read_text(encoding="utf-8")
        assert eb.read_key(text, "MASTER_PASSWORD") == "my-own-password"
        assert eb.read_key(text, "FLASK_SECRET_KEY") == "ab" * 32
        # 足りない分だけ追記される
        assert set(result["generated"]) == {"INITIAL_PASSWORD_A", "INITIAL_PASSWORD_B"}

    def test_placeholders_left_in_env_are_replaced(self, env_pair):
        """雛形を手でコピーした `.env` も直す（起動はするが鍵は公開値、を潰す）。"""
        env_path, example = env_pair
        env_path.write_text(
            "FLASK_SECRET_KEY=CHANGE_ME_TO_RANDOM_HEX_64\n"
            "MASTER_PASSWORD=CHANGE_ME_MASTER_PASSWORD\n"
            "INITIAL_PASSWORD_B=ChangeMe_B_FirstRun\n",
            encoding="utf-8",
        )

        eb.bootstrap(env_path, example, is_windows=True)

        text = env_path.read_text(encoding="utf-8")
        for key in _REQUIRED_TO_START:
            assert not eb.is_placeholder(eb.read_key(text, key))
        eb.validate_secret_key(eb.read_key(text, "FLASK_SECRET_KEY"))


class TestPlatformAdjustment:
    """`run_app.sh` は `.env` を `set -a; . ./.env` で **シェルとして読む**。"""

    def test_windows_r_home_is_commented_out_on_posix(self, env_pair):
        env_path, example = env_pair
        eb.bootstrap(env_path, example, is_windows=False)
        text = env_path.read_text(encoding="utf-8")
        assert not re.search(r"^R_HOME=C:", text, re.MULTILINE), (
            "空白入りの Windows パスをそのまま複製すると run_app.sh が "
            "`Files\\R\\R-4.4.2: command not found` で落ちる"
        )
        assert "# R_HOME=C:" in text, "見本としてコメントで残すこと"

    def test_windows_keeps_r_home(self, env_pair):
        env_path, example = env_pair
        eb.bootstrap(env_path, example, is_windows=True)
        text = env_path.read_text(encoding="utf-8")
        assert re.search(r"^R_HOME=C:", text, re.MULTILINE)

    def test_generated_env_is_shell_sourceable(self, env_pair):
        """`KEY=value` 以外の行が混ざっていないこと（run_app.sh 用）。"""
        env_path, example = env_pair
        eb.bootstrap(env_path, example, is_windows=False)
        for line in env_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            assert re.match(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$", stripped), (
                f"シェルが解釈できない行: {line!r}"
            )


class TestKeyEditing:
    def test_read_key_takes_the_last_definition(self):
        text = "A=1\nA=2\n"
        assert eb.read_key(text, "A") == "2"

    def test_read_key_ignores_commented_lines(self):
        assert eb.read_key("# A=1\n", "A") is None

    def test_set_key_replaces_in_place_and_keeps_others(self):
        text = "# comment\nA=1\nB=2\n"
        out = eb.set_key(text, "A", "9")
        assert out == "# comment\nA=9\nB=2\n"

    def test_set_key_appends_when_absent(self):
        assert eb.set_key("A=1\n", "B", "2") == "A=1\nB=2\n"


class TestLauncherWiring:
    """ランチャが生成処理を呼ばなくなったら、利用者から見た不具合が再発する。"""

    @pytest.mark.parametrize("launcher", [
        "run_app.bat", "setup.bat", "run_app.sh", "setup.sh",
    ])
    def test_launcher_invokes_bootstrap(self, launcher):
        text = (_REPO_ROOT / launcher).read_text(encoding="utf-8-sig")
        assert "app.services.env_bootstrap" in text, (
            f"{launcher} が .env を用意しないと、初回起動が "
            "FLASK_SECRET_KEY の RuntimeError で止まる"
        )

    def test_main_validates_the_secret_key(self):
        text = (_APP_DIR / "app" / "main.py").read_text(encoding="utf-8")
        assert "validate_secret_key(" in text, (
            "起動時チェックを外すと、鍵が公開値のままでも起動してしまう"
        )
