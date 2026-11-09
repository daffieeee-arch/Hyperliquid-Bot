# November research accounting repair

Checkpoint 2026-11-09, issue #220. The Nov2 momentum and Nov7 whale, retail,
funding and volume investigations lived in Claude session scratchpads. Their
source is partly recoverable from surviving transcripts. Original scratchpad
file identities, downloaded account snapshots and financial result artifacts
are unavailable. Their reported statistics require revalidation.

## Corrected executable path

`python -m research.repaired_replay` consumes a bounded offline JSON export of
prices, funding coverage and execution targets. It uses the fixed-quantity
ledger in `research.repaired_accounting`. The existing frozen experiment runners
keep their original code/specification identities. The new path repairs the
accounting of explicitly exported targets. Reconstructing the original strategy
signals and qualifying their original observations is a separate admission step.

The ledger sizes units at explicit execution targets and holds those units until
another execution. Every trade changes cash and pays fees on its actual fill
notional. Equity is cash plus marked inventory. Perpetual funding uses the units
held before the timestamp's execution and the settlement mark at that timestamp.
Each timestamp is valued, including open trades and the final close with fees.
Daily results must derive from equity endpoints. Adding hourly percentage
returns would recreate the original error.

The synthetic fixture exercises the normal entrypoint. Run from a reviewed
checkout with pinned dependencies and an existing local output parent. The
writer reserves a new private output directory.

```bash
PYTHONPATH=src .venv/bin/python -m research.repaired_replay \
  --input "$PWD/tests/python/fixtures/repaired-replay-roundtrip.json" \
  --output /absolute/private/new-roundtrip-replay
```

The fixture's zero source commit is an explicit synthetic placeholder. The
result records it as `declared_source_commit` and separately hashes the actual
implementation modules. Use the reviewed source commit for an actual export.
Outputs retain the original input bytes, input hash, source hashes, complete
ledger and a completion marker written last. Every result has
`promotion_decision: forbidden`.

## Export contract

The committed fixture is a complete schema example. Schema version 1 requires
`purpose: retrospective_repair`, a bounded `study_id`, `study_kind` of
`positions`, `volume` or `hedged_carry`, and `data_status` of `synthetic` or
`development_exposed`. Unknown fields are refused at each schema level.

- `spec` fixes `start_ms`, `end_ms`, positive `step_ms`, initial equity and
  instruments. Instrument IDs identify distinct venue products. Each instrument
  declares spot/perp kind, fee and adverse-slippage basis points. Perpetual
  `price_kind` must be `perp_mark`; spot uses `spot_close`. A trade close cannot
  substitute for a funding settlement mark.
- `frames` cover the complete regular grid, including both boundary valuations.
  `marks` contain positive finite prices for every held or executed instrument.
  A frame's perpetual `funding_rates` entry certifies complete coverage through
  that interval. Explicit zero certifies zero payment. An absent/null value is
  unknown and fails a held interval. Use finer frames when multiple funding
  events need distinct settlement marks. Admitting an export requires reviewing
  this coverage against original native events.
- `targets` carry decision/execution times and weights relative to pretrade
  equity. `minimum_latency_ms` must be positive. Decisions and executions lie
  within the window, executions are ordered and on grid. With `replace_all:
  true`, the target replaces the whole portfolio. With `false`, it updates only
  named instruments and preserves other open quantities. Weight zero closes a
  named position. The end valuation closes all remaining quantities with costs.
- Volume exports declare every `trade_windows` item with symbol, entry and exit
  times. The complete holding window must fit. Matching partial execution
  targets must open and close each position, except the terminal close performed
  by the ledger. Overlapping same-symbol trades and portfolios above ten slots
  are refused. This prevents a boundary trade being counted in trade statistics
  while its PnL disappears from the equity series.
- Carry exports declare `pairs` with matching underlying asset identifiers,
  separate actual spot/perpetual prices and equal opposite units at each paired
  target. Premium-index proxies and unmatched legs are refused. Carry output
  retains `economic_assessment: blocked_margin_model`. Collateral, financing,
  liquidation, venue and transfer risks require their own model and evidence.
- Actual exports require nonempty `sources` with absolute regular-file paths
  and SHA-256 hashes. Sources are checked before and after replay. An individual
  source is bounded to 64MiB, at most 32 sources are accepted, input JSON to
  16MiB, observations to 100,000 and instruments to 100. Source binding preserves
  bytes; it does not prove the export transformation or instrument semantics.

A fixed `scale` replays the declared position size. Alternatively, `calibration`
contains `stage: development`, at most 32 predeclared distinct candidate scales
and `max_drawdown`. Every candidate is replayed using marked equity and costs;
the highest passing candidate is retained. The calculation measures the supplied
historical sample and sampling grid. Drawdown between samples, future loss limits
and margin safety require additional evidence.

## Recovered evidence and research disposition

The [recovery tool](session-recovery.md) reconstructed 24 selected source/plan
files from two session logs. The create-only private VM bundle is
`2026-11-09-claude-source-recovery`. Its manifest SHA-256 is
`d1d5a96962307b87a139fe8aa67130f7e6fc793ca4fb3d07d13a460cfcf37382`.
The manifest records 11 and 57 unresolved observations respectively. Some are
uninterpreted shell fragments; complete final-file recovery is uncertified.
Recovered scripts remain quarantined evidence. Their bugs are preserved in that
evidence, while the tested accounting entrypoint provides the correction path.

Both fresh durable copies were independently read back in full, 25 files and
105,344 bytes each, with matching hashes and private permissions. VM mount paths
are `/mnt/hyperliquid-work/research/claude-research-recovery-20261109` and
`/mnt/hyperliquid-archive/market-data/claude-research-recovery-20261109`. Independent
copy acceptance SHA-256 is
`24e232740cf8771f86b4a967fb62d17a1124aeabe553f6917685df18d8d8c5d4`.

The [study register](historical-study-register.md) records the external studies,
exposed periods and superseded risk statements. In particular, the old carry
description as a safe cash-parking strategy is withdrawn pending actual leg,
coverage and margin validation. A corrected replay of exposed history remains
retrospective. Every eventual rescore must retain its new code/input identities,
original and corrected results, limitations and independent review. Full original
whale/account reconstruction remains blocked by the missing snapshot inputs.
