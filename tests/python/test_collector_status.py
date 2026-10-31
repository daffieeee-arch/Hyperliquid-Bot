"""Actual operator HTTP and metadata boundaries, with deterministic local files."""

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hyperliquid_bot import collector_status as status
from hyperliquid_bot.collector_runtime import LANES
from hyperliquid_bot.local_mode import UnsafeTradingModeError

NOW = datetime(2026, 11, 2, 0, 2, tzinfo=UTC)
NOW_NS = int(NOW.timestamp()) * 10**9
SOURCE = "a" * 40
IMAGE = "sha256:" + "b" * 64


def write(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def live_path(roots: status.StatusRoots, lane: str) -> Path:
    return (
        roots.spool
        / "pilot"
        / "chunk-0000"
        / lane
        / LANES[lane][1]
        / "pilot-0000"
        / "capture-live.json"
    )


def live_document(lane: str) -> dict[str, Any]:
    return {
        "schema": "capture-live-v1",
        "run_id": "pilot-0000",
        "published_utc": NOW.isoformat(),
        "feeds": [
            {
                "name": name,
                "role": "required",
                "state": "fresh",
                "last_market_utc": (NOW - timedelta(seconds=1)).isoformat(),
                "silence_bound_seconds": 30.0,
            }
            for name in sorted(status.REQUIRED_FEEDS[lane])
        ],
        "writer_pending_records": 7,
        "writer_published_parts": 11,
        "definitive_outage": None,
        "unrelated_private_value": "must-not-leak",
    }


@pytest.fixture
def roots(tmp_path: Path) -> status.StatusRoots:
    result = status.StatusRoots(
        *(tmp_path / name for name in ("spool", "work", "archive", "control"))
    )
    for path in (result.spool, result.capture, result.archive, result.control):
        path.mkdir()
    write(
        result.spool / "runtime-health.json",
        {
            "schema": "public-collector-runtime-health-v1",
            "phase": "capturing",
            "campaign": "pilot",
            "run_id": "pilot-0000",
            "chunk": 0,
            "chunks": 1,
            "chunk_seconds": 600,
            "source_commit": SOURCE,
            "image_digest": IMAGE,
            "lanes": ["hyperliquid", "binance"],
            "storage_profile": "truenas-zfs",
            "updated_utc_ns": NOW_NS,
            "spool_allocated_bytes": 12345,
            "unrelated_private_value": "must-not-leak",
        },
    )
    write(
        result.control / "clock.json",
        {
            "schema": "collector-host-clock-v1",
            "sampled_utc_ns": NOW_NS,
            "synchronized": True,
            "system_offset_seconds": 0.0001,
            "root_delay_seconds": 0.01,
            "root_dispersion_seconds": 0.001,
        },
    )
    for lane in ("hyperliquid", "binance"):
        write(live_path(result, lane), live_document(lane))
    return result


def client(roots: status.StatusRoots) -> TestClient:
    return TestClient(
        status.create_status_app(
            roots, environment={"TRADING_MODE": "PAPER"}, now_ns=lambda: NOW_NS
        )
    )


def change_health(roots: status.StatusRoots, **changes: object) -> None:
    path = roots.spool / "runtime-health.json"
    write(path, json.loads(path.read_text()) | changes)


def test_actual_http_reports_fresh_native_sources_without_echoing_other_fields(
    roots: status.StatusRoots,
) -> None:
    response = client(roots).get("/api/status")
    assert response.status_code == 200
    data = response.json()
    assert data["overall"] == "capturing"
    assert data["mode"] == "PAPER" and data["read_only"] is True
    assert len(data["lanes"]) == 2
    assert all(lane["state"] == "fresh" for lane in data["lanes"])
    assert data["lanes"][0]["writer_published_parts"] == 11
    assert data["lanes"][0]["feeds"][0]["age_seconds"] == 1
    assert "must-not-leak" not in response.text and str(roots.spool) not in response.text
    assert data["clock"]["state"] == "synchronized"
    assert data["research_ready"] is False


def test_fresh_publisher_and_heartbeat_do_not_hide_stale_economic_input(
    roots: status.StatusRoots,
) -> None:
    doc = live_document("binance")
    doc["feeds"][0]["last_market_utc"] = (NOW - timedelta(seconds=31)).isoformat()
    write(live_path(roots, "binance"), doc)
    data = client(roots).get("/api/status").json()
    assert data["controller"]["fresh"] is True
    assert data["lanes"][1]["publisher_fresh"] is True
    assert data["lanes"][1]["feeds"][0]["state"] == "stale"
    assert data["overall"] == "degraded"


def test_stale_controller_never_becomes_green_from_fresh_children(
    roots: status.StatusRoots,
) -> None:
    change_health(roots, updated_utc_ns=NOW_NS - 121 * 10**9)
    app = client(roots)
    assert app.get("/healthz").json()["status"] == "alive"
    data = app.get("/api/status").json()
    assert data["overall"] == "stale"
    assert all(lane["state"] == "unknown" for lane in data["lanes"])


def test_completed_measurement_remains_completed_after_heartbeat_ages(
    roots: status.StatusRoots,
) -> None:
    change_health(roots, phase="completed", updated_utc_ns=NOW_NS - 86_400 * 10**9)
    data = client(roots).get("/api/status").json()
    assert data["overall"] == "completed"
    assert data["controller"]["fresh"] is False
    assert all(lane["state"] == "completed" for lane in data["lanes"])


@pytest.mark.parametrize(
    "change",
    [
        {"run_id": "other-run"},
        {"feeds": []},
        {"published_utc": (NOW + timedelta(seconds=1)).isoformat()},
        {"feeds": live_document("binance")["feeds"][:1]},
    ],
)
def test_untrusted_or_incomplete_native_status_is_not_fresh(
    roots: status.StatusRoots, change: dict[str, Any]
) -> None:
    write(live_path(roots, "binance"), live_document("binance") | change)
    data = client(roots).get("/api/status").json()
    assert data["overall"] == "degraded"
    assert data["lanes"][1]["state"] == "unknown"


def test_stale_native_publisher_is_unknown_even_if_it_claims_fresh(
    roots: status.StatusRoots,
) -> None:
    write(
        live_path(roots, "hyperliquid"),
        live_document("hyperliquid") | {"published_utc": (NOW - timedelta(seconds=31)).isoformat()},
    )
    data = client(roots).get("/api/status").json()
    assert data["lanes"][0]["state"] == "unknown"
    assert data["overall"] == "degraded"


@pytest.mark.parametrize(
    "change",
    [
        {"campaign": "../private"},
        {"run_id": "pilot-0001"},
        {"chunk": True},
        {"updated_utc_ns": NOW_NS + 1},
        {"lanes": ["unknown"]},
        {"storage_profile": "unknown"},
    ],
)
def test_unknown_identity_or_future_controller_clock_fails_closed(
    roots: status.StatusRoots, change: dict[str, Any]
) -> None:
    change_health(roots, **change)
    response = client(roots).get("/api/status")
    assert response.json()["overall"] == "unknown"
    assert response.json()["controller"] is None
    assert "private" not in response.text


def test_explicit_public_four_lane_census_is_supported(roots: status.StatusRoots) -> None:
    change_health(roots, lanes=list(status.REQUIRED_FEEDS))
    for lane in ("kraken", "bitvavo-standard"):
        write(live_path(roots, lane), live_document(lane))
    data = client(roots).get("/api/status").json()
    assert data["overall"] == "capturing"
    assert len(data["lanes"]) == 4 and data["inactive_lanes"] == []
    assert all(lane["state"] == "fresh" for lane in data["lanes"])


def test_inactive_venues_are_explicit_without_claiming_any_capture(
    roots: status.StatusRoots,
) -> None:
    data = client(roots).get("/api/status").json()
    assert data["inactive_lanes"] == ["kraken", "bitvavo-standard"]
    assert [lane["lane"] for lane in data["lanes"]] == ["hyperliquid", "binance"]


def test_legacy_health_without_declared_lanes_is_unknown(roots: status.StatusRoots) -> None:
    path = roots.spool / "runtime-health.json"
    doc = json.loads(path.read_text())
    del doc["lanes"]
    write(path, doc)
    assert client(roots).get("/api/status").json()["overall"] == "unknown"


def test_future_clock_evidence_prevents_green_collection(roots: status.StatusRoots) -> None:
    path = roots.control / "clock.json"
    write(path, json.loads(path.read_text()) | {"sampled_utc_ns": NOW_NS + 1})
    data = client(roots).get("/api/status").json()
    assert data["clock"]["state"] == "unknown"
    assert data["overall"] == "degraded"


def test_exact_native_publication_marker_is_only_reported_not_accepted(
    roots: status.StatusRoots,
) -> None:
    for root in (roots.capture, roots.archive):
        write(
            root / "pilot/hyperliquid/pilot-0000/COMPLETE.json",
            {"manifest_sha256": "d" * 64, "closed_chunk_verified": True, "research_ready": False},
        )
    lane = client(roots).get("/api/status").json()["lanes"][0]
    assert lane["publication"]["work"] == {"state": "seal_present", "independently_verified": False}
    assert lane["publication"]["archive"]["state"] == "seal_present"


def test_symlinked_native_metadata_and_ancestor_are_not_followed(
    roots: status.StatusRoots, tmp_path: Path
) -> None:
    live = live_path(roots, "binance")
    outside = tmp_path / "outside.json"
    write(outside, live_document("binance"))
    live.unlink()
    live.symlink_to(outside)
    assert client(roots).get("/api/status").json()["lanes"][1]["state"] == "unknown"
    live.unlink()
    live.parent.rmdir()
    live.parent.symlink_to(outside.parent, target_is_directory=True)
    assert client(roots).get("/api/status").json()["lanes"][1]["state"] == "unknown"


def test_fifo_control_file_is_rejected_without_blocking(tmp_path: Path) -> None:
    fifo = tmp_path / "clock.json"
    os.mkfifo(fifo)
    code = (
        "from pathlib import Path; from hyperliquid_bot.collector_status import metadata; "
        "metadata(Path(__import__('sys').argv[1]), 'clock.json')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=5,
        env=os.environ | {"PYTHONPATH": str(Path(status.__file__).parents[1])},
    )
    assert result.returncode != 0 and "bounded regular file" in result.stderr


@pytest.mark.parametrize(
    "content",
    [
        '{"x":NaN}',
        '{"phase":1,"phase":2}',
        "[" * 2000 + "0" + "]" * 2000,
        "x" * (status.MAX_DOCUMENT_BYTES + 1),
    ],
)
def test_invalid_bounded_documents_produce_unknown_not_http_errors(
    roots: status.StatusRoots, content: str
) -> None:
    (roots.spool / "runtime-health.json").write_text(content)
    response = client(roots).get("/api/status")
    assert response.status_code == 200 and response.json()["overall"] == "unknown"


def test_http_is_read_only_has_local_assets_and_security_headers(roots: status.StatusRoots) -> None:
    app = client(roots)
    before = (roots.spool / "runtime-health.json").read_bytes()
    for route in ("/", "/status.css", "/status.js", "/api/status", "/healthz"):
        response = app.get(route)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "access-control-allow-origin" not in response.headers
        assert app.post(route, json={"phase": "capturing"}).status_code == 405
    assert app.get("/openapi.json").status_code == 404
    assert app.get("/docs").status_code == 404
    assert (roots.spool / "runtime-health.json").read_bytes() == before
    html = app.get("/").text
    assert "https://" not in html and "http://" not in html
    assert "innerHTML" not in app.get("/status.js").text


def test_webservice_is_paper_only_before_reading_metadata(roots: status.StatusRoots) -> None:
    with pytest.raises(UnsafeTradingModeError):
        status.create_status_app(roots, environment={"TRADING_MODE": "LIVE"})
    environment = {"TRADING_MODE": "PAPER"}
    app = TestClient(status.create_status_app(roots, environment=environment))
    environment["TRADING_MODE"] = "LIVE"
    assert app.get("/api/status").status_code == 503
    assert app.get("/healthz").json()["status"] == "alive"
