# Always-on Claude Code Remote Control on the VPS

PAPER development tooling only. This runbook keeps one Claude Code
[Remote Control](https://code.claude.com/docs/en/remote-control) server
running on the Netcup VPS (`chupa`), so Claude sessions that work *on the VPS*
can be opened from the Claude app or claude.ai/code at any time. It changes no
trading mode, adds no credential and moves no capital.

## What runs

- `claude-rc.service`, a systemd **system** unit owned by root, starts at
  boot and runs `/usr/local/libexec/claude-rc/claude-rc-run` (a copy of
  `scripts/claude_rc_run.sh`) as the operator user, never as root.
- The supervisor starts `claude remote-control --name hyperliquid-vps` in the
  repo checkout, inside a detached tmux session `claude-rc` on its **own tmux
  socket** (`tmux -L claude-rc`). Restarting it never touches the capture
  (`*-capture`) or research (`s4-*`) sessions on the default tmux server.
- When claude exits (a crash, a network outage of more than about ten
  minutes, a stop from the app), the supervisor writes the last screen to the
  journal and exits. systemd starts it again after 30 seconds, without a
  retry limit.
- `claude remote-control` started again in the same folder brings back the
  sessions it served, for about four hours after it stopped (documented for a
  Ctrl+C stop; the docs do not say whether a crash or reboot is the same).
  After that a new session starts; its title is `hyperliquid-vps` in the app.

## Decision on rights (2026-10-12)

Chupa chose that this always-on server runs with the **same rights as an
ordinary interactive session** of the operator user: no extra sandbox and no
extra deny list. What that means:

- Anyone who can sign in to the operator's claude.ai account can make Claude
  run commands on the VPS as the operator, at any hour. Protect that account
  (strong password, two-factor sign-in) and never use **Share** on a
  `hyperliquid-vps` session.
- The repository rules still bind every session: PAPER only, no private keys
  or exchange credentials on the host, no edits inside running containers, no
  changes to durable runtime data outside an approved runbook, and explicit
  approval for high-blast-radius actions (`AGENTS.md`, [Security](../SECURITY.md)).
- Root owns the unit and the supervisor copy, so a session cannot rewrite
  what restarts it. Stopping or removing the service needs `sudo`.

## One-time preparation (operator, over SSH)

1. Log in Claude Code as the operator: `claude auth login`. Remote Control
   needs a claude.ai subscription login; API keys do not work.
2. In the repo folder, run `claude remote-control` once by hand and answer its
   one-time questions (`Enable Remote Control? (y/n)` and, if asked, trust for
   the folder). Then stop it with **Ctrl+C**. The service cannot answer these
   questions itself.
3. Stop any other `claude remote-control` you started by hand (for example in
   a tmux window). The installer refuses while one runs.

## Install or update

```bash
cd "$HOME/Hyperliquid Project/Hyperliquid-Bot"
git pull --ff-only
bash scripts/claude_rc_install.sh --print-unit   # optional: show the unit, change nothing
sudo bash scripts/claude_rc_install.sh
```

The installer finds `claude` for the operator, refuses root as the operator,
refuses paths that systemd would expand (`%`, `$`, quotes, backslashes),
copies the supervisor, writes `/etc/systemd/system/claude-rc.service`, and
enables and (re)starts the service. Run it again after a merged change to the
`claude_rc_*` scripts: the service runs the installed copy, not the checkout.

If the layout differs, pass `CLAUDE_RC_REPO`, `CLAUDE_RC_BIN` or
`CLAUDE_RC_NAME` through sudo, for example
`sudo CLAUDE_RC_NAME=my-vps bash scripts/claude_rc_install.sh`.

## Daily use

| Task | Command |
|---|---|
| Status, recent log and current screen | `bash scripts/claude_rc_status.sh` |
| Open the screen | `tmux -L claude-rc attach -t claude-rc` (leave with Ctrl+B, then D) |
| Restart | `sudo systemctl restart claude-rc` |
| Stop until the next boot | `sudo systemctl stop claude-rc` |
| Remove completely | `sudo bash scripts/claude_rc_uninstall.sh` |

Pressing Ctrl+C inside the attached screen stops claude; the service then
starts it again after 30 seconds.

## When it needs the operator

- **Login expired**: the status screen or journal shows a login message.
  Run `claude auth login` as the operator, then `sudo systemctl restart claude-rc`.
- **A permission prompt**: a session waits until someone answers it in the
  app. The service keeps running meanwhile.
- **A one-time question on the screen** (Remote Control or folder trust):
  attach, answer, and leave with Ctrl+B, then D.

## Acceptance test after install

1. `bash scripts/claude_rc_status.sh` shows `service: active (enabled)` and a
   Remote Control screen; the app lists `hyperliquid-vps` with a green dot.
2. Attach, press Ctrl+C, leave with Ctrl+B, then D. Within about a minute the
   status shows a new screen and the app session comes back online.
3. Optional, in a quiet window: `sudo reboot`. After boot the service is
   active again without any login.

## Limits

- The official docs describe tmux or screen for keeping Remote Control alive
  over SSH. Supervising it with systemd is this project's choice and is not
  an officially documented setup.
- An idle server costs next to nothing, but a session that waits on a prompt
  does no work until it is answered.
- Tests: `tests/python/test_claude_rc_scripts.py` runs the supervisor against
  a fake `claude` in a folder with a space, checks Ctrl+C stop and the
  rendered unit (`systemd-analyze verify` where available).
