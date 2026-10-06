"""JSON and markdown artifacts. paper_candidate is not a LIVE authorization."""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from typing import Final

from research.harness.costs import STRESS_MULTIPLIERS, round_trip_cost, stress_key
from research.harness.data import BarTable
from research.harness.errors import HarnessError
from research.harness.evaluate import ConfigScore, Decision, MetricBlock
from research.harness.spec import HypothesisSpec, Json
from research.harness.splits import Fold

HARNESS_VERSION: Final = "1"
_ENVIRONMENTS: Final = frozenset({"DEV", "CI", "VPS_RESEARCH"})
LIMITATIONS: Final[tuple[str, ...]] = (
    "The t-test treats trades as iid. Persistent regimes can inflate significance.",
    "Sharpe is per trade, not annualized. Drawdown sums simple returns.",
    "Costs are flat bps, stressed at 1.0x, 1.5x, and 2.0x. No queue or funding cashflow.",
    "Latency fills at the close of decision_bar + latency_bars. Zero latency is optimistic.",
    "Look-ahead control uses the declared clock. A falsely stamped future value is invisible.",
    "paper_candidate is not LIVE, SHADOW, TESTNET, or an order authorization.",
    "Spot Vision timestamps from 2025-01-01 are microseconds; USD-M examples are milliseconds.",
    "Kraken OHLCVT omits empty intervals and is USD-quoted, not USDT.",
)


def source_environment() -> str:
    raw = os.environ.get("RESEARCH_ENV", "DEV")
    if raw not in _ENVIRONMENTS:
        raise HarnessError("data_config", "RESEARCH_ENV must be DEV, CI, or VPS_RESEARCH.")
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
) -> dict[str, Json]:
    return {
        "harness_version": HARNESS_VERSION,
        "status": "failed_closed",
        "failure_kind": failure_kind,
        "label": None,
        "promotion_decision": "forbidden",
        "trading_mode": trading_mode,
        "spec_sha256": spec_sha256,
        "hypothesis_id": hypothesis_id,
        "reasons": list(reasons),
        "limitations": list(LIMITATIONS),
        "generated_at_utc": _now(),
    }


def completed_document(
    spec: HypothesisSpec,
    digest: str,
    table: BarTable,
    decision: Decision,
) -> dict[str, Json]:
    timestamps = table.timestamps
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
        "source_environment": source_environment(),
        "source_commit": source_commit(),
        "image_digest": None,
        "bar_count": len(timestamps),
        "timestamp_min": timestamps[0] if timestamps else None,
        "timestamp_max": timestamps[-1] if timestamps else None,
        "costs": {
            "fee_bps": spec.costs.fee_bps,
            "slippage_bps": spec.costs.slippage_bps,
            "spread_bps": spec.costs.spread_bps,
            "latency_bars": spec.costs.latency_bars,
            "round_trip_cost": {
                stress_key(stress): round_trip_cost(spec.costs, stress)
                for stress in STRESS_MULTIPLIERS
            },
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
        "holdout": {
            "config_id": decision.holdout_config_id,
            "gross": _metric_json(decision.holdout_gross),
            "net": {key: _metric_json(block) for key, block in decision.holdout_net.items()},
        },
        "reasons": list(decision.reasons),
        "limitations": list(LIMITATIONS),
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
        "paper_candidate means H1 passed the untouched holdout after costs. "
        "It does not authorize LIVE, SHADOW, TESTNET, or order placement.",
        "",
        "## Reasons",
        "",
    ]
    reasons = document.get("reasons")
    if isinstance(reasons, list) and reasons:
        lines.extend(f"- {reason}" for reason in reasons)
    else:
        lines.append("- none")
    if status == "completed":
        lines.extend(["", "## Validation", ""])
        lines.extend(_validation_lines(document))
        lines.extend(["", "## Holdout", "", "Gross and net are both reported.", ""])
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
            "| config | trades | mean gross | mean net 1.0 | mean net 2.0 "
            "| p | bonferroni | holm | bh | selected |"
        ),
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for config in configs:
        if not isinstance(config, dict):
            continue
        gross = config.get("gross")
        net = config.get("net")
        adjusted = config.get("adjusted_p")
        lines.append(
            (
                "| {id} | {trades} | {gross} | {net1} | {net2} "
                "| {p_value} | {bonf} | {holm} | {bh} | {selected} |"
            ).format(
                id=config.get("id"),
                trades=_metric_field(gross, "trade_count"),
                gross=_metric_field(gross, "mean_return"),
                net1=_nested_metric(net, "1.0", "mean_return"),
                net2=_nested_metric(net, "2.0", "mean_return"),
                p_value=config.get("family_p_value"),
                bonf=_mapping_field(adjusted, "bonferroni"),
                holm=_mapping_field(adjusted, "holm"),
                bh=_mapping_field(adjusted, "bh"),
                selected=config.get("selected"),
            )
        )
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
    if isinstance(net, dict):
        for key in ("1.0", "1.5", "2.0"):
            lines.append(f"- net {key} mean: `{_metric_field(net.get(key), 'mean_return')}`")
    return lines


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
        "horizon_bars": score.horizon_bars,
        "selected": score.selected,
        "family_p_value": score.family_p_value,
        "adjusted_p": dict(score.adjusted_p),
        "gross": _metric_json(score.validation_gross),
        "net": {key: _metric_json(block) for key, block in score.validation_net.items()},
    }


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
