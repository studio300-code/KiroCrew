"""Resolve a WhatsApp sender to the display name the group actually sees.

``wa.db`` stores the sender's JID local part for group messages: a phone number
or, with multi-device, a LID. Neither is a name, and two numbers must never
collapse into one person because the surrounding conversation made it sound
plausible. The linked device's own store (``session.db``) is the authority:

* a LID is followed through ``whatsmeow_lid_map`` to its phone JID;
* the label is the contact's **push name** (what the group sees), then the full
  name, then the first name;
* a number with no contact entry stays a number.

Names already stored as text ("Mara", "pano") are aliases of the same contact;
they are rewritten to the push name only when the alias points at exactly one
push name. An ambiguous alias is left untouched.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path

#: How long the contact map is trusted before it is read again. The contact
#: book changes rarely; a long-lived gateway should not need a restart to learn
#: a name, and a per-message read would hit disk for every line of history.
RELOAD_SECONDS = 6 * 60 * 60

SESSION_DB = Path(
    os.environ.get("KIROCREW_HOME", str(Path.home() / ".kiro" / "crew"))
) / "whatsapp" / "session.db"


class IdentityResolver:
    """A cached, read-only view of the WhatsApp contact book."""

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path is not None else SESSION_DB
        self._lock = threading.Lock()
        self._loaded_at = 0.0
        self._by_jid: dict[str, str] = {}
        self._lid_to_pn: dict[str, str] = {}
        self._by_name: dict[str, set[str]] = {}

    @staticmethod
    def _local(raw: str) -> str:
        return str(raw).split("@", 1)[0].split(":", 1)[0]

    def _load(self) -> None:
        by_jid: dict[str, str] = {}
        lid_to_pn: dict[str, str] = {}
        by_name: dict[str, set[str]] = {}
        rows = []
        try:
            con = sqlite3.connect(f"file:{self._path}?mode=ro", uri=True, timeout=5.0)
            con.row_factory = sqlite3.Row
        except sqlite3.Error:
            con = None
        if con is not None:
            try:
                try:
                    lid_to_pn = {
                        str(r["lid"]): str(r["pn"])
                        for r in con.execute("SELECT lid, pn FROM whatsmeow_lid_map")
                        if r["lid"] and r["pn"]
                    }
                except sqlite3.Error:
                    lid_to_pn = {}
                rows = con.execute(
                    "SELECT their_jid, first_name, full_name, push_name "
                    "FROM whatsmeow_contacts"
                ).fetchall()
            except sqlite3.Error:
                rows = []
            finally:
                try:
                    con.close()
                except Exception:  # noqa: BLE001
                    pass
        for row in rows:
            jid = str(row["their_jid"] or "").strip()
            if not jid:
                continue
            push = str(row["push_name"] or "").strip()
            full = str(row["full_name"] or "").strip()
            first = str(row["first_name"] or "").strip()
            label = push or full or first
            if not label:
                continue
            by_jid.setdefault(jid, label)
            local = jid.split("@", 1)[0]
            # A LID contact entry and its phone entry are the same person.
            # Teach the phone JID the LID's label when it has no row of its
            # own, so a phone-number sender still resolves.
            if jid.endswith("@lid") and local in lid_to_pn:
                by_jid.setdefault(f"{lid_to_pn[local]}@s.whatsapp.net", label)
            if push:
                for alias in (full, first):
                    if alias:
                        by_name.setdefault(alias.casefold(), set()).add(push)
        self._by_jid = by_jid
        self._lid_to_pn = lid_to_pn
        self._by_name = by_name
        self._loaded_at = time.monotonic()

    def _ensure_loaded(self) -> None:
        if self._loaded_at and time.monotonic() - self._loaded_at < RELOAD_SECONDS:
            return
        with self._lock:
            if self._loaded_at and time.monotonic() - self._loaded_at < RELOAD_SECONDS:
                return
            self._load()

    def resolve(self, raw: str) -> str:
        """The display name for *raw*, or the number when it has no contact."""
        raw = str(raw or "").strip()
        if not raw:
            return ""
        self._ensure_loaded()
        local = self._local(raw)
        if not local.isdigit():
            names = self._by_name.get(raw.casefold()) or set()
            if len(names) == 1:
                return next(iter(names))
            return raw
        candidates: list[str] = []
        if local in self._lid_to_pn:
            candidates.append(f"{local}@lid")
            candidates.append(f"{self._lid_to_pn[local]}@s.whatsapp.net")
        elif raw.endswith("@lid"):
            candidates.append(f"{local}@lid")
        else:
            candidates.append(f"{local}@s.whatsapp.net")
            candidates.append(f"{local}@lid")
        for jid in candidates:
            label = self._by_jid.get(jid)
            if label:
                return label
        return local


_DEFAULT: IdentityResolver | None = None
_DEFAULT_LOCK = threading.Lock()


def identity() -> IdentityResolver:
    """The process-wide resolver, loaded on first use."""
    global _DEFAULT
    if _DEFAULT is None:
        with _DEFAULT_LOCK:
            if _DEFAULT is None:
                _DEFAULT = IdentityResolver()
    return _DEFAULT
