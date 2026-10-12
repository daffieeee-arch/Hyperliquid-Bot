# Always-on Claude Code Remote Control on the VPS

PAPER development tooling only. This runbook keeps one Claude Code
[Remote Control](https://code.claude.com/docs/en/remote-control) server
running on the Netcup VPS (`chupa`), so Claude sessions that work *on the VPS*
can be opened from the Claude app or claude.ai/code at any time. It changes no
trading mode, adds no credential and moves no capital.

## What runs

- `claude-rc.service`, a systemd **system** unit owned by root, starts at
  boot and runs `/usr/local/libexec/claude-rc/claude-rc-run` (a root-owned
  copy of `scripts/claude_rc_run.sh`) as the operator user, never as root.
  The status and uninstall helpers are root-owned copies in the same folder;
  `/usr/local/bin/claude-rc-status` points to the status helper.
- The supervisor starts `claude remote-control --name hyperliquid-vps` in the
  repo checkout, inside a detached tmux session `claude-rc` on its **own tmux
  socket** (`tmux -L claude-rc`), and targets that session by its exact name.
  claude starts through a login shell (so it reads the operator's
  `~/.profile`) and without the `TMUX` variables, so tmux sessions that a
  Claude session starts (a capture, an `s4-*` job) land on the operator's
  default tmux server, where the capture scripts look for them. Restarting
  the service never touches that server.
- The environment is close to, not identical with, an SSH login: there is no
  SSH agent, no `XDG_RUNTIME_DIR` and no user D-Bus, so `systemctl --user`
  and agent-forwarded SSH do not work from these sessions. A default tmux
  server that a session starts inherits that environment.
- When claude exits (a crash, a network outage of more than about ten
  minutes, a stop from the app), the supervisor writes its exit status and
  the last 40 lines of its screen to the journal and exits 0; systemd starts
  it again after 30 seconds, without a retry limit. Only a missing repo or
  `claude` binary exits 1.
  - The status comes from the login shell that runs claude
    (`~/.cache/claude-rc/exit-status`), because tmux 3.4 does not always
    report it. A status above 128 reads "signal N or exit code 128+N": the
    shell reports both alike.
  - A screen equal to the one dumped in the last ten minutes (an expired
    login would repeat it every 30 seconds) gives one short line instead.
    The last exit screen is also kept in `~/.cache/claude-rc/last-screen.txt`,
    which `claude-rc-status` shows.
  - A planned stop never writes a crash dump: it logs "stopped on request",
    or "the tmux session ended" when the stop removed the session first.
- A stop or restart of the service ends only claude (with Ctrl+C; while it
  waits it watches the screen and presses again at once if claude asks) and
  the supervisor (`KillMode=process`). Processes that a session started stay
  up: a capture or research tmux session, a download. They do run inside the
  service's cgroup, so `systemctl status claude-rc` lists them.
- The installer and uninstaller refuse to run inside a served session or
  another window of the private tmux server (a restart would end it): a
  process whose first tmux parent is the private server (also after
  `sudo -i`), or that has none and sits in the service's cgroup. A shell in a tmux session that a served session
  started reaches that default tmux server first, so it counts as an SSH
  shell.
- All `claude_rc_*` tools ignore `TMUX_TMPDIR`, so the private socket is
  always `/tmp/tmux-<uid>/claude-rc`, also when `~/.profile` sets it. The
  private server ignores `~/.tmux.conf`, and the supervisor remembers
  claude's own pane, so a window opened in that session never hides claude's
  exit or receives its Ctrl+C. The private session belongs to the service:
  such a window is closed with the session at claude's next exit or restart,
  so do not work there.
- A stop's Ctrl+C reaches claude's whole foreground process group, like a
  Ctrl+C typed into a hand-started server. Long jobs belong in their own
  tmux session on the default server (as the capture and research runbooks
  already do); those survive every restart.
- `~/.profile` runs at every start of claude, so it must not `exec tmux` or
  wait for input; claude would never start.
- Under pytest or CI the supervisor refuses the live `claude-rc` socket
  (`status=TEST_ISOLATION_NOOP`), like the capture helpers refuse live
  capture sessions; tests always use an isolated socket.
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
- Root owns the unit and the installed scripts, so a session cannot change
  them, and stopping or removing the service needs `sudo`. This is not a
  boundary against a stolen account: whoever steers the operator user can
  also wait for the operator's next `sudo` in that account (for example by
  changing shell startup files). The account's own protection is what counts.

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

Run this from an SSH terminal, not from a Claude session the service serves
(the restart would end that session; the installer refuses). It installs from
a temporary worktree of `origin/main`, so the main checkout keeps its branch
and running jobs such as `s4-build` are not disturbed:

```bash
cd "$HOME/Hyperliquid Project/Hyperliquid-Bot"
git fetch origin main
wt="$(mktemp -d)/main"
git worktree add --detach "$wt" origin/main
bash "$wt/scripts/claude_rc_install.sh" --print-unit   # optional: show the unit
sudo bash "$wt/scripts/claude_rc_install.sh"
git worktree remove "$wt" && rmdir "$(dirname "$wt")"
```

`mktemp -d` gives a fresh folder only the operator can open. Never use a
fixed name under `/tmp`: another account could create it first and swap the
script that root runs.

The installer:

- runs from a `main()` function, so bash has read the whole file before root
  executes any of it;
- finds `claude` for the operator and refuses root as the operator;
- refuses values systemd would expand (`%`, `$`, quotes, backslash, backtick);
- refuses when its `claude_rc_*` scripts differ from `origin/main` (a guard
  against local edits, not a security boundary);
- refuses while a hand-started `claude remote-control` runs outside the
  service (best effort: an npm install hides its arguments, so stop it by
  hand as step 3 says);
- copies the scripts, writes `/etc/systemd/system/claude-rc.service`, and
  enables and (re)starts the service.

Repeat the same steps after a merged change to the `claude_rc_*` scripts: the
service runs the installed copies, not the checkout. If the layout differs,
pass `CLAUDE_RC_REPO`, `CLAUDE_RC_BIN` or `CLAUDE_RC_NAME` through sudo, for
example `sudo CLAUDE_RC_NAME=my-vps bash "$wt/scripts/claude_rc_install.sh"`.

## Daily use

| Task | Command |
|---|---|
| Status, recent log and current screen | `claude-rc-status` (also under `sudo`, which shows the full journal) |
| Open the screen | `env -u TMUX_TMPDIR tmux -L claude-rc attach -t claude-rc` (leave with Ctrl+B, then D) |
| Restart | `sudo systemctl restart claude-rc` |
| Stop until the next boot | `sudo systemctl stop claude-rc` |
| Remove completely | `sudo /usr/local/libexec/claude-rc/claude-rc-uninstall` |

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

1. `claude-rc-status` shows `service: active (enabled)` and a
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
  a fake `claude` in a folder with a space. It checks the Ctrl+C stop (also
  when claude asks for a second Ctrl+C), that claude starts without `TMUX`,
  that a stop spares a session whose name only starts with `claude-rc`, that
  an identical exit screen is printed once (again after a long run), the
  40-line cap, that a planned exit is not reported as a crash, that an
  operator window changes nothing, that `~/.tmux.conf` is ignored, the
  test-isolation refusal, the server and private-tmux-server detection, the
  in-service check on a fake `/proc` tree, the status
  helper through a symlink, and the rendered unit (`systemd-analyze verify`
  where available). The tmux tests skip locally without tmux but fail in CI.
  The installer refusals (a hand-started server, scripts that differ from
  `origin/main`, checked before root sources anything) were run by hand as
  root with a throwaway user.
