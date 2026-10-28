"""Durable record of the messages this WhatsApp channel sent.

The group reply gate asks "is the quoted message one the AGENT sent?". The
linked account IS the operator's account, so a ``from_me`` JID or the quoted
message's participant cannot answer it: the operator's own messages carry the
same account. Only the send itself knows, so every outbound message id is
appended to a small owner-only log and loaded back when the gateway starts.

This is deliberately separate from :class:`~kiro_crew.whatsapp.echo.EchoTracker`:
the echo tracker is a short-lived in-memory guard against answering our own
echo, while a reply may quote a message from the previous day or from before a
restart. Entries here live for a month, capped in memory and compacted
periodically so the file stays bounded. Every I/O failure is swallowed: the
reply gate must never break a send.
"""

from __future__ import annotations

import logging
import os
import time
from collections import OrderedDict
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_DEFAULT_TTL_S = 30 * 24 * 60 * 60
_DEFAULT_MAX_ENTRIES = 50_000
#: Rewrite the append-only file once this many sends have accumulated.
_COMPACT_EVERY = 5_000


def _key(conversation_jid: str, message_id: str) -> str:
    # Same composite-key shape as the echo tracker: a message id collision in
    # one chat must not shadow another chat's id.
    return f"{conversation_jid}\n{message_id}"


class BotSentLog:
    """TTL + LRU set of message ids this channel sent, optionally persisted."""

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        ttl_s: float = _DEFAULT_TTL_S,
        max_entries: int = _DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._path = Path(path) if path else None
        self._ttl_s = ttl_s
        self._max_entries = max_entries
        self._clock = clock or time.time
        self._sent: OrderedDict[str, float] = OrderedDict()
        self._appends = 0
        if self._path is not None:
            self._load()

    def remember(self, conversation_jid: str, message_id: str) -> None:
        """Record an outbound message id and persist it best-effort."""
        if not message_id:
            return
        now = self._clock()
        key = _key(conversation_jid, message_id)
        self._sent.pop(key, None)
        self._sent[key] = now
        self._evict(now)
        self._append(conversation_jid, message_id, now)

    def contains(self, conversation_jid: str, message_id: str) -> bool:
        """True when *message_id* is one this channel sent and is still live."""
        if not message_id:
            return False
        now = self._clock()
        self._evict(now)
        key = _key(conversation_jid, message_id)
        stamp = self._sent.get(key)
        if stamp is None:
            return False
        if now - stamp > self._ttl_s:
            self._sent.pop(key, None)
            return False
        return True

    def _evict(self, now: float) -> None:
        while self._sent:
            key, stamp = next(iter(self._sent.items()))
            if now - stamp > self._ttl_s or len(self._sent) > self._max_entries:
                self._sent.pop(key, None)
                continue
            break

    def _load(self) -> None:
        assert self._path is not None
        try:
            raw = self._path.read_text(encoding="utf-8")
        except OSError:
            return
        now = self._clock()
        dropped = False
        for line in raw.splitlines():
            parts = line.split("\t", 2)
            if len(parts) != 3:
                dropped = True
                continue
            try:
                stamp = float(parts[0])
            except ValueError:
                dropped = True
                continue
            chat, message_id = parts[1], parts[2]
            if not chat or not message_id or now - stamp > self._ttl_s:
                dropped = True
                continue
            self._sent[_key(chat, message_id)] = stamp
        self._evict(now)
        if dropped:
            self._rewrite()

    def _append(self, conversation_jid: str, message_id: str, now: float) -> None:
        if self._path is None:
            return
        try:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(f"{now:.0f}\t{conversation_jid}\t{message_id}\n")
        except OSError:
            # The in-memory set still answers for this process; a restart only
            # loses the durable half, never a send.
            logger.debug("whatsapp: could not persist a bot-sent id", exc_info=True)
            return
        self._appends += 1
        if self._appends >= _COMPACT_EVERY:
            self._appends = 0
            self._rewrite()

    def _rewrite(self) -> None:
        if self._path is None:
            return
        tmp = self._path.with_name(f"{self._path.name}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as handle:
                for key, stamp in self._sent.items():
                    conversation_jid, _, message_id = key.partition("\n")
                    handle.write(f"{stamp:.0f}\t{conversation_jid}\t{message_id}\n")
            os.replace(tmp, self._path)
        except OSError:
            logger.debug("whatsapp: could not compact the bot-sent log", exc_info=True)
            try:
                tmp.unlink()
            except OSError:
                pass

    def __len__(self) -> int:
        return len(self._sent)
