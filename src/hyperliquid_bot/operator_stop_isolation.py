"""Fail-closed isolation for operator stop helpers.

The OPERATOR_STOP incident was a test/runbook helper sending SIGINT to a live
TerraPC session (``hl-capture`` / ``bn-capture`` / ``bv-capture`` /
``kr-capture``) without isolation. Committed stop scripts must interpolate
``TMUX_SESSION`` and never hard-code those live names on ``tmux send-keys``.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Final

PROTECTED_LIVE_SESSIONS: Final = ("hl-capture", "bn-capture", "bv-capture", "kr-capture")


class OperatorStopIsolationError(ValueError):
    """Raised when a stop helper would target a live capture session by name."""


def require_stop_script_does_not_hardcode_live_targets(script_text: str) -> None:
    """Refuse literal ``tmux send-keys -t <live-session>`` without isolation."""

    for session in PROTECTED_LIVE_SESSIONS:
        needle = f"tmux send-keys -t {session}"
        if needle in script_text:
            raise OperatorStopIsolationError(
                f"Stop helper hard-codes {session!r} as a tmux send-keys target. "
                "Use TMUX_SESSION interpolation and refuse foreign live sessions."
            )


def require_sources_do_not_hardcode_live_stop_targets(
    sources: Iterable[tuple[str, str]],
) -> None:
    """Scan text sources for the same hardcoded live send-keys pattern."""

    for name, text in sources:
        try:
            require_stop_script_does_not_hardcode_live_targets(text)
        except OperatorStopIsolationError as exc:
            raise OperatorStopIsolationError(f"{name}: {exc}") from exc
