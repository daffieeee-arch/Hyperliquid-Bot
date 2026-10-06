"""Walk-forward folds plus a final untouched holdout suffix."""

from __future__ import annotations

from dataclasses import dataclass

from research.harness.errors import HarnessError
from research.harness.spec import SplitSpec


@dataclass(frozen=True, slots=True)
class Fold:
    index: int
    train_start: int
    train_end: int
    test_start: int
    test_end: int


def walk_forward(n_rows: int, split: SplitSpec) -> tuple[tuple[Fold, ...], tuple[int, int]]:
    """Return test folds on the prefix and the holdout index range.

    The holdout is the last `holdout_bars` rows. Folds never extend into it.
    Train windows are recorded so the split is auditable; this harness does not
    fit parameters on them. An empty fold tuple means the prefix is too short.
    """

    if split.holdout_bars >= n_rows:
        return (), (0, n_rows)
    prefix = n_rows - split.holdout_bars
    holdout = (prefix, n_rows)
    folds: list[Fold] = []
    test_start = split.train_bars
    while test_start + split.test_bars <= prefix:
        if split.method == "expanding":
            train_start = 0
        elif split.method == "rolling":
            train_start = test_start - split.train_bars
        else:
            raise HarnessError("spec", "split.method must be expanding or rolling.")
        folds.append(
            Fold(
                index=len(folds),
                train_start=train_start,
                train_end=test_start,
                test_start=test_start,
                test_end=test_start + split.test_bars,
            )
        )
        test_start += split.test_bars
    return tuple(folds), holdout
