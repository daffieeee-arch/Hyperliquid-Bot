"""Score pre-registered configs. Selection never reads the holdout."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from research.harness.costs import STRESS_MULTIPLIERS, round_trip_cost, stress_key
from research.harness.data import BarTable
from research.harness.errors import HarnessError
from research.harness.spec import ConfigSpec, HypothesisSpec
from research.harness.splits import Fold, walk_forward
from research.harness.stats import (
    benjamini_hochberg,
    bonferroni,
    holm,
    newey_west_mean_test,
    student_t_upper_tail,
)

LABEL_NOT_ENOUGH_DATA: Final = "not_enough_data"
LABEL_NO_EDGE: Final = "no_edge"
LABEL_FRAGILE: Final = "interesting_but_fragile"
LABEL_PASSES_H1: Final = "passes_h1"
PROMOTION_FORBIDDEN: Final = "forbidden"
PROMOTION_PAPER_CANDIDATE: Final = "paper_candidate"


@dataclass(frozen=True, slots=True)
class MetricBlock:
    trade_count: int
    mean_return: float | None
    sum_return: float | None
    stdev: float | None
    sharpe_per_trade: float | None
    max_drawdown: float | None
    profit_factor: float | None
    win_rate: float | None
    expectancy: float | None
    t_stat: float | None
    p_value: float | None
    naive_p_value: float | None
    hac_t_stat: float | None
    hac_p_value: float | None
    hac_lag: int | None


@dataclass(frozen=True, slots=True)
class ConfigScore:
    config_id: str
    threshold: float
    horizon_bars: int
    validation_gross: MetricBlock
    validation_net: dict[str, MetricBlock]
    family_p_value: float
    adjusted_p: dict[str, float]
    selected: bool


@dataclass(frozen=True, slots=True)
class Decision:
    label: str
    promotion_decision: str
    reasons: tuple[str, ...]
    folds: tuple[Fold, ...]
    holdout_start: int
    holdout_end: int
    selected_config_id: str | None
    primary_config_id: str
    scores: tuple[ConfigScore, ...]
    holdout_config_id: str | None
    holdout_gross: MetricBlock | None
    holdout_net: dict[str, MetricBlock] | None


def decide(spec: HypothesisSpec, table: BarTable) -> Decision:
    """Label the pre-registered family. Promotion stays forbidden unless H1 passes OOS."""

    folds, (holdout_start, holdout_end) = walk_forward(len(table.timestamps), spec.split)
    feature = table.features[_signal_column(spec)]
    gross_by_config = [
        (
            config,
            _pooled_gross(
                feature,
                table.prices,
                config=config,
                direction=spec.direction,
                latency_bars=spec.costs.latency_bars,
                folds=folds,
            ),
        )
        for config in spec.configs
    ]
    family_p = [
        _family_p_value(spec, _apply_cost(gross, round_trip_cost(spec.costs, 1.0)))
        for _, gross in gross_by_config
    ]
    adjusted = {
        "bonferroni": bonferroni(family_p),
        "holm": holm(family_p),
        "bh": benjamini_hochberg(family_p),
    }
    scores = _build_scores(spec, gross_by_config, family_p, adjusted)
    label, reasons, selected_index = _validation_label(spec, scores, folds)
    holdout_config_id: str | None = None
    holdout_gross: MetricBlock | None = None
    holdout_net: dict[str, MetricBlock] | None = None
    if selected_index is None:
        selected_id = None
        scored = scores
    else:
        holdout_config = spec.configs[selected_index]
        selected_id = holdout_config.id
        scored = _mark_selected(scores, selected_index)
        label, holdout_reasons, holdout_gross, holdout_net = _confirm_holdout(
            spec,
            feature,
            table.prices,
            holdout_config,
            holdout_start,
            holdout_end,
        )
        reasons = (*reasons, *holdout_reasons)
        holdout_config_id = holdout_config.id
    decision = Decision(
        label=label,
        promotion_decision=_promotion_for(label),
        reasons=reasons,
        folds=folds,
        holdout_start=holdout_start,
        holdout_end=holdout_end,
        selected_config_id=selected_id,
        primary_config_id=spec.configs[0].id,
        scores=scored,
        holdout_config_id=holdout_config_id,
        holdout_gross=holdout_gross,
        holdout_net=holdout_net,
    )
    _assert_promotion_invariant(decision)
    return decision


def collect_gross_returns(
    feature: Sequence[float],
    prices: Sequence[float],
    *,
    threshold: float,
    horizon_bars: int,
    latency_bars: int,
    direction: str,
    start: int,
    end: int,
) -> tuple[float, ...]:
    """Non-overlapping close-to-close returns inside [start, end).

    The fill is the close of bar `decision + latency`. Exit is `horizon_bars`
    after that fill. Both indexes must stay strictly inside the window, so a
    validation trade cannot read a holdout price.
    """

    if start < 0 or end < start:
        raise HarnessError("split", "Trade window is not a valid index range.")
    gross: list[float] = []
    decision = start
    while True:
        entry = decision + latency_bars
        exit_index = entry + horizon_bars
        if decision >= end or exit_index >= end:
            break
        side = _side(feature[decision], threshold, direction)
        if side == 0:
            decision += 1
            continue
        gross.append(side * (prices[exit_index] - prices[entry]) / prices[entry])
        decision = exit_index
    return tuple(gross)


def summarize(values: Sequence[float]) -> MetricBlock:
    """Per-trade metrics. Sharpe is not annualized. Drawdown sums simple returns."""

    count = len(values)
    if count == 0:
        return _empty_metric()
    total = math.fsum(values)
    mean = total / count
    stdev = _sample_stdev(values, mean) if count >= 2 else None
    sharpe = None if stdev is None or stdev == 0.0 else mean / stdev
    naive_t, naive_p = _mean_test(mean, stdev, count)
    hac_t, hac_p, hac_lag = newey_west_mean_test(values)
    return MetricBlock(
        trade_count=count,
        mean_return=mean,
        sum_return=total,
        stdev=stdev,
        sharpe_per_trade=sharpe,
        max_drawdown=_max_drawdown(values),
        profit_factor=_profit_factor(values),
        win_rate=sum(1 for value in values if value > 0.0) / count,
        expectancy=mean,
        t_stat=naive_t,
        p_value=_conservative_p(naive_p, hac_p),
        naive_p_value=naive_p,
        hac_t_stat=hac_t,
        hac_p_value=hac_p,
        hac_lag=hac_lag,
    )


def _confirm_holdout(
    spec: HypothesisSpec,
    feature: Sequence[float],
    prices: Sequence[float],
    config: ConfigSpec,
    holdout_start: int,
    holdout_end: int,
) -> tuple[str, tuple[str, ...], MetricBlock, dict[str, MetricBlock]]:
    gross = collect_gross_returns(
        feature,
        prices,
        threshold=config.threshold,
        horizon_bars=config.horizon_bars,
        latency_bars=spec.costs.latency_bars,
        direction=spec.direction,
        start=holdout_start,
        end=holdout_end,
    )
    gross_block = summarize(gross)
    net = {
        stress_key(stress): summarize(_apply_cost(gross, round_trip_cost(spec.costs, stress)))
        for stress in STRESS_MULTIPLIERS
    }
    base = net["1.0"]
    if base.trade_count < spec.sample.min_trades_holdout:
        return (
            LABEL_NOT_ENOUGH_DATA,
            (
                f"Holdout has {base.trade_count} trades; "
                f"sample.min_trades_holdout is {spec.sample.min_trades_holdout}.",
            ),
            gross_block,
            net,
        )
    stresses_hold = all(_mean_positive(net[stress_key(stress)]) for stress in (1.5, 2.0))
    if _significant(spec, base) and stresses_hold:
        return (
            LABEL_PASSES_H1,
            ("Untouched holdout mean net stayed positive after costs at 1.0x, 1.5x, and 2.0x.",),
            gross_block,
            net,
        )
    return (
        LABEL_FRAGILE,
        ("Validation survived, but the untouched holdout did not confirm H1 after costs.",),
        gross_block,
        net,
    )


def _validation_label(
    spec: HypothesisSpec,
    scores: tuple[ConfigScore, ...],
    folds: tuple[Fold, ...],
) -> tuple[str, tuple[str, ...], int | None]:
    if len(folds) < spec.sample.min_folds:
        return (
            LABEL_NOT_ENOUGH_DATA,
            (
                f"Walk-forward produced {len(folds)} test folds; "
                f"sample.min_folds is {spec.sample.min_folds}.",
            ),
            None,
        )
    if all(
        score.validation_net["1.0"].trade_count < spec.sample.min_trades_validation
        for score in scores
    ):
        return (
            LABEL_NOT_ENOUGH_DATA,
            ("Every config has fewer validation trades than sample.min_trades_validation.",),
            None,
        )
    eligible = [index for index, score in enumerate(scores) if _survives_validation(spec, score)]
    if not eligible:
        if any(_fragile_validation(spec, score) for score in scores):
            return (
                LABEL_FRAGILE,
                (
                    "A config showed a gross or unadjusted net result that failed costs, "
                    "stress, or multiple-testing control.",
                ),
                None,
            )
        return (
            LABEL_NO_EDGE,
            (
                "No config had positive after-cost validation expectancy that survived "
                f"{spec.selection_method} at alpha {spec.alpha} and the 1.5x/2.0x cost stress.",
            ),
            None,
        )
    selected = _best_index(scores, eligible)
    return (
        LABEL_PASSES_H1,
        (
            (
                f"Config {scores[selected].config_id} survived validation "
                f"under {spec.selection_method}."
            ),
        ),
        selected,
    )


def _survives_validation(spec: HypothesisSpec, score: ConfigScore) -> bool:
    base = score.validation_net["1.0"]
    if not _net_block_passes(spec, base):
        return False
    if score.adjusted_p[spec.selection_method] > spec.alpha:
        return False
    return all(_mean_positive(score.validation_net[stress_key(stress)]) for stress in (1.5, 2.0))


def _fragile_validation(spec: HypothesisSpec, score: ConfigScore) -> bool:
    gross = score.validation_gross
    net = score.validation_net["1.0"]
    if gross.trade_count < spec.sample.min_trades_validation:
        return False
    gross_significant = (
        gross.mean_return is not None
        and gross.mean_return > 0.0
        and gross.p_value is not None
        and gross.p_value <= spec.alpha
    )
    if gross_significant and not _mean_positive(net):
        return True
    unadjusted = (
        net.trade_count >= spec.sample.min_trades_validation
        and _mean_positive(net)
        and net.p_value is not None
        and net.p_value <= spec.alpha
    )
    if not unadjusted:
        return False
    failed_family = score.adjusted_p[spec.selection_method] > spec.alpha
    failed_stress = any(
        not _mean_positive(score.validation_net[stress_key(stress)]) for stress in (1.5, 2.0)
    )
    return failed_family or failed_stress


def _significant(spec: HypothesisSpec, block: MetricBlock) -> bool:
    return _mean_positive(block) and block.p_value is not None and block.p_value <= spec.alpha


def _net_block_passes(spec: HypothesisSpec, block: MetricBlock) -> bool:
    return block.trade_count >= spec.sample.min_trades_validation and _significant(spec, block)


def _mean_positive(block: MetricBlock) -> bool:
    return block.mean_return is not None and block.mean_return > 0.0


def _best_index(scores: tuple[ConfigScore, ...], eligible: list[int]) -> int:
    best = eligible[0]
    best_mean = scores[best].validation_net["1.0"].mean_return
    if best_mean is None:
        raise HarnessError("invariant", "Eligible config is missing a validation mean.")
    for index in eligible[1:]:
        mean = scores[index].validation_net["1.0"].mean_return
        if mean is None:
            raise HarnessError("invariant", "Eligible config is missing a validation mean.")
        if mean > best_mean:
            best = index
            best_mean = mean
    return best


def _build_scores(
    spec: HypothesisSpec,
    gross_by_config: list[tuple[ConfigSpec, tuple[float, ...]]],
    family_p: list[float],
    adjusted: dict[str, list[float]],
) -> tuple[ConfigScore, ...]:
    scores: list[ConfigScore] = []
    for index, (config, gross) in enumerate(gross_by_config):
        scores.append(
            ConfigScore(
                config_id=config.id,
                threshold=config.threshold,
                horizon_bars=config.horizon_bars,
                validation_gross=summarize(gross),
                validation_net={
                    stress_key(stress): summarize(
                        _apply_cost(gross, round_trip_cost(spec.costs, stress))
                    )
                    for stress in STRESS_MULTIPLIERS
                },
                family_p_value=family_p[index],
                adjusted_p={
                    method: adjusted[method][index] for method in ("bonferroni", "holm", "bh")
                },
                selected=False,
            )
        )
    return tuple(scores)


def _mark_selected(scores: tuple[ConfigScore, ...], selected_index: int) -> tuple[ConfigScore, ...]:
    marked: list[ConfigScore] = []
    for index, score in enumerate(scores):
        marked.append(score if index != selected_index else _copy_score(score, selected=True))
    return tuple(marked)


def _copy_score(score: ConfigScore, *, selected: bool) -> ConfigScore:
    return ConfigScore(
        config_id=score.config_id,
        threshold=score.threshold,
        horizon_bars=score.horizon_bars,
        validation_gross=score.validation_gross,
        validation_net=score.validation_net,
        family_p_value=score.family_p_value,
        adjusted_p=score.adjusted_p,
        selected=selected,
    )


def _family_p_value(spec: HypothesisSpec, net: Sequence[float]) -> float:
    """Underpowered configs stay in the family with p=1 so they cannot shrink m."""

    if len(net) < spec.sample.min_trades_validation:
        return 1.0
    p_value = summarize(net).p_value
    if p_value is None:
        return 1.0
    return p_value


def _signal_column(spec: HypothesisSpec) -> str:
    for feature in spec.features:
        if feature.name == spec.signal_feature:
            return feature.column
    raise HarnessError("spec", "signal_feature disappeared after validation.")


def _pooled_gross(
    feature: Sequence[float],
    prices: Sequence[float],
    *,
    config: ConfigSpec,
    direction: str,
    latency_bars: int,
    folds: tuple[Fold, ...],
) -> tuple[float, ...]:
    pooled: list[float] = []
    for fold in folds:
        pooled.extend(
            collect_gross_returns(
                feature,
                prices,
                threshold=config.threshold,
                horizon_bars=config.horizon_bars,
                latency_bars=latency_bars,
                direction=direction,
                start=fold.test_start,
                end=fold.test_end,
            )
        )
    return tuple(pooled)


def _apply_cost(gross: Sequence[float], cost: float) -> tuple[float, ...]:
    return tuple(value - cost for value in gross)


def _side(value: float, threshold: float, direction: str) -> int:
    if direction == "long_only":
        return 1 if value > threshold else 0
    if direction != "signed":
        raise HarnessError("spec", "direction must be signed or long_only.")
    if value > threshold:
        return 1
    if value < -threshold:
        return -1
    return 0


def _promotion_for(label: str) -> str:
    if label == LABEL_PASSES_H1:
        return PROMOTION_PAPER_CANDIDATE
    return PROMOTION_FORBIDDEN


def _assert_promotion_invariant(decision: Decision) -> None:
    passed = decision.label == LABEL_PASSES_H1
    if passed and decision.promotion_decision != PROMOTION_PAPER_CANDIDATE:
        raise HarnessError("invariant", "passes_h1 requires promotion_decision paper_candidate.")
    if not passed and decision.promotion_decision != PROMOTION_FORBIDDEN:
        raise HarnessError("invariant", "promotion_decision must stay forbidden unless H1 passes.")
    if passed and decision.selected_config_id is None:
        raise HarnessError("invariant", "passes_h1 requires a validation-selected config.")


def _empty_metric() -> MetricBlock:
    return MetricBlock(
        trade_count=0,
        mean_return=None,
        sum_return=None,
        stdev=None,
        sharpe_per_trade=None,
        max_drawdown=None,
        profit_factor=None,
        win_rate=None,
        expectancy=None,
        t_stat=None,
        p_value=None,
        naive_p_value=None,
        hac_t_stat=None,
        hac_p_value=None,
        hac_lag=None,
    )


def _conservative_p(naive_p: float | None, hac_p: float | None) -> float | None:
    """The multiple-testing gate and holdout check use the larger tail probability."""

    if naive_p is None:
        return hac_p
    if hac_p is None:
        return naive_p
    return max(naive_p, hac_p)


def _sample_stdev(values: Sequence[float], mean: float) -> float:
    variance = math.fsum((value - mean) ** 2 for value in values) / (len(values) - 1)
    return math.sqrt(variance)


def _mean_test(mean: float, stdev: float | None, count: int) -> tuple[float | None, float | None]:
    if count < 2 or stdev is None:
        return None, None
    if stdev == 0.0:
        if mean > 0.0:
            return None, 0.0
        if mean < 0.0:
            return None, 1.0
        return None, 0.5
    t_stat = mean / (stdev / math.sqrt(count))
    return t_stat, student_t_upper_tail(t_stat, count - 1)


def _max_drawdown(values: Sequence[float]) -> float:
    equity = 0.0
    peak = 0.0
    worst = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        worst = max(worst, peak - equity)
    return worst


def _profit_factor(values: Sequence[float]) -> float | None:
    wins = math.fsum(value for value in values if value > 0.0)
    losses = math.fsum(-value for value in values if value < 0.0)
    if losses == 0.0:
        return None if wins > 0.0 else 0.0
    return wins / losses
