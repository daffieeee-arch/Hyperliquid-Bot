"""Regression: pytest must not be able to stop live TerraPC capture sessions."""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from hyperliquid_bot.operator_stop_isolation import PROTECTED_LIVE_SESSIONS

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
STOP_SCRIPTS = {
    "hl-capture": "data1a_stop.sh",
    "bn-capture": "data1f_stop.sh",
    "bv-capture": "data1e_stop.sh",
    "kr-capture": "data1b_stop.sh",
}


def _fake_tmux(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    tmux = fake_bin / "tmux"
    log = tmp_path / "tmux-invocations.log"
    tmux.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$*" >> "{log}"\n'
        'if [[ "$1" == "has-session" ]]; then exit 0; fi\n'
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
