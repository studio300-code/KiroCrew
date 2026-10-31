# Local fork workflow

This checkout is the **live Kiro Crew install** on this machine. It is installed
editable into `~/.kiro/crew-venv`, so the running gateway loads the code from
`src/kiro_crew/` here — there is no copy under `site-packages` to patch.

```bash
# what is running
~/.kiro/crew-venv/bin/python3 -c "import kiro_crew; print(kiro_crew.__file__)"
```

## Layout

- `main` = upstream **main** + the local commits, joined by merge commits (no
  rebase; local hashes stay stable).
- `origin` = `https://github.com/kirodotdev/KiroCrew.git`.
- `fork` = `https://github.com/studio300-code/KiroCrew.git` — our fork; `main`
  tracks `fork/main` and pushes there, while `origin` stays the upstream for
  updates.
- `src/kiro_crew/static/dist/` is the built dashboard, `.gitignore`d. Rebuild it
  from `website/` when the UI changes:
  `cd website && npm ci && npm run build`, then
  `~/.kiro/crew-venv/bin/python -m kiro_crew.frontend stage .`.

The fork carries roughly 86 local commits on top of upstream: the WhatsApp
archive and "musone" wake word, phone-code pairing, the STT/orukeet work,
OpenCode 2 ACP support, and the Docker appliance under `deploy/opencode/`. The
old note about three local commits is obsolete.

The opencode reasoning-effort patch that used to be local is **upstream** since
0.10: `ACP_BACKENDS_EFFORT_VIA_CONFIG_OPTION` and
`ACP_BACKENDS_EFFORT_FROM_ADVERTISED_OPTION` both include opencode.

## Updating

`main` is a normal branch on top of `origin/main`; upstream is **merged** into
it, never rebased. The built-in updater stays off (`"auto_update": false` in
`~/.kiro/crew/config.json`) because it would install a wheel into a separate
versioned venv and bypass this checkout.

```bash
cd ~/repositories/kirocrew
git fetch origin
git status --porcelain            # must be empty
git merge --no-edit origin/main   # one merge commit, local history intact
```

Resolve conflicts where the local work overlaps upstream (today `whatsapp/`
and `src/kiro_crew/dashboard/stt_stream.py`). `rerere` is enabled
(`rerere.enabled` and `rerere.autoupdate`), so a conflict already resolved once
is replayed automatically on the next identical merge. Then rebuild the
dashboard if `website/` changed and restart:

```bash
cd website && npm ci && npm run build && cd ..
~/.kiro/crew-venv/bin/python -m kiro_crew.frontend stage .
sudo systemctl restart kirocrew.service
```

If upstream ever absorbs the mirror or the wake word, drop the corresponding
local commit instead of resolving against new code.

## Nightly update

A systemd user timer (`kirocrew-selfupdate.timer`) runs the skill's
`selfupdate.sh apply --with-opencode` every night at 03:00. It merges
`origin/main` when it moved, updates OpenCode when a newer CLI is published
(gateway stopped for both), creates a `selfupdate-backup-<timestamp>` tag, and
rolls back if the gateway does not come healthy. A conflicted merge does not
block OpenCode: `last-run.json` records `kirocrew: "skipped-conflict"` and the
merge retries the next night. The old `harness-opencode-update.timer` at 03:50
is disabled.

```bash
systemctl --user list-timers kirocrew-selfupdate.timer
journalctl --user -u kirocrew-selfupdate.service -n 50 --no-pager
cat ~/.kiro/crew/selfupdate/last-run.json
```

## Reinstalling the venv

```bash
~/.kiro/crew-venv/bin/pip install -e ~/repositories/kirocrew
```

The previous wheel install (0.9.0 + local files) is backed up at
`~/.kiro/crew-backup-wheel-20261025/` for one release; delete it once the
editable install has proven itself.
