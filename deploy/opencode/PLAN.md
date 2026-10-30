# Plan

| Phase | Work | Status |
|---|---|---|
| 1 | Make the archive transcriber portable (no `/home/marco` hardcode) | pending |
| 2 | Add the Docker stack: Dockerfile, seed, entrypoint, compose | pending |
| 3 | Add build/update/export/push/smoke scripts | pending |
| 4 | Build the wheel and the image, then run a fresh-volume smoke test | pending |
| 5 | Verify dashboard model/effort, WhatsApp pairing, Telegram, vision | partial |
| 6 | Prove no personal data is in the image; document and commit | done |

## Design

- Base: the official `ghcr.io/kirodotdev/kirocrew:stable` image, so kiro-cli,
  tini, the sandbox entrypoint and the security posture are inherited.
- Overlay: the local KiroCrew wheel (force-reinstalled), `neonize`, ffmpeg,
  `pywhispercpp`, and a pinned OpenCode npm tarball.
- First-run seed: writes a clean `~/.kiro/crew/config.json` and a clean
  `~/.config/opencode/opencode.jsonc` plus `auth.json`; then hands over to the
  official entrypoint for credential scrubbing and sandbox probing.
- Runtime secrets: `.env` passed by compose; the seed writes the OpenRouter key
  to `~/.kiro/crew/.env` and the OpenCode auth store, then the official
  entrypoint scrubs the rest.
- Persistence: one named volume at `/home/kirocrew`.
- Updates: rebuild the wheel and image; bump `OPENCODE_VERSION` in `.env`; the
  volume carries configuration and sessions forward.
