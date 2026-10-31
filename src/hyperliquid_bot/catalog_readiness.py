"""Read-only selected-view planning; never execute or attach copied catalog SQL.

Reuses migration_verify's mount/descriptor guards unchanged. Only selected
Parquet footer metadata is queried, with external access restricted to held FDs.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import re
import stat
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import duckdb

from hyperliquid_bot import migration_verify as boundary
from hyperliquid_bot.local_mode import require_local_paper_mode

SCHEMA = "selected-catalog-readiness-v1"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_SUPPORTED_NAME = re.compile(r"(?:hist|live)_[a-z0-9_]{1,122}", re.IGNORECASE)
_TOKEN = re.compile(
    r"(?P<space>\s+)|(?P<line>--[^\r\n]*(?:\r\n|\r|\n|$))|(?P<block>/\*.*?\*/)"
    r'|(?P<quoted>"(?:""|[^"])*")'
    r"|(?P<string>'(?:''|[^'])*')|(?P<word>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<symbol>:=|[()[\],;*=.])",
    re.DOTALL,
)
_FORBIDDEN = frozenset(
    {
        "CREATE",
        "INSTALL",
        "LOAD",
        "ATTACH",
        "SET",
        "PRAGMA",
        "COPY",
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "EXPORT",
        "IMPORT",
    }
)
_TIME_FIELDS = (
    "ts",
    "open_time",
    "close_time",
    "funding_time_ms",
    "received_utc_ns",
    "event_time_ns",
    "open_time_ms",
    "timestamp",
)
_SYMBOL_FIELDS = ("symbol", "pair", "coin", "product", "instrument_id")


class Invalid(ValueError):
    """A fixed diagnostic code; copied SQL/data values are never exception text."""


class Incomplete(Invalid):
    """A resource bound prevented complete selected-input inspection."""


@dataclass(frozen=True, slots=True)
class Limits:
    catalog_bytes: int = 8_388_608
    definitions: int = 5000
    references: int = 100_000
    files: int = 64
    entries: int = 512
    depth: int = 16
    footer_bytes: int = 2_097_152
    metadata_rows: int = 5000
    seconds: float = 10.0

    def __post_init__(self) -> None:
        for key, maximum in (
            ("catalog_bytes", 16_777_216),
            ("definitions", 10_000),
            ("references", 200_000),
            ("files", 256),
            ("entries", 5000),
            ("depth", 32),
            ("footer_bytes", 8_388_608),
            ("metadata_rows", 20_000),
        ):
            value = getattr(self, key)
            if type(value) is not int or not 1 <= value <= maximum:
                raise Invalid("invalid_resource_bound")
        if (
            type(self.seconds) not in {int, float}
            or not math.isfinite(self.seconds)
            or not 0 < self.seconds <= 60
        ):
            raise Invalid("invalid_time_bound")


_DEFAULT_LIMITS = Limits()


@dataclass(frozen=True, slots=True)
class Binding:
    root: Path
    mount: boundary.MountIdentity


@dataclass(frozen=True, slots=True)
class Definition:
    paths: tuple[str, ...]
    union_by_name: bool | None


def logical_parts(value: str, *, glob: bool = True) -> tuple[str, tuple[str, ...]]:
    parts = value.split("/")
    if (
        len(value) > 4096
        or len(parts) < 2
        or parts[0] not in {"__HIST__", "__DC__"}
        or any(part in {"", ".", ".."} for part in parts[1:])
        or any(ord(char) < 32 or ord(char) == 127 for char in value)
        or "\\" in value
        or "://" in value
        or "**" in value
        or not value.endswith(".parquet")
        or (not glob and any(char in value for char in "*?[]"))
    ):
        raise Invalid("unsafe_logical_reference")
    return parts[0], tuple(parts[1:])


def _statements(
    sql: str, tick: Callable[[], None], maximum: int
) -> Iterator[list[tuple[str, str]]]:
    position = 0
    statement: list[tuple[str, str]] = []
    while position < len(sql):
        tick()
        match = _TOKEN.match(sql, position)
        if match is None:
            raise Invalid("unrecognized_or_unterminated_catalog_token")
        position = match.end()
        kind, text = match.lastgroup, match.group()
        if kind in {"space", "line", "block"}:
            continue
        if text == ";":
            if not statement:
                raise Invalid("empty_catalog_statement")
            yield statement
            statement = []
        else:
            statement.append((str(kind), text))
            if len(statement) > maximum * 2 + 32:
                raise Incomplete("statement_token_limit")
    if statement:
        raise Invalid("missing_statement_terminator")


def _definition(tokens: list[tuple[str, str]]) -> Definition | None:
    position = 0

    def take(expected: str) -> None:
        nonlocal position
        if (
            position >= len(tokens)
            or tokens[position][0] == "string"
            or tokens[position][1].upper() != expected
        ):
            raise Invalid("unsupported_view_definition")
        position += 1

    def path() -> str:
        nonlocal position
        if position >= len(tokens) or tokens[position][0] != "string":
            raise Invalid("unsupported_view_definition")
        value = tokens[position][1][1:-1].replace("''", "'")
        position += 1
        logical_parts(value)
        return value

    try:
        for expected in ("SELECT", "*", "FROM", "READ_PARQUET", "("):
            take(expected)
        paths: list[str] = []
        if position < len(tokens) and tokens[position][1] == "[":
            take("[")
            paths.append(path())
            while position < len(tokens) and tokens[position][1] == ",":
                take(",")
                paths.append(path())
            take("]")
        else:
            paths.append(path())
        union: bool | None = None
        if position < len(tokens) and tokens[position][1] == ",":
            take(",")
            take("UNION_BY_NAME")
            if position >= len(tokens) or tokens[position][1] not in {"=", ":="}:
                raise Invalid("unsupported_view_definition")
            position += 1
            if position >= len(tokens) or tokens[position][1].upper() not in {"TRUE", "FALSE"}:
                raise Invalid("unsupported_view_definition")
            union = tokens[position][1].upper() == "TRUE"
            position += 1
        take(")")
        if position != len(tokens):
            raise Invalid("unsupported_view_definition")
        return Definition(tuple(paths), union)
    except Invalid:
        return None


def parse_catalog(
    sql: str, selected: str, limits: Limits, tick: Callable[[], None]
) -> tuple[Definition, dict[str, object]]:
    if not _SUPPORTED_NAME.fullmatch(selected):
        raise Invalid("invalid_selected_view_name")
    selected = selected.lower()
    names: set[str] = set()
    unsupported: list[str] = []
    found: Definition | None = None
    references = 0
    for tokens in _statements(sql, tick, limits.references):
        words = [
            text.upper() if kind == "word" or kind == "symbol" else "<QUOTED>"
            for kind, text in tokens
        ]
        prefix = 4 if words[:3] == ["CREATE", "OR", "REPLACE"] else 2
        if (
            (prefix == 4 and words[3:4] != ["VIEW"])
            or (prefix == 2 and words[:2] != ["CREATE", "VIEW"])
            or len(tokens) <= prefix + 1
            or tokens[prefix][0] != "word"
            or words[prefix + 1] != "AS"
            or not _NAME.fullmatch(tokens[prefix][1])
        ):
            raise Invalid("unaccounted_top_level_statement")
        name = tokens[prefix][1].lower()
        if name in names:
            raise Invalid("duplicate_view_definition")
        names.add(name)
        if len(names) > limits.definitions:
            raise Incomplete("definition_limit")
        body = tokens[prefix + 2 :]
        if any(kind == "word" and text.upper() in _FORBIDDEN for kind, text in body):
            raise Invalid("unaccounted_command_in_view")
        references += sum(kind == "string" for kind, _ in body)
        if references > limits.references:
            raise Incomplete("reference_limit")
        definition = _definition(body) if _SUPPORTED_NAME.fullmatch(name) else None
        if definition is None:
            unsupported.append(name)
        elif name == selected:
            found = definition
    if selected not in names:
        raise Invalid("selected_view_missing")
    if found is None:
        raise Invalid("selected_view_unsupported")
    return found, {
        "state": "accounted_with_unsupported" if unsupported else "accounted",
        "definitions": len(names),
        "supported_definitions": len(names) - len(unsupported),
        "unsupported_definitions": unsupported,
        "literal_references": references,
        "whole_catalog_ready": False,
    }


def _read_catalog(
    descriptor: int, scan: boundary._Scan, maximum: int
) -> tuple[bytes, os.stat_result]:
    before = os.fstat(descriptor)
    if before.st_size > maximum:
        raise Incomplete("catalog_byte_limit")
    content = bytearray()
    while len(content) <= maximum:
        scan.tick()
        chunk = os.read(descriptor, min(65_536, maximum + 1 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
    if len(content) != before.st_size or boundary._signature(before) != boundary._signature(
        os.fstat(descriptor)
    ):
        raise Invalid("catalog_changed_or_incomplete_read")
    return bytes(content), before


def _pread(descriptor: int, count: int, offset: int, scan: boundary._Scan) -> bytes:
    result = bytearray()
    while len(result) < count:
        scan.tick()
        chunk = os.pread(descriptor, count - len(result), offset + len(result))
        if not chunk:
            break
        result.extend(chunk)
    return bytes(result)


def _metadata(descriptor: int, scan: boundary._Scan, limits: Limits) -> dict[str, Any]:
    before = os.fstat(descriptor)
    trailer = _pread(descriptor, 8, max(0, before.st_size - 8), scan)
    if (
        before.st_size < 12
        or _pread(descriptor, 4, 0, scan) != b"PAR1"
        or len(trailer) != 8
        or trailer[-4:] != b"PAR1"
    ):
        raise Invalid("invalid_parquet_envelope")
    footer_size = int.from_bytes(trailer[:4], "little")
    if not 0 < footer_size <= before.st_size - 12:
        raise Invalid("invalid_parquet_footer_length")
    if footer_size > limits.footer_bytes:
        raise Incomplete("footer_byte_limit")
    source = f"/proc/self/fd/{descriptor}"
    with duckdb.connect(
        ":memory:",
        config={
            "threads": 1,
            "memory_limit": "64MB",
            "temp_directory": "",
            "max_temp_directory_size": "0B",
            "autoload_known_extensions": False,
            "autoinstall_known_extensions": False,
        },
    ) as connection:
        connection.execute("SET allowed_paths = ?", [[source]])
        connection.execute("SET enable_external_access = false")
        connection.execute("SET lock_configuration = true")
        schema = connection.execute("DESCRIBE SELECT * FROM read_parquet(?)", [source]).fetchall()
        if not schema or len(schema) > limits.metadata_rows:
            raise Incomplete("schema_metadata_limit")
        rows = connection.execute(
            "SELECT path_in_schema, stats_min_value, stats_max_value, min_is_exact, max_is_exact, "
            "stats_null_count, row_group_num_rows FROM parquet_metadata(?) WHERE path_in_schema IN "
            "(SELECT unnest(?)) LIMIT ?",
            [source, [*_TIME_FIELDS, *_SYMBOL_FIELDS], limits.metadata_rows + 1],
        ).fetchall()
    if len(rows) > limits.metadata_rows:
        raise Incomplete("footer_metadata_row_limit")
    stats: list[dict[str, object]] = []
    for name, low, high, exact_low, exact_high, nulls, count in rows:
        checked = (
            exact_low is True
            and exact_high is True
            and type(nulls) is int
            and type(count) is int
            and 0 <= nulls < count
            and type(low) is str
            and type(high) is str
            and len(low) <= 128
            and len(high) <= 128
            and not any(ord(char) < 32 for char in low + high)
        )
        stats.append(
            {
                "column": name,
                "state": "present" if checked else "not_checked",
                "min": low if checked else None,
                "max": high if checked else None,
                "null_count": nulls,
                "scope": "footer_statistics_not_record_coverage",
            }
        )
    return {
        "schema": [{"name": row[0], "type": row[1]} for row in schema],
        "statistics": stats,
        "time_statistics": _stats_state(stats, _TIME_FIELDS),
        "symbol_statistics": _stats_state(stats, _SYMBOL_FIELDS),
    }


def _stats_state(stats: list[dict[str, object]], columns: tuple[str, ...]) -> str:
    selected = [item for item in stats if item["column"] in columns]
    return (
        "present"
        if selected and all(item["state"] == "present" for item in selected)
        else "not_checked"
    )


def _matches(file: str, pattern: str) -> bool:
    alias, parts = logical_parts(file, glob=False)
    other, patterns = logical_parts(pattern)
    return (
        alias == other
        and len(parts) == len(patterns)
        and all(fnmatch.fnmatchcase(part, glob) for part, glob in zip(parts, patterns, strict=True))
    )


def inspect_catalog(
    hist: Binding,
    *,
    view: str,
    copy_complete: bool,
    capture: Binding | None = None,
    selected_files: Sequence[str] = (),
    limits: Limits = _DEFAULT_LIMITS,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "selection_state": "not_checked",
        "selected_view": view.lower(),
        "scope": "selected_file_header_footer_readability",
        "migration_complete": False,
        "record_coverage": "not_checked",
        "research_ready": False,
        "whole_view_coverage": "not_checked",
        "files": [],
        "query_plan": None,
        "catalog": {"state": "not_checked"},
        "bindings": {},
        "limits": asdict(limits),
        "limited_by": [],
    }
    if copy_complete is not True:
        report["reason"] = "copy_completion_attestation_required"
        return report
    deadline = clock() + limits.seconds
    scans: dict[str, boundary._Scan] = {}
    roots: dict[str, Binding] = {"__HIST__": hist}
    if capture is not None:
        roots["__DC__"] = capture
    descriptors: list[tuple[int, Path, boundary._Scan, os.stat_result]] = []

    def bind(alias: str) -> tuple[Binding, boundary._Scan]:
        if alias not in roots:
            raise Invalid("selected_reference_has_unbound_root")
        binding = roots[alias]
        if alias not in scans:
            mount, _ = boundary._bound_mount(binding.root, binding.mount)
            scan = boundary._Scan(
                mount, boundary.ScanLimits(entries=limits.entries, seconds=limits.seconds), clock
            )
            scan.deadline = deadline
            if not stat.S_ISDIR(scan.safe(binding.root).st_mode):
                raise Invalid("selected_root_is_not_a_directory")
            scans[alias] = scan
            report["bindings"][alias] = {
                "root": str(binding.root),
                "mount_target": str(mount.target),
                "mount_source": mount.source,
                "filesystem": mount.filesystem,
                "mount_id": mount.mount_id,
                "device": mount.device,
            }
        return binding, scans[alias]

    def expand(reference: str) -> Iterator[str]:
        alias, parts = logical_parts(reference)
        if len(parts) > limits.depth:
            raise Incomplete("path_depth_limit")
        binding, scan = bind(alias)
        pending = [(binding.root, 0)]
        while pending:
            directory, index = pending.pop()
            scan.tick()
            component = parts[index]
            if any(token in component for token in "*?["):
                children: Iterator[Path] = scan.children(directory)
            else:
                children = iter((directory / component,))
            for path in children:
                if sum(item.entries for item in scans.values()) > limits.entries:
                    raise Incomplete("directory_entry_limit")
                if not fnmatch.fnmatchcase(path.name, component):
                    continue
                value = scan.safe(path)
                if index == len(parts) - 1:
                    if not stat.S_ISREG(value.st_mode):
                        raise Invalid("selected_input_is_not_regular")
                    yield f"{alias}/{path.relative_to(binding.root).as_posix()}"
                elif stat.S_ISDIR(value.st_mode):
                    pending.append((path, index + 1))
                else:
                    raise Invalid("non_directory_reference_component")

    try:
        _, hist_scan = bind("__HIST__")
        catalog_path = hist.root / "catalog.sql"
        catalog_fd = hist_scan.open_regular(catalog_path)
        before = os.fstat(catalog_fd)
        descriptors.append((catalog_fd, catalog_path, hist_scan, before))
        content, _ = _read_catalog(catalog_fd, hist_scan, limits.catalog_bytes)
        definition, accounting = parse_catalog(
            content.decode("utf-8"), view, limits, hist_scan.tick
        )
        report["catalog"] = {
            **accounting,
            "bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        if len(set(definition.paths)) != len(definition.paths):
            raise Invalid("duplicate_selected_declared_reference")
        requested = tuple(selected_files)
        if len(requested) > limits.files:
            raise Incomplete("explicit_file_limit")
        if len(set(requested)) != len(requested):
            raise Invalid("duplicate_explicit_files")
        for file in requested:
            matches = 0
            for pattern in definition.paths:
                hist_scan.tick()
                matches += _matches(file, pattern)
            if matches != 1:
                raise Invalid("explicit_file_undeclared_or_overlapping")
        references = requested or definition.paths
        chosen: list[str] = []
        seen: set[str] = set()
        for reference in references:
            matched = False
            for file in expand(reference):
                matched = True
                if file in seen:
                    raise Invalid("duplicate_or_overlapping_selected_inputs")
                seen.add(file)
                chosen.append(file)
                if len(chosen) > limits.files:
                    raise Incomplete("selected_file_limit")
            if not matched:
                raise Invalid("reference_has_no_matching_files")
        identities: set[tuple[int, int]] = set()
        parameters: list[str] = []
        for file in chosen:
            alias, parts = logical_parts(file, glob=False)
            binding, scan = bind(alias)
            path = binding.root.joinpath(*parts)
            descriptor = scan.open_regular(path)
            before = os.fstat(descriptor)
            descriptors.append((descriptor, path, scan, before))
            identity = (before.st_dev, before.st_ino)
            if identity in identities:
                raise Invalid("duplicate_physical_selected_file")
            identities.add(identity)
            metadata = _metadata(descriptor, scan, limits)
            report["files"].append(
                {
                    "logical_path": file,
                    "bytes": before.st_size,
                    "identity": {
                        "inode": before.st_ino,
                        "device": before.st_dev,
                        "mtime_ns": before.st_mtime_ns,
                        "ctime_ns": before.st_ctime_ns,
                    },
                    "content_checksum": "not_checked",
                    **metadata,
                }
            )
            parameters.append(str(path))
        schemas: dict[str, str] = {}
        first_schema = report["files"][0]["schema"]
        for file in report["files"]:
            if definition.union_by_name is not True and file["schema"] != first_schema:
                raise Invalid("selected_file_schemas_differ")
            for column in file["schema"]:
                name = column["name"].casefold()
                if name in schemas and schemas[name] != column["type"]:
                    raise Invalid("selected_column_types_differ")
                schemas[name] = column["type"]
        for descriptor, path, scan, before in descriptors:
            scan.tick()
            if boundary._signature(os.fstat(descriptor)) != boundary._signature(
                before
            ) or boundary._signature(scan.safe(path)) != boundary._signature(before):
                raise Invalid("input_changed_during_inspection")
        for alias, scan in scans.items():
            final, _ = boundary._bound_mount(roots[alias].root, roots[alias].mount)
            if final != scan.mount:
                raise Invalid("mount_changed_during_inspection")
        option = (
            ""
            if definition.union_by_name is None
            else f", union_by_name = {str(definition.union_by_name).lower()}"
        )
        report.update(
            selection_state="ready",
            whole_view_coverage="not_checked" if requested else "paths_enumerated_only",
            query_plan={
                "sql": f"SELECT * FROM read_parquet(?{option})",
                "parameters": [parameters],
                "source_view": view.lower(),
                "explicit_subset": bool(requested),
                "requires_revalidation_before_query": True,
                "execution_performed": False,
            },
        )
    except boundary._Stop:
        report["reason"] = "cooperative_or_directory_limit"
        report["limited_by"] = sorted(
            {reason for scan in scans.values() for reason in scan.reasons}
        )
    except Incomplete as error:
        report["reason"] = str(error)
        report["limited_by"] = [str(error)]
    except FileNotFoundError:
        report["selection_state"] = "missing"
        report["reason"] = "selected_root_catalog_or_input_missing"
    except (Invalid, boundary._Unsafe, OSError, UnicodeError, duckdb.Error) as error:
        report["selection_state"] = (
            "in_progress"
            if isinstance(error, Invalid)
            and str(error)
            in {"catalog_changed_or_incomplete_read", "input_changed_during_inspection"}
            else "invalid"
        )
        report["reason"] = (
            str(error)
            if isinstance(error, (Invalid, boundary._Unsafe))
            else "metadata_inspection_failed"
        )
    finally:
        for descriptor, *_ in descriptors:
            os.close(descriptor)
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--view", required=True)
    parser.add_argument("--copy-complete", action="store_true")
    parser.add_argument("--file", action="append", default=[])
    for prefix in ("hist", "capture"):
        required = prefix == "hist"
        parser.add_argument(f"--{prefix}-root", type=Path, required=required)
        parser.add_argument(f"--{prefix}-mount-target", type=Path, required=required)
        parser.add_argument(f"--{prefix}-mount-source", required=required)
        parser.add_argument(
            f"--{prefix}-filesystem", choices=("nfs", "nfs4", "cifs"), required=required
        )
    for key, value in asdict(Limits()).items():
        parser.add_argument(
            f"--max-{key.replace('_', '-')}", type=float if key == "seconds" else int, default=value
        )
    args = parser.parse_args(argv)
    try:
        require_local_paper_mode(os.environ.get("TRADING_MODE"))
        capture_values = (
            args.capture_root,
            args.capture_mount_target,
            args.capture_mount_source,
            args.capture_filesystem,
        )
        if any(value is not None for value in capture_values) and not all(
            value is not None for value in capture_values
        ):
            raise Invalid("incomplete_capture_binding")
        hist = Binding(
            args.hist_root,
            boundary.MountIdentity(
                args.hist_mount_target, args.hist_mount_source, args.hist_filesystem
            ),
        )
        capture = (
            Binding(
                args.capture_root,
                boundary.MountIdentity(
                    args.capture_mount_target, args.capture_mount_source, args.capture_filesystem
                ),
            )
            if args.capture_root is not None
            else None
        )
        limits = Limits(**{key: getattr(args, f"max_{key}") for key in asdict(Limits())})
        report = inspect_catalog(
            hist,
            view=args.view,
            copy_complete=args.copy_complete,
            capture=capture,
            selected_files=args.file,
            limits=limits,
        )
    except ValueError:
        parser.error("Invalid configuration or non-PAPER development mode.")
    print(json.dumps(report, indent=2, sort_keys=True))
    return (
        0
        if report["selection_state"] == "ready"
        else 2
        if report["selection_state"] == "invalid"
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
