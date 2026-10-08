"""The CI change-set classifier: areas, docs, drafts, failures, and the workflow wiring."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from hyperliquid_bot import ci_scope
from hyperliquid_bot.ci_scope import (
    classify_areas,
    classify_github_event,
    path_areas,
    pull_request_is_draft,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO_ROOT / ".github" / "workflows"


@pytest.mark.parametrize(
    ("path", "areas"),
    [
        # Python area.
        ("src/research/harness/portfolio.py", (True, False)),
        ("tests/python/test_research_portfolio.py", (True, False)),
        ("vertical_slices/d01_btc_perp/README.md", (True, False)),
        ("fit_gates/d41_nautilus/requirements.lock", (True, False)),
        ("config/hist_etl/datasets.toml", (True, False)),
        ("scripts/data1a_stop.sh", (True, False)),
        ("uv.lock", (True, False)),
        (".python-version", (True, False)),
        ("docs/DATA.md", (True, False)),
        ("docs/runbooks/data1a-wsl-pc-retained-capture.md", (True, False)),
        ("docs/research/examples/panel-template.spec.yaml", (True, False)),
        ("docs/experiments/exp_h1_leadlag.registry.template.json", (True, False)),
        # Any Python file or Python tool config, wherever it sits.
        ("apps/api/server.py", (True, False)),
        ("conftest.py", (True, False)),
        ("tests/conftest.py", (True, False)),
        # TypeScript area.
        ("apps/cockpit/src/lib/paths.ts", (False, True)),
        ("apps/cockpit/package.json", (False, True)),
        ("tests/typescript/toolchain.test.ts", (False, True)),
        ("pnpm-lock.yaml", (False, True)),
        ("eslint.config.mjs", (False, True)),
        (".prettierignore", (False, True)),
        # Both: workflows, shared fixtures, the classifier itself, ignore files.
        (".github/workflows/cockpit.yml", (True, True)),
        (".github/actions/setup-node-pnpm/action.yml", (True, True)),
        ("tests/fixtures/course1_cockpit/sample-run/orders.json", (True, True)),
        ("src/hyperliquid_bot/ci_scope.py", (True, True)),
        (".gitignore", (True, True)),
        ("apps/cockpit/.gitignore", (True, True)),
        (".gitattributes", (True, True)),
        # Unknown paths run everything.
        ("infra/compose.yaml", (True, True)),
        (".env.example", (True, True)),
        # Docs no job reads.
        ("docs/ROADMAP.md", (False, False)),
        ("docs/research/cross-sectional-panel.md", (False, False)),
        # ruff format checks Python code blocks in Markdown outside docs/.
        ("README.md", (True, False)),
        ("AGENTS.md", (True, False)),
        ("apps/api/README.md", (True, False)),
        ("apps/cockpit/README.md", (True, True)),
        ("notebooks/study.ipynb", (True, False)),
    ],
)
def test_path_areas(path: str, areas: tuple[bool, bool]) -> None:
    assert path_areas(path) == areas


def test_paths_are_normalized_by_prefix_not_by_character() -> None:
    # str.lstrip("./") would turn .github/ into github/ and miss the rule.
    assert path_areas("./.github/workflows/ci.yml") == (True, True)
    assert path_areas(".\\.github\\workflows\\ci.yml") == (True, True)
    assert path_areas("./docs/ROADMAP.md") == (False, False)


def test_change_sets_combine_their_paths() -> None:
    docs_only = classify_areas(["docs/ROADMAP.md", "docs/ARCHITECTURE.md"])
    assert (docs_only.python, docs_only.typescript) == (False, False)
    assert docs_only.reason == "docs-only change set"
    research = classify_areas(["docs/research/cross-sectional-panel.md", "src/research/a.py"])
    assert (research.python, research.typescript) == (True, False)
    cockpit = classify_areas(["apps/cockpit/src/app/page.tsx", "docs/ROADMAP.md"])
    assert (cockpit.python, cockpit.typescript) == (False, True)
    both = classify_areas(["src/research/a.py", "apps/cockpit/src/app/page.tsx"])
    assert (both.python, both.typescript) == (True, True)
    empty = classify_areas([])
    assert (empty.python, empty.typescript) == (True, True)


def test_events_other_than_a_ready_pull_request(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_git(base_ref: str) -> list[str]:
        raise AssertionError("no diff is taken for this event")

    monkeypatch.setattr(ci_scope, "_git_changed_paths", no_git)
    push = classify_github_event(event_name="push", base_ref=None)
    assert (push.python, push.typescript) == (True, True)
    draft = classify_github_event(event_name="pull_request", base_ref="main", draft=True)
    assert (draft.python, draft.typescript) == (False, False)
    assert "draft" in draft.reason
    no_base = classify_github_event(event_name="pull_request", base_ref="")
    assert (no_base.python, no_base.typescript) == (True, True)


def test_a_moved_file_counts_in_its_old_and_new_area(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Ignore the host's git config (commit signing, hooks, templates).
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "ci@example.invalid")
    git("config", "user.name", "ci")
    fixture = tmp_path / "tests" / "python" / "data.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text("{}", encoding="utf-8")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    git("checkout", "-q", "-b", "feature")
    (tmp_path / "apps" / "cockpit").mkdir(parents=True)
    git("mv", "tests/python/data.json", "apps/cockpit/data.json")
    git("commit", "-q", "-m", "move")
    git("remote", "add", "origin", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    changed = ci_scope._git_changed_paths("main")
    assert sorted(changed) == ["apps/cockpit/data.json", "tests/python/data.json"]
    scope = classify_areas(changed)
    assert (scope.python, scope.typescript) == (True, True)


def test_a_failed_diff_runs_everything(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_git(base_ref: str) -> list[str]:
        raise subprocess.CalledProcessError(128, ["git", "fetch"])

    monkeypatch.setattr(ci_scope, "_git_changed_paths", failing_git)
    scope = classify_github_event(event_name="pull_request", base_ref="main")
    assert (scope.python, scope.typescript) == (True, True)
    assert "full CI" in scope.reason


def test_draft_flag_is_read_from_the_event_payload(tmp_path: Path) -> None:
    draft = tmp_path / "draft.json"
    draft.write_text(json.dumps({"pull_request": {"draft": True}}), encoding="utf-8")
    ready = tmp_path / "ready.json"
    ready.write_text(json.dumps({"pull_request": {"draft": False}}), encoding="utf-8")
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    assert pull_request_is_draft(str(draft)) is True
    assert pull_request_is_draft(str(ready)) is False
    assert pull_request_is_draft(str(broken)) is False
    assert pull_request_is_draft(str(tmp_path / "missing.json")) is False
    assert pull_request_is_draft(None) is False


def test_main_writes_the_outputs_the_workflows_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"draft": True}}), encoding="utf-8")
    output = tmp_path / "output.txt"
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")
    assert ci_scope.main([]) == 0
    written = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert written["python"] == "false"
    assert written["typescript"] == "false"
    assert set(written) == {"python", "typescript", "reason"}
    # Every output a workflow job reads is one main() writes and the changes job exports.
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        read = set(re.findall(r"needs\.changes\.outputs\.([a-z_]+)", text))
        exported = set(re.findall(r"steps\.scope\.outputs\.([a-z_]+)", text))
        assert read <= exported <= set(written), workflow.name


# The classifier and its own tests name docs paths as data; they read none.
_CLASSIFIER_FILES = frozenset({"src/hyperliquid_bot/ci_scope.py", "tests/python/test_ci_scope.py"})
_SCANNED_TREES = ("tests", "src", "vertical_slices", "fit_gates", "scripts")


def test_a_rerun_of_a_draft_run_does_not_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A re-run replays the draft-era payload; it must classify the diff instead.
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"pull_request": {"draft": True}}), encoding="utf-8")
    output = tmp_path / "output.txt"
    monkeypatch.setattr(ci_scope, "_git_changed_paths", lambda base_ref: ["src/a.py"])
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    monkeypatch.setenv("GITHUB_BASE_REF", "main")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert ci_scope.main([]) == 0
    written = dict(line.split("=", 1) for line in output.read_text(encoding="utf-8").splitlines())
    assert (written["python"], written["typescript"]) == ("true", "false")


_DOCS_LITERAL = re.compile(r"""["'](docs/[A-Za-z0-9_./-]+)["']""")
_DOCS_COMPONENT = re.compile(r"""["']docs["']((?:\s*/\s*["'][A-Za-z0-9_.-]+["'])*)""")


def _docs_reads_in(text: str) -> set[str]:
    """Docs paths named in source text, as literals or ``"docs" / ...`` joins.

    A bare ``"docs"`` component not followed by literal parts (a path built
    through a variable) is recorded as ``docs`` itself, which no area table
    covers, so the guard fails until the read is spelled out or covered.
    """

    found = {match.group(1) for match in _DOCS_LITERAL.finditer(text)}
    for match in _DOCS_COMPONENT.finditer(text):
        parts = re.findall(r"""["']([A-Za-z0-9_.-]+)["']""", match.group(1))
        found.add("/".join(["docs", *parts]))
    return found


def _docs_paths_read_by_python() -> set[str]:
    found: set[str] = set()
    for tree in _SCANNED_TREES:
        for source in sorted((REPO_ROOT / tree).rglob("*")):
            relative = source.relative_to(REPO_ROOT).as_posix()
            if source.suffix not in {".py", ".sh"} or relative in _CLASSIFIER_FILES:
                continue
            found |= _docs_reads_in(source.read_text(encoding="utf-8"))
    return found


def test_every_doc_python_code_reads_is_a_python_path() -> None:
    read = _docs_paths_read_by_python()
    # The scan must see the reads it guards, or it guards nothing.
    assert "docs/DATA.md" in read
    assert any(path.startswith("docs/runbooks/") for path in read)
    for path in sorted(read):
        # A directory read covers every file under it.
        probe = path if "." in path.rsplit("/", 1)[-1] else f"{path}/any.md"
        assert path_areas(probe)[0], f"{path} is read by Python code but skips Python CI"


def test_the_docs_scan_sees_single_quotes_and_bare_components() -> None:
    sample = "A = ROOT / 'docs' / 'RISK.md'\nDOCS = ROOT / \"docs\"\nB = \"docs/x/y.md\"\n"
    assert _docs_reads_in(sample) == {"docs/RISK.md", "docs", "docs/x/y.md"}
