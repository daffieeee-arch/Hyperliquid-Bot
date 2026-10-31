# S4 preparation readiness on the Ubuntu VM

This continues **existing draft PR #146** and issue #150. It is a
pre-registration/source-environment amendment before any S4 panel build
or shape inspection in this continuation. It does not register a new
study, change a spec, score a strategy or grant trading/runtime authority.
The PR remains a draft until its original measured prerequisites and
independent final review are complete.

## Source and environment amendment

The original code pin was
`694497a089409b15b49d1ed7ad4abe9db2986054` (#145). The original PR head
`837280223ea1fcb28a4354adfcf2f8d94b284b28` matched it across the complete
broad pin: `src`, `uv.lock`, `pyproject.toml`, `.python-version` and
`config/hist_etl/universe/binance-um-usdt-1d-2026-10-08.json`.

The proposed amended pin is
`0cd7666392928b0af3f4a0c0ef5c8fc34a516d58`. Main at that commit was
merged normally into this existing PR branch. Both study docs and both
run-script `pin` variables name that same commit. The broad pin check
remains intact, including untracked/ignored source checks: there is no
exception that silently loads a newer dependency or source module.

Independent review of this exact amendment is required **before** a
panel build. Review evidence is recorded in the PR with exact head/base
commit IDs. Until it is recorded, preparation is blocked. This checkpoint
is separate from final review of the measured pre-registration.

Compared with the original pin:

- `research/bar_tables` (including exclusion, ranking, feature and funding
  edge semantics), `research/harness`, universe/manifest/planning modules,
  the universe JSON, `pyproject.toml` and `.python-version` are byte-identical.
- ETL changes in `binance_convert.py`, `hyperliquid.py`, `kraken.py` and
  the new `finite.py` reject non-finite financial inputs and audit existing
  output more strictly. They do not re-transform archived data here. The
  amended source must not be used to silently repair files or reinterpret
  a previous study result.
- Changes/additions in `hyperliquid_bot` concern PAPER risk/execution,
  operator isolation, migration/catalog validation and the offline PAPER
  command. The S4 panel/harness does not import those modules.
- The only locked dependency change is the security update
  `certifi 2026.7.22` to `2026.10.19` (#157). Python remains `3.13.15`
  and DuckDB remains `1.5.5`. Restoring the old CA bundle to reproduce an
  obsolete environment is not an approved workaround.

The old `uv.lock` SHA256 is
`edb4ee09b2effca731043b49ea61477ebc72f8047dc2b7f16317f0b2ae9d905d`.
The amended lock SHA256 is
`eda24fdd682f62b175e68474b5a8f07627558b86d7dc72f333a5c742d6f9aa58`.
This records an environment change explicitly; it does not claim the two
lockfiles are equivalent or preserve an earlier panel fingerprint.

Hypotheses, costs, grids, train/validation/holdout dates, original failure
remedies and known-holdout limitations remain unchanged. In particular,
`harness run`, overlap diagnostics and real-data beta/return checks remain
prohibited before the final pre-registration merge. No previous STOP
study is re-registered by this amendment.

## Frozen metadata consistency (2026-10-31)

The checked-in classification contains all **896** universe symbols:
675 crypto and 221 non-crypto. Its non-crypto projection exactly equals
the frozen exclusions: 159 equity, 38 ETF/ETP, 10 commodity, 6 index,
6 private company and 2 fiat-pegged/FX contracts. Every classification
entry has a source label. This is consistency of the frozen metadata,
not a fresh independent validation of all upstream sources or proof that
the metadata was available point in time.

| Input | SHA256 |
| --- | --- |
| Universe | `add8d0f26d6b7c2bdf18963cf1f77752f0b1aa8a943a64c868e097951f4ec520` |
| Classification | `065f7ba001e4679ec22d2ca3ef5567dcd277ce0fbe0b7eab6b5078f96caacad7` |
| Exclusions | `37dc30be8a1aa95c983ec0d2e091ed9297d9f452e85fd51ee03b75fbff5d5348` |

The canonical spec hashes remain:

- Momentum: `c8c5d93142e27e468c642fd576d8cc7f2263dd05b70bdc2a0998c77285fbbd08`.
- Carry: `457009762711c0ac113719360fc7d04431f67f63ce10a88a6d02b74e0f0db44a`.

## Exact archive coverage scope

Archive root: `/mnt/hyperliquid-archive/vps-chupa/hist-archives`, bound
explicitly to `/mnt/hyperliquid-archive` on
`192.168.1.234:/mnt/tank/hyperliquid-archive`, filesystem `nfs4`.
Inputs are read-only. Legacy post-merge study run blocks still call
`hist_etl sync`/`verify`; they are not authorized or executed by this
preparation, and must not be pointed at the NAS. Before any eventual
scoring, reconcile those operational instructions separately with the
locked local input snapshot and forward-data boundary. Outputs stay in
private local DEV scratch outside
Git and outside the NAS mounts. No copied catalog SQL or archived DuckDB
database is executed.

The original planner is evaluated for `bn-um-usdt-1d`,
`[2020-01-01, 2026-09-01)`, lookback 14, volatility/volume windows 30
and funding window 7. Its 30-day warm-up reaches 2019-12-02 but is clipped
to each declared universe run; it does not invent pre-listing files.

The exact plan comprises **42,385** Parquet paths and as many sidecars:
21,357 kline files and 21,028 funding files. Of the funding paths,
20,325 are required by the builder and 703 are following-boundary files
that its open-run rule includes only when present. The original S4
contract assumes those published boundary files are present too, so they
are checked explicitly as an additional S4 prerequisite; they are not
silently treated as optional just because the builder would skip them.

A bounded descriptor-anchored audit checks these explicit paths, effective
mount identity and sidecar metadata. Presence is not row coverage or
financial data validation. Actual audit results and remaining blockers
must be recorded here before declaring readiness. At this amendment's
creation, that audit is still running; panel/shape/fingerprint/rebuild
placeholders remain unmeasured.

A complete follow-up requires time/symbol coverage, source consistency,
two independent panel builds and the original 1/2/8/32-thread byte checks,
both shared lock fingerprints, and the original shape-only helper with
its predeclared remedies. A missing input or failed shape gate is reported
under those remedies; it never permits changing thresholds after seeing
counts. Nothing in this record enables scoring, live/testnet orders,
archive mutation or deployment.
