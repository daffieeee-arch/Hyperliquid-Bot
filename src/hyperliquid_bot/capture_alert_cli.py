"""PAPER capture-failure alert CLI.

``test`` prints a labelled TEST payload. ``watch`` reports dead, stale, or
FAILED retains. Both dry-run unless ``--send``. Neither prints the webhook
URL or the authorization header.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from .capture_alert_watch import (
    DEFAULT_FAILED_LOOKBACK_SECONDS,
    DEFAULT_LIVE_STALE_SECONDS,
    DEFAULT_STALE_SECONDS,
    DEFAULT_STARTUP_GRACE_SECONDS,
    WATCH_LANES,
    WatchLane,
    evaluate_capture_watch,
    iter_watch_run_dirs,
    load_capture_watch_snapshot,
    process_alive_for_run,
    tmux_session_alive,
)
from .capture_operator_alert import (
    CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV,
    CAPTURE_ALERT_WEBHOOK_ENV,
    alert_receipt_exists,
    capture_operator_alert_payload,
    emit_capture_operator_alert,
    resolve_capture_alert_source,
    resolve_receipt_dir,
)

_PUBLIC_PAYLOAD_KEYS = (
    "venue",
    "run_id",
    "state",
    "status",
    "reason",
    "error_class",
    "error_message",
    "last_write_utc",
    "last_write_amsterdam",
    "host",
    "code_version",
    "ts_utc",
    "idempotency_key",
    "alert_kind",
)


def _configured_flags(source: Mapping[str, str]) -> tuple[str, str]:
    url = source.get(CAPTURE_ALERT_WEBHOOK_ENV, "").strip()
    authorization = source.get(CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV, "").strip()
    webhook = "yes" if url else "no"
    auth = "yes" if authorization else "no"
    return webhook, auth


def _public_payload(payload: Mapping[str, str]) -> dict[str, str]:
    return {key: payload[key] for key in _PUBLIC_PAYLOAD_KEYS if key in payload}


def _print_result(
    *,
    delivery: str,
    payload: Mapping[str, str] | None,
    webhook_configured: str,
    authorization_configured: str,
) -> None:
    print(f"delivery={delivery}")
    print(f"webhook_configured={webhook_configured}")
    print(f"authorization_configured={authorization_configured}")
    if payload is None:
        return
    public = _public_payload(payload)
    print(f"venue={public.get('venue', '')}")
    print(f"run_id={public.get('run_id', '')}")
    print(f"state={public.get('state', '')}")
    print(f"idempotency_key={public.get('idempotency_key', '')}")
    print("payload=" + json.dumps(public, ensure_ascii=True, sort_keys=True, separators=(",", ":")))


def _test_command(args: argparse.Namespace) -> int:
    source = resolve_capture_alert_source(None)
    webhook_configured, authorization_configured = _configured_flags(source)
    nonce = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if not args.send:
        payload = capture_operator_alert_payload(
            venue=args.venue,
            run_id=args.run_id,
            status="TEST",
            reason="operator_webhook_test",
            alert_kind="test",
            idempotency_nonce=nonce,
            error_class="CaptureAlertTest",
        )
        _print_result(
            delivery="dry_run",
            payload=payload,
            webhook_configured=webhook_configured,
            authorization_configured=authorization_configured,
        )
        return 0
    if webhook_configured != "yes":
        _print_result(
            delivery="failed",
            payload=None,
            webhook_configured=webhook_configured,
            authorization_configured=authorization_configured,
        )
        return 1
    result = emit_capture_operator_alert(
        venue=args.venue,
        run_id=args.run_id,
        status="TEST",
        reason="operator_webhook_test",
        alert_kind="test",
        idempotency_nonce=nonce,
        error_class="CaptureAlertTest",
        force=True,
    )
    delivered = result is not None and result.get("webhook_delivered") == "yes"
    _print_result(
        delivery="sent" if delivered else "failed",
        payload=result,
        webhook_configured=webhook_configured,
        authorization_configured=authorization_configured,
    )
    return 0 if delivered else 1


def _selected_lanes(venue: str | None) -> tuple[WatchLane, ...]:
    if venue is None:
        return WATCH_LANES
    chosen = tuple(lane for lane in WATCH_LANES if lane.venue == venue)
    return chosen


def _watch_command(args: argparse.Namespace) -> int:
    source = resolve_capture_alert_source(None)
    webhook_configured, authorization_configured = _configured_flags(source)
    now = datetime.now(UTC)
    lanes = _selected_lanes(args.venue)
    if args.venue is not None and not lanes:
        print("delivery=failed")
        print("reason=unknown_venue")
        return 2
    if args.tmux_session and args.venue is None and args.run_id is None:
        print("delivery=failed")
        print("reason=tmux_session_requires_venue_or_run_id")
        return 2
    finding_count = 0
    findings_failed = 0
    explicit = args.run_id is not None
    for lane in lanes:
        session = args.tmux_session or lane.tmux_session
        tmux_alive = tmux_session_alive(session) if args.check_tmux else None
        for run_dir in iter_watch_run_dirs(args.artifact_root, lane):
            if explicit and run_dir.name != args.run_id:
                continue
            process_alive = process_alive_for_run(run_dir.name) if args.check_process else None
            snapshot = load_capture_watch_snapshot(
                venue=lane.venue,
                run_dir=run_dir,
                tmux_alive=tmux_alive,
                process_alive=process_alive,
            )
            if snapshot is None:
                continue
            finding = evaluate_capture_watch(
                snapshot,
                now=now,
                stale_seconds=args.stale_seconds,
                live_stale_seconds=args.live_stale_seconds,
                startup_grace_seconds=args.startup_grace_seconds,
                failed_lookback_seconds=args.failed_lookback_seconds,
                explicit_run=explicit,
            )
            if finding is None:
                continue
            finding_count += 1
            payload = capture_operator_alert_payload(
                venue=finding.venue,
                run_id=finding.run_id,
                status=finding.state,
                reason=finding.reason,
                last_write=finding.last_write,
                code_version=finding.code_version,
            )
            if alert_receipt_exists(payload["idempotency_key"], resolve_receipt_dir()):
                _print_result(
                    delivery="already_delivered",
                    payload=payload,
                    webhook_configured=webhook_configured,
                    authorization_configured=authorization_configured,
                )
                continue
            if not args.send:
                _print_result(
                    delivery="dry_run",
                    payload=payload,
                    webhook_configured=webhook_configured,
                    authorization_configured=authorization_configured,
                )
                continue
            result = emit_capture_operator_alert(
                venue=finding.venue,
                run_id=finding.run_id,
                status=finding.state,
                reason=finding.reason,
                last_write=finding.last_write,
                code_version=finding.code_version,
            )
            delivered = result is not None and result.get("webhook_delivered") == "yes"
            if not delivered:
                findings_failed += 1
            _print_result(
                delivery="sent" if delivered else "failed",
                payload=result,
                webhook_configured=webhook_configured,
                authorization_configured=authorization_configured,
            )
    if finding_count == 0:
        print("delivery=none")
        print(f"webhook_configured={webhook_configured}")
        print(f"authorization_configured={authorization_configured}")
    return 1 if findings_failed else 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="capture_alert",
        description=(
            "PAPER capture-failure webhook. Dry-run unless --send. "
            "Never prints the webhook URL or authorization value."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)
    test = sub.add_parser("test", help="Print or send one labelled TEST alert.")
    test.add_argument("--send", action="store_true", help="POST the TEST payload.")
    test.add_argument("--venue", default="test")
    test.add_argument("--run-id", default="capture-alert-test")
    watch = sub.add_parser("watch", help="Check retains and optionally POST alerts.")
    watch.add_argument("--artifact-root", type=Path, required=True)
    watch.add_argument("--run-id", default=None)
    watch.add_argument(
        "--venue",
        choices=sorted({lane.venue for lane in WATCH_LANES}),
        default=None,
    )
    watch.add_argument("--tmux-session", default=None)
    watch.add_argument("--stale-seconds", type=float, default=DEFAULT_STALE_SECONDS)
    watch.add_argument("--live-stale-seconds", type=float, default=DEFAULT_LIVE_STALE_SECONDS)
    watch.add_argument(
        "--startup-grace-seconds",
        type=float,
        default=DEFAULT_STARTUP_GRACE_SECONDS,
    )
    watch.add_argument(
        "--failed-lookback-seconds",
        type=float,
        default=DEFAULT_FAILED_LOOKBACK_SECONDS,
    )
    watch.add_argument(
        "--check-tmux",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use tmux has-session (default: on). Never sends keys.",
    )
    watch.add_argument(
        "--check-process",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Read /proc command lines for the run id (default: on).",
    )
    watch.add_argument("--send", action="store_true", help="POST each finding.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "test":
        return _test_command(args)
    if args.command == "watch":
        return _watch_command(args)
    print("delivery=failed")
    print("reason=unknown_command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
