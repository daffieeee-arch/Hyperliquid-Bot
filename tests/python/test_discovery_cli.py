"""Discovery selection and pre-decode refusal invariants, using only fixtures."""

from __future__ import annotations

import subprocess
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

from hyperliquid_bot.local_mode import UnsafeTradingModeError
from research import discovery_cli as cli
from research.harness.errors import IntegrityError


def result(name: str, base: float, stress: float, diagnostic: float) -> dict[str, Any]:
    return {
        "config": {"name": name},
        "verdict": "reject",
        **{
            cost: {
                "segments": {
                    "development": {"net_return": score},
                    "diagnostic": {"net_return": diagnostic},
                },
                "ledger": [{"equity": 10000}],
                "trades": [{"quantity": 1}],
            }
            for cost, score in (("base", base), ("stress", stress))
        },
    }


def test_ranking_uses_worst_development_cost_and_lexical_tie() -> None:
    rows = [
        result("zeta", 0.05, 0.02, 100),
        result("alpha", 0.03, 0.02, -100),
        result("base-only-winner", 100, -0.01, 1000),
        {"config": {"name": "failed"}, "verdict": "failed"},
    ]
    assert cli.development_rank(rows) == ["alpha", "zeta"]


def test_diagnostic_outcomes_and_verdict_cannot_choose_finalists() -> None:
    rows = [result("first", 0.03, 0.02, -1), result("second", 0.01, 0, 1)]
    expected = cli.development_rank(rows)
    for row in rows:
        row["verdict"] = "exploratory_candidate"
        for cost in ("base", "stress"):
            row[cost]["segments"]["diagnostic"]["net_return"] *= -1_000_000
    assert cli.development_rank(rows) == expected == ["first", "second"]


def test_compact_preserves_metrics_and_original_full_trial() -> None:
    original = result("test", 0.01, -0.01, 0.5)
    saved = deepcopy(original)
    compact = cli.compact(original)
    assert original == saved
    for cost in ("base", "stress"):
        assert "ledger" not in compact[cost]
        assert "trades" not in compact[cost]
        assert compact[cost]["segments"] == original[cost]["segments"]
    failure = {"config": {"name": "bad"}, "verdict": "failed", "error": {"type": "ValueError"}}
    assert cli.compact(failure) == failure


def unexpected(*args: Any, **kwargs: Any) -> Any:
    pytest.fail("operation must be refused before source/data/output work")


@pytest.mark.parametrize("mode", ["LIVE", "SHADOW", "TESTNET", "paper", "", " PAPER"])
def test_run_refuses_unapproved_mode_before_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    monkeypatch.setenv("TRADING_MODE", mode)
    monkeypatch.setattr(cli, "verify_code", unexpected)
    monkeypatch.setattr(cli, "observations", unexpected)
    monkeypatch.setattr(cli, "PrivateOutput", unexpected)
    output = tmp_path / "output"
    with pytest.raises(UnsafeTradingModeError):
        cli.run_batch(tmp_path / "input", "0" * 64, "1" * 40, output)
    assert not output.exists()


def test_source_commit_mismatch_refuses_before_identity_or_financial_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TRADING_MODE", raising=False)
    monkeypatch.setattr(subprocess, "check_output", lambda *args, **kwargs: "0" * 40)
    monkeypatch.setattr(cli, "source_identity", unexpected)
    monkeypatch.setattr(cli, "read_bytes", unexpected)
    monkeypatch.setattr(cli, "observations", unexpected)
    monkeypatch.setattr(cli, "PrivateOutput", unexpected)
    with pytest.raises(IntegrityError, match="actual checkout HEAD"):
        cli.run_batch(tmp_path / "input", "0" * 64, "1" * 40, tmp_path / "output")


@pytest.mark.parametrize(
    "drift",
    [" M src/research/repaired_replay.py\n", "?? src/research/repaired_replay.py\n"],
)
def test_dirty_imported_dependency_refuses_before_identity_or_decode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, drift: str
) -> None:
    commit = "1" * 40
    calls: list[list[str]] = []

    def git_output(command: list[str], **kwargs: Any) -> str:
        calls.append(command)
        return commit if command == ["git", "rev-parse", "HEAD"] else drift

    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setattr(subprocess, "check_output", git_output)
    monkeypatch.setattr(cli, "source_identity", unexpected)
    monkeypatch.setattr(cli, "read_bytes", unexpected)
    monkeypatch.setattr(cli, "observations", unexpected)
    monkeypatch.setattr(cli, "PrivateOutput", unexpected)
    with pytest.raises(IntegrityError, match="source tree must be clean"):
        cli.run_batch(tmp_path / "input", "0" * 64, commit, tmp_path / "output")
    assert calls == [
        ["git", "rev-parse", "HEAD"],
        ["git", "status", "--porcelain", "--untracked-files=all", "--", "src"],
    ]


@pytest.mark.parametrize("commit", ["main", "a" * 39, "G" * 40, "a" * 41])
def test_full_source_commit_is_required_before_git(
    monkeypatch: pytest.MonkeyPatch, commit: str
) -> None:
    monkeypatch.setattr(subprocess, "check_output", unexpected)
    with pytest.raises(IntegrityError, match="full source commit"):
        cli.verify_code(commit)


def test_input_hash_mismatch_refuses_before_decode_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TRADING_MODE", "PAPER")
    monkeypatch.setattr(cli, "verify_code", lambda commit: {})
    monkeypatch.setattr(cli, "read_bytes", lambda path, limit: b"{}")
    monkeypatch.setattr(cli, "document", unexpected)
    monkeypatch.setattr(cli, "observations", unexpected)
    monkeypatch.setattr(cli, "PrivateOutput", unexpected)
    with pytest.raises(IntegrityError, match="operator-pinned"):
        cli.run_batch(tmp_path / "input", "0" * 64, "1" * 40, tmp_path / "output")


def test_cli_returns_failure_for_unapproved_mode_without_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("TRADING_MODE", "LIVE")
    monkeypatch.setattr(cli, "verify_code", unexpected)
    output = tmp_path / "output"
    assert (
        cli.main(
            [
                "run",
                "--input",
                str(tmp_path / "input"),
                "--expected-input-sha256",
                "0" * 64,
                "--source-commit",
                "1" * 40,
                "--output",
                str(output),
            ]
        )
        == 2
    )
    assert "PAPER" in capsys.readouterr().err
    assert not output.exists()
