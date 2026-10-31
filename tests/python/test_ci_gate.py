"""A draft skip, stale PR identity, or unavailable CI evidence cannot pass the gate."""

from __future__ import annotations

import http.client
import json
import re
import urllib.request
from pathlib import Path

import pytest
from scripts import ci_gate
from scripts.ci_gate import CI_JOBS, PullRequestState, gate_errors, pull_request_state

from hyperliquid_bot.ci_scope import classify_github_event

HEAD = "a" * 40
BASE = "b" * 40
READY = PullRequestState(42, False, "open", HEAD, BASE)


def _needs() -> dict[str, object]:
    return {
        name: {
            "result": "success",
            "outputs": {
                "python": "true",
                "typescript": "false",
                "reason": "only Python-area paths changed",
            }
            if name == "changes"
            else {},
        }
        for name in CI_JOBS
    }


def _payload() -> dict[str, object]:
    return {
        "number": 42,
        "state": "open",
        "draft": False,
        "head": {"sha": HEAD},
        "base": {"sha": BASE},
    }


def test_ready_pr_and_docs_only_run_pass() -> None:
    assert gate_errors(_needs(), event_name="pull_request", event_pr=READY, live_pr=READY) == ()
    needs = _needs()
    needs["changes"] = {
        "result": "success",
        "outputs": {"python": "false", "typescript": "false", "reason": "docs-only change set"},
    }
    assert gate_errors(needs, event_name="pull_request", event_pr=READY, live_pr=READY) == ()


@pytest.mark.parametrize("name", sorted(CI_JOBS))
@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped", None])
def test_every_dependency_must_have_successful_evidence(name: str, result: object) -> None:
    needs = _needs()
    needs[name] = {"result": result}
    assert gate_errors(needs, event_name="pull_request", event_pr=READY, live_pr=READY)


def test_missing_and_unexpected_dependencies_fail() -> None:
    needs = _needs()
    del needs["regress"]
    assert gate_errors(needs, event_name="push")
    needs = _needs()
    needs["unregistered-check"] = {"result": "success"}
    assert gate_errors(needs, event_name="push")


def test_dependency_review_may_skip_only_on_main_push() -> None:
    needs = _needs()
    needs["dependency-review"] = {"result": "skipped"}
    assert gate_errors(needs, event_name="push") == ()
    assert gate_errors(needs, event_name="pull_request", event_pr=READY, live_pr=READY)
    assert gate_errors(needs, event_name="workflow_dispatch")


@pytest.mark.parametrize(
    "outputs",
    [
        {},
        {"python": "false", "typescript": "false"},
        {"python": "false", "typescript": "false", "reason": "unknown skip"},
        {"python": "true", "typescript": None, "reason": "only Python-area paths changed"},
        {"python": [], "typescript": "false", "reason": "only Python-area paths changed"},
    ],
)
def test_a_ready_pr_cannot_reuse_draft_or_missing_classification(outputs: object) -> None:
    needs = _needs()
    needs["changes"] = {"result": "success", "outputs": outputs}
    assert gate_errors(needs, event_name="pull_request", event_pr=READY, live_pr=READY)


def test_actual_draft_classifier_output_cannot_pass_after_pr_is_marked_ready() -> None:
    draft = classify_github_event(event_name="pull_request", base_ref="main", draft=True)
    needs = _needs()
    needs["changes"] = {
        "result": "success",
        "outputs": {
            "python": str(draft.python).lower(),
            "typescript": str(draft.typescript).lower(),
            "reason": draft.reason,
        },
    }
    assert gate_errors(needs, event_name="pull_request", event_pr=READY, live_pr=READY)


@pytest.mark.parametrize(
    "live",
    [
        None,
        PullRequestState(42, True, "open", HEAD, BASE),
        PullRequestState(42, False, "closed", HEAD, BASE),
        PullRequestState(43, False, "open", HEAD, BASE),
        PullRequestState(42, False, "open", "c" * 40, BASE),
        PullRequestState(42, False, "open", HEAD, "c" * 40),
    ],
)
def test_gate_is_bound_to_current_open_ready_pr_and_both_commits(
    live: PullRequestState | None,
) -> None:
    assert gate_errors(_needs(), event_name="pull_request", event_pr=READY, live_pr=live)


def test_missing_event_pr_identity_fails() -> None:
    assert gate_errors(_needs(), event_name="pull_request", live_pr=READY)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("number", True),
        ("number", 0),
        ("number", "42"),
        ("draft", "false"),
        ("state", None),
        ("head", {"sha": "short"}),
        ("head", None),
        ("base", {"sha": "G" * 40}),
    ],
)
def test_malformed_pull_request_identity_is_unavailable(key: str, value: object) -> None:
    payload = _payload()
    assert pull_request_state(payload) == READY
    payload[key] = value
    assert pull_request_state(payload) is None


class _Response:
    def __init__(self, body: bytes | Exception) -> None:
        self.body = body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        if isinstance(self.body, Exception):
            raise self.body
        return self.body


def test_live_pr_request_is_read_only_and_unavailable_on_bad_responses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses: list[bytes | Exception] = [
        json.dumps(_payload()).encode(),
        b"{",
        b"\xff",
        http.client.IncompleteRead(b"{", 8),
    ]
    bodies = iter(responses)

    def urlopen(request: urllib.request.Request, timeout: int) -> _Response:
        assert request.get_method() == "GET"
        assert request.full_url == "https://api.github.com/repos/owner/repo/pulls/42"
        assert timeout == 15
        return _Response(next(bodies))

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert ci_gate.live_pull_request("owner/repo", 42, "test-token") == READY
    for _ in range(3):
        assert ci_gate.live_pull_request("owner/repo", 42, "test-token") is None
    assert ci_gate.live_pull_request("owner/repo", 42, None) is None
    assert ci_gate.live_pull_request("owner/repo/../other", 42, "test-token") is None

    def unavailable(request: urllib.request.Request, timeout: int) -> _Response:
        raise TimeoutError("unavailable")

    monkeypatch.setattr(urllib.request, "urlopen", unavailable)
    assert ci_gate.live_pull_request("owner/repo", 42, "test-token") is None


def test_cli_uses_this_run_event_and_fails_on_unreadable_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": _payload()}), encoding="utf-8")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("CI_NEEDS", json.dumps(_needs()))
    monkeypatch.setattr(ci_gate, "live_pull_request", lambda repository, number, token: READY)
    assert ci_gate.main() == 0
    event.write_text("{", encoding="utf-8")
    assert ci_gate.main() == 1
    event.unlink()
    assert ci_gate.main() == 1
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    monkeypatch.setenv("CI_NEEDS", "null")
    assert ci_gate.main() == 1
    monkeypatch.setenv("CI_NEEDS", "{")
    assert ci_gate.main() == 1


def test_final_gate_depends_on_every_other_job_in_its_workflow() -> None:
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
    text = workflow.read_text(encoding="utf-8")
    jobs = text.split("\njobs:\n", 1)[1]
    job_names = set(re.findall(r"^  ([a-z0-9-]+):$", jobs, re.MULTILINE))
    gate = jobs.split("\n  ci-gate:\n", 1)[1]
    declared_dependencies = set(re.findall(r"^      - ([a-z0-9-]+)$", gate, re.MULTILINE))
    assert job_names - {"ci-gate"} == CI_JOBS == declared_dependencies
    assert "if: ${{ always() }}" in gate
