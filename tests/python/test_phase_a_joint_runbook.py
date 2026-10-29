"""Offline tests for the Phase A joint 72h WSL campaign runbook."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = REPO_ROOT / "docs" / "runbooks" / "phase-a-72h-joint-retained-capture.md"
OFFICIAL_HOSTS = frozenset(
    {"hyperliquid.gitbook.io", "developers.binance.com", "docs.bitvavo.com", "docs.kraken.com"}
)


def _https_documentation_hosts(text: str) -> set[str]:
    """Count actual HTTPS references, not domain mentions inside unrelated URLs."""

    hosts: set[str] = set()
    for token in re.findall(r"https?://[^\s<>`]+", text, flags=re.IGNORECASE):
        try:
            url = urlsplit(token)
            if (
                url.scheme == "https"
                and url.hostname is not None
                and url.username is None
                and url.password is None
                and url.port in (None, 443)
            ):
                hosts.add(url.hostname)
        except ValueError:
            continue
    return hosts


def test_phase_a_runbook_links_official_https_documentation() -> None:
    assert OFFICIAL_HOSTS <= _https_documentation_hosts(RUNBOOK.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "text",
    [
        "docs.kraken.com",
        "http://docs.kraken.com/exchange",
        "https://docs.kraken.com.evil.example/exchange",
        "https://evil.example/docs.kraken.com",
        "https://evil.example/?source=docs.kraken.com",
        "https://docs.kraken.com@evil.example/exchange",
        "https://evil.example@docs.kraken.com/exchange",
        "https://docs.kraken.com:444/exchange",
        "https://docs.kraken.com:invalid/exchange",
        "https://[broken/docs.kraken.com",
    ],
)
def test_domain_mentions_and_misleading_urls_do_not_satisfy_references(text: str) -> None:
    assert "docs.kraken.com" not in _https_documentation_hosts(text)


def test_markdown_links_and_uppercase_official_hosts_are_supported() -> None:
    text = "[Kraken](https://DOCS.KRAKEN.COM/exchange) <https://docs.bitvavo.com:443/docs/>"
    assert _https_documentation_hosts(text) == {"docs.kraken.com", "docs.bitvavo.com"}


def test_phase_a_runbook_covers_four_lanes_and_hard_lessons() -> None:
    text = RUNBOOK.read_text(encoding="utf-8")
    assert "active evidence host" in text.lower() or "Active evidence host" in text
    assert "PARTIAL" in text
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
    assert "BTC/USD" in text
    assert "not BTC/EUR" in text or "not BTC/EUR" in text.lower()
    assert "TerraPC" in text
    assert "historical" in text.lower()
