# Goal: KiroCrew + OpenCode Docker appliance

Build a clean, self-hosted Docker deployment of the KiroCrew environment that
runs today on the ASUS host, with the OpenCode harness and OpenRouter Nemotron,
WhatsApp and Telegram, and no personal data from this machine baked into the
image.

## Definition of done

- [ ] `docker build -f deploy/opencode/Dockerfile .` produces an image from the
      current KiroCrew checkout, without copying a personal `~/.kiro`, `wa.db`,
      skill, conversation, OpenCode session, or API key into any layer.
- [ ] The image contains KiroCrew, OpenCode 2.2.x, the WhatsApp `neonize`
      dependency, Telegram support, ffmpeg/ffprobe and local STT.
- [ ] A fresh named volume starts with a seeded config for:
      `agent.acp_backend=opencode`,
      `agent.model=openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`,
      `agent.reasoning_effort=high`,
      background/subagent roles on the same model and effort,
      vision model `nvidia/nemotron-3.5-super-vl:free`.
- [ ] `docker compose up -d` reaches healthy, and a dashboard token minted with
      `docker exec ... kirocrew token` works.
- [ ] A live dashboard turn reports the Nemotron free model; WhatsApp and
      Telegram channels load; WhatsApp pairing can be started from a fresh
      volume.
- [ ] `scripts/update.sh` rebuilds from a newer checkout and a newer OpenCode
      version without losing the named volume.
- [ ] `scripts/export.sh` writes a transferable image tarball for a private
      server; `scripts/push.sh` can push to a registry.
- [ ] The deployment README documents first-run configuration, secrets, backup,
      reverse proxy, and the WhatsApp ToS risk.
- [ ] A smoke test proves the image has no embedded personal data and no
      dependency on the host's `/home/marco`.

## Constraints

- The image must be usable by a new operator: all personal values come from
  `.env` at deploy time.
- The build must be reproducible from this repository plus public artifacts.
- The native ASUS KiroCrew service must keep working; do not mount or mutate
  its data.
- No secret is committed. The local `.env` used for the test is gitignored.
