# KiroCrew + OpenCode appliance

A self-hosted Docker image for the KiroCrew environment:

- KiroCrew gateway and dashboard
- **OpenCode** ACP harness, using **OpenRouter Nemotron 3 Ultra (free)** at
  reasoning effort **high** for the chat, background and subagent roles
- **Nemotron 3.5 Super VL (free)** for image and video-frame descriptions
- **WhatsApp** (personal linked device, `neonize`) with the local archive
- **Telegram**, Slack, Discord, WeCom, Weixin, Webex, Microsoft Teams and Feishu
  (the Teams/Feishu optional dependencies are installed)
- Local speech-to-text (`pywhispercpp`) and ffmpeg for voice notes and video
- No personal data, conversations, skills, WhatsApp database, OpenCode sessions
  or API keys in the image. Everything is created in a named volume on first
  start and configured from `.env`.

The image is built from the current KiroCrew checkout, so the local WhatsApp
document/attachment fixes are included.

## Quick start

```bash
cd deploy/opencode
cp .env.example .env
# edit .env: OPENROUTER_API_KEY, TELEGRAM_BOT_TOKEN, ...
sudo scripts/install-host.sh        # one-time AppArmor profile for the inner sandbox
docker compose up -d --build
docker compose exec kirocrew kirocrew token --ttl 2h
```

Open the printed `http://127.0.0.1:5476/?token=...` link. The first start seeds:

- `~/.kiro/crew/config.json` with the OpenCode backend, Nemotron free model,
  effort `high`, WhatsApp and Telegram enabled;
- `~/.config/opencode/opencode.jsonc` with the same model;
- `~/.local/share/opencode/auth.json` with the OpenRouter key;
- `~/.kiro/crew/.env` with the gateway credentials.

All of that lives in the `kirocrew-home` named volume, not in the image.

## Host prerequisite: AppArmor

The OpenCode harness refuses to run unsandboxed: it routes tool calls through a
permission gate whose compensating control is the OS-level credential mask. On
Ubuntu 23.10+ the kernel restricts unprivileged user namespaces, and Docker's
default AppArmor profile blocks the mount namespace the inner sandbox builds.
`scripts/install-host.sh` installs a tiny profile that grants only `userns,`
(the same grant the native `kirocrew-userns` profile uses). The compose file
references it as `apparmor=kirocrew-container`.

Run it once per host, then keep it across reboots (AppArmor loads the profile
from `/etc/apparmor.d` automatically). If your host does not use AppArmor, the
stack cannot select the OpenCode harness with the inner sandbox; use a host
that supports it or choose a harness whose tool calls reach the gate directly.

## First-run configuration

1. **Dashboard** — the token link is the only login. For a LAN/private server,
   set `KIROCREW_BIND_HOST=0.0.0.0` and put a TLS reverse proxy in front; set
   `KIROCREW_PUBLIC_URL` to the public origin so dashboard links and CORS match.
2. **Telegram** — put the bot token in `.env` and, for safety, your numeric
   Telegram user ID in `TELEGRAM_ALLOWED_USER_IDS`. Restart the container.
3. **WhatsApp** — the channel starts unpaired. The dashboard pairing controls
   are local-machine-only, so on a remote server pair from the Docker host:

   ```bash
   deploy/opencode/scripts/whatsapp-pair.sh 393451234567
   # or put WHATSAPP_PAIR_PHONE=393451234567 in .env and run it with no argument
   ```

   The command prints an 8-character code on the terminal; nothing is sent to
   that number or to your own chat. On the phone: WhatsApp → Settings → Linked
   devices → Link a device → **Link with phone number instead**, and enter the
   code. WhatsApp always requires the phone to confirm; a server cannot link an
   account silently. The session is stored in the volume and survives upgrades.
   If you are browsing the dashboard on the same machine, *Settings → Messaging
   Channels → WhatsApp* exposes the QR/code flow directly.
4. **Groups** — add them from the WhatsApp panel. Nothing outside a configured
   group is answered.

## Configuration

Most values are in `.env`; see `.env.example` for the full list.

| Variable | Meaning |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter key used by OpenCode and the vision worker |
| `KIROCREW_MODEL` | Chat model, default `openrouter/nvidia/nemotron-3-ultra-550b-a55b:free` |
| `KIROCREW_REASONING_EFFORT` | `high` by default |
| `OPENROUTER_VISION_MODEL` | `nvidia/nemotron-3.5-super-vl:free` |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `TELEGRAM_ALLOWED_USER_IDS` | comma-separated numeric Telegram IDs |
| `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` | Slack Socket Mode |
| `DISCORD_BOT_TOKEN` | Discord bot |
| `WECOM_BOT_ID`, `WECOM_SECRET` | WeCom |
| `WEBEX_BOT_TOKEN`, `WEIXIN_TOKEN` | Webex / Weixin |
| `MICROSOFT_APP_ID`, `MICROSOFT_APP_PASSWORD`, `MICROSOFT_APP_TENANT_ID` | Microsoft Teams |
| `FEISHU_APP_ID`, `FEISHU_APP_SECRET` | Feishu |
| `KIROCREW_ENABLE_WHATSAPP` | `1` starts the channel; `0` disables it |
| `WHATSAPP_PAIR_PHONE` | phone number used by `whatsapp-pair.sh` when no CLI argument is given |
| `WHATSAPP_ALLOWED_WA_IDS` | optional DM/group sender allowlist |
| `KIROCREW_STT_MODEL` | whisper.cpp model, default `small` |
| `KIROCREW_STT_LANGUAGE` | default `it` |
| `KIROCREW_YOLO` | `1` auto-approves agent tools; `0` keeps approvals |
| `KIROCREW_ALLOW_UNSANDBOXED` | `1` accepts container-only isolation if the inner sandbox is refused |
| `KIROCREW_PUBLIC_URL` | public dashboard origin for links/CORS |
| `KIROCREW_BIND_HOST`, `KIROCREW_PORT` | published address and port |

The seed is idempotent. On an existing volume it merges the stack-managed keys
(model, effort, roles, sandbox, STT, channel enable flags). Groups, skills,
memory, sessions and channel credentials are preserved. Set
`KIROCREW_PRESERVE_AGENT_MODEL=1` to keep a model chosen in the dashboard.

## Updates

The image is immutable; the volume carries state. To update KiroCrew:

```bash
deploy/opencode/scripts/update.sh
```

It fetches `origin/main`, rebases local commits (refusing a dirty checkout),
rebuilds the wheel and image, and recreates the container. The volume is not
touched.

To update OpenCode:

```bash
deploy/opencode/scripts/update-opencode.sh --build
```

It reads the latest published version and the sha512 of the x64/arm64 tarballs
from the registry, writes them to `.env`, and rebuilds.

To update only the base KiroCrew image:

```bash
docker compose -f deploy/opencode/compose.yaml build --pull
docker compose -f deploy/opencode/compose.yaml up -d
```

Pin `KIROCREW_PYTHON_BASE` if you need a specific Python base image.

## Private server / offline transfer

Build on a machine with network access, then either push to a registry:

```bash
REGISTRY=ghcr.io/your-org deploy/opencode/scripts/push.sh
```

or export a single file:

```bash
deploy/opencode/scripts/export.sh
# on the server:
gunzip -c kirocrew-opencode-0.12.0.tar.gz | docker load
cd deploy/opencode && docker compose up -d
```

Copy `deploy/opencode/` (compose, `.env`, scripts, seccomp profile) to the
server. The image already contains KiroCrew, OpenCode, WhatsApp and Telegram
support; no build toolchain is needed there.

## State and self-sufficiency

One named volume, `kirocrew-home`, holds **everything that changes**:

- `~/whatsapp-web/data/wa.db` — the WhatsApp archive, plus `media/` with the
  downloaded photos, voice notes, videos and documents
- `~/.kiro/crew/whatsapp/session.db` — the linked-device credentials
- `~/.kiro/crew/config.json`, `.env`, skills, sessions, memory and crons
- `~/.config/opencode` and `~/.local/share/opencode` — OpenCode config/auth and
  sessions
- `~/.kiro/crew/models/whisper` — the local STT model after the first download

That makes the deployment self-sufficient after the first pairing: restarting,
recreating, or updating the container does not lose the archive, the WhatsApp
link, the settings or the agent sessions. `docker compose down` keeps the
volume; only `docker compose down -v` (or `docker volume rm`) deletes it.

The archive is **this deployment's own** archive, populated from the messages
the linked device receives. It is not a copy of the ASUS `wa.db`; if you want
the old history migrated, copy `wa.db` and `media/` into the volume yourself.
The image never contains either.

External dependencies remain: OpenRouter for the model, WhatsApp/Telegram for
the channels, and the network for the first STT model download. After those,
the archive and the agent state are local to the volume.

## Persistence and backup

One named volume, `kirocrew-home`, holds:

- `~/.kiro/crew` — config, credentials, memory, skills, sessions, WhatsApp
  session and archive;
- `~/.config/opencode` and `~/.local/share/opencode` — OpenCode config/auth and
  session state;
- `~/whatsapp-web` — the local WhatsApp archive (`wa.db`, media).

Back up with:

```bash
docker run --rm -v kirocrew-home:/data -v "$PWD":/backup alpine \
  tar czf /backup/kirocrew-home.tgz -C /data .
```

Restore into a fresh volume before the first start.

## Security

- The image contains no credentials. `.env` is gitignored; never commit it.
- The dashboard is token-authenticated and bound to `127.0.0.1` by default.
  Expose it only behind a TLS reverse proxy on a private network.
- `KIROCREW_YOLO=1` lets the agent run tools without per-call approval, as on
  the ASUS setup. The container (plus the inner user-namespace sandbox when
  available) is the boundary. Set it to `0` if you want approvals.
- WhatsApp uses the unofficial WhatsApp Web protocol through `neonize`. It can
  get a number banned; use a number you can afford to lose. The channel has no
  bulk-send feature and only answers configured chats/groups.
- The image does not include the host's `~/.ssh`, `~/.aws`, browser profile,
  skills, conversations or `wa.db`. The smoke test asserts this.

## Troubleshooting

```bash
docker compose -f deploy/opencode/compose.yaml logs -f --tail=200
docker compose -f deploy/opencode/compose.yaml ps
docker exec -it kirocrew kirocrew doctor
docker exec -it kirocrew opencode --version
docker exec -it kirocrew sh -c 'curl -fsS http://127.0.0.1:5476/api/health'
docker exec -it kirocrew kirocrew config get agent.model
```

If the container is healthy but agent commands are refused, the inner sandbox
was blocked by the runtime. Either keep the bundled seccomp profile or set
`KIROCREW_ALLOW_UNSANDBOXED=1` (the default in `.env.example`) and restart.

If OpenCode cannot authenticate, check:

```bash
docker exec -it kirocrew sh -c 'test -f ~/.local/share/opencode/auth.json && echo auth-present'
docker exec -it kirocrew sh -c 'grep -o "openrouter" ~/.local/share/opencode/auth.json | head -1'
```

The key is read from `.env` on every start; rotating it and restarting updates
both the gateway `.env` and the OpenCode auth store.

## Smoke test

```bash
deploy/opencode/scripts/smoke.sh
```

It builds the image, starts a throwaway compose project on port `15476`, checks
health, OpenCode, neonize, pywhispercpp, the seeded model/effort, a dashboard
token, and that the image contains no personal data. It removes its volume on
exit; `KEEP=1` leaves it running for a manual dashboard/WhatsApp check.

One real model turn against the running deployment:

```bash
deploy/opencode/scripts/live-check.sh
```

It prints the assistant reply and the session's `turn_stats.model`, which is how
you confirm OpenRouter/OpenCode and the seeded Nemotron model end to end.
