# S4 operator-universe amendment — 2026-10-31

The operator explicitly states that ICP will not be traded. This version
of the existing, still-unregistered S4 proposal therefore excludes
ICPUSDT from input planning, coverage, features and volume ranking. It is
not an exception that permits missing rows for a selected instrument.
Original archives and the full-source classification remain unchanged.

The choice followed source-gap observations and preceded strategy
scoring in this continuation. It was not selected from returns or signal
values. Removing an instrument also changes other instruments' volume
ranks/top-50 membership. No panel counts, fingerprints or shape results
from another universe are reused. Conclusions apply only to this reduced
operator-selected cohort, not to the full exchange universe.

## Source, config and proposal identities

The operator config merged in [PR #189](https://github.com/daffieeee-arch/Hyperliquid-Bot/pull/189)
at source/config pin `d1378756e25ba04a580061a304037290f1fc9538`.
`src`, `uv.lock`, `pyproject.toml` and `.python-version` are byte-identical
to the reviewed previous preparation source `0cd7666`; the new pin also
contains the selected config. Python remains 3.13.15, DuckDB 1.5.5 and
certifi 2026.10.19. There is no dependency override.

- Manifest: `config/research/operator-universe-2026-10-31/datasets.toml`.
- Explicit group: `bn-um-usdt-1d-operator-2026-10-31`, disabled by default.
- Its policy binds the original source inventory, selected snapshot and
  manifest hashes. The source inventory still has 896 members; the view
  has exactly 895, with every other month run and technical exclusion
  preserved. Its historical `as_of` is not the operator decision date.
- Classification source remains 896 members / 675 crypto / 221 non-crypto.
  The selected view has 895 / 674 / 221. ICP is not called non-crypto.
- The original 221-name non-crypto file remains intact. The selected
  `config/research/binance-um-usdt-non-crypto-operator-2026-10-31.json`
  changes only its universe filename pointer, retaining every category
  and rule. Its SHA256 is
  `512277bd0d560b7950a51e8e6c779d3353e726624af6b789dc1b36ab832de58c`.

The machine-readable [operator input amendment](exp_xs_usdm.operator-inputs.json)
records both old and new hypothesis IDs/spec hashes, exact config/source
identities and a golden hash of every unchanged spec field. Only
`hypothesis_id`, `universe` and `dataset_version` change. Hypotheses,
features, grids, costs, split/holdout dates, sample floors and predefined
statistical/shape remedies do not change. This versions an unregistered
proposal; it does not re-register a scored or failed strategy.

## Selected recovery source set

The original 323-pair full-universe ledger and failed preparation are
retained in [the historical coverage record](exp_xs_usdm.coverage.json).
That original all-323 recovery gate failed, and no partial repair ran.
It is not retrospectively declared a success.

Under the explicit operator selection, the new closed set is **297**
non-ICP day/symbol pairs in **51** symbols and **102** months. Every
selected native daily ZIP has verified official provenance, sibling
CHECKSUM/SHA256, CRC and exactly the requested UTC day. SHA256 of the
canonical sorted-key compact JSON encoding of `selected_missing_days`
is `b4537b510a244056cd01f64d2a6f8606bb3acccf46d03038269fdac2a3c5da31`.
The existing planner requires **42,272** Parquet/sidecar pairs, with
21,293 kline and 20,979 funding files; selected planning, conversion and
panel queries require no ICP inputs. The preservation check may still
byte-hash the full original B seal, including legacy ICP files, solely to
prove that originals remain unchanged. Those bytes are not decoded as
selected research data or fed to the panel.
Full time/funding coverage still needs actual preparation checks.

The [historical daily-source recovery procedure](exp_xs_usdm.data-repair.md)
is superseded only in universe scope: its closed-set gate now applies to
all 297 selected pairs, not to unused ICP data. Its integrity requirements
remain. A reviewed local-only wrapper must:

1. Verify the original sealed local input copy and all selected raw
   source/checksum identities before and after use; preserve original
   snapshots and NAS bytes.
2. Copy unaffected selected inputs byte-for-byte into a fresh private
   local root. Do not hardlink or overwrite originals. Do not copy the
   102 affected original Parquets/sidecars into their output slots.
3. Reject overlapping source timestamps before normalization. Invoke
   the unchanged `materialize_binance_month` with explicit original
   monthly plus only the frozen missing native daily `SourceDigest`s,
   `rebuild=False` and absent output/partial/sidecar paths. Require a
   return path **and no gaps**, since a path alone can accompany a failure.
4. Compare all existing normalized fields programmatically, including
   source identity fields, and require exactly the frozen additions.
   Report only mismatch counts, timestamps and hashes; never manual
   numerical financial values. No alternate interval, imputation,
   lifecycle waiver or new pricing algorithm is introduced.
5. Seal every selected Parquet and sidecar, all raw-source identities,
   config/policy/source/wrapper hashes, exact additions and unaffected
   hashes. COMPLETE is last. Failed local reservations stay incomplete;
   do not resume, overwrite, shrink the set or retry automatically.

Before-recovery contract review and a separate review of the actual
wrapper are required. This document alone authorizes no execution. Local
CPU/RAM/bytes/time limits, PAPER startup and no-network/no-NAS-write
boundaries remain mandatory. Approved conversion and equality checks may
process source financial fields internally but expose only metadata and
counts; no returns or strategy selection may be computed.

## Remaining preparation and merge gates

Only after that new complete input identity is independently accepted may
the selected panel be built. Both independent builds and original
1/2/8/32-thread byte checks, both matching lock fingerprints, and the
existing shape-only helper/remedies must run against this exact view.
All measured placeholders remain open until actual evidence exists.
The declared 2020-01-01..2026-09-01 panel and forward-data boundary remain.

The source pin check now includes the selected config directory as well
as source, dependency/interpreter files and the original source inventory;
the selected non-crypto file retains its explicit hash check. No ignored
source/dependency exception is added. Use fresh bytecode scratch for
preparation and verify clean tracked/untracked source.

The final post-merge scoring recipe must use the sealed local selected
inputs, prove the expected input identity and rebuild/fingerprint the
same table. Legacy automatic `hist_etl sync` is not allowed over that
curated root: the ordinary planner prefers incomplete monthly archives
and does not implement this explicit mixed-source recovery. No strategy
run, beta/overlap values or promotion is part of this preparation. Final
preregistration review and actual ready-head CI remain separate gates.
