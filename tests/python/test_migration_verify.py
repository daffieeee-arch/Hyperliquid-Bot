"""Synthetic local fixtures only; no real mount or archive is inspected."""

from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path
from typing import cast

import duckdb
import pytest

from hyperliquid_bot import migration_verify as verifier
from hyperliquid_bot.hyperliquid_raw_research import data1a_capture_claim
from hyperliquid_bot.parquet_research import _CREATE_SEGMENT_TABLE
from hyperliquid_bot.reconstructable_paths import data1a_run_paths


def _mount_line(
    target: Path,
    source: str = "test-nas:/project",
    *,
    device: str | None = None,
    mount_id: str = "42",
    filesystem: str = "nfs4",
) -> str:
    value = target.stat()
    token = device or f"{os.major(value.st_dev)}:{os.minor(value.st_dev)}"
    escaped = str(target).replace(" ", r"\040")
    return f"{mount_id} 1 {token} / {escaped} rw - {filesystem} {source} rw\n"


@pytest.fixture
def mounted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, verifier.MountIdentity]:
    export = tmp_path / "synthetic export"
    export.mkdir()
    root = export / "copy"
    root.mkdir()
    identity = verifier.MountIdentity(export, "test-nas:/project", "nfs4")
    monkeypatch.setattr(verifier, "_read_mountinfo", lambda: _mount_line(export))
    monkeypatch.setattr(verifier, "_descriptor_mount_id", lambda _descriptor: "42")
    return root, identity


def _part(
    path: Path, venue: str = "hyperliquid", *, product: str = "BTC-PERP", wrong_schema: bool = False
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(":memory:")
    try:
        if wrong_schema:
            connection.execute("CREATE TABLE raw_segment AS SELECT 1 AS unexpected")
        else:
            connection.execute(_CREATE_SEGMENT_TABLE)
            connection.execute(
                "INSERT INTO raw_segment VALUES (1, ?, ?, 'trades', 'session', 1, "
                "1780000000000000000, 1, 'inbound', 'text', 'utf-8', ?, 'not-a-real-digest')",
                [venue, product, b"unparsed-payload-must-not-appear-in-report"],
            )
        connection.execute("COPY raw_segment TO ? (FORMAT PARQUET)", [str(path)])
    finally:
        connection.close()


def _run(root: Path, *, run_id: str = "fixture-run") -> Path:
    paths = data1a_run_paths(root / "data-capture", run_id)
    paths.raw_dir.mkdir(parents=True)
    claim = data1a_capture_claim(run_id=run_id, duration_seconds=1, paths=paths)
    paths.capture_claim_path.write_text(json.dumps(claim))
    paths.capture_health_path.write_text(
        json.dumps(
            {
                "schema": "data-1a-retained-capture-health-v1",
                "run_id": run_id,
                "status": "COMPLETED",
                "parquet_files": 1,
            }
        )
    )
    _part(paths.raw_dir / "part-000001.parquet")
    return paths.run_dir


def _first_run(report: dict[str, object]) -> dict[str, object]:
    lanes = cast(list[dict[str, object]], report["captures"])
    return cast(list[dict[str, object]], lanes[0]["runs"])[0]


def _parts(report: dict[str, object]) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], _first_run(report)["parts"])


def test_copy_attestation_is_required_before_even_mount_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden() -> str:
        pytest.fail("No mount/root inspection before operator attestation")

    monkeypatch.setattr(verifier, "_read_mountinfo", forbidden)
    report = verifier.inventory(
        tmp_path, verifier.MountIdentity(tmp_path, "test:/export", "nfs4"), copy_complete=False
    )
    assert report["mount"] == {"state": "not_checked"}
    assert report["migration_complete"] is False


@pytest.mark.parametrize(
    "change", ["source", "target", "filesystem", "local", "relative", "parent"]
)
def test_mount_identity_fails_before_data_root_probe(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    root, identity = mounted
    expected = identity
    if change == "source":
        expected = verifier.MountIdentity(identity.target, "other:/export", "nfs4")
    elif change == "target":
        expected = verifier.MountIdentity(root, identity.source, "nfs4")
    elif change == "filesystem":
        expected = verifier.MountIdentity(identity.target, identity.source, "cifs")
    elif change == "local":
        monkeypatch.setattr(
            verifier, "_read_mountinfo", lambda: _mount_line(identity.target, filesystem="ext4")
        )
    elif change == "relative":
        root = Path("not-an-explicit-root")
    else:
        root = root / ".." / "copy"

    def forbidden(_self: object, _path: Path) -> os.stat_result:
        pytest.fail("Data root touched before mount identity verified")

    monkeypatch.setattr(verifier._Scan, "safe", forbidden)
    report = verifier.inventory(root, expected, copy_complete=True)
    assert report["mount"] == {"state": "invalid"}
    assert report["captures"] == []


def test_sampled_footer_and_archive_metadata_do_not_claim_integrity(
    mounted: tuple[Path, verifier.MountIdentity],
) -> None:
    root, identity = mounted
    _run(root)
    archive = root / "hist-archives" / "binance"
    archive.mkdir(parents=True)
    (archive / "BTCUSDT-2025-09.zip").write_bytes(b"not-read-or-hashed")
    (archive / "copy.partial").write_bytes(b"in-progress")
    (archive / ".env").write_text("unrelated-file-body")
    report = verifier.inventory(root, identity, copy_complete=True)
    part = _parts(report)[0]
    assert part["state"] == "present"
    assert part["receipt_utc_ns_min"] == 1780000000000000000
    assert part["receipt_utc_ns_max"] == 1780000000000000000
    assert report["transfer_integrity"] == report["record_coverage"] == "not_checked"
    assert report["migration_complete"] is False
    text = json.dumps(report)
    assert "unparsed-payload" not in text and "unrelated-file-body" not in text
    assert "in_progress" in text and "2025-09" in text
    assert ".env" not in text


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", "wrong"),
        ("run_id", "other"),
        ("venue", "other"),
        ("product", "ETH-PERP"),
        ("signing", True),
        ("signing", 0),
    ],
)
def test_bad_claim_identity_does_not_authorize_footer_read(
    mounted: tuple[Path, verifier.MountIdentity],
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    root, identity = mounted
    run = _run(root)
    path = run / "capture-claim.json"
    claim = json.loads(path.read_text())
    claim[field] = value
    path.write_text(json.dumps(claim))

    def forbidden(*_arguments: object) -> dict[str, object]:
        pytest.fail("Invalid claim must not trigger Parquet inspection")

    monkeypatch.setattr(verifier, "_footer", forbidden)
    report = verifier.inventory(root, identity, copy_complete=True)
    assert _first_run(report)["claim"] == "invalid"
    assert _parts(report)[0]["state"] == "not_checked"


@pytest.mark.parametrize(
    "kind",
    [
        "missing_claim",
        "malformed_claim",
        "duplicate_key",
        "large_claim",
        "missing_health",
        "partial",
        "corrupt_part",
        "wrong_schema",
        "wrong_venue",
        "symlink_part",
        "count_mismatch",
    ],
)
def test_copy_gaps_and_invalid_parts_have_distinct_states(
    mounted: tuple[Path, verifier.MountIdentity], tmp_path: Path, kind: str
) -> None:
    root, identity = mounted
    run = _run(root)
    claim = run / "capture-claim.json"
    part = run / "raw" / "part-000001.parquet"
    if kind == "missing_claim":
        claim.unlink()
    elif kind == "malformed_claim":
        claim.write_text("{")
    elif kind == "duplicate_key":
        claim.write_text('{"schema":"a","schema":"b"}')
    elif kind == "large_claim":
        claim.write_text("x" * 131073)
    elif kind == "missing_health":
        (run / "capture-health.json").unlink()
    elif kind == "partial":
        (run / "raw" / ".copy.partial").write_bytes(b"not-to-open")
    elif kind == "count_mismatch":
        health = run / "capture-health.json"
        body = json.loads(health.read_text())
        body["parquet_files"] = 2
        health.write_text(json.dumps(body))
    else:
        part.unlink()
        if kind == "corrupt_part":
            part.write_bytes(b"not-parquet")
        elif kind == "wrong_schema":
            _part(part, wrong_schema=True)
        elif kind == "wrong_venue":
            _part(part, venue="other")
        else:
            outside = tmp_path / "outside.parquet"
            _part(outside)
            part.symlink_to(outside)
    report = verifier.inventory(root, identity, copy_complete=True)
    observed = _first_run(report)
    if kind == "missing_claim":
        assert observed["claim"] == "missing"
    elif kind in {"malformed_claim", "duplicate_key"}:
        assert observed["claim"] == "invalid"
    elif kind == "large_claim":
        assert observed["claim"] == "not_checked" and "json_byte_limit" in cast(
            list[str], report["limited_by"]
        )
    elif kind == "missing_health":
        assert observed["health"] == "missing"
    elif kind == "partial":
        assert any(row["state"] == "in_progress" for row in _parts(report))
    elif kind == "count_mismatch":
        assert observed["part_count_check"] == {"state": "in_progress", "expected": 2, "seen": 1}
    else:
        assert _parts(report)[0]["state"] == "invalid"


def test_budget_exhaustion_keeps_partial_report(
    mounted: tuple[Path, verifier.MountIdentity],
) -> None:
    root, identity = mounted
    _run(root)
    report = verifier.inventory(
        root, identity, copy_complete=True, limits=verifier.ScanLimits(entries=1)
    )
    assert report["selection_complete"] is False
    assert "entry_limit" in cast(list[str], report["limited_by"])
    assert _first_run(report)["run_id"] == "fixture-run"
    assert report["history"] == {"state": "not_checked"}


def test_footer_budget_is_separate_from_part_discovery(
    mounted: tuple[Path, verifier.MountIdentity],
) -> None:
    root, identity = mounted
    run = _run(root)
    _part(run / "raw" / "part-000002.parquet")
    report = verifier.inventory(
        root, identity, copy_complete=True, limits=verifier.ScanLimits(footers=1)
    )
    assert _first_run(report)["published_parts_seen"] == 2
    assert sorted(str(row["state"]) for row in _parts(report)) == ["not_checked", "present"]
    assert report["selection_complete"] is False


def test_cooperative_deadline_is_explicit_not_a_hard_nfs_timeout(
    mounted: tuple[Path, verifier.MountIdentity],
) -> None:
    root, identity = mounted
    calls = iter([0.0, 11.0])
    report = verifier.inventory(root, identity, copy_complete=True, clock=lambda: next(calls))
    assert report["limited_by"] == ["cooperative_deadline"]
    assert report["selection_complete"] is False


def test_missing_statistics_do_not_become_zero_or_trigger_payload_fallback() -> None:
    rows = [
        ("schema_version", "1", "1", 0, 1),
        ("venue", "hyperliquid", "hyperliquid", 0, 1),
        ("received_utc_ns", None, None, None, 1),
    ]
    assert verifier._footer_statistics(rows, "hyperliquid") == {
        "state": "not_checked",
        "reason": "footer_statistics_missing",
    }


def test_nested_mount_and_symlink_roots_fail_closed(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    root, identity = mounted
    nested = root / "nested"
    nested.mkdir()
    monkeypatch.setattr(
        verifier,
        "_read_mountinfo",
        lambda: _mount_line(identity.target) + _mount_line(nested, "other:/export", mount_id="43"),
    )
    assert verifier.inventory(root, identity, copy_complete=True)["mount"] == {"state": "invalid"}
    monkeypatch.setattr(verifier, "_read_mountinfo", lambda: _mount_line(identity.target))
    link = identity.target / "link"
    link.symlink_to(tmp_path, target_is_directory=True)
    assert verifier.inventory(link, identity, copy_complete=True)["root"] == {"state": "invalid"}


def test_cli_is_stdout_only_and_refuses_unapproved_modes_before_mounts(
    mounted: tuple[Path, verifier.MountIdentity],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root, identity = mounted
    arguments = [
        "--root",
        str(root),
        "--expected-mount-target",
        str(identity.target),
        "--expected-mount-source",
        identity.source,
        "--expected-filesystem",
        identity.filesystem,
    ]
    assert verifier.main(arguments) == 1
    assert json.loads(capsys.readouterr().out)["migration_complete"] is False
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    with pytest.raises(SystemExit) as caught:
        verifier.main([*arguments, "--copy-complete"])
    assert caught.value.code == 2


def test_inventory_leaves_fixture_bytes_and_mtimes_unchanged(
    mounted: tuple[Path, verifier.MountIdentity],
) -> None:
    root, identity = mounted
    _run(root)
    before = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    verifier.inventory(root, identity, copy_complete=True)
    after = {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in root.rglob("*")
        if path.is_file()
    }
    assert after == before


@pytest.mark.parametrize(
    "lane,module",
    [
        ("data1a", "hyperliquid_raw_research"),
        ("data1b", "kraken_l3_research"),
        ("data1c", "okx_public_research"),
        ("data1d", "bitvavo_standard_research"),
        ("data1e", "bitvavo_mdpro_research"),
        ("data1f", "binance_public_research"),
    ],
)
def test_all_existing_lane_claim_producers_are_compatible(
    mounted: tuple[Path, verifier.MountIdentity], lane: str, module: str
) -> None:
    root, identity = mounted
    paths_module = importlib.import_module("hyperliquid_bot.reconstructable_paths")
    producer = importlib.import_module(f"hyperliquid_bot.{module}")
    paths = getattr(paths_module, f"{lane}_run_paths")(root / "data-capture", "producer-run")
    paths.raw_dir.mkdir(parents=True)
    parameters = {"run_id": "producer-run", "duration_seconds": 1, "paths": paths}
    if lane == "data1b":
        parameters["include_l3"] = False
    claim = getattr(producer, f"{lane}_capture_claim")(**parameters)
    paths.capture_claim_path.write_text(json.dumps(claim))
    _part(paths.raw_dir / "part-000001.parquet", venue=claim["venue"], product=claim["product"])
    report = verifier.inventory(root, identity, copy_complete=True)
    lanes = cast(list[dict[str, object]], report["captures"])
    observed = next(item for item in lanes if item["lane"] == f"data-{lane[-2:]}")
    run = cast(list[dict[str, object]], observed["runs"])[0]
    assert run["claim"] == "present"
    assert cast(list[dict[str, object]], run["parts"])[0]["state"] == "present"


def test_run_depth_and_footer_byte_limits_are_visible(
    mounted: tuple[Path, verifier.MountIdentity],
) -> None:
    root, identity = mounted
    _run(root, run_id="first")
    _run(root, run_id="second")
    limited = verifier.inventory(
        root, identity, copy_complete=True, limits=verifier.ScanLimits(runs=1)
    )
    assert "run_limit" in cast(list[str], limited["limited_by"])
    assert _first_run(limited)["claim"] == "present"
    deep = root / "hist-archives" / "one" / "two"
    deep.mkdir(parents=True)
    (deep / "sample-2025-01.zip").write_bytes(b"not-hashed")
    limited = verifier.inventory(
        root, identity, copy_complete=True, limits=verifier.ScanLimits(depth=1, footer_bytes=1)
    )
    assert {"history_depth_limit", "footer_byte_limit"} <= set(
        cast(list[str], limited["limited_by"])
    )
    assert all(item["state"] == "not_checked" for item in _parts(limited))


def test_changed_claim_is_in_progress(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identity = mounted
    run = _run(root)
    original = os.read
    calls = 0

    def read_then_change(descriptor: int, size: int) -> bytes:
        nonlocal calls
        data = original(descriptor, size)
        if calls == 0:
            (run / "capture-claim.json").write_text("{}")
        calls += 1
        return data

    monkeypatch.setattr(os, "read", read_then_change)
    report = verifier.inventory(root, identity, copy_complete=True)
    assert _first_run(report)["claim"] == "in_progress"


def test_mount_replaced_at_end_invalidates_the_report(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identity = mounted
    _run(root)
    metadata = iter(
        [_mount_line(identity.target), _mount_line(identity.target, "replacement:/other")]
    )
    monkeypatch.setattr(verifier, "_read_mountinfo", lambda: next(metadata))
    report = verifier.inventory(root, identity, copy_complete=True)
    assert report["mount"] == {"state": "invalid"}
    assert report["selection_complete"] is False


def test_empty_missing_and_incorrect_device_roots_are_not_valid_data(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identity = mounted
    missing = verifier.inventory(root / "missing", identity, copy_complete=True)
    assert missing["root"] == {"state": "missing"}
    empty = verifier.inventory(root, identity, copy_complete=True)
    assert all(
        lane["state"] == "missing" for lane in cast(list[dict[str, object]], empty["captures"])
    )
    assert empty["migration_complete"] is False
    monkeypatch.setattr(
        verifier, "_read_mountinfo", lambda: _mount_line(identity.target, device="99:99")
    )
    invalid = verifier.inventory(root, identity, copy_complete=True)
    assert invalid["mount"] == {"state": "invalid"}
    assert invalid["root"] == {"state": "not_checked"}


@pytest.mark.parametrize("contents", ["not mountinfo", "1 2 3 / /mnt rw", ""])
def test_unknown_kernel_mount_metadata_fails_closed(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch, contents: str
) -> None:
    root, identity = mounted
    monkeypatch.setattr(verifier, "_read_mountinfo", lambda: contents)
    assert verifier.inventory(root, identity, copy_complete=True)["mount"] == {"state": "invalid"}


def test_ancestor_swap_does_not_follow_symlink_or_read_outside_claim(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identity = mounted
    run = _run(root)
    outside = root.parent / "outside"
    outside.mkdir()
    outside_claim = json.loads((run / "capture-claim.json").read_text())
    outside_claim["signing"] = True
    (outside / "capture-claim.json").write_text(json.dumps(outside_claim))
    original_open = os.open
    moved = run.with_name("moved-run")
    swapped = False

    def swap_open(path: str | Path, flags: int, *, dir_fd: int | None = None) -> int:
        nonlocal swapped
        if path in {"capture-claim.json", run / "capture-claim.json"} and not swapped:
            run.rename(moved)
            run.symlink_to(outside, target_is_directory=True)
            swapped = True
        return original_open(path, flags, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_open)
    report = verifier.inventory(root, identity, copy_complete=True)
    assert swapped
    assert "invalid" in set(verifier._states(report))
    assert _first_run(report)["claim"] == "present"


def test_same_device_nested_mount_id_rejected_before_claim_read(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identity = mounted
    run = _run(root)

    def mount_id(descriptor: int) -> str:
        path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        return "999" if path.is_relative_to(run) else "42"

    monkeypatch.setattr(verifier, "_descriptor_mount_id", mount_id)
    report = verifier.inventory(root, identity, copy_complete=True)
    assert "invalid" in set(verifier._states(report))
    assert cast(list[dict[str, object]], report["captures"])[0]["state"] == "invalid"


@pytest.mark.parametrize("invalid_suffix", [False, True])
def test_short_reads_validate_the_whole_json_document(
    mounted: tuple[Path, verifier.MountIdentity],
    monkeypatch: pytest.MonkeyPatch,
    invalid_suffix: bool,
) -> None:
    root, identity = mounted
    run = _run(root)
    claim_path = run / "capture-claim.json"
    # In the suffix case the first read is itself valid JSON; trailing bytes
    # still belong to the stable file. The valid case splits JSON mid-document.
    content = claim_path.read_bytes()
    if invalid_suffix:
        claim_path.write_bytes(content + b"not-valid-json-trailing-bytes")
    original_read = os.read
    chunks = 0

    def short_read(descriptor: int, size: int) -> bytes:
        nonlocal chunks
        chunks += 1
        if Path(os.readlink(f"/proc/self/fd/{descriptor}")) == claim_path:
            return original_read(
                descriptor, min(size, len(content) if invalid_suffix else len(content) // 2)
            )
        return original_read(descriptor, size)

    monkeypatch.setattr(os, "read", short_read)
    report = verifier.inventory(root, identity, copy_complete=True)
    assert _first_run(report)["claim"] == ("invalid" if invalid_suffix else "present")
    assert chunks >= 3  # claim content, claim EOF/suffix, plus health


def test_premature_eof_cannot_accept_a_valid_json_prefix(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identity = mounted
    path = root / "claim.json"
    prefix = b'{"signing":false}'
    path.write_bytes(prefix + b"not-valid-json-trailing-bytes")
    original_read = os.read
    first = True

    def truncated_read(descriptor: int, size: int) -> bytes:
        nonlocal first
        if first:
            first = False
            return original_read(descriptor, len(prefix))
        return b""

    monkeypatch.setattr(os, "read", truncated_read)
    observed, _ = verifier._bound_mount(root, identity)
    scan = verifier._Scan(observed, verifier.ScanLimits(), time.monotonic)
    state, document = verifier._json_file(scan, path)
    assert state == "in_progress"
    assert document is None


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("nfs_id,autofs_id", [("42", "900"), ("900", "42")])
def test_visible_descriptor_selects_active_automount_independent_of_order_and_id_size(
    mounted: tuple[Path, verifier.MountIdentity],
    monkeypatch: pytest.MonkeyPatch,
    reverse: bool,
    nfs_id: str,
    autofs_id: str,
) -> None:
    root, identity = mounted
    rows = [
        _mount_line(
            identity.target, "systemd-1", device="0:38", mount_id=autofs_id, filesystem="autofs"
        ),
        _mount_line(identity.target, mount_id=nfs_id),
    ]
    monkeypatch.setattr(
        verifier, "_read_mountinfo", lambda: "".join(reversed(rows) if reverse else rows)
    )
    monkeypatch.setattr(verifier, "_descriptor_mount_id", lambda _descriptor: nfs_id)
    report = verifier.inventory(root, identity, copy_complete=True)
    assert report["mount"] == {
        "state": "present",
        "target": str(identity.target),
        "source": identity.source,
        "filesystem": identity.filesystem,
    }
    assert report["root"] == {"state": "present"}
    assert report["migration_complete"] is False


@pytest.mark.parametrize(
    "visible", ["autofs", "unknown", "source", "filesystem", "device", "duplicate"]
)
def test_hidden_expected_entry_cannot_authorize_another_visible_mount(
    mounted: tuple[Path, verifier.MountIdentity],
    monkeypatch: pytest.MonkeyPatch,
    visible: str,
) -> None:
    root, identity = mounted
    rows = _mount_line(identity.target)
    mount_id = "43"
    if visible == "autofs":
        rows += _mount_line(identity.target, "systemd-1", mount_id=mount_id, filesystem="autofs")
    elif visible == "source":
        rows += _mount_line(identity.target, "other:/export", mount_id=mount_id)
    elif visible == "filesystem":
        rows += _mount_line(identity.target, mount_id=mount_id, filesystem="ext4")
    elif visible == "device":
        rows += _mount_line(identity.target, mount_id=mount_id, device="99:99")
    elif visible == "duplicate":
        mount_id = "42"
        rows += _mount_line(identity.target)
    monkeypatch.setattr(verifier, "_read_mountinfo", lambda: rows)
    monkeypatch.setattr(verifier, "_descriptor_mount_id", lambda _descriptor: mount_id)

    def forbidden(_self: object, _path: Path) -> os.stat_result:
        pytest.fail("A hidden expected entry cannot authorize data-root inspection")

    monkeypatch.setattr(verifier._Scan, "safe", forbidden)
    report = verifier.inventory(root, identity, copy_complete=True)
    assert report["mount"] == {"state": "invalid"}
    assert report["root"] == {"state": "not_checked"}


def test_deeper_covering_mount_rejected_before_mount_target_lookup(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identity = mounted
    monkeypatch.setattr(
        verifier,
        "_read_mountinfo",
        lambda: _mount_line(identity.target) + _mount_line(root, mount_id="43"),
    )

    def forbidden(_target: Path) -> tuple[str, str]:
        pytest.fail("Deeper covering mount must be rejected using kernel metadata alone")

    monkeypatch.setattr(verifier, "_mount_target_identity", forbidden)
    report = verifier.inventory(root, identity, copy_complete=True)
    assert report["mount"] == {"state": "invalid"}
    assert report["root"] == {"state": "not_checked"}


@pytest.mark.parametrize("component", ["anchor", "export"])
def test_mount_target_ancestor_or_leaf_swap_cannot_follow_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    component: str,
) -> None:
    target = tmp_path / "anchor" / "export"
    root = target / "copy"
    root.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "export" / "copy").mkdir(parents=True)
    identity = verifier.MountIdentity(target, "test-nas:/project", "nfs4")
    metadata = _mount_line(target)
    monkeypatch.setattr(verifier, "_read_mountinfo", lambda: metadata)
    monkeypatch.setattr(verifier, "_descriptor_mount_id", lambda _descriptor: "42")
    original_open = os.open
    swapped = False

    def swap_open(path: str | Path, flags: int, *, dir_fd: int | None = None) -> int:
        nonlocal swapped
        if path == component and not swapped:
            selected = target if component == "export" else target.parent
            selected.rename(selected.with_name(f"{component}-moved"))
            selected.symlink_to(outside, target_is_directory=True)
            swapped = True
        assert flags & os.O_NOFOLLOW
        assert flags & os.O_DIRECTORY
        assert flags & os.O_PATH
        if path != "/":
            assert dir_fd is not None
        return original_open(path, flags, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", swap_open)
    report = verifier.inventory(root, identity, copy_complete=True)
    assert swapped
    assert report["mount"] == {"state": "invalid"}
    assert report["root"] == {"state": "not_checked"}


@pytest.mark.parametrize("failure", ["open", "fstat", "fdinfo"])
def test_mount_target_descriptors_close_on_lookup_and_metadata_failures(
    mounted: tuple[Path, verifier.MountIdentity],
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    root, identity = mounted
    opened: set[int] = set()
    original_open, original_fstat = os.open, os.fstat

    def track_open(path: str | Path, flags: int, *, dir_fd: int | None = None) -> int:
        if path == identity.target.name and failure == "open":
            raise PermissionError("synthetic target lookup failure")
        descriptor = original_open(path, flags, dir_fd=dir_fd)
        opened.add(descriptor)
        return descriptor

    def fail_fstat(_descriptor: int) -> os.stat_result:
        raise OSError("synthetic descriptor metadata failure")

    def fail_fdinfo(_descriptor: int) -> str:
        raise verifier._Unsafe("descriptor_mount_identity_unavailable")

    monkeypatch.setattr(os, "open", track_open)
    if failure == "fstat":
        monkeypatch.setattr(os, "fstat", fail_fstat)
    elif failure == "fdinfo":
        monkeypatch.setattr(verifier, "_descriptor_mount_id", fail_fdinfo)
    report = verifier.inventory(root, identity, copy_complete=True)
    assert report["mount"] == {"state": "invalid"}
    assert opened
    for descriptor in opened:
        with pytest.raises(OSError):
            original_fstat(descriptor)


def test_same_source_automount_replacement_is_detected_at_final_check(
    mounted: tuple[Path, verifier.MountIdentity], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, identity = mounted
    generation = 41

    def metadata() -> str:
        nonlocal generation
        generation += 1
        return _mount_line(identity.target, mount_id=str(generation)) + _mount_line(
            identity.target,
            "systemd-1",
            mount_id="36",
            filesystem="autofs",
        )

    monkeypatch.setattr(verifier, "_read_mountinfo", metadata)
    monkeypatch.setattr(verifier, "_descriptor_mount_id", lambda _descriptor: str(generation))
    report = verifier.inventory(root, identity, copy_complete=True)
    assert generation == 43
    assert report["mount"] == report["root"] == {"state": "invalid"}
    assert report["selection_complete"] is False
