# Run the fixed historical calendar proxy study

This is the implementation of the
[registered month-turn study](../experiments/exp_btc_month_turn_proxy.md).
It reuses the existing harness's calendar-independent trade collection,
cashflows, costs, statistics, folds and selection. The additional paired-control
validation gate runs before the exposed historical test's prices are loaded.
No network client, venue order API, signing or trading service is involved.

## Source and data gates

Before executing, independently review the final runner/spec diff and synthetic
tests, pass current ready-head CI and merge through a separate merge agent.
Use a clean reviewed commit on `origin/main`. No source merge runs this study.

Independently qualify the complete original 80 monthly Binance BTCUSDT USD-M
hourly candle files and 80 native funding files, Jan2020 through Aug2026. The
strict input JSON has exactly:

- `format: 1`, `venue: binance-usdm`, `symbol: BTCUSDT`;
- `start_ms: 1577836800000`, `end_ms: 1788220800000` (exclusive);
- `bars` and `funding`: ordered lists of `{path, sha256}`, with all monthly
  `BTCUSDT_YYYY-MM.parquet` files and no alias/subset/glob;
- `qualification`: three `{path, sha256}` pins for the original candle, funding
  and current official checksum-comparison reports.

The operator verifies the independent source-acceptance report covers exactly
these selectors and proofs. A SHA syntax check does not authenticate a reviewer
or certify source quality. Keep report evidence with the operator execution
record. No source path, dataset or reviewer identity is embedded in code.

The runner independently checks original hashes, full timestamp/asset census,
unique complete funding slots and staged source continuity. Literal no-follow
paths and sealed memfds prevent archived SQL, extensions or pathname swaps from
becoming research inputs. Each source file is capped at4MiB; total inputs64MiB.
DuckDB uses at most2threads/2GB without spill; process address space is capped at
8GiB or a tighter inherited limit. These bounds do not authorize truncating data.

## Separate explicit operator execution

Create a new private local output name outside Git; the parent must already
exist. NFS output and existing directories refuse execution. The explicit GO
JSON has exactly `study`, `source_commit`, `inputs_sha256`, `spec_sha256`,
`output`, `review_sha256`, `data_acceptance_sha256` and `explicit_go: true`.
`spec_sha256` is the canonical existing-harness hash. Both report hashes identify
the actual independent reviews the operator has checked. Bind the full GO bytes
by the separate command argument; approval of another source or output is not GO.

```bash
PYTHONPATH=src uv run --frozen python -B -m hyperliquid_bot.calendar_research \
  --inputs /absolute/approved-inputs.json \
  --inputs-sha256 <approved-input-manifest-sha256> \
  --source-commit <reviewed-merged-source-commit> \
  --review-go /absolute/explicit-operator-go.json \
  --review-go-sha256 <approved-operator-go-sha256> \
  --output /absolute/new-private-local-study
```

PAPER is required. Before any financial decode, the fixed hourly and native
funding clock census must pass. The first stage reads only pre2025 prices/rates.
`validation.json` is written create-only, fsynced and bound to source/spec/input
and GO hashes **before** the existing selector can request historical-test data.
Both positive primary net and paired midmonth excess must pass the frozen
sample/p-value/stress gates. Failure preserves the historical test's sealed state.
Even buy-and-hold for that stage remains gated. After evaluation, source,
manifest, spec and GO identities are checked again.

Outputs retain the exact spec/input/GO, input lock, validation evidence and final
result. An execution failure retains partial outputs and a failure result where
possible. Do not resume, overwrite or retune a failed run. Require actual process
exit0 and independent result/artifact acceptance before publishing a new copy to
NAS. This tool never mutates or deletes durable data, or installs background jobs.

## Interpretation

Every result is `historical_*`, exploratory and promotion-forbidden, including
nested positive harness decisions. `historical_interesting_but_unconfirmed`
requires the existing primary harness pass plus the paired historical-test rule;
it is not Hyperliquid profitability or PAPER admission. The historical test is
already exposed, and no fresh future period is assigned here.

Keep the flat entry-notional cost model and hourly-price funding valuation
limitations in the protocol. Native event timestamps are not rounded before
exposure classification. This is Binance reference data in USDT, not a converted
Hyperliquid price/funding/settlement model. Existing Spot-flow preregistration,
Nov2-5 OOS, S4 and deployed collector images remain unchanged.
