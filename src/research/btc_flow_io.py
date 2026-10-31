"""Linux-only immutable research inputs and anchored create-only local outputs."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import TracebackType

import duckdb

from research.btc_flow import require


def read_bytes(path: Path, maximum: int) -> bytes:
    require(path.is_absolute() and ".." not in path.parts, "absolute source path required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for index, name in enumerate(path.parts[1:]):
            nxt = os.open(
                name,
                os.O_RDONLY
                | os.O_NOFOLLOW
                | (os.O_NONBLOCK if index == len(path.parts) - 2 else os.O_DIRECTORY),
                dir_fd=fd,
            )
            os.close(fd)
            fd = nxt
        before = os.fstat(fd)
        require(
            stat.S_ISREG(before.st_mode) and before.st_size <= maximum, "regular source byte limit"
        )
        content = bytearray()
        while block := os.read(fd, 1 << 20):
            content.extend(block)
            require(len(content) <= maximum, "source byte limit")
        after = os.fstat(fd)
        require(
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns),
            "source changed while reading",
        )
        return bytes(content)
    finally:
        os.close(fd)


@contextmanager
def sealed_parquet(
    path: Path, expected_sha256: str
) -> Iterator[tuple[duckdb.DuckDBPyConnection, str]]:
    """One immutable part, one fresh connection; never retain all input FDs.

    Linux fcntl sealing ABI constants are stable; the pinned CPython build does
    not export their symbolic names. All four seals are read back before SQL.
    """
    content = read_bytes(path, 512 << 20)
    require(
        hashlib.sha256(content).hexdigest() == expected_sha256, "approved Parquet bytes required"
    )
    fd = os.memfd_create("btc-flow-approved-part", os.MFD_ALLOW_SEALING | os.MFD_CLOEXEC)
    try:
        view = memoryview(content)
        while view:
            written = os.write(fd, view)
            require(written > 0, "short immutable Parquet write")
            view = view[written:]
        del view, content
        fcntl.fcntl(fd, 1033, 0x000F)  # F_ADD_SEALS: SEAL|SHRINK|GROW|WRITE.
        require(fcntl.fcntl(fd, 1034) == 0x000F, "immutable Parquet seals not verified")
        bound = f"/proc/self/fd/{fd}"
        connection = duckdb.connect(
            config={
                "threads": 2,
                "memory_limit": "2GB",
                "max_temp_directory_size": "0B",
                "enable_external_file_cache": "false",
                "autoload_known_extensions": "false",
                "autoinstall_known_extensions": "false",
            }
        )
        try:
            # DuckDB requires the database to be started before allowed_paths,
            # and the allowlist to be set before disabling external access.
            connection.execute("SET allowed_paths = ?", [[bound]])
            connection.execute("SET enable_external_access = false")
            yield connection, bound
        finally:
            connection.close()
    finally:
        os.close(fd)


def _directory(path: Path) -> int:
    require(path.is_absolute() and ".." not in path.parts, "absolute output path required")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parts[1:]:
            other = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = other
        return fd
    except BaseException:
        os.close(fd)
        raise


def _mount(fd: int) -> str:
    info = Path(f"/proc/self/fdinfo/{fd}").read_text()
    return next(line.split()[1] for line in info.splitlines() if line.startswith("mnt_id:"))


class PrivateOutput:
    """Keep the reserved local directory open through every artifact write."""

    def __init__(self, path: Path) -> None:
        self.path = path
        parent = _directory(path.parent)
        self.fd = -1
        try:
            mount = _mount(parent)
            types = [
                line.partition(" - ")[2].split()[0]
                for line in Path("/proc/self/mountinfo").read_text().splitlines()
                if line.split()[0] == mount
            ]
            require(
                len(types) == 1 and types[0] in ("ext4", "xfs", "btrfs", "tmpfs"),
                "local research output filesystem",
            )
            os.mkdir(path.name, 0o700, dir_fd=parent)
            self.fd = os.open(
                path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent
            )
            require(
                _mount(self.fd) == mount, "output leaf mount differs from verified local parent"
            )
            value = os.fstat(self.fd)
            require(
                value.st_uid == os.getuid() and stat.S_IMODE(value.st_mode) == 0o700,
                "private output ownership",
            )
            self.identity = (value.st_dev, value.st_ino, _mount(self.fd))
            self.assert_bound()
        except BaseException:
            if self.fd >= 0:
                os.close(self.fd)
            raise
        finally:
            os.close(parent)

    def assert_bound(self) -> None:
        other = _directory(self.path)
        try:
            value = os.fstat(other)
            require(
                (value.st_dev, value.st_ino, _mount(other)) == self.identity,
                "output directory was rebound",
            )
        finally:
            os.close(other)

    def write(self, name: str, content: bytes) -> None:
        require(name not in ("", ".", "..") and "/" not in name, "exact output leaf required")
        self.assert_bound()
        fd = os.open(
            name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.fd
        )
        try:
            with os.fdopen(fd, "wb", closefd=False) as stream:
                stream.write(content)
                stream.flush()
            os.fsync(fd)
            self.assert_bound()
        finally:
            os.close(fd)

    def json(self, name: str, payload: object) -> None:
        self.write(
            name, (json.dumps(payload, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        )

    def __enter__(self) -> PrivateOutput:
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if kind is None:
                self.assert_bound()
        finally:
            os.close(self.fd)
