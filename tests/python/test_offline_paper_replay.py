"""Completed synthetic parts only; never inspect an operator archive or mount."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import duckdb
import pytest

from hyperliquid_bot.migration_verify import MountIdentity, _descriptor_mount_id
from hyperliquid_bot.paper_engine import offline_inputs as inputs
from hyperliquid_bot.paper_engine import offline_replay as cli
from hyperliquid_bot.paper_engine.engine import PaperEngine, PaperEngineConfig
from hyperliquid_bot.paper_engine.errors import PaperTapeError
from hyperliquid_bot.paper_engine.replay import ReplayResources, load_hyperliquid_parquet_tape
from hyperliquid_bot.paper_engine.strategy import NonProductionReferenceStrategy
from hyperliquid_bot.parquet_research import _CREATE_SEGMENT_TABLE

COMMIT = "a" * 40
RECEIVED = 1_784_000_000_000_000_000


def raw_row(ordinal: int, channel: str = "bbo", body: bytes | None = None) -> tuple[object, ...]:
    if body is None:
        body = json.dumps(
            {
                "channel": "bbo",
                "data": {
                    "coin": "BTC",
                    "time": RECEIVED // 1_000_000 + ordinal * 1000,
                    "bbo": [{"px": "100000", "sz": "1"}, {"px": "100001", "sz": "1"}],
                },
            }
        ).encode()
    return (
        1,
        "hyperliquid",
        "BTC-PERP",
        channel,
        "fixture-session",
        ordinal,
        RECEIVED + ordinal * 1_000_000_000,
        ordinal * 1_000_000_000,
        "inbound",
        "text",
        "utf-8",
        body,
        hashlib.sha256(body).hexdigest(),
    )


def write_part(path: Path, rows: list[tuple[object, ...]] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(":memory:", config={"threads": 1})
    try:
        connection.execute(_CREATE_SEGMENT_TABLE)
        for row in rows if rows is not None else [raw_row(index) for index in range(1, 5)]:
            connection.execute(
                "INSERT INTO raw_segment VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row
            )
        connection.execute("COPY raw_segment TO ? (FORMAT PARQUET, COMPRESSION ZSTD)", [str(path)])
    finally:
        connection.close()


@pytest.fixture
def replay_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> cli.ReplayRequest:
    root = tmp_path / "inputs"
    store = tmp_path / "store"
    store.mkdir()
    write_part(root / "part-000001.parquet")
    # Staging must be local/private and is retained on failure; keep these
    # synthetic remnants within pytest's own disposable directory.
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(scratch))
    return cli.ReplayRequest(
        input_root=root,
        parts=("part-000001.parquet",),
        store_root=store,
        run_id="offline-fixture",
        source_commit=COMMIT,
        source_provenance="operator-asserted",
        input_mode="local-fixture",
        inputs_complete=True,
    )


@pytest.mark.parametrize("mode", ["flat", "toy"])
def test_replay_matches_direct_engine_with_identical_defaults(
    replay_request: cli.ReplayRequest,
    tmp_path: Path,
    mode: str,
) -> None:
    replay_request = replace(replay_request, strategy=mode)
    result = cli.run_replay(replay_request)
    assert result["status"] == "COMPLETE"
    loaded = load_hyperliquid_parquet_tape([replay_request.input_root / replay_request.parts[0]])
    direct = PaperEngine(
        store_root=tmp_path / "direct",
        run_id=replay_request.run_id,
        created_at_utc=datetime.fromtimestamp(loaded[0].received_utc_ns // 1_000_000_000, tz=UTC),
        config=PaperEngineConfig(
            trading_mode="PAPER",
            source_commit=f"operator-asserted:{COMMIT}",
            image_digest=cli.LOCAL_IMAGE,
        ),
        strategy=NonProductionReferenceStrategy(mode=mode),
    )
    direct.run_events(loaded)
    direct.close()
    for name in cli.ARTIFACTS:
        assert (replay_request.store_root / replay_request.run_id / name).read_bytes() == (
            tmp_path / "direct" / replay_request.run_id / name
        ).read_bytes()
    claim = json.loads(
        (replay_request.store_root / replay_request.run_id / "replay-claim.json").read_text()
    )
    assert claim["funding"] == "UNSETTLED"
    assert claim["source"]["mode"] == "operator-asserted"
    assert claim["production_eligible"] is False
    state = json.loads(
        (replay_request.store_root / replay_request.run_id / "state.json").read_text()
    )
    assert state["fill_count"] == (0 if mode == "flat" else 2)


def test_repeat_is_deterministic_with_same_run_id_in_separate_store(
    replay_request: cli.ReplayRequest,
    tmp_path: Path,
) -> None:
    first = cli.run_replay(replay_request)
    second_store = tmp_path / "second-store"
    second_store.mkdir()
    second = cli.run_replay(replace(replay_request, store_root=second_store))
    assert first["business_fingerprint_sha256"] == second["business_fingerprint_sha256"]
    for name in (*cli.ARTIFACTS, "replay-claim.json"):
        assert (replay_request.store_root / replay_request.run_id / name).read_bytes() == (
            second_store / replay_request.run_id / name
        ).read_bytes()


def test_nonpaper_fails_before_any_input_output_or_source_probe(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setattr(
        cli, "source_provenance", lambda *_args: pytest.fail("source probe before PAPER gate")
    )
    monkeypatch.setattr(
        os, "open", lambda *_args, **_kwargs: pytest.fail("filesystem probe before PAPER gate")
    )
    with pytest.raises(ValueError, match="PAPER"):
        cli.run_replay(replay_request)


@pytest.mark.parametrize(
    "part",
    [
        "../part.parquet",
        "/part.parquet",
        ".part.parquet",
        "part.partial.parquet",
        "*.parquet",
        "parts",
        "part.parquet/../other.parquet",
    ],
)
def test_invalid_explicit_part_is_rejected_before_staging(
    replay_request: cli.ReplayRequest, part: str
) -> None:
    with pytest.raises(cli.OfflineReplayError) as failure:
        cli.run_replay(replace(replay_request, parts=(part,)))
    assert failure.value.staging is None
    assert not list(replay_request.store_root.iterdir())


@pytest.mark.parametrize(
    "bound", ["physical_bytes", "uncompressed_bytes", "payload_bytes", "rows", "events"]
)
def test_resource_preflight_fails_before_eager_loader(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
    bound: str,
) -> None:
    monkeypatch.setattr(
        cli,
        "load_hyperliquid_parquet_tape",
        lambda *_args, **_kwargs: pytest.fail("eager loader ran before bounds"),
    )
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replace(replay_request, limits=replace(replay_request.limits, **{bound: 1})))
    assert not list(replay_request.store_root.iterdir())


@pytest.mark.parametrize(
    "body",
    [
        b'{"channel":"trades","data":[1,2,3],"data":[1]}',
        b'{"channel":"trades","data":[{"coin":"BTC","coin":"ETH"}]}',
        b'{"channel":"trades","data":[NaN]}',
        b'{"channel":"trades","data":[Infinity]}',
    ],
)
def test_ambiguous_json_cannot_understate_expansion_before_eager_loader(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
) -> None:
    write_part(replay_request.input_root / "part-000002.parquet", [raw_row(1, "trades", body)])
    monkeypatch.setattr(
        cli,
        "load_hyperliquid_parquet_tape",
        lambda *_args, **_kwargs: pytest.fail("ambiguous JSON reached eager reader"),
    )
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replace(replay_request, parts=("part-000002.parquet",)))


def test_raw_trade_array_is_counted_before_dedup(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trade = {
        "coin": "BTC",
        "side": "B",
        "px": "100000",
        "sz": "0.01",
        "time": 1784000000000,
        "tid": 1,
    }
    body = json.dumps({"channel": "trades", "data": [trade] * 3}).encode()
    write_part(replay_request.input_root / "trades.parquet", [raw_row(1, "trades", body)])
    monkeypatch.setattr(
        cli,
        "load_hyperliquid_parquet_tape",
        lambda *_args, **_kwargs: pytest.fail("pre-dedup bound bypassed"),
    )
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(
            replace(
                replay_request,
                parts=("trades.parquet",),
                limits=replace(replay_request.limits, events=2),
            )
        )


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "copy", "same-path"])
def test_symlinks_and_duplicate_input_identity_rejected(
    replay_request: cli.ReplayRequest, kind: str
) -> None:
    original = replay_request.input_root / replay_request.parts[0]
    other = replay_request.input_root / "other.parquet"
    if kind == "symlink":
        other.symlink_to(original)
    elif kind == "hardlink":
        os.link(original, other)
    elif kind == "copy":
        other.write_bytes(original.read_bytes())
    else:
        other = original
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replace(replay_request, parts=(replay_request.parts[0], other.name)))
    assert not list(replay_request.store_root.iterdir())


def test_mutated_original_prevents_publication_and_retains_snapshots(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = load_hyperliquid_parquet_tape

    def load_then_mutate(*args: Any, **kwargs: Any) -> Any:
        events = original(*args, **kwargs)
        with (replay_request.input_root / replay_request.parts[0]).open("ab") as handle:
            handle.write(b"changed")
        return events

    monkeypatch.setattr(cli, "load_hyperliquid_parquet_tape", load_then_mutate)
    with pytest.raises(cli.OfflineReplayError) as failure:
        cli.run_replay(replay_request)
    assert not list(replay_request.store_root.iterdir())
    assert failure.value.staging is not None
    assert (failure.value.staging / "input-claim.json").is_file()
    assert (failure.value.staging / "failure.json").is_file()


@pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
def test_existing_run_id_is_never_replaced_even_when_empty(
    replay_request: cli.ReplayRequest, kind: str
) -> None:
    path = replay_request.store_root / replay_request.run_id
    if kind == "directory":
        path.mkdir()
    elif kind == "file":
        path.write_text("untouched")
    else:
        path.symlink_to(replay_request.input_root, target_is_directory=True)
    before = path.lstat()
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replay_request)
    assert path.lstat() == before


def test_failed_publication_has_no_health_and_preserves_private_evidence(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cli._write_exclusive

    def fail(descriptor: int, name: str, value: bytes) -> None:
        if name == ".health.publishing":
            raise OSError("synthetic failed publication")
        original(descriptor, name, value)

    monkeypatch.setattr(cli, "_write_exclusive", fail)
    with pytest.raises(cli.OfflineReplayError) as failure:
        cli.run_replay(replay_request)
    assert (replay_request.store_root / replay_request.run_id).is_dir()
    assert not (replay_request.store_root / replay_request.run_id / "health.json").exists()
    assert failure.value.staging is not None
    assert (failure.value.staging / "replay-claim.json").is_file()
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replay_request)


@pytest.mark.parametrize("limit", ["JSON_BYTE_LIMIT", "LEDGER_BYTE_LIMIT", "LEDGER_RECORD_LIMIT"])
def test_cockpit_limits_never_publish_truncated_run(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
    limit: str,
) -> None:
    monkeypatch.setattr(cli, limit, 1)
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replace(replay_request, strategy="toy"))
    assert not list(replay_request.store_root.iterdir())


def test_remote_identity_never_falls_back_to_local_fixture(
    replay_request: cli.ReplayRequest,
) -> None:
    remote = replace(
        replay_request,
        input_mode="remote",
        expected_mount=MountIdentity(replay_request.input_root, "test-nas:/export", "nfs4"),
    )
    with pytest.raises(cli.OfflineReplayError) as failure:
        cli.run_replay(remote)
    assert failure.value.staging is None


def test_resource_profile_is_opt_in_and_strict() -> None:
    for value in (True, 0, 2, 1.0):
        with pytest.raises(ValueError):
            ReplayResources(threads=value)  # type: ignore[arg-type]
    assert ReplayResources().connection_config()["max_temp_directory_size"] == "0B"


def test_verified_source_rejects_wrong_commit_and_dirty_tree() -> None:
    with pytest.raises(Exception, match="actual HEAD"):
        cli.source_provenance("0" * 40, "verified-head")


def test_output_inside_input_root_is_rejected(replay_request: cli.ReplayRequest) -> None:
    with pytest.raises(cli.OfflineReplayError) as failure:
        cli.run_replay(replace(replay_request, store_root=replay_request.input_root / "output"))
    assert failure.value.staging is None
    assert not (replay_request.input_root / "output").exists()


@pytest.mark.parametrize("exponent", ["1e-100", "1e100", "1e-1000000000", "1e1000000000"])
def test_extreme_decimal_is_rejected_before_engine_fixed_point_formatting(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
    exponent: str,
) -> None:
    body = json.dumps(
        {"channel": "activeAssetCtx", "data": {"coin": "BTC", "ctx": {"markPx": exponent}}}
    ).encode()
    write_part(replay_request.input_root / "mark.parquet", [raw_row(1, "activeAssetCtx", body)])
    monkeypatch.setattr(
        cli, "PaperEngine", lambda **_kwargs: pytest.fail("unbounded decimal reached engine")
    )
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replace(replay_request, parts=("mark.parquet",)))
    assert not list(replay_request.store_root.iterdir())


def test_derived_mid_outside_reader_precision_is_not_published(
    replay_request: cli.ReplayRequest,
) -> None:
    body = json.dumps(
        {
            "channel": "bbo",
            "data": {
                "coin": "BTC",
                "time": 1784000000000,
                "bbo": [{"px": "1e-40", "sz": "1"}, {"px": "2e-40", "sz": "1"}],
            },
        }
    ).encode()
    write_part(replay_request.input_root / "mid.parquet", [raw_row(1, "bbo", body)])
    with pytest.raises(cli.OfflineReplayError) as failure:
        cli.run_replay(replace(replay_request, parts=("mid.parquet",)))
    assert failure.value.staging is not None
    # Existing engine precision is untouched; only the CLI publication refuses
    # its valid Decimal result that the existing TS scalar contract cannot read.
    health = json.loads(
        (failure.value.staging / "runs" / replay_request.run_id / "health.json").read_text()
    )
    assert health["mark_price"].endswith("15")
    assert not list(replay_request.store_root.iterdir())


@pytest.mark.parametrize("location", ["store", "scratch"])
def test_same_device_different_mount_alias_fails_before_snapshot(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
    location: str,
) -> None:
    original = inputs.descriptor_identity

    def alias(descriptor: int) -> tuple[int, int, str]:
        identity = original(descriptor)
        path = Path(os.readlink(f"/proc/self/fd/{descriptor}"))
        if path.name == location:
            return identity[0], identity[1], "synthetic-other-bind-mount"
        return identity

    monkeypatch.setattr(cli, "descriptor_identity", alias)
    monkeypatch.setattr(
        cli, "snapshot_parts", lambda *_args: pytest.fail("alias reached source bytes")
    )
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replay_request)
    assert not list(replay_request.store_root.iterdir())


def test_remote_binding_is_rechecked_without_local_fallback(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = inputs.descriptor_mount
    with inputs.directory(replay_request.input_root) as fd:
        observed = actual(fd)
    expected = MountIdentity(
        replay_request.input_root, "fixture-nas:/export", "nfs4", observed.device, observed.mount_id
    )
    calls: list[MountIdentity] = []

    def bind(_root: Path, selected: MountIdentity) -> MountIdentity:
        calls.append(selected)
        return expected

    monkeypatch.setattr(cli, "bind_remote", bind)
    monkeypatch.setattr(cli, "descriptor_mount", lambda _fd: expected)
    result = cli.run_replay(replace(replay_request, input_mode="remote", expected_mount=expected))
    assert result["status"] == "COMPLETE"
    assert calls == [expected, expected]
    claim = json.loads(
        (replay_request.store_root / replay_request.run_id / "replay-claim.json").read_text()
    )
    assert claim["input_mode"] == "remote"
    assert claim["input_mount"]["source"] == "fixture-nas:/export"


def test_input_change_during_copy_is_not_a_completed_snapshot(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = os.read
    source = replay_request.input_root / replay_request.parts[0]
    changed = False

    def read_then_change(descriptor: int, size: int) -> bytes:
        nonlocal changed
        data = original(descriptor, size)
        if not changed and Path(os.readlink(f"/proc/self/fd/{descriptor}")) == source:
            with source.open("ab") as handle:
                handle.write(b"changed")
            changed = True
        return data

    monkeypatch.setattr(os, "read", read_then_change)
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replay_request)
    assert changed
    assert not list(replay_request.store_root.iterdir())


@pytest.mark.parametrize("kind", ["ancestor-link", "fifo", "output-link", "input-root-link"])
def test_nonregular_and_symlink_paths_cannot_redirect_io(
    replay_request: cli.ReplayRequest,
    kind: str,
) -> None:
    selected = replay_request
    if kind == "ancestor-link":
        (replay_request.input_root / "linked").symlink_to(
            replay_request.input_root, target_is_directory=True
        )
        selected = replace(selected, parts=(f"linked/{selected.parts[0]}",))
    elif kind == "fifo":
        os.mkfifo(replay_request.input_root / "fifo.parquet")
        selected = replace(selected, parts=("fifo.parquet",))
    else:
        linked = replay_request.store_root.parent / "linked-root"
        linked.symlink_to(
            replay_request.store_root if kind == "output-link" else replay_request.input_root,
            target_is_directory=True,
        )
        selected = (
            replace(selected, store_root=linked)
            if kind == "output-link"
            else replace(selected, input_root=linked)
        )
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(selected)
    assert not list(replay_request.store_root.iterdir())


@pytest.mark.parametrize(
    "kind", ["attestation", "missing-mount", "fixture-with-mount", "file-count", "bad-source"]
)
def test_invalid_request_rejected_before_staging(
    replay_request: cli.ReplayRequest,
    kind: str,
) -> None:
    if kind == "attestation":
        replay_request = replace(replay_request, inputs_complete=False)
    elif kind == "missing-mount":
        replay_request = replace(replay_request, input_mode="remote")
    elif kind == "fixture-with-mount":
        replay_request = replace(
            replay_request,
            expected_mount=MountIdentity(replay_request.input_root, "fixture:/data", "nfs4"),
        )
    elif kind == "file-count":
        replay_request = replace(replay_request, parts=(replay_request.parts[0],) * 5)
    else:
        replay_request = replace(replay_request, source_commit="unknown")
    with pytest.raises(cli.OfflineReplayError) as failure:
        cli.run_replay(replay_request)
    assert failure.value.staging is None


@pytest.mark.parametrize(
    "field,value",
    [("rows", True), ("rows", 5001), ("events", 2001), ("files", 5), ("physical_bytes", -1)],
)
def test_limits_are_strict_not_coerced(field: str, value: int) -> None:
    with pytest.raises(ValueError):
        inputs.ReplayLimits(**{field: value})


def test_one_python_module_command_produces_native_artifacts(
    replay_request: cli.ReplayRequest,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "hyperliquid_bot.paper_engine.offline_replay",
            "--input-root",
            str(replay_request.input_root),
            "--part",
            replay_request.parts[0],
            "--input-mode",
            "local-fixture",
            "--store-root",
            str(replay_request.store_root),
            "--run-id",
            replay_request.run_id,
            "--source-commit",
            COMMIT,
            "--source-provenance",
            "operator-asserted",
            "--inputs-complete",
        ],
        env={
            "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src"),
            "TRADING_MODE": "PAPER",
            "TMPDIR": str(replay_request.store_root.parent / "scratch"),
        },
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["status"] == "COMPLETE"
    for name in (*cli.ARTIFACTS, "replay-claim.json"):
        assert (replay_request.store_root / replay_request.run_id / name).is_file()


@pytest.mark.parametrize("bad", ["hash", "product", "raw-identity", "coin", "parquet"])
def test_malformed_or_conflicting_input_cannot_publish(
    replay_request: cli.ReplayRequest,
    bad: str,
) -> None:
    path = replay_request.input_root / "bad.parquet"
    if bad == "parquet":
        path.write_bytes(b"not a parquet file")
    else:
        row = list(raw_row(1))
        if bad == "hash":
            row[12] = "0" * 64
        elif bad == "product":
            row[2] = "ETH-PERP"
        elif bad == "coin":
            body = json.dumps(
                {"channel": "bbo", "data": {"coin": "ETH", "time": 1784000000000, "bbo": []}}
            ).encode()
            row = list(raw_row(1, "bbo", body))
        write_part(path, [tuple(row)] * (2 if bad == "raw-identity" else 1))
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replace(replay_request, parts=(path.name,)))
    assert not list(replay_request.store_root.iterdir())


def test_reserved_output_mount_is_checked_before_writing(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = inputs.descriptor_identity

    def replaced(descriptor: int) -> tuple[int, int, str]:
        actual = original(descriptor)
        if (
            Path(os.readlink(f"/proc/self/fd/{descriptor}"))
            == replay_request.store_root / replay_request.run_id
        ):
            return actual[0], actual[1], "synthetic-new-output-mount"
        return actual

    monkeypatch.setattr(cli, "descriptor_identity", replaced)
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replay_request)
    assert list((replay_request.store_root / replay_request.run_id).iterdir()) == []


def test_source_binding_change_after_snapshot_prevents_publication(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = cli.source_provenance
    calls = 0

    def changed(commit: str, mode: str) -> dict[str, object]:
        nonlocal calls
        value = original(commit, mode)
        calls += 1
        return value if calls == 1 else {**value, "code_binding_sha256": "changed"}

    monkeypatch.setattr(cli, "source_provenance", changed)
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replay_request)
    assert calls == 2
    assert not list(replay_request.store_root.iterdir())


@pytest.mark.parametrize("root_name", ["input_root", "store_root"])
@pytest.mark.parametrize("phase", ["after-staging", "before-publication"])
def test_root_path_swap_is_detected_before_publication(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
    root_name: str,
    phase: str,
) -> None:
    selected = getattr(replay_request, root_name)
    outside = replay_request.store_root.parent / "outside"
    outside.mkdir()

    def swap() -> None:
        selected.rename(selected.with_name(selected.name + "-moved"))
        selected.symlink_to(outside, target_is_directory=True)

    if phase == "after-staging":
        original_stage = cli._new_staging

        def stage_then_swap(path: Path, identity: tuple[int, int, str]) -> Path:
            result = original_stage(path, identity)
            swap()
            return result

        monkeypatch.setattr(cli, "_new_staging", stage_then_swap)
        monkeypatch.setattr(
            cli, "snapshot_parts", lambda *_args: pytest.fail("changed root reached input bytes")
        )
    else:
        original_loader = load_hyperliquid_parquet_tape

        def load_then_swap(*args: Any, **kwargs: Any) -> Any:
            result = original_loader(*args, **kwargs)
            swap()
            return result

        monkeypatch.setattr(cli, "load_hyperliquid_parquet_tape", load_then_swap)
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replay_request)
    assert not list(outside.iterdir())


def test_nested_input_mount_rejected_before_payload_read(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = _descriptor_mount_id
    part = replay_request.input_root / replay_request.parts[0]

    def nested(descriptor: int) -> str:
        return (
            "synthetic-nested"
            if Path(os.readlink(f"/proc/self/fd/{descriptor}")) == part
            else original(descriptor)
        )

    monkeypatch.setattr(inputs, "_descriptor_mount_id", nested)
    monkeypatch.setattr(os, "read", lambda *_args: pytest.fail("nested input reached payload read"))
    with pytest.raises(cli.OfflineReplayError):
        cli.run_replay(replay_request)


def test_empty_trade_response_does_not_invent_an_event(replay_request: cli.ReplayRequest) -> None:
    body = b'{"channel":"trades","data":[]}'
    write_part(
        replay_request.input_root / "empty-trades.parquet", [raw_row(1, "trades", body), raw_row(2)]
    )
    result = cli.run_replay(replace(replay_request, parts=("empty-trades.parquet",)))
    counts = cast(dict[str, int], result["counts"])
    assert counts["raw_rows"] == 2
    assert counts["expanded_event_upper_bound"] == counts["normalized_events"] == 1


def test_each_selected_file_requires_exact_schema_before_eager_loader(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extra = replay_request.input_root / "extra.parquet"
    connection = duckdb.connect(":memory:", config={"threads": 1})
    try:
        connection.execute(
            "CREATE TABLE extra AS SELECT *, 1 AS unsupported FROM read_parquet(?)",
            [str(replay_request.input_root / replay_request.parts[0])],
        )
        connection.execute("COPY extra TO ? (FORMAT PARQUET)", [str(extra)])
    finally:
        connection.close()
    monkeypatch.setattr(
        cli,
        "load_hyperliquid_parquet_tape",
        lambda *_args, **_kwargs: pytest.fail("second schema escaped preflight"),
    )
    with pytest.raises(cli.OfflineReplayError) as failure:
        cli.run_replay(replace(replay_request, parts=(*replay_request.parts, extra.name)))
    assert "Every part" in failure.value.reason


def test_untrusted_parser_error_values_are_not_public_diagnostics(
    replay_request: cli.ReplayRequest,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(*_args: Any, **_kwargs: Any) -> Any:
        raise PaperTapeError("synthetic-sensitive-payload-value")

    monkeypatch.setattr(cli, "load_hyperliquid_parquet_tape", fail)
    with pytest.raises(cli.OfflineReplayError) as failure:
        cli.run_replay(replay_request)
    assert "synthetic-sensitive" not in failure.value.reason
    assert failure.value.staging is not None
    assert "synthetic-sensitive" not in (failure.value.staging / "failure.json").read_text()
