# Bounded native PAPER replay

This command replays explicitly selected, completed Hyperliquid BTC-PERP raw
Parquet parts through the existing native `PaperEngine`, risk checks, IOC fill
model and `NonProductionReferenceStrategy`. It produces the existing native
cockpit artifacts. It is an **engineering-only** replay, not an alpha study,
S4 score, strategy promotion, deployment or venue reconciliation.

`flat` is the default and places no orders. Explicit `--strategy toy` uses the
existing fixed-size no-edge reference strategy. Engine economics, risk limits,
250 ms assumed latency, fees, stop rules and fill behavior are unchanged. Funding
is not settled. Neither mode is production-eligible.

## Preconditions and command

The operator confirms the selected inputs have finished copying and are no
longer being written. `--inputs-complete` records that attestation; hashes of
the selected bytes do not certify a complete transfer, venue authenticity or
whole-archive coverage. Select exact relative `.parquet` files, not a directory
or glob. The command does not search other runs or sample/truncate a large part.

Use a clean, reviewed checkout and an already existing, verified **local DEV
scratch** output store outside the entire input/archive root. The command
requires both roots to exist; it does not create a missing mount or store.
Supply the actual mount target, source and filesystem independently checked
by the operator. There are no default NAS addresses or private paths.

```bash
: "${COMPLETED_INPUT_ROOT:?Set the completed project copy root}"
: "${COMPLETED_PART_RELATIVE:?Set one exact completed relative Parquet part}"
: "${VERIFIED_MOUNT_TARGET:?Set the existing kernel mount target}"
: "${VERIFIED_MOUNT_SOURCE:?Set the exact expected export source}"
: "${VERIFIED_MOUNT_FILESYSTEM:?Set nfs, nfs4 or cifs}"
: "${LOCAL_PAPER_STORE:?Set an existing local disposable output directory}"
: "${NEW_PAPER_RUN_ID:?Set a fresh create-only run ID}"
SOURCE_COMMIT="$(git rev-parse HEAD)"

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --frozen --no-sync \
  python -m hyperliquid_bot.paper_engine.offline_replay \
  --input-mode remote \
  --input-root "$COMPLETED_INPUT_ROOT" \
  --part "$COMPLETED_PART_RELATIVE" \
  --expected-mount-target "$VERIFIED_MOUNT_TARGET" \
  --expected-mount-source "$VERIFIED_MOUNT_SOURCE" \
  --expected-mount-filesystem "$VERIFIED_MOUNT_FILESYSTEM" \
  --store-root "$LOCAL_PAPER_STORE" \
  --run-id "$NEW_PAPER_RUN_ID" \
  --source-commit "$SOURCE_COMMIT" \
  --inputs-complete
```

Repeat `--part` for additional explicit parts, within the limits below. Add
`--strategy toy` only to exercise the existing no-edge order/fill path. No
credential, signer, venue client, network capture, collector or service is
started. Remote inputs are read only through their existing mounted filesystem.
No mount, NAS, ACL or durable source data is changed.

For synthetic local fixtures use explicit `--input-mode local-fixture` and
omit **all** expected-mount options. That mode requires a supported local
filesystem and is labelled separately in provenance. It never substitutes
for a missing or mismatched expected NAS. `TRADING_MODE` must be unset or
exactly `PAPER`; mode validation precedes input/output/source probes.

The default `--source-provenance verified-head` requires the supplied full
SHA to equal this checkout's actual HEAD and requires a clean working tree,
including untracked files. For fixture development, explicit
`--source-provenance operator-asserted` records that assertion instead; engine
artifacts visibly prefix the source with `operator-asserted:`. Both modes hash
the relevant code and dependency lock files and record Python/DuckDB versions.
The complete binding is rechecked before publication. This is consistent local
snapshot provenance, not OS attestation, wheel authentication or a complete
binary/environment proof. `local-offline:no-oci-image` never claims deployment.
The assertion option is available in both input modes; it never verifies the
supplied commit. Use the default `verified-head` for the reviewed real-input run.

## Bounds and input safety

| Bound | Default and maximum |
|---|---|
| Explicit parts | 4 |
| Total physical input bytes | 16 MiB |
| Total Parquet uncompressed metadata bytes | 64 MiB |
| Total raw payload bytes | 16 MiB |
| Raw rows | 5,000 |
| Expanded events before trade dedup | 2,000 |
| DuckDB threads | 1 |
| DuckDB query memory | 128 MiB |
| DuckDB spill | Disabled (`max_temp_directory_size=0B`) |

The `--max-files`, `--max-physical-bytes`, `--max-uncompressed-bytes`,
`--max-payload-bytes`, `--max-rows` and `--max-events` options may **lower**
these caps. They cannot raise them. A part exceeding a cap is rejected in full;
there is no implicit first-N selection. Native library memory limits are not an
OS sandbox or a guaranteed NFS wall-clock timeout.

Component-wise anchored no-follow opens reject symlink roots/ancestors/leaves,
nonregular files, hidden/partial names, traversal and duplicate path/inode/content
identities. Remote mode reuses the effective mount validation from the
[migration helper](migration-metadata-verification.md), including autofs/NFS
overmount identity. Inputs cannot cross another mount. Output and staging require
supported local filesystems; same-device, different-mount aliases are refused
conservatively. Input/store directory identities are rechecked after staging and
before publication. This does not defend against a privileged hostile host.

Bounded bytes are hashed and copied into a unique private local staging
directory before the eager replay loader runs. Footer row/uncompressed bounds,
exact raw schema, payload hashes, row identity, payload bytes and pre-dedup
event expansion are checked first. Duplicate JSON keys and non-standard JSON
constants are rejected to prevent parser disagreements. Original input identities
and bytes are checked again through anchored descriptors before publication. No
input is modified. The existing loader still validates inner BTC identity and normalized
events. Its optional resource profile leaves previous callers' defaults intact.

Extreme decimal exponents are rejected before fixed-point rendering. Produced
decimal/text scalars must fit the existing cockpit representation (80-character
decimals, at most 40 fractional places). No prices are rounded to force a fit.
This publication guard checks bounded producer representation; it is not a
second semantic ledger reader. The unchanged TypeScript cockpit reader remains
the independent artifact validator.

## Output, failures and cockpit

Stdout is a JSON result; exit 0 means one bounded engineering replay was
published, and exit 2 means rejected/incomplete. The result records the final
`run_dir`, private `staging_dir`, input/event counts and business fingerprint.
Artifacts are:

```text
<local-store>/<run-id>/run-claim.json
<local-store>/<run-id>/ledger.jsonl
<local-store>/<run-id>/state.json
<local-store>/<run-id>/health.json
<local-store>/<run-id>/replay-claim.json
```

The extra replay claim binds exact ordered input hashes, source/code/lock hashes,
configuration, reference strategy, bounds and artifact hashes. The engine's four
schemas remain unchanged. All JSON artifacts must fit 256 KiB; the **complete**
ledger must fit 2 MiB and 5,000 records. Over-limit output is refused, never
truncated or advertised as a readable success.

A run ID is reserved using exclusive `mkdir`, then validated artifacts are
copied through the opened local output descriptor. An existing run ID, including
an empty directory, file or symlink, is never replaced or cleaned up. Publication
is **not directory-atomic**. A publication failure retains an incomplete reserved
run and private staging/source evidence; it cannot be resumed. Health becomes
visible last, after the other complete files, so an incomplete publication fails
closed in the existing reader. Do not bind private staging or an incomplete run.
No automatic cleanup or retry follows. Scratch retention is the operator's
separate responsibility; disposable publication is not a durability guarantee.

Bind only the successful final directory using the server-only
`COCKPIT_NATIVE_PAPER_RUN_DIR` and open `/paper`, as described in
[PAPER_ENGINE.md](../PAPER_ENGINE.md#optional-read-only-cockpit-snapshot).
The existing SSR/API reader is unchanged. Historical tapes correctly display
`historical` and normally `stale`, with their original observation timestamps.
Never rewrite the clock to make a historical replay appear live.

Run creation time comes from the first event receipt; the business path has no
wall-clock input. The engine closes at the end of the selected tape. It does not
invent later quotes, force a final fill/flatten, cancel pending orders or settle
funding. Ending positions and pending orders can therefore remain open and are
reported honestly. Repeating the same input/config/source with the same run ID
in another local store yields identical business artifacts; there is no resume.
Fees and PnL remain assumed PAPER results, not performance or venue evidence.

After merge, the coordinator may run the selected bounded real input and validate
its artifacts using the actual existing cockpit reader. Such a run does not
authorize S4 scoring, source/spec repinning, runtime deployment, TESTNET, SHADOW,
LIVE or any capital-bearing action.
