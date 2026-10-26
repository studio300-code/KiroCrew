"""WhatsApp sender identity: LID -> phone -> push name, never a guess.

The archive stores group senders as a LID or a phone number. The contact book
the WhatsApp client itself keeps is the only authority for their name; the
group's own name, or a plausible name from the conversation, must never become
the author.
"""

from __future__ import annotations

import sqlite3

import kiro_crew.whatsapp.history as history
from kiro_crew.whatsapp.identity import IdentityResolver

GROUP = "120363430853234757@g.us"


def _session_db(path, contacts, lids=()):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE whatsmeow_contacts ("
        "our_jid TEXT, their_jid TEXT, first_name TEXT, full_name TEXT, "
        "push_name TEXT, business_name TEXT, redacted_phone TEXT, "
        "PRIMARY KEY (our_jid, their_jid))"
    )
    con.execute(
        "CREATE TABLE whatsmeow_lid_map (lid TEXT PRIMARY KEY, "
        "pn TEXT UNIQUE NOT NULL)"
    )
    con.executemany(
        "INSERT INTO whatsmeow_contacts VALUES (?,?,?,?,?,?,?)",
        [
            ("me@s.whatsapp.net", jid, first, full, push, "", "")
            for jid, first, full, push in contacts
        ],
    )
    con.executemany("INSERT INTO whatsmeow_lid_map VALUES (?,?)", lids)
    con.commit()
    con.close()


def _archive_db(path, rows):
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE messages (msg_id TEXT PRIMARY KEY, chat_id TEXT, "
        "chat_name TEXT, sender TEXT, from_me INTEGER, ts INTEGER, "
        "ts_raw TEXT, body TEXT, msg_type TEXT, transcript TEXT, "
        "audio_path TEXT, media_path TEXT, media_mime TEXT, media_desc TEXT, "
        "link_text TEXT, transcript_ts INTEGER, frames_desc TEXT)"
    )
    con.executemany(
        "INSERT INTO messages (msg_id, chat_id, chat_name, sender, from_me, ts, "
        "body, msg_type) VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    con.commit()
    con.close()


def _resolver(tmp_path):
    db = tmp_path / "session.db"
    _session_db(
        db,
        contacts=[
            ("393403486586@s.whatsapp.net", "", "Mara", "FedeM"),
            ("393498563866@s.whatsapp.net", "", "pano", "Ale_pano"),
            ("3844146770130@lid", "", "", "FedeM"),
            ("72254402592996@lid", "", "", "Maki"),
            ("393000000001@s.whatsapp.net", "", "Marco", "Marco Uno"),
            ("393000000002@s.whatsapp.net", "", "Marco", "Marco Due"),
        ],
        lids=[
            ("3844146770130", "393403486586"),
            ("72254402592996", "393341847681"),
        ],
    )
    return IdentityResolver(db)


def test_lid_resolves_to_phone_contact_push_name(tmp_path):
    assert _resolver(tmp_path).resolve("3844146770130") == "FedeM"


def test_lid_with_only_its_own_contact_still_resolves(tmp_path):
    assert _resolver(tmp_path).resolve("72254402592996") == "Maki"


def test_phone_and_text_alias_resolve_to_the_same_push_name(tmp_path):
    resolver = _resolver(tmp_path)
    assert resolver.resolve("393403486586") == "FedeM"
    assert resolver.resolve("393403486586@s.whatsapp.net") == "FedeM"
    assert resolver.resolve("Mara") == "FedeM"
    assert resolver.resolve("pano") == "Ale_pano"


def test_ambiguous_name_is_left_alone(tmp_path):
    assert _resolver(tmp_path).resolve("Marco") == "Marco"


def test_unknown_number_stays_a_number(tmp_path):
    assert _resolver(tmp_path).resolve("999999999999") == "999999999999"


def test_missing_session_db_never_invents_a_name(tmp_path):
    resolver = IdentityResolver(tmp_path / "absent.db")
    assert resolver.resolve("3844146770130") == "3844146770130"


def test_recent_context_uses_resolved_names_not_the_group_name(
    tmp_path, monkeypatch
):
    archive = tmp_path / "wa.db"
    _archive_db(
        archive,
        [
            ("m1", GROUP, "Cape Town 27", "3844146770130", 0, 1000, "ciao", "text"),
            ("m2", GROUP, "Cape Town 27", "393453396492", 1, 1001, "musone", "text"),
            ("m3", GROUP, "Cape Town 27", "999999999999", 0, 1002, "eh", "text"),
            ("m4", GROUP, "Cape Town 27", "Cape Town 27", 0, 1003, "x", "system"),
        ],
    )
    monkeypatch.setattr(history, "identity", lambda: _resolver(tmp_path))
    text = history.recent_chat_context(GROUP, db_path=str(archive))
    assert "FedeM: ciao" in text
    assert "io: musone" in text
    assert "999999999999: eh" in text
    assert "sistema: x" in text
    assert "Cape Town 27:" not in text
