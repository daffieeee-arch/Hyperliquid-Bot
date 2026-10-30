"""Fail-closed test/CI isolation for capture operator start and stop helpers.

Only six explicit fake session names are accepted during tests/CI. Normal
operators may use simple session names, with exact tmux targeting in the shell
helpers. This guard is accidental-operation prevention, not a sandbox against
code that deliberately clears its environment or invokes tmux directly.
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping
from typing import Final

PROTECTED_LIVE_SESSIONS: Final = (
    "hl-capture",
    "bn-capture",
    "bv-capture",
    "bv-std-capture",
    "kr-capture",
    "okx-capture",
)
ISOLATED_FAKE_LANES: Final = ("hl", "bn", "bv", "bv-std", "kr", "okx")
ISOLATED_FAKE_SESSIONS: Final = tuple(f"pytest-{lane}-isolated" for lane in ISOLATED_FAKE_LANES)
SESSION_NAME_PATTERN: Final = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
TEST_ISOLATION_ENV_NAMES: Final = (
    "PYTEST_CURRENT_TEST",
    "CI",
    "HYPERLIQUID_BOT_TEST_ISOLATION",
)
ISOLATED_FAKE_SESSION_PREFIX: Final = "pytest-"
FORBIDDEN_BROAD_KILL_PATTERNS: Final = (
    "pkill",
    "killall",
    "tmux kill-server",
    "tmux kill-session -a",
)


class OperatorStopIsolationError(ValueError):
    """Raised when a stop helper would target a live capture session by name."""


def is_protected_live_session(session: object) -> bool:
    return type(session) is str and session in PROTECTED_LIVE_SESSIONS


def is_test_or_ci_environment(environment: Mapping[str, str] | None = None) -> bool:
    """True when pytest, CI, or an explicit isolation flag is present."""

    env = os.environ if environment is None else environment
    return any(env.get(name) for name in TEST_ISOLATION_ENV_NAMES)


def isolated_fake_session(lane: str) -> str:
    """Return a pytest-only session name that is never a live retain."""

    if type(lane) is not str or lane not in ISOLATED_FAKE_LANES:
        raise OperatorStopIsolationError("lane must identify one of the six isolated capture lanes")
    return f"{ISOLATED_FAKE_SESSION_PREFIX}{lane}-isolated"


def require_session_is_not_live_in_test_env(
    session: object,
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Allow only explicit fake names in test/CI; reject tmux target syntax everywhere."""

    if type(session) is not str or re.fullmatch(SESSION_NAME_PATTERN, session) is None:
        raise OperatorStopIsolationError("session must be a simple, bounded tmux session name")
    if is_test_or_ci_environment(environment) and session not in ISOLATED_FAKE_SESSIONS:
        raise OperatorStopIsolationError(
            f"test/CI isolation forbids capture session {session!r}; use an explicit isolated fake"
        )


def require_stop_script_does_not_hardcode_live_targets(script_text: str) -> None:
    """Refuse literal ``tmux send-keys -t <live-session>`` without isolation."""

    for session in PROTECTED_LIVE_SESSIONS:
        target = r"tmux\s+send-keys\s+-t\s+['\"]?=?" + re.escape(session) + r"(?=[:'\"\s]|$)"
        if re.search(target, script_text):
            raise OperatorStopIsolationError(
                f"Stop helper hard-codes {session!r} as a tmux send-keys target. "
                "Use TMUX_SESSION interpolation and refuse foreign live sessions."
            )


def require_sources_do_not_use_broad_kills(script_text: str) -> None:
    """Refuse pkill/killall/tmux kill-server in operator helpers and tests."""

    for pattern in FORBIDDEN_BROAD_KILL_PATTERNS:
        if pattern in script_text:
            raise OperatorStopIsolationError(
                f"Helper uses broad process kill {pattern!r}. "
                "Tests and operators must target one interpolated tmux session."
            )


def require_sources_do_not_hardcode_live_stop_targets(
    sources: Iterable[tuple[str, str]],
) -> None:
    """Scan text sources for the same hardcoded live send-keys pattern."""

    for name, text in sources:
        try:
            require_stop_script_does_not_hardcode_live_targets(text)
            require_sources_do_not_use_broad_kills(text)
        except OperatorStopIsolationError as exc:
            raise OperatorStopIsolationError(f"{name}: {exc}") from exc
