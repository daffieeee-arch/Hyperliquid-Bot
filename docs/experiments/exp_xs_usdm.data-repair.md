# S4 pre-build daily-source recovery amendment

> **Current operator version:** the user's no-ICP target is now recorded
> in [the operator amendment](exp_xs_usdm.operator-scope.md). The
> full-universe source/pin/323-gap observations below are historical;
> they are not declared passed or erased. Current recovery is scoped to
> all 297 selected native-daily gaps after independent review. Follow the
> explicit selected manifest; do not run legacy sync/scoring blocks.

This is a proposed data-quality remedy for the existing draft #146/#150,
not a new strategy or an authorization to score one. Review this amendment
before any recovery or new panel build. The original failed preparation
and its immutable input snapshot remain recorded. No S4 strategy has run;
no panel, shape result or data fingerprint was produced by that attempt.

## Measured fault and source evidence

The complete local timestamp/symbol audit read 636,990 rows from 21,357
kline files for 860 symbols. The original pinned time validator rejects
52 symbol runs, with 323 interior missing day/symbol pairs across 103
symbol/months, all in 2022 or 2023. No duplicate symbol/timestamps,
file-symbol/month/close-clock discrepancies, sidecar row-count mismatches
or empty monthly files were observed. The first actual panel build stopped
at BNXUSDT 2022-04-17; it was not retried on the unchanged input.

The exact metadata-only list and bounded source observations are committed
in [exp_xs_usdm.coverage.json](exp_xs_usdm.coverage.json). SHA256 of the
canonical sorted-key compact JSON encoding of its `interior_missing_days`
array is `8f893aff6669d3b751f52d8a423fc605d4b72d3f43f4affcf943fac152c2cd16`.
Freeze that complete list before recovery; do not choose assets or dates
from returns, signals, prices, volumes or funding values.

Two retained monthly ZIP samples pass SHA256, sibling CHECKSUM and CRC,
and their timestamp projections exactly match Parquet: BNXUSDT April
2022 lacks April 17; FILUSDT February 2022 lacks February 26–28. Small
fresh official monthly downloads have identical hashes and gaps. Official
1d daily ZIP/CHECKSUM samples for BNX April 17 and FIL February 26/28 are
available and contain exactly their requested day. The completed closed-set follow-up verified all 103 retained monthly
ZIP/CHECKSUM pairs (175,309 bytes read from NAS); all 323 missing timestamps
are absent already from those raw sources. Of 323 official daily pairs,
318 pass checksum/CRC/exact-day validation; ICPUSDT September 22–26,
2022 return HTTP 404 for both ZIP and CHECKSUM. The exact-window public
1d kline API diagnostic returned HTTP 200 and zero rows. This reports
current source availability, not the historical cause of missing bars.

Binance publishes both daily and monthly archives and their checksums;
archives may later be corrected. Use only its [official public-data
layout](https://github.com/binance/binance-public-data/blob/master/README.md).
A correct checksum proves bytes, not complete calendar coverage.

## Uniform proposed remedy before registration

1. Check every one of the frozen 323 missing pairs against its exact
   official USD-M **1d** daily ZIP and sibling CHECKSUM. Require valid
   SHA256/CRC, the known schema and exactly the requested UTC open/close
   timestamps. Bound requests, bytes, decompression and runtime. A missing
   or invalid day is a blocker; never fill it with zero, a previous price,
   an estimated candle, another interval or another venue.
2. Copy the 103 affected retained monthly ZIPs and CHECKSUMs into a new
   private local quarantine. Their hashes must match the original sidecars
   and retained checksums. The NAS and original local snapshot stay intact.
3. Independently review the closed input manifest, source identities,
   recipe and bounded recovery wrapper before normalization. Numerical
   financial values are never exposed for manual inspection or selection.
4. Create a new private local derived-input root. Copy unaffected files
   byte-for-byte; do not hardlink sealed originals (that changes their
   recorded inode metadata). Do not copy the 103 affected Parquets and
   their sidecars into those output slots.
5. For each affected month, use the unchanged pinned
   `research.hist_etl.binance_convert.materialize_binance_month` with an
   explicit `SourceDigest` list: the original monthly archive followed by
   only its frozen missing daily archives. Use `rebuild=False` and fresh
   output paths. The existing normalizer validates schema, finite values
   and conflicts, and writes complete source hashes into the new sidecar.
   No new aggregation, pricing or imputation algorithm is introduced.
6. Programmatically verify that existing normalized rows are preserved,
   added timestamps are exactly the frozen missing set, and unaffected
   files retain their hashes. Only metadata and mismatch counts may be
   reported. Record a new complete input manifest/seal and every attempt;
   stop on any source, schema, conflict, coverage or resource failure.
7. Repeat the bounded panel preparation only after independent acceptance
   of that new input identity. Preserve source pin, specs, exclusions,
   grids, costs, split boundaries and original shape/failure remedies.
   Run the two independent builds and original thread checks, lock and
   shape gates before filling placeholders or requesting final merge.

The source remains `0cd7666392928b0af3f4a0c0ef5c8fc34a516d58`; the proposed
change is an explicit **unregistered input-data** recovery, not a silent
replacement of a previously fingerprinted panel or scored study. If a
research semantic change is required, stop and review it separately. No
previous STOP study may be re-registered through this procedure.

The ordinary planner prefers monthly archives; adding daily ZIPs to its
root alone does not implement this remedy. Do not run legacy `sync` against
the curated root. Before any eventual post-merge scoring, replace/reconcile
the legacy run blocks with the reviewed frozen-input recipe and exact
fingerprint checks; they are not usable as written for this amendment.

**Recovery is stopped:** the all-323 source gate failed. The 318 verified
available days remain quarantined and are not applied as a partial repair.
No curator was executed and no second panel build followed. The missing
five source days require a separately reviewed source or missing-data
contract proposal; do not silently drop ICPUSDT, estimate candles, accept
gaps, change a threshold or move a study period. This is a failed
preparation gate, not a scored strategy failure or evidence of no edge.

No recovery has run. Funding coverage,
rebuild/fingerprint/shape evidence and final preregistration review remain
open. No NAS writes, strategy scoring or trading/runtime promotion follows.
