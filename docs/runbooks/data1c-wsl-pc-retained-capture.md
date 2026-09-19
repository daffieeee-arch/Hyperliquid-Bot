# DATA-1C WSL/PC runbook — OKX public BTC-USDT-SWAP (secondary)

Status: secondary TerraPC (Windows 11 + WSL2 Ubuntu) operator notes for the
prepare-only DATA-1C path. The Netcup Ubuntu 24.04 LTS VPS and
[data1c-vps-retained-capture.md](data1c-vps-retained-capture.md) are primary.

This is **not** a 24/7 service, not D22-B, and not LIVE trading. Do not start
OKX retain while Phase A 72h captures are running on the VPS.

## Known-good TerraPC paths

```bash
export REPO_ROOT="$HOME/Hyperliquid Project/Hyperliquid-Bot"
export ARTIFACT_ROOT="$HOME/Hyperliquid Project/data-capture"
export TMUX_SESSION=okx-capture
```

Helpers: `scripts/data1c_{start,status,stop}.sh`. Create-only. Never resume.
Never touch `hl-capture`, `bn-capture`, `bv-capture`, `bv-std-capture`, or
`kr-capture`.

## Check-only (safe anytime)

```bash
DURATION_SECONDS=60 bash "$REPO_ROOT/scripts/data1c_start.sh" --check-only
```

## After CoS assign only

```bash
export DURATION_SECONDS=259200
export RUN_ID="$(date -u +%Y%m%dt%H%M%Sz)-live-retained"
bash "$REPO_ROOT/scripts/data1c_start.sh"
bash "$REPO_ROOT/scripts/data1c_status.sh"
# stop only when assigned and outside a protected evidence window:
# bash "$REPO_ROOT/scripts/data1c_stop.sh"
```

Cloud Agents must not SSH to TerraPC or send `C-c` to live Phase A sessions.
