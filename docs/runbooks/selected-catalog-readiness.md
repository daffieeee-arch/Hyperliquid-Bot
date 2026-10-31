# Selected copied-catalog readiness

Use this read-only CLI for one explicitly selected view in a completed copy.
It prepares bounded file/header/footer metadata and a parameterized local query
plan. It does not certify migration, complete archive coverage, source integrity,
research readiness, a study, strategy performance or runtime deployment.

The legacy `phase_a_research_tools duckdb-smoke` API is unchanged. Its scalar
regex does not account for the list form emitted by `hist_etl.catalog`: a
list-only catalog can report zero recognized views without a failure. Its
optional DB probe opens the copied `research.duckdb` with its old embedded paths.
**This selected-view CLI is the recommended safe route for the copied catalog.**
Do not run old catalog-building scripts to repair paths: they execute/rewrite SQL
and the database. This tool never opens that database or applies copied SQL.

## Explicit mount bindings

First obtain the operator's copy-completion attestation and independently verify
the exact archive root, mount target/source and filesystem. The flag records an
attestation, not transfer proof. No default path, discovered export, local empty
directory or unverified old host path substitutes for a missing mount.

```bash
: "${VERIFIED_HIST_ROOT:?Set the verified copied hist-archives root}"
: "${VERIFIED_HIST_MOUNT_TARGET:?Set its existing kernel mount target}"
: "${VERIFIED_HIST_MOUNT_SOURCE:?Set its exact verified export source}"
: "${VERIFIED_HIST_FILESYSTEM:?Set nfs, nfs4 or cifs}"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --frozen --no-sync \
  python -m hyperliquid_bot.catalog_readiness \
  --hist-root "$VERIFIED_HIST_ROOT" \
  --hist-mount-target "$VERIFIED_HIST_MOUNT_TARGET" \
  --hist-mount-source "$VERIFIED_HIST_MOUNT_SOURCE" \
  --hist-filesystem "$VERIFIED_HIST_FILESYSTEM" \
  --view hist_kr_xbtusd_1d --copy-complete
```

The catalog is exactly `catalog.sql` inside that bound archive root. A selected
`__DC__/...` reference also requires all four `--capture-root`,
`--capture-mount-target`, `--capture-mount-source`, `--capture-filesystem` options.
Unselected capture references cause no capture-root scan. The tool reuses the
unchanged migration helper's kernel-visible mount checks, anchored no-follow
lookup and per-open mount-ID/device checks, including autofs/overmount handling.
It rejects symlinks and nested/changed mounts. No mount is activated or changed
by an assumed local-directory fallback.

Output is JSON on stdout only. There is no output-file option, catalog rewrite,
local database creation or recovery action. If saving a report, choose a local
location outside the NAS/data roots. Do not redirect it into the archive.

## Selection and bounds

The parser reads a bounded catalog snapshot and accounts for every complete
view declaration. Supported selections use simple unquoted `hist_*` or `live_*`
names and exactly `SELECT * FROM read_parquet(...)`, with a string or nonempty
list of strings and optional boolean `union_by_name := true/false` (also `=`).
Comments/quoted strings do not hide statements. Duplicate names, missing
selected views, trailing SQL, unknown top-level commands and malformed selected
forms cannot pass. Other named definitions, including legacy `DISTINCT ON ...
ORDER BY` variants, remain explicitly unsupported and counted. They are never
executed; a successful selected plan is not whole-catalog PASS.

Only `__HIST__/` and `__DC__/` references are bound. URLs, old absolute paths,
parent traversal, recursive `**`, control characters and non-Parquet inputs
are refused. Segment globs expand through bounded descriptor-based listings;
no unselected files are searched. Overlapping paths and duplicate physical
inputs are rejected rather than silently de-duplicated or counted twice.

For a large view, add `--file` with an exact, verified logical member, for example
`--file '__HIST__/parquet/binance/um/klines_1h/verified-file.parquet'` alongside
`--view hist_bn_um_klines_1h` and the same binding options. Replace that example
filename with a known member. Repeat the flag for a small subset. Each requested
file must match exactly one declared reference; overlapping definitions fail.
This avoids enumerating a huge glob. `whole_view_coverage` remains `not_checked`
in subset mode; otherwise it says only `paths_enumerated_only`.

Defaults (CLI `--max-*` overrides have hard caps):

| Bound                             | Default |                  Maximum |
| --------------------------------- | ------: | -----------------------: |
| Catalog bytes                     |   8 MiB |                   16 MiB |
| View declarations                 |   5,000 |                   10,000 |
| Literal references                | 100,000 |                  200,000 |
| Selected files / held descriptors |      64 | 256, plus one catalog FD |
| Directory entries across roots    |     512 |                    5,000 |
| Relative path depth               |      16 |                       32 |
| Footer bytes per file             |   2 MiB |                    8 MiB |
| Metadata rows/columns per file    |   5,000 |                   20,000 |
| Cooperative seconds               |      10 |                       60 |

A limit produces an unchecked result with no query plan. Checks occur between
operations; blocked NFS/DuckDB calls cannot be interrupted by this cooperative
deadline. It is not a guaranteed wall-clock deadline or process-kill facility.
There is no retry worker, watcher or full-scan fallback.

## Metadata and consuming the plan

Selected descriptors remain open until final identity/mutation checks. The tool
inspects Parquet envelope/footer length and schema, then only allowlisted
time/symbol footer statistics. It never selects price, funding, signal, return
or payload rows. Missing, inexact, all-null or oversized statistics stay
`not_checked`; no record scan fills them in. Footer extrema are observations,
not verified record/instrument/market-date coverage or checksum evidence.

The private in-memory DuckDB connection has a 64 MiB memory limit, no spill,
disabled extension installation/autoload and disabled external access except
the exact held descriptor path; configuration is locked. Copied SQL never
reaches that connection. Files conservatively require equal schemas unless
`union_by_name=true`; overlapping column names still require equal types.

A ready report includes `query_plan.sql` and `query_plan.parameters`. These use
one parameterized `read_parquet` call over the explicit selected absolute files
and only the allowlisted declared union option. This is usable input for a local
DuckDB consumer; it is not copied executable catalog text. Before execution,
the consumer must revalidate mount/path/file identities using the same boundary:
a report cannot authorize a future lookup after descriptors close or prevent a
later rename/symlink swap. The CLI itself executes no plan.

Exit 0 means selected-file metadata readability checks completed, with the plan
scope above. Exit 1 means missing, changing or unchecked/limited inputs. Exit 2
means invalid configuration/catalog/path/metadata. Missing symbol/time statistics
can coexist with a readable selected-file plan; those explicit unchecked fields
are never promoted to coverage. `research_ready` and `migration_complete` remain
false. Keep existing #149/#150 gates and study registrations/pins unchanged.
