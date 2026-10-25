# Local fork workflow

This checkout is the **live Kiro Crew install** on this machine. It is installed
editable into `~/.kiro/crew-venv`, so the running gateway loads the code from
`src/kiro_crew/` here — there is no copy under `site-packages` to patch.

```bash
# what is running
~/.kiro/crew-venv/bin/python3 -c "import kiro_crew; print(kiro_crew.__file__)"
```

## Layout

- `main` = upstream **main** (v0.12.0) + the local commits on top.
- `origin` = `https://github.com/kirodotdev/KiroCrew.git`.
- `src/kiro_crew/static/dist/` is the built dashboard, `.gitignore`d. Rebuild it
  from `website/` when the UI changes:
  `cd website && npm ci && npm run build`, then
  `~/.kiro/crew-venv/bin/python -m kiro_crew.frontend stage .`.

Local commits:

1. `feat(whatsapp): archive every message and answer the musone wake word`
2. `feat(whatsapp): pair a device with an 8-character phone code`
3. `docs: record the local fork install and update workflow`

The opencode reasoning-effort patch that used to be local is **upstream** since
0.10: `ACP_BACKENDS_EFFORT_VIA_CONFIG_OPTION` and
`ACP_BACKENDS_EFFORT_FROM_ADVERTISED_OPTION` both include opencode.

## Updating

`main` is a normal branch on top of `origin/main`; the local commits rebase onto
it. The built-in updater stays off (`"auto_update": false` in
`~/.kiro/crew/config.json`) because it would install a wheel into a separate
versioned venv and bypass this checkout.

```bash
cd ~/repositories/kirocrew
git fetch origin
git status --porcelain            # must be empty
git rebase origin/main            # replay the local commits onto the new main
```

Resolve conflicts in the three touched areas (`whatsapp/`, `messaging/`,
`dashboard/handlers/whatsapp_setup.py`), then rebuild the dashboard if
`website/` changed and restart:

```bash
cd website && npm ci && npm run build && cd ..
~/.kiro/crew-venv/bin/python -m kiro_crew.frontend stage .
sudo systemctl restart kirocrew.service
```

If upstream ever absorbs the mirror or the wake word, drop the corresponding
local commit instead of resolving against new code.

## Reinstalling the venv

```bash
~/.kiro/crew-venv/bin/pip install -e ~/repositories/kirocrew
```

The previous wheel install (0.9.0 + local files) is backed up at
`~/.kiro/crew-backup-wheel-20261025/` for one release; delete it once the
editable install has proven itself.
