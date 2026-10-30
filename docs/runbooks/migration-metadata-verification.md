# Post-copy migration metadata inventory

Preparation for [#149](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/149),
implemented in [#169](https://github.com/daffieeee-arch/Hyperliquid-Bot/issues/169).
**Do not run against the NAS while the operator's copy is active.** This work
uses only synthetic temporary fixtures. It does not inspect, finish, repair,
resume or certify the real transfer.

The operator must first report copy completion and identify the intended
project root and its existing mount. `--copy-complete` records that operator
attestation; it is not source/destination evidence. Mounts, ACLs, services,
catalogs, datasets and study registrations are not changed by this helper.

## Why a separate bounded helper

The existing [Phase A inventory](phase-a-research-inventory.md) is useful for
known capture layouts but walks all runs and derives time windows from file
mtimes. Its optional catalog smoke has another scope. `research.hist_etl
verify` performs deeper checks but writes a gap report under the archive root.
Neither command is automatically run by this read-only migration helper.

`hyperliquid_bot.migration_verify` reuses DATA-1A..1F path contracts, run-ID
validation, the raw writer's schema and raw schema version. It includes the
OKX lane. It uses an in-memory DuckDB connection solely to inspect selected
Parquet headers/footers; it does not attach a catalog or select payload rows.

## Explicit identity, no local fallback

The root must be absolute and under an explicitly selected existing NFS/NFS4
or CIFS mount. Require all three expected values: kernel mount target,
source and filesystem. The helper matches the longest mount containing the
root **before probing that root**. An ordinary empty local directory cannot
replace an absent NAS export. Unknown identity, a mismatched source/type,
root traversal, nested mounts or symlinks fail closed. Each opened file must
be regular. Component-by-component no-follow directory-descriptor lookup
prevents ancestor symlink swaps. Every opened component at or below the
selected mount must retain its kernel mount ID and device, including when
a nested bind mount shares the same device. Kernel mount identity is checked
again at the end. This identifies the
selected export, not physical disks, storage-pool durability or backups.

Use actual values independently verified by the operator; the public example
below deliberately has no default source address or private NAS path:

```bash
: "${VERIFIED_PROJECT_COPY_ROOT:?Set the verified project copy directory}"
: "${VERIFIED_MOUNT_TARGET:?Set its existing kernel mount target}"
: "${VERIFIED_MOUNT_SOURCE:?Set the exact verified source export}"
: "${VERIFIED_MOUNT_FILESYSTEM:?Set nfs, nfs4 or cifs}"

# Only after the operator confirms the copy has finished:
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --frozen --no-sync \
  python -m hyperliquid_bot.migration_verify \
  --root "$VERIFIED_PROJECT_COPY_ROOT" \
  --expected-mount-target "$VERIFIED_MOUNT_TARGET" \
  --expected-mount-source "$VERIFIED_MOUNT_SOURCE" \
  --expected-filesystem "$VERIFIED_MOUNT_FILESYSTEM" \
  --copy-complete
```

The root defaults to sibling `data-capture` and `hist-archives` children.
If the copy used a different layout, specify `--capture-subdir` and
`--history-subdir` as explicit relative paths inside the verified root. It
does not search other exports or infer a lost source from absent directories.
Missing data stays missing/unverified; it never starts replacement downloads.

Output is JSON on stdout only. The helper has no report-file option, makes
no directories and writes no data. If saving stdout, the operator must select
a local destination outside the NAS and data roots; do not redirect into the
copy. No credentials, process environments, raw payloads, arbitrary JSON
fields or mount options are printed.

## Bounds and meanings

| Default | Maximum selectable through this tool |
|---|---|
| 500 directory entries | 5,000 (`--max-entries`) |
| 12 run directories | 100 (`--max-runs`) |
| 12 published-part footers | 100 (`--max-footers`) |
| 5 history directory levels | 8 (`--max-depth`) |
| 10 cooperative seconds | 60 (`--max-seconds`) |
| 128 KiB per claim/health JSON | Fixed CLI bound |
| 2 MiB per selected Parquet footer | Fixed CLI bound |

The time bound is **cooperative**: checks occur between operations. A blocked
NFS syscall cannot be interrupted by that check, so it is not a guaranteed
wall-clock deadline or guaranteed process-kill mechanism. Stop only this
inspection process if necessary; do not signal collectors or retry a stuck
scan automatically. There is no background worker or watcher.

Enumeration order comes from the filesystem. A budget-limited subset is not
a representative statistical sample. Partial observations are retained with
`limited_by`, `observed_counts` and `selection_complete: false`; unvisited
lanes/history remain `not_checked`. Reaching the footer limit does not turn
unopened parts into valid data. Bounds must not be bypassed by launching a
full archive scan during the copy.

Per-item states are distinct:

- `present`: the reported metadata check succeeded, within its stated scope.
- `missing`: the selected path/claim/final health/raw directory was absent.
- `invalid`: identity, JSON, schema, mount, file safety or selected footer
  checks failed. No repair or rewrite follows.
- `in_progress`: a `.partial` file, changing JSON/part, or health-vs-part-count
  disagreement was seen. It is not evidence of an active healthy collector.
- `not_checked`: no suitable run was observed, a limit stopped inspection,
  required statistics were absent/inexact, or a check is deliberately outside
  this tool's scope. Missing final health may mean an unfinished source run
  or an incomplete copy; the helper cannot choose between them.

Exit 0 means bounded enumeration/header checks finished without a discovered
missing, invalid, in-progress or unchecked item. Exit 1 means incomplete or
unavailable observations; exit 2 means invalid configuration/metadata. **None
is a migration PASS:** `migration_complete` is always false and
`transfer_integrity` / `record_coverage` remain `not_checked`.

## What is and is not established

Capture checks validate the required claim identity (schema/path/run/venue/
product and `signing: false`), basic final-health identity/status, published
part schema and exact footer extrema for schema version, venue, product and
receipt UTC nanoseconds. Existing multi-product Binance and declared
Hyperliquid add-on identities remain distinct. Product extrema cannot prove
every intermediate row's instrument identity. Footer statistics are sampled
metadata, not authenticated records. No payload checksum, JSON payload,
sequence continuity, row-by-row identity, 72-hour sufficiency, reconnect gap,
strategy result or runtime readiness is validated. Missing/inexact statistics
stay unchecked; there is no fallback to a full row scan.

Historical inventory records relative names, sizes and dates appearing in
known archive/output filenames. Filename dates are not observed market-date
coverage. ZIP/CSV contents, Parquet rows, `.CHECKSUM` contents and source
sidecars are not hashed or verified. `catalog.sql` and `research.duckdb` are
listed only; SQL, globs, old absolute catalog targets and catalog validity are
not evaluated or rewritten. Unknown files, including environment files, are
not opened or printed.

After this bounded inventory, #149 still needs reviewed source/destination
counts and coverage, a consistent transfer manifest/checksum procedure and
separately authorized deeper checks on completed data. Catalogue relocation
and any recovery are separate operator work. Keep S4/data readiness blocked;
do not repin studies, score old holdouts, deploy PAPER or enable authenticated
trading on the strength of this report.
