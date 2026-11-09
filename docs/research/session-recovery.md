# Recover quarantined research source from session logs

`research.session_recovery` extracts selected source text from local Claude JSONL
transcripts into a fresh private local directory. This preserves evidence when
session scratchpads have disappeared. Every bundle carries the status
`quarantined_log_recovery` and requires review before the recovered programs are
executed.

Run from a reviewed checkout with the pinned project Python. All paths must be
absolute. The output parent must already exist on a local filesystem supported
by the existing research output writer.

```bash
PYTHONPATH=src .venv/bin/python -m research.session_recovery \
  --session /absolute/private/session.jsonl \
  --output /absolute/private/new-recovery-directory \
  --name carry.py \
  --name research_plan_carry.md
```

Repeat `--session` for up to eight logs and `--name` for up to 64 unique bare
`.py` or `.md` names. A source log is limited to 64 MiB, each JSON line to 2 MiB,
each recovered file to 1 MiB and the complete output bundle to 16 MiB. A bundle
is created only after parsing and validating all selected text. An existing
output directory, symbolic link or ambiguous absolute source basename is
rejected. Directories have mode 0700 and files have mode 0600.

Each session receives a deterministic prefix such as `session-000--carry.py`.
The final `manifest.json` records input hashes, recovered file hashes, source
line numbers, applied operation kinds, missing names and unresolved operations.
The entire transcript and unrelated conversation are excluded. A conservative
possible-secret check refuses selected source containing private-key markers,
credential-like literals, recognized token shapes or 64-digit hex strings.
Review remains necessary because this check cannot recognize every secret.

Supported extraction covers tool `Write`, exact `Edit` and `replace_all`, quoted
literal `cat` heredocs, and a small Python syntax subset. That subset reads a
literal selected filename into `s`, applies literal `s.replace` or `s +=` edits
and writes the string to the selected filename. It also accepts a literal string
write. Parsing these forms never executes transcript shell commands or Python.
Unsupported commands or mutations are recorded as unresolved when they can be
identified. Recovered source text stays quarantined even when every recognized
operation was understood.

The manifest documents recovery limitations. Original file identities, complete
final bytes, market datasets and financial result artifacts may remain
unavailable. Tool call text may also include failed or interrupted writes.
Reconstruction alone cannot validate a historical result or restore an untouched
test period. Preserve an accepted bundle in a fresh durable research namespace
using the separately reviewed copy and hash-verification process. Keep research
source, original input identities and result artifacts together for future runs.
