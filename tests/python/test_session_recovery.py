"""Synthetic transcript recovery tests with no exchange or market-data access."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

import pytest

from research.harness.errors import HarnessError
from research.session_recovery import RecoveryError, main, recover_sessions


def _call(kind: str, **values: object) -> dict[str, object]:
    return {"message": {"content": [{"type": "tool_use", "name": kind, "input": values}]}}


def _session(tmp_path: Path, *documents: dict[str, object], name: str = "source.jsonl") -> Path:
    source = tmp_path / name
    source.write_text("\n".join(json.dumps(row) for row in documents) + "\n")
    return source


def _manifest(output: Path) -> dict[str, object]:
    value = json.loads((output / "manifest.json").read_text())
    assert isinstance(value, dict)
    return value


def test_cli_recovers_write_edit_and_provenance_without_full_transcript(tmp_path: Path) -> None:
    source = _session(
        tmp_path,
        {"message": {"content": "personal conversation excluded from output"}},
        _call("Write", file_path="/old/scratch/a.py", content="value = 1\n"),
        _call("Edit", file_path="/old/scratch/a.py", old_string="1", new_string="2"),
    )
    output = tmp_path / "recovered"
    assert main(["--session", str(source), "--output", str(output), "--name", "a.py"]) == 0
    assert (output / "session-000--a.py").read_text() == "value = 2\n"
    manifest = _manifest(output)
    assert manifest["status"] == "quarantined_log_recovery"
    sessions = manifest["sessions"]
    assert isinstance(sessions, list)
    assert sessions[0]["source_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert sessions[0]["files"][0]["sha256"] == hashlib.sha256(b"value = 2\n").hexdigest()
    assert sessions[0]["operations"] == [
        {"kind": "Write", "line": 2, "name": "a.py"},
        {"kind": "Edit", "line": 3, "name": "a.py"},
    ]
    assert stat.S_IMODE(output.stat().st_mode) == 0o700
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in output.iterdir())
    assert "personal conversation" not in (output / "manifest.json").read_text()
    assert not (output / source.name).exists()


def test_literal_shell_and_python_patches_are_interpreted_as_data(tmp_path: Path) -> None:
    sentinel = tmp_path / "never-created"
    command = (
        "cat > a.py <<'PY'\nvalue = 1\nPY\n"
        f"touch {sentinel}\n"
        "python3 - <<'PY'\n"
        's=open("a.py").read()\n'
        's=s.replace("1", "2")\n'
        's+="extra = 3\\n"\n'
        'open("a.py","w").write(s)\n'
        "PY\n"
        "cat > plan.md <<'DOC'\nfirst\nDOC\n"
        "cat >> plan.md <<'DOC'\nsecond\nDOC\n"
    )
    source = _session(tmp_path, _call("Bash", command=command))
    output = tmp_path / "recovered"
    recover_sessions([source], output, ["a.py", "plan.md"])
    assert (output / "session-000--a.py").read_text() == "value = 2\nextra = 3\n"
    assert (output / "session-000--plan.md").read_text() == "first\nsecond\n"
    assert not sentinel.exists()


def test_unknown_python_mutation_is_quarantined_and_never_executed(tmp_path: Path) -> None:
    sentinel = tmp_path / "never-created"
    command = (
        "python3 - <<'PY'\n"
        's=open("a.py").read()\n'
        f"__import__('pathlib').Path({str(sentinel)!r}).write_text('danger')\n"
        "s = transform(s)\n"
        'open("a.py","w").write(s)\nPY\n'
    )
    source = _session(
        tmp_path,
        _call("Write", file_path="a.py", content="original\n"),
        _call("Bash", command=command),
    )
    output = tmp_path / "recovered"
    result = recover_sessions([source], output, ["a.py"])
    assert (output / "session-000--a.py").read_text() == "original\n"
    assert not sentinel.exists()
    sessions = result["sessions"]
    assert isinstance(sessions, list) and sessions[0]["unresolved_operations"]


def test_edit_without_unique_predecessor_preserves_prior_text(tmp_path: Path) -> None:
    source = _session(
        tmp_path,
        _call("Write", file_path="a.py", content="one one"),
        _call("Edit", file_path="a.py", old_string="one", new_string="two"),
        _call("Edit", file_path="a.py", old_string="missing", new_string="two"),
    )
    output = tmp_path / "recovered"
    recover_sessions([source], output, ["a.py"])
    assert (output / "session-000--a.py").read_text() == "one one"
    assert "unique recovered predecessor" in (output / "manifest.json").read_text()


def test_replace_all_and_literal_python_write(tmp_path: Path) -> None:
    source = _session(
        tmp_path,
        _call("Write", file_path="a.py", content="one one"),
        _call("Edit", file_path="a.py", old_string="one", new_string="two", replace_all=True),
        _call("Bash", command="python3 - <<'PY'\nopen('plan.md','w').write('plan')\nPY\n"),
    )
    output = tmp_path / "recovered"
    recover_sessions([source], output, ["a.py", "plan.md"])
    assert (output / "session-000--a.py").read_text() == "two two"
    assert (output / "session-000--plan.md").read_text() == "plan"


@pytest.mark.parametrize("name", ["../a.py", "/tmp/a.py", "a.json", "a.py/../../b.py", "a/ok.py"])
def test_selected_names_are_bare_source_leaves(tmp_path: Path, name: str) -> None:
    source = _session(tmp_path, _call("Write", file_path="a.py", content="pass\n"))
    output = tmp_path / "recovered"
    with pytest.raises(RecoveryError, match="bare"):
        recover_sessions([source], output, [name])
    assert not output.exists()


def test_existing_output_and_symlink_inputs_or_parents_are_rejected(tmp_path: Path) -> None:
    source = _session(tmp_path, _call("Write", file_path="a.py", content="pass\n"))
    output = tmp_path / "recovered"
    output.mkdir()
    (output / "keep").write_text("keep")
    with pytest.raises(FileExistsError):
        recover_sessions([source], output, ["a.py"])
    assert (output / "keep").read_text() == "keep"
    link = tmp_path / "linked.jsonl"
    link.symlink_to(source)
    with pytest.raises(OSError):
        recover_sessions([link], tmp_path / "other", ["a.py"])
    parent = tmp_path / "linked-parent"
    parent.symlink_to(output, target_is_directory=True)
    with pytest.raises(OSError):
        recover_sessions([source], parent / "nested", ["a.py"])
    assert not (output / "nested").exists()


def test_sessions_with_same_source_basename_have_separate_outputs(tmp_path: Path) -> None:
    first = _session(tmp_path, _call("Write", file_path="a.py", content="first"))
    second = _session(
        tmp_path, _call("Write", file_path="a.py", content="second"), name="two.jsonl"
    )
    output = tmp_path / "recovered"
    recover_sessions([first, second], output, ["a.py"])
    assert (output / "session-000--a.py").read_text() == "first"
    assert (output / "session-001--a.py").read_text() == "second"


def test_ambiguous_absolute_source_basename_is_refused(tmp_path: Path) -> None:
    source = _session(
        tmp_path,
        _call("Write", file_path="/old/a.py", content="one"),
        _call("Write", file_path="/other/a.py", content="two"),
    )
    with pytest.raises(RecoveryError, match="ambiguous"):
        recover_sessions([source], tmp_path / "recovered", ["a.py"])


def test_possible_secret_is_refused_before_any_output_is_created(tmp_path: Path) -> None:
    source = _session(
        tmp_path, _call("Write", file_path="a.py", content="API_KEY = 'synthetic-test-value'\n")
    )
    output = tmp_path / "recovered"
    with pytest.raises(RecoveryError, match="possible secret"):
        recover_sessions([source], output, ["a.py"])
    assert not output.exists()


def test_literal_patch_cannot_expand_beyond_file_bound(tmp_path: Path) -> None:
    command = (
        "python3 - <<'PY'\n"
        's=open("a.py").read()\n'
        f"s=s.replace('a', {'b' * 2000!r})\n"
        'open("a.py","w").write(s)\nPY\n'
    )
    source = _session(
        tmp_path,
        _call("Write", file_path="a.py", content="a" * 1000),
        _call("Bash", command=command),
    )
    output = tmp_path / "recovered"
    with pytest.raises(RecoveryError, match="byte limit"):
        recover_sessions([source], output, ["a.py"])
    assert not output.exists()


def test_cli_failure_never_prints_transcript_payload(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = _session(
        tmp_path, _call("Write", file_path="a.py", content="password='synthetic-sensitive-text'")
    )
    result = main(["--session", str(source), "--name", "a.py", "--output", str(tmp_path / "out")])
    assert result == 1
    assert "synthetic-sensitive-text" not in capsys.readouterr().out


def test_oversized_or_nonregular_source_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _session(tmp_path, _call("Write", file_path="a.py", content="pass\n"))
    monkeypatch.setattr("research.session_recovery.MAX_SESSION_BYTES", 1)
    with pytest.raises(HarnessError):
        recover_sessions([source], tmp_path / "recovered", ["a.py"])


@pytest.mark.parametrize(
    "identifier",
    [
        "HYPERLIQUID_API_KEY",
        "OPENAI_API_KEY",
        "BINANCE_SECRET",
        "HL_PRIVATE_KEY",
        "krakenApiKey",
        "SERVICE_ACCESS_TOKEN",
    ],
)
@pytest.mark.parametrize("source_name", ["a.py", "plan.md"])
def test_prefixed_secret_assignments_and_json_keys_are_refused(
    tmp_path: Path, identifier: str, source_name: str
) -> None:
    text = (
        f"{identifier} = 'synthetic-review-value'\n"
        if source_name.endswith(".py")
        else json.dumps({identifier: "synthetic-review-value"})
    )
    source = _session(tmp_path, _call("Write", file_path=source_name, content=text))
    output = tmp_path / "recovered"
    with pytest.raises(RecoveryError, match="possible secret"):
        recover_sessions([source], output, [source_name])
    assert not output.exists()


@pytest.mark.parametrize("operation", ["read", "write"])
def test_literal_python_paths_preserve_absolute_origin_binding(
    tmp_path: Path, operation: str
) -> None:
    statement = (
        's = open("/other/a.py").read()\nopen("/old/a.py", "w").write(s)'
        if operation == "read"
        else 'open("/other/a.py", "w").write("different_source\\n")'
    )
    source = _session(
        tmp_path,
        _call("Write", file_path="/old/a.py", content="original\n"),
        _call("Bash", command=f"python3 - <<'PY'\n{statement}\nPY\n"),
    )
    output = tmp_path / "recovered"
    with pytest.raises(RecoveryError, match="ambiguous"):
        recover_sessions([source], output, ["a.py"])
    assert not output.exists()
