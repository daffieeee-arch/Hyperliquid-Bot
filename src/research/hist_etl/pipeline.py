"""plan, sync, verify, and catalog commands."""

from __future__ import annotations

import json
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

from research.hist_etl.binance_convert import audit_binance_month, materialize_binance_month
from research.hist_etl.catalog import refresh_catalog
from research.hist_etl.checksums import cached_sha256, verify_zip
from research.hist_etl.discover import checksum_beside, index_zips, resolve_local
from research.hist_etl.disk import assert_free, assert_safe_root, free_bytes
from research.hist_etl.download import (
    download_checksum_beside,
    download_opaque,
    download_verified_zip,
    head_status,
)
from research.hist_etl.errors import HistEtlError
from research.hist_etl.http import RateLimiter, Transport, build_transport
from research.hist_etl.kraken import audit_kraken_tree, ingest_kraken, manifest_present
from research.hist_etl.manifest import (
    assert_known_ids,
    load_manifest,
    select_binance,
    select_kraken,
)
from research.hist_etl.models import (
    ArchivePlan,
    BinanceSpec,
    Gap,
    HistManifest,
    KrakenSpec,
    SourceDigest,
)
from research.hist_etl.planning import group_by_month, plan_binance, sources_for_month


def default_sleeper(seconds: float) -> None:
    time.sleep(seconds)


def apply_env(manifest: HistManifest, env: Mapping[str, str]) -> HistManifest:
    minimum = manifest.min_free_bytes
    per_second = manifest.requests_per_second
    retries = manifest.max_retries
    timeout = manifest.timeout_seconds
    if env.get("HIST_ETL_MIN_FREE_BYTES"):
        minimum = int(env["HIST_ETL_MIN_FREE_BYTES"])
    if env.get("HIST_ETL_REQUESTS_PER_SECOND"):
        per_second = float(env["HIST_ETL_REQUESTS_PER_SECOND"])
    if env.get("HIST_ETL_MAX_RETRIES"):
        retries = int(env["HIST_ETL_MAX_RETRIES"])
    if env.get("HIST_ETL_HTTP_TIMEOUT_SECONDS"):
        timeout = float(env["HIST_ETL_HTTP_TIMEOUT_SECONDS"])
    return replace(
        manifest,
        min_free_bytes=minimum,
        requests_per_second=per_second,
        max_retries=retries,
        timeout_seconds=timeout,
    )


def run_plan(
    *,
    root: Path,
    manifest_path: Path,
    today: date,
    dataset_ids: tuple[str, ...] | None,
    env: Mapping[str, str],
    transport: Transport | None = None,
) -> int:
    manifest, binance, kraken, safe_root = _context(root, manifest_path, dataset_ids, env)
    lines, items = _describe(manifest, binance, kraken, safe_root, today, transport, probe=True)
    for line in lines:
        print(line)
    _print_summary(items, safe_root, manifest.min_free_bytes)
    if any(item.action == "absent" for item in items):
        return 2
    return 0


def run_sync(
    *,
    root: Path,
    manifest_path: Path,
    today: date,
    dataset_ids: tuple[str, ...] | None,
    env: Mapping[str, str],
    dry_run: bool,
    transport: Transport | None = None,
    rebuild: bool = False,
    replace_legacy_views: bool = False,
) -> int:
    if dry_run:
        return run_plan(
            root=root,
            manifest_path=manifest_path,
            today=today,
            dataset_ids=dataset_ids,
            env=env,
            transport=transport,
        )
    manifest, binance, kraken, safe_root = _context(root, manifest_path, dataset_ids, env)
    assert_free(safe_root, manifest.min_free_bytes)
    safe_root.mkdir(parents=True, exist_ok=True)
    client = transport if transport is not None else build_transport(manifest.timeout_seconds)
    limiter = RateLimiter(manifest.requests_per_second, default_sleeper)
    gaps: list[Gap] = []
    acquired = _acquire_binance(
        manifest,
        binance,
        safe_root,
        today,
        client,
        limiter,
        gaps,
    )
    _materialize_binance(
        binance,
        acquired,
        safe_root,
        today,
        manifest.min_free_bytes,
        gaps,
        rebuild=rebuild,
    )
    _sync_kraken(manifest, kraken, safe_root, client, limiter, gaps, rebuild=rebuild)
    refresh_catalog(safe_root, replace_legacy_views=replace_legacy_views)
    _write_report(safe_root, gaps, command="sync")
    for gap in gaps:
        print(f"gap\t{gap.kind}\t{gap.dataset_id}\t{gap.detail}")
    return 2 if gaps else 0


def run_verify(
    *,
    root: Path,
    manifest_path: Path,
    today: date,
    dataset_ids: tuple[str, ...] | None,
    env: Mapping[str, str],
) -> int:
    _manifest, binance, kraken, safe_root = _context(root, manifest_path, dataset_ids, env)
    if not safe_root.is_dir():
        raise HistEtlError(f"archive root does not exist: {safe_root}", exit_code=2)
    gaps: list[Gap] = []
    index = index_zips(safe_root)
    plans = tuple(resolve_local(plan, index) for plan in plan_binance(binance, today, safe_root))
    for plan in plans:
        if plan.local_path is None:
            gaps.append(Gap("missing_archive", plan.dataset_id, plan.filename))
            continue
        checksum = checksum_beside(plan.local_path)
        if not checksum.is_file():
            gaps.append(
                Gap("checksum_mismatch", plan.dataset_id, f"missing checksum for {plan.filename}")
            )
            continue
        try:
            verify_zip(plan.local_path, checksum)
        except HistEtlError as exc:
            gaps.append(Gap("checksum_mismatch", plan.dataset_id, str(exc)))
    _audit_binance_parquet(binance, plans, safe_root, today, gaps)
    for spec in kraken:
        zips = _kraken_zips(safe_root, spec)
        if not zips:
            gaps.append(Gap("missing_kraken_zip", spec.id, f"no zip matched {spec.zip_glob}"))
            continue
        gaps.extend(audit_kraken_tree(spec, safe_root))
    _write_report(safe_root, gaps, command="verify")
    for gap in gaps:
        print(f"gap\t{gap.kind}\t{gap.dataset_id}\t{gap.detail}")
    return 2 if gaps else 0


def run_catalog(
    *,
    root: Path,
    manifest_path: Path,
    dataset_ids: tuple[str, ...] | None,
    env: Mapping[str, str],
    replace_legacy_views: bool = False,
) -> int:
    _manifest, _binance, _kraken, safe_root = _context(root, manifest_path, dataset_ids, env)
    if not safe_root.is_dir():
        raise HistEtlError(f"archive root does not exist: {safe_root}", exit_code=2)
    names = refresh_catalog(safe_root, replace_legacy_views=replace_legacy_views)
    print(f"catalog\tviews={len(names)}")
    for name in names:
        print(f"catalog\tview\t{name}")
    return 0


def _context(
    root: Path,
    manifest_path: Path,
    dataset_ids: tuple[str, ...] | None,
    env: Mapping[str, str],
) -> tuple[HistManifest, tuple[BinanceSpec, ...], tuple[KrakenSpec, ...], Path]:
    manifest = apply_env(load_manifest(manifest_path), env)
    assert_known_ids(manifest, dataset_ids)
    return (
        manifest,
        select_binance(manifest, dataset_ids),
        select_kraken(manifest, dataset_ids),
        assert_safe_root(root),
    )


def _describe(
    manifest: HistManifest,
    binance: tuple[BinanceSpec, ...],
    kraken: tuple[KrakenSpec, ...],
    root: Path,
    today: date,
    transport: Transport | None,
    *,
    probe: bool,
) -> tuple[list[str], list[ArchivePlan]]:
    index = index_zips(root) if root.is_dir() else {}
    resolved = [resolve_local(plan, index) for plan in plan_binance(binance, today, root)]
    client = transport if transport is not None else build_transport(manifest.timeout_seconds)
    limiter = RateLimiter(manifest.requests_per_second, default_sleeper)
    described: list[ArchivePlan] = []
    lines: list[str] = []
    for item in resolved:
        current = item
        if probe and item.action == "download":
            status, size = head_status(
                item.url,
                transport=client,
                limiter=limiter,
                max_retries=manifest.max_retries,
                sleeper=default_sleeper,
            )
            if status == 404:
                current = replace(item, action="absent", size_bytes=None)
            elif status != 200:
                raise HistEtlError(f"HEAD {item.url} returned HTTP {status}")
            else:
                current = replace(item, size_bytes=size)
        described.append(current)
        lines.append(_format_plan(current))
    for spec in kraken:
        lines.extend(_describe_kraken(spec, root, manifest, client, limiter, probe))
    return lines, described


def _describe_kraken(
    spec: KrakenSpec,
    root: Path,
    manifest: HistManifest,
    transport: Transport,
    limiter: RateLimiter,
    probe: bool,
) -> list[str]:
    found = _kraken_zips(root, spec)
    lines: list[str] = []
    if found:
        for path in found:
            flag = "manifest" if manifest_present(path) else "no-manifest"
            lines.append(f"plan\tpresent\t{spec.id}\t{path.name}\t{path.stat().st_size}\t{flag}")
        return lines
    if spec.url is None:
        lines.append(f"plan\tabsent\t{spec.id}\t{spec.zip_glob}\t\t")
        return lines
    filename = spec.url.rstrip("/").rsplit("/", 1)[-1]
    size: int | None = None
    action = "download"
    if probe:
        status, size = head_status(
            spec.url,
            transport=transport,
            limiter=limiter,
            max_retries=manifest.max_retries,
            sleeper=default_sleeper,
        )
        if status == 404:
            action = "absent"
            size = None
        elif status != 200:
            raise HistEtlError(f"HEAD {spec.url} returned HTTP {status}")
    rendered = "" if size is None else str(size)
    lines.append(f"plan\t{action}\t{spec.id}\t{filename}\t{rendered}\t{spec.url}")
    return lines


def _acquire_binance(
    manifest: HistManifest,
    specs: tuple[BinanceSpec, ...],
    root: Path,
    today: date,
    transport: Transport,
    limiter: RateLimiter,
    gaps: list[Gap],
) -> tuple[ArchivePlan, ...]:
    index = index_zips(root)
    acquired: list[ArchivePlan] = []
    for plan in (resolve_local(item, index) for item in plan_binance(specs, today, root)):
        try:
            assert_free(root, manifest.min_free_bytes)
            local = _ensure_archive(plan, root, manifest, transport, limiter)
        except HistEtlError as exc:
            if exc.exit_code == 3:
                raise
            text = str(exc)
            if "not found" in text:
                kind = "missing_archive"
            elif "download failed" in text:
                kind = "download_failed"
            else:
                kind = "checksum_mismatch"
            gaps.append(Gap(kind, plan.dataset_id, text))
            continue
        if local is None:
            gaps.append(Gap("missing_archive", plan.dataset_id, plan.filename))
            continue
        acquired.append(local)
        print(f"sync\tready\t{plan.dataset_id}\t{local.local_path}")
    return tuple(acquired)


def _ensure_archive(
    plan: ArchivePlan,
    root: Path,
    manifest: HistManifest,
    transport: Transport,
    limiter: RateLimiter,
) -> ArchivePlan | None:
    if plan.local_path is not None and plan.action == "present":
        verify_zip(plan.local_path, checksum_beside(plan.local_path))
        return plan
    if plan.local_path is not None and plan.action == "needs_checksum":
        download_checksum_beside(
            plan.local_path,
            plan.checksum_url,
            root=root,
            min_free_bytes=manifest.min_free_bytes,
            transport=transport,
            limiter=limiter,
            max_retries=manifest.max_retries,
            sleeper=default_sleeper,
        )
        verify_zip(plan.local_path, checksum_beside(plan.local_path))
        return replace(plan, action="present")
    download_verified_zip(
        url=plan.url,
        checksum_url=plan.checksum_url,
        destination=plan.canonical_path,
        root=root,
        min_free_bytes=manifest.min_free_bytes,
        transport=transport,
        limiter=limiter,
        max_retries=manifest.max_retries,
        sleeper=default_sleeper,
    )
    return replace(
        plan,
        local_path=plan.canonical_path,
        action="present",
        size_bytes=plan.canonical_path.stat().st_size,
    )


def _materialize_binance(
    specs: tuple[BinanceSpec, ...],
    acquired: tuple[ArchivePlan, ...],
    root: Path,
    today: date,
    min_free_bytes: int,
    gaps: list[Gap],
    *,
    rebuild: bool,
) -> None:
    by_id = {spec.id: spec for spec in specs}
    have = {(item.dataset_id, item.period): item for item in acquired}
    grouped = group_by_month(plan_binance(specs, today, root))
    for key in sorted(grouped):
        group = grouped[key]
        dataset_id, month_text = key
        chosen = sources_for_month(group)
        ready = [
            have[(item.dataset_id, item.period)]
            for item in chosen
            if (item.dataset_id, item.period) in have
        ]
        if len(ready) != len(chosen) and (
            not ready or any(item.granularity == "monthly" for item in chosen)
        ):
            continue
        if not ready:
            continue
        assert_free(root, min_free_bytes)
        spec = by_id[dataset_id]
        sources = tuple(
            SourceDigest(
                name=item.local_path.name,
                sha256=cached_sha256(root, item.local_path),
                path=item.local_path,
            )
            for item in ready
            if item.local_path is not None
        )
        month = date.fromisoformat(f"{month_text}-01")
        _path, month_gaps = materialize_binance_month(
            spec=spec,
            month=month,
            sources=sources,
            root=root,
            today=today,
            staging=root / "staging" / "hist_etl" / "binance",
            rebuild=rebuild,
        )
        gaps.extend(month_gaps)


def _audit_binance_parquet(
    specs: tuple[BinanceSpec, ...],
    plans: tuple[ArchivePlan, ...],
    root: Path,
    today: date,
    gaps: list[Gap],
) -> None:
    """Re-check month files that are already on disk. Does not rebuild them."""

    by_id = {spec.id: spec for spec in specs}
    present = {(plan.dataset_id, plan.period) for plan in plans if plan.local_path is not None}
    for key, group in group_by_month(plans).items():
        dataset_id, month_text = key
        chosen = sources_for_month(group)
        if any((item.dataset_id, item.period) not in present for item in chosen):
            continue
        month = date.fromisoformat(f"{month_text}-01")
        gaps.extend(
            audit_binance_month(spec=by_id[dataset_id], month=month, root=root, today=today)
        )


def _sync_kraken(
    manifest: HistManifest,
    specs: tuple[KrakenSpec, ...],
    root: Path,
    transport: Transport,
    limiter: RateLimiter,
    gaps: list[Gap],
    *,
    rebuild: bool,
) -> None:
    for spec in specs:
        if spec.url is not None:
            filename = spec.url.rstrip("/").rsplit("/", 1)[-1]
            parent = spec.zip_glob.rsplit("/", 1)[0] if "/" in spec.zip_glob else ""
            destination = root / parent / filename if parent else root / filename
            if not destination.exists():
                try:
                    assert_free(root, manifest.min_free_bytes)
                    download_opaque(
                        spec.url,
                        destination,
                        root=root,
                        min_free_bytes=manifest.min_free_bytes,
                        transport=transport,
                        limiter=limiter,
                        max_retries=manifest.max_retries,
                        sleeper=default_sleeper,
                    )
                except HistEtlError as exc:
                    if exc.exit_code == 3:
                        raise
                    gaps.append(Gap("missing_kraken_zip", spec.id, str(exc)))
                    continue
        zips = _kraken_zips(root, spec)
        digests = tuple(
            SourceDigest(name=path.name, sha256=cached_sha256(root, path), path=path)
            for path in zips
        )
        gaps.extend(ingest_kraken(spec, zips, root=root, sources=digests, rebuild=rebuild))


def _kraken_zips(root: Path, spec: KrakenSpec) -> tuple[Path, ...]:
    if not root.is_dir():
        return ()
    return tuple(
        sorted(
            path for path in root.glob(spec.zip_glob) if path.is_file() and not path.is_symlink()
        )
    )


def _format_plan(item: ArchivePlan) -> str:
    size = "" if item.size_bytes is None else str(item.size_bytes)
    return f"plan\t{item.action}\t{item.dataset_id}\t{item.filename}\t{size}\t{item.url}"


def _print_summary(items: Sequence[ArchivePlan], root: Path, minimum: int) -> None:
    present = sum(item.action == "present" for item in items)
    download = sum(item.action == "download" for item in items)
    needs = sum(item.action == "needs_checksum" for item in items)
    absent = sum(item.action == "absent" for item in items)
    known = [item.size_bytes for item in items if item.size_bytes is not None]
    unknown = sum(item.size_bytes is None for item in items)
    free = free_bytes(root)
    print(
        "plan\ttotal"
        f"\tfiles={len(items)}\tpresent={present}\tdownload={download}"
        f"\tneeds_checksum={needs}\tabsent={absent}"
        f"\tbytes={sum(known)}\tunknown={unknown}\tfree={free}\tmin_free={minimum}"
    )


def _write_report(root: Path, gaps: Sequence[Gap], *, command: str) -> None:
    path = root / "logs" / "gap_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ok": not gaps,
        "command": command,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "gaps": [gap.as_dict() for gap in gaps],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def parse_today(value: str | None) -> date:
    if value is None:
        return datetime.now(UTC).date()
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HistEtlError("--today must be YYYY-MM-DD") from exc
