"""The quoted message must reach the model as the exact message replied to."""

from __future__ import annotations

import sqlite3

from kiro_crew.whatsapp import history

CHAT = "72254402592996@lid"


def _db(path):
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
        "body, msg_type, transcript, audio_path) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [
            ("quoted", CHAT, "Maki", "Maki", 0, 1000, "", "voice",
             "il vocale giusto", "/tmp/quoted.ogg"),
            ("other", CHAT, "Maki", "Maki", 0, 1001, "", "voice",
             "l'altro vocale", "/tmp/other.ogg"),
            ("op", CHAT, "Maki", "156740704288819", 1, 1002, "", "voice",
             "", "/tmp/op.ogg"),
        ],
    )
    con.commit()
    con.close()


def test_quoted_context_is_the_exact_row_not_another_voice(tmp_path):
    db = tmp_path / "wa.db"
    _db(db)
    text = history.quoted_message_context(CHAT, "quoted", db_path=str(db))
    assert "il vocale giusto" in text
    assert "l'altro vocale" not in text
    assert "msg_id=quoted" in text
    assert "QUESTO" in text


def test_quoted_context_names_the_audio_file_when_untranscribed(tmp_path):
    db = tmp_path / "wa.db"
    _db(db)
    text = history.quoted_message_context(CHAT, "op", db_path=str(db))
    assert "io:" in text
    assert "file audio: /tmp/op.ogg" in text
    assert "msg_id=op" in text
