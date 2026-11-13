from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from research import micro_response_cli as cli
from research.btc_flow_io import PrivateOutput
from research.micro_flow import MicroEvent
from research.micro_response import POLICIES
from research.micro_response_io import Observation
from research.micro_response_stats import Summary


def args(tmp_path: Path) -> list[str]:
    return [
        "--input",
        str(tmp_path / "index.json"),
        "--output",
        str(tmp_path / "run"),
        "--source-commit",
        "a" * 40,
        "--expected-plan-sha256",
        "b" * 64,
    ]


@pytest.mark.parametrize("mode", ["LIVE", "TESTNET", "SHADOW", "paper ", "PAPER\t"])
def test_nonpaper_refuses_before_source_identity_or_data_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    mode: str,
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setattr(cli, "identity", lambda commit: pytest.fail("identity reached"))
    with pytest.raises(Exception, match="PAPER"):
        cli.main(args(tmp_path))
    assert not (tmp_path / "run").exists()


def decision() -> dict[str, Any]:
    return {
        "kind": "decision",
        "group": "development",
        "capture": "x",
        "directions": {p[0]: 0 for p in POLICIES},
        "reasons": {p[0]: "no_signal" for p in POLICIES},
    }


def test_chunked_records_are_create_only_private_and_reconstructable(tmp_path: Path) -> None:
    destination = tmp_path / "run"
    summary = Summary()
    with PrivateOutput(destination) as output:
        writer = cli.Records(output, summary)
        writer.emit(decision())
        writer.flush()
        assert writer.rows["decision"] == 1
        raw = (destination / writer.chunks[0]["path"]).read_bytes()
        assert json.loads(gzip.decompress(raw)) == decision()
        assert (destination.stat().st_mode & 0o777) == 0o700
        assert ((destination / writer.chunks[0]["path"]).stat().st_mode & 0o777) == 0o600
    with pytest.raises(FileExistsError):
        PrivateOutput(destination)


@pytest.mark.parametrize("field", ["MAX_ROWS", "MAX_OUTPUT_BYTES"])
def test_output_bound_is_fatal_instead_of_discarding_samples(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    field: str,
) -> None:
    monkeypatch.setattr(cli, field, 0)
    with PrivateOutput(tmp_path / "run") as output:
        with pytest.raises(Exception, match="output count/byte bound"):
            cli.Records(output, Summary()).emit(decision())


def test_wrong_source_sha_fails_without_source_or_market_reads() -> None:
    with pytest.raises(Exception, match="actual clean reviewed source HEAD"):
        cli.identity("0" * 40)


def test_missing_commit_is_not_accepted_as_an_identity() -> None:
    with pytest.raises(Exception, match="actual Git SHA"):
        cli.identity("main")


def test_synthetic_end_to_end_keeps_every_clock_and_emits_no_strategy_admission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    second = 1_000_000_000
    utc = 1_788_221_100 * second
    data = []
    from decimal import Decimal

    for index in range(61):
        instant = index * second
        event = MicroEvent(
            instant,
            utc + instant,
            (utc + instant) // 1_000_000,
            "book",
            "s",
            Decimal(10000),
            Decimal(10001),
            Decimal(2),
            Decimal(1),
        )
        data.append(
            Observation(
                1,
                instant,
                utc + instant,
                "bbo",
                f"s:{index}",
                "a" * 64,
                instant,
                event,
                level_counts=(1, 1),
            )
        )
    data.append(
        Observation(
            1,
            60 * second,
            utc + 60 * second,
            "bbo",
            "end",
            "a" * 64,
            60 * second,
            end_reason="closed_capture_end",
        )
    )
    metadata = {"id": 1, "start_mono_ns": 0, "end_mono_ns": 60 * second, "start_utc_ns": utc}
    monkeypatch.setattr(cli, "funding_times", lambda body: (0, 10**15))
    monkeypatch.setattr(
        cli,
        "qualify",
        lambda capture: {
            "capture": "fixture",
            "group": "development",
            "windows": [metadata],
            "observation_ns": 60 * second,
        },
    )
    monkeypatch.setattr(cli, "verify_coverage", lambda coverage: "synthetic-only")
    monkeypatch.setattr(cli, "observations", lambda capture: iter(data))
    with PrivateOutput(tmp_path / "run") as output:
        result = cli.run({"captures": [{"name": "fixture", "group": "development"}]}, output)
    assert result["selector"] is None and result["promotion_decision"] == "forbidden"
    assert result["new_alpha_configurations"] == 0
    assert result["capacity_certified"] is False and result["funding_cash_certified"] is False
    assert result["record_counts"]["decision"] == 6
    assert all(c["counts"]["scheduled"] == 6 for c in result["cells"])
    for chunk in result["record_chunks"]:
        raw = (tmp_path / "run" / chunk["path"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == chunk["sha256"]
        assert len(gzip.decompress(raw).splitlines()) == 6
