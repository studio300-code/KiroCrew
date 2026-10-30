---
name: whatsapp
description: "Read, search and summarise the WhatsApp archive at ~/whatsapp-web/data/wa.db. Use for questions about past messages, groups, voice notes, photos, videos or documents, including old history and attachments."
triggers: whatsapp, wa, chat, group, gruppo, voice note, vocale, trascrizione, cosa ha detto, cosa si sono detti, cerca nei messaggi, riassumi la chat, ultimi messaggi, chi ha scritto, tutta la chat, dal primo giorno, storico completo, allegato, documento
---

# WhatsApp archive

The gateway's linked device writes every message it receives into
`~/whatsapp-web/data/wa.db`, before any admission decision. The archive is
read-only for you and is the complete local history for this deployment.

- A message the operator sends to the agent is answered in that chat.
- In a group, an ordinary message is not a summons. The agent answers only to
  the wake word, an @-mention of the linked account, a reply to one of its
  messages, or an operator slash command.
- A reply/quote block in the prompt names the exact message being answered.

## Paths

| What | Path |
|---|---|
| Archive database | `~/whatsapp-web/data/wa.db` |
| Downloaded media | `~/whatsapp-web/data/media/{image,voice,video,document}/` |
| WhatsApp session | `~/.kiro/crew/whatsapp/session.db` |

## The archive tool

`<skill dir>/scripts/wa_archive.py` is read-only and paged. A name query merges
every `chat_id` that carries that exact name, because the archive can split one
conversation across a phone JID, a LID and a display label.

```bash
S=<skill dir>/scripts/wa_archive.py

python3 $S stats "Family"
python3 $S first "Family" --limit 20
python3 $S history "Family" --asc --limit 100 --offset 0
python3 $S history "Family" --from 2026-09-01 --to 2026-09-30 --limit 100
python3 $S search "invoice" --chat "Family" --limit 30
python3 $S export "Family" --format md --limit 100000 > /tmp/family.md
python3 $S who 447700900111 --json
```

Rules:

- Page with `--limit`/`--offset` (max 500). Never dump a multi-thousand-message
  chat into the answer; summarise or export to a file and read it in slices.
- If a name matches different people, the tool refuses with candidates; pass
  the `chat_id` instead of guessing.
- A LID is not a phone number. Never present it as one.
- Use the resolved sender the tool prints. Do not infer a name from the
  conversation or carry one forward from an earlier summary.

## Sending a generated file

To put a generated PDF, image, audio or video back into the current chat, call
the MCP tool `file_send` with the absolute `path`. It copies the file to the
outbox and delivers it to the conversation the session belongs to. Never use
`wa_send.py`, `ssh`, `scp` or a browser driver: those are denied by policy and
the turn fails.

To convert a DOCX/ODT to PDF:

```bash
libreoffice --headless --convert-to pdf --outdir <dir> <file.docx>
```

Then pass the produced PDF path to `file_send`.

## Media and documents

A row can carry:

- `media_path` — the file on disk
- `media_mime`
- `media_desc` — photo/video description, or text extracted from a PDF/DOCX
- `transcript` — voice/video transcript

Read `media_desc` and `transcript` first. If a document has no extracted text
(a scan), open `media_path`; the text-reader tool may reject a binary DOCX/PDF
with `file is not valid UTF-8`. In that case extract it with Kiro Crew's parser:

```bash
PYTHONPATH=/usr/local/lib/python3.12/site-packages \
  python3 -c 'from kiro_crew.doc_parser import extract_text; print(extract_text("<path>"))'
```

Do not claim a document was never downloaded when the row has a `media_path`.

## Raw SQL fallback

Always `sqlite3 -readonly`. Schema:

- `chats(chat_id, name, last_seen, unread, preview, extra)`
- `messages(msg_id, chat_id, chat_name, sender, from_me, ts, ts_raw, body,
  msg_type, transcript, audio_path, media_path, media_mime, media_desc,
  link_text, transcript_ts, frames_desc)`

`ts` is Unix seconds (display in the host timezone). When filtering by a local
wall-clock time, SQLite reads `strftime('%s','2026-10-30 12:15')` as UTC and
drops the previous hours; use `strftime('%s','2026-10-30 12:15','utc')` or the
script's local `--from`/`--to`.

`sender` is raw: a LID, a phone number, or a display name written by WhatsApp.
Resolve it with `wa_archive.py who`, or use the script, which does it for you.
A display name is already an attribution; keep it as stored.

## Answering

- Italian if the operator writes Italian; quote with chat, sender and date.
- Say whether something is typed text, a voice transcript, a photo description
  or a document's extracted text. Missing data is missing, not silence.
- Never invent a JID, a phone number or a chat that is not in the archive.
