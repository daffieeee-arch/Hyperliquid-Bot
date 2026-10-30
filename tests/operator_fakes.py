"""Offline commands for operator-helper subprocess tests; never invoke host tmux/uv."""

from pathlib import Path


def fake_operator_path(root: Path, original_path: str) -> str:
    binary_dir = root / "operator-fake-bin"
    binary_dir.mkdir(exist_ok=True)
    scripts = {
        "tmux": '#!/bin/bash\nif [[ "$1" == "has-session" ]]; then exit 1; fi\nexit 0\n',
        "uv": (
            '#!/bin/bash\necho "Refuse: operator tests must not execute collectors" >&2\nexit 97\n'
        ),
    }
    for name, script in scripts.items():
        path = binary_dir / name
        path.write_text(script, encoding="utf-8")
        path.chmod(0o700)
    return f"{binary_dir}:{original_path}"
