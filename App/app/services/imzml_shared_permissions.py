"""Opt-in, metadata-only sharing of newly completed imzML revisions.

The administrator prepares the shared root. Existing revisions are never chmod'ed
here. Directory descriptors and O_NOFOLLOW keep permission changes inside the
private staging tree; group members receive read access, not write access.
"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import stat


class SharedReadError(ValueError):
    pass


def shared_read_policy(root):
    if os.environ.get("MSI_IMZML_SHARED_READ", "0") != "1":
        return None
    if os.name != "posix":
        raise SharedReadError("MSI_IMZML_SHARED_READ=1 は POSIX の共有保存先専用です。")
    raw_gid = os.environ.get("MSI_SHARED_GID", "")
    if not raw_gid.isdecimal():
        raise SharedReadError("共有保存には数値の MSI_SHARED_GID を設定してください。")
    gid = int(raw_gid)
    if gid not in {os.getegid(), *os.getgroups()}:
        raise SharedReadError("アプリが MSI_SHARED_GID の共有グループに参加していません。")
    return SharedReadPolicy(root, gid)


class SharedReadPolicy:
    DIRECTORY_MODE = 0o2750
    FILE_MODE = 0o640

    def __init__(self, root, gid):
        # abspath deliberately does not resolve symlinks before checking them.
        self.root = Path(os.path.abspath(os.path.expanduser(os.fspath(root))))
        self.gid = gid
        self.uid = os.geteuid()
        self._identity = None
        with self._root_fd() as fd:
            info = os.fstat(fd)
            self._identity = (info.st_dev, info.st_ino)

    @staticmethod
    def _open_directory(name, *, dir_fd=None):
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=dir_fd)

    def _check_owner(self, info):
        if info.st_uid != self.uid or info.st_gid != self.gid:
            raise SharedReadError("共有保存先の所有者または共有グループが一致しません。移行手順を確認してください。")

    def _check_directory(self, info):
        self._check_owner(info)
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) != self.DIRECTORY_MODE:
            raise SharedReadError("共有保存先のディレクトリはアプリ所有・共有 GID・mode 2750 が必要です。")

    @contextmanager
    def _root_fd(self):
        fd = self._open_directory(self.root.anchor)
        try:
            for part in self.root.parts[1:]:
                next_fd = self._open_directory(part, dir_fd=fd)
                os.close(fd)
                fd = next_fd
            info = os.fstat(fd)
            self._check_directory(info)
            if self._identity is not None and self._identity != (info.st_dev, info.st_ino):
                raise SharedReadError("共有保存先が処理中に置換されました。")
            yield fd
        finally:
            os.close(fd)

    def _relative_parts(self, path):
        path = Path(path)
        if ".." in path.parts:
            raise SharedReadError("共有保存先の親参照は許可されません。")
        try:
            return path.relative_to(self.root).parts
        except ValueError as exc:
            raise SharedReadError("共有保存先の管理領域外は変更しません。") from exc

    @contextmanager
    def _parent_fd(self, parts, *, create=False):
        with self._root_fd() as root_fd:
            fd = os.dup(root_fd)
            try:
                for part in parts:
                    created = False
                    if create:
                        try:
                            os.mkdir(part, self.DIRECTORY_MODE, dir_fd=fd)
                            created = True
                        except FileExistsError:
                            pass
                    next_fd = self._open_directory(part, dir_fd=fd)
                    os.close(fd)
                    fd = next_fd
                    if created:
                        self._check_owner(os.fstat(fd))
                        os.fchmod(fd, self.DIRECTORY_MODE)
                    self._check_directory(os.fstat(fd))
                yield fd
            finally:
                os.close(fd)

    def ensure_parent(self, path):
        with self._parent_fd(self._relative_parts(path), create=True):
            pass

    def ensure_marker(self, name):
        if Path(name).name != name:
            raise SharedReadError("Invalid cache marker name")
        with self._root_fd() as fd:
            try:
                marker = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 self.FILE_MODE, dir_fd=fd)
            except FileExistsError:
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise SharedReadError("共有保存先の管理マーカーが通常ファイルではありません。")
            else:
                try:
                    self._check_owner(os.fstat(marker))
                    os.fchmod(marker, self.FILE_MODE)
                finally:
                    os.close(marker)

    def _check_stage_item(self, info):
        self._check_owner(info)
        if stat.S_ISREG(info.st_mode):
            if info.st_nlink != 1:
                raise SharedReadError("共有公開する一時領域にハードリンクは置けません。")
        elif not stat.S_ISDIR(info.st_mode):
            raise SharedReadError("共有公開する一時領域にリンク・特殊ファイルは置けません。")

    def _walk(self, fd, *, apply, cancel):
        for name in os.listdir(fd):
            if cancel:
                cancel()
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            self._check_stage_item(info)
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if stat.S_ISDIR(info.st_mode):
                flags |= os.O_DIRECTORY
            child = os.open(name, flags, dir_fd=fd)
            try:
                opened = os.fstat(child)
                self._check_stage_item(opened)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise SharedReadError("共有公開する一時領域が処理中に変更されました。")
                if stat.S_ISDIR(opened.st_mode):
                    self._walk(child, apply=apply, cancel=cancel)
                if apply:
                    mode = self.DIRECTORY_MODE if stat.S_ISDIR(opened.st_mode) else self.FILE_MODE
                    os.fchmod(child, mode)
            finally:
                os.close(child)

    def prepare_stage(self, path, *, cancel=None):
        """Preflight first, then share descendants and finally the private root."""
        parts = self._relative_parts(path)
        if not parts or not parts[-1].startswith(".stage-"):
            raise SharedReadError("権限変更は未公開の .stage- 領域に限定されています。")
        with self._parent_fd(parts[:-1]) as parent:
            stage = self._open_directory(parts[-1], dir_fd=parent)
            try:
                info = os.fstat(stage)
                self._check_owner(info)
                if stat.S_IMODE(info.st_mode) & 0o077:
                    raise SharedReadError("共有公開前の一時領域が既に他ユーザーへ公開されています。")
                self._walk(stage, apply=False, cancel=cancel)
                self._walk(stage, apply=True, cancel=cancel)
                if cancel:
                    cancel()
                os.fchmod(stage, self.DIRECTORY_MODE)
            finally:
                os.close(stage)
