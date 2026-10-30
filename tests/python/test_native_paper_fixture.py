"""Keep the cross-language cockpit fixture bound to the real native producer."""

from pathlib import Path

import pytest
from tests.fixtures.native_paper.generate import create_demo_run


@pytest.mark.parametrize(("round_trips", "subdir"), [(1, ""), (60, "history")])
def test_native_cockpit_fixture_matches_the_engine(
    tmp_path: Path, round_trips: int, subdir: str
) -> None:
    written = create_demo_run(tmp_path, round_trips=round_trips)
    fixture = (
        Path(__file__).resolve().parents[1] / "fixtures" / "native_paper" / subdir / "native-demo"
    )
    for name in ("run-claim.json", "ledger.jsonl", "state.json", "health.json"):
        assert (written / name).read_bytes() == (fixture / name).read_bytes(), name
