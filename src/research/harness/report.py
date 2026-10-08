"""JSON and markdown artifacts. paper_candidate is not a LIVE authorization."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Final

from research.harness.benchmark import (
    ERROR,
    EVALUATED,
    NO_FOLDS,
    NOT_APPLICABLE,
    SEALED,
    TOO_SHORT,
    Benchmark,
    Window,
)
from research.harness.costs import STRESS_MULTIPLIERS, round_trip_cost, stress_key
from research.harness.data import BarTable, PanelTable
from research.harness.errors import HarnessError
from research.harness.evaluate import ConfigScore, Decision, MetricBlock
from research.harness.overfit import Overfitting
from research.harness.spec import HypothesisSpec, Json
from research.harness.splits import Fold

HARNESS_VERSION: Final = "6"
_ENVIRONMENTS: Final = frozenset({"DEV", "CI", "VPS_RESEARCH"})
_IMAGE_DIGEST: Final = re.compile(r"sha256:[0-9a-f]{64}")
LIMITATIONS: Final[tuple[str, ...]] = (
    "The gate uses the larger of the iid t p-value and a Newey-West HAC t p-value.",
    "Sharpe is per trade, not annualized. Drawdown sums simple returns.",
    "spread_bps is the half-spread per side. Costs are flat bps at 1.0x, 1.5x, and 2.0x.",
    "Funding accrues only from a declared costs.funding_column, per bar held, on the "
    "notional at each bar close. Under stress, each bar's funding paid is multiplied and "
    "each bar's funding received divided by the multiplier; payments inside one bar are "
    "netted first.",
    "Sizing is one unit per trade unless sizing.method is vol_target: target_vol / vol at "
    "the decision bar, capped at max_leverage. Costs and funding scale with the weight.",
    "Latency fills at decision_bar + latency_bars. Zero latency requires allow_zero_latency.",
    "The deflated Sharpe ratio and the probability of backtest overfitting are diagnostics "
    "and never change the label. They use net returns at 1.0x and count only the "
    "pre-registered configs as trials. In each split PBO picks the best positive mean net "
    "per trade among the configs that meet the trade floor pro-rated to the in-sample "
    "folds, and does not re-run the significance and stress gates.",
    "Look-ahead control uses the declared clock. A falsely stamped future value is invisible.",
    "paper_candidate is not LIVE, SHADOW, TESTNET, or an order authorization.",
    "Spot Vision timestamps from 2025-01-01 are microseconds; USD-M examples are milliseconds.",
    "Kraken OHLCVT omits empty intervals and is USD-quoted, not USDT.",
)

# What a bar series' result must say beyond the shared list.
BAR_LIMITATIONS: Final[tuple[str, ...]] = (
    "The buy-and-hold benchmark is one unit long over the validation test folds, and over the "
    "holdout only when a config was selected. It fills after latency_bars like a trade, is "
    "context, and never changes the label.",
)

# What a panel portfolio's result must say beyond the shared list.
PANEL_LIMITATIONS: Final[tuple[str, ...]] = (
    "A panel portfolio scores one trade per non-overlapping period: long the top quantile of "
    "the day's universe by the signal and, when signed, short the bottom quantile, equal "
    "weight within a leg, one round trip on the capital per period. A position is held to "
    "its exit day; a symbol not trading then is marked at its last traded close, which is "
    "not the delisting price a holder got, and pays the recorded funding of its untraded "
    "days to the exit. A held day whose funding is not whole is charged its recorded sum and "
    "counted as uncovered; a halt day without any is charged nothing and counted as "
    "unfunded; a traded day without funding that a position could hold fails closed. The "
    "buy-and-hold benchmark does not apply to a panel.",
)


@dataclass(frozen=True, slots=True)
class Provenance:
    source_environment: str
    source_commit: str | None
    image_digest: str | None


def provenance() -> Provenance:
    """Read the run's provenance once; a malformed environment or digest fails closed."""

    return Provenance(
        source_environment=source_environment(),
        source_commit=source_commit(),
        image_digest=image_digest(),
    )


def source_environment() -> str:
    raw = os.environ.get("RESEARCH_ENV", "DEV")
    if raw not in _ENVIRONMENTS:
        raise HarnessError("data_config", "RESEARCH_ENV must be DEV, CI, or VPS_RESEARCH.")
    return raw


def image_digest() -> str | None:
    """The container image digest from RESEARCH_IMAGE_DIGEST; None when it is unset.

    A value that is set must be a full ``sha256:`` digest, so a broken image
    template fails the run instead of recording a wrong provenance.
    """

    raw = os.environ.get("RESEARCH_IMAGE_DIGEST")
    if raw is None:
        return None
    if _IMAGE_DIGEST.fullmatch(raw) is None:
        raise HarnessError(
            "data_config", "RESEARCH_IMAGE_DIGEST must be sha256: and 64 lowercase hex digits."
        )
    return raw


def source_commit() -> str | None:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    digest = completed.stdout.strip()
    if len(digest) != 40 or any(char not in "0123456789abcdef" for char in digest):
        return None
    return digest


def failure_document(
    *,
    failure_kind: str,
    reasons: tuple[str, ...],
    spec_sha256: str | None,
    hypothesis_id: str | None,
    trading_mode: str,
    origin: Provenance | None,
) -> dict[str, Json]:
    """A fail-closed record. ``origin`` is None when provenance itself was refused."""

    return {
        "harness_version": HARNESS_VERSION,
        "status": "failed_closed",
        "failure_kind": failure_kind,
        "label": None,
        "promotion_decision": "forbidden",
        "trading_mode": trading_mode,
        "spec_sha256": spec_sha256,
        "hypothesis_id": hypothesis_id,
        **_provenance_json(origin),
        "reasons": list(reasons),
        "limitations": list(LIMITATIONS),
        "generated_at_utc": _now(),
    }


def completed_document(
    spec: HypothesisSpec,
    digest: str,
    table: BarTable | PanelTable,
    decision: Decision,
    data_fingerprint: dict[str, Json],
    benchmark: Benchmark,
    origin: Provenance,
    portfolio: dict[str, Json] | None = None,
) -> dict[str, Json]:
    """The completed record; ``portfolio`` is the panel block, present for a panel only."""

    timestamps = table.timestamps
    if (spec.portfolio is None) != (portfolio is None):
        raise HarnessError("invariant", "A portfolio block is written for a panel spec only.")
    return {
        "harness_version": HARNESS_VERSION,
        "status": "completed",
        "failure_kind": None,
        "label": decision.label,
        "promotion_decision": decision.promotion_decision,
        "trading_mode": "PAPER",
        "spec_sha256": digest,
        "hypothesis_id": spec.hypothesis_id,
        "universe": spec.universe,
        "dataset_version": spec.dataset_version,
        "h0": spec.h0,
        "h1": spec.h1,
        "alpha": spec.alpha,
        "selection_method": spec.selection_method,
        **_provenance_json(origin),
        "bar_count": len(timestamps),
        "timestamp_min": timestamps[0] if timestamps else None,
        "timestamp_max": timestamps[-1] if timestamps else None,
        "data_fingerprint": data_fingerprint,
        "costs": {
            "fee_bps": spec.costs.fee_bps,
            "slippage_bps": spec.costs.slippage_bps,
            "spread_bps": spec.costs.spread_bps,
            "latency_bars": spec.costs.latency_bars,
            "allow_zero_latency": spec.costs.allow_zero_latency,
            "funding_column": spec.costs.funding_column,
            "round_trip_cost": {
                stress_key(stress): round_trip_cost(spec.costs, stress)
                for stress in STRESS_MULTIPLIERS
            },
        },
        "sizing": {
            "method": spec.sizing.method,
            "vol_feature": spec.sizing.vol_feature,
            "target_vol": spec.sizing.target_vol,
            "max_leverage": spec.sizing.max_leverage,
        },
        "split": {
            "method": spec.split.method,
            "train_bars": spec.split.train_bars,
            "test_bars": spec.split.test_bars,
            "holdout_bars": spec.split.holdout_bars,
            "holdout_start": decision.holdout_start,
            "holdout_end": decision.holdout_end,
            "folds": [_fold_json(fold) for fold in decision.folds],
        },
        "multiple_testing": {
            "family_size": len(decision.scores),
            "configs": [_score_json(score) for score in decision.scores],
        },
        "selected_config_id": decision.selected_config_id,
        "primary_config_id": decision.primary_config_id,
        "holdout": _holdout_json(decision),
        "benchmark": {
            "method": "buy_and_hold",
            "validation": _window_json(benchmark.validation),
            "holdout": _window_json(benchmark.holdout),
        },
        "overfitting": _overfitting_json(decision.overfitting),
        **({} if portfolio is None else {"portfolio": portfolio}),
        "reasons": list(decision.reasons),
        "limitations": [
            *LIMITATIONS,
            *(BAR_LIMITATIONS if portfolio is None else PANEL_LIMITATIONS),
        ],
        "generated_at_utc": _now(),
    }


def render_markdown(document: dict[str, Json]) -> str:
    """Operator report. Gross and net are both shown for a completed run."""

    status = document.get("status")
    label = document.get("label")
    promotion = document.get("promotion_decision")
    lines = [
        "# Research harness result",
        "",
        "PAPER research only. This artifact does not place orders.",
        "",
        f"- status: `{status}`",
        f"- label: `{label}`",
        f"- promotion_decision: `{promotion}`",
        f"- spec_sha256: `{document.get('spec_sha256')}`",
        f"- hypothesis_id: `{document.get('hypothesis_id')}`",
        "",
    ]
    costs = document.get("costs")
    if isinstance(costs, dict) and costs.get("allow_zero_latency") is True:
        lines.append("- allow_zero_latency: true")
        lines.append("")
    lines.extend(
        [
            "paper_candidate means H1 passed the untouched holdout after costs. "
            "It does not authorize LIVE, SHADOW, TESTNET, or order placement.",
            "",
            "## Reasons",
            "",
        ]
    )
    reasons = document.get("reasons")
    if isinstance(reasons, list) and reasons:
        lines.extend(f"- {reason}" for reason in reasons)
    else:
        lines.append("- none")
    if status == "completed":
        lines.extend(["", "## Validation", ""])
        lines.extend(_validation_lines(document))
        if isinstance(document.get("portfolio"), dict):
            lines.extend(["", "## Portfolio", ""])
            lines.extend(_portfolio_lines(document))
        lines.extend(["", "## Benchmark", ""])
        lines.extend(_benchmark_lines(document))
        lines.extend(["", "## Overfitting diagnostics", ""])
        lines.extend(_overfitting_lines(document))
        lines.extend(["", "## Holdout", ""])
        if document.get("holdout") is None:
            lines.append("holdout sealed (not evaluated)")
        else:
            lines.append("Gross and net are both reported.")
            lines.append("")
            lines.extend(_holdout_lines(document))
    lines.extend(["", "## Limitations", ""])
    limitations = document.get("limitations")
    if isinstance(limitations, list):
        lines.extend(f"- {item}" for item in limitations)
    lines.append("")
    return "\n".join(lines)


def dump_json(document: dict[str, Json]) -> str:
    return json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _validation_lines(document: dict[str, Json]) -> list[str]:
    testing = document.get("multiple_testing")
    if not isinstance(testing, dict):
        return ["- validation block missing"]
    configs = testing.get("configs")
    if not isinstance(configs, list):
        return ["- validation configs missing"]
    lines = [
        (
            "| config | trades | mean gross | mean funding | mean net 1.0 | mean net 2.0 "
            "| mean weight | p | bonferroni | holm | bh | selected |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for config in configs:
        if not isinstance(config, dict):
            continue
        gross = config.get("gross")
        net = config.get("net")
        adjusted = config.get("adjusted_p")
        lines.append(
            (
                "| {id} | {trades} | {gross} | {funding} | {net1} | {net2} "
                "| {weight} | {p_value} | {bonf} | {holm} | {bh} | {selected} |"
            ).format(
                id=config.get("id"),
                trades=_metric_field(gross, "trade_count"),
                gross=_metric_field(gross, "mean_return"),
                funding=_metric_field(config.get("funding"), "mean_return"),
                net1=_nested_metric(net, "1.0", "mean_return"),
                net2=_nested_metric(net, "2.0", "mean_return"),
                weight=config.get("mean_weight"),
                p_value=config.get("family_p_value"),
                bonf=_mapping_field(adjusted, "bonferroni"),
                holm=_mapping_field(adjusted, "holm"),
                bh=_mapping_field(adjusted, "bh"),
                selected=config.get("selected"),
            )
        )
    lines.append("")
    lines.append("p is the more conservative of the iid t and the Newey-West HAC t.")
    return lines


def _benchmark_lines(document: dict[str, Json]) -> list[str]:
    block = document.get("benchmark")
    if not isinstance(block, dict):
        return ["- benchmark block missing"]
    lines = [
        "Buy-and-hold: one unit long, filled after latency_bars, held to the window's "
        "last close. Funding is positive when received. Context only; it never changes "
        "the label.",
        "",
    ]
    for window in ("validation", "holdout"):
        values = block.get(window)
        if not isinstance(values, dict):
            lines.append(f"- {window}: block missing")
            continue
        status = values.get("status")
        if status != EVALUATED:
            state = _WINDOW_STATES.get(str(status), str(status))
            note = values.get("note")
            lines.append(f"- {window}: {state}" + ("" if note is None else f" ({note})"))
            continue
        lines.append(
            (
                "- {window}: {bars} bars held, gross `{gross}`, funding `{funding}`, "
                "net 1.0 `{net}`, Sharpe per bar `{sharpe}`"
            ).format(
                window=window,
                bars=values.get("bars_held"),
                gross=_shown(values.get("gross_return")),
                funding=_shown(values.get("funding")),
                net=_shown(_mapping_field(values.get("net"), "1.0")),
                sharpe=_shown(values.get("sharpe_per_bar")),
            )
        )
    return lines


_WINDOW_STATES: Final = {
    SEALED: "sealed (not evaluated)",
    NOT_APPLICABLE: "not applicable",
    NO_FOLDS: "no validation fold",
    TOO_SHORT: "window too short to hold after the fill",
    ERROR: "not computed (benchmark error; the label stands)",
}


def _portfolio_lines(document: dict[str, Json]) -> list[str]:
    block = document.get("portfolio")
    if not isinstance(block, dict):
        return []
    lines = [
        (
            "Each trade is one period: long the top quantile of the day's universe by the "
            "signal{short}, equal weight within a leg, one round trip on the capital. A symbol "
            "that stops trading while held exits at its last traded close (a forced exit)."
        ).format(short=" and short the bottom quantile" if block.get("signed") is True else ""),
        "",
        f"- universe: rank at most {block.get('universe_size')}, "
        f"at least {block.get('min_names_per_leg')} names per leg",
        "",
        (
            "| config | quantile | window | periods | skipped decisions | mean long names "
            "| mean short names | forced exits | uncovered funding days | unfunded halt days |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    configs = block.get("configs")
    if not isinstance(configs, list):
        return lines
    for config in configs:
        if not isinstance(config, dict):
            continue
        for window in ("validation", "holdout"):
            stats = config.get(window)
            if not isinstance(stats, dict):
                continue
            lines.append(
                "| {id} | {quantile} | {window} | {periods} | {skipped} | {long} | {short} "
                "| {forced} | {uncovered} | {unfunded} |".format(
                    id=config.get("id"),
                    quantile=config.get("quantile"),
                    window=window,
                    periods=stats.get("periods"),
                    skipped=stats.get("skipped_decisions"),
                    long=_shown(stats.get("mean_long_names")),
                    short=_shown(stats.get("mean_short_names")),
                    forced=stats.get("forced_exits"),
                    uncovered=stats.get("uncovered_funding_days"),
                    unfunded=stats.get("unfunded_halt_days"),
                )
            )
    return lines


def _shown(value: object) -> object:
    return "null" if value is None else value


def _overfitting_lines(document: dict[str, Json]) -> list[str]:
    block = document.get("overfitting")
    if not isinstance(block, dict):
        return ["- overfitting block missing"]
    lines = ["Diagnostics only; they never change the label.", ""]
    raw_dsr = block.get("deflated_sharpe")
    dsr = raw_dsr if isinstance(raw_dsr, dict) else {}
    which = "selected" if dsr.get("selected") is True else "best validation mean, none selected"
    if dsr.get("dsr") is not None:
        lines.append(
            (
                "- deflated Sharpe ratio: `{dsr}` for `{config}` ({which}; Sharpe per trade "
                "{sharpe}, {trades} trades, {trials} trials, noise maximum {benchmark})"
            ).format(
                dsr=dsr.get("dsr"),
                config=dsr.get("config_id"),
                which=which,
                sharpe=dsr.get("sharpe_per_trade"),
                trades=dsr.get("trades"),
                trials=dsr.get("trials"),
                benchmark=dsr.get("expected_max_sharpe"),
            )
        )
    else:
        config = dsr.get("config_id")
        tested = "" if config is None else f" for `{config}` ({which})"
        lines.append(f"- deflated Sharpe ratio: not computed{tested}. {dsr.get('note')}")
    raw_pbo = block.get("pbo")
    pbo = raw_pbo if isinstance(raw_pbo, dict) else {}
    if pbo.get("value") is not None:
        lines.append(
            "- probability of backtest overfitting: `{value}` (CSCV, {blocks} blocks over "
            "{folds} folds, {splits} splits, {skipped} skipped, in-sample trade floor "
            "{floor})".format(
                value=pbo.get("value"),
                blocks=pbo.get("blocks"),
                folds=pbo.get("folds_used"),
                splits=pbo.get("splits"),
                skipped=pbo.get("skipped_splits"),
                floor=pbo.get("in_sample_floor"),
            )
        )
    else:
        lines.append(f"- probability of backtest overfitting: not computed. {pbo.get('note')}")
    return lines


def _holdout_lines(document: dict[str, Json]) -> list[str]:
    holdout = document.get("holdout")
    if not isinstance(holdout, dict):
        return ["- holdout block missing"]
    lines = [f"- config: `{holdout.get('config_id')}`"]
    gross = holdout.get("gross")
    net = holdout.get("net")
    lines.append(f"- gross mean: `{_metric_field(gross, 'mean_return')}`")
    lines.append(f"- gross trades: `{_metric_field(gross, 'trade_count')}`")
    lines.append(f"- funding mean: `{_metric_field(holdout.get('funding'), 'mean_return')}`")
    lines.append(f"- mean weight: `{holdout.get('mean_weight')}`")
    if isinstance(net, dict):
        for key in ("1.0", "1.5", "2.0"):
            lines.append(f"- net {key} mean: `{_metric_field(net.get(key), 'mean_return')}`")
    return lines


def _holdout_json(decision: Decision) -> dict[str, Json] | None:
    if (
        decision.holdout_config_id is None
        or decision.holdout_gross is None
        or decision.holdout_net is None
    ):
        return None
    return {
        "config_id": decision.holdout_config_id,
        "gross": _metric_json(decision.holdout_gross),
        "net": {key: _metric_json(block) for key, block in decision.holdout_net.items()},
        "funding": _optional_metric_json(decision.holdout_funding),
        "mean_weight": decision.holdout_mean_weight,
    }


def _window_json(window: Window) -> dict[str, Json]:
    """``status`` is evaluated, sealed, no_folds, too_short, error or not_applicable;
    values only when evaluated, a note with error and not_applicable."""

    result = window.result
    if result is None:
        if window.note is not None:
            return {"status": window.status, "note": window.note}
        return {"status": window.status}
    values: dict[str, Json] = {"status": window.status}
    values.update(asdict(result))
    return values


def _provenance_json(origin: Provenance | None) -> dict[str, Json]:
    return {
        "source_environment": None if origin is None else origin.source_environment,
        "source_commit": None if origin is None else origin.source_commit,
        "image_digest": None if origin is None else origin.image_digest,
    }


def _overfitting_json(result: Overfitting) -> dict[str, Json]:
    dsr = result.deflated_sharpe
    pbo = result.pbo
    return {
        "deflated_sharpe": {
            "config_id": dsr.config_id,
            "selected": dsr.selected,
            "sharpe_per_trade": dsr.sharpe_per_trade,
            "trades": dsr.trades,
            "trials": dsr.trials,
            "null_sharpe_variance": dsr.null_sharpe_variance,
            "skewness": dsr.skewness,
            "kurtosis": dsr.kurtosis,
            "expected_max_sharpe": dsr.expected_max_sharpe,
            "dsr": dsr.dsr,
            "note": dsr.note,
        },
        "pbo": {
            "value": pbo.value,
            "in_sample_floor": pbo.in_sample_floor,
            "blocks": pbo.blocks,
            "folds_used": pbo.folds_used,
            "splits": pbo.splits,
            "skipped_splits": pbo.skipped_splits,
            "median_logit": pbo.median_logit,
            "note": pbo.note,
        },
    }


def _fold_json(fold: Fold) -> dict[str, Json]:
    return {
        "index": fold.index,
        "train_start": fold.train_start,
        "train_end": fold.train_end,
        "test_start": fold.test_start,
        "test_end": fold.test_end,
    }


def _score_json(score: ConfigScore) -> dict[str, Json]:
    return {
        "id": score.config_id,
        "threshold": score.threshold,
        "quantile": score.quantile,
        "horizon_bars": score.horizon_bars,
        "selected": score.selected,
        "family_p_value": score.family_p_value,
        "adjusted_p": dict(score.adjusted_p),
        "gross": _metric_json(score.validation_gross),
        "net": {key: _metric_json(block) for key, block in score.validation_net.items()},
        "funding": _optional_metric_json(score.validation_funding),
        "mean_weight": score.mean_weight,
    }


def _optional_metric_json(block: MetricBlock | None) -> dict[str, Json] | None:
    return None if block is None else _metric_json(block)


def _metric_json(block: MetricBlock) -> dict[str, Json]:
    return {
        "trade_count": block.trade_count,
        "mean_return": block.mean_return,
        "sum_return": block.sum_return,
        "stdev": block.stdev,
        "sharpe_per_trade": block.sharpe_per_trade,
        "max_drawdown": block.max_drawdown,
        "profit_factor": block.profit_factor,
        "win_rate": block.win_rate,
        "expectancy": block.expectancy,
        "t_stat": block.t_stat,
        "p_value": block.p_value,
        "naive_p_value": block.naive_p_value,
        "hac_t_stat": block.hac_t_stat,
        "hac_p_value": block.hac_p_value,
        "hac_lag": block.hac_lag,
    }


def _metric_field(value: object, field: str) -> object:
    if isinstance(value, dict):
        return value.get(field)
    return None


def _nested_metric(value: object, key: str, field: str) -> object:
    if isinstance(value, dict):
        return _metric_field(value.get(key), field)
    return None


def _mapping_field(value: object, key: str) -> object:
    if isinstance(value, dict):
        return value.get(key)
    return None


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
