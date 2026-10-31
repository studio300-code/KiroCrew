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
- [x] The image converts DOCX/ODT to PDF with LibreOffice and the bundled
      WhatsApp skill returns the result through `file_send`, not SSH.

## Current evidence

- Image: `kirocrew-opencode:0.12.2` / `:test`, 2.97 GB, built 2026-10-31.
- `deploy/opencode/scripts/smoke.sh` passed: healthy, OpenCode 2.2.4, ffmpeg,
  LibreOffice 25.2.3 (`libreoffice-convert-ok`, DOCX → PDF), neonize,
  pywhispercpp, sandbox off, seeded model/effort, skill learning on with
  approval and script generation off, auth, dashboard token, image-clean.
- Live turn in the container: `docker-final-live`, HTTP 200, assistant `OK`,
  `turn_stats.model=openrouter/nvidia/nemotron-3-ultra-550b-a55b:free`.
- WhatsApp phone pairing on a fresh volume returned a code through the
  local-only dashboard endpoint (`scripts/whatsapp-pair.sh`); the code was not
  entered on a phone, so no device was linked.
- Telegram: token stored, `telegram.enabled=true`, doctor reports
  "enabled, credentials present". An actual inbound reply still needs a person
  with the Telegram account.
- Export: `dist/kirocrew-opencode-0.12.2.tar.gz`, 742 MB.
- Document delivery: the WhatsApp skill now says to convert with
  `libreoffice --headless --convert-to pdf` and send with the MCP `file_send`
  tool; `ssh`/`wa_send.py` remain denied. The `file_send` description now names
  WhatsApp among the channels with native delivery.
- Sandbox: `KIROCREW_SANDBOX=auto` in `.env.example`; the OpenCode harness
  refuses to run unsandboxed. The SEL trust key is restored read-only into the
  sandbox after the masks, so the in-sandbox MCP server verifies the session
  token instead of creating a divergent key and refusing every tool call.
- Transcription: the native host uses orukeet via `KIROCREW_TRANSCRIBER`; the
  image ships whisper.cpp as the portable fallback and accepts a mounted
  external wrapper with the same `TXT=` contract.
- Skill learning: `KIROCREW_SKILLS_AUTO_CREATE=1`,
  `KIROCREW_SKILLS_AUTO_REFINE=1`, `KIROCREW_SKILLS_REQUIRE_APPROVAL=0`,
  `KIROCREW_SKILLS_GENERATE_SCRIPTS=0` in `.env.example`; the seed applies them
  only when set, so an operator's dashboard choice survives an update.
- Vision: a real `_describe_image` call with `nvidia/nemotron-3.5-super-vl:free`
  returned an Italian description of a contract screenshot.
- Voice: the container transcribed a real `.opus` WhatsApp note through the
  built-in whisper.cpp path; no external ASUS transcriber was needed.
- AppArmor profile `kirocrew-container` loaded on the ASUS; without it the
  OpenCode harness correctly refuses to run unsandboxed.
- Self-sufficiency: `wa.db`, `media/`, the WhatsApp session, config, skills,
  OpenCode state and the STT model all live in the `kirocrew-home` volume.
  Restart/update keeps them; `compose down -v` deletes them. The archive is the
  deployment own new archive, not a copy of the ASUS one.
- The image bundles a generic `whatsapp` archive skill through
  `skills.extra_paths`; `wa_archive.py chats` on a fresh volume says "archive is
  empty: no chats yet" instead of a traceback.
- `whatsapp-pair.sh` accepts the phone number as an argument or from
  `WHATSAPP_PAIR_PHONE` in `.env`; it prints the code and sends nothing.

## Constraints

- The image must be usable by a new operator: all personal values come from
  `.env` at deploy time.
- The build must be reproducible from this repository plus public artifacts.
- The native ASUS KiroCrew service must keep working; do not mount or mutate
  its data.
- No secret is committed. The local `.env` used for the test is gitignored.
