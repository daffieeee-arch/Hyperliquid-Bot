"""Fail-closed isolation for operator stop helpers.

The OPERATOR_STOP incident was a test/runbook helper sending SIGINT to a live
TerraPC session (``hl-capture`` / ``bn-capture`` / ``bv-capture`` /
``kr-capture``) without isolation. Committed stop scripts must interpolate
``TMUX_SESSION`` and never hard-code those live names on ``tmux send-keys``.

Tests and CI must never be able to stop live collectors, shared tmux sockets,
runtime PIDs, or evidence. Prefer isolated fake session names. In a test or CI
environment, targeting a protected live session is a no-op / refuse.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from typing import Final

PROTECTED_LIVE_SESSIONS: Final = ("hl-capture", "bn-capture", "bv-capture", "kr-capture")
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
    if env.get("PYTEST_CURRENT_TEST"):
        return True
    if env.get("CI") in {"true", "1", "yes"}:
        return True
    return env.get("HYPERLIQUID_BOT_TEST_ISOLATION") == "1"


def isolated_fake_session(lane: str) -> str:
    """Return a pytest-only session name that is never a live retain."""

    if type(lane) is not str or not lane:
        raise OperatorStopIsolationError("lane must be a non-empty string")
    return f"{ISOLATED_FAKE_SESSION_PREFIX}{lane}-isolated"


def require_session_is_not_live_in_test_env(
    session: object,
    *,
    environment: Mapping[str, str] | None = None,
) -> None:
    """Refuse live retain names when tests or CI invoke a stop helper."""

    if not is_test_or_ci_environment(environment):
        return
    if is_protected_live_session(session):
        raise OperatorStopIsolationError(
            f"test/CI isolation forbids stopping live capture session {session!r}"
        )


def require_stop_script_does_not_hardcode_live_targets(script_text: str) -> None:
    """Refuse literal ``tmux send-keys -t <live-session>`` without isolation."""

    for session in PROTECTED_LIVE_SESSIONS:
        needle = f"tmux send-keys -t {session}"
        if needle in script_text:
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
