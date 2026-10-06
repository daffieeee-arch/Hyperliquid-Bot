"""Strict YAML subset for hypothesis specs.

The subset is block mappings, block lists, and scalars. It rejects tabs, flow
collections, anchors, and duplicate keys so a spec has one canonical reading.
"""

from __future__ import annotations

from dataclasses import dataclass

from research.harness.errors import SpecError

JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


@dataclass(frozen=True, slots=True)
class _Line:
    number: int
    indent: int
    content: str


def loads(text: str) -> JsonValue:
    """Parse a YAML-subset document. The root must be a mapping or a list."""

    if "\t" in text:
        raise SpecError("YAML specs must not contain tabs.")
    lines = _meaningful_lines(text)
    if not lines:
        raise SpecError("YAML spec is empty.")
    if lines[0].indent != 0:
        raise SpecError("YAML root must start at column 0.")
    value, index = _parse_block(lines, 0, 0)
    if index != len(lines):
        raise SpecError(f"YAML has trailing content at line {lines[index].number}.")
    return value


def _meaningful_lines(text: str) -> list[_Line]:
    parsed: list[_Line] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        if raw.strip() == "":
            continue
        stripped = _strip_comment(raw).rstrip()
        if stripped.strip() == "":
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        content = stripped[indent:]
        parsed.append(_Line(number=number, indent=indent, content=content))
    return parsed


def _strip_comment(line: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(line):
        if in_double:
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_double = False
            continue
        if in_single:
            if char == "'":
                # YAML single-quote escape is a doubled quote, not a closer.
                if index + 1 < len(line) and line[index + 1] == "'":
                    continue
                in_single = False
            continue
        if char == '"':
            in_double = True
            continue
        if char == "'":
            in_single = True
            continue
        if char == "#":
            return line[:index]
    return line


def _parse_block(lines: list[_Line], index: int, indent: int) -> tuple[JsonValue, int]:
    if index >= len(lines):
        raise SpecError("YAML ended before a nested block.")
    current = lines[index]
    if current.indent != indent:
        raise SpecError(
            f"YAML line {current.number} is indented {current.indent}; expected {indent}."
        )
    if current.content.startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_map(lines, index, indent)


def _parse_map(lines: list[_Line], index: int, indent: int) -> tuple[dict[str, JsonValue], int]:
    mapping: dict[str, JsonValue] = {}
    while index < len(lines) and lines[index].indent == indent:
        current = lines[index]
        if current.content.startswith("- "):
            raise SpecError(f"YAML line {current.number} mixes a list into a mapping.")
        key, inline = _split_key(current.content, current.number)
        if key in mapping:
            raise SpecError(f"YAML duplicate key {key!r} at line {current.number}.")
        if inline is None:
            index += 1
            if index >= len(lines) or lines[index].indent <= indent:
                raise SpecError(f"YAML key {key!r} at line {current.number} has no nested value.")
            child_indent = lines[index].indent
            mapping[key], index = _parse_block(lines, index, child_indent)
            continue
        mapping[key] = _parse_scalar(inline, current.number)
        index += 1
    if not mapping:
        raise SpecError("YAML mapping is empty.")
    return mapping, index


def _parse_list(lines: list[_Line], index: int, indent: int) -> tuple[list[JsonValue], int]:
    items: list[JsonValue] = []
    while index < len(lines) and lines[index].indent == indent:
        current = lines[index]
        if not current.content.startswith("- "):
            break
        rest = current.content[2:].strip()
        if rest == "":
            raise SpecError(f"YAML line {current.number} has an empty list item.")
        if _has_key_separator(rest):
            key, inline = _split_key(rest, current.number)
            item: dict[str, JsonValue] = {}
            if inline is None:
                index += 1
                if index >= len(lines) or lines[index].indent <= indent:
                    raise SpecError(
                        f"YAML list key {key!r} at line {current.number} has no nested value."
                    )
                child_indent = lines[index].indent
                nested, index = _parse_block(lines, index, child_indent)
                if not isinstance(nested, dict):
                    raise SpecError(
                        f"YAML line {current.number} nested list item must be a mapping."
                    )
                item[key] = nested
            else:
                item[key] = _parse_scalar(inline, current.number)
                index += 1
            while index < len(lines) and lines[index].indent > indent:
                continuation = lines[index]
                if continuation.content.startswith("- "):
                    raise SpecError(
                        f"YAML line {continuation.number} starts a nested list; use a mapping item."
                    )
                child_key, child_inline = _split_key(continuation.content, continuation.number)
                if child_key in item:
                    raise SpecError(
                        f"YAML duplicate key {child_key!r} at line {continuation.number}."
                    )
                if child_inline is None:
                    index += 1
                    if index >= len(lines) or lines[index].indent <= continuation.indent:
                        raise SpecError(
                            f"YAML key {child_key!r} at line {continuation.number} has no value."
                        )
                    nested_indent = lines[index].indent
                    item[child_key], index = _parse_block(lines, index, nested_indent)
                    continue
                item[child_key] = _parse_scalar(child_inline, continuation.number)
                index += 1
            items.append(item)
            continue
        items.append(_parse_scalar(rest, current.number))
        index += 1
    if not items:
        raise SpecError("YAML list is empty.")
    return items, index


def _has_key_separator(content: str) -> bool:
    if content.startswith('"') or content.startswith("'"):
        return False
    return content.endswith(":") or ": " in content


def _split_key(content: str, line_number: int) -> tuple[str, str | None]:
    if (
        content.startswith("{")
        or content.startswith("[")
        or content.startswith("&")
        or content.startswith("*")
    ):
        raise SpecError(
            f"YAML line {line_number} uses unsupported syntax. Use block style or JSON."
        )
    if content.endswith(":"):
        raw_key = content[:-1].strip()
        inline: str | None = None
    else:
        separator = content.find(": ")
        if separator < 0:
            raise SpecError(f"YAML line {line_number} must use 'key:' or 'key: value'.")
        raw_key = content[:separator].strip()
        inline = content[separator + 2 :].strip()
        if inline == "":
            raise SpecError(f"YAML line {line_number} has an empty value.")
    key = _parse_key(raw_key, line_number)
    return key, inline


def _parse_key(raw_key: str, line_number: int) -> str:
    if len(raw_key) >= 2 and raw_key[0] == raw_key[-1] and raw_key[0] in {'"', "'"}:
        value = _parse_scalar(raw_key, line_number)
        if not isinstance(value, str) or value == "":
            raise SpecError(f"YAML line {line_number} has an invalid key.")
        return value
    if not raw_key or not (raw_key[0].isascii() and (raw_key[0].isalpha() or raw_key[0] == "_")):
        raise SpecError(f"YAML line {line_number} has an invalid key {raw_key!r}.")
    if any(not (char.isascii() and (char.isalnum() or char == "_")) for char in raw_key):
        raise SpecError(f"YAML line {line_number} has an invalid key {raw_key!r}.")
    return raw_key


def _parse_scalar(raw: str, line_number: int) -> JsonValue:
    if raw.startswith("{") or raw.startswith("[") or raw.startswith("|") or raw.startswith(">"):
        raise SpecError(
            f"YAML line {line_number} uses unsupported syntax. Use block style or JSON."
        )
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
        return _unquote(raw, line_number)
    if raw == "null":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if _is_int(raw):
        return int(raw)
    if _is_float(raw):
        return float(raw)
    if any(ord(char) < 32 for char in raw):
        raise SpecError(f"YAML line {line_number} must keep the value on one line.")
    if raw.startswith(("'", '"', "!", "&", "*")):
        raise SpecError(f"YAML line {line_number} uses unsupported syntax.")
    return raw


def _unquote(raw: str, line_number: int) -> str:
    quote = raw[0]
    body = raw[1:-1]
    if quote == "'":
        if "'" in body.replace("''", ""):
            raise SpecError(f"YAML line {line_number} has a broken single-quoted string.")
        return body.replace("''", "'")
    chars: list[str] = []
    escaped = False
    for char in body:
        if escaped:
            if char == "n":
                raise SpecError(f"YAML line {line_number} must keep strings on one line.")
            if char not in {'"', "\\", "/"}:
                raise SpecError(f"YAML line {line_number} has an unsupported escape.")
            chars.append(char)
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            raise SpecError(f"YAML line {line_number} has a broken double-quoted string.")
        chars.append(char)
    if escaped:
        raise SpecError(f"YAML line {line_number} has a trailing escape.")
    return "".join(chars)


def _is_int(raw: str) -> bool:
    if raw in {"", "-", "+"}:
        return False
    digits = raw[1:] if raw[0] in "+-" else raw
    return digits.isdigit() and not (len(digits) > 1 and digits.startswith("0"))


def _is_float(raw: str) -> bool:
    if raw.count(".") != 1:
        return False
    left, right = raw.split(".", 1)
    if left in {"", "-", "+"} or not right.isdigit():
        return False
    left_digits = left[1:] if left[0] in "+-" else left
    if not left_digits.isdigit():
        return False
    return not (len(left_digits) > 1 and left_digits.startswith("0"))
