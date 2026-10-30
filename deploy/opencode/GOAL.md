# Goal: KiroCrew + OpenCode Docker appliance

Build a clean, self-hosted Docker deployment of the KiroCrew environment that
runs today on the ASUS host, with the OpenCode harness and OpenRouter Nemotron,
WhatsApp and Telegram, and no personal data from this machine baked into the
image.

## Definition of done

- [x] `docker build -f deploy/opencode/Dockerfile .` produces an image from the
      current KiroCrew checkout, without copying a personal `~/.kiro`, `wa.db`,
      skill, conversation, OpenCode session, or API key into any layer.
- [x] The image contains KiroCrew, OpenCode 2.2.x, the WhatsApp `neonize`
      dependency, Telegram support, ffmpeg/ffprobe and local STT.
- [x] A fresh named volume starts with a seeded config for:
      `agent.acp_backend=opencode`,
      `agent.model=openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`,
      `agent.reasoning_effort=high`,
      background/subagent roles on the same model and effort,
      vision model `nvidia/nemotron-3.5-super-vl:free`.
- [x] `docker compose up -d` reaches healthy, and a dashboard token minted with
      `docker exec ... kirocrew token` works.
- [~] A live dashboard turn reports the Nemotron free model; WhatsApp and
      Telegram channels load; WhatsApp pairing can be started from a fresh
      volume.
- [x] `scripts/update.sh` rebuilds from a newer checkout and a newer OpenCode
      version without losing the named volume.
- [x] `scripts/export.sh` writes a transferable image tarball for a private
      server; `scripts/push.sh` can push to a registry.
- [x] The deployment README documents first-run configuration, secrets, backup,
      reverse proxy, and the WhatsApp ToS risk.
- [x] A smoke test proves the image has no embedded personal data and no
      dependency on the host's `/home/marco`.

## Current evidence

- Image: `kirocrew-opencode:0.12.0` / `:test`, 2.45 GB, built 2026-10-30.
- `deploy/opencode/scripts/smoke.sh` passed: healthy, OpenCode 2.2.2, ffmpeg,
  neonize, pywhispercpp, seeded model/effort, auth, dashboard token, image-clean.
- Live turn in the container: `docker-final-live`, HTTP 200, assistant `OK`,
  `turn_stats.model=openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`.
- WhatsApp phone pairing on a fresh volume returned a code through the
  local-only dashboard endpoint (`scripts/whatsapp-pair.sh`); the code was not
  entered on a phone, so no device was linked.
- Telegram: token stored, `telegram.enabled=true`, doctor reports
  "enabled, credentials present". An actual inbound reply still needs a person
  with the Telegram account.
- Export: `dist/kirocrew-opencode-0.12.0.tar.gz`, 606 MB.
- AppArmor profile `kirocrew-container` loaded on the ASUS; without it the
  OpenCode harness correctly refuses to run unsandboxed.

## Constraints

- The image must be usable by a new operator: all personal values come from
  `.env` at deploy time.
- The build must be reproducible from this repository plus public artifacts.
- The native ASUS KiroCrew service must keep working; do not mount or mutate
  its data.
- No secret is committed. The local `.env` used for the test is gitignored.
