# S4 VPS steps: what a Claude session on the VPS fills before merge

PAPER research only. The two drafts, `exp_xsmom_usdm.md` and
`exp_xscarry_usdm.md`, leave the values in angle brackets open. Every one
of them comes from the universe's metadata or the panel's **shape**. None
may come from a price, a return, a funding value or a strategy run.

Nothing waits for a date. The code pin is known: the S4 panel change merged
in #145 as `694497a089409b15b49d1ed7ad4abe9db2986054`. The panel ends with
`--end 2026-09-01` (axis 2020-01-01 to 2026-08-31, 2,435 days), and the
month the builder reads after the window for a still-published funding run,
2026-09, is the universe file's latest month and is published for every
such run, so the shape-only build and the study's own build read the same
files. The steps below can run now, in order, from a Claude session on the
VPS (`chupa`).

## What "shape-only" allows

- **Allowed**: the build's success line; `harness lock` (it validates the
  spec and fingerprints the table, and computes no return);
  `docs/experiments/exp_xs_usdm_shape.py`, which reads `ts`, `symbol`, `traded`,
  `volume_rank` and `funding_covered`, and only whether `funding_rate`,
  `ret_14d`, `funding_7d` and `carry_7d` are present; Binance's contract
  metadata and announcements for the classification (step 2).
- **Not allowed before the run**: `harness run`; any read of a value of
  `close`, `quote_volume`, `ret_14d`, `funding_rate`, `qv_30d`, `vol_30d`,
  `funding_7d` or `carry_7d`; `exp_xs_usdm_overlap.py` (it reads signal
  values) and `exp_xs_usdm_beta.py` (it reads holdout returns, and refuses
  to run without a scored holdout); any holdout statistic beyond presence
  and rank; any read of the funding or price data from 2026-09-01 on, the
  forward test's data, beyond the build's own end check.
- The draft's tie counts, which read validation `carry_7d` values before
  the run, are gone: the panel change (#145) breaks ties by a hashed draw
  at the source, and the tie counts are a post-run diagnostic in
  `exp_xs_usdm_overlap.py`.

## Before step 1: the session itself

- Work on the pre-registration branch of draft PR #146,
  `claude/project-analysis-code-review-59mcsr`, in a clean tree: `git fetch
  origin`, `git checkout claude/project-analysis-code-review-59mcsr`, `git
  pull --ff-only`, then `uv sync --frozen`. Every file of step 6 is already
  on that branch; fill them in place, so every edit is in the PR's diff.
- Set `HIST_ARCHIVES_ROOT` to the research archive root
  (`$HOME/Hyperliquid Project/hist-archives` unless the operator keeps it
  elsewhere). It is outside `data-capture`; the study directories (`$DIR`)
  are new folders outside the repository. Nothing here touches durable
  runtime capture data or the runtime containers.
- Run the sync and the builds inside `tmux`, with at least 10 GiB free
  where the archive root lives (`df -h`).

## Order

### 1. Check the code pin → `694497a089409b15b49d1ed7ad4abe9db2986054`

- The pin is #145's merge commit. Confirm nothing later touched the pinned
  files, the same list the run scripts' `pinned` array checks:
  `git diff --exit-code 694497a089409b15b49d1ed7ad4abe9db2986054 origin/main -- src uv.lock pyproject.toml .python-version config/hist_etl/universe/binance-um-usdt-1d-2026-10-08.json`.
  If something did, the pin moves to the newest such commit, both docs'
  Data sections and run scripts change with it, `exp_xs_usdm_beta.py` is
  re-checked on a synthetic run (step 6), and steps 3 to 5 run at the new
  pin. Check again just before the PR merges.

### 2. Classify every symbol and freeze the list → `<EXCLUSION_SHA256>`, `<CLASSIFICATION_SHA256>`, `<EXCLUSION_COUNTS>`, `<CLASSIFICATION_DIFF>`

The draft list is `binance-um-usdt-non-crypto-2026-10-08.json` (239
contracts, draft sha256
`86802d81edbd11ed134aca13acb5e7297e3bb16294a57ff8c91e25ff34c06067`). It was
made from tickers and listing months, because `exchangeInfo` answered HTTP
451 from the container: the author's recognition was the classifier. Kept
tickers were never checked, 267 of the 657 kept contracts list from 2025-01
(27 in 2026), and the 52 `unverified` entries are only tickers the author
did not recognise. That is not the rule the docs state, so the list is
redone, the same way for every symbol:

- On the VPS, classify **all 896 symbols** of the universe file, kept and
  excluded alike, from metadata only, in this order of sources:
  1. `GET https://fapi.binance.com/fapi/v1/exchangeInfo` (`underlyingType`,
     `underlyingSubType`) for the contracts it still lists;
  2. Binance's listing or delisting announcement of the contract;
  3. for a contract Binance no longer lists and neither source settles, a
     public token registry entry (CoinGecko or CoinMarketCap) for that
     ticker's token at the contract's listing month.
- A symbol is `crypto` only when a source verifies a crypto-native token;
  otherwise it gets the category the source shows, or `unverified`. The
  rule then excludes every non-`crypto` symbol, `unverified` included,
  whatever its listing date. If an `unverified` symbol lists before 2025,
  the PR names it.
- Record every symbol's class, source and the metadata fields read in
  `config/research/binance-um-usdt-classification-2026-10-08.json`
  (committed; sha256 `<CLASSIFICATION_SHA256>` in both docs). The exclusion
  list is exactly its non-`crypto` symbols with their categories, in the
  format the builder checks (#145): keys `format` (1), `universe`
  (`binance-um-usdt-1d-2026-10-08.json`), `rule` (non-empty) and
  `symbols`; categories only from `index`, `fiat_pegged_or_fx`,
  `commodity`, `etf_or_etp`, `equity`, `private_company`, `unverified`; no
  duplicate key; every symbol in the universe file.
- Record the counts per category (the draft's 5, 2, 10, 30, 138, 2 and 52
  will change) and the number of exclusions listing before the holdout
  (six in the draft) as `<EXCLUSION_COUNTS>` in both Universe sections.
- `<CLASSIFICATION_DIFF>` (momentum's Seen before): the symbols whose class
  differs from the draft list's, in either direction, each with its source.
- Also record the listing months of the five contracts with Chinese names
  that the universe file excludes (they cannot be in the panel), for the
  survivorship limitation.
- **Freeze the list before step 3.** Put the final file at
  `config/research/binance-um-usdt-non-crypto-2026-10-08.json` in the
  branch and record its sha256 (`<EXCLUSION_SHA256>`). Steps 3 to 5 use
  exactly that file. If review of the pre-registration PR changes the list
  in any byte, its sha256 changes and steps 3 to 5 run again; both run
  scripts check the list against `<EXCLUSION_SHA256>` with `sha256sum -c`.

### 3. Sync and build the panel twice → `<PARQUET_SHA256>`, `<ROW_COUNT>`, `<SYMBOL_COUNT>`, `<RANKED_COUNT>`, `<REBUILD_CHECK>`

- Sync at the pin, inside `tmux`, with the archive root outside
  `data-capture`:

  ```bash
  HIST_ETL_REQUESTS_PER_SECOND=4 PYTHONPATH=src uv run --frozen \
    python -m research.hist_etl sync --dataset bn-um-usdt-1d
  PYTHONPATH=src uv run --frozen python -m research.hist_etl verify --dataset bn-um-usdt-1d
  ```

  About 43,300 monthly archives plus the daily tails of 864 open runs, two
  GETs each: about 7 hours at 4 requests a second, 14 at the default 2.
  Exit 2 with gaps is expected for contracts delisted after the universe
  file was taken (their daily files stop); the build below refuses any
  missing month inside the range, which is the real check.
- Build twice, into two new directories outside the repository, with the
  exact arguments of the docs and the frozen list of step 2:

  ```bash
  echo "<EXCLUSION_SHA256>  config/research/binance-um-usdt-non-crypto-2026-10-08.json" \
    | sha256sum -c -
  PYTHONPATH=src uv run --frozen python -m research.bar_tables panel \
    --group bn-um-usdt-1d --start 2020-01-01 --end 2026-09-01 \
    --lookbacks 14 --vol-window 30 --volume-window 30 --funding-window 7 \
    --exclude-symbols config/research/binance-um-usdt-non-crypto-2026-10-08.json \
    --rank-requires-funding --out "$DIR/panel.parquet"
  ```

  The success line, `bar_tables wrote N rows S symbols R ever ranked`
  followed by the output path, `X excluded sha256:<list sha256>` and
  `rank requires funding`, gives `<ROW_COUNT>`, `<SYMBOL_COUNT>` and
  `<RANKED_COUNT>`; its list sha256 must equal `<EXCLUSION_SHA256>`.
  `sha256sum` of the two Parquets must match: that is `<PARQUET_SHA256>`.
- `<REBUILD_CHECK>`: repeat with 1, 2, 8 and 32 DuckDB threads and record
  that all hashes match. One way, not tested here: a wrapper in the study
  directory that replaces `duckdb.connect` with one adding
  `config={"threads": N}` and then runs `research.bar_tables` with
  `runpy`. If the hashes differ, the table cannot be pinned by bytes; stop
  and fix the builder first (a code fix moves the pin).
- Expected from the universe file's arithmetic: at most about 650,000 rows
  (kline days from 2020-01 to 2026-08; under `max_rows` 2,000,000) and at
  most 860 symbols.

### 4. Lock both specs → `<DATA_FINGERPRINT>`

```bash
cp docs/experiments/exp_xsmom_usdm.spec.yaml "$DIR/xsmom.spec.yaml"
cp docs/experiments/exp_xscarry_usdm.spec.yaml "$DIR/xscarry.spec.yaml"
PYTHONPATH=src uv run --frozen python -m research.harness lock "$DIR/xsmom.spec.yaml"
PYTHONPATH=src uv run --frozen python -m research.harness lock "$DIR/xscarry.spec.yaml"
```

- Both lock files must carry the same `fingerprint_sha256`; that is
  `<DATA_FINGERPRINT>`, in both docs' Data section and run script.
- Their `inputs[0]` must show `timestamp_min` `1577923199999`,
  `timestamp_max` `1788220799999`, the `row_count` of step 3 and the
  `content_checksum` `<PARQUET_SHA256>`.
- `spec_sha256` must be `c8c5d931…` (momentum) and `45700976…` (carry). If
  a spec changed in review, run `python -m research.harness hash` and
  update the doc's header and its run script together.
- Delete the lock files afterwards; the run writes its own.

### 5. Shape counts → `<SHAPE_COUNTS>`, `<CARRY_PRESENCE>`, `<GATE_OUTCOMES>`

```bash
PYTHONPATH=src uv run --frozen python docs/experiments/exp_xs_usdm_shape.py \
  "$DIR/panel.parquet" config/research/binance-um-usdt-non-crypto-2026-10-08.json \
  > "$DIR/shape.json"
cp "$DIR/shape.json" docs/experiments/exp_xs_usdm.shape.json
```

It takes under a minute. Each item says what it fills, its gate, and what
is done when the gate fails. **Every remedy is fixed here, before any count
is seen**; whatever applies is recorded in both docs' Seen before. No
remedy drops or adds a symbol by hand, moves a period, or changes a
tolerance of the decision rules.

- **`axis`**: the axis claim in Data and Periods. Gate: `as_expected` true
  (2,435 days, one-day steps, first and last `ts` as above). Fails: the
  table is wrong; find the sync or builder fault, fix it (a code fix moves
  the pin), and redo steps 3 to 5.
- **`symbols`, `symbols_ever_ranked`**: `<SYMBOL_COUNT>`, `<RANKED_COUNT>`.
  Gate: equal to the success line. Fails: as for `axis`.
- **`excluded`**: Gate: `ever_ranked` is empty. Fails: a bug of the panel
  change; fix it (new pin) and redo steps 3 to 5.
- **`traded_without_rate`**: the Universe list of contracts trading without
  funding. Gate: every `ranked_days` is 0. Fails: as for `excluded`.
- **`ranked_any_median_by_year`**: universe depth per year, for Seen
  before. Gate: every year from 2021 at least 60, so a top 50 is a
  selection. Fails: S4 pauses; a redesign is a new pre-registration that
  records this count.
- **`depth.<window>.days_below`**: the decision days a config must skip.
  Gate: the period bound below stays at or above both floors for every
  config. Fails: each config that misses it is removed from its family's
  grid by this rule alone; Holm and the deflated Sharpe ratio then count
  the remaining configs, the specs are re-hashed, and a family left with no
  config is not registered.
- **`reach.traded_days_without_rate`**: the claim that no position can
  hold a traded day without funding. Gate: **0**; otherwise a run may fail
  closed. `traded_without_rate_by_symbol` names the contracts and days.
  The gate counts every such day of every contract; the cases below are
  examples. BTCSTUSDT's 2022-01 (a position decided in late 2021-12) is
  the validation-window case. From 2025-07 on, the universe file shows 131
  contracts with klines months without a funding month (130 after their
  funding archive ends, and LITUSDT in its funding gap). 124 of them have
  such months inside the panel; among the latest, ATAUSDT, MLNUSDT,
  PHBUSDT and SYSUSDT from 2026-06, TONUSDT, IPUSDT, COSUSDT, DUSDT,
  HIGHUSDT and MBOXUSDT from 2026-07, and NFPUSDT from 2026-08. The other
  seven (ACXUSDT, HFTUSDT, ICXUSDT, SCRTUSDT, STORJUSDT, VANRYUSDT,
  VICUSDT) have funding through 2026-08, so only their days of 2026-08
  after the last settlement can count. Most of these contracts are
  delisted, with a flat, untraded archive, and only traded days count. A
  counted day before 2026-07 rules out remedy 1, so remedy 2 applies.
  Fails, in this order:
  1. If every such day lies in a month whose funding archive Binance may
     not yet have published when the universe file was taken (2026-07 or
     2026-08 for a run the 2026-10-08 file marks closed), take a new dated
     universe snapshot (`hist_etl universe`), commit it, point the
     manifest and the exclusion list's `universe` at it, replace the
     universe file's name and sha256 in both docs' Data sections and run
     scripts (the `pinned` array and step 1), and redo steps 2 to 5
     (symbols that list after 2026-08 do not reach the panel).
  2. If any such day remains, the harness gets a version-7 rule, the same
     for every symbol: a position whose hold reaches a traded day without
     a rate exits at that day's close, counts as a forced exit, and that
     day's funding is charged nothing and counted as an unfunded day.
     Exiting at that close, not the one before, uses no later knowledge of
     price. The rule is its own PR with tests and `/code-review`; the pin
     moves, steps 3 to 5 are redone, and both docs say so. Rule 5 bounds
     how much a pass can rest on it.
- **`reach.cannot_fill`, `contract_ended`, `halt_days`,
  `halt_days_without_rate`, `uncovered_days`, `by_horizon`**: leg fill and
  forced-exit exposure, before any signal. No gate. These are **absolute
  bounds**: each config's legs are drawn from the ranked names on the
  scored decision days, so its non-fills are at most `cannot_fill` and its
  forced exits at most `by_horizon.h<H>.forced_exits` of its horizon, in
  any window. They do not bound a rate within a leg: a leg is chosen by
  its signal, and momentum's short leg leans toward crashing, delisting
  names. Reading, fixed now, for the holdout:
  - if `by_horizon.h<H>.forced_exits` is under 1% of the positions a
    config of that horizon holds with no skip (75 × 2 × names for h7,
    40 × 2 × names for h14), rule 5's forced-exit part cannot bind for that
    config;
  - otherwise the bound is loose (it counts every ranked name on every
    day), and `forced_exits / positions` of `by_horizon` is the rate a leg
    of randomly chosen names would meet. If that rate is 0.5% or more, the
    docs say rule 5's forced-exit part is likely to bind, most of all for
    momentum's short leg; below it, that it may still bind for a leg the
    signal tilts toward delistings.
  - Rule 5 stays at 1% either way.
- **`uncovered_ranked`**: the share of ranked rows whose funding day is not
  whole. No gate. Reading, fixed now: rule 5 stays at 1%; if the share is
  1% or more in the holdout, the docs say rule 5's funding part may bind.
- **`carry_presence`** (carry), per window: `without_carry`, ranked rows
  that have a rate but no `carry_7d` (a day of the last seven that did not
  trade or has no rate, a funding dataset's first day that is not covered,
  or fewer than seven days since a listing or gap), and
  `covered_mean_missing`, ranked rows with a `carry_7d` but no
  `funding_7d`: the ranked rows with an uncovered day in their carry
  window, which the merged panel doc asks a study to count. Both come with
  rows per symbol. No gate. Reading, fixed now: the carry study tolerates
  any number of either; if either is 1% or more of `ranked_rows` in any
  window, the carry doc lists its symbols under Limitations; nothing else
  changes.

- **Period bound** from `days_below`: a window of `W` days with `s` skipped
  decision days holds at least `floor((W - 1 - s) / (horizon + 1))`
  periods. Summed over the 16 folds this must stay at or above 72 for every
  config (h14 holds 96 with no skips), and over the holdout at or above 30
  (h14 holds 40 with no skips, so up to 157 skipped days; h7 holds 75, up
  to 367). The thresholds per config are in `NEEDED` in `exp_xs_usdm_shape.py`:
  momentum q20 needs 25 names, q30 17; carry q10 needs 40, q20 20.
- **Leg fill counts** of the configs themselves (`mean_long_names`,
  `mean_short_names`, `unwound_periods`, `forced_exits`) exist only after
  `harness run`.
- `<SHAPE_COUNTS>` in both docs is a short summary: the axis, symbols ever
  ranked, the median universe depth per year, the median names with each
  signal per fold, the skip days per config, the contracts trading without
  funding, the forced-exit bounds against 1% of positions, and the
  uncovered share. `<CARRY_PRESENCE>` (carry's Seen before) is the
  `carry_presence` block in short. `<GATE_OUTCOMES>` (both Seen before
  sections) says which gate failed, which remedy applied and what it
  changed, or "none".

### 6. Fill the placeholders and open the pre-registration PR

- Files (all already on the branch):
  `docs/experiments/exp_xsmom_usdm.md`, `.spec.yaml`;
  `docs/experiments/exp_xscarry_usdm.md`, `.spec.yaml`;
  `docs/experiments/exp_xs_usdm_overlap.py` and
  `docs/experiments/exp_xs_usdm_beta.py` (the run scripts call both; rule 4
  reads the second); `docs/experiments/exp_xs_usdm_power.py` (both docs
  cite it; its default output and its sensitivity runs are in momentum's
  Limitations);
  `config/research/binance-um-usdt-non-crypto-2026-10-08.json` and
  `config/research/binance-um-usdt-classification-2026-10-08.json`.
- Also `docs/experiments/exp_xs_usdm_shape.py` and its output,
  `docs/experiments/exp_xs_usdm.shape.json` (new), so a reader sees exactly
  what was read before the run.
- Replace every placeholder: `<CLASSIFICATION_SHA256>`,
  `<EXCLUSION_SHA256>` (also in both run scripts), `<EXCLUSION_COUNTS>`,
  `<CLASSIFICATION_DIFF>`, `<ROW_COUNT>`, `<SYMBOL_COUNT>`,
  `<RANKED_COUNT>`, `<DATA_FINGERPRINT>` (also in both run scripts),
  `<PARQUET_SHA256>`, `<REBUILD_CHECK>`, `<SHAPE_COUNTS>`,
  `<CARRY_PRESENCE>` and `<GATE_OUTCOMES>`. `grep -n '<[A-Z0-9_]*>'` on both
  docs must then find nothing.
- Remove the draft notes at the top of both docs once every value is in.
- Run `ruff check`, `ruff format --check` and `mypy` on the four scripts.
- Commit on the branch, with a message that lists the filled values, and
  push it to update draft PR #146. Leave the PR a draft: the Claude Code
  session that opened it reviews the filled version (`/code-review`),
  marks it ready and merges. If this session cannot push, say so and leave
  the commit in place. The PR is docs and config, but it touches
  `docs/experiments/**` and `config/**`, so the `python` CI area runs. It
  enables no LIVE, SHADOW or TESTNET mode and moves no capital.

### 7. Stop there

- Do not run `harness run`, `exp_xs_usdm_overlap.py` or
  `exp_xs_usdm_beta.py` on the real panel before the PR merges: the study
  runs from main, as the docs' Run sections say.
- Keep the two study directories and the archive root; the study's run
  tops up the same sync.

## Not open, already known

- Spec hashes: `c8c5d93142e27e468c642fd576d8cc7f2263dd05b70bdc2a0998c77285fbbd08`
  (momentum), `457009762711c0ac113719360fc7d04431f67f63ce10a88a6d02b74e0f0db44a`
  (carry). Both specs pass `validate_spec` (alpha 0.05, `holdout_bars`
  608).
- Code pin: `694497a089409b15b49d1ed7ad4abe9db2986054` (#145), unless step
  1 moves it.
- Universe file sha256:
  `add8d0f26d6b7c2bdf18963cf1f77752f0b1aa8a943a64c868e097951f4ec520`; its
  latest month is 2026-09, and no still-published run ends before it.
- Split: on a 2,435-day axis, 16 folds from day 371 (2021-01-06) to 1,827
  (2025-01-01), holdout 1,827 to 2,435 (2026-08-31). Checked at the pin with
  both specs on synthetic panels of that axis built with the merged builder
  (`build_symbol_rows`, `rank_by_volume` with a `RankRule`; no real data):
  176 (h7) and 96 (h14) validation periods and 75 and 40 holdout periods
  with no skip (`PanelSource.window`), PBO on 16 blocks with an in-sample
  floor of 36, the deflated Sharpe ratio over 4 trials.
- The scripts ran at the pin on those synthetic panels: the shape script
  (including a 4h-to-8h switch, which `covered_mean_missing` counted),
  `exp_xs_usdm_overlap.py` (its tie threshold, half a step of 1e-9 over
  seven days, separated the hashed ties), and `exp_xs_usdm_beta.py` on runs
  that selected a config, whose rebuild matched `PanelSource.window` and
  `result.json` exactly. The power script ran with 50,000 draws by default and
  with `--independent`, `--annual-vol 0.15` and `--iid-only`, the outputs
  quoted in momentum's Limitations. `ruff check`, `ruff format --check` and
  `mypy` are clean on all four.
