"""Read-only operator view of bounded collector metadata; never a control plane."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response

from .collector_runtime import LANES
from .local_mode import UnsafeTradingModeError, require_local_paper_mode

MAX_DOCUMENT_BYTES = 128 * 1024
CONTROLLER_MAX_AGE = 120.0
PUBLISHER_MAX_AGE = 30.0
ASSETS = Path(__file__).with_name("collector_status_assets")
CAMPAIGN = re.compile(r"[a-z0-9][a-z0-9-]{0,35}")
SOURCE = re.compile(r"[0-9a-f]{40}")
IMAGE = re.compile(r"sha256:[0-9a-f]{64}")
SAFE_NAME = re.compile(r"[A-Za-z0-9_./:+-]{1,100}")
REQUIRED_FEEDS = {
    "hyperliquid": {"trades/BTC", "bbo/BTC", "l2Book/BTC", "activeAssetCtx/BTC"},
    "binance": {"spot", "usdm_market", "usdm_public"},
    "kraken": {"book", "trade"},
    "bitvavo-standard": {"book", "trades"},
}


def check_assets() -> None:
    """Existing immutable-image smoke checks the files served by this module."""
    for name in ("index.html", "status.css", "status.js"):
        content = (ASSETS / name).read_bytes()
        if not content or len(content) > 512 * 1024:
            raise ValueError("Packaged status asset is missing or exceeds its bound.")


@dataclass(frozen=True)
class StatusRoots:
    spool: Path
    capture: Path
    archive: Path
    control: Path

    def __post_init__(self) -> None:
        for path in (self.spool, self.capture, self.archive, self.control):
            if not path.is_absolute() or ".." in path.parts:
                raise ValueError("Status roots require explicit absolute paths.")


def directory(path: Path) -> int:
    """Keep descriptor traversal inside real directories, including ancestors."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Unsafe status root.")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            other = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = other
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    document: dict[str, object] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError("Ambiguous status metadata.")
        document[key] = value
    return document


def _invalid_constant(_text: str) -> object:
    raise ValueError("Non-finite status metadata.")


def metadata(root: Path, *parts: str) -> dict[str, Any]:
    """Read one bounded regular metadata file without following a FIFO or link."""
    if not parts or any(not p or p in {".", ".."} or "/" in p for p in parts):
        raise ValueError("Unsafe metadata path.")
    parent = directory(root)
    try:
        for part in parts[:-1]:
            other = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            os.close(parent)
            parent = other
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    finally:
        os.close(parent)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_DOCUMENT_BYTES:
            raise ValueError("Status metadata is not a bounded regular file.")
        content = bytearray()
        while block := os.read(descriptor, min(16_384, MAX_DOCUMENT_BYTES + 1 - len(content))):
            content.extend(block)
            if len(content) > MAX_DOCUMENT_BYTES:
                raise ValueError("Status metadata exceeded its bound.")
        after = os.fstat(descriptor)
        if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(content) != before.st_size:
            raise ValueError("Status metadata changed while reading.")
    finally:
        os.close(descriptor)
    try:
        result: object = json.loads(
            content, object_pairs_hook=_unique, parse_constant=_invalid_constant
        )
    except (RecursionError, UnicodeDecodeError) as error:
        raise ValueError("Invalid status metadata encoding or nesting.") from error
    if not isinstance(result, dict):
        raise ValueError("Status metadata must be an object.")
    return result


def _count(value: object) -> int | None:
    return value if type(value) is int and 0 <= value <= 10**15 else None


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    result = float(value)
    return result if math.isfinite(result) else None


def _age(stamp: object, now: datetime) -> tuple[str | None, float | None]:
    if type(stamp) is not str or len(stamp) > 40:
        return None, None
    try:
        value = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if value.utcoffset() is None:
            return None, None
        value = value.astimezone(UTC)
        seconds = (now - value).total_seconds()
    except (ValueError, OverflowError):
        return None, None
    if seconds < 0:
        return None, None
    return value.isoformat().replace("+00:00", "Z"), round(seconds, 3)


def _utc_ns(value: object, now_ns: int) -> tuple[str | None, float | None]:
    if type(value) is not int or not 0 < value <= now_ns:
        return None, None
    try:
        stamp = datetime.fromtimestamp(value / 1e9, UTC).isoformat().replace("+00:00", "Z")
    except (ValueError, OverflowError, OSError):
        return None, None
    return stamp, round((now_ns - value) / 1e9, 3)


def _controller(document: dict[str, Any], now_ns: int) -> dict[str, Any]:
    campaign = document.get("campaign")
    source = document.get("source_commit")
    image = document.get("image_digest")
    lanes = document.get("lanes")
    profile = document.get("storage_profile")
    phase = document.get("phase")
    if (
        document.get("schema") != "public-collector-runtime-health-v1"
        or type(campaign) is not str
        or CAMPAIGN.fullmatch(campaign) is None
        or type(source) is not str
        or SOURCE.fullmatch(source) is None
        or type(image) is not str
        or IMAGE.fullmatch(image) is None
        or type(lanes) is not list
        or not lanes
        or len(lanes) > len(LANES)
        or any(type(lane) is not str or lane not in LANES for lane in lanes)
        or len(set(lanes)) != len(lanes)
        or profile not in {"vm-nfs", "truenas-zfs"}
        or phase not in {"starting", "capturing", "publishing", "completed", "failed"}
    ):
        raise ValueError("Controller identity is unavailable.")
    updated, age = _utc_ns(document.get("updated_utc_ns"), now_ns)
    if updated is None:
        raise ValueError("Controller clock is unavailable.")
    chunk = document.get("chunk")
    run_id = document.get("run_id")
    if chunk is not None or run_id is not None:
        if type(chunk) is not int or not 0 <= chunk < 36 or run_id != f"{campaign}-{chunk:04d}":
            raise ValueError("Controller run identity is unavailable.")
    elif phase not in {"starting", "failed"}:
        raise ValueError("Controller run identity is unavailable.")
    return {
        "phase": phase,
        "campaign": campaign,
        "run_id": run_id,
        "chunk_index": chunk,
        "chunk_count": _count(document.get("chunks")),
        "chunk_seconds": _count(document.get("chunk_seconds")),
        "source_commit": source,
        "image_digest": image,
        "storage_profile": profile,
        "lanes": lanes,
        "updated_utc": updated,
        "age_seconds": age,
        "fresh": age is not None and age <= CONTROLLER_MAX_AGE,
        "spool_allocated_bytes": _count(document.get("spool_allocated_bytes")),
    }


def _feed(document: object, now: datetime, publisher_fresh: bool) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError("Invalid feed status.")
    name, role = document.get("name"), document.get("role")
    if (
        type(name) is not str
        or SAFE_NAME.fullmatch(name) is None
        or role not in {"required", "optional"}
    ):
        raise ValueError("Invalid feed identity.")
    last, age = _age(document.get("last_market_utc"), now)
    bound = _number(document.get("silence_bound_seconds"))
    reported = document.get("state")
    if bound is None or not 0 < bound <= 3600:
        raise ValueError("Invalid feed freshness bound.")
    if reported not in {"fresh", "recovering", "unknown", "definitive_outage", "stale"}:
        reported = "unknown"
    if not publisher_fresh:
        state = "unknown"
    elif reported == "definitive_outage":
        state = "outage"
    elif age is None:
        state = "unknown"
    elif age > bound or reported == "stale":
        state = "stale"
    elif reported == "fresh":
        state = "fresh"
    else:
        state = reported
    return {
        "name": name,
        "role": role,
        "state": state,
        "last_market_utc": last,
        "age_seconds": age,
        "silence_bound_seconds": bound,
    }


def _publication(root: Path, campaign: str, lane: str, run_id: str) -> dict[str, Any]:
    try:
        seal = metadata(root, campaign, lane, run_id, "COMPLETE.json")
        if (
            seal.get("closed_chunk_verified") is not True
            or seal.get("research_ready") is not False
            or type(seal.get("manifest_sha256")) is not str
            or re.fullmatch(r"[0-9a-f]{64}", seal["manifest_sha256"]) is None
        ):
            raise ValueError("Invalid publication marker.")
        return {"state": "seal_present", "independently_verified": False}
    except (OSError, ValueError, TypeError):
        return {"state": "unconfirmed", "independently_verified": False}


def _lane(
    roots: StatusRoots, lane: str, controller: dict[str, Any], now: datetime
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "lane": lane,
        "state": "unknown",
        "publisher_fresh": False,
        "feeds": [],
        "writer_pending_records": None,
        "writer_published_parts": None,
        "publication": {"work": {"state": "unconfirmed"}, "archive": {"state": "unconfirmed"}},
    }
    run_id, chunk = controller["run_id"], controller["chunk_index"]
    if run_id is None or chunk is None:
        return result
    for name in ("capture", "archive"):
        root = roots.capture if name == "capture" else roots.archive
        result["publication"]["work" if name == "capture" else name] = _publication(
            root, controller["campaign"], lane, run_id
        )
    parts = (controller["campaign"], f"chunk-{chunk:04d}", lane, *LANES[lane][1].split("/"), run_id)
    try:
        live = metadata(roots.spool, *parts, "capture-live.json")
        if live.get("schema") != "capture-live-v1" or live.get("run_id") != run_id:
            raise ValueError("Unknown native live status.")
        published, age = _age(live.get("published_utc"), now)
        fresh = age is not None and age <= PUBLISHER_MAX_AGE
        records = live.get("feeds")
        if type(records) is not list or not 1 <= len(records) <= 64:
            raise ValueError("Missing bounded feed census.")
        feeds = [_feed(record, now, fresh) for record in records]
        if len({feed["name"] for feed in feeds}) != len(feeds):
            raise ValueError("Duplicate feed status.")
        required = [feed for feed in feeds if feed["role"] == "required"]
        if {feed["name"] for feed in required} != REQUIRED_FEEDS[lane]:
            raise ValueError("Required feed census does not match the bounded lane.")
        state = "fresh" if all(feed["state"] == "fresh" for feed in required) else "degraded"
        if not fresh:
            state = "unknown"
        if live.get("definitive_outage") is not None:
            state = "outage"
        result.update(
            state=state,
            publisher_fresh=fresh,
            published_utc=published,
            publisher_age_seconds=age,
            feeds=feeds,
            writer_pending_records=_count(live.get("writer_pending_records")),
            writer_published_parts=_count(live.get("writer_published_parts")),
        )
    except (OSError, ValueError, TypeError, OverflowError):
        pass
    # A terminal or stale controller never becomes live collection from child metadata alone.
    if controller["phase"] in {"completed", "failed", "publishing"}:
        result["state"] = controller["phase"]
    elif not controller["fresh"]:
        result["state"] = "unknown"
    return result


def _disk(path: Path) -> dict[str, Any]:
    try:
        descriptor = directory(path)
        try:
            info = os.fstatvfs(descriptor)
        finally:
            os.close(descriptor)
        return {
            "state": "available",
            "total_bytes": info.f_blocks * info.f_frsize,
            "available_bytes": info.f_bavail * info.f_frsize,
            "filesystem_readonly": bool(info.f_flag & os.ST_RDONLY),
        }
    except (OSError, ValueError):
        return {"state": "unknown", "total_bytes": None, "available_bytes": None}


def _clock(roots: StatusRoots, now_ns: int) -> dict[str, Any]:
    try:
        doc = metadata(roots.control, "clock.json")
        sampled, age = _utc_ns(doc.get("sampled_utc_ns"), now_ns)
        offset = _number(doc.get("system_offset_seconds"))
        delay = _number(doc.get("root_delay_seconds"))
        dispersion = _number(doc.get("root_dispersion_seconds"))
        if (
            doc.get("schema") != "collector-host-clock-v1"
            or doc.get("synchronized") is not True
            or sampled is None
            or age is None
            or age > 120
            or offset is None
            or abs(offset) > 0.050
            or delay is None
            or not 0 <= delay <= 0.500
            or dispersion is None
            or not 0 <= dispersion <= 0.250
        ):
            raise ValueError("Clock evidence unavailable.")
        return {
            "state": "synchronized",
            "sampled_utc": sampled,
            "age_seconds": age,
            "offset_ms": round(offset * 1000, 4),
            "dispersion_ms": round(dispersion * 1000, 4),
        }
    except (OSError, ValueError, TypeError, OverflowError):
        return {"state": "unknown"}


def snapshot(roots: StatusRoots, *, now_ns: int | None = None) -> dict[str, Any]:
    current = time.time_ns() if now_ns is None else now_ns
    now = datetime.fromtimestamp(current / 1e9, UTC)
    result: dict[str, Any] = {
        "schema": "collector-operator-status-v1",
        "generated_utc": now.isoformat().replace("+00:00", "Z"),
        "service": "alive",
        "mode": "PAPER",
        "read_only": True,
        "overall": "unknown",
        "controller": None,
        "lanes": [],
        "inactive_lanes": [],
        "clock": _clock(roots, current),
        "storage": {
            name: _disk(path)
            for name, path in (
                ("spool", roots.spool),
                ("work", roots.capture),
                ("archive", roots.archive),
            )
        },
        "research_ready": False,
        "note": "Public-data monitoring only; no orders or profitability assessment.",
    }
    try:
        controller = _controller(metadata(roots.spool, "runtime-health.json"), current)
        lanes = [_lane(roots, lane, controller, now) for lane in controller["lanes"]]
        phase = controller["phase"]
        overall = phase
        if phase not in {"completed", "failed"} and not controller["fresh"]:
            overall = "stale"
        elif phase == "capturing" and (
            not all(lane["state"] == "fresh" for lane in lanes)
            or result["clock"]["state"] != "synchronized"
        ):
            overall = "degraded"
        result.update(
            controller=controller,
            lanes=lanes,
            overall=overall,
            inactive_lanes=[lane for lane in LANES if lane not in controller["lanes"]],
        )
    except (OSError, ValueError, TypeError, OverflowError):
        pass
    return result


def create_status_app(
    roots: StatusRoots,
    *,
    environment: Mapping[str, str] | None = None,
    now_ns: Callable[[], int] = time.time_ns,
) -> FastAPI:
    source = os.environ if environment is None else environment
    require_local_paper_mode(source.get("TRADING_MODE"))
    check_assets()
    application = FastAPI(
        title="Hyperliquid collector status", docs_url=None, redoc_url=None, openapi_url=None
    )

    @application.middleware("http")
    async def secure_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; "
                "style-src 'self'; "
                "connect-src 'self'; img-src 'self'; frame-ancestors 'none'; base-uri 'none'; "
                "form-action 'none'",
            }
        )
        return response

    @application.get("/", include_in_schema=False)
    def page() -> FileResponse:
        return FileResponse(ASSETS / "index.html", media_type="text/html")

    @application.get("/status.css", include_in_schema=False)
    def stylesheet() -> FileResponse:
        return FileResponse(ASSETS / "status.css", media_type="text/css")

    @application.get("/status.js", include_in_schema=False)
    def javascript() -> FileResponse:
        return FileResponse(ASSETS / "status.js", media_type="text/javascript")

    @application.get("/healthz")
    def health() -> dict[str, object]:
        return {"status": "alive", "read_only": True, "mode": "PAPER"}

    @application.get("/api/status")
    def status_document() -> JSONResponse:
        try:
            require_local_paper_mode(source.get("TRADING_MODE"))
        except UnsafeTradingModeError:
            return JSONResponse({"state": "unavailable"}, status_code=503)
        return JSONResponse(snapshot(roots, now_ns=now_ns()))

    return application


def main(argv: Sequence[str] | None = None) -> int:
    require_local_paper_mode(os.environ.get("TRADING_MODE"))
    parser = argparse.ArgumentParser(description=__doc__)
    for role in ("spool", "capture", "archive", "control"):
        parser.add_argument("--" + role, required=True, type=Path)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("Use a valid status HTTP port.")
    roots = StatusRoots(args.spool, args.capture, args.archive, args.control)
    import uvicorn

    uvicorn.run(
        create_status_app(roots),
        host=args.host,
        port=args.port,
        access_log=False,
        limit_concurrency=16,
        timeout_keep_alive=5,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
