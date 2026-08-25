"""Local composition root guarded by the strict PAPER-only policy."""

import os
from collections.abc import Callable, Mapping
from typing import Literal

from .local_mode import require_local_paper_mode

TRADING_MODE_ENV_VAR = "TRADING_MODE"


def start_local_application[ResolvedT, ApplicationT](
    resolver: Callable[[Literal["PAPER"]], ResolvedT],
    factory: Callable[[ResolvedT], ApplicationT],
    *,
    environment: Mapping[str, str] | None = None,
) -> ApplicationT:
    """Validate raw local mode before resolving or constructing the application."""

    source = os.environ if environment is None else environment
    mode = require_local_paper_mode(source.get(TRADING_MODE_ENV_VAR))
    resolved = resolver(mode)
    return factory(resolved)
