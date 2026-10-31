# Ubuntu VM migration status

Status as of **2026-10-29**: source development is on the Ubuntu VM; durable
research data, captures and continuous runtime migration are **unverified**.
This records read-only observations and development-tool restoration. It is
not a deployment, capture-start or strategy-promotion authorization.

## Confirmed source and host boundary

- Operator reports an Ubuntu VM hosted on TrueNAS SCALE.
- Local `/etc/os-release` reports Ubuntu **26.04.1 LTS**;
  `systemd-detect-virt` reports **KVM**. The TrueNAS control plane was not inspected.
- Main checkout: `/home/chupa/coding-projects/Hyperliquid-Bot`.
- Initial main checkout was clean at
  `694497a089409b15b49d1ed7ad4abe9db2986054`, matching GitHub `main`.
- Development uses feature branches and separate worktrees. Source synchronization
  does not transfer ignored datasets, environment files, services or images.
- ADR-025 changes the development host. ADR-024 and existing VPS-named runbooks
  retain the historical Netcup profile; they do not certify services on the VM.

## Initial read-only data and runtime inspection

| Check | Observation | Limit |
|---|---|---|
| Old project tree | `/home/chupa/Hyperliquid Project` absent | Says nothing about data still on Netcup |
| Old roots | `data-capture` and `hist-archives` absent under the old tree | No data copied or recreated |
| New sibling roots | Neither root found under `/home/chupa/coding-projects` | Other locations remain possible |
| Additional locations | No matching roots found in a bounded directory inspection of `/mnt`, `/media`, `/srv`, `/opt` | Not a scan of all VM/NAS storage |
| Mount list | No project data mount identified by `findmnt` target/filesystem listing | TrueNAS datasets and unmounted storage not inspected |
| Project runtime | No applicable Python/Node collector processes or project services identified | No proof of old VPS service state or future availability |
| Catalog and coverage | Not verified: no actual archive/capture root available | No DuckDB rebuild, payload scan or strategy scoring performed |

These observations do **not** establish data loss. Locate the original data
or its intended durable mount before declaring the migration complete.
The operator subsequently confirmed that `data-capture` and `hist-archives`
remain on the Netcup VPS. Their remote paths, coverage and transfer to this VM
have not been independently verified; no transfer was started by this task.
No secret files, process environments or full command lines were inspected.
No services were stopped/restarted and no runtime data was written.

### Subsequent update: copy in progress (2026-10-30)

The operator later withdrew certainty about the old dataset location, then
reported copying the Netcup archive to TrueNAS. The coordinator observed two
existing project NFS4 exports and a bounded sample of capture lane/run
directories. This supersedes the earlier no-mount observation; it does not
establish complete transfer, historical-archive presence, claims, published
parts, market coverage or checksums. No write canary or storage change was
performed. Source/destination data consistency remains unverified in #149.

The [bounded post-copy metadata helper](migration-metadata-verification.md)
is prepared with synthetic fixtures only. Do not run it against the active
copy. Copy completion must first be confirmed by the operator, then the exact
root and mount identity supplied explicitly. Even a completed bounded
inventory does not certify migration, S4 readiness or runtime deployment.

## Development tools: before and after

At the initial inspection, system Python was **3.14.4**, outside the project's
`>=3.13,<3.14` range. `uv`, Node.js and `pnpm` were absent from PATH, DuckDB was
unavailable to system Python, and the main checkout contained no `.venv` or
`node_modules`.

The workflow-restoration task subsequently installed and verified the pinned
user-local tools:

| Tool | Verified version |
|---|---|
| Python | 3.13.15, managed by uv |
| uv | 0.12.5 |
| Node.js | 24.18.1 |
| pnpm | 11.23.0 |

Executables are exposed under `$HOME/.local/bin`; Node/pnpm tool stores are
under `$HOME/.local/share/hyperliquid-toolchains`. No operating-system or shell
profile changes were needed. Select the tools explicitly in a shell:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

Locked dependencies were restored in the workflow task's isolated feature
worktree. No environment/dependencies were installed into the main checkout.
Tool/dependency restoration alone is not a passing test baseline: use the
PR's actual local-check results and CI runs for that evidence.

## Next verification steps

1. Identify the intended `ARTIFACT_ROOT` and `HIST_ARCHIVES_ROOT`, whether on
   transferred local storage or a deliberate durable mount. Record capacity,
   ownership and backup responsibility without disclosing secret values.
2. Verify existing completed datasets and coverage using read-only operations.
   Keep historical archives separate from capture `run_id` directories.
3. Run the existing filesystem inventory and catalog-glob smoke with explicit
   roots. Do not let the legacy Netcup default select an empty directory.
4. Once data is verified, use the research harness's existing fingerprint and
   pre-registration rules before evaluating a new hypothesis.
5. Inspect and approve runtime configuration separately before starting any
   bounded capture or PAPER service. A merged PR does not deploy.

After the actual roots have been set and locked development dependencies exist:

```bash
export REPO_ROOT="$HOME/coding-projects/Hyperliquid-Bot"
: "${ARTIFACT_ROOT:?Set the verified capture root}"
: "${HIST_ARCHIVES_ROOT:?Set the verified historical root}"
cd "$REPO_ROOT"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --frozen --no-sync \
  python -m hyperliquid_bot.phase_a_research_tools inventory \
  --artifact-root "$ARTIFACT_ROOT" --hist-root "$HIST_ARCHIVES_ROOT"
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src uv run --frozen --no-sync \
  python -m hyperliquid_bot.phase_a_research_tools duckdb-smoke \
  --artifact-root "$ARTIFACT_ROOT" --hist-root "$HIST_ARCHIVES_ROOT"
```

These commands inspect metadata/globs. They do not rebuild catalogs or start
collectors. Follow the [read-only inventory runbook](phase-a-research-inventory.md)
for limits, especially when a capture is still open.

See [ADR-025](../DECISIONS/README.md#adr-025--ubuntu-vm-development-move-runtime-migration-unverified),
[development workflow](../DEVELOPMENT.md), and
[GitHub workflow](../GITHUB_WORKFLOW.md).
