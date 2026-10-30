#!/usr/bin/env python3
"""Read-only, paged access to the whole WhatsApp archive (wa.db).

The wake-word turn already receives the newest 200 messages / 14 days as
context. This script is for everything older or for a search the operator asked
for explicitly: the complete history, from the first message the archive holds.

Usage:
  wa_archive.py chats [--query TEXT] [--limit N] [--json]
  wa_archive.py stats <chat> [--json]
  wa_archive.py first <chat> [--limit N] [--json]
  wa_archive.py history <chat> [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                            [--limit N] [--offset N] [--asc] [--json]
  wa_archive.py search <text> [--chat <chat>] [--from YYYY-MM-DD]
                            [--to YYYY-MM-DD] [--limit N] [--json]
  wa_archive.py export <chat> [--from YYYY-MM-DD] [--to YYYY-MM-DD]
                            [--limit N] [--format md|jsonl]
  wa_archive.py who <number-or-jid> [...] [--json]

<chat> is a JID (…@g.us, …@s.whatsapp.net, …@lid), a `name:` label, or a
contact/group name. A name that matches more than one chat is refused with the
candidate list, so the caller picks a chat_id instead of guessing.

A sender is resolved through the WhatsApp client's own contact store: LID →
phone JID, then the group-visible push name, then full name, then first name.
An unresolved number stays a number; it is never guessed from context.

Read-only by construction: the connection is opened with mode=ro and nothing in
this file writes.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys

DB = os.environ.get(
    "KIROCREW_WA_ARCHIVE", os.path.expanduser("~/whatsapp-web/data/wa.db")
)
SESSION_DB = os.environ.get(
    "KIROCREW_WA_SESSION",
    os.path.expanduser("~/.kiro/crew/whatsapp/session.db"),
)
MAX_LIMIT = 500
DEFAULT_LIMIT = 50


class Identity:
    """Turn what ``wa.db`` stored in ``sender`` into a stable display name.

    Depending on the collector path, ``sender`` holds a WhatsApp display name, a
    phone JID local part, or a LID local part. A number is not a name: two
    different numbers must never collapse into one person because the context
    seemed to suggest it. ``session.db`` is the mapping the WhatsApp client
    itself keeps, so resolution is deterministic:

    * a LID is followed through ``whatsmeow_lid_map`` to its phone JID;
    * the label is the contact's **push name** (what the group actually sees),
      then the full name, then the first name;
    * an unknown number is printed as a number;
    * a display name already stored in ``wa.db`` is kept verbatim: it came from
      WhatsApp when the message arrived and may be a group nickname ("Piersito")
      that is not the contact's push name. Rewriting it would rename a person.
    """

    def __init__(self, path: str = SESSION_DB) -> None:
        self.by_jid: dict[str, str] = {}
        self.lid_to_pn: dict[str, str] = {}
        if not os.path.exists(path):
            return
        try:
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
            c.row_factory = sqlite3.Row
        except sqlite3.Error:
            return
        try:
            try:
                self.lid_to_pn = {
                    str(r["lid"]): str(r["pn"])
                    for r in c.execute("SELECT lid, pn FROM whatsmeow_lid_map")
                    if r["lid"] and r["pn"]
                }
            except sqlite3.Error:
                self.lid_to_pn = {}
            rows = c.execute(
                "SELECT their_jid, first_name, full_name, push_name "
                "FROM whatsmeow_contacts"
            ).fetchall()
        except sqlite3.Error:
            return
        finally:
            c.close()
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
            self.by_jid.setdefault(jid, label)
            local = jid.split("@", 1)[0]
            # A LID contact entry and its phone entry are the same person.
            # Teach the phone JID the LID's label when it has no row of its
            # own, so a phone-number sender still resolves.
            if jid.endswith("@lid") and local in self.lid_to_pn:
                self.by_jid.setdefault(
                    f"{self.lid_to_pn[local]}@s.whatsapp.net", label
                )

    @staticmethod
    def _local(raw: str) -> str:
        return raw.split("@", 1)[0].split(":", 1)[0]

    def resolve(self, raw: str) -> str:
        raw = (raw or "").strip()
        if not raw:
            return "?"
        local = self._local(raw)
        if not local.isdigit():
            # A display name written by WhatsApp at capture time. Keep it: it
            # can be a nickname the group knows, not the contact's push name.
            return raw
        candidates: list[str] = []
        if local in self.lid_to_pn:
            candidates.append(f"{local}@lid")
            candidates.append(f"{self.lid_to_pn[local]}@s.whatsapp.net")
        elif raw.endswith("@lid"):
            candidates.append(f"{local}@lid")
        else:
            candidates.append(f"{local}@s.whatsapp.net")
            candidates.append(f"{local}@lid")
        for jid in candidates:
            label = self.by_jid.get(jid)
            if label:
                return label
        return local


_IDENTITY: Identity | None = None


def identity() -> Identity:
    global _IDENTITY
    if _IDENTITY is None:
        _IDENTITY = Identity()
    return _IDENTITY


def con() -> sqlite3.Connection:
    if not os.path.exists(DB):
        sys.exit(f"archive not found: {DB}")
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=5.0)
    c.row_factory = sqlite3.Row
    return c


def ts(v) -> str:
    if not v:
        return "?"
    return datetime.datetime.fromtimestamp(int(v)).strftime("%Y-%m-%d %H:%M")


def day(v: str | None) -> int | None:
    if not v:
        return None
    try:
        # Midnight in the HOST's timezone, which is the timezone every rendered
        # timestamp uses. Pinning UTC here moved a --from/--to boundary by one
        # or two hours and silently dropped messages near it.
        return int(datetime.datetime.strptime(v, "%Y-%m-%d").astimezone().timestamp())
    except ValueError:
        sys.exit(f"bad date {v!r}; expected YYYY-MM-DD")


def clamp(n: int) -> int:
    return max(1, min(MAX_LIMIT, int(n)))


def body(row: sqlite3.Row) -> str:
    kind = row["msg_type"] or "text"
    text = (row["body"] or "").strip()
    transcript = (row["transcript"] or "").strip()
    desc = (row["media_desc"] or "").strip()
    if kind in ("voice", "audio"):
        return transcript or "[voice note without transcript]"
    if kind == "image":
        return f"[photo] {desc or text}".strip()
    if kind == "video":
        return f"[video] {desc or text}".strip()
    if kind == "sticker":
        return "[sticker]"
    if kind == "document":
        # media_desc is the extracted document text (PDF/DOCX/...), not a
        # caption. Without it the line was just "[document]" and the agent
        # concluded the file had never been downloaded.
        return f"[document] {desc or text}".strip()
    return text or f"[{kind}]"


def one_line(text: str, width: int = 1000) -> str:
    line = " ".join((text or "").split())
    return line if len(line) <= width else line[: width - 1] + "…"


def sender(row: sqlite3.Row) -> str:
    if row["from_me"]:
        return "io"
    if str(row["msg_type"] or "") == "system":
        return "sistema"
    raw = (row["sender"] or "").strip()
    if raw:
        return identity().resolve(raw)
    # Legacy rows carry no sender. In a direct chat `chat_name` is the person;
    # in a group it is the group, and showing it as the author is the bug this
    # function exists to prevent.
    name = (row["chat_name"] or "").strip()
    if name and not (row["chat_id"] or "").endswith("@g.us"):
        plain = name.lstrip("+")
        return identity().resolve(plain if plain.isdigit() else name)
    return "?"


def file_note(row: sqlite3.Row) -> str:
    path = (row["media_path"] or "").strip()
    return f" [file: {path}]" if path else ""


def render(row: sqlite3.Row) -> str:
    return f"[{ts(row['ts'])}] {sender(row)}: {one_line(body(row))}{file_note(row)}"


def as_dict(row: sqlite3.Row) -> dict:
    return {
        "msg_id": row["msg_id"],
        "ts": int(row["ts"] or 0),
        "date": ts(row["ts"]),
        "chat_id": row["chat_id"],
        "chat_name": row["chat_name"],
        "sender": sender(row),
        "sender_raw": row["sender"],
        "from_me": bool(row["from_me"]),
        "type": row["msg_type"],
        "text": body(row),
        "media_path": row["media_path"],
        "media_mime": row["media_mime"],
        "media_desc": row["media_desc"],
        "transcript": row["transcript"],
    }


def _has_table(c: sqlite3.Connection, name: str) -> bool:
    try:
        return (
            c.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (name,),
            ).fetchone()
            is not None
        )
    except sqlite3.Error:
        return False


def _require_messages(c: sqlite3.Connection) -> None:
    """A fresh volume has a wa.db with no messages table until the first message."""
    if not _has_table(c, "messages"):
        print("archive is empty: no messages have arrived yet", file=sys.stderr)
        raise SystemExit(0)


def resolve_many(c: sqlite3.Connection, raw: str) -> list[str]:
    """The chat_id(s) named by *raw*.

    A name can map to more than one id: the archive kept a display-label row
    (``name:Filippo Rossi``) for the pre-LID history AND the LID row for recent
    traffic. Exact name matches are therefore MERGED — "the whole chat from the
    first day" spans both. A partial name that matches different people refuses
    with candidates instead of guessing.
    """
    raw = (raw or "").strip()
    if not raw:
        sys.exit("missing chat")
    if "@" in raw or raw.startswith("name:"):
        return [raw]
    if not _has_table(c, "chats"):
        sys.exit("archive is empty: no chats yet")
    rows = c.execute(
        "SELECT c.chat_id, c.name, COUNT(m.msg_id) n, MAX(m.ts) last "
        "FROM chats c LEFT JOIN messages m ON m.chat_id = c.chat_id "
        "WHERE c.name = ? COLLATE NOCASE GROUP BY c.chat_id ORDER BY last DESC",
        (raw,),
    ).fetchall()
    if rows:
        return [r["chat_id"] for r in rows]
    rows = c.execute(
        "SELECT c.chat_id, c.name, COUNT(m.msg_id) n, MAX(m.ts) last "
        "FROM chats c LEFT JOIN messages m ON m.chat_id = c.chat_id "
        "WHERE c.name LIKE ? COLLATE NOCASE GROUP BY c.chat_id ORDER BY last DESC",
        (f"%{raw}%",),
    ).fetchall()
    if len(rows) == 1:
        return [rows[0]["chat_id"]]
    if not rows:
        sys.exit(f"no chat matches {raw!r}")
    print("multiple chats match; pass one of these chat_id values:", file=sys.stderr)
    for r in rows:
        print(
            f"  {r['chat_id']}  {r['name'] or ''}  "
            f"({r['n']} messages, last {ts(r['last'])})",
            file=sys.stderr,
        )
    sys.exit(2)


def marks(ids: list[str]) -> str:
    return ",".join("?" for _ in ids)


def cmd_chats(a) -> None:
    c = con()
    if not _has_table(c, "chats"):
        if a.json:
            print("[]")
        else:
            print("archive is empty: no chats yet")
        return
    sql = (
        "SELECT c.chat_id, c.name, COUNT(m.msg_id) n, MIN(m.ts) first, MAX(m.ts) last "
        "FROM chats c LEFT JOIN messages m ON m.chat_id = c.chat_id "
    )
    args: tuple = ()
    if a.query:
        sql += "WHERE c.name LIKE ? COLLATE NOCASE "
        args = (f"%{a.query}%",)
    sql += "GROUP BY c.chat_id ORDER BY last DESC LIMIT ?"
    rows = c.execute(sql, (*args, clamp(a.limit))).fetchall()
    if a.json:
        print(
            json.dumps(
                [
                    {
                        "chat_id": r["chat_id"],
                        "name": r["name"],
                        "messages": r["n"],
                        "first": ts(r["first"]),
                        "last": ts(r["last"]),
                    }
                    for r in rows
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    for r in rows:
        print(
            f"{r['chat_id']} | {r['name'] or ''} | {r['n']} msg | "
            f"{ts(r['first'])} → {ts(r['last'])}"
        )


def cmd_stats(a) -> None:
    c = con()
    _require_messages(c)
    ids = resolve_many(c, a.chat)
    in_sql = marks(ids)
    head = c.execute(
        f"SELECT COUNT(*) n, MIN(ts) first, MAX(ts) last FROM messages "
        f"WHERE chat_id IN ({in_sql})",
        tuple(ids),
    ).fetchone()
    types = c.execute(
        f"SELECT msg_type, COUNT(*) n FROM messages WHERE chat_id IN ({in_sql}) "
        "GROUP BY msg_type ORDER BY n DESC",
        tuple(ids),
    ).fetchall()
    first = c.execute(
        f"SELECT * FROM messages WHERE chat_id IN ({in_sql}) ORDER BY ts ASC LIMIT 1",
        tuple(ids),
    ).fetchone()
    last = c.execute(
        f"SELECT * FROM messages WHERE chat_id IN ({in_sql}) ORDER BY ts DESC LIMIT 1",
        tuple(ids),
    ).fetchone()
    data = {
        "chat_ids": ids,
        "messages": head["n"],
        "first": ts(head["first"]),
        "last": ts(head["last"]),
        "types": {r["msg_type"] or "text": r["n"] for r in types},
        "first_message": as_dict(first) if first else None,
        "last_message": as_dict(last) if last else None,
    }
    if a.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    print(f"chats:  {', '.join(ids)}")
    print(f"range:  {data['first']} → {data['last']} ({data['messages']} messages)")
    print("types:  " + ", ".join(f"{k}={v}" for k, v in data["types"].items()))
    if first:
        print(f"first:  {render(first)}")
    if last:
        print(f"last:   {render(last)}")


def _range(a) -> tuple[int | None, int | None]:
    return day(getattr(a, "from_", None)), day(getattr(a, "to", None))


def cmd_history(a) -> None:
    c = con()
    _require_messages(c)
    ids = resolve_many(c, a.chat)
    start, end = _range(a)
    where = [f"chat_id IN ({marks(ids)})"]
    args: list = list(ids)
    if start is not None:
        where.append("ts >= ?")
        args.append(start)
    if end is not None:
        where.append("ts < ?")
        args.append(end + 86400)
    order = "ASC" if a.asc else "DESC"
    args.extend([clamp(a.limit), max(0, a.offset)])
    rows = c.execute(
        f"SELECT * FROM messages WHERE {' AND '.join(where)} "
        f"ORDER BY ts {order} LIMIT ? OFFSET ?",
        tuple(args),
    ).fetchall()
    if a.json:
        print(json.dumps([as_dict(r) for r in rows], ensure_ascii=False, indent=2))
        return
    for r in rows:
        print(render(r))


def cmd_first(a) -> None:
    a.asc = True
    a.offset = 0
    a.from_ = None
    a.to = None
    cmd_history(a)


def cmd_search(a) -> None:
    c = con()
    if not _has_table(c, "messages"):
        if a.json:
            print("[]")
        else:
            print("archive is empty: no messages have arrived yet")
        return
    ids = resolve_many(c, a.chat) if a.chat else []
    start, end = _range(a)
    limit = clamp(a.limit)
    # FTS5 first; quote each term so a query with punctuation is not read as
    # FTS syntax. If it still fails, fall back to LIKE.
    terms = [t for t in re.split(r"\s+", a.text.strip()) if t]
    fts_query = " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)
    rows = []
    try:
        sql = (
            "SELECT m.* FROM msg_fts f JOIN messages m ON m.rowid = f.rowid "
            "WHERE msg_fts MATCH ?"
        )
        args: list = [fts_query]
        if ids:
            sql += f" AND m.chat_id IN ({marks(ids)})"
            args.extend(ids)
        if start is not None:
            sql += " AND m.ts >= ?"
            args.append(start)
        if end is not None:
            sql += " AND m.ts < ?"
            args.append(end + 86400)
        sql += " ORDER BY m.ts DESC LIMIT ?"
        args.append(limit)
        rows = c.execute(sql, tuple(args)).fetchall()
    except sqlite3.Error:
        rows = []
    if not rows:
        like = f"%{a.text.strip()}%"
        sql = (
            "SELECT * FROM messages WHERE (body LIKE ? OR transcript LIKE ? "
            "OR media_desc LIKE ?)"
        )
        args = [like, like, like]
        if ids:
            sql += f" AND chat_id IN ({marks(ids)})"
            args.extend(ids)
        if start is not None:
            sql += " AND ts >= ?"
            args.append(start)
        if end is not None:
            sql += " AND ts < ?"
            args.append(end + 86400)
        sql += " ORDER BY ts DESC LIMIT ?"
        args.append(limit)
        rows = c.execute(sql, tuple(args)).fetchall()
    if a.json:
        print(json.dumps([as_dict(r) for r in rows], ensure_ascii=False, indent=2))
        return
    if not rows:
        print("no matches")
        return
    for r in rows:
        print(f"{r['chat_name'] or r['chat_id']} · {render(r)}")


def cmd_export(a) -> None:
    a.asc = True
    a.offset = 0
    a.json = False
    c = con()
    _require_messages(c)
    ids = resolve_many(c, a.chat)
    start, end = _range(a)
    where = [f"chat_id IN ({marks(ids)})"]
    args: list = list(ids)
    if start is not None:
        where.append("ts >= ?")
        args.append(start)
    if end is not None:
        where.append("ts < ?")
        args.append(end + 86400)
    args.append(max(1, min(100_000, a.limit)))
    rows = c.execute(
        f"SELECT * FROM messages WHERE {' AND '.join(where)} "
        "ORDER BY ts ASC LIMIT ?",
        tuple(args),
    ).fetchall()
    if a.format == "jsonl":
        for r in rows:
            print(json.dumps(as_dict(r), ensure_ascii=False))
        return
    for r in rows:
        print(render(r))


def cmd_who(a) -> None:
    resolved = [(raw, identity().resolve(raw)) for raw in a.values]
    if a.json:
        print(json.dumps(dict(resolved), ensure_ascii=False, indent=2))
        return
    for raw, name in resolved:
        print(f"{raw} -> {name}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    pc = sub.add_parser("chats")
    pc.add_argument("--query", default="")
    pc.add_argument("--limit", type=int, default=200)
    pc.add_argument("--json", action="store_true")
    pc.set_defaults(func=cmd_chats)

    ps = sub.add_parser("stats")
    ps.add_argument("chat")
    ps.add_argument("--json", action="store_true")
    ps.set_defaults(func=cmd_stats)

    pf = sub.add_parser("first")
    pf.add_argument("chat")
    pf.add_argument("--limit", type=int, default=20)
    pf.add_argument("--json", action="store_true")
    pf.set_defaults(func=cmd_first)

    ph = sub.add_parser("history")
    ph.add_argument("chat")
    ph.add_argument("--from", dest="from_", default=None)
    ph.add_argument("--to", default=None)
    ph.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ph.add_argument("--offset", type=int, default=0)
    ph.add_argument("--asc", action="store_true")
    ph.add_argument("--json", action="store_true")
    ph.set_defaults(func=cmd_history)

    pse = sub.add_parser("search")
    pse.add_argument("text")
    pse.add_argument("--chat", default="")
    pse.add_argument("--from", dest="from_", default=None)
    pse.add_argument("--to", default=None)
    pse.add_argument("--limit", type=int, default=30)
    pse.add_argument("--json", action="store_true")
    pse.set_defaults(func=cmd_search)

    pe = sub.add_parser("export")
    pe.add_argument("chat")
    pe.add_argument("--from", dest="from_", default=None)
    pe.add_argument("--to", default=None)
    pe.add_argument("--limit", type=int, default=5000)
    pe.add_argument("--format", choices=("md", "jsonl"), default="md")
    pe.set_defaults(func=cmd_export)

    pw = sub.add_parser(
        "who", help="resolve a sender number/LID to the name the group sees"
    )
    pw.add_argument("values", nargs="+")
    pw.add_argument("--json", action="store_true")
    pw.set_defaults(func=cmd_who)

    a = p.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()
