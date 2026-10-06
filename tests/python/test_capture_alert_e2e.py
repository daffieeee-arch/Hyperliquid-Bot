"""Capture-failure alerts against a local webhook, including the external checker."""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest

from hyperliquid_bot.capture_alert_cli import main
from hyperliquid_bot.capture_alert_watch import (
    WATCH_LANES,
    CaptureWatchSnapshot,
    evaluate_capture_watch,
    load_capture_watch_snapshot,
)
from hyperliquid_bot.capture_operator_alert import (
    CAPTURE_ALERT_ENV_FILE_ENV,
    CAPTURE_ALERT_RECEIPT_DIR_ENV,
    CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV,
    CAPTURE_ALERT_WEBHOOK_ENV,
    emit_capture_operator_alert,
    write_health_then_alert,
)
from hyperliquid_bot.reconstructable_paths import DATA1A_CLAIM_NAME, DATA1A_HEALTH_NAME

_TOKEN = "Bearer unit-test-token"
_URL_MARKER = "https://example.invalid/do-not-log-this-webhook"


class _WebhookServer(ThreadingHTTPServer):
    def __init__(self, responses: list[int]) -> None:
        super().__init__(("127.0.0.1", 0), _WebhookHandler)
        self.responses = list(responses)
        self.requests: list[tuple[str, dict[str, str], bytes]] = []


class _WebhookHandler(BaseHTTPRequestHandler):
    server: _WebhookServer

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        headers = {
            "Authorization": self.headers.get("Authorization", ""),
            "Idempotency-Key": self.headers.get("Idempotency-Key", ""),
            "Content-Type": self.headers.get("Content-Type", ""),
        }
        self.server.requests.append((self.path, headers, body))
        status = 204
        if self.server.responses:
            status = self.server.responses.pop(0)
        self.send_response(status)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


@pytest.fixture
def webhook_server() -> Iterator[_WebhookServer]:
    server = _WebhookServer([204])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    thread.join(timeout=2)


def _url(server: _WebhookServer) -> str:
    address = server.server_address
    host = address[0]
    port = address[1]
    if isinstance(host, bytes):
        host = host.decode("ascii")
    return f"http://{host}:{port}/hook"


def _write_env(path: Path, url: str, token: str = _TOKEN) -> None:
    path.write_text(
        "\n".join(
            (
                "# PAPER capture-fail webhook. Not a shell script.",
                f"export {CAPTURE_ALERT_WEBHOOK_ENV}={url}",
                f"{CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV}='{token}'",
                "UNRELATED_SECRET=should-not-load",
                "",
            )
        ),
        encoding="utf-8",
    )


def _claim(run_dir: Path, *, code_version: str = "abc1234") -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / DATA1A_CLAIM_NAME).write_text(
        json.dumps({"code_version": code_version, "state": "STARTED_FAIL_CLOSED"}) + "\n",
        encoding="utf-8",
    )


def _health(run_dir: Path, status: str, **extra: object) -> None:
    body: dict[str, object] = {"status": status, "kind": "capture-health"}
    body.update(extra)
    (run_dir / DATA1A_HEALTH_NAME).write_text(
        json.dumps(body) + "\n",
        encoding="utf-8",
    )


def _touch(path: Path, *, age_seconds: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"")
    moment = (datetime.now(UTC) - timedelta(seconds=age_seconds)).timestamp()
    os.utime(path, (moment, moment))


def _snapshot(
    *,
    venue: str = "binance",
    run_id: str = "20260920t090000z-watch",
    health_status: str | None = None,
    health_reason: str = "capture_health_failed",
    claim_age: float | None = 30,
    parquet_age: float | None = 10,
    live_age: float | None = 5,
    outage_reason: str | None = None,
    tmux_alive: bool | None = None,
    process_alive: bool | None = True,
    health_age: float | None = None,
) -> CaptureWatchSnapshot:
    now = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)

    def ago(seconds: float | None) -> datetime | None:
        if seconds is None:
            return None
        return now - timedelta(seconds=seconds)

    return CaptureWatchSnapshot(
        venue=venue,
        run_id=run_id,
        run_dir=Path("/tmp/unused"),
        health_status=health_status,
        health_mtime=ago(health_age if health_age is not None else claim_age),
        health_reason=health_reason,
        claim_mtime=ago(claim_age),
        code_version="abc1234",
        last_parquet_mtime=ago(parquet_age),
        live_published=ago(live_age),
        outage_reason=outage_reason,
        tmux_alive=tmux_alive,
        process_alive=process_alive,
    )


_NOW = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def test_evaluate_terminal_success_is_quiet() -> None:
    for status in ("COMPLETED", "OPERATOR_STOP"):
        finding = evaluate_capture_watch(
            _snapshot(health_status=status),
            now=_NOW,
        )
        assert finding is None


def test_evaluate_failed_dead_stale_and_outage() -> None:
    failed = evaluate_capture_watch(
        _snapshot(health_status="FAILED", health_reason="BinanceTransportError"),
        now=_NOW,
    )
    assert failed is not None
    assert failed.state == "FAILED"
    assert failed.reason == "BinanceTransportError"

    dead = evaluate_capture_watch(
        _snapshot(process_alive=False, tmux_alive=False, parquet_age=None, live_age=None),
        now=_NOW,
    )
    assert dead is not None
    assert dead.state == "DEAD"
    assert dead.reason == "process_absent"

    tmux_dead = evaluate_capture_watch(
        _snapshot(process_alive=None, tmux_alive=False, parquet_age=None, live_age=None),
        now=_NOW,
    )
    assert tmux_dead is not None
    assert tmux_dead.state == "DEAD"
    assert tmux_dead.reason == "tmux_session_absent"

    stale_live = evaluate_capture_watch(
        _snapshot(live_age=90, parquet_age=10),
        now=_NOW,
    )
    assert stale_live is not None
    assert stale_live.state == "STALE"
    assert stale_live.reason == "live_status_stale"

    stale_parquet = evaluate_capture_watch(
        _snapshot(live_age=5, parquet_age=400),
        now=_NOW,
    )
    assert stale_parquet is not None
    assert stale_parquet.reason == "parquet_stale"

    quiet = evaluate_capture_watch(
        _snapshot(
            parquet_age=None,
            live_age=None,
            claim_age=10,
            process_alive=True,
        ),
        now=_NOW,
    )
    assert quiet is None

    no_data = evaluate_capture_watch(
        _snapshot(parquet_age=None, live_age=None, claim_age=600, process_alive=True),
        now=_NOW,
    )
    assert no_data is not None
    assert no_data.reason == "no_market_data"

    outage = evaluate_capture_watch(
        _snapshot(outage_reason="BinanceTransportError: ended"),
        now=_NOW,
    )
    assert outage is not None
    assert outage.state == "FAILED"


def test_evaluate_skips_ancient_runs_unless_named() -> None:
    ancient = _snapshot(
        health_status="FAILED",
        claim_age=200_000,
        parquet_age=200_000,
        live_age=200_000,
        health_age=200_000,
    )
    assert evaluate_capture_watch(ancient, now=_NOW) is None
    named = evaluate_capture_watch(ancient, now=_NOW, explicit_run=True)
    assert named is not None
    assert named.state == "FAILED"


def test_env_file_posts_without_logging_secrets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    env_file = tmp_path / "capture-alert.env"
    _write_env(env_file, _URL_MARKER)
    monkeypatch.delenv(CAPTURE_ALERT_WEBHOOK_ENV, raising=False)
    monkeypatch.delenv(CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV, raising=False)
    monkeypatch.setenv(CAPTURE_ALERT_ENV_FILE_ENV, str(env_file))
    posted: list[tuple[str, bytes, Mapping[str, str]]] = []

    def poster(url: str, body: bytes, timeout: float, headers: Mapping[str, str]) -> int:
        del timeout
        posted.append((url, body, dict(headers)))
        return 204

    with caplog.at_level("INFO", logger="hyperliquid_bot.capture"):
        payload = emit_capture_operator_alert(
            venue="kraken",
            run_id="20260920t090000z-env",
            status="FAILED",
            reason="capture_health_failed",
            environ=None,
            webhook_poster=poster,
            receipt_dir=tmp_path / "receipts",
        )
    assert payload is not None
    assert payload["webhook_delivered"] == "yes"
    assert posted[0][0] == _URL_MARKER
    assert posted[0][2]["Authorization"] == _TOKEN
    assert _URL_MARKER not in caplog.text
    assert _TOKEN not in caplog.text
    assert "UNRELATED_SECRET" not in caplog.text
    again = emit_capture_operator_alert(
        venue="kraken",
        run_id="20260920t090000z-env",
        status="FAILED",
        reason="capture_health_failed",
        environ=None,
        webhook_poster=poster,
        receipt_dir=tmp_path / "receipts",
    )
    assert again is not None
    assert again["webhook_delivered"] == "yes"
    assert len(posted) == 1


def test_missing_webhook_is_logged_and_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.delenv(CAPTURE_ALERT_WEBHOOK_ENV, raising=False)
    monkeypatch.delenv(CAPTURE_ALERT_ENV_FILE_ENV, raising=False)
    with caplog.at_level("WARNING", logger="hyperliquid_bot.capture"):
        payload = emit_capture_operator_alert(
            venue="hyperliquid",
            run_id="20260920t090000z-unset",
            status="DEAD",
            reason="process_absent",
            environ=None,
        )
    assert payload is not None
    assert payload["state"] == "DEAD"
    assert payload["webhook_delivered"] == "no"
    assert "capture_operator_alert_webhook_unset" in caplog.text


def test_health_write_failure_still_emits(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def _emit(**kwargs: object) -> dict[str, str]:
        calls.append(str(kwargs.get("status")))
        return {"webhook_delivered": "yes"}

    monkeypatch.setattr(
        "hyperliquid_bot.capture_operator_alert.emit_capture_operator_alert",
        _emit,
    )

    def _write() -> None:
        raise OSError("disk")

    with pytest.raises(OSError, match="disk"):
        write_health_then_alert(
            should_write_health=True,
            write_health=_write,
            status="FAILED",
            venue="bitvavo",
            run_id="20260920t090000z-health",
            error=RuntimeError("boom"),
        )
    assert calls == ["FAILED"]


def test_fake_server_retries_then_records_receipt(
    webhook_server: _WebhookServer,
    tmp_path: Path,
) -> None:
    webhook_server.responses[:] = [500, 500, 204]
    payload = emit_capture_operator_alert(
        venue="binance",
        run_id="20260920t090000z-retry",
        status="FAILED",
        reason="capture_health_failed",
        environ={
            CAPTURE_ALERT_WEBHOOK_ENV: _url(webhook_server),
            CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV: _TOKEN,
        },
        retry_backoff_seconds=(0.0, 0.0),
        receipt_dir=tmp_path / "receipts",
    )
    assert payload is not None
    assert payload["webhook_delivered"] == "yes"
    assert len(webhook_server.requests) == 3
    assert webhook_server.requests[-1][1]["Authorization"] == _TOKEN
    body = cast(dict[str, str], json.loads(webhook_server.requests[-1][2]))
    assert body["state"] == "FAILED"
    assert body["idempotency_key"] == webhook_server.requests[-1][1]["Idempotency-Key"]
    assert _TOKEN not in webhook_server.requests[-1][2].decode("utf-8")


def test_fake_server_does_not_retry_auth_reject(webhook_server: _WebhookServer) -> None:
    webhook_server.responses[:] = [401, 204]
    payload = emit_capture_operator_alert(
        venue="bitvavo",
        run_id="20260920t090000z-auth",
        status="STALE",
        reason="parquet_stale",
        environ={CAPTURE_ALERT_WEBHOOK_ENV: _url(webhook_server)},
        retry_backoff_seconds=(0.0, 0.0),
    )
    assert payload is not None
    assert payload["webhook_delivered"] == "no"
    assert payload["state"] == "STALE"
    assert len(webhook_server.requests) == 1


def _lane_run(root: Path, venue: str, product_prefix: tuple[str, ...], run_id: str) -> Path:
    run_dir = root.joinpath(*product_prefix, run_id)
    _claim(run_dir)
    return run_dir


def test_watch_cli_posts_each_failure_mode_once(
    webhook_server: _WebhookServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / "capture-alert.env"
    _write_env(env_file, _url(webhook_server))
    monkeypatch.setenv(CAPTURE_ALERT_ENV_FILE_ENV, str(env_file))
    monkeypatch.delenv(CAPTURE_ALERT_WEBHOOK_ENV, raising=False)
    monkeypatch.delenv(CAPTURE_ALERT_WEBHOOK_AUTHORIZATION_ENV, raising=False)
    receipt_dir = tmp_path / "receipts"
    monkeypatch.setenv(CAPTURE_ALERT_RECEIPT_DIR_ENV, str(receipt_dir))
    root = tmp_path / "data-capture"
    failed = _lane_run(
        root,
        "binance",
        ("data-1f", "binance", "BTCUSDT"),
        "20260920t090000z-failed",
    )
    _health(
        failed,
        "FAILED",
        exception_class="BinanceTransportError",
        preserved_profile_error_message="Binance public reconnect bound was exhausted.",
    )
    dead = _lane_run(
        root,
        "hyperliquid",
        ("data-1a", "hyperliquid", "BTC-PERP"),
        "20260920t090000z-dead",
    )
    _touch(dead / DATA1A_CLAIM_NAME, age_seconds=600)
    stale = _lane_run(root, "kraken", ("data-1b", "kraken", "BTC-USD"), "20260920t090000z-stale")
    part = stale / "raw" / "part-0001.parquet"
    _touch(part, age_seconds=900)
    quiet = _lane_run(root, "bitvavo", ("data-1d", "bitvavo", "BTC-EUR"), "20260920t090000z-done")
    _health(quiet, "COMPLETED")
    outage = _lane_run(
        root,
        "bitvavo",
        ("data-1e", "bitvavo", "BTC-EUR"),
        "20260920t090000z-outage",
    )
    (outage / "capture-live.json").write_text(
        json.dumps(
            {
                "published_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "definitive_outage": {
                    "error_class": "TimeoutError",
                    "error_message": "Bitvavo socket ended early.",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    fresh_part = outage / "raw" / "part-0001.parquet"
    _touch(fresh_part, age_seconds=5)

    code = main(
        [
            "watch",
            "--artifact-root",
            str(root),
            "--send",
            "--no-check-tmux",
            "--no-check-process",
            "--stale-seconds",
            "180",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert _url(webhook_server) not in captured.out
    assert _TOKEN not in captured.out
    bodies = [cast(dict[str, str], json.loads(item[2])) for item in webhook_server.requests]
    states = {(body["venue"], body["run_id"], body["state"], body["reason"]) for body in bodies}
    assert (
        "binance",
        "20260920t090000z-failed",
        "FAILED",
        "BinanceTransportError: Binance public reconnect bound was exhausted.",
    ) in states
    assert ("hyperliquid", "20260920t090000z-dead", "STALE", "no_market_data") in states
    assert ("kraken", "20260920t090000z-stale", "STALE", "parquet_stale") in states
    assert (
        "bitvavo",
        "20260920t090000z-outage",
        "FAILED",
        "TimeoutError: Bitvavo socket ended early.",
    ) in states
    assert all(body["run_id"] != "20260920t090000z-done" for body in bodies)
    assert all(body["code_version"] == "abc1234" for body in bodies)
    assert all(body["last_write_utc"] != "" for body in bodies)
    assert all("last_write_amsterdam" in body for body in bodies)

    webhook_server.requests.clear()
    again = main(
        [
            "watch",
            "--artifact-root",
            str(root),
            "--send",
            "--no-check-tmux",
            "--no-check-process",
        ]
    )
    assert again == 0
    assert webhook_server.requests == []
    second = capsys.readouterr()
    assert "delivery=already_delivered" in second.out


def test_watch_cli_dead_process_posts(
    webhook_server: _WebhookServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / "capture-alert.env"
    _write_env(env_file, _url(webhook_server))
    monkeypatch.setenv(CAPTURE_ALERT_ENV_FILE_ENV, str(env_file))
    monkeypatch.delenv(CAPTURE_ALERT_WEBHOOK_ENV, raising=False)
    monkeypatch.setenv(CAPTURE_ALERT_RECEIPT_DIR_ENV, str(tmp_path / "receipts"))
    root = tmp_path / "data-capture"
    run_dir = _lane_run(
        root,
        "binance",
        ("data-1a", "hyperliquid", "BTC-PERP"),
        "20260920t090100z-killed",
    )
    del run_dir
    code = main(
        [
            "watch",
            "--artifact-root",
            str(root),
            "--venue",
            "hyperliquid",
            "--run-id",
            "20260920t090100z-killed",
            "--send",
            "--no-check-tmux",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    assert _TOKEN not in captured.out
    assert len(webhook_server.requests) == 1
    body = cast(dict[str, str], json.loads(webhook_server.requests[0][2]))
    assert body["state"] == "DEAD"
    assert body["reason"] == "process_absent"
    assert body["venue"] == "hyperliquid"


def test_alert_test_dry_run_prints_payload_without_secrets_or_post(
    webhook_server: _WebhookServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / "capture-alert.env"
    _write_env(env_file, _url(webhook_server))
    monkeypatch.setenv(CAPTURE_ALERT_ENV_FILE_ENV, str(env_file))
    monkeypatch.delenv(CAPTURE_ALERT_WEBHOOK_ENV, raising=False)
    code = main(["test", "--venue", "binance", "--run-id", "capture-alert-test"])
    captured = capsys.readouterr()
    assert code == 0
    assert "delivery=dry_run" in captured.out
    assert "webhook_configured=yes" in captured.out
    assert "authorization_configured=yes" in captured.out
    assert '"state":"TEST"' in captured.out
    assert '"alert_kind":"test"' in captured.out
    assert "operator_webhook_test" in captured.out
    assert _url(webhook_server) not in captured.out
    assert _TOKEN not in captured.out
    assert webhook_server.requests == []


def test_alert_test_send_posts_labelled_payload(
    webhook_server: _WebhookServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    env_file = tmp_path / "capture-alert.env"
    _write_env(env_file, _url(webhook_server))
    monkeypatch.setenv(CAPTURE_ALERT_ENV_FILE_ENV, str(env_file))
    monkeypatch.delenv(CAPTURE_ALERT_WEBHOOK_ENV, raising=False)
    monkeypatch.setenv(CAPTURE_ALERT_RECEIPT_DIR_ENV, str(tmp_path / "receipts"))
    code = main(["test", "--send", "--venue", "binance", "--run-id", "capture-alert-test"])
    captured = capsys.readouterr()
    assert code == 0
    assert "delivery=sent" in captured.out
    assert _TOKEN not in captured.out
    assert _url(webhook_server) not in captured.out
    assert len(webhook_server.requests) == 1
    body = cast(dict[str, str], json.loads(webhook_server.requests[0][2]))
    assert body["state"] == "TEST"
    assert body["alert_kind"] == "test"
    assert body["reason"] == "operator_webhook_test"
    assert body["error_class"] == "CaptureAlertTest"
    assert webhook_server.requests[0][1]["Authorization"] == _TOKEN
    assert webhook_server.requests[0][1]["Idempotency-Key"] == body["idempotency_key"]


def test_load_snapshot_reads_health_reason(tmp_path: Path) -> None:
    lane = next(item for item in WATCH_LANES if item.venue == "binance")
    run_dir = tmp_path.joinpath(*lane.relative_prefix, "20260920t090000z-snap")
    _claim(run_dir, code_version="deadbeef")
    _health(
        run_dir,
        "FAILED",
        exception_class="BinanceTransportError",
        preserved_profile_error_message="ended",
    )
    snapshot = load_capture_watch_snapshot(
        venue=lane.venue,
        run_dir=run_dir,
        tmux_alive=None,
        process_alive=None,
    )
    assert snapshot is not None
    assert snapshot.health_reason == "BinanceTransportError: ended"
    assert snapshot.code_version == "deadbeef"
