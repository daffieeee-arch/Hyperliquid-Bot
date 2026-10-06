"""Resume-safe downloads. Completed archives are never deleted or replaced."""

from __future__ import annotations

import os
from pathlib import Path

from research.hist_etl.checksums import parse_checksum, sha256_file
from research.hist_etl.disk import assert_free
from research.hist_etl.errors import HistEtlError
from research.hist_etl.http import HttpBody, RateLimiter, Sleeper, Transport, open_with_retries


def head_status(
    url: str,
    *,
    transport: Transport,
    limiter: RateLimiter,
    max_retries: int,
    sleeper: Sleeper,
) -> tuple[int, int | None]:
    with open_with_retries(
        transport,
        "HEAD",
        url,
        None,
        limiter=limiter,
        max_retries=max_retries,
        sleeper=sleeper,
    ) as response:
        return response.status, _content_length(response)


def download_verified_zip(
    *,
    url: str,
    checksum_url: str,
    destination: Path,
    root: Path,
    min_free_bytes: int,
    transport: Transport,
    limiter: RateLimiter,
    max_retries: int,
    sleeper: Sleeper,
) -> str:
    """Fetch a missing zip and its checksum, then promote the partial.

    Returns ``downloaded``. Raises ``HistEtlError`` with exit 2 when the
    archive is absent or the checksum does not match. A failed partial is
    removed because it is scratch this function created; ``destination`` is
    never removed.
    """

    if destination.exists():
        raise HistEtlError(f"refusing to download over existing {destination.name}", exit_code=2)
    destination.parent.mkdir(parents=True, exist_ok=True)
    checksum_path = destination.with_name(destination.name + ".CHECKSUM")
    if not checksum_path.exists():
        status = _download_small(
            checksum_url,
            checksum_path,
            root=root,
            min_free_bytes=min_free_bytes,
            transport=transport,
            limiter=limiter,
            max_retries=max_retries,
            sleeper=sleeper,
        )
        if status == 404:
            raise HistEtlError(f"checksum not found: {checksum_url}", exit_code=2)
        if status != 200:
            raise HistEtlError(f"checksum download returned HTTP {status} for {checksum_url}")
    partial = destination.with_name(destination.name + ".partial")
    status = _download_body(
        url,
        partial,
        root=root,
        min_free_bytes=min_free_bytes,
        transport=transport,
        limiter=limiter,
        max_retries=max_retries,
        sleeper=sleeper,
        resume=True,
    )
    if status == 404:
        _discard_partial(partial)
        raise HistEtlError(f"archive not found: {url}", exit_code=2)
    if status not in {200, 206, 416}:
        raise HistEtlError(f"archive download returned HTTP {status} for {url}")
    if not partial.is_file() and status != 416:
        raise HistEtlError(f"download produced no bytes for {destination.name}", exit_code=2)
    try:
        expected = parse_checksum(checksum_path.read_text(encoding="utf-8"), destination.name)
    except HistEtlError:
        _discard_partial(partial)
        raise
    actual = sha256_file(partial)
    if actual != expected:
        _discard_partial(partial)
        raise HistEtlError(
            f"checksum mismatch for {destination.name}: expected {expected} actual {actual}",
            exit_code=2,
        )
    os.replace(partial, destination)
    return "downloaded"


def download_checksum_beside(
    zip_path: Path,
    checksum_url: str,
    *,
    root: Path,
    min_free_bytes: int,
    transport: Transport,
    limiter: RateLimiter,
    max_retries: int,
    sleeper: Sleeper,
) -> Path:
    destination = zip_path.with_name(zip_path.name + ".CHECKSUM")
    if destination.exists():
        return destination
    status = _download_small(
        checksum_url,
        destination,
        root=root,
        min_free_bytes=min_free_bytes,
        transport=transport,
        limiter=limiter,
        max_retries=max_retries,
        sleeper=sleeper,
    )
    if status == 404:
        raise HistEtlError(f"checksum not found: {checksum_url}", exit_code=2)
    if status != 200:
        raise HistEtlError(f"checksum download returned HTTP {status} for {checksum_url}")
    return destination


def download_opaque(
    url: str,
    destination: Path,
    *,
    root: Path,
    min_free_bytes: int,
    transport: Transport,
    limiter: RateLimiter,
    max_retries: int,
    sleeper: Sleeper,
) -> str:
    """Download one file that has no upstream checksum. Skip when present."""

    if destination.exists():
        return "present"
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".partial")
    status = _download_body(
        url,
        partial,
        root=root,
        min_free_bytes=min_free_bytes,
        transport=transport,
        limiter=limiter,
        max_retries=max_retries,
        sleeper=sleeper,
        resume=True,
    )
    if status == 404:
        _discard_partial(partial)
        raise HistEtlError(f"archive not found: {url}", exit_code=2)
    if status not in {200, 206, 416}:
        raise HistEtlError(f"download returned HTTP {status} for {url}")
    os.replace(partial, destination)
    return "downloaded"


def _download_small(
    url: str,
    destination: Path,
    *,
    root: Path,
    min_free_bytes: int,
    transport: Transport,
    limiter: RateLimiter,
    max_retries: int,
    sleeper: Sleeper,
) -> int:
    partial = destination.with_name(destination.name + ".partial")
    status = _download_body(
        url,
        partial,
        root=root,
        min_free_bytes=min_free_bytes,
        transport=transport,
        limiter=limiter,
        max_retries=max_retries,
        sleeper=sleeper,
        resume=False,
    )
    if status == 200:
        os.replace(partial, destination)
    else:
        _discard_partial(partial)
    return status


def _download_body(
    url: str,
    partial: Path,
    *,
    root: Path,
    min_free_bytes: int,
    transport: Transport,
    limiter: RateLimiter,
    max_retries: int,
    sleeper: Sleeper,
    resume: bool,
) -> int:
    have = partial.stat().st_size if resume and partial.is_file() else 0
    headers: dict[str, str] = {}
    if have > 0:
        headers["Range"] = f"bytes={have}-"
    with open_with_retries(
        transport,
        "GET",
        url,
        headers or None,
        limiter=limiter,
        max_retries=max_retries,
        sleeper=sleeper,
    ) as response:
        if response.status == 404:
            return 404
        if response.status == 416 and have > 0:
            return 416
        if response.status not in {200, 206}:
            return response.status
        incoming = _content_length(response)
        extra = incoming if incoming is not None else 0
        free = assert_free(root, min_free_bytes)
        if incoming is not None and free < min_free_bytes + extra:
            raise HistEtlError(
                f"free space {free} bytes cannot hold {url} ({extra} bytes) "
                f"above the {min_free_bytes} byte floor",
                exit_code=3,
            )
        mode = "ab" if response.status == 206 and have > 0 else "wb"
        partial.parent.mkdir(parents=True, exist_ok=True)
        with partial.open(mode) as handle:
            for chunk in response.iter_bytes():
                handle.write(chunk)
        return response.status


def _content_length(response: HttpBody) -> int | None:
    raw = response.headers.get("content-length")
    if raw is None or not raw.isdigit():
        return None
    return int(raw)


def _discard_partial(path: Path) -> None:
    if path.is_file() and path.name.endswith(".partial"):
        path.unlink()
