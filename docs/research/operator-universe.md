# Operator research universe — 2026-10-31

The operator explicitly excludes ICP from the target trading universe.
The corresponding opt-in research selection is committed under
`config/research/operator-universe-2026-10-31/`. Its `policy.json` records
the decision and the SHA256 identities of the source snapshot, selected
snapshot and separate manifest.

The original Binance USD-M archive discovery snapshot remains unchanged:
`config/hist_etl/universe/binance-um-usdt-1d-2026-10-08.json` contains 896
members. The selected snapshot contains exactly those members minus
`ICPUSDT`: 895 metadata members, with 894 kline-symbol series and 895
funding-symbol series. GAIBUSDT already has no daily kline series in the
original snapshot. All other month runs and the five original technical
ticker exclusions are preserved.

This is an operator selection, not a new scientific classification of ICP.
The technical `excluded` map is unchanged and does not contain ICP. Normal
crypto, eligibility and risk filters remain separate requirements. The
snapshot's `as_of` remains the original archive-discovery date; the policy's
`decision_date` is 2026-10-31. The selection does not claim that today's
operator preference was known at historical decision times.

## Use the separate manifest explicitly

Manifest:
`config/research/operator-universe-2026-10-31/datasets.toml`

Group: `bn-um-usdt-1d-operator-2026-10-31`

The existing `research.hist_etl` parser expands this selected snapshot into
both daily kline and funding inputs. ICP is absent before input planning
and coverage validation; this is not a waiver of missing ICP rows inside
an otherwise selected dataset. The global manifest remains unchanged.
The new group is disabled, so it requires an explicit dataset selection.

A read-only file plan against an already verified local input root uses:

```bash
PYTHONPATH=src TRADING_MODE=PAPER uv run --frozen python -m research.hist_etl plan \
  --manifest config/research/operator-universe-2026-10-31/datasets.toml \
  --dataset bn-um-usdt-1d-operator-2026-10-31 \
  --root "$VERIFIED_LOCAL_INPUT_ROOT" \
  --today 2026-10-31
```

For an independently approved panel preparation, pass the same
`--manifest` and `--group` to the existing `research.bar_tables panel`
command. The remaining panel parameters and research gates belong to its
preregistration; this config does not supply or satisfy them. A ranking
exclusion file must name the selected universe filename and contain only
members of that selected universe. Do not silently reuse a list bound to
the original snapshot or mislabel ICP as non-crypto.

## Evidence and readiness boundary

ICP archive-availability gaps were already observed before the operator
made this choice. No S4 strategy was scored in this continuation. Record
that chronology when revising the still-unregistered S4 proposal, its
universe/dataset identity, spec hashes, input fingerprints and source pins.
The original full-universe proposal and its data-gap evidence remain
historical records, not results for this narrower selection.

This config does not amend hypotheses, costs, grids, periods or statistical
thresholds, repair data, run research or prove readiness. Other selected
symbols still require their own coverage and provenance checks. Durable
archives are preserved; no capture, ETL, database or NAS operation runs
merely because this config is merged.

The manifest selects research inputs. It is not a generic execution,
signing or LIVE enforcement mechanism. The existing native PAPER engine is
already BTC-only. Future execution work must continue to honor the
operator's ICP exclusion and its separate environment/risk gates; this
change enables no trading mode and deploys nothing.
