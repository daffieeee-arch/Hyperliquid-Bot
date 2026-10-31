"""Temporary synthetic files/mount metadata only. No NAS path is inspected."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any

import duckdb
import pytest

from hyperliquid_bot import catalog_readiness as tool
from hyperliquid_bot import migration_verify as boundary
from research.hist_etl.catalog import _view


def sql(paths: tuple[str, ...], *, name: str = "hist_fixture", option: str = "") -> str:
    argument = (
        repr(paths[0]) if len(paths) == 1 else "[" + ",".join(repr(path) for path in paths) + "]"
    )
    return f"CREATE OR REPLACE VIEW {name} AS SELECT * FROM read_parquet({argument}{option});\n"


@pytest.fixture
def mounted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tool.Binding:
    export = tmp_path / "synthetic export"
    root = export / "hist"
    root.mkdir(parents=True)
    mount = boundary.MountIdentity(export, "synthetic-nas:/project", "nfs4")
    value = export.stat()
    device = f"{os.major(value.st_dev)}:{os.minor(value.st_dev)}"
    target = str(export).replace(" ", r"\040")
    monkeypatch.setattr(
        boundary,
        "_read_mountinfo",
        lambda: f"42 1 {device} / {target} rw - nfs4 synthetic-nas:/project rw\n",
    )
    monkeypatch.setattr(boundary, "_descriptor_mount_id", lambda _fd: "42")
    return tool.Binding(root, mount)


def parquet(path: Path, *, symbol: bool = True, extra: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(":memory:") as connection:
        fields = "TIMESTAMP '2025-01-01' AS ts, 9876543.21 AS close"
        if symbol:
            fields += ", 'XBTUSD' AS symbol"
        if extra:
            fields += ", 1 AS another_column"
        connection.execute(f"COPY (SELECT {fields}) TO ? (FORMAT PARQUET)", [str(path)])


def setup_catalog(
    binding: tool.Binding,
    paths: tuple[str, ...] = ("__HIST__/parquet/a.parquet",),
    *,
    option: str = "",
) -> None:
    (binding.root / "catalog.sql").write_text(sql(paths, option=option))
    for path in paths:
        if "*" not in path and path.startswith("__HIST__/"):
            parquet(binding.root / path.removeprefix("__HIST__/"))


def inspect(binding: tool.Binding, **kwargs: Any) -> dict[str, Any]:
    return tool.inspect_catalog(binding, view="hist_fixture", copy_complete=True, **kwargs)


@pytest.mark.parametrize("option", ["", ", union_by_name := true", ", UNION_BY_NAME = false"])
@pytest.mark.parametrize("list_form", [False, True])
def test_generated_single_list_and_real_boolean_options_emit_usable_plan(
    mounted: tool.Binding,
    option: str,
    list_form: bool,
) -> None:
    paths = (
        ("__HIST__/parquet/a.parquet", "__HIST__/parquet/b.parquet")
        if list_form
        else ("__HIST__/parquet/a.parquet",)
    )
    setup_catalog(mounted, paths, option=option)
    if list_form and not option:
        (mounted.root / "catalog.sql").write_text(
            _view("hist_fixture", tuple(path.removeprefix("__HIST__/") for path in paths))
        )
    before = {
        p: (p.read_bytes(), p.stat().st_mtime_ns) for p in mounted.root.rglob("*") if p.is_file()
    }
    report = inspect(mounted)
    assert report["selection_state"] == "ready"
    assert len(report["files"]) == len(paths)
    assert report["whole_view_coverage"] == "paths_enumerated_only"
    assert report["migration_complete"] is report["research_ready"] is False
    assert report["record_coverage"] == "not_checked"
    assert report["bindings"]["__HIST__"]["mount_source"] == mounted.mount.source
    assert report["files"][0]["identity"]["inode"] > 0
    assert report["files"][0]["content_checksum"] == "not_checked"
    assert report["files"][0]["time_statistics"] == "present"
    assert report["files"][0]["symbol_statistics"] == "present"
    assert "9876543.21" not in json.dumps(report)
    plan = report["query_plan"]
    assert plan["requires_revalidation_before_query"] is True
    assert plan["execution_performed"] is False
    # Fixture-only proof of parameter binding; no copied catalog statement runs.
    with duckdb.connect(":memory:") as connection:
        assert connection.execute(
            f"SELECT count(*) FROM ({plan['sql']})", plan["parameters"]
        ).fetchone() == (len(paths),)
    assert set(before) == {p for p in mounted.root.rglob("*") if p.is_file()}
    assert before == {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in before}


@pytest.mark.parametrize("newline", ["\n", "\r", "\r\n"])
def test_quote_comment_aware_accounting_never_hides_unknown_trailing_sql(newline: str) -> None:
    text = sql(("__HIST__/a.parquet",))
    definition, report = tool.parse_catalog(
        "/* comment; ' */" + text + "-- comment; '" + newline,
        "HIST_FIXTURE",
        tool.Limits(),
        lambda: None,
    )
    assert definition.paths == ("__HIST__/a.parquet",)
    assert report["definitions"] == 1
    with pytest.raises(tool.Invalid, match="top_level"):
        tool.parse_catalog(
            text + "-- comment" + newline + "DROP TABLE x;",
            "hist_fixture",
            tool.Limits(),
            lambda: None,
        )


def test_unsupported_distinct_views_remain_named_without_global_readiness(
    mounted: tool.Binding,
) -> None:
    setup_catalog(mounted)
    path = mounted.root / "catalog.sql"
    path.write_text(
        path.read_text() + "CREATE VIEW hist_legacy_full AS SELECT DISTINCT ON (ts) * FROM "
        "read_parquet(['__HIST__/missing.parquet'], union_by_name := true) ORDER BY ts;\n"
    )
    report = inspect(mounted)
    assert report["selection_state"] == "ready"
    assert report["catalog"]["definitions"] == 2
    assert report["catalog"]["unsupported_definitions"] == ["hist_legacy_full"]
    assert report["catalog"]["whole_catalog_ready"] is False
    rejected = tool.inspect_catalog(mounted, view="hist_legacy_full", copy_complete=True)
    assert rejected["selection_state"] == "invalid"
    assert rejected["query_plan"] is None


@pytest.mark.parametrize(
    "bad",
    [
        "CREATE VIEW hist_other AS SELECT * FROM read_parquet('__HIST__/x.parquet');",
        "CREATE VIEW HIST_FIXTURE AS SELECT * FROM read_parquet('__HIST__/x.parquet');",
        "INSTALL httpfs;",
        "ATTACH 'elsewhere';",
        "SELECT 1;",
        "SET x = true;",
    ],
)
def test_missing_duplicate_and_unaccounted_definitions_fail(
    mounted: tool.Binding, bad: str
) -> None:
    setup_catalog(mounted)
    path = mounted.root / "catalog.sql"
    path.write_text(bad if "hist_other" in bad else path.read_text() + bad)
    report = inspect(mounted)
    assert report["selection_state"] == "invalid" and report["query_plan"] is None


@pytest.mark.parametrize(
    "body",
    [
        "SELECT * FROM read_parquet('__HIST__/a.parquet') WHERE true",
        "SELECT * FROM read_parquet('__HIST__/a.parquet', filename = true)",
        "SELECT * FROM read_parquet(['__HIST__/a.parquet',])",
        "SELECT * FROM read_parquet('__HIST__/a.parquet', "
        "union_by_name := true, union_by_name := true)",
        "SELECT * FROM read_parquet('__HIST__/a.parquet'",
    ],
)
def test_unsupported_or_malformed_selected_body_cannot_match_a_prefix(
    mounted: tool.Binding, body: str
) -> None:
    (mounted.root / "catalog.sql").write_text(f"CREATE VIEW hist_fixture AS {body};")
    assert inspect(mounted)["selection_state"] == "invalid"


@pytest.mark.parametrize("name", ["select", "from", "table", "group"])
def test_reserved_or_out_of_contract_selected_identifiers_are_rejected(name: str) -> None:
    with pytest.raises(tool.Invalid, match="selected_view_name"):
        tool.parse_catalog(
            sql(("__HIST__/a.parquet",), name=name), name, tool.Limits(), lambda: None
        )


@pytest.mark.parametrize(
    "reference",
    [
        "https://server/file.parquet",
        "/old/root/file.parquet",
        "__HIST__/../a.parquet",
        "__HIST__/a//b.parquet",
        "__HIST__/**/a.parquet",
        "__HIST__/secret.env",
        "__HIST__/a\\b.parquet",
        "__HIST__/line\nbreak.parquet",
    ],
)
def test_unsafe_references_are_never_opened(mounted: tool.Binding, reference: str) -> None:
    (mounted.root / "catalog.sql").write_text(sql((reference,)))
    assert inspect(mounted)["selection_state"] == "invalid"


def test_explicit_subset_is_declared_bounded_and_not_whole_view_coverage(
    mounted: tool.Binding,
) -> None:
    parquet(mounted.root / "parquet" / "a.parquet")
    (mounted.root / "catalog.sql").write_text(sql(("__HIST__/parquet/*.parquet",)))
    report = inspect(mounted, selected_files=("__HIST__/parquet/a.parquet",))
    assert report["selection_state"] == "ready"
    assert report["whole_view_coverage"] == "not_checked"
    assert report["query_plan"]["explicit_subset"] is True
    assert (
        inspect(mounted, selected_files=("__HIST__/elsewhere/a.parquet",))["selection_state"]
        == "invalid"
    )


@pytest.mark.parametrize("subset", [False, True])
def test_overlapping_and_duplicate_selected_inputs_are_not_deduplicated_silently(
    mounted: tool.Binding, subset: bool
) -> None:
    parquet(mounted.root / "parquet" / "a.parquet")
    for references in (
        ("__HIST__/parquet/a.parquet", "__HIST__/parquet/*.parquet"),
        ("__HIST__/parquet/a.parquet",) * 2,
    ):
        (mounted.root / "catalog.sql").write_text(sql(references))
        report = inspect(mounted, selected_files=("__HIST__/parquet/a.parquet",) if subset else ())
        assert report["selection_state"] == "invalid" and report["query_plan"] is None


def test_hard_linked_inputs_are_not_counted_twice(mounted: tool.Binding) -> None:
    setup_catalog(mounted)
    (mounted.root / "parquet/b.parquet").hardlink_to(mounted.root / "parquet/a.parquet")
    (mounted.root / "catalog.sql").write_text(sql(("__HIST__/parquet/*.parquet",)))
    assert inspect(mounted)["reason"] == "duplicate_physical_selected_file"


def test_explicit_hist_and_capture_root_bindings_are_required_for_used_paths(
    mounted: tool.Binding,
) -> None:
    capture = tool.Binding(mounted.root.parent / "capture", mounted.mount)
    capture.root.mkdir()
    parquet(capture.root / "raw/a.parquet")
    (mounted.root / "catalog.sql").write_text(sql(("__DC__/raw/a.parquet",), name="live_fixture"))
    missing = tool.inspect_catalog(mounted, view="live_fixture", copy_complete=True)
    assert missing["selection_state"] == "invalid"
    ready = tool.inspect_catalog(mounted, view="live_fixture", copy_complete=True, capture=capture)
    assert ready["selection_state"] == "ready"


@pytest.mark.parametrize(
    "limit",
    [
        "catalog_bytes",
        "definitions",
        "references",
        "files",
        "entries",
        "depth",
        "footer_bytes",
        "metadata_rows",
    ],
)
def test_resource_limits_cannot_turn_partial_work_into_ready(
    mounted: tool.Binding, limit: str
) -> None:
    setup_catalog(mounted, ("__HIST__/parquet/a.parquet", "__HIST__/parquet/b.parquet"))
    if limit == "definitions":
        path = mounted.root / "catalog.sql"
        path.write_text(path.read_text() + sql(("__HIST__/other.parquet",), name="hist_other"))
    if limit == "entries":
        (mounted.root / "catalog.sql").write_text(sql(("__HIST__/parquet/*.parquet",)))
    limits = replace(tool.Limits(), **{limit: 1})
    report = inspect(mounted, limits=limits)
    assert report["selection_state"] != "ready" and report["query_plan"] is None


def test_cooperative_deadline_never_claims_a_wall_clock_kill(mounted: tool.Binding) -> None:
    setup_catalog(mounted)
    counter = iter(range(10000))
    report = inspect(
        mounted, clock=lambda: float(next(counter)), limits=replace(tool.Limits(), seconds=1)
    )
    assert report["selection_state"] == "not_checked"
    assert report["query_plan"] is None


def test_actual_schema_mismatch_is_refused_and_union_by_name_preserved(
    mounted: tool.Binding,
) -> None:
    setup_catalog(mounted, ("__HIST__/parquet/a.parquet", "__HIST__/parquet/b.parquet"))
    parquet(mounted.root / "parquet/b.parquet", extra=True)
    assert inspect(mounted)["reason"] == "selected_file_schemas_differ"
    (mounted.root / "catalog.sql").write_text(
        sql(("__HIST__/parquet/*.parquet",), option=", union_by_name := true")
    )
    report = inspect(mounted)
    assert report["selection_state"] == "ready"
    assert "union_by_name = true" in report["query_plan"]["sql"]


def test_missing_symbol_statistics_are_not_invented(mounted: tool.Binding) -> None:
    setup_catalog(mounted)
    parquet(mounted.root / "parquet/a.parquet", symbol=False)
    report = inspect(mounted)
    assert report["selection_state"] == "ready"
    assert report["files"][0]["symbol_statistics"] == "not_checked"


@pytest.mark.parametrize("kind", ["missing", "corrupt", "symlink"])
def test_missing_corrupt_or_symlink_files_do_not_get_a_plan(
    mounted: tool.Binding, kind: str
) -> None:
    setup_catalog(mounted)
    path = mounted.root / "parquet/a.parquet"
    path.unlink()
    if kind == "corrupt":
        path.write_bytes(b"PAR1garbagePAR1")
    elif kind == "symlink":
        outside = mounted.root.parent / "outside.parquet"
        parquet(outside)
        path.symlink_to(outside)
    report = inspect(mounted)
    assert report["selection_state"] in {"invalid", "missing"}
    assert report["query_plan"] is None


def test_short_reads_reach_eof_and_short_header_reads_are_reassembled(
    mounted: tool.Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_catalog(mounted)
    read, pread = os.read, os.pread
    monkeypatch.setattr(os, "read", lambda fd, size: read(fd, min(size, 7)))
    monkeypatch.setattr(os, "pread", lambda fd, size, offset: pread(fd, min(size, 2), offset))
    assert inspect(mounted)["selection_state"] == "ready"


def test_catalog_and_file_mutation_withdraw_the_plan(
    mounted: tool.Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_catalog(mounted)
    original = tool._metadata

    def change_after_metadata(fd: int, scan: boundary._Scan, limits: tool.Limits) -> dict[str, Any]:
        result = original(fd, scan, limits)
        (mounted.root / "catalog.sql").write_text("-- changed\n")
        return result

    monkeypatch.setattr(tool, "_metadata", change_after_metadata)
    report = inspect(mounted)
    assert report["reason"] == "input_changed_during_inspection" and report["query_plan"] is None


def test_attestation_and_wrong_mount_identity_fail_before_data_reads(
    mounted: tool.Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(_scan: object, _path: Path) -> int:
        pytest.fail("No selected/catalog bytes before attestation and mount identity")

    monkeypatch.setattr(boundary._Scan, "open_regular", forbidden)
    assert (
        tool.inspect_catalog(mounted, view="hist_fixture", copy_complete=False)["selection_state"]
        == "not_checked"
    )
    wrong = tool.Binding(mounted.root, replace(mounted.mount, source="other:/export"))
    assert inspect(wrong)["selection_state"] == "invalid"


def test_mount_changed_after_metadata_never_emits_query_plan(
    mounted: tool.Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_catalog(mounted)
    original = tool._metadata

    def change(fd: int, scan: boundary._Scan, limits: tool.Limits) -> dict[str, Any]:
        result = original(fd, scan, limits)
        monkeypatch.setattr(boundary, "_descriptor_mount_id", lambda _fd: "99")
        return result

    monkeypatch.setattr(tool, "_metadata", change)
    assert inspect(mounted)["selection_state"] == "invalid"


def test_large_catalog_accounts_unselected_paths_without_opening_them(
    mounted: tool.Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_catalog(mounted)
    path = mounted.root / "catalog.sql"
    path.write_text(
        path.read_text()
        + "".join(
            sql((f"__HIST__/absent/{i}/x.parquet",), name=f"hist_other_{i}") for i in range(1818)
        )
    )
    opened: list[str] = []
    original = boundary._Scan.open_regular

    def record(scan: boundary._Scan, path: Path) -> int:
        opened.append(path.name)
        return original(scan, path)

    monkeypatch.setattr(boundary._Scan, "open_regular", record)
    report = inspect(mounted)
    assert report["selection_state"] == "ready"
    assert report["catalog"]["definitions"] == 1819
    assert opened == ["catalog.sql", "a.parquet"]


def test_cli_stdout_only_and_paper_mode_guard(
    mounted: tool.Binding, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_catalog(mounted)
    args = [
        "--view",
        "hist_fixture",
        "--hist-root",
        str(mounted.root),
        "--hist-mount-target",
        str(mounted.mount.target),
        "--hist-mount-source",
        mounted.mount.source,
        "--hist-filesystem",
        "nfs4",
        "--copy-complete",
    ]
    assert tool.main(args) == 0
    assert json.loads(capsys.readouterr().out)["selection_state"] == "ready"
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(SystemExit) as rejected:
        tool.main(args)
    assert rejected.value.code == 2
    assert capsys.readouterr().out == ""


def test_all_null_and_oversize_footer_statistics_remain_unchecked(mounted: tool.Binding) -> None:
    setup_catalog(mounted)
    path = mounted.root / "parquet/a.parquet"
    with duckdb.connect(":memory:") as connection:
        connection.execute(
            "COPY (SELECT CAST(NULL AS TIMESTAMP) AS ts, repeat('X',200) AS symbol) "
            "TO ? (FORMAT PARQUET)",
            [str(path)],
        )
    report = inspect(mounted)
    assert report["selection_state"] == "ready"
    assert report["files"][0]["time_statistics"] == "not_checked"
    assert report["files"][0]["symbol_statistics"] == "not_checked"
    assert all(
        row["state"] == "not_checked" and row["min"] is None
        for row in report["files"][0]["statistics"]
    )
    assert (
        tool._stats_state(
            [
                {"column": "ts", "state": "present"},
                {"column": "ts", "state": "not_checked"},
            ],
            ("ts",),
        )
        == "not_checked"
    )


def test_selected_file_mutation_after_metadata_withdraws_plan(
    mounted: tool.Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_catalog(mounted)
    original = tool._metadata

    def change(fd: int, scan: boundary._Scan, limits: tool.Limits) -> dict[str, Any]:
        result = original(fd, scan, limits)
        path = mounted.root / "parquet/a.parquet"
        with path.open("ab") as target:
            target.write(b"changed")
        return result

    monkeypatch.setattr(tool, "_metadata", change)
    report = inspect(mounted)
    assert report["reason"] == "input_changed_during_inspection"
    assert report["query_plan"] is None


def test_ancestor_swap_during_regular_open_never_authorizes_changed_path(
    mounted: tool.Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_catalog(mounted)
    outside = mounted.root.parent / "outside"
    outside.mkdir()
    parquet(outside / "a.parquet", extra=True)
    original_open = os.open
    swapped = False

    def swap(path: str | Path, flags: int, *, dir_fd: int | None = None) -> int:
        nonlocal swapped
        if path == "a.parquet" and not flags & os.O_PATH and not swapped:
            (mounted.root / "parquet").rename(mounted.root / "original")
            (mounted.root / "parquet").symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap)
    report = inspect(mounted)
    assert swapped and report["selection_state"] == "invalid"
    assert report["query_plan"] is None
    assert all(
        column["name"] != "another_column" for file in report["files"] for column in file["schema"]
    )


def test_same_device_nested_mount_id_cannot_open_selected_payload(
    mounted: tool.Binding, monkeypatch: pytest.MonkeyPatch
) -> None:
    setup_catalog(mounted)

    def mount_id(fd: int) -> str:
        path = Path(os.readlink(f"/proc/self/fd/{fd}"))
        return "99" if path.is_relative_to(mounted.root / "parquet") else "42"

    monkeypatch.setattr(boundary, "_descriptor_mount_id", mount_id)
    report = inspect(mounted)
    assert report["selection_state"] == "invalid"
    assert report["files"] == [] and report["query_plan"] is None
