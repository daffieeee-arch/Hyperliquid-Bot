"""Rate-limited, retrying HTTP with a streaming body.

Tests inject a transport. The default talks to data.binance.vision and does
not embed credentials.
"""

from __future__ import annotations

import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from research.hist_etl.errors import HistEtlError
from research.hist_etl.models import USER_AGENT

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
Sleeper = Callable[[float], None]


class HttpBody(Protocol):
    status: int
    headers: Mapping[str, str]

    def iter_bytes(self) -> Iterator[bytes]: ...


class Transport(Protocol):
    def open(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
    ) -> AbstractContextManager[HttpBody]: ...


class RateLimiter:
    def __init__(self, per_second: float, sleeper: Sleeper) -> None:
        self._interval = 0.0 if per_second <= 0 else 1.0 / per_second
        self._sleeper = sleeper
        self._next = 0.0

    def wait(self) -> None:
        if self._interval <= 0:
            return
        now = time.monotonic()
        if now < self._next:
            self._sleeper(self._next - now)
        self._next = time.monotonic() + self._interval


class _HeaderItems(Protocol):
    def items(self) -> Iterable[tuple[object, object]]: ...


class _UrllibBody:
    def __init__(
        self,
        status: int,
        headers: _HeaderItems,
        stream: object | None,
    ) -> None:
        self.status = status
        lowered: dict[str, str] = {}
        for key, value in headers.items():
            lowered[str(key).lower()] = str(value)
        self.headers: Mapping[str, str] = lowered
        self._stream = stream

    def iter_bytes(self) -> Iterator[bytes]:
        stream = self._stream
        if stream is None or not hasattr(stream, "read"):
            return
        reader = stream.read
        while True:
            chunk = reader(1024 * 1024)
            if not isinstance(chunk, bytes) or not chunk:
                break
            yield chunk


class UrllibTransport:
    def __init__(self, timeout: float) -> None:
        self._timeout = timeout

    @contextmanager
    def open(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
    ) -> Iterator[_UrllibBody]:
        request_headers = {"User-Agent": USER_AGENT}
        if headers:
            request_headers.update(headers)
        request = urllib.request.Request(url, method=method, headers=request_headers)
        try:
            response = urllib.request.urlopen(request, timeout=self._timeout)
        except urllib.error.HTTPError as exc:
            try:
                stream = None if method == "HEAD" else exc
                yield _UrllibBody(exc.code, exc.headers, stream)
            finally:
                exc.close()
            return
        try:
            stream = None if method == "HEAD" else response
            yield _UrllibBody(response.status, response.headers, stream)
        finally:
            response.close()


def build_transport(timeout: float) -> Transport:
    return UrllibTransport(timeout)


@contextmanager
def open_with_retries(
    transport: Transport,
    method: str,
    url: str,
    headers: Mapping[str, str] | None,
    *,
    limiter: RateLimiter,
    max_retries: int,
    sleeper: Sleeper,
) -> Iterator[HttpBody]:
    """Retry opening the response. Body reads are the caller's job.

    An ``OSError`` raised while the caller reads ``iter_bytes()`` must propagate.
    Catching it here and yielding again raises ``RuntimeError: generator didn't
    stop after throw()`` and aborts the sync.
    """

    delay = 0.5
    last_status = 0
    for attempt in range(1, max_retries + 1):
        limiter.wait()
        opened = transport.open(method, url, headers)
        try:
            response = opened.__enter__()
        except OSError as exc:
            if attempt >= max_retries:
                raise HistEtlError(f"download failed for {url}: {exc}") from exc
            sleeper(delay)
            delay *= 2
            continue
        if response.status in RETRYABLE_STATUS and attempt < max_retries:
            last_status = response.status
            opened.__exit__(None, None, None)
            sleeper(delay)
            delay *= 2
            continue
        try:
            yield response
        finally:
            exc_type, raised, traceback = sys.exc_info()
            opened.__exit__(exc_type, raised, traceback)
        return
    raise HistEtlError(f"download failed for {url} after HTTP {last_status}")
