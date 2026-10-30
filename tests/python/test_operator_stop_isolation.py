"""Regression: pytest must not be able to stop live TerraPC capture sessions."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

import pytest

from hyperliquid_bot.operator_stop_isolation import PROTECTED_LIVE_SESSIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
STOP_SCRIPTS = {
    "hl-capture": "data1a_stop.sh",
    "bn-capture": "data1f_stop.sh",
    "bv-capture": "data1e_stop.sh",
    "kr-capture": "data1b_stop.sh",
    "bv-std-capture": "data1d_stop.sh",
    "okx-capture": "data1c_stop.sh",
}


def _fake_tmux(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux = fake_bin / "tmux"
    log = tmp_path / "tmux-invocations.log"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'if [[ "$1" == "has-session" ]]; then exit "${FAKE_TMUX_EXISTS:-0}"; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    tmux.chmod(tmux.stat().st_mode | stat.S_IXUSR)
    return fake_bin


def test_stop_scripts_are_noop_for_live_names_under_pytest(tmp_path: Path) -> None:
    fake_bin = _fake_tmux(tmp_path)
    log = tmp_path / "tmux-invocations.log"
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
    env["HYPERLIQUID_BOT_TEST_ISOLATION"] = "1"
    env["PYTEST_CURRENT_TEST"] = "tests/python/test_operator_stop_isolation.py::test"
    env.pop("TMUX_SESSION", None)
    assert env.get("PYTEST_CURRENT_TEST")

    for session, script in STOP_SCRIPTS.items():
        completed = subprocess.run(
            ["bash", str(SCRIPTS / script)],
            check=False,
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env={**env, "TMUX_SESSION": session},
        )
        text = completed.stdout + completed.stderr
        assert completed.returncode != 0, text
        assert "TEST_ISOLATION_NOOP" in text
        assert session in text
        assert "pkill" not in text
        assert "killall" not in text
        assert "kill-server" not in text

    if log.exists():
        invocations = log.read_text(encoding="utf-8")
        assert "send-keys" not in invocations


def test_isolated_fake_session_is_not_a_protected_live_name() -> None:
    for session in PROTECTED_LIVE_SESSIONS:
        assert not session.startswith("pytest-")


# Independently listed producer defaults: omitting a lane from production guards
# must not silently reduce the regression matrix.
CAPTURE_LANES = {
    "a": ("hl-capture", "pytest-hl-isolated"),
    "b": ("kr-capture", "pytest-kr-isolated"),
    "c": ("okx-capture", "pytest-okx-isolated"),
    "d": ("bv-std-capture", "pytest-bv-std-isolated"),
    "e": ("bv-capture", "pytest-bv-isolated"),
    "f": ("bn-capture", "pytest-bn-isolated"),
}
ISOLATION_MARKERS = (
    {"PYTEST_CURRENT_TEST": "operator-test"},
    {"CI": "true"},
    {"CI": "false"},
    {"CI": "0"},
    {"HYPERLIQUID_BOT_TEST_ISOLATION": "1"},
    {"HYPERLIQUID_BOT_TEST_ISOLATION": "0"},
)
UNSAFE_TARGETS = (
    "unknown",
    "hl",
    "bv-",
    "hl-*",
    "*",
    "=hl-capture",
    "$0",
    "hl-capture:0",
    "hl-capture.0",
    "pytest-hl",
    "pytest-unknown-isolated",
    "pytest-hl-isolated*",
    "-a",
    "é-session",
    "a" * 129,
    "pytest-hl-isolated\n",
)


def _environment(tmp_path: Path, marker: dict[str, str]) -> dict[str, str]:
    from tests.operator_fakes import fake_operator_path

    env = os.environ.copy()
    for key in ("PYTEST_CURRENT_TEST", "CI", "HYPERLIQUID_BOT_TEST_ISOLATION"):
        env.pop(key, None)
    env.update(marker)
    env.update(
        PATH=f"{_fake_tmux(tmp_path)}:{fake_operator_path(tmp_path, env.get('PATH', ''))}",
        REPO_ROOT=str(REPO_ROOT),
        ARTIFACT_ROOT=str(tmp_path / "artifacts"),
        RUN_ID="20261030t000000z-test-isolation",
        DURATION_SECONDS="1",
        TRADING_MODE="PAPER",
    )
    return env


def _invoke(
    lane: str, action: str, env: dict[str, str], *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPTS / f"data1{lane}_{action}.sh"), *args],
        env=env,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )


@pytest.mark.parametrize("marker", ISOLATION_MARKERS)
@pytest.mark.parametrize("lane", CAPTURE_LANES)
def test_all_six_default_lanes_refuse_before_any_tmux_call(
    tmp_path: Path,
    marker: dict[str, str],
    lane: str,
) -> None:
    env = _environment(tmp_path, marker)
    env.pop("TMUX_SESSION", None)
    for action, args in (("stop", ()), ("start", ()), ("start", ("--check-only",))):
        result = _invoke(lane, action, env, *args)
        assert result.returncode != 0
        assert "TEST_ISOLATION_NOOP" in result.stderr
        assert CAPTURE_LANES[lane][0] in result.stderr
    assert not (tmp_path / "tmux-invocations.log").exists()
    assert not (tmp_path / "artifacts").exists()


@pytest.mark.parametrize("lane", CAPTURE_LANES)
def test_foreign_live_names_and_tmux_aliases_cannot_bypass_isolation(
    tmp_path: Path, lane: str
) -> None:
    env = _environment(tmp_path, {"CI": "1"})
    for session in (*PROTECTED_LIVE_SESSIONS, *UNSAFE_TARGETS):
        for action in ("start", "stop"):
            result = _invoke(lane, action, {**env, "TMUX_SESSION": session})
            assert result.returncode != 0, (session, action, result.stdout)
            assert "TEST_ISOLATION_NOOP" in result.stderr
    assert not (tmp_path / "tmux-invocations.log").exists()


@pytest.mark.parametrize("lane", CAPTURE_LANES)
def test_explicit_fake_stops_use_an_exact_session_target(tmp_path: Path, lane: str) -> None:
    env = _environment(tmp_path, {"HYPERLIQUID_BOT_TEST_ISOLATION": "1"})
    session = CAPTURE_LANES[lane][1]
    result = _invoke(lane, "stop", {**env, "TMUX_SESSION": session})
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "tmux-invocations.log").read_text().splitlines() == [
        f"has-session -t ={session}",
        f"send-keys -t ={session}: C-c",
    ]


@pytest.mark.parametrize("lane", CAPTURE_LANES)
def test_normal_operator_stop_still_works_with_exact_default_target(
    tmp_path: Path, lane: str
) -> None:
    env = _environment(tmp_path, {})
    env.pop("TMUX_SESSION", None)
    result = _invoke(lane, "stop", env)
    assert result.returncode == 0, result.stderr
    session = CAPTURE_LANES[lane][0]
    assert f"send-keys -t ={session}: C-c" in (tmp_path / "tmux-invocations.log").read_text()


@pytest.mark.parametrize("marker", (*ISOLATION_MARKERS, {}))
@pytest.mark.parametrize(
    "session", (*PROTECTED_LIVE_SESSIONS, *UNSAFE_TARGETS, "pytest-hl-isolated")
)
def test_python_shell_guards_agree(
    tmp_path: Path,
    marker: dict[str, str],
    session: str,
) -> None:
    from hyperliquid_bot.operator_stop_isolation import (
        OperatorStopIsolationError,
        require_session_is_not_live_in_test_env,
    )

    env = _environment(tmp_path, marker)
    try:
        require_session_is_not_live_in_test_env(session, environment=marker)
        expected = 0
    except OperatorStopIsolationError:
        expected = 1
    # Session is a positional argument, never shell source.
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; data1_require_safe_operator_session "$2"',
            "guard",
            str(SCRIPTS / "data1_operator_isolation.sh"),
            session,
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert result.returncode == expected
    assert not (tmp_path / "tmux-invocations.log").exists()


def test_python_and_shell_protected_sets_cover_every_default() -> None:
    result = subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; data1_protected_live_sessions',
            "guard",
            str(SCRIPTS / "data1_operator_isolation.sh"),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    )
    expected = {sessions[0] for sessions in CAPTURE_LANES.values()}
    assert set(result.stdout.split()) == set(PROTECTED_LIVE_SESSIONS) == expected


@pytest.mark.parametrize("lane", CAPTURE_LANES)
def test_explicit_fake_start_and_check_only_remain_offline(tmp_path: Path, lane: str) -> None:
    env = _environment(tmp_path, {"CI": "true"})
    session = CAPTURE_LANES[lane][1]
    env.update(TMUX_SESSION=session, FAKE_TMUX_EXISTS="1")
    if lane == "e":
        # Synthetic fixtures only; fake tmux never executes a collector.
        env.update(
            BITVAVO_MDPRO_API_KEY="offline-fixture-key",
            BITVAVO_MDPRO_API_SECRET="offline-fixture-secret",
        )
    dry_run = _invoke(lane, "start", env, "--check-only")
    assert dry_run.returncode == 0, dry_run.stderr
    assert "status=CHECK_ONLY" in dry_run.stdout
    before = (tmp_path / "tmux-invocations.log").read_text()
    assert "new-session" not in before
    result = _invoke(lane, "start", env)
    assert result.returncode == 0, result.stderr
    assert "status=STARTED" in result.stdout
    calls = (tmp_path / "tmux-invocations.log").read_text()
    assert f"new-session -d -s {session} " in calls
    assert f"pipe-pane -t ={session}: " in calls
    # Fake tmux records but never executes the collector command string.
    assert not list((tmp_path / "artifacts").rglob("run-claim.json"))


@pytest.mark.parametrize("quote", ("", "'", '"'))
@pytest.mark.parametrize("exact", ("", "="))
@pytest.mark.parametrize("window", ("", ":"))
def test_literal_live_stop_scanner_also_rejects_quoted_exact_targets(
    quote: str, exact: str, window: str
) -> None:
    from hyperliquid_bot.operator_stop_isolation import (
        OperatorStopIsolationError,
        require_stop_script_does_not_hardcode_live_targets,
    )

    for session in PROTECTED_LIVE_SESSIONS:
        command = "tmux send-keys -t " + quote + exact + session + window + quote + " C-c"
        with pytest.raises(OperatorStopIsolationError):
            require_stop_script_does_not_hardcode_live_targets(command)


@pytest.mark.parametrize("lane", ("unknown", "hl*", "hl-isolated:0", "", "hl\n"))
def test_fake_session_factory_refuses_nonlanes(lane: str) -> None:
    from hyperliquid_bot.operator_stop_isolation import (
        OperatorStopIsolationError,
        isolated_fake_session,
    )

    with pytest.raises(OperatorStopIsolationError):
        isolated_fake_session(lane)
