"""Offline tests for the Phase A joint 72h WSL campaign runbook."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "phase-a-72h-joint-retained-capture.md"


def test_phase_a_runbook_covers_four_lanes_and_hard_lessons() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "prepare-only" in text
    assert "DATA-1A" in text and "DATA-1F" in text
    assert "DATA-1E" in text and "DATA-1B" in text
    assert "hl-capture" in text
    assert "bn-capture" in text
    assert "bv-capture" in text
    assert "kr-capture" in text
    assert "259200" in text
    assert "retained_72h" in text
    assert "DATA-2A" in text
    assert "trickle" in text
    assert "100 Mbit" in text
    assert "5 Mbit" in text
    assert "Chupa" in text
    assert "PHASE_A_CHUPA_OK" in text
    assert "deferred" in text
    assert "ETH" in text and "SOL" in text
    assert "pytest-hl-isolated" in text or "isolated fake" in text
    assert "pkill" in text
    assert "Netcup Ubuntu 24.04" in text
    assert "OKX" in text
    assert "$HOME" in text
    assert "192.168.x.x" in text or "<LAN-IP>" in text
    assert "standby-timeout-ac 0" in text
    assert "wsl --shutdown" in text
    assert "hyperliquid.gitbook.io" in text
    assert "developers.binance.com" in text
    assert "docs.bitvavo.com" in text
    assert "docs.kraken.com" in text
    assert "BTC/USD" in text
    assert "not BTC/EUR" in text or "not BTC/EUR" in text.lower()
    assert "campaign has **not** started" in text or "has **not** started" in text
