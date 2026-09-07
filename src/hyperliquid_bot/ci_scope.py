"""Classify a GitHub change set so docs-only PRs can skip heavy CI.

Full CI is required when workflow, source, apps, tests, lockfiles, or
Python/TypeScript manifests change. Secret-scan is treated as required for
any code change (not docs-only).
"""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Final

ALWAYS_HEAVY_PREFIXES: Final = (
    ".github/",
    "src/",
    "apps/",
    "tests/",
    "vertical_slices/",
    "fit_gates/",
    "scripts/",
    "infra/",
)
ALWAYS_HEAVY_NAMES: Final = frozenset(
    {
        "uv.lock",
        "pnpm-lock.yaml",
        "pyproject.toml",
        "package.json",
        "pnpm-workspace.yaml",
        "eslint.config.mjs",
        "tsconfig.json",
        "vitest.config.ts",
        ".python-version",
        "prettier.config.mjs",
    }
)
DOCS_PREFIXES: Final = ("docs/",)
DOCS_SUFFIXES: Final = (".md",)


def _is_heavy_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    name = Path(normalized).name
    if name in ALWAYS_HEAVY_NAMES:
        return True
    if normalized.endswith("/package.json") or normalized.endswith("/tsconfig.json"):
        return True
    return any(
        normalized == prefix[:-1] or normalized.startswith(prefix)
        for prefix in ALWAYS_HEAVY_PREFIXES
    )


def _is_docs_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lstrip("./")
    if any(normalized == prefix[:-1] or normalized.startswith(prefix) for prefix in DOCS_PREFIXES):
        return True
    return normalized.endswith(DOCS_SUFFIXES) and "/" not in normalized


def classify_paths(paths: Iterable[str]) -> tuple[bool, bool]:
    """Return ``(heavy, secrets)`` for a changed-path list.

    Empty path lists (for example a merge with no file diff) stay full CI.
    """

    changed = [path.strip() for path in paths if path.strip()]
    if not changed:
        return True, True
    heavy = any(_is_heavy_path(path) for path in changed)
    docs_only = all(_is_docs_path(path) for path in changed)
    if docs_only and not heavy:
        return False, False
    return True, True


def _git_changed_paths(base_ref: str) -> list[str]:
    subprocess.run(
        ["git", "fetch", "--depth=1", "origin", base_ref],
        check=True,
        capture_output=True,
        text=True,
    )
    diff = subprocess.check_output(
        ["git", "diff", "--name-only", f"origin/{base_ref}...HEAD"],
        text=True,
    )
    return [line for line in diff.splitlines() if line]


def classify_github_event(
    *,
    event_name: str,
    base_ref: str | None,
) -> tuple[bool, bool]:
    """Push/main and unknown events stay full CI. PRs use the merge-base diff."""

    if event_name != "pull_request":
        return True, True
    if base_ref is None or base_ref == "":
        return True, True
    return classify_paths(_git_changed_paths(base_ref))


def _write_output(handle: Path | None, key: str, value: bool) -> None:
    rendered = "true" if value else "false"
    print(f"{key}={rendered}")
    if handle is None:
        return
    with handle.open("a", encoding="utf-8") as stream:
        stream.write(f"{key}={rendered}\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit GitHub Actions CI scope outputs.")
    parser.parse_args(argv)
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    base_ref = os.environ.get("GITHUB_BASE_REF") or None
    heavy, secrets = classify_github_event(event_name=event_name, base_ref=base_ref)
    output_path = os.environ.get("GITHUB_OUTPUT")
    handle = Path(output_path) if output_path else None
    _write_output(handle, "heavy", heavy)
    _write_output(handle, "secrets", secrets)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
