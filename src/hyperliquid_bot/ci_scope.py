"""Classify a GitHub change set into the CI areas it can affect.

Two areas gate the heavy jobs:

- ``python``: python-foundation, d01-publication and the regress job's
  Python step;
- ``typescript``: typescript-foundation, the regress job's browser steps and
  the Cockpit workflow's first-paper-screen.

A change set sets an area when any changed path can change a job of that
area. The tables below were built from what each job actually reads (see
docs/CI.md): Python tests assert text in some docs and run the scripts, ruff
lints every Python file in the repository, the cockpit reads the shared
fixtures under tests/fixtures, prettier checks the workflows, and the D01
publication manifest hashes .github/workflows/ci.yml, pyproject.toml and
uv.lock. A path the tables do not know sets both areas. The remaining files
under docs/ set neither, so a change set of only those skips every heavy job,
as before. Docs that Python tests read, and Markdown outside docs/ (which
ruff format checks), are Python paths, so a change to them alone no longer
skips the checks that read them.

A draft pull request sets neither area: its heavy jobs run once it is marked
ready for review (the workflows also trigger on ``ready_for_review``), so
pushing work-in-progress commits costs no heavy CI. The draft state is read
live from the GitHub API, not from the event payload: a payload is a snapshot
from when the event fired, so a late run of a draft-era push, or a re-run,
would otherwise skip on a PR that is already ready. If the live state cannot
be read, the PR is treated as ready and everything runs. Pushes to main and
any other event run everything. The secret scan always runs; it is not gated.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import urllib.request
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Final

# Paths read by jobs of both areas, or by the classifier that gates them.
BOTH_PREFIXES: Final = (
    ".github/",
    "tests/fixtures/",
)
BOTH_NAMES: Final = frozenset({"src/hyperliquid_bot/ci_scope.py", ".editorconfig"})
# At any depth: ruff and prettier honour ignore files, and line endings feed
# ruff format, prettier and the D01 byte hashes.
BOTH_BASENAMES: Final = frozenset({".gitignore", ".ignore", ".gitattributes"})

PYTHON_PREFIXES: Final = (
    "src/",
    "tests/python/",
    "vertical_slices/",
    "fit_gates/",
    "config/",
    "scripts/",
    # Docs whose text or content Python tests assert or load.
    "docs/runbooks/",
    "docs/research/examples/",
    "docs/experiments/",
)
PYTHON_NAMES: Final = frozenset({".python-version", "docs/DATA.md"})
# At any depth: ruff lints every Python file in the repository, and these
# files configure pytest, ruff, mypy or uv wherever they sit. ruff format also
# checks the Python code blocks of every Markdown file outside docs/ (the one
# directory pyproject.toml excludes), so that Markdown is Python too.
PYTHON_SUFFIXES: Final = (".py", ".pyi", ".ipynb")
RUFF_MARKDOWN_SUFFIX: Final = ".md"
PYTHON_BASENAME_PATTERNS: Final = (
    "pyproject.toml",
    "uv.lock",
    "uv.toml",
    "ruff.toml",
    ".ruff.toml",
    "pytest.ini",
    "mypy.ini",
    ".mypy.ini",
    "setup.cfg",
    "tox.ini",
)

TYPESCRIPT_PREFIXES: Final = (
    "apps/cockpit/",
    "tests/typescript/",
)
TYPESCRIPT_NAMES: Final = frozenset({".prettierignore", ".node-version", ".nvmrc"})
# At any depth: pnpm, tsc, eslint, prettier, vitest, next and playwright read
# these wherever they sit, and a script file belongs to the TypeScript tools.
TYPESCRIPT_SUFFIXES: Final = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
TYPESCRIPT_BASENAME_PATTERNS: Final = (
    "package.json",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    ".npmrc",
    ".pnpmfile.*",
    "tsconfig*.json",
    "eslint.config.*",
    ".eslintrc*",
    "prettier.config.*",
    ".prettierrc*",
    "vitest.config.*",
    "vite.config.*",
    "next.config.*",
    "playwright.config.*",
    "postcss.config.*",
)

DOCS_PREFIX: Final = "docs/"


@dataclass(frozen=True, slots=True)
class Scope:
    """The CI areas a change set affects, and why."""

    python: bool
    typescript: bool
    reason: str


FULL: Final = Scope(python=True, typescript=True, reason="full CI")

_REASONS: Final = {
    (False, False): "docs-only change set",
    (True, True): "Python and TypeScript paths changed",
    (True, False): "only Python-area paths changed",
    (False, True): "only TypeScript-area paths changed",
}


def _normalize(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _in_area(path: str, prefixes: Sequence[str], names: frozenset[str]) -> bool:
    return path in names or any(path.startswith(prefix) for prefix in prefixes)


def _named_like(name: str, patterns: Sequence[str]) -> bool:
    return any(fnmatchcase(name, pattern) for pattern in patterns)


def _ruff_checks_markdown(path: str) -> bool:
    return path.endswith(RUFF_MARKDOWN_SUFFIX) and not path.startswith(DOCS_PREFIX)


def path_areas(path: str) -> tuple[bool, bool]:
    """``(python, typescript)`` for one changed path; an unknown path is both."""

    normalized = _normalize(path)
    name = normalized.rsplit("/", 1)[-1]
    if name in BOTH_BASENAMES or _in_area(normalized, BOTH_PREFIXES, BOTH_NAMES):
        return True, True
    python = (
        normalized.endswith(PYTHON_SUFFIXES)
        or _ruff_checks_markdown(normalized)
        or _named_like(name, PYTHON_BASENAME_PATTERNS)
        or _in_area(normalized, PYTHON_PREFIXES, PYTHON_NAMES)
    )
    typescript = (
        normalized.endswith(TYPESCRIPT_SUFFIXES)
        or _named_like(name, TYPESCRIPT_BASENAME_PATTERNS)
        or _in_area(normalized, TYPESCRIPT_PREFIXES, TYPESCRIPT_NAMES)
    )
    if python or typescript:
        return python, typescript
    if normalized.startswith(DOCS_PREFIX):
        # Docs no job reads; the ones Python tests read matched above.
        return False, False
    return True, True


def classify_areas(paths: Iterable[str]) -> Scope:
    """The areas a changed-path list affects.

    An empty list (for example a merge with no file diff) stays full CI.
    """

    changed = [path for path in (_normalize(path) for path in paths) if path]
    if not changed:
        return Scope(python=True, typescript=True, reason="no changed paths; full CI")
    areas = [path_areas(path) for path in changed]
    python = any(path_python for path_python, _ in areas)
    typescript = any(path_typescript for _, path_typescript in areas)
    return Scope(python=python, typescript=typescript, reason=_REASONS[(python, typescript)])


def _git_changed_paths(base_ref: str) -> list[str]:
    subprocess.run(
        ["git", "fetch", "--depth=1", "origin", base_ref],
        check=True,
        capture_output=True,
        text=True,
    )
    # --no-renames lists a moved file's old path as well as its new one, so a
    # move out of an area still counts as a change to that area.
    diff = subprocess.check_output(
        ["git", "diff", "--name-only", "--no-renames", f"origin/{base_ref}...HEAD"],
        text=True,
    )
    return [line for line in diff.splitlines() if line]


def classify_github_event(
    *,
    event_name: str,
    base_ref: str | None,
    draft: bool = False,
) -> Scope:
    """Push/main and unknown events stay full CI. PRs use the merge-base diff."""

    if event_name != "pull_request":
        return FULL
    if draft:
        return Scope(
            python=False,
            typescript=False,
            reason="draft pull request; heavy jobs run when it is marked ready for review",
        )
    if base_ref is None or base_ref == "":
        return FULL
    try:
        changed = _git_changed_paths(base_ref)
    except (OSError, subprocess.SubprocessError) as error:
        return Scope(
            python=True,
            typescript=True,
            reason=f"could not diff against origin/{base_ref} ({type(error).__name__}); full CI",
        )
    return classify_areas(changed)


def pull_request_number(event_path: str | None) -> int | None:
    """The pull request number in the webhook payload at ``event_path``, if any."""

    if not event_path:
        return None
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pull_request = payload.get("pull_request") if isinstance(payload, dict) else None
    number = pull_request.get("number") if isinstance(pull_request, dict) else None
    return number if isinstance(number, int) and not isinstance(number, bool) else None


def live_draft_state(repository: str, number: int, token: str | None) -> bool | None:
    """Whether the pull request is a draft now, read from the GitHub API.

    None when the state cannot be read (no token, a network or HTTP error, an
    unexpected body); the caller then runs everything.
    """

    if not token or "/" not in repository:
        return None
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/pulls/{number}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    draft = body.get("draft") if isinstance(body, dict) else None
    return draft if isinstance(draft, bool) else None


def _write_output(handle: Path | None, key: str, value: bool | str) -> None:
    rendered = value if isinstance(value, str) else ("true" if value else "false")
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
    draft = False
    number = pull_request_number(os.environ.get("GITHUB_EVENT_PATH"))
    if event_name == "pull_request" and number is not None:
        live = live_draft_state(
            os.environ.get("GITHUB_REPOSITORY", ""), number, os.environ.get("GITHUB_TOKEN")
        )
        if live is None:
            print("draft state unavailable; treating the pull request as ready")
        draft = live is True
    scope = classify_github_event(event_name=event_name, base_ref=base_ref, draft=draft)
    output_path = os.environ.get("GITHUB_OUTPUT")
    handle = Path(output_path) if output_path else None
    _write_output(handle, "python", scope.python)
    _write_output(handle, "typescript", scope.typescript)
    _write_output(handle, "reason", scope.reason)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
