"""Score pre-registered configs. Selection never reads the holdout."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final

from research.harness.costs import STRESS_MULTIPLIERS, round_trip_cost, stress_key
from research.harness.data import BarTable
from research.harness.errors import HarnessError, IntegrityError
from research.harness.overfit import (
    BlockStats,
    ConfigUnderTest,
    Overfitting,
    Pbo,
    cscv_blocks,
    deflated_sharpe,
    probability_of_backtest_overfitting,
)
from research.harness.spec import ConfigSpec, CostSpec, HypothesisSpec, SizingSpec
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
class Trade:
    """One non-overlapping trade: decided at ``decision``, filled at ``entry``."""

    decision: int
    entry: int
    exit: int
    side: int


@dataclass(frozen=True, slots=True)
class TradeSeries:
    """Per-trade returns at each trade's weight, in units of equity.

    ``gross`` is the price return and ``weights`` the position weight; costs
    scale with the weight. ``funding_paid`` and ``funding_received`` are the
    funding payments a trade made and received (both >= 0), kept apart so a
    stress can treat each payment adversely.
    """

    gross: tuple[float, ...]
    funding_paid: tuple[float, ...]
    funding_received: tuple[float, ...]
    weights: tuple[float, ...]

    @property
    def funding(self) -> tuple[float, ...]:
        """The realized funding cashflow per trade (positive when received)."""

        return tuple(
            received - paid
            for paid, received in zip(self.funding_paid, self.funding_received, strict=True)
        )

    def net(self, costs: CostSpec, stress: float) -> tuple[float, ...]:
        """Gross minus the weighted round trip, plus funding, all at one stress.

        Funding paid is multiplied by the stress and funding received divided
        by it, payment by payment: the realized funding is exact in a backtest
        but need not repeat, so an edge resting on it must survive a haircut.
        At 1.0x every value is the realized one.
        """

        round_trip = round_trip_cost(costs, stress)
        return tuple(
            gross - weight * round_trip - paid * stress + received / stress
            for gross, paid, received, weight in zip(
                self.gross,
                self.funding_paid,
                self.funding_received,
                self.weights,
                strict=True,
            )
        )

    def mean_weight(self) -> float | None:
        if not self.weights:
            return None
        return math.fsum(self.weights) / len(self.weights)


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
    # None when the spec declares no funding column.
    validation_funding: MetricBlock | None = None
    mean_weight: float | None = None


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
    holdout_funding: MetricBlock | None = None
    holdout_mean_weight: float | None = None
    # Diagnostics only: they never change the label.
    overfitting: Overfitting | None = None


@dataclass(frozen=True, slots=True)
class _HoldoutResult:
    label: str
    reasons: tuple[str, ...]
    gross: MetricBlock
    net: dict[str, MetricBlock]
    funding: MetricBlock | None
    mean_weight: float | None


def decide(spec: HypothesisSpec, table: BarTable) -> Decision:
    """Label the pre-registered family. Promotion stays forbidden unless H1 passes OOS."""

    if (spec.costs.funding_column is None) != (table.funding is None):
        raise HarnessError("invariant", "The bar table's funding does not match the spec.")
    # Audited over the whole series first, after load_bars' point-in-time
    # checks, so every table that reaches a decision gets the same check.
    vol = _vol_series(spec, table)
    folds, (holdout_start, holdout_end) = walk_forward(len(table.timestamps), spec.split)
    feature = table.features[_feature_column(spec, spec.signal_feature)]
    fold_series_by_config = [
        _fold_series(spec, feature, table, vol, config=config, folds=folds)
        for config in spec.configs
    ]
    series_by_config = [
        (config, _concat_series(fold_series))
        for config, fold_series in zip(spec.configs, fold_series_by_config, strict=True)
    ]
    nets_by_config = [_net_blocks(spec, series) for _, series in series_by_config]
    family_p = [_family_p_value(spec, nets["1.0"]) for nets in nets_by_config]
    adjusted = {
        "bonferroni": bonferroni(family_p),
        "holm": holm(family_p),
        "bh": benjamini_hochberg(family_p),
    }
    scores = _build_scores(spec, series_by_config, nets_by_config, family_p, adjusted)
    label, reasons, selected_index = _validation_label(spec, scores, folds)
    holdout_config_id: str | None = None
    holdout: _HoldoutResult | None = None
    if selected_index is None:
        selected_id = None
        scored = scores
    else:
        holdout_config = spec.configs[selected_index]
        selected_id = holdout_config.id
        scored = _mark_selected(scores, selected_index)
        holdout = _confirm_holdout(
            spec,
            feature,
            table,
            vol,
            holdout_config,
            holdout_start,
            holdout_end,
        )
        label = holdout.label
        reasons = (*reasons, *holdout.reasons)
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
        holdout_gross=None if holdout is None else holdout.gross,
        holdout_net=None if holdout is None else holdout.net,
        holdout_funding=None if holdout is None else holdout.funding,
        holdout_mean_weight=None if holdout is None else holdout.mean_weight,
        overfitting=_overfitting(
            spec, series_by_config, nets_by_config, fold_series_by_config, folds, selected_index
        ),
    )
    _assert_promotion_invariant(decision)
    return decision


def collect_trades(
    feature: Sequence[float],
    *,
    threshold: float,
    horizon_bars: int,
    latency_bars: int,
    direction: str,
    start: int,
    end: int,
) -> tuple[Trade, ...]:
    """Non-overlapping trades inside [start, end).

    The fill is the close of bar `decision + latency`. Exit is `horizon_bars`
    after that fill. Both indexes must stay strictly inside the window, so a
    validation trade cannot read a holdout price.
    """

    if start < 0 or end < start:
        raise HarnessError("split", "Trade window is not a valid index range.")
    trades: list[Trade] = []
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
        trades.append(Trade(decision=decision, entry=entry, exit=exit_index, side=side))
        decision = exit_index
    return tuple(trades)


def trade_series(
    trades: Sequence[Trade],
    prices: Sequence[float],
    *,
    funding: Sequence[float] | None,
    sizing: SizingSpec,
    vol: Sequence[float] | None,
) -> TradeSeries:
    """Weight each trade, then add its funding cashflow next to its price return."""

    gross: list[float] = []
    paid: list[float] = []
    received: list[float] = []
    weights: list[float] = []
    for trade in trades:
        weight = position_weight(sizing, vol, trade.decision)
        unit_paid, unit_received = _unit_funding(prices, funding, trade)
        gross.append(weight * _unit_return(prices, trade))
        paid.append(weight * unit_paid)
        received.append(weight * unit_received)
        weights.append(weight)
    return TradeSeries(
        gross=tuple(gross),
        funding_paid=tuple(paid),
        funding_received=tuple(received),
        weights=tuple(weights),
    )


def position_weight(sizing: SizingSpec, vol: Sequence[float] | None, decision: int) -> float:
    """One unit, or ``target_vol / vol`` at the decision bar capped at ``max_leverage``.

    The vol value is read at the decision bar only, like the signal, so its
    declared availability clock bounds what the weight can know. A
    non-positive vol fails closed rather than taking the leverage cap.
    """

    if sizing.method == "unit":
        return 1.0
    if vol is None or sizing.target_vol is None or sizing.max_leverage is None:
        raise HarnessError("invariant", "vol_target sizing is missing its inputs.")
    value = vol[decision]
    if not (math.isfinite(value) and value > 0.0):
        # decide() refuses such a series up front; this guards direct callers.
        raise IntegrityError("sizing", f"Volatility feature is not positive at bar {decision}.")
    return min(sizing.max_leverage, sizing.target_vol / value)


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
    table: BarTable,
    vol: Sequence[float] | None,
    config: ConfigSpec,
    holdout_start: int,
    holdout_end: int,
) -> _HoldoutResult:
    series = _window_series(
        spec, feature, table, vol, config=config, start=holdout_start, end=holdout_end
    )
    gross_block = summarize(series.gross)
    net = _net_blocks(spec, series)
    funding = _funding_block(spec, series)
    base = net["1.0"]
    if base.trade_count < spec.sample.min_trades_holdout:
        label = LABEL_NOT_ENOUGH_DATA
        reasons: tuple[str, ...] = (
            f"Holdout has {base.trade_count} trades; "
            f"sample.min_trades_holdout is {spec.sample.min_trades_holdout}.",
        )
    elif _significant(spec, base) and all(
        _mean_positive(net[stress_key(stress)]) for stress in (1.5, 2.0)
    ):
        label = LABEL_PASSES_H1
        reasons = (
            "Untouched holdout mean net stayed positive after costs at 1.0x, 1.5x, and 2.0x.",
        )
    else:
        label = LABEL_FRAGILE
        reasons = (
            "Validation survived, but the untouched holdout did not confirm H1 after costs.",
        )
    return _HoldoutResult(
        label=label,
        reasons=reasons,
        gross=gross_block,
        net=net,
        funding=funding,
        mean_weight=series.mean_weight(),
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
    series_by_config: list[tuple[ConfigSpec, TradeSeries]],
    nets_by_config: list[dict[str, MetricBlock]],
    family_p: list[float],
    adjusted: dict[str, list[float]],
) -> tuple[ConfigScore, ...]:
    scores: list[ConfigScore] = []
    for index, (config, series) in enumerate(series_by_config):
        scores.append(
            ConfigScore(
                config_id=config.id,
                threshold=config.threshold,
                horizon_bars=config.horizon_bars,
                validation_gross=summarize(series.gross),
                validation_net=nets_by_config[index],
                family_p_value=family_p[index],
                adjusted_p={
                    method: adjusted[method][index] for method in ("bonferroni", "holm", "bh")
                },
                selected=False,
                validation_funding=_funding_block(spec, series),
                mean_weight=series.mean_weight(),
            )
        )
    return tuple(scores)


def _net_blocks(spec: HypothesisSpec, series: TradeSeries) -> dict[str, MetricBlock]:
    return {
        stress_key(stress): summarize(series.net(spec.costs, stress))
        for stress in STRESS_MULTIPLIERS
    }


def _funding_block(spec: HypothesisSpec, series: TradeSeries) -> MetricBlock | None:
    """The realized (1.0x) funding cashflow, or None when no funding is declared."""

    return None if spec.costs.funding_column is None else summarize(series.funding)


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
        validation_funding=score.validation_funding,
        mean_weight=score.mean_weight,
    )


def _family_p_value(spec: HypothesisSpec, net: MetricBlock) -> float:
    """Underpowered configs stay in the family with p=1 so they cannot shrink m."""

    if net.trade_count < spec.sample.min_trades_validation:
        return 1.0
    if net.p_value is None:
        return 1.0
    return net.p_value


def _feature_column(spec: HypothesisSpec, name: str) -> str:
    for feature in spec.features:
        if feature.name == name:
            return feature.column
    raise HarnessError("spec", f"Feature {name} disappeared after validation.")


def _vol_series(spec: HypothesisSpec, table: BarTable) -> tuple[float, ...] | None:
    """The sizing volatility, positive on every row or the run fails closed.

    Checking every row, not only where a trade sizes, keeps the outcome a
    property of the data rather than of which configs happen to trade. Trim
    warm-up rows in the ETL instead of zero-filling them.
    """

    name = spec.sizing.vol_feature
    if name is None:
        return None
    values = table.features[_feature_column(spec, name)]
    for index, value in enumerate(values):
        if not (math.isfinite(value) and value > 0.0):
            raise IntegrityError(
                "sizing", f"Volatility feature {name} at row {index} must be positive."
            )
    return values


def _window_series(
    spec: HypothesisSpec,
    feature: Sequence[float],
    table: BarTable,
    vol: Sequence[float] | None,
    *,
    config: ConfigSpec,
    start: int,
    end: int,
) -> TradeSeries:
    trades = _window_trades(spec, feature, config=config, start=start, end=end)
    return trade_series(trades, table.prices, funding=table.funding, sizing=spec.sizing, vol=vol)


def _window_trades(
    spec: HypothesisSpec,
    feature: Sequence[float],
    *,
    config: ConfigSpec,
    start: int,
    end: int,
) -> tuple[Trade, ...]:
    return collect_trades(
        feature,
        threshold=config.threshold,
        horizon_bars=config.horizon_bars,
        latency_bars=spec.costs.latency_bars,
        direction=spec.direction,
        start=start,
        end=end,
    )


def _fold_series(
    spec: HypothesisSpec,
    feature: Sequence[float],
    table: BarTable,
    vol: Sequence[float] | None,
    *,
    config: ConfigSpec,
    folds: tuple[Fold, ...],
) -> tuple[TradeSeries, ...]:
    """One config's trades per walk-forward test fold, in fold order."""

    return tuple(
        _window_series(
            spec, feature, table, vol, config=config, start=fold.test_start, end=fold.test_end
        )
        for fold in folds
    )


def _concat_series(parts: Sequence[TradeSeries]) -> TradeSeries:
    """The folds' trades pooled in order, as one validation series."""

    return TradeSeries(
        gross=tuple(value for part in parts for value in part.gross),
        funding_paid=tuple(value for part in parts for value in part.funding_paid),
        funding_received=tuple(value for part in parts for value in part.funding_received),
        weights=tuple(value for part in parts for value in part.weights),
    )


def _overfitting(
    spec: HypothesisSpec,
    series_by_config: Sequence[tuple[ConfigSpec, TradeSeries]],
    nets_by_config: Sequence[dict[str, MetricBlock]],
    fold_series_by_config: Sequence[tuple[TradeSeries, ...]],
    folds: tuple[Fold, ...],
    selected_index: int | None,
) -> Overfitting:
    """The deflated Sharpe ratio and PBO, both on validation net returns at 1.0x.

    They are reported, never gated on.
    """

    dsr = deflated_sharpe(
        len(spec.configs), _tested_config(spec, series_by_config, nets_by_config, selected_index)
    )
    groups = cscv_blocks(len(folds))
    if groups is None:
        pbo = Pbo(
            None,
            None,
            None,
            None,
            None,
            None,
            f"PBO needs at least 4 walk-forward test folds; this run has {len(folds)}.",
        )
    else:
        nets_by_fold = [
            [part.net(spec.costs, 1.0) for part in fold_series]
            for fold_series in fold_series_by_config
        ]
        pbo = replace(
            probability_of_backtest_overfitting(
                [
                    [
                        BlockStats(
                            trades=sum(len(nets[index]) for index in group),
                            total=math.fsum(value for index in group for value in nets[index]),
                        )
                        for group in groups
                    ]
                    for nets in nets_by_fold
                ]
            ),
            folds_used=sum(len(group) for group in groups),
        )
    return Overfitting(deflated_sharpe=dsr, pbo=pbo)


def _tested_config(
    spec: HypothesisSpec,
    series_by_config: Sequence[tuple[ConfigSpec, TradeSeries]],
    nets_by_config: Sequence[dict[str, MetricBlock]],
    selected_index: int | None,
) -> ConfigUnderTest | None:
    """The config validation selected, else the best validation mean that meets the floor.

    Ranking by mean net per trade, as validation selection does, keeps the
    diagnostic on the config the run takes forward or comes closest to.
    """

    if selected_index is None:
        ranked = [
            (index, nets["1.0"].mean_return)
            for index, nets in enumerate(nets_by_config)
            if nets["1.0"].trade_count >= spec.sample.min_trades_validation
        ]
        means = [(index, mean) for index, mean in ranked if mean is not None]
        if not means:
            return None
        index = max(means, key=lambda item: item[1])[0]
    else:
        index = selected_index
    sharpe = nets_by_config[index]["1.0"].sharpe_per_trade
    if sharpe is None:
        return None
    config, series = series_by_config[index]
    return ConfigUnderTest(
        config_id=config.id,
        sharpe=sharpe,
        returns=series.net(spec.costs, 1.0),
        selected=selected_index is not None,
    )


def _unit_return(prices: Sequence[float], trade: Trade) -> float:
    return trade.side * (prices[trade.exit] - prices[trade.entry]) / prices[trade.entry]


def _unit_funding(
    prices: Sequence[float], funding: Sequence[float] | None, trade: Trade
) -> tuple[float, float]:
    """Funding paid and received per unit of entry notional while the trade is held.

    The trade holds bars ``entry + 1`` through ``exit``. Each bar's rate is
    charged on the notional at that bar's close. A long pays a positive rate;
    a short receives it. Both totals are >= 0.
    """

    if funding is None:
        return 0.0, 0.0
    entry_price = prices[trade.entry]
    paid: list[float] = []
    received: list[float] = []
    for index in range(trade.entry + 1, trade.exit + 1):
        flow = -trade.side * funding[index] * prices[index] / entry_price
        if flow < 0.0:
            paid.append(-flow)
        else:
            received.append(flow)
    return math.fsum(paid), math.fsum(received)


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
