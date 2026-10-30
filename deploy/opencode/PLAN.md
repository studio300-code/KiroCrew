# Plan

| Phase | Work | Status |
|---|---|---|
| 1 | Make the archive transcriber portable (no `/home/marco` hardcode) | done |
| 2 | Add the Docker stack: Dockerfile, seed, entrypoint, compose | done |
| 3 | Add build/update/export/push/smoke scripts | done |
| 4 | Build the wheel and the image, then run a fresh-volume smoke test | done |
| 5 | Verify dashboard model/effort, WhatsApp pairing, Telegram, vision, voice | done (actual link/reply needs the owner) |
| 6 | Prove no personal data is in the image; document and commit | done |
| 7 | Bundle LibreOffice and prove DOCX → PDF conversion in the image | done |

## Design

- Base: `python:3.12-slim-trixie` with tini as PID 1. The image is
  self-contained and does not depend on kiro-cli/KAS.
- Overlay: the KiroCrew wheel built from this checkout, OpenCode pinned to
  2.2.4, `neonize` for WhatsApp, Telegram support, `lark-oapi`, ffmpeg,
  `pywhispercpp`, LibreOffice Writer and Liberation fonts.
- First-run seed: writes `~/.kiro/crew/config.json` and
  `~/.config/opencode/opencode.jsonc` plus `auth.json`; the entrypoint then
  hands over to the gateway.
- Runtime secrets: `.env`; the seed writes the OpenRouter key to the OpenCode
  auth store and `~/.kiro/crew/.env`.
- Persistence: one named volume at `/home/kirocrew` (archive, media, WhatsApp
  session, config, skills, OpenCode state, STT model).
- Updates: rebuild the wheel and image (`scripts/update.sh`), or bump
  `OPENCODE_VERSION`; the volume carries state forward.
