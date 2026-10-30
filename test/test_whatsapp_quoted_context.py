"""The quoted message must reach the model as the exact message replied to."""

from __future__ import annotations

import sqlite3
import time

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
    con.execute(
        "INSERT INTO messages (msg_id, chat_id, chat_name, sender, from_me, ts, "
        "body, msg_type, media_path, media_mime, media_desc) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "doc",
            CHAT,
            "Maki",
            "Maki",
            0,
            int(time.time()),
            "",
            "document",
            "/tmp/contract.docx",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "[Document: contract.docx]\nCONTRATTO DI LOCAZIONE",
        ),
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


def test_quoted_document_carries_its_extracted_text_and_path(tmp_path):
    """A quoted contract is the contract, not the word "[documento]".

    The extracted text is already in ``media_desc``; the path is how the agent
    opens the original when it needs more than the extracted text.
    """
    db = tmp_path / "wa.db"
    _db(db)
    text = history.quoted_message_context(CHAT, "doc", db_path=str(db))
    assert "CONTRATTO DI LOCAZIONE" in text
    assert "[file: /tmp/contract.docx]" in text
    assert "[documento] [file: /tmp/contract.docx]" in text
    assert "[Document: contract.docx]" in text


def test_recent_history_carries_a_documents_text_and_path(tmp_path):
    db = tmp_path / "wa.db"
    _db(db)
    text = history.recent_chat_context(CHAT, db_path=str(db), limit=10)
    assert "CONTRATTO DI LOCAZIONE" in text
    assert "[file: /tmp/contract.docx]" in text
