"""Tests for app.services.env_file_manager"""

from pathlib import Path

import pytest

from app.services import env_file_manager as em


@pytest.fixture
def temp_env(tmp_path, monkeypatch):
    """`.env` と `.env.example` の解決先を一時ディレクトリに差し替える"""
    env_path = tmp_path / ".env"
    example_path = tmp_path / ".env.example"
    monkeypatch.setattr(em, "ENV_PATH", env_path)
    monkeypatch.setattr(em, "ENV_EXAMPLE_PATH", example_path)
    return env_path, example_path


class TestReadEmptyOrMissing:
    def test_missing_file_returns_blank_values(self, temp_env):
        env_path, _ = temp_env
        assert not env_path.exists()
        values = em.read_env_values()
        assert all(v == "" for v in values.values())
        assert set(values) == set(em.EDITABLE_KEYS)

    def test_blank_file_returns_blank_values(self, temp_env):
        env_path, _ = temp_env
        env_path.write_text("")
        values = em.read_env_values()
        assert all(v == "" for v in values.values())


class TestReadValues:
    def test_reads_existing_keys(self, temp_env):
        env_path, _ = temp_env
        env_path.write_text(
            "TIMS_DATA_DIR=/data/tims\n"
            "DESI_DATA_DIR=/data/desi\n"
            "# コメント行\n"
            "APP_PORT=3838\n"
        )
        values = em.read_env_values()
        assert values["TIMS_DATA_DIR"] == "/data/tims"
        assert values["DESI_DATA_DIR"] == "/data/desi"
        assert values["APP_PORT"] == "3838"
        # 未定義キーは空文字
        assert values["R_HOME"] == ""


class TestWriteValues:
    def test_creates_file_from_example_if_missing(self, temp_env):
        env_path, example_path = temp_env
        example_path.write_text("# TIMS_DATA_DIR=/example\n")
        em.write_env_values({"TIMS_DATA_DIR": "/data/tims"})
        assert env_path.exists()
        text = env_path.read_text()
        assert "/data/tims" in text
        # example のコメント行は保全される
        assert "# TIMS_DATA_DIR=/example" in text

    def test_preserves_unrelated_lines(self, temp_env):
        env_path, _ = temp_env
        env_path.write_text(
            "# 重要なコメント\n"
            "SOME_OTHER_KEY=value\n"
            "TIMS_DATA_DIR=/old\n"
        )
        em.write_env_values({"TIMS_DATA_DIR": "/new"})
        text = env_path.read_text()
        assert "# 重要なコメント" in text
        assert "SOME_OTHER_KEY=value" in text
        assert "/new" in text
        assert "/old" not in text

    def test_blank_value_is_skipped(self, temp_env):
        env_path, _ = temp_env
        env_path.write_text("TIMS_DATA_DIR=/keep\n")
        em.write_env_values({"TIMS_DATA_DIR": "  ", "DESI_DATA_DIR": "/new"})
        text = env_path.read_text()
        assert "/keep" in text
        assert "/new" in text

    def test_rejects_unknown_keys(self, temp_env, caplog):
        env_path, _ = temp_env
        env_path.write_text("")
        em.write_env_values({"DANGEROUS_KEY": "foo"})
        assert "DANGEROUS_KEY" not in env_path.read_text()


class TestRoundTrip:
    def test_write_then_read(self, temp_env):
        em.write_env_values({
            "TIMS_DATA_DIR": "/tims/path",
            "R_HOME": "/usr/lib/R",
            "APP_PORT": "9999",
        })
        values = em.read_env_values()
        assert values["TIMS_DATA_DIR"] == "/tims/path"
        assert values["R_HOME"] == "/usr/lib/R"
        assert values["APP_PORT"] == "9999"


class TestEnsureExists:
    def test_creates_empty_when_no_template(self, temp_env):
        env_path, _ = temp_env
        em.ensure_env_file_exists()
        assert env_path.exists()
        assert env_path.read_text() == ""

    def test_noop_when_already_exists(self, temp_env):
        env_path, _ = temp_env
        env_path.write_text("EXISTING=1\n")
        em.ensure_env_file_exists()
        assert env_path.read_text() == "EXISTING=1\n"
