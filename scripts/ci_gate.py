"""Fail-closed final check for one CI workflow run; no trading or write access."""

from __future__ import annotations

import http.client
import json
import os
import re
import sys
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

CI_JOBS: Final = frozenset(
    {
        "changes",
        "python-foundation",
        "d01-publication",
        "typescript-foundation",
        "regress",
        "dependency-review",
        "actionlint",
        "secret-scan",
    }
)
_SHA: Final = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY: Final = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict) or any(type(key) is not str for key in value):
        return {}
    return cast(dict[str, object], value)


@dataclass(frozen=True, slots=True)
class PullRequestState:
    number: int
    draft: bool
    state: str
    head_sha: str
    base_sha: str


def pull_request_state(value: object) -> PullRequestState | None:
    """Require an identified PR with typed state and full head/base commit IDs."""

    data = _mapping(value)
    number, draft, state = data.get("number"), data.get("draft"), data.get("state")
    head = _mapping(data.get("head")).get("sha")
    base = _mapping(data.get("base")).get("sha")
    if (
        type(number) is not int
        or number <= 0
        or type(draft) is not bool
        or type(state) is not str
        or type(head) is not str
        or type(base) is not str
        or not _SHA.fullmatch(head)
        or not _SHA.fullmatch(base)
    ):
        return None
    return PullRequestState(number, draft, state, head, base)


def gate_errors(
    needs: Mapping[str, object],
    *,
    event_name: str,
    event_pr: PullRequestState | None = None,
    live_pr: PullRequestState | None = None,
) -> tuple[str, ...]:
    """A success proves this run's CI dependencies, never another workflow's checks."""

    errors: list[str] = []
    if event_name not in {"push", "pull_request"}:
        errors.append("Unsupported CI event.")
    if set(needs) != CI_JOBS:
        errors.append("CI dependency set is missing or unexpected.")
    for name in sorted(CI_JOBS):
        result = _mapping(needs.get(name)).get("result")
        if result == "success":
            continue
        if name == "dependency-review" and event_name == "push" and result == "skipped":
            continue
        errors.append(f"CI job {name} did not succeed.")
    outputs = _mapping(_mapping(needs.get("changes")).get("outputs"))
    if any(outputs.get(area) not in ("true", "false") for area in ("python", "typescript")):
        errors.append("CI classification outputs are missing or invalid.")
    reason = outputs.get("reason")
    # The existing classifier reports both areas false for drafts and docs.
    # Only its explicit docs-only result can justify skipping both suites.
    # Read this run's result even if the live PR was marked ready meanwhile.
    if (
        type(reason) is not str
        or not reason
        or reason.startswith("draft ")
        or (
            outputs.get("python") == "false"
            and outputs.get("typescript") == "false"
            and reason != "docs-only change set"
        )
    ):
        errors.append("This run skipped tests for a draft or lacks classification provenance.")
    if event_name == "pull_request":
        if event_pr is None or live_pr is None:
            errors.append("Current pull request identity and state could not be verified.")
        elif (
            live_pr.state != "open"
            or live_pr.draft
            or event_pr.number != live_pr.number
            or event_pr.head_sha != live_pr.head_sha
            or event_pr.base_sha != live_pr.base_sha
        ):
            errors.append("Pull request is draft, closed, or no longer matches this run's commits.")
    return tuple(errors)


def live_pull_request(repository: str, number: int, token: str | None) -> PullRequestState | None:
    """Read only; an unavailable or malformed API response cannot authorize a merge."""

    if not token or not _REPOSITORY.fullmatch(repository):
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
            value: object = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return pull_request_state(value)


def main() -> int:
    try:
        needs: object = json.loads(os.environ.get("CI_NEEDS", "null"))
        event_name = os.environ.get("GITHUB_EVENT_NAME", "")
        event_pr = live_pr = None
        if event_name == "pull_request":
            payload: object = json.loads(
                Path(os.environ["GITHUB_EVENT_PATH"]).read_text(encoding="utf-8")
            )
            event_pr = pull_request_state(_mapping(payload).get("pull_request"))
            if event_pr is not None:
                live_pr = live_pull_request(
                    os.environ.get("GITHUB_REPOSITORY", ""),
                    event_pr.number,
                    os.environ.get("GITHUB_TOKEN"),
                )
        errors = gate_errors(
            _mapping(needs), event_name=event_name, event_pr=event_pr, live_pr=live_pr
        )
    except (KeyError, OSError, ValueError):
        errors = ("CI gate inputs could not be read.",)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1
    print("CI gate passed for this workflow run. Other required checks remain separate.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
