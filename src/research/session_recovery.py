"""Quarantine selected source text recovered from bounded local session transcripts.

Transcript commands are parsed as data. Recovered programs are never imported or
executed, and the manifest never certifies that the unavailable original files or
market datasets have been recovered completely.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from research.btc_flow_io import PrivateOutput, read_bytes
from research.harness.errors import HarnessError

MAX_SESSION_BYTES = 64 << 20
MAX_LINE_BYTES = 2 << 20
MAX_FILE_BYTES = 1 << 20
MAX_OUTPUT_BYTES = 16 << 20
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.(?:py|md)\Z")
_CAT = re.compile(
    r"\bcat\s*(>>?)\s*(['\"]?)([^\s'\"<>;|&]+)\2\s*"
    r"<<(['\"])([A-Za-z_][A-Za-z0-9_]*)\4[^\n]*\n(.*?)\n\5(?:\n|$)",
    re.DOTALL,
)
_PYTHON = re.compile(
    r"\bpython(?:3)?[^\n]*<<(['\"])([A-Za-z_][A-Za-z0-9_]*)\1\n(.*?)\n\2(?:\n|$)",
    re.DOTALL,
)
_SECRET = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[A-Z0-9]{16})\b|"
    r"\b(?:0x)?[0-9a-fA-F]{64}\b|"
    r"\b(?:[A-Za-z_][A-Za-z0-9_-]{0,119})?"
    r"(?:api[_-]?(?:key|secret|token)|private[_-]?key|secret|password|access[_-]?token)"
    r"\b['\"]?\s*[:=]\s*['\"][^'\"\n]+['\"]",
    re.IGNORECASE,
)


class RecoveryError(ValueError):
    """A selected recovery input failed a bounded, safe interpretation rule."""


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise RecoveryError(message)


def _replace_text(text: str, old: str, new: str, count: int = -1) -> str:
    matches = text.count(old) if old else 0
    if count >= 0:
        matches = min(matches, count)
    size = len(text.encode()) + matches * (len(new.encode()) - len(old.encode()))
    _need(bool(old) and size <= MAX_FILE_BYTES, "literal replacement exceeds byte limit")
    return text.replace(old, new, count)


def _literal(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _open_name(node: ast.expr, method: str) -> str | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == method
        and isinstance(node.func.value, ast.Call)
    ):
        return None
    opened = node.func.value
    if not (
        isinstance(opened.func, ast.Name)
        and opened.func.id == "open"
        and opened.args
        and not opened.keywords
    ):
        return None
    path = _literal(opened.args[0])
    if path is None or ".." in Path(path).parts:
        return None
    if method == "read":
        return path if len(opened.args) == 1 and not node.args else None
    return path if len(opened.args) == 2 and _literal(opened.args[1]) == "w" else None


@dataclass
class _Session:
    selected: frozenset[str]
    files: dict[str, str] = field(default_factory=dict)
    records: list[dict[str, object]] = field(default_factory=list)
    unresolved: list[dict[str, object]] = field(default_factory=list)
    absolute_sources: dict[str, str] = field(default_factory=dict)

    def source_name(self, value: object) -> str | None:
        if not isinstance(value, str) or ".." in Path(value).parts:
            return None
        name = Path(value).name
        if name not in self.selected:
            return None
        if Path(value).is_absolute():
            previous = self.absolute_sources.setdefault(name, value)
            _need(previous == value, "selected basename has ambiguous source paths")
        return name

    def write(self, name: str, text: str, line: int, kind: str) -> None:
        _need(len(text.encode()) <= MAX_FILE_BYTES, "recovered file exceeds byte limit")
        _need(_SECRET.search(text) is None, "selected source contains a possible secret")
        self.files[name] = text
        _need(len(self.records) < 10_000, "too many recovery operations")
        self.records.append({"name": name, "line": line, "kind": kind})

    def uncertain(self, line: int, reason: str) -> None:
        record: dict[str, object] = {"line": line, "reason": reason}
        if record not in self.unresolved:
            _need(len(self.unresolved) < 10_000, "too many unresolved operations")
            self.unresolved.append(record)

    def tool(self, kind: str, values: dict[str, object], line: int) -> None:
        if kind == "Bash":
            command = values.get("command")
            if isinstance(command, str):
                self.shell_text(command, line)
            return
        name = self.source_name(values.get("file_path"))
        if name is None or kind not in ("Write", "Edit"):
            return
        if kind == "Write":
            text = values.get("content")
            _need(isinstance(text, str), "selected Write has no text")
            self.write(name, str(text), line, kind)
            return
        old, new = values.get("old_string"), values.get("new_string")
        _need(isinstance(old, str) and isinstance(new, str), "selected Edit has no literal text")
        old, new = str(old), str(new)
        text = self.files.get(name)
        replace_all = values.get("replace_all", False)
        _need(type(replace_all) is bool, "Edit replacement mode must be boolean")
        if text is None or not old or (text.count(old) != 1 and replace_all is not True):
            self.uncertain(line, "Edit lacks a unique recovered predecessor")
            return
        if old not in text:
            self.uncertain(line, "Edit predecessor is absent")
            return
        self.write(
            name, _replace_text(text, str(old), str(new), -1 if replace_all else 1), line, kind
        )

    def shell_text(self, command: str, line: int) -> None:
        spans: list[tuple[int, int]] = []
        operations: list[tuple[int, str, re.Match[str]]] = []
        operations.extend((m.start(), "cat", m) for m in _CAT.finditer(command))
        operations.extend((m.start(), "python", m) for m in _PYTHON.finditer(command))
        for _, kind, match in sorted(operations, key=lambda item: item[0]):
            spans.append(match.span())
            if kind == "python":
                self.python_text(match.group(3), line)
            else:
                name = self.source_name(match.group(3))
                if name:
                    text = match.group(6) + "\n"
                    if match.group(1) == ">>":
                        if name not in self.files:
                            self.uncertain(line, "append lacks a recovered predecessor")
                            continue
                        text = self.files[name] + text
                    self.write(name, text, line, "Bash literal cat")
        # Uninterpreted shell fragments may mutate a selected file. Retain only
        # the line and reason, so command text and credentials never enter output.
        remainder = command
        for start, end in sorted(spans, reverse=True):
            remainder = remainder[:start] + remainder[end:]
        if any(name in remainder for name in self.selected) and re.search(
            r"\b(?:sed|perl|python3?|cp|mv|tee)\b|>>?", remainder
        ):
            self.uncertain(line, "uninterpreted shell fragment references selected source")

    def python_text(self, source: str, line: int) -> None:
        if not any(name in source for name in self.selected):
            return
        try:
            tree = ast.parse(source)
        except (SyntaxError, RecursionError):
            self.uncertain(line, "unparsed Python source references selected source")
            return
        current: str | None = None
        text: str | None = None
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if not isinstance(target, ast.Name) or target.id != "s":
                    text = None
                    self.uncertain(line, "unsupported Python assignment")
                    continue
                source_path = _open_name(node.value, "read")
                if source_path is not None:
                    current = self.source_name(source_path)
                    text = self.files.get(current) if current is not None else None
                    continue
                value = node.value
                if (
                    isinstance(value, ast.Call)
                    and isinstance(value.func, ast.Attribute)
                    and isinstance(value.func.value, ast.Name)
                    and value.func.value.id == "s"
                    and value.func.attr == "replace"
                    and len(value.args) == 2
                    and not value.keywords
                    and current is not None
                    and text is not None
                ):
                    old, new = (_literal(arg) for arg in value.args)
                    if old is not None and new is not None and old and old in text:
                        text = _replace_text(text, old, new)
                        continue
                text = None
                self.uncertain(line, "unsupported or unmatched Python replacement")
            elif (
                isinstance(node, ast.AugAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == "s"
                and isinstance(node.op, ast.Add)
                and _literal(node.value) is not None
                and text is not None
            ):
                addition = str(_literal(node.value))
                _need(
                    len(text.encode()) + len(addition.encode()) <= MAX_FILE_BYTES,
                    "literal append exceeds byte limit",
                )
                text += addition
            elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
                call = node.value
                source_path = _open_name(call, "write")
                name = self.source_name(source_path) if source_path is not None else None
                if name is not None and len(call.args) == 1 and not call.keywords:
                    content = _literal(call.args[0])
                    if isinstance(call.args[0], ast.Name) and call.args[0].id == "s":
                        content = text if name == current else None
                    if content is not None:
                        self.write(str(name), content, line, "Bash literal Python write")
                        continue
                text = None
                self.uncertain(line, "unsupported Python expression")
            else:
                # A branch/loop can change `s`; do not apply a later write based
                # on text whose mutation this deliberately small parser skipped.
                text = None
                self.uncertain(line, "unsupported Python statement")


def _tool_calls(document: object) -> list[tuple[str, dict[str, object]]]:
    if not isinstance(document, dict):
        return []
    message = document.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return []
    out: list[tuple[str, dict[str, object]]] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_use":
            continue
        name, values = block.get("name"), block.get("input")
        if isinstance(name, str) and isinstance(values, dict):
            out.append((name, values))
    return out


def recover_sessions(
    sessions: Sequence[Path], output: Path, names: Sequence[str]
) -> dict[str, object]:
    """Create a private, immutable quarantine bundle from explicit local inputs."""
    _need(0 < len(sessions) <= 8, "select between one and eight sessions")
    _need(0 < len(names) <= 64 and len(set(names)) == len(names), "unique selected names required")
    _need(all(_NAME.fullmatch(name) is not None for name in names), "bare .py/.md names required")
    _need(len(set(sessions)) == len(sessions), "duplicate sessions are not accepted")
    prepared: list[tuple[str, _Session]] = []
    session_records: list[dict[str, object]] = []
    total_bytes = 0
    for index, path in enumerate(sessions):
        raw = read_bytes(path, MAX_SESSION_BYTES)
        state = _Session(frozenset(names))
        for line, raw_line in enumerate(raw.splitlines(), 1):
            _need(len(raw_line) <= MAX_LINE_BYTES, "session line exceeds byte limit")
            if not raw_line.strip():
                continue
            try:
                document = json.loads(raw_line)
            except (ValueError, RecursionError) as exc:
                raise RecoveryError("session contains an invalid JSON line") from exc
            for kind, values in _tool_calls(document):
                state.tool(kind, values, line)
        total_bytes += sum(len(text.encode()) for text in state.files.values())
        _need(total_bytes <= MAX_OUTPUT_BYTES, "recovery exceeds total output byte limit")
        prefix = f"session-{index:03d}"
        prepared.append((prefix, state))
        session_records.append(
            {
                "id": prefix,
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "source_bytes": len(raw),
                "operations": state.records,
                "unresolved_operations": state.unresolved,
                "missing_names": sorted(set(names) - state.files.keys()),
                "files": [
                    {
                        "name": name,
                        "output_name": f"{prefix}--{name}",
                        "sha256": hashlib.sha256(text.encode()).hexdigest(),
                        "bytes": len(text.encode()),
                    }
                    for name, text in sorted(state.files.items())
                ],
            }
        )
    _need(any(state.files for _, state in prepared), "no selected source text was recovered")
    manifest: dict[str, object] = {
        "schema_version": 1,
        "status": "quarantined_log_recovery",
        "sessions": session_records,
        "limitations": [
            "Original scratchpad file identities and final bytes are unavailable.",
            "Transcript text and supported literal edits are partial recovery evidence.",
            "Original market data and financial result artifacts remain unavailable.",
            "Tool call text may include failed or interrupted writes.",
            "Recovered scripts require code review before execution.",
        ],
    }
    manifest_bytes = json.dumps(manifest, sort_keys=True, indent=2, allow_nan=False).encode()
    _need(total_bytes + len(manifest_bytes) <= MAX_OUTPUT_BYTES, "bundle exceeds byte limit")
    with PrivateOutput(output) as destination:
        for prefix, state in prepared:
            for name, text in sorted(state.files.items()):
                destination.write(f"{prefix}--{name}", text.encode())
        destination.json("manifest.json", manifest)
    return manifest


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, action="append", required=True)
    parser.add_argument("--name", action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = recover_sessions(args.session, args.output, args.name)
    except (RecoveryError, HarnessError, OSError, UnicodeError, RecursionError):
        print("Recovery refused. Check bounded inputs, selected names and a fresh output path.")
        return 1
    print(json.dumps({"status": result["status"], "output": str(args.output)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
