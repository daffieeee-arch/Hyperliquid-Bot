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

# CI must have tmux: a skip there would hide the supervisor tests.
needs_tmux = pytest.mark.skipif(
    shutil.which("tmux") is None and not os.environ.get("CI"), reason="tmux is not installed"
)


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
        'echo "fake claude TMUX=${TMUX:-unset} TMUX_PANE=${TMUX_PANE:-unset}"\n'
        'echo "fake claude profile=${CLAUDE_RC_TEST_PROFILE:-unset}"\n'
        "presses=0\n"
        "on_int() {\n"
        "  presses=$((presses + 1))\n"
        '  if [[ "${FAKE_IGNORE_FIRST_INT:-0}" == 1 && "${presses}" -lt 2 ]]; then\n'
        '    echo "Press Ctrl-C again to exit"\n'
        "    return\n"
        "  fi\n"
        f'  touch "{marker}"\n'
        "  exit 130\n"
        "}\n"
        "trap on_int INT\n"
        'for _ in $(seq 1 "${FAKE_SECONDS:-1}"); do sleep 1; done\n'
        "exit 7\n",
    )


def _spaced_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "Hyperliquid Project" / "Hyperliquid-Bot"
    repo.mkdir(parents=True)
    return repo


def _test_home(tmp_path: Path) -> Path:
    """A HOME whose ~/.profile marks that the login shell read it."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    (home / ".profile").write_text("export CLAUDE_RC_TEST_PROFILE=loaded\n", encoding="utf-8")
    return home


def _runner_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    env = {
        **os.environ,
        "CLAUDE_RC_TMUX_SOCKET": f"pytest-claude-rc-{uuid.uuid4().hex[:12]}",
        "CLAUDE_RC_TMUX_SESSION": SESSION,
        "CLAUDE_RC_POLL_SECONDS": "0.2",
        "CLAUDE_RC_FIRST_STOP_WAIT_SECONDS": "2",
        "CLAUDE_RC_STOP_WAIT_SECONDS": "5",
        "CLAUDE_RC_MISSING_PAUSE_SECONDS": "0",
        "CLAUDE_RC_REPO": str(_spaced_repo(tmp_path)),
        "CLAUDE_RC_BIN": str(_fake_claude(tmp_path)),
        "CLAUDE_RC_NAME": "hyperliquid vps",
        "CLAUDE_RC_STATE_DIR": str(tmp_path / "state"),
        # The login shell reads this HOME's profile, not the real one.
        "HOME": str(_test_home(tmp_path)),
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

    # Exit 0: systemd restarts it anyway, and a stop must not mark it failed.
    assert completed.returncode == 0, completed.stderr
    assert "remote-control started in" in completed.stdout
    assert "exited with status 7" in completed.stderr
    # The name stays one argument and the session starts in the spaced folder.
    assert (
        "fake claude arg <remote-control>\n"
        "fake claude arg <--name>\n"
        "fake claude arg <hyperliquid vps>\n"
    ) in completed.stderr
    assert f"fake claude cwd: {env['CLAUDE_RC_REPO']}" in completed.stderr
    # Without TMUX, tmux sessions that Claude sessions start go to the default
    # server, where the capture scripts look for them.
    assert "fake claude TMUX=unset TMUX_PANE=unset" in completed.stderr
    # claude starts through a login shell, so it sees the operator's ~/.profile.
    assert "fake claude profile=loaded" in completed.stderr
    assert not _session_alive(env)


@needs_tmux
def test_stop_leaves_a_session_that_only_starts_with_the_name(tmp_path: Path) -> None:
    env = _runner_env(tmp_path)
    socket = env["CLAUDE_RC_TMUX_SOCKET"]
    decoy = f"{SESSION}-decoy"
    subprocess.run(
        ["tmux", "-L", socket, "new-session", "-d", "-s", decoy, "--", "sleep", "30"],
        check=True,
        env=env,
    )
    try:
        stopped = subprocess.run(
            ["bash", str(RUNNER), "--stop"],
            check=False,
            capture_output=True,
            env=env,
            text=True,
            timeout=30,
        )
        alive = subprocess.run(
            ["tmux", "-L", socket, "has-session", "-t", f"={decoy}"],
            check=False,
            capture_output=True,
        )
    finally:
        subprocess.run(
            ["tmux", "-L", socket, "kill-session", "-t", f"={decoy}"],
            check=False,
            capture_output=True,
        )

    assert stopped.returncode == 0, stopped.stderr
    assert alive.returncode == 0


@needs_tmux
@pytest.mark.parametrize("ignore_first", ["0", "1"])
def test_stop_sends_ctrl_c_and_the_runner_exits(tmp_path: Path, ignore_first: str) -> None:
    # "1": claude asks for a second Ctrl+C before it exits.
    env = _runner_env(tmp_path, FAKE_SECONDS="60", FAKE_IGNORE_FIRST_INT=ignore_first)
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
        assert runner.wait(timeout=30) == 0
        runner_err = runner.stderr.read() if runner.stderr else ""
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait()
        _end_test_session(env)

    assert (tmp_path / "interrupted").exists()
    # A planned stop is not reported as a crash with a screen dump.
    assert "last screen:" not in runner_err
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
    # Processes the sessions start (capture tmux sessions, downloads) must
    # survive a restart of the service.
    assert "KillMode=process" in unit
    assert "KillMode=control-group" not in unit
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


@needs_tmux
def test_status_via_symlink_reports_one_line_and_the_screen(tmp_path: Path) -> None:
    fake_bin = tmp_path / "fakebin"
    fake_bin.mkdir()
    _executable(
        fake_bin / "systemctl",
        "#!/usr/bin/env bash\n"
        'case "$1" in\n'
        "  is-active) echo inactive; exit 3 ;;\n"
        "  is-enabled) echo disabled; exit 1 ;;\n"
        "esac\n"
        "exit 1\n",
    )
    # A user outside adm sees no lines and exit 0, like the real journalctl.
    _executable(fake_bin / "journalctl", "#!/usr/bin/env bash\nexit 0\n")
    link = tmp_path / "claude-rc-status"
    link.symlink_to(REPO_ROOT / "scripts" / "claude_rc_status.sh")
    env = _runner_env(tmp_path)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    socket = env["CLAUDE_RC_TMUX_SOCKET"]
    subprocess.run(
        [
            "tmux",
            "-L",
            socket,
            "new-session",
            "-d",
            "-s",
            SESSION,
            "--",
            "bash",
            "-c",
            "echo remote-control screen line; sleep 30",
        ],
        check=True,
        env=env,
    )
    try:
        deadline = time.monotonic() + 5
        screen = ""
        while "screen line" not in screen and time.monotonic() < deadline:
            time.sleep(0.1)
            screen = subprocess.run(
                ["tmux", "-L", socket, "capture-pane", "-p", "-t", SESSION],
                capture_output=True,
                text=True,
                check=False,
            ).stdout
        completed = subprocess.run(
            ["bash", str(link)], check=False, capture_output=True, env=env, text=True, timeout=30
        )
    finally:
        _end_test_session(env)

    assert completed.returncode == 0, completed.stderr
    lines = completed.stdout.splitlines()
    assert lines[0] == "service: inactive (disabled)"
    assert "sudo journalctl -u claude-rc.service" in completed.stdout
    assert "remote-control screen line" in completed.stdout
    assert completed.stdout.endswith("remote-control screen line\n")


@needs_tmux
def test_a_repeated_identical_exit_screen_is_not_printed_again(tmp_path: Path) -> None:
    env = _runner_env(tmp_path)
    outputs = []
    try:
        for _ in range(2):
            completed = subprocess.run(
                ["bash", str(RUNNER)],
                check=False,
                capture_output=True,
                env=env,
                text=True,
                timeout=30,
            )
            assert completed.returncode == 0, completed.stderr
            outputs.append(completed.stderr)
    finally:
        _end_test_session(env)

    assert "last screen:" in outputs[0]
    assert "fake claude arg <remote-control>" in outputs[0]
    assert "same screen as the last exit" in outputs[1]
    assert "fake claude arg" not in outputs[1]


def _lib_call(function: str, *args: str) -> int:
    lib = REPO_ROOT / "scripts" / "claude_rc_lib.sh"
    return subprocess.run(
        ["bash", "-c", 'source "$1"; shift; "$@"', "bash", str(lib), function, *args],
        check=False,
        capture_output=True,
    ).returncode


def test_server_detection_needs_claude_and_the_remote_control_argument(tmp_path: Path) -> None:
    claude = _fake_claude(tmp_path)
    server = subprocess.Popen(
        [str(claude), "remote-control"],
        env={**os.environ, "FAKE_SECONDS": "30"},
        stdout=subprocess.DEVNULL,
    )
    # Like `rg remote-control ~/.npm/.../claude-code`: the argument and a
    # claude path, but another program. "; true" keeps bash from exec-ing
    # sleep, so these command lines stay as written while the test reads them.
    lookalike = subprocess.Popen(
        ["bash", "-c", 'exec -a rg bash -c "sleep 30; true" x remote-control /x/claude-code']
    )
    plain = subprocess.Popen(["bash", "-c", "sleep 30; true", "remote-control"])
    try:
        time.sleep(0.2)
        assert all(p.poll() is None for p in (server, lookalike, plain))
        assert _lib_call("claude_rc_is_server_pid", str(server.pid)) == 0
        assert _lib_call("claude_rc_is_server_pid", str(lookalike.pid)) == 1
        assert _lib_call("claude_rc_is_server_pid", str(plain.pid)) == 1
    finally:
        for process in (server, lookalike, plain):
            process.kill()
            process.wait()


@needs_tmux
def test_an_exit_marked_as_planned_is_not_reported_as_a_crash(tmp_path: Path) -> None:
    # --stop marks the session before its Ctrl+C; here the mark is set by hand
    # and claude exits on its own, so the main loop always sees the dead pane.
    env = _runner_env(tmp_path, FAKE_SECONDS="2")
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
        subprocess.run(
            [
                "tmux",
                "-L",
                env["CLAUDE_RC_TMUX_SOCKET"],
                "set-option",
                "-t",
                f"={SESSION}:",
                "@claude_rc_stopping",
                "1",
            ],
            check=True,
        )
        assert runner.wait(timeout=30) == 0
        runner_err = runner.stderr.read() if runner.stderr else ""
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait()
        _end_test_session(env)

    assert "stopped on request" in runner_err
    assert "last screen:" not in runner_err
