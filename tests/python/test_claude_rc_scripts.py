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


@pytest.fixture(autouse=True)
def _no_tmux_tmpdir(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scripts ignore TMUX_TMPDIR; so must every tmux call these tests make."""
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)


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
        "first_press_ms=0\n"
        "on_int() {\n"
        '  if [[ "${FAKE_IGNORE_FIRST_INT:-0}" == 1 ]]; then\n'
        "    now_ms=$(($(date +%s%N) / 1000000))\n"
        "    # Like claude: a second press counts only within a short window.\n"
        "    window_ms=${FAKE_CONFIRM_MS:-800}\n"
        "    if ((first_press_ms == 0 || now_ms - first_press_ms > window_ms)); then\n"
        "      first_press_ms=${now_ms}\n"
        '      echo "Press Ctrl-C again to exit"\n'
        "      return\n"
        "    fi\n"
        "  fi\n"
        f'  touch "{marker}"\n'
        "  exit 130\n"
        "}\n"
        '[[ "${FAKE_NO_TRAP:-0}" == 1 ]] || trap on_int INT\n'
        'for i in $(seq 1 "${FAKE_LINES:-0}"); do echo "fake line ${i}"; done\n'
        '[[ "${FAKE_KILL_SELF:-0}" == 1 ]] && kill -9 $$\n'
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


def _tmux(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["tmux", "-f", "/dev/null", "-L", env["CLAUDE_RC_TMUX_SOCKET"], *args],
        check=False,
        capture_output=True,
        env=env,
        text=True,
    )


def _wait_for_stored_pane(env: dict[str, str]) -> str:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        pane = _tmux(env, "show-options", "-qv", "-t", f"={SESSION}:", "@claude_rc_pane").stdout
        if pane.strip():
            return pane.strip()
        time.sleep(0.1)
    raise AssertionError("the supervisor never stored claude's pane id")


# timing: the "1" case relies on an 800 ms confirmation window.
@pytest.mark.timing
@needs_tmux
@pytest.mark.parametrize("extra_window", [False, True])
@pytest.mark.parametrize("ignore_first", ["0", "1"])
def test_stop_sends_ctrl_c_and_the_runner_exits(
    tmp_path: Path, ignore_first: str, extra_window: bool
) -> None:
    # "1": claude asks for a second Ctrl+C before it exits. extra_window: the
    # operator opened another window, which becomes the active pane.
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
        _wait_for_stored_pane(env)
        if extra_window:
            assert (
                _tmux(env, "new-window", "-t", f"={SESSION}:", "--", "sleep", "60").returncode == 0
            )

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


def _run_once(env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["bash", str(RUNNER)], check=False, capture_output=True, env=env, text=True, timeout=30
        )
    finally:
        _end_test_session(env)


@needs_tmux
def test_the_exit_screen_in_the_journal_is_at_most_40_lines(tmp_path: Path) -> None:
    completed = _run_once(_runner_env(tmp_path, FAKE_LINES="120"))

    assert completed.returncode == 0, completed.stderr
    dump = completed.stderr.split("last screen:\n", 1)[1].splitlines()
    assert 0 < len(dump) <= 40
    assert dump[-1] == "fake line 120"


@needs_tmux
def test_an_identical_screen_is_printed_again_after_a_long_run(tmp_path: Path) -> None:
    # With the reset at 0 s every run counts as long, so nothing is suppressed.
    env = _runner_env(tmp_path, CLAUDE_RC_SCREEN_RESET_SECONDS="0")
    first = _run_once(env)
    second = _run_once(env)

    assert "last screen:" in first.stderr
    assert "last screen:" in second.stderr


@needs_tmux
def test_the_private_tmux_server_is_recognised_by_its_socket(tmp_path: Path) -> None:
    env = _runner_env(tmp_path)
    socket = env["CLAUDE_RC_TMUX_SOCKET"]
    subprocess.run(
        [
            "tmux",
            "-L",
            socket,
            "start-server",
            ";",
            "new-session",
            "-d",
            "-s",
            SESSION,
            "--",
            "sleep",
            "30",
        ],
        check=True,
        env=env,
    )
    try:
        server_pid = subprocess.run(
            ["tmux", "-L", socket, "display-message", "-p", "-t", f"={SESSION}:", "#{pid}"],
            check=True,
            capture_output=True,
            env=env,
            text=True,
        ).stdout.strip()
        lib = REPO_ROOT / "scripts" / "claude_rc_lib.sh"

        def is_private(socket_name: str) -> int:
            return subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; claude_rc_is_private_tmux_server_pid "$2"',
                    "bash",
                    str(lib),
                    server_pid,
                ],
                check=False,
                capture_output=True,
                env={**env, "CLAUDE_RC_TMUX_SOCKET": socket_name},
            ).returncode

        assert is_private(socket) == 0
        assert is_private("some-other-socket") == 1
    finally:
        _end_test_session(env)


def _fake_proc(root: Path, pid: int, *, argv: list[str], comm: str, ppid: int, cgroup: str) -> None:
    entry = root / str(pid)
    entry.mkdir(parents=True)
    (entry / "cmdline").write_bytes("\0".join(argv).encode() + b"\0")
    (entry / "comm").write_text(comm + "\n", encoding="utf-8")
    (entry / "status").write_text(f"Name:\t{comm}\nPPid:\t{ppid}\n", encoding="utf-8")
    (entry / "cgroup").write_text(f"0::{cgroup}\n", encoding="utf-8")


def _fake_lib_call(proc: Path, function: str, *args: str) -> int:
    lib = REPO_ROOT / "scripts" / "claude_rc_lib.sh"
    return subprocess.run(
        ["bash", "-c", 'source "$1"; shift; "$@"', "bash", str(lib), function, *args],
        check=False,
        capture_output=True,
        env={**os.environ, "CLAUDE_RC_PROC_ROOT": str(proc), "CLAUDE_RC_TMUX_SOCKET": "claude-rc"},
    ).returncode


def test_inside_service_follows_the_first_tmux_server(tmp_path: Path) -> None:
    proc = tmp_path / "proc"
    service = "/system.slice/claude-rc.service"
    # A served session: shell -> claude -> private tmux server.
    _fake_proc(
        proc,
        80,
        argv=["tmux", "-L", "claude-rc", "start-server"],
        comm="tmux: server",
        ppid=1,
        cgroup=service,
    )
    _fake_proc(proc, 90, argv=["claude"], comm="claude", ppid=80, cgroup=service)
    _fake_proc(proc, 100, argv=["bash"], comm="bash", ppid=90, cgroup=service)
    # An operator window in a default tmux server that a session started.
    _fake_proc(
        proc,
        210,
        argv=["tmux", "new", "-s", "hl-capture"],
        comm="tmux: server",
        ppid=1,
        cgroup=service,
    )
    _fake_proc(proc, 200, argv=["bash"], comm="bash", ppid=210, cgroup=service)
    # A detached session process, and an ordinary SSH shell.
    _fake_proc(proc, 300, argv=["bash"], comm="bash", ppid=1, cgroup=service)
    _fake_proc(proc, 400, argv=["bash"], comm="bash", ppid=1, cgroup="/user.slice/session-3.scope")

    assert _fake_lib_call(proc, "claude_rc_inside_service", "100") == 0
    assert _fake_lib_call(proc, "claude_rc_inside_service", "200") == 1
    assert _fake_lib_call(proc, "claude_rc_inside_service", "300") == 0
    assert _fake_lib_call(proc, "claude_rc_inside_service", "400") == 1


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["claude", "remote-control"], 0),
        (["node", "--max-old-space-size=4096", "/x/claude-code/cli.js", "remote-control"], 0),
        (["node", "/x/claude-code/cli.js", "--resume"], 1),
        (["rg", "remote-control", "/x/claude-code"], 1),
        (["node", "--inspect", "/x/other.js", "remote-control"], 1),
    ],
)
def test_server_detection_reads_the_program_and_its_arguments(
    tmp_path: Path, argv: list[str], expected: int
) -> None:
    proc = tmp_path / "proc"
    _fake_proc(proc, 500, argv=argv, comm=argv[0], ppid=1, cgroup="/user.slice")

    assert _fake_lib_call(proc, "claude_rc_is_server_pid", "500") == expected


@needs_tmux
def test_an_operator_window_does_not_hide_claude_exiting(tmp_path: Path) -> None:
    env = _runner_env(tmp_path, FAKE_SECONDS="2")
    runner = subprocess.Popen(
        ["bash", str(RUNNER)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        _wait_for_stored_pane(env)
        assert _tmux(env, "new-window", "-t", f"={SESSION}:", "--", "sleep", "60").returncode == 0
        assert runner.wait(timeout=30) == 0
        runner_err = runner.stderr.read() if runner.stderr else ""
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait()
        _end_test_session(env)

    assert "exited with status 7" in runner_err


@needs_tmux
def test_the_operators_tmux_conf_is_ignored(tmp_path: Path) -> None:
    env = _runner_env(tmp_path)
    # With this setting a detached session would be destroyed at once.
    (Path(env["HOME"]) / ".tmux.conf").write_text(
        "set -g destroy-unattached on\n", encoding="utf-8"
    )
    completed = _run_once(env)

    assert completed.returncode == 0, completed.stderr
    assert "exited with status 7" in completed.stderr


def test_the_live_socket_is_refused_under_test(tmp_path: Path) -> None:
    env = _runner_env(tmp_path)
    del env["CLAUDE_RC_TMUX_SOCKET"]
    env["PYTEST_CURRENT_TEST"] = "test_claude_rc_scripts"
    completed = subprocess.run(
        ["bash", str(RUNNER), "--stop"], check=False, capture_output=True, env=env, text=True
    )

    assert completed.returncode == 2
    assert "status=TEST_ISOLATION_NOOP" in completed.stderr


@needs_tmux
def test_a_signal_death_is_named_in_the_journal(tmp_path: Path) -> None:
    completed = _run_once(_runner_env(tmp_path, FAKE_KILL_SELF="1"))

    assert completed.returncode == 0, completed.stderr
    assert "exited with status 137 (signal 9 or exit code 137)" in completed.stderr


@needs_tmux
def test_a_ctrl_c_in_the_screen_is_reported_with_its_signal(tmp_path: Path) -> None:
    # The operator presses Ctrl+C in the attached screen (not a planned stop),
    # and claude dies of SIGINT instead of handling it.
    env = _runner_env(tmp_path, FAKE_SECONDS="60", FAKE_NO_TRAP="1")
    runner = subprocess.Popen(
        ["bash", str(RUNNER)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        pane = _wait_for_stored_pane(env)
        time.sleep(0.3)
        assert _tmux(env, "send-keys", "-t", pane, "C-c").returncode == 0
        assert runner.wait(timeout=30) == 0
        runner_err = runner.stderr.read() if runner.stderr else ""
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait()
        _end_test_session(env)

    assert "exited with status 130 (signal 2 or exit code 130)" in runner_err


@needs_tmux
def test_closing_claudes_pane_beside_another_window_counts_as_an_exit(tmp_path: Path) -> None:
    env = _runner_env(tmp_path, FAKE_SECONDS="60")
    runner = subprocess.Popen(
        ["bash", str(RUNNER)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        pane = _wait_for_stored_pane(env)
        assert _tmux(env, "new-window", "-t", f"={SESSION}:", "--", "sleep", "60").returncode == 0
        assert _tmux(env, "kill-pane", "-t", pane).returncode == 0
        assert runner.wait(timeout=30) == 0
        runner_err = runner.stderr.read() if runner.stderr else ""
    finally:
        if runner.poll() is None:
            runner.kill()
            runner.wait()
        _end_test_session(env)

    assert "remote-control exited with status" in runner_err


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"PYTEST_CURRENT_TEST": "x"},
        {"CI": "true"},
        {"CI": "1"},
        {"CI": "yes"},
        {"CI": "false"},
        {"HYPERLIQUID_BOT_TEST_ISOLATION": "1"},
        {"HYPERLIQUID_BOT_TEST_ISOLATION": "0"},
    ],
)
def test_test_isolation_rules_match_the_capture_helpers(extra: dict[str, str]) -> None:
    # claude_rc_lib.sh is installed on its own, so it repeats the rules of
    # data1_in_test_or_ci_env; this keeps the two copies equal.
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTEST_CURRENT_TEST", "CI", "HYPERLIQUID_BOT_TEST_ISOLATION")
    }
    env.update(extra)
    scripts = REPO_ROOT / "scripts"

    def verdict(source: str, function: str) -> int:
        return subprocess.run(
            ["bash", "-c", 'source "$1"; "$2"', "bash", str(scripts / source), function],
            check=False,
            capture_output=True,
            env=env,
        ).returncode

    assert verdict("claude_rc_lib.sh", "claude_rc_in_test_or_ci") == verdict(
        "data1_operator_isolation.sh", "data1_in_test_or_ci_env"
    )
