"""Shared publication changes metadata only; opt-in is required on every platform."""
from hashlib import sha256
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace

import pytest

from app.services import imzml_shared_permissions as sharing
from app.services import input_preparation as ip
from tests.test_imzml_autoimport_core import entry, fake_convert, fake_validate, prep, source, spec_for


@pytest.mark.parametrize("setting", [None, "0", "true", "yes", ""])
def test_shared_publication_requires_explicit_one(monkeypatch, setting, tmp_path):
    if setting is None:
        monkeypatch.delenv("MSI_IMZML_SHARED_READ", raising=False)
    else:
        monkeypatch.setenv("MSI_IMZML_SHARED_READ", setting)
    assert sharing.shared_read_policy(tmp_path / "not-created") is None
    assert not (tmp_path / "not-created").exists()


def test_enabled_policy_rejects_non_posix_without_touching_files(monkeypatch, tmp_path):
    monkeypatch.setattr(sharing, "os", SimpleNamespace(name="nt", environ={"MSI_IMZML_SHARED_READ": "1"}))
    with pytest.raises(sharing.SharedReadError, match="POSIX"):
        sharing.shared_read_policy(tmp_path)


def test_permission_failure_does_not_publish_or_leave_stage(monkeypatch, tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    class Policy:
        def __init__(self):
            self.root = root
        def ensure_marker(self, name):
            (root / name).touch()
        def ensure_parent(self, path):
            path.mkdir(parents=True, exist_ok=True)
        def prepare_stage(self, path, **kwargs):
            assert (path / ip.RECEIPT).exists()
            raise sharing.SharedReadError("test permission failure")
    monkeypatch.setattr(sharing, "shared_read_policy", lambda path: Policy())
    with pytest.raises(ip.InputPreparationError, match="test permission failure"):
        prep(source(tmp_path / "raw"), root)
    assert not list(root.rglob(ip.RECEIPT))
    assert not list(root.rglob("*.parquet"))
    assert not list(root.rglob(".stage-*"))


def test_healthy_pinned_asset_reuses_without_chmod_or_requiring_new_root(monkeypatch, tmp_path):
    monkeypatch.delenv("MSI_IMZML_SHARED_READ", raising=False)
    saved = prep(source(tmp_path / "raw"), tmp_path / "cache")
    def forbidden(*args, **kwargs):
        raise AssertionError("existing healthy revision must not be chmod'ed")
    monkeypatch.setattr(sharing, "shared_read_policy", forbidden)
    assert ip.prepare_imzml(saved, pinned=True) == saved


def test_disabled_sharing_preserves_default_cache_resolver(monkeypatch, tmp_path):
    monkeypatch.setenv("MSI_IMZML_SHARED_READ", "0")
    root = tmp_path / "custom-default"
    monkeypatch.setattr(ip, "default_cache_root", lambda: root)
    value = entry(source(tmp_path / "raw"))
    result = ip.prepare_imzml(value, spec=spec_for(value),
                              converter=fake_convert, validator=fake_validate)
    assert Path(result["runtime_path"]).is_relative_to(root)


@pytest.fixture
def posix_shared_root(tmp_path, monkeypatch):
    if os.name != "posix":
        pytest.skip("actual POSIX descriptor/ownership integration is exercised on Linux CI")
    root = tmp_path / "shared"
    root.mkdir()
    os.chmod(root, 0o2750)
    monkeypatch.setenv("MSI_IMZML_SHARED_READ", "1")
    monkeypatch.setenv("MSI_SHARED_GID", str(root.stat().st_gid))
    return root


def test_publish_preserves_bytes_keys_and_readonly_group_access(posix_shared_root, tmp_path, monkeypatch):
    raw = source(tmp_path / "raw")
    value = entry(raw)
    spec = spec_for(value)
    saved = ip.prepare_imzml(value, cache_root=posix_shared_root, spec=spec,
                             converter=fake_convert, validator=fake_validate)
    revision = Path(saved["runtime_path"]).parent
    expected_hashes = {p.name: sha256(p.read_bytes()).hexdigest() for p in revision.iterdir()}
    for directory in (posix_shared_root, revision.parent.parent, revision.parent, revision):
        assert stat.S_IMODE(directory.stat().st_mode) == 0o2750
        assert directory.stat().st_gid == posix_shared_root.stat().st_gid
    for path in revision.iterdir():
        assert stat.S_IMODE(path.stat().st_mode) == 0o640
        assert path.stat().st_gid == posix_shared_root.stat().st_gid
    assert ip.validate_asset(saved)["validation"] == saved["validation"]
    assert {p.name: sha256(p.read_bytes()).hexdigest() for p in revision.iterdir()} == expected_hashes
    monkeypatch.setenv("MSI_IMZML_SHARED_READ", "0")
    unshared = ip.prepare_imzml(value, cache_root=tmp_path / "unshared", spec=spec,
                                converter=fake_convert, validator=fake_validate)
    assert unshared["conversion_key"] == saved["conversion_key"]
    assert unshared["validation"] == saved["validation"]


def stage_at(root):
    policy = sharing.shared_read_policy(root)
    parent = root / "project" / "file"
    policy.ensure_parent(parent)
    stage = Path(tempfile.mkdtemp(prefix=".stage-test-", dir=parent))
    (stage / "nested").mkdir()
    (stage / "nested" / "data").write_bytes(b"unchanged")
    return policy, stage


@pytest.mark.parametrize("kind", ["file_symlink", "dir_symlink", "hardlink", "fifo"])
def test_stage_rejects_links_and_special_files_before_permission_changes(posix_shared_root, tmp_path, kind):
    policy, stage = stage_at(posix_shared_root)
    outside = tmp_path / "outside"
    outside.write_bytes(b"protected")
    os.chmod(outside, 0o600)
    if kind == "file_symlink":
        (stage / "unsafe").symlink_to(outside)
    elif kind == "dir_symlink":
        (stage / "unsafe").symlink_to(tmp_path, target_is_directory=True)
    elif kind == "hardlink":
        os.link(outside, stage / "unsafe")
    else:
        os.mkfifo(stage / "unsafe")
    before = stat.S_IMODE((stage / "nested" / "data").stat().st_mode)
    with pytest.raises(sharing.SharedReadError):
        policy.prepare_stage(stage)
    assert stat.S_IMODE(stage.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(outside.stat().st_mode) == 0o600
    assert stat.S_IMODE((stage / "nested" / "data").stat().st_mode) == before


def test_nested_publication_and_cancel_keeps_root_private(posix_shared_root):
    policy, stage = stage_at(posix_shared_root)
    def stop():
        raise ip.PreparationCancelled("cancelled")
    with pytest.raises(ip.PreparationCancelled):
        policy.prepare_stage(stage, cancel=stop)
    assert stat.S_IMODE(stage.stat().st_mode) & 0o077 == 0
    policy.prepare_stage(stage)
    assert stat.S_IMODE((stage / "nested").stat().st_mode) == 0o2750
    assert stat.S_IMODE((stage / "nested" / "data").stat().st_mode) == 0o640


@pytest.mark.parametrize("mode", [0o755, 0o2775, 0o700, 0o750])
def test_root_must_be_prepared_and_is_never_automatically_chmoded(posix_shared_root, mode):
    os.chmod(posix_shared_root, mode)
    with pytest.raises(sharing.SharedReadError, match="2750"):
        sharing.shared_read_policy(posix_shared_root)
    assert stat.S_IMODE(posix_shared_root.stat().st_mode) == mode


def test_existing_unsafe_parent_is_not_modified(posix_shared_root):
    child = posix_shared_root / "project"
    child.mkdir()
    os.chmod(child, 0o2700)
    with pytest.raises(sharing.SharedReadError):
        sharing.shared_read_policy(posix_shared_root).ensure_parent(child / "file")
    assert stat.S_IMODE(child.stat().st_mode) == 0o2700


def test_root_and_parent_symlinks_are_rejected(posix_shared_root, tmp_path):
    alias = tmp_path / "alias"
    alias.symlink_to(posix_shared_root, target_is_directory=True)
    with pytest.raises(OSError):
        sharing.shared_read_policy(alias)
    policy = sharing.shared_read_policy(posix_shared_root)
    (posix_shared_root / "redirect").symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(OSError):
        policy.ensure_parent(posix_shared_root / "redirect" / "new")
    assert not (tmp_path / "new").exists()


def test_wrong_gid_and_owner_are_rejected(posix_shared_root):
    policy = sharing.shared_read_policy(posix_shared_root)
    for uid, gid in ((policy.uid + 1, policy.gid), (policy.uid, policy.gid + 1)):
        with pytest.raises(sharing.SharedReadError):
            policy._check_owner(SimpleNamespace(st_uid=uid, st_gid=gid))


@pytest.mark.parametrize("gid", ["", "-1", "invalid"])
def test_invalid_configured_gid_is_rejected(posix_shared_root, monkeypatch, gid):
    monkeypatch.setenv("MSI_SHARED_GID", gid)
    with pytest.raises(sharing.SharedReadError, match="数値"):
        sharing.shared_read_policy(posix_shared_root)


def test_process_must_belong_to_configured_group(posix_shared_root, monkeypatch):
    gid = max({os.getegid(), *os.getgroups()}) + 1
    monkeypatch.setenv("MSI_SHARED_GID", str(gid))
    with pytest.raises(sharing.SharedReadError, match="参加"):
        sharing.shared_read_policy(posix_shared_root)


def test_outside_or_existing_revision_cannot_be_chmoded(posix_shared_root, tmp_path):
    policy = sharing.shared_read_policy(posix_shared_root)
    for path in (tmp_path / ".stage-outside", posix_shared_root,
                 posix_shared_root / "revision", posix_shared_root / ".." / ".stage-x"):
        with pytest.raises(sharing.SharedReadError):
            policy.prepare_stage(path)


def test_shared_compose_keeps_all_existing_mounts_and_internal_paths():
    repo = Path(__file__).resolve().parents[2]
    compose = (repo / "docker-compose.yml").read_text(encoding="utf-8")
    for env, host in (("TIMS", "TIMS_DATA"), ("DESI", "DESI_DATA"),
                      ("OUTPUT", "OUTPUT_DATA"), ("IMZML_ASSETS", "IMZML_ASSETS")):
        key = "MSI_HOST_IMZML_ASSETS_DIR" if env == "IMZML_ASSETS" else f"MSI_HOST_{env}_DATA_DIR"
        assert f"{key}=${{{host}_HOST:-}}" in compose
    assert "MSI_IMZML_SHARED_READ=${MSI_IMZML_SHARED_READ:-0}" in compose
    assert "IMZML_CACHE_DIR=/app/Data/Other/imzml_assets" in compose
    assert "${IMZML_ASSETS_HOST:-msi-imzml-assets}:/app/Data/Other/imzml_assets" in compose
    for mount in ("projects", "sessions", "presets", "shares", "common", "logs", "cache", "seurat-cache"):
        assert f"- msi-{mount}:/app/Data/Other/" in compose
