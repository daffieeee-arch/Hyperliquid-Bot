"""Unit tests for DATA-1 status reconnect/gap hints. Counts are never invented."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
HINTS_PATH = REPO_ROOT / "scripts" / "data1_status_hints.py"


def _hints() -> Any:
    spec = importlib.util.spec_from_file_location("data1_status_hints", HINTS_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_health_profiles_win_and_malformed_health_stays_na(tmp_path: Path) -> None:
    hints = _hints()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "capture-health.json").write_text(
        json.dumps(
            {
                "status": "OPERATOR_STOP",
                "transport_profiles": [
                    {"transport_profile": "usdm_public", "reconnects": 4, "gaps": 4},
                    {"transport_profile": "spot", "reconnects": 1, "gaps": 0},
                ],
            }
        ),
        encoding="utf-8",
    )
    lines = hints.render_transport_hints(run_dir, "20260906t101559z-live-retained")
    assert lines == [
        "transport_hints_source=health",
        "transport_reconnects=spot:1,usdm_public:4",
        "transport_gaps=spot:0,usdm_public:4",
    ]

    (run_dir / "capture-health.json").write_text(
        json.dumps({"status": "OPERATOR_STOP", "transport_profiles": [{"reconnects": "many"}]}),
        encoding="utf-8",
    )
    assert hints.render_transport_hints(run_dir, "x") == [
        "transport_hints_source=n/a",
        "transport_reconnects=n/a",
        "transport_gaps=n/a",
    ]


def test_capture_log_counts_usdm_public_reconnects_and_leaves_gaps_na(tmp_path: Path) -> None:
    hints = _hints()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "capture-20260906t101559z-live-retained.log").write_text(
        "\n".join(
            [
                "binance session_start transport_profile=spot",
                "binance session_start transport_profile=usdm_public",
                "binance reconnect transport_profile=usdm_public attempt=1",
                "binance reconnect transport_profile=usdm_public attempt=2",
                "unrelated warning without a profile",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    lines = hints.render_transport_hints(run_dir, "20260906t101559z-live-retained")
    assert lines[0] == "transport_hints_source=capture-log"
    assert lines[1] == "transport_reconnects=spot:0,usdm_public:2"
    assert lines[2] == "transport_gaps=n/a"


def test_empty_or_missing_log_is_na(tmp_path: Path) -> None:
    hints = _hints()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    assert hints.render_transport_hints(run_dir, "missing") == [
        "transport_hints_source=n/a",
        "transport_reconnects=n/a",
        "transport_gaps=n/a",
    ]
    (run_dir / "capture-missing.log").write_text("   \n", encoding="utf-8")
    assert hints.render_transport_hints(run_dir, "missing") == [
        "transport_hints_source=n/a",
        "transport_reconnects=n/a",
        "transport_gaps=n/a",
    ]
