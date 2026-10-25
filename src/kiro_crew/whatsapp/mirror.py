"""Write every WhatsApp message into the local archive (wa.db).

No browser, no collector, no HTTP hop: the live client is a linked device, so it
already receives every message that reaches the account. This tap copies each one
straight into the archive before any admission decision, so the history keeps the
chats and groups the agent is configured not to answer.

Fail-open by construction: bounded queues, short timeouts, every error swallowed.
The archive must never slow down or break a turn.

Media (voice notes, images) are downloaded by a SECOND worker and processed:
- voice -> transcriber.sh (orukeet/parakeet) -> messages.transcript
- image/video -> OpenRouter API (Nemotron 3.5 Super VL vision) -> messages.media_desc

Both write back into wa.db so the skill sees them. No browser, no sweep.

PERSISTENT MEDIA QUEUE: media items are stored in a `media_jobs` table in wa.db
so they survive restarts. The worker picks up pending jobs on start.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import pickle
import sqlite3
import subprocess
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Awaitable, Callable

import aiohttp

from kiro_crew.whatsapp.media import musone_wake, unwrap_message

logger = logging.getLogger(__name__)
logger.warning("whatsapp mirror: MODULE LOADED")

DB_PATH = os.environ.get(
    "KIROCREW_WA_ARCHIVE",
    str(Path.home() / "whatsapp-web/data/wa.db"),
)

#: whatsmeow's own store, where the contact book lives. The archive's
#: ``chat_name`` was empty for @lid chats whose stanza carried no PushName, so a
#: name search ("what did Filippo say?") missed their whole history.
_SESSION_DB = Path(
    os.environ.get("KIROCREW_HOME", str(Path.home() / ".kiro" / "crew"))
) / "whatsapp" / "session.db"


def _contact_name(sender_user: str) -> str:
    """The address book's name for *sender_user*, or ``""``.

    Reads whatsmeow's read-only contact store: the same LID can be looked up
    directly, and a LID with no row is mapped to its phone JID first. Cosmetic
    only -- every failure returns ``""`` rather than blocking a message.
    """
    if not sender_user or not _SESSION_DB.is_file():
        return ""
    con = None
    try:
        con = sqlite3.connect(f"file:{_SESSION_DB}?mode=ro", uri=True, timeout=2.0)
        candidates = [f"{sender_user}@lid", f"{sender_user}@s.whatsapp.net"]
        row = con.execute(
            "SELECT COALESCE(NULLIF(push_name,''), NULLIF(full_name,''), "
            "NULLIF(first_name,'')) FROM whatsmeow_contacts "
            "WHERE their_jid IN (?, ?) LIMIT 1",
            tuple(candidates),
        ).fetchone()
        if row and row[0]:
            return str(row[0]).strip()
        phone = con.execute(
            "SELECT pn FROM whatsmeow_lid_map WHERE lid = ? LIMIT 1", (sender_user,)
        ).fetchone()
        if phone and phone[0]:
            row = con.execute(
                "SELECT COALESCE(NULLIF(push_name,''), NULLIF(full_name,''), "
                "NULLIF(first_name,'')) FROM whatsmeow_contacts "
                "WHERE their_jid = ? LIMIT 1",
                (f"{phone[0]}@s.whatsapp.net",),
            ).fetchone()
            if row and row[0]:
                return str(row[0]).strip()
    except sqlite3.Error:
        return ""
    finally:
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
    return ""

MEDIA_ROOT = os.environ.get(
    "KIROCREW_WA_MEDIA",
    str(Path.home() / "whatsapp-web/data/media"),
)
QUEUE_MAX = 2000
NAME_CACHE_MAX = 2000

# OpenRouter config for vision
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_VISION_MODEL = os.environ.get("OPENROUTER_VISION_MODEL", "nvidia/nemotron-3.5-super-vl:free")
OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"

#: media.py kinds -> the strings the archive stores in messages.msg_type
_KIND_MAP = {
    "voice": "voice",
    "audio": "audio",
    "image": "image",
    "video": "video",
    "document": "document",
    "sticker": "sticker",
    "text": "text",
}

_INSERT_MESSAGE = """
INSERT INTO messages(msg_id,chat_id,chat_name,sender,from_me,ts,ts_raw,body,msg_type)
VALUES(?,?,?,?,?,?,?,?,?)
ON CONFLICT(msg_id) DO UPDATE SET
  from_me=MAX(messages.from_me,excluded.from_me),
  msg_type=CASE WHEN messages.msg_type='text' AND excluded.msg_type IN
      ('voice','audio','image','video','sticker','document')
    THEN excluded.msg_type ELSE messages.msg_type END,
  sender=CASE
    WHEN TRIM(COALESCE(excluded.sender,''))<>'' THEN excluded.sender
    WHEN TRIM(COALESCE(messages.sender,''))<>'' THEN messages.sender
    ELSE '' END,
  body=CASE WHEN (messages.body IS NULL OR TRIM(messages.body)='')
             AND TRIM(excluded.body)<>''
    THEN excluded.body ELSE messages.body END
"""

_UPSERT_CHAT = """
INSERT INTO chats(chat_id,name,last_seen,unread,preview,extra)
VALUES(?,?,?,?,?,?)
ON CONFLICT(chat_id) DO UPDATE SET
  name=CASE WHEN TRIM(COALESCE(excluded.name,''))<>'' THEN excluded.name
            ELSE chats.name END,
  last_seen=MAX(chats.last_seen,excluded.last_seen)
"""

# Update only media columns (transcript, media_path, media_desc, frames_desc)
_UPDATE_MEDIA = """
UPDATE messages SET
  transcript=COALESCE(NULLIF(TRIM(:transcript), ''), transcript),
  media_path=COALESCE(NULLIF(TRIM(:media_path), ''), media_path),
  media_desc=COALESCE(NULLIF(TRIM(:media_desc), ''), media_desc),
  frames_desc=COALESCE(NULLIF(TRIM(:frames_desc), ''), frames_desc)
WHERE msg_id=:msg_id
"""

# Persistent media job queue table
_CREATE_MEDIA_JOBS = """
CREATE TABLE IF NOT EXISTS media_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_id TEXT NOT NULL UNIQUE,
    chat_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    raw_envelope BLOB NOT NULL,
    ts INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending, processing, done, failed
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT DEFAULT '',
    created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_media_jobs_status ON media_jobs(status);
"""

_CLAIM_JOB = """
UPDATE media_jobs SET
    status='processing',
    attempts=attempts+1,
    updated_at=strftime('%s','now')
WHERE id = (
    SELECT id FROM media_jobs
    WHERE status='pending' AND attempts < 5
    ORDER BY created_at ASC
    LIMIT 1
)
RETURNING id, msg_id, chat_id, kind, raw_envelope;
"""

_MARK_DONE = """
UPDATE media_jobs SET
    status='done',
    updated_at=strftime('%s','now')
WHERE id=?;
"""

_MARK_FAILED = """
UPDATE media_jobs SET
    status='failed',
    last_error=?,
    updated_at=strftime('%s','now')
WHERE id=?;
"""

_RESET_STALE = """
UPDATE media_jobs SET
    status='pending',
    updated_at=strftime('%s','now')
WHERE status='processing' AND updated_at < strftime('%s','now','-10 minutes');
"""


def msg_type(kind: str) -> str:
    return _KIND_MAP.get(str(kind or ""), "text")


class MirrorTap:
    """Fire-and-forget copy of inbound/outbound messages into wa.db.

    Two pipelines:
    1. Messages (text + type) -> fast, batched, < 10ms per batch
    2. Media (voice/image) -> PERSISTENT queue in wa.db (media_jobs table),
       separate worker downloads via client, transcribes/describes, updates row.
       Jobs survive restarts and are retried on failure.
    """

    def __init__(
        self,
        client: Any,
        db_path: str | None = None,
        on_voice_wake: Callable[[str, str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._client = client
        self._db_path = db_path or DB_PATH
        #: Called once a VOICE note is transcribed, with
        #: ``(chat_id, transcript, message_id)``. The transport uses it to honor
        #: a spoken wake word, which no stanza-time check can see.
        self._on_voice_wake = on_voice_wake
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=QUEUE_MAX)
        self._task: asyncio.Task | None = None
        self._media_task: asyncio.Task | None = None
        self._names: OrderedDict[str, str] = OrderedDict()
        self.dropped = 0
        self.written = 0
        self.media_written = 0
        self.media_dropped = 0
        self.last_error = ""

        # Ensure media_jobs table exists
        self._init_media_jobs_table()

        # The docstring promises pending jobs are picked up on start, and the
        # durable queue is empty of a live submitter after a restart: without
        # this, a job left pending by a shutdown waits for the next inbound
        # message. Constructed inside the gateway loop, so the loop is running;
        # a construction outside one (tests, tooling) simply stays lazy.
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            self._ensure_worker()

    def _init_media_jobs_table(self) -> None:
        try:
            con = sqlite3.connect(self._db_path, timeout=5.0)
            con.execute("PRAGMA busy_timeout=5000")
            with con:
                for stmt in _CREATE_MEDIA_JOBS.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        con.execute(stmt)
        except Exception as e:
            logger.warning("whatsapp mirror: media_jobs table init failed: %s", e)
        finally:
            try:
                con.close()
            except Exception:
                pass

    # -- producer ---------------------------------------------------------

    def submit(
        self,
        *,
        message_id: str,
        chat: str,
        sender: str,
        from_me: bool,
        ts: int,
        body: str,
        kind: str,
        is_group: bool,
        push_name: str = "",
        sender_name: str = "",
        # NEW: raw envelope for media download
        raw_envelope: Any = None,
    ) -> None:
        if not message_id or not chat:
            return
        # neonize carries the message timestamp in MILLISECONDS; the archive
        # stores Unix seconds like every other writer of wa.db.
        stamp = int(ts or 0) or int(time.time())
        if stamp > 10**12:
            stamp //= 1000
        item = {
            "id": message_id,
            "chat_id": chat,
            "sender": sender,
            "from_me": bool(from_me),
            "ts": stamp,
            "body": (body or "")[:8000],
            "msg_type": msg_type(kind),
            "_group": bool(is_group),
            "_push": (push_name or "")[:200],
            "_sender_name": (sender_name or "")[:200],
            # For media worker
            "_raw_envelope": raw_envelope,
            "_kind": kind,
        }
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            self.dropped += 1
            if self.dropped % 100 == 1:
                logger.warning("whatsapp mirror: queue full, dropped=%d", self.dropped)
            return
        self._ensure_worker()

        # If media, PERSIST to media_jobs table (survives restarts)
        # Skip stickers - they often can't be downloaded via standard method
        if kind in ("voice", "audio", "image", "video", "document") and raw_envelope is not None:
            self._persist_media_job(message_id, chat, kind, raw_envelope, stamp)
        elif kind == "sticker":
            logger.debug("whatsapp mirror: skipping sticker download (not supported)")

    def _persist_media_job(self, msg_id: str, chat_id: str, kind: str, raw_envelope: Any, ts: int) -> None:
        """Write media job to persistent queue. Runs in worker thread."""
        try:
            # Serialize protobuf to bytes (more reliable than pickle for protobuf messages)
            logger.warning("whatsapp mirror: PERSIST raw_envelope type=%s", type(raw_envelope))
            if raw_envelope is None:
                logger.warning("whatsapp mirror: PERSIST raw_envelope is None!")
                return
            if hasattr(raw_envelope, "SerializeToString"):
                serialize_method = getattr(raw_envelope, "SerializeToString")
                if callable(serialize_method):
                    raw_bytes = serialize_method()
                else:
                    logger.warning("whatsapp mirror: SerializeToString is not callable: %s", serialize_method)
                    raw_bytes = pickle.dumps(raw_envelope)
            else:
                logger.warning("whatsapp mirror: no SerializeToString, using pickle")
                raw_bytes = pickle.dumps(raw_envelope)
            con = sqlite3.connect(self._db_path, timeout=5.0)
            con.execute("PRAGMA busy_timeout=5000")
            with con:
                con.execute(
                    """
                    INSERT OR IGNORE INTO media_jobs (msg_id, chat_id, kind, raw_envelope, ts)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (msg_id, chat_id, kind, raw_bytes, ts),
                )
            logger.warning("whatsapp mirror: PERSISTED media job id=%s kind=%s", msg_id, kind)
        except Exception as e:
            logger.warning("whatsapp mirror: PERSIST failed id=%s: %s", msg_id, e)
        finally:
            try:
                con.close()
            except Exception:
                pass

    def _ensure_worker(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(
                self._run(), name="whatsapp-mirror"
            )
        if self._media_task is None or self._media_task.done():
            logger.info("whatsapp mirror: starting media worker")
            self._media_task = asyncio.get_running_loop().create_task(
                self._run_media(), name="whatsapp-mirror-media"
            )

    # -- consumer: messages ----------------------------------------------

    async def _run(self) -> None:
        batch: list[dict] = []
        try:
            while True:
                timeout = 1.0 if batch else None
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    await self._flush(batch)
                    batch = []
                    continue
                batch.append(await self._with_name(item))
                if len(batch) >= 25:
                    await self._flush(batch)
                    batch = []
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a dead tap must not kill the channel
            logger.warning("whatsapp mirror: worker stopped", exc_info=True)

    async def _flush(self, batch: list[dict]) -> None:
        if not batch:
            return
        rows = [{k: v for k, v in it.items() if not k.startswith("_")} for it in batch]
        written, err = await asyncio.to_thread(self._write, rows)
        self.written += written
        if err:
            self.last_error = err
            logger.debug("whatsapp mirror: %s", err)

    def _write(self, rows: list[dict]) -> tuple[int, str]:
        """Blocking batch insert; runs in a worker thread."""
        try:
            con = sqlite3.connect(self._db_path, timeout=5.0)
        except Exception as e:  # noqa: BLE001
            return 0, f"open failed: {e}"
        try:
            con.execute("PRAGMA busy_timeout=5000")
            con.execute("PRAGMA synchronous=NORMAL")
            with con:
                for r in rows:
                    con.execute(
                        _INSERT_MESSAGE,
                        (
                            r["id"],
                            r["chat_id"],
                            r.get("chat_name") or "",
                            r.get("sender") or "",
                            1 if r.get("from_me") else 0,
                            r.get("ts") or 0,
                            str(r.get("ts") or ""),
                            r.get("body") or "",
                            r.get("msg_type") or "text",
                        ),
                    )
                    preview = (r.get("body") or "").replace("\n", " ")[:200]
                    con.execute(
                        _UPSERT_CHAT,
                        (
                            r["chat_id"],
                            r.get("chat_name") or "",
                            r.get("ts") or 0,
                            0,
                            preview,
                            "",
                        ),
                    )
            return len(rows), ""
        except Exception as e:  # noqa: BLE001
            return 0, f"write failed: {e}"
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass

    # -- consumer: media (persistent queue) -------------------------------

    async def _run_media(self) -> None:
        """Process persistent media jobs from wa.db. One at a time."""
        logger.info("whatsapp mirror: media worker STARTED")
        try:
            while True:
                # The durable queue is drained at boot, before the WhatsApp
                # client finishes connecting. A job claimed in that window fails
                # with "client is not connected" and would be spent; wait for the
                # link instead.
                if not getattr(self._client, "is_connected", True):
                    await asyncio.sleep(5)
                    continue

                # Reset any stale processing jobs (crashed worker)
                await asyncio.to_thread(self._reset_stale_jobs)

                job = await asyncio.to_thread(self._claim_job)
                if job is None:
                    # No pending jobs, wait a bit
                    await asyncio.sleep(2)
                    continue

                job_id, msg_id, chat_id, kind, raw_bytes = job
                logger.warning("whatsapp mirror: CLAIMED job id=%s msg_id=%s kind=%s", job_id, msg_id, kind)

                # Deserialize protobuf from bytes.
                #
                # The producer serializes the neonize EVENT (MessageEv), not the
                # inner waE2E.Message: transport.receive() passes the MessageEv
                # it was handed. Parsing those bytes as waE2E.Message succeeds
                # (protobuf ignores unknown fields) but yields a message with no
                # media node at all, so download_media() answered "didn't find
                # any attachments in message" for every voice note.
                #
                # Parse as MessageEv first and keep the event; fall back to the
                # inner Message so jobs written by any older producer still
                # load.
                try:
                    from neonize.proto.Neonize_pb2 import Message as MessageEv
                    from neonize.proto.waE2E.WAWebProtobufsE2E_pb2 import Message
                    try:
                        event = MessageEv()
                        event.ParseFromString(raw_bytes)
                        if event.HasField("Message"):
                            raw_envelope = event
                        else:
                            msg = Message()
                            msg.ParseFromString(raw_bytes)
                            raw_envelope = msg
                    except Exception:
                        msg = Message()
                        msg.ParseFromString(raw_bytes)
                        raw_envelope = msg
                except Exception as e:
                    logger.warning("whatsapp mirror: PARSE failed job_id=%s: %s", job_id, e)
                    await asyncio.to_thread(self._mark_job_failed, job_id, f"parse: {e}")
                    continue

                try:
                    await self._process_media(job_id, msg_id, chat_id, kind, raw_envelope)
                    await asyncio.to_thread(self._mark_job_done, job_id)
                    self.media_written += 1
                except Exception as e:  # noqa: BLE001 - single media failure must not stop the worker
                    logger.warning("whatsapp mirror: PROCESS failed job_id=%s: %s", job_id, e)
                    if "not connected" in str(e).lower():
                        # Transient at boot: put it back without spending an
                        # attempt, or a restart could exhaust the retry budget.
                        await asyncio.to_thread(self._requeue_job, job_id)
                    else:
                        await asyncio.to_thread(self._mark_job_failed, job_id, str(e))

        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("whatsapp mirror: media worker stopped", exc_info=True)

    def _claim_job(self) -> tuple | None:
        """Atomically claim the next pending job. Returns (id, msg_id, chat_id, kind, raw_envelope) or None."""
        try:
            con = sqlite3.connect(self._db_path, timeout=5.0)
            con.execute("PRAGMA busy_timeout=5000")
            cur = con.execute(_CLAIM_JOB)
            row = cur.fetchone()
            con.close()
            return row
        except Exception as e:
            logger.debug("whatsapp mirror: claim_job failed: %s", e)
            return None

    def _mark_job_done(self, job_id: int) -> None:
        con = None
        try:
            con = sqlite3.connect(self._db_path, timeout=5.0)
            con.execute("PRAGMA busy_timeout=5000")
            with con:
                con.execute(_MARK_DONE, (job_id,))
        except Exception as e:
            logger.debug("whatsapp mirror: mark_done failed: %s", e)
        finally:
            try:
                if con is not None:
                    con.close()
            except Exception:
                pass

    def _mark_job_failed(self, job_id: int, error: str) -> None:
        con = None
        try:
            con = sqlite3.connect(self._db_path, timeout=5.0)
            con.execute("PRAGMA busy_timeout=5000")
            with con:
                con.execute(_MARK_FAILED, (error[:500], job_id))
        except Exception as e:
            logger.debug("whatsapp mirror: mark_failed failed: %s", e)
        finally:
            try:
                if con is not None:
                    con.close()
            except Exception:
                pass

    def _requeue_job(self, job_id: int) -> None:
        """Return a job claimed during a transient fault to the queue.

        Resets ``attempts`` too: the failure was the client not being linked
        yet, not the job, and five restarts would otherwise exhaust the retry
        budget without ever trying to download.
        """
        con = None
        try:
            con = sqlite3.connect(self._db_path, timeout=5.0)
            con.execute("PRAGMA busy_timeout=5000")
            with con:
                con.execute(
                    "UPDATE media_jobs SET status='pending', attempts=0,"
                    " updated_at=strftime('%s','now') WHERE id=?",
                    (job_id,),
                )
        except Exception as e:
            logger.debug("whatsapp mirror: requeue failed: %s", e)
        finally:
            try:
                if con is not None:
                    con.close()
            except Exception:
                pass

    def _reset_stale_jobs(self) -> None:
        """Reset stuck 'processing' jobs back to 'pending' for retry."""
        con = None
        try:
            con = sqlite3.connect(self._db_path, timeout=5.0)
            con.execute("PRAGMA busy_timeout=5000")
            with con:
                con.execute(_RESET_STALE)
        except Exception:
            pass
        finally:
            try:
                if con is not None:
                    con.close()
            except Exception:
                pass

    async def _process_media(self, job_id: int, msg_id: str, chat_id: str, kind: str, raw_envelope: Any) -> None:
        logger.warning("whatsapp mirror: _process_media START job_id=%s msg_id=%s kind=%s", job_id, msg_id, kind)

        # The transport passes the ALREADY UNWRAPPED message (inner), not the raw MessageEv.
        # Check if it's a MessageEv (has 'Message' field) or already the inner waE2E.Message.
        inner_msg = getattr(raw_envelope, "Message", None)
        if inner_msg is not None:
            # It's a MessageEv, unwrap to get the inner message
            inner = unwrap_message(inner_msg)
            logger.warning("whatsapp mirror: unwrapped from MessageEv, inner type=%s", type(inner))
        else:
            # Already the inner waE2E.Message (what transport passes)
            inner = raw_envelope
            logger.warning("whatsapp mirror: using envelope directly as inner, type=%s", type(inner))

        # Extra unwrap in case transport's unwrap missed a carrier layer
        inner = unwrap_message(inner)
        logger.warning("whatsapp mirror: final inner type=%s", type(inner))

        # Debug: log what media fields the message has
        try:
            from kiro_crew.whatsapp.media import _MEDIA_FIELDS, _has
            for name, _ in _MEDIA_FIELDS:
                if _has(inner, name):
                    logger.warning("whatsapp mirror: found media field %s", name)
        except Exception:
            pass

        # Download via client (async, neonize dispatches to worker thread internally)
        try:
            logger.warning("whatsapp mirror: DOWNLOAD start job_id=%s msg_id=%s", job_id, msg_id)
            data = await asyncio.wait_for(
                self._client.download_media(inner),
                timeout=30.0,
            )
            logger.warning("whatsapp mirror: DOWNLOAD done job_id=%s msg_id=%s bytes=%s", job_id, msg_id, len(data) if data else 0)
        except Exception as e:  # noqa: BLE001
            logger.warning("whatsapp mirror: DOWNLOAD failed job_id=%s msg_id=%s: %s", job_id, msg_id, e)
            raise

        if not data:
            logger.warning("whatsapp mirror: DOWNLOAD empty job_id=%s msg_id=%s", job_id, msg_id)
            raise RuntimeError("empty download")

        # Save to disk under media root
        ext = self._ext_for_kind(kind)
        media_dir = Path(MEDIA_ROOT) / kind
        media_dir.mkdir(parents=True, exist_ok=True)
        # Sanitize msg_id for filename
        safe_id = msg_id.replace("/", "_").replace(":", "_")[:64]
        path = media_dir / f"{safe_id}.{ext}"
        try:
            logger.warning("whatsapp mirror: SAVE start job_id=%s path=%s", job_id, path)
            await asyncio.to_thread(path.write_bytes, data)
            logger.warning("whatsapp mirror: SAVE done job_id=%s", job_id)
        except Exception as e:  # noqa: BLE001
            logger.warning("whatsapp mirror: SAVE failed job_id=%s: %s", job_id, e)
            raise

        # Process by kind
        transcript = ""
        media_desc = ""
        frames_desc = ""

        if kind in ("voice", "audio"):
            logger.warning("whatsapp mirror: TRANSCRIBE start job_id=%s", job_id)
            transcript = await self._transcribe(path)
            logger.warning("whatsapp mirror: TRANSCRIBE done job_id=%s len=%s", job_id, len(transcript))
        elif kind in ("image", "sticker"):
            logger.warning("whatsapp mirror: VISION start job_id=%s", job_id)
            media_desc = await self._describe_image(path)
            logger.warning("whatsapp mirror: VISION done job_id=%s len=%s", job_id, len(media_desc))
        elif kind == "video":
            logger.warning("whatsapp mirror: VISION VIDEO start job_id=%s", job_id)
            media_desc, frames_desc = await self._describe_video(path)
            logger.warning("whatsapp mirror: VISION VIDEO done job_id=%s", job_id)

        # Update DB row (messages table)
        if transcript or media_desc or frames_desc:
            logger.warning("whatsapp mirror: DB UPDATE start job_id=%s msg_id=%s", job_id, msg_id)
            await asyncio.to_thread(
                self._update_media_row,
                msg_id,
                str(path),
                transcript,
                media_desc,
                frames_desc,
            )
            logger.warning("whatsapp mirror: DB UPDATE done job_id=%s msg_id=%s", job_id, msg_id)
        else:
            logger.warning("whatsapp mirror: NO CONTENT to update job_id=%s msg_id=%s", job_id, msg_id)
            raise RuntimeError("no transcript or description produced")

        # A spoken wake word is only readable once the transcript exists. The
        # transport answers it in the chat the note was sent to.
        if transcript and kind in ("voice", "audio"):
            await self._maybe_voice_wake(msg_id, chat_id, transcript)

    async def _maybe_voice_wake(self, msg_id: str, chat_id: str, transcript: str) -> None:
        """Call the transport's wake hook for the operator's own voice note.

        ``from_me`` is read back from the archive row rather than trusted from
        the job, so a note someone forwarded into a chat the operator did not
        write can never wake the agent.
        """
        if self._on_voice_wake is None or not musone_wake(transcript):
            return
        con = None
        try:
            con = sqlite3.connect(self._db_path, timeout=5.0)
            con.execute("PRAGMA busy_timeout=5000")
            row = con.execute(
                "SELECT from_me, ts FROM messages WHERE msg_id = ?", (msg_id,)
            ).fetchone()
        except Exception as e:  # noqa: BLE001 - a lookup fault must not fail the job
            logger.debug("whatsapp mirror: voice wake lookup failed: %s", e)
            return
        finally:
            try:
                if con is not None:
                    con.close()
            except Exception:  # noqa: BLE001
                pass
        if not row or not row[0]:
            return
        # Only a note that just arrived may wake the agent. The worker also
        # drains jobs written before this feature existed; answering one of
        # those would reply into a conversation hours after the fact.
        if time.time() - float(row[1] or 0) > 600:
            return
        try:
            await self._on_voice_wake(chat_id, transcript, msg_id)
        except Exception:  # noqa: BLE001 - the archive row is already saved
            logger.warning("whatsapp mirror: voice wake dispatch failed", exc_info=True)

    def _ext_for_kind(self, kind: str) -> str:
        return {
            "voice": "opus",
            "audio": "m4a",
            "image": "jpg",
            "video": "mp4",
            "sticker": "webp",
            "document": "bin",
        }.get(kind, "bin")

    async def _transcribe(self, path: Path) -> str:
        """Run transcriber.sh and return transcript text."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "/home/marco/.config/opencode/scripts/transcriber.sh",
                "--lang", "it",
                "--model", "orukeet",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120.0)
            if proc.returncode != 0:
                logger.debug("transcriber failed: %s", stderr.decode()[:200])
                return ""
            # Parse output: "TXT=/path/to/file.txt"
            for line in stdout.decode().splitlines():
                if line.startswith("TXT="):
                    txt_path = Path(line.split("=", 1)[1].strip())
                    if txt_path.exists():
                        return txt_path.read_text(encoding="utf-8").strip()
            return ""
        except Exception as e:  # noqa: BLE001
            logger.debug("transcriber error: %s", e)
            return ""

    async def _describe_image(self, path: Path) -> str:
        """Describe image via OpenRouter API (Nemotron 3.5 Super VL vision)."""
        if not OPENROUTER_API_KEY:
            logger.debug("image description skipped: no OPENROUTER_API_KEY")
            return ""
        try:
            # Read and encode image as base64
            img_bytes = await asyncio.to_thread(path.read_bytes)
            b64 = base64.b64encode(img_bytes).decode()
            mime = "image/jpeg"
            if path.suffix.lower() in (".png",):
                mime = "image/png"
            elif path.suffix.lower() in (".webp",):
                mime = "image/webp"

            payload = {
                "model": OPENROUTER_VISION_MODEL,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Descrivi questa foto in italiano in al massimo 50 parole: "
                                    "soggetti, luogo, eventuali testi leggibili (riportali tra "
                                    "virgolette) e azione. Solo la descrizione, niente preamboli."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:{mime};base64,{b64}"},
                            },
                        ],
                    }
                ],
                "max_tokens": 200,
                "temperature": 0.1,
                # The free Nemotron vision model reasons by default, and with a
                # 200-token cap it spends the whole budget on `reasoning` and
                # answers `content: null` — every image description came back
                # empty. Turning reasoning off gives a direct answer.
                "reasoning": {"enabled": False},
            }

            headers = {
                "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                "Content-Type": "application/json",
                "HTTP-Referer": "https://github.com/kiro-crew",
                "X-Title": "Kiro Crew WhatsApp Mirror",
            }

            timeout = aiohttp.ClientTimeout(total=60)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(OPENROUTER_API_URL, json=payload, headers=headers) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        logger.debug("OpenRouter vision failed %s: %s", resp.status, text[:200])
                        return ""
                    data = await resp.json()
                    message = (data.get("choices") or [{}])[0].get("message") or {}
                    content = message.get("content") or message.get("reasoning") or ""
                    return str(content).strip()[:600]

        except Exception as e:  # noqa: BLE001
            logger.debug("OpenRouter vision error: %s", e)
            return ""

    async def _describe_video(self, path: Path) -> tuple[str, str]:
        """Describe video via OpenRouter API by sampling frames.

        Extracts frames at intervals using ffmpeg, describes each via vision model,
        returns (summary, frames_desc).
        """
        try:
            # Get video duration
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1", str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await proc.communicate()
            duration = float(stdout.decode().strip() or "0")
        except Exception:
            duration = 0

        if duration <= 0:
            logger.warning("whatsapp mirror: video duration unknown, skipping frame extraction")
            return await self._describe_image(path), ""

        # Sample frames: max 5 frames, evenly spaced
        num_frames = min(5, max(1, int(duration / 10)))
        frame_times = [duration * (i + 0.5) / (num_frames + 1) for i in range(num_frames)]

        frame_descriptions = []
        for i, t in enumerate(frame_times):
            # Extract frame at time t
            frame_path = path.with_suffix(f".frame{i}.jpg")
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-ss", str(t), "-i", str(path),
                    "-vframes", "1", "-q:v", "2", str(frame_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=30.0)
                if proc.returncode != 0 or not frame_path.exists():
                    continue

                # Describe frame
                desc = await self._describe_image(frame_path)
                if desc:
                    frame_descriptions.append(f"[{i+1}/{num_frames} @ {t:.1f}s] {desc}")

                # Cleanup frame
                try:
                    frame_path.unlink()
                except Exception:
                    pass

            except Exception as e:
                logger.debug("whatsapp mirror: frame extraction failed at %.1fs: %s", t, e)
                continue

        if not frame_descriptions:
            return await self._describe_image(path), ""

        # Build summary and frames_desc
        summary = " | ".join(frame_descriptions[:3])  # First 3 for summary
        frames_desc = "\n\n".join(frame_descriptions)
        return summary[:600], frames_desc[:2000]

    def _update_media_row(
        self,
        msg_id: str,
        media_path: str,
        transcript: str,
        media_desc: str,
        frames_desc: str,
    ) -> None:
        try:
            con = sqlite3.connect(self._db_path, timeout=5.0)
            con.execute("PRAGMA busy_timeout=5000")
            with con:
                con.execute(
                    _UPDATE_MEDIA,
                    {
                        "transcript": transcript,
                        "media_path": media_path,
                        "media_desc": media_desc,
                        "frames_desc": frames_desc,
                        "msg_id": msg_id,
                    },
                )
        except Exception as e:  # noqa: BLE001
            logger.debug("whatsapp mirror: media update failed: %s", e)
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass

    async def _with_name(self, item: dict) -> dict:
        chat = item["chat_id"]
        name = self._names.get(chat)
        if name is None:
            name = ""
            try:
                if item.get("_group"):
                    getter = getattr(self._client, "group_name", None)
                    if getter is not None:
                        name = await asyncio.wait_for(getter(chat), 3)
                else:
                    name = item.get("_sender_name") or item.get("_push") or ""
                    if not name:
                        # The CHAT's contact, not the sender's: for an outbound
                        # message the sender is us and the reader still needs the
                        # conversation's name. Fall back to the sender for an
                        # inbound stanza whose chat JID is an alias.
                        chat_user = str(chat).split("@", 1)[0]
                        name = await asyncio.to_thread(_contact_name, chat_user)
                    if not name:
                        name = await asyncio.to_thread(
                            _contact_name, str(item.get("sender") or "")
                        )
            except Exception:  # noqa: BLE001 - names are cosmetic
                name = ""
            if name:
                self._names[chat] = name
                while len(self._names) > NAME_CACHE_MAX:
                    self._names.popitem(last=False)
        if name:
            item["chat_name"] = name
        return item