"""The always-on Claude Remote Control scripts (scripts/claude_rc_*.sh).

Every tmux call here uses its own socket and a pytest- session name, so the
tests never reach the capture or research sessions on the default server.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER = REPO_ROOT / "scripts" / "claude_rc_run.sh"
INSTALLER = REPO_ROOT / "scripts" / "claude_rc_install.sh"
SESSION = "pytest-claude-rc"

needs_tmux = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux is not installed")


def _executable(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def _fake_claude(tmp_path: Path) -> Path:
    """Print its arguments and folder, then exit 7, or 130 on Ctrl+C."""
    marker = tmp_path / "interrupted"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return _executable(
        bin_dir / "claude",
        "#!/usr/bin/env bash\n"
        "printf 'fake claude arg <%s>\\n' \"$@\"\n"
        'echo "fake claude cwd: $(pwd)"\n'
        f"trap 'touch \"{marker}\"; exit 130' INT\n"
        'for _ in $(seq 1 "${FAKE_SECONDS:-1}"); do sleep 1; done\n'
        "exit 7\n",
    )


def _spaced_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "Hyperliquid Project" / "Hyperliquid-Bot"
    repo.mkdir(parents=True)
    return repo


def _runner_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        **os.environ,
        "CLAUDE_RC_TMUX_SOCKET": f"pytest-claude-rc-{uuid.uuid4().hex[:12]}",
        "CLAUDE_RC_TMUX_SESSION": SESSION,
        "CLAUDE_RC_POLL_SECONDS": "0.2",
        "CLAUDE_RC_STOP_WAIT_SECONDS": "5",
        "CLAUDE_RC_MISSING_PAUSE_SECONDS": "0",
        "CLAUDE_RC_REPO": str(_spaced_repo(tmp_path)),
        "CLAUDE_RC_BIN": str(_fake_claude(tmp_path)),
        "CLAUDE_RC_NAME": "hyperliquid vps",
    }
    env.pop("TMUX", None)
    env.update(extra)
    return env


def _end_test_session(env: dict[str, str]) -> None:
    subprocess.run(
        ["tmux", "-L", env["CLAUDE_RC_TMUX_SOCKET"], "kill-session", "-t", SESSION],
        check=False,
        capture_output=True,
    )


def _session_alive(env: dict[str, str]) -> bool:
    probe = subprocess.run(
        ["tmux", "-L", env["CLAUDE_RC_TMUX_SOCKET"], "has-session", "-t", SESSION],
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


@needs_tmux
def test_runner_starts_claude_in_the_spaced_repo_and_reports_its_exit(tmp_path: Path) -> None:
    env = _runner_env(tmp_path)
    try:
        completed = subprocess.run(
            ["bash", str(RUNNER)],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
    finally:
        _end_test_session(env)

    assert completed.returncode == 1, completed.stderr
    assert "remote-control started in" in completed.stdout
    assert "exited with status 7" in completed.stderr
    # The name stays one argument and the session starts in the spaced folder.
    assert (
        "fake claude arg <remote-control>\n"
        "fake claude arg <--name>\n"
        "fake claude arg <hyperliquid vps>\n"
    ) in completed.stderr
    assert f"fake claude cwd: {env['CLAUDE_RC_REPO']}" in completed.stderr
    assert not _session_alive(env)


@needs_tmux
def test_stop_sends_ctrl_c_and_the_runner_exits(tmp_path: Path) -> None:
    env = _runner_env(tmp_path, FAKE_SECONDS="60")
    runner = subprocess.Popen(
        ["bash", str(RUNNER)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not _session_alive(env) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert _session_alive(env)

        stopped = subprocess.run(
            ["bash", str(RUNNER), "--stop"],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
        assert stopped.returncode == 0, stopped.stderr
        assert runner.wait(timeout=30) == 1
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait()
        _end_test_session(env)

    assert (tmp_path / "interrupted").exists()
    assert not _session_alive(env)


def test_runner_waits_and_fails_when_claude_is_missing(tmp_path: Path) -> None:
    env = _runner_env(tmp_path, CLAUDE_RC_BIN=str(tmp_path / "no-such-claude"))
    completed = subprocess.run(
        ["bash", str(RUNNER)], check=False, capture_output=True, env=env, text=True, timeout=30
    )

    assert completed.returncode == 1
    assert "claude is not executable" in completed.stderr


def test_runner_rejects_unknown_arguments(tmp_path: Path) -> None:
    completed = subprocess.run(
        ["bash", str(RUNNER), "--bogus"],
        check=False,
        capture_output=True,
        env=_runner_env(tmp_path),
        text=True,
    )

    assert completed.returncode == 2
    assert "Usage" in completed.stderr


def _non_root_user() -> str:
    for name in ("nobody", "daemon"):
        if subprocess.run(["getent", "passwd", name], capture_output=True).returncode == 0:
            return name
    pytest.skip("no unprivileged system user to render a unit for")


def _print_unit(tmp_path: Path, **extra: str) -> subprocess.CompletedProcess[str]:
    libexec = tmp_path / "libexec"
    libexec.mkdir(exist_ok=True)
    runner_copy = libexec / "claude-rc-run"
    if not runner_copy.exists():
        shutil.copy(RUNNER, runner_copy)
        runner_copy.chmod(0o755)
    env = {
        **os.environ,
        "CLAUDE_RC_USER": _non_root_user(),
        "CLAUDE_RC_REPO": str(_spaced_repo(tmp_path)),
        "CLAUDE_RC_BIN": str(_fake_claude(tmp_path)),
        "CLAUDE_RC_LIBEXEC_DIR": str(libexec),
    }
    env.pop("CLAUDE_RC_NAME", None)
    env.update(extra)
    return subprocess.run(
        ["bash", str(INSTALLER), "--print-unit"],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def test_print_unit_quotes_the_spaced_repo_and_never_runs_as_root(tmp_path: Path) -> None:
    completed = _print_unit(tmp_path)
    assert completed.returncode == 0, completed.stderr
    unit = completed.stdout
    repo = tmp_path / "Hyperliquid Project" / "Hyperliquid-Bot"

    assert f'Environment="CLAUDE_RC_REPO={repo}"' in unit
    assert 'Environment="CLAUDE_RC_NAME=hyperliquid-vps"' in unit
    assert f"User={_non_root_user()}" in unit
    assert "User=root" not in unit
    assert "Restart=always" in unit
    assert f"ExecStart={tmp_path / 'libexec' / 'claude-rc-run'}" in unit
    assert "ExecStop=" in unit and "claude-rc-run --stop" in unit
    assert "WantedBy=multi-user.target" in unit


@pytest.mark.skipif(shutil.which("systemd-analyze") is None, reason="systemd-analyze missing")
def test_printed_unit_passes_systemd_verify(tmp_path: Path) -> None:
    completed = _print_unit(tmp_path)
    assert completed.returncode == 0, completed.stderr
    unit_path = tmp_path / "claude-rc.service"
    unit_path.write_text(completed.stdout, encoding="utf-8")

    verified = subprocess.run(
        ["systemd-analyze", "verify", str(unit_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verified.returncode == 0, verified.stderr


def test_installer_refuses_root_as_the_operator(tmp_path: Path) -> None:
    completed = _print_unit(tmp_path, CLAUDE_RC_USER="root")

    assert completed.returncode == 1
    assert "not as root" in completed.stderr


@pytest.mark.parametrize("bad", ["50%", "a$HOME", 'say"hi', "tick`x`"])
def test_installer_refuses_characters_systemd_would_expand(tmp_path: Path, bad: str) -> None:
    completed = _print_unit(tmp_path, CLAUDE_RC_NAME=bad)

    assert completed.returncode == 1
    assert "unsupported character" in completed.stderr


@pytest.mark.skipif(os.geteuid() == 0, reason="as root the installer would really install")
def test_installer_needs_sudo_to_install(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "CLAUDE_RC_REPO": str(_spaced_repo(tmp_path)),
        "CLAUDE_RC_BIN": str(_fake_claude(tmp_path)),
    }
    env.pop("SUDO_USER", None)
    env.pop("CLAUDE_RC_USER", None)
    completed = subprocess.run(
        ["bash", str(INSTALLER)], check=False, capture_output=True, env=env, text=True
    )

    assert completed.returncode == 1
    assert "Run with sudo" in completed.stderr
