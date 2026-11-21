"""Power of the S4 decision rules: how often a config with a given true Sharpe passes.

PAPER research only. A simulation, not data: it reads no panel, no archive and
no result. It backs the Power table under Limitations in exp_xsmom_usdm and
exp_xscarry_usdm.

    PYTHONPATH=src uv run --frozen python docs/experiments/exp_xs_usdm_power.py

The model, stated so the table can be checked:

- Each config's net return at 1.0x accrues day by day, normal and independent
  over time, at 40% a year for every config. A period holds ``horizon`` days
  of return and lasts ``horizon + 1`` days (decide, fill a day later, exit
  ``horizon`` days after the fill), on the pre-registered split: 16 folds of
  91 days from day 371 and a 608-day holdout from day 1,827, so 176 (h7) or
  96 (h14) validation periods and 75 or 40 holdout periods. No decision is
  skipped.
- A config's true annualized net Sharpe ``S`` is its per-period Sharpe times
  ``sqrt(365 / (horizon + 1))``.
- The four configs of a family are two quantiles at each of two horizons.
  Their daily noise shares a family factor (weight 0.5 of the variance) and
  a horizon factor (0.35), so two configs of one horizon correlate at 0.85
  and configs of different horizons at 0.5.
- Validation, as the harness does it: per config the larger of the iid t and
  the Newey-West HAC one-sided p-values (the harness's lag rule), Holm across
  the four at alpha 0.05; a config survives when its Holm-adjusted p-value is
  at most 0.05 and its mean stays positive at the 1.5x and 2.0x stresses; the
  survivor with the highest mean per period is selected.
- Holdout pass, the documents' rule: the selected config's holdout mean is
  positive at 1.0x and 1.5x and its conservative p-value at 1.0x is at most
  0.10. The harness's own label (p at most 0.05, positive at 2.0x) is counted
  too.
- Stress: 1.5x and 2.0x cost half and one more 20 bp round trip a period.
  Funding stress is not modelled, nor are rule 4 (source of the return) and
  rule 5 (data), so the figures are upper bounds.

Scenarios: the config under test (q-a-h7 or q-a-h14) has Sharpe ``S`` and the
other three 0; and all four configs at ``S``. The p-value arithmetic is the
harness's (``student_t_upper_tail``, ``holm``); the HAC standard error is
checked against ``newey_west_mean_test`` on the first draws.

Sensitivity runs change one model choice each; the random draws are the same
as the default run's for the same ``--draws``:

- ``--independent``: no family or horizon factor, so the four configs are
  independent;
- ``--annual-vol 0.15``: another book volatility (the costs stay 20 bp);
- ``--iid-only``: the iid t-test's p-value alone, in validation and holdout;
- ``--draws N``: fewer or more draws.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from collections.abc import Sequence
from dataclasses import dataclass

from research.harness.stats import holm, newey_west_mean_test, student_t_upper_tail

SEED = 20261010
DRAWS = 50_000
SHARPES = (0.0, 0.5, 1.0, 1.25, 1.5, 2.0)
ALPHA = 0.05
HOLDOUT_P = 0.10
ANNUAL_VOL = 0.40
ROUND_TRIP = 0.002  # 20 bp a period at 1.0x; 1.5x adds half of it, 2.0x all of it
FAMILY_SHARE, HORIZON_SHARE = 0.5, 0.35
TRAIN, TEST, FOLDS, HOLDOUT, AXIS = 371, 91, 16, 608, 2435
HORIZONS = (7, 7, 14, 14)  # configs: q-a-h7, q-b-h7, q-a-h14, q-b-h14
LATENCY = 1

Span = tuple[int, int]  # days of return a period holds: (first, last + 1)


@dataclass(frozen=True, slots=True)
class Model:
    """The model choices a sensitivity run may change."""

    draws: int = DRAWS
    annual_vol: float = ANNUAL_VOL
    family_share: float = FAMILY_SHARE
    horizon_share: float = HORIZON_SHARE
    iid_only: bool = False

    @property
    def day_vol(self) -> float:
        return self.annual_vol / math.sqrt(365.0)


def periods(start: int, end: int, horizon: int) -> list[Span]:
    """The harness's schedule with no skip: exit strictly inside the window."""

    out: list[Span] = []
    decision = start
    while decision + LATENCY + horizon < end:
        entry = decision + LATENCY
        out.append((entry + 1, entry + horizon + 1))
        decision = entry + horizon
    return out


def windows() -> tuple[list[Span], list[Span]]:
    folds = [(TRAIN + TEST * k, TRAIN + TEST * (k + 1)) for k in range(FOLDS)]
    return folds, [(AXIS - HOLDOUT, AXIS)]


def schedule(horizon: int) -> tuple[list[Span], list[Span]]:
    folds, holdout = windows()
    validation = [span for start, end in folds for span in periods(start, end, horizon)]
    return validation, [span for start, end in holdout for span in periods(start, end, horizon)]


def daily_mean(sharpe: float, horizon: int, day_vol: float) -> float:
    """Daily drift that gives an annualized per-period Sharpe of ``sharpe``."""

    per_period = sharpe / math.sqrt(365.0 / (horizon + 1))
    return per_period * day_vol / math.sqrt(horizon)


def moments(values: list[float]) -> tuple[float, float, float]:
    """Mean, iid standard error and Newey-West standard error (the harness's formulas)."""

    count = len(values)
    mean = math.fsum(values) / count
    demeaned = [value - mean for value in values]
    squares = math.fsum(value * value for value in demeaned)
    iid = math.sqrt(squares / (count - 1)) / math.sqrt(count)
    lag = max(0, min(count - 1, math.floor(4.0 * (count / 100.0) ** (2.0 / 9.0))))
    hac = squares / count
    for distance in range(1, lag + 1):
        weight = 1.0 - distance / (lag + 1.0)
        gamma = (
            math.fsum(demeaned[i] * demeaned[i - distance] for i in range(distance, count)) / count
        )
        hac += 2.0 * weight * gamma
    return mean, iid, math.sqrt(hac / count)


def p_value(mean: float, iid: float, hac: float, count: int, iid_only: bool = False) -> float:
    """The harness's conservative one-sided p-value: the larger of iid and HAC."""

    iid_p = student_t_upper_tail(mean / iid, count - 1)
    if iid_only:
        return iid_p
    return max(iid_p, student_t_upper_tail(mean / hac, count - 1))


def draw(rng: random.Random, spans: list[list[Span]], model: Model) -> list[list[float]]:
    """Noise of each config's periods: family, horizon and own factors, summed over held days."""

    first = min(span[0] for series in spans for span in series)
    last = max(span[1] for series in spans for span in series)
    family = [0.0]
    for _ in range(first, last):
        family.append(family[-1] + rng.gauss(0.0, 1.0))
    own_weight = math.sqrt(1.0 - model.family_share - model.horizon_share)
    horizon_noise: dict[int, list[float]] = {}
    out: list[list[float]] = []
    for config, series in enumerate(spans):
        horizon = HORIZONS[config]
        if horizon not in horizon_noise:
            horizon_noise[horizon] = [rng.gauss(0.0, math.sqrt(horizon)) for _ in series]
        shared = horizon_noise[horizon]
        out.append(
            [
                model.day_vol
                * (
                    math.sqrt(model.family_share) * (family[end - first] - family[start - first])
                    + math.sqrt(model.horizon_share) * shared[index]
                    + own_weight * rng.gauss(0.0, math.sqrt(horizon))
                )
                for index, (start, end) in enumerate(series)
            ]
        )
    return out


def simulate(model: Model) -> dict[str, dict[float, dict[str, int]]]:
    rng = random.Random(SEED)
    plans = [schedule(horizon) for horizon in HORIZONS]
    validation_spans = [plan[0] for plan in plans]
    holdout_spans = [plan[1] for plan in plans]
    targets: dict[str, int | None] = {"h7": 0, "h14": 2, "all": None}
    keys = ("selected", "holdout", "both", "both_label", "family")
    counts = {name: {s: dict.fromkeys(keys, 0) for s in SHARPES} for name in targets}
    for index in range(model.draws):
        validation = [moments(series) for series in draw(rng, validation_spans, model)]
        holdout = [moments(series) for series in draw(rng, holdout_spans, model)]
        if index == 0:
            _check_hac()
        for name, target in targets.items():
            for sharpe in SHARPES:
                drift = [
                    daily_mean(sharpe if target in (None, c) else 0.0, HORIZONS[c], model.day_vol)
                    * HORIZONS[c]
                    for c in range(4)
                ]
                selected = _select(validation, validation_spans, drift, model)
                passes = [
                    _holdout(holdout[c], len(holdout_spans[c]), drift[c], model) for c in range(4)
                ]
                block = counts[name][sharpe]
                if target is not None:
                    block["holdout"] += passes[target][0]
                    if selected == target:
                        block["selected"] += 1
                        block["both"] += passes[target][0]
                        block["both_label"] += passes[target][1]
                if selected is not None:
                    block["family"] += passes[selected][0]
    return counts


def _select(
    validation: list[tuple[float, float, float]],
    spans: list[list[Span]],
    drift: list[float],
    model: Model,
) -> int | None:
    means = [validation[c][0] + drift[c] for c in range(4)]
    raw = [
        p_value(means[c], validation[c][1], validation[c][2], len(spans[c]), model.iid_only)
        for c in range(4)
    ]
    adjusted = holm(raw)
    survivors = [
        c
        for c in range(4)
        if adjusted[c] <= ALPHA
        and means[c] > 0.0
        and means[c] - 0.5 * ROUND_TRIP > 0.0
        and means[c] - ROUND_TRIP > 0.0
    ]
    if not survivors:
        return None
    return max(survivors, key=lambda c: (means[c], -c))


def _holdout(
    moment: tuple[float, float, float], count: int, drift: float, model: Model
) -> tuple[bool, bool]:
    """(the documents' holdout rule, the harness's passes_h1 label)."""

    mean = moment[0] + drift
    p = p_value(mean, moment[1], moment[2], count, model.iid_only)
    rule = mean > 0.0 and mean - 0.5 * ROUND_TRIP > 0.0 and p <= HOLDOUT_P
    label = mean > 0.0 and mean - ROUND_TRIP > 0.0 and p <= ALPHA
    return rule, label


def _check_hac() -> None:
    rng = random.Random(0)
    for count in (40, 75, 96, 176):
        values = [rng.gauss(0.001, 0.05) for _ in range(count)]
        mean, _iid, hac = moments(values)
        t_stat, _p, _lag = newey_west_mean_test(values)
        if t_stat is None or not math.isclose(mean / hac, t_stat, rel_tol=1e-12):
            raise SystemExit("power: the HAC standard error differs from the harness's")


def parse_model(argv: Sequence[str]) -> Model:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--draws", type=int, default=DRAWS)
    parser.add_argument("--annual-vol", type=float, default=ANNUAL_VOL)
    parser.add_argument("--independent", action="store_true", help="no shared factors")
    parser.add_argument("--iid-only", action="store_true", help="iid t-test p-values only")
    args = parser.parse_args(argv)
    if args.draws < 1:
        parser.error("--draws must be at least 1")
    if not (math.isfinite(args.annual_vol) and args.annual_vol > 0.0):
        parser.error("--annual-vol must be a positive number")
    shares = (0.0, 0.0) if args.independent else (FAMILY_SHARE, HORIZON_SHARE)
    return Model(
        draws=args.draws,
        annual_vol=args.annual_vol,
        family_share=shares[0],
        horizon_share=shares[1],
        iid_only=args.iid_only,
    )


def main(argv: Sequence[str] | None = None) -> None:
    model = parse_model(sys.argv[1:] if argv is None else argv)
    counts = simulate(model)
    plans = [schedule(horizon) for horizon in (7, 14)]
    out = sys.stdout
    header = f"draws {model.draws}, seed {SEED}, book volatility {model.annual_vol:.0%} a year"
    if model.family_share == 0.0 and model.horizon_share == 0.0:
        header += ", configs independent"
    if model.iid_only:
        header += ", iid p-values only"
    out.write(header + "\n")
    counts_text = [len(plans[h][w]) for w in (0, 1) for h in (0, 1)]
    out.write("periods: validation h7 {} h14 {}, holdout h7 {} h14 {}\n".format(*counts_text))
    out.write(
        "\n| True net annualized Sharpe | h7: validation | h7: holdout | h7: both "
        "| h14: validation | h14: holdout | h14: both | All four at S: family passes |\n"
    )
    out.write("| --- | --- | --- | --- | --- | --- | --- | --- |\n")
    for sharpe in SHARPES:
        cells = []
        for name in ("h7", "h14"):
            block = counts[name][sharpe]
            cells += [block["selected"], block["holdout"], block["both"]]
        cells.append(counts["all"][sharpe]["family"])
        out.write(
            f"| {sharpe:.2f} | "
            + " | ".join(f"{value / model.draws:.3f}" for value in cells)
            + " |\n"
        )
    out.write("\nboth, counting only the harness's passes_h1 label as a holdout pass:\n")
    for sharpe in SHARPES:
        out.write(
            f"  S {sharpe:.2f}: h7 {counts['h7'][sharpe]['both_label'] / model.draws:.3f}, "
            f"h14 {counts['h14'][sharpe]['both_label'] / model.draws:.3f}\n"
        )
    null = counts["all"][0.0]
    out.write(
        f"\nnull, all four at 0: the family passes in {null['family'] / model.draws:.4f} of draws\n"
    )


if __name__ == "__main__":
    main()
