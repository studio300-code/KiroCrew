"""Layer 1 — WhatsApp (QR-linked personal account) as a ``MessagingTransport``.

Wraps :class:`kiro_crew.whatsapp.client.WhatsAppClient` in the channel-neutral
transport contract. Dependency direction is ``whatsapp -> messaging``
(allowed); ``messaging`` never imports ``whatsapp``.

The inbound pipeline in :meth:`WhatsAppTransport.receive` is a gauntlet every
event must survive, in order:

1. **shape**: the message is unwrapped (ephemeral, view-once and edited
   messages carry the real one inside) and classified by
   :mod:`kiro_crew.whatsapp.media`. Anything that yields neither text, nor
   ingestible media, nor a note worth showing is dropped;
2. **flood gate**: messages older than the connection moment are history
   replay after a reconnect. Never answer a backlog (marks nothing read);
3. **echo gate**: a ``from_me`` message whose ID we sent is our own echo;
   a ``from_me`` message we did NOT send is the operator typing (self-chat
   command surface);
4. **group gate**: group chats are dropped unless configured, then gated by
   mode/mention/cooldown (:mod:`kiro_crew.whatsapp.group_gate`);
5. **authorize**: deny-by-default, with SEL audit on every denial. A DM is
   judged by ``dm_policy`` (``self`` default). A GROUP message is judged by the
   per-sender allowlist, exactly as every other channel judges group traffic:
   the linked account always passes, any other member must be listed in
   ``allowed_wa_ids``, and an empty list admits nobody but the operator. The
   group gate above decides whether the agent may SPEAK in the group; this step
   decides whether the PERSON may make it speak, and it runs for mention,
   reply and rules-mode unprompted turns alike, so an unlisted member cannot
   trigger a reply of any kind. ``dm_policy`` is deliberately not consulted for
   a group: ``open`` would re-admit every member of a configured group;
6. **media fetch**, and only here, and only for an INDIVIDUALLY admitted sender.
   Downloading before this point would let anyone who can message the number
   trigger an authenticated fetch on the operator's host with no authorization
   behind it. Step 5 answers that for a DM through ``dm_policy``; a group sender
   takes the same individual test again at the fetch
   (:meth:`WhatsAppTransport._may_fetch_media`), so the download stays gated on
   the linked account or an explicitly allowed number even if a future caller
   reaches the fetch by another path.

Capabilities (personal account over the Web protocol): the reply streams by
editing one bubble, because this protocol exposes an edit where the Business
Cloud API does not; media rides the shared ingest and upload paths; reactions
carry phase receipts. ``max_buttons=0``, and unlike the other zero-widget channels
this renderer does not fall back to a numbered list: ``turn_renderer._strip_options``
removes a COMPLETE ``[OPTIONS:]`` trailer from the reply, so a question whose
choices live only there reaches the user without them. A tool approval is
unaffected -- ``on_prompt_choice`` builds its own numbered prompt. Declaring no
widget is a deliberately conservative choice, NOT a platform ceiling: the pinned
wheel ships a complete interactive-message builder and a poll builder, and what is
unverified is whether a recipient's client renders a native-flow message sent from
a PERSONAL linked device rather than a Business account. Recording it as impossible
would close the door on every future picker here, so it is recorded as unverified.
Losing the list is a gap rather than a position; ``docs/channel-capabilities.md``
records it for a reader. Unlike
the Business Cloud API there is no 24-hour window, so
``supports_proactive_send=True`` and reminders work.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Awaitable, Callable

from kiro_crew.config.sections import _coerce_whatsapp_groups
from kiro_crew.messaging.attachments import append_attachment_context, cleanup
from kiro_crew.messaging.dispatch import inbound_permitted
from kiro_crew.messaging.outbound_files import OutboundFile
from kiro_crew.messaging.transport import (
    ConfiguredChannelTarget,
    InboundMessage,
    MessagingTransport,
    TransportCapabilities,
)
from kiro_crew.sel import sel
from kiro_crew.whatsapp.attachments import ingest_media
from kiro_crew.whatsapp.bot_sent import BotSentLog
from kiro_crew.whatsapp.client import WhatsAppClient
from kiro_crew.whatsapp.commands import parse_command
from kiro_crew.whatsapp.echo import EchoTracker
from kiro_crew.whatsapp.group_gate import GroupGate, GroupVerdict
from kiro_crew.whatsapp.history import quoted_message_context, recent_chat_context
from kiro_crew.whatsapp.jids import (
    LID_SERVER,
    is_group_jid,
    jid_to_str,
    jid_user,
    normalize_jid,
    wa_id_to_user_jid,
)

from kiro_crew.whatsapp.mirror import MirrorTap
from kiro_crew.whatsapp.media import KIND_POLL, describe, musone_wake, unsupported_note, unwrap_message
from kiro_crew.whatsapp.renderer import WHATSAPP_CHUNK_LIMIT

logger = logging.getLogger(__name__)

#: seconds of pre-connection history still answered after (re)connect. Events
#: older than ``connected_at - GRACE`` are reconnect replay, not live traffic.
_REPLAY_GRACE_S = 60.0

#: An identical reply sent to the same chat inside this window is a duplicate of
#: the previous one, not a second answer. The renderer's documented trade and a
#: retried send can both put the same text on the wire twice; a reader sees that
#: as the agent answering twice, so the transport refuses the second copy.
_DUPLICATE_SEND_WINDOW_S = 15.0

#: Distinct ``@lid`` senders whose phone JID is remembered. An alias is stable
#: for the life of an account, so this only bounds memory against a group with
#: churning membership; a miss costs one resolver round-trip, never a denial.
_ALIAS_CACHE_MAX = 512

#: Said in the group when a member's attachment is not fetched. Chat content, so
#: no catalog path applies; it lives here as the owning module's constant.
NOTE_MEDIA_NOT_ADMITTED = (
    "(Attachment not opened: files are only downloaded for the account owner "
    "and explicitly allowed numbers.)"
)

WHATSAPP_CAPABILITIES = TransportCapabilities(
    # The Web protocol exposes an edit (the Business Cloud API does not), so the
    # renderer streams by editing one bubble and sealing it when the text
    # outgrows a message or the 20-minute edit window closes. Declared True only
    # because the behaviour exists: these flags are a claim other code trusts.
    streaming=True,
    edit=True,
    reactions=True,
    # Images, stickers, voice notes, audio and documents ride the shared ingest
    # path; the fetch happens only after the authorization gauntlet.
    files_inbound=True,
    # The renderer runs the shared outbound extractor at the seal and uploads
    # each raster through client.send_image_bytes; a refusal is reported rather
    # than dropped. Inbound stays False until the download half lands.
    files_outbound=True,
    rich_blocks=False,
    threads=False,
    max_message_chars=WHATSAPP_CHUNK_LIMIT,
    max_buttons=0,
    supports_proactive_send=True,
    # Declared explicitly rather than left to the default. Inbound builds a
    # session key from the chat JID and never resolves a dashboard mirror
    # binding, so a connect from the dashboard is outbound-only; claiming
    # otherwise would promise a two-way link whose replies land in a different
    # session. Spelling it out is also what puts this channel under
    # test_capability_ledger's roster instead of silently outside it.
    supports_session_resume=False,
)

DM_POLICY_SELF = "self"
DM_POLICY_ALLOWLIST = "allowlist"
DM_POLICY_OPEN = "open"
DM_POLICY_DISABLED = "disabled"


class WhatsAppTransport(MessagingTransport):
    """WhatsApp personal-account transport (see module docstring)."""

    channel_type = "whatsapp"

    @property
    def client(self) -> WhatsAppClient:
        """The low-level client (dashboard pairing handlers read state/QR)."""
        return self._client

    # -- Live config ---------------------------------------------------------
    def __init__(
        self,
        client: WhatsAppClient,
        dispatch: Callable[[InboundMessage], Awaitable[None]],
        *,
        dm_policy: str = DM_POLICY_SELF,
        allowed_wa_ids: list[str] | None = None,
        groups: list[dict] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.capabilities = WHATSAPP_CAPABILITIES
        self._client = client
        self._dispatch = dispatch
        self._dm_policy = (dm_policy or "").strip().lower()
        # Frozen at construction so an in-flight decision can't see a mutation.
        self._allowed = frozenset(
            normalize_jid(wa_id_to_user_jid(w)) for w in (allowed_wa_ids or []) if str(w).strip()
        )
        self.echo = EchoTracker()
        #: Durable answer to "did the agent send the quoted message?" for the
        #: group reply gate. The echo tracker forgets after 20 minutes and on
        #: every restart; a reply may quote yesterday's answer. This lives in
        #: the owner-only whatsapp data directory beside the session store.
        bot_sent_path = (
            Path(client.db_path).parent / "bot-sent.log"
            if getattr(client, "db_path", "")
            else None
        )
        self.bot_sent = BotSentLog(bot_sent_path)
        #: copy of every message into the local archive (wa.db), independent of
        #: dm_policy/groups: the archive is history, not admission. The worker
        #: also calls back once a voice note is transcribed, because a spoken
        #: wake word is invisible at stanza time (`_on_voice_wake`).
        self.mirror = MirrorTap(client, on_voice_wake=self._on_voice_wake)
        # Coerced, so a live reload can compare like with like: the loader's
        # coercion is what decides an entry's effective mode and cooldown, and
        # comparing raw config against it would rebuild the gate (resetting its
        # cooldown clock) on every unrelated whatsapp write.
        self._group_rules: list[dict] = _coerce_whatsapp_groups(groups)
        self.group_gate = GroupGate(self._group_rules)
        self._clock = clock or time.time
        #: ``(chat, exact text) -> (sent_at, last message id)`` for the duplicate
        #: guard in ``send_message``. Bounded and pruned on write.
        self._recent_sends: dict[tuple[str, str], tuple[float, str]] = {}
        #: ``@lid`` alias -> phone JID, resolved once per sender (see
        #: ``_canonical_sender``). Confined to the gateway event loop like the
        #: echo tracker, so no lock is taken.
        self._alias: OrderedDict[str, str] = OrderedDict()
        #: verdict metadata for the dispatcher, keyed by id(InboundMessage) —
        #: set in receive() immediately before the dispatch await.
        self.pending_verdicts: dict[int, GroupVerdict] = {}
        #: "this message came from the linked account", decided in receive()
        #: from the full multi-device identity and read back by is_operator().
        #: Same keying and same lifetime as pending_verdicts.
        self.pending_operator: dict[int, bool] = {}
        #: The platform message id of the inbound message, so the dispatcher can
        #: draw a phase reaction on it. InboundMessage carries no id field: it is
        #: a channel-neutral shape and a WhatsApp stanza id means nothing to the
        #: others, so it rides here rather than widening the shared contract.
        self.pending_message_id: dict[int, str] = {}
        #: ``(text as the user sent it, media count)`` captured BEFORE ingestion
        #: rewrites ``msg.text`` with attachment context and temp paths. The
        #: durable inbound spool quotes the spooled text back to the
        #: user in a restart notice, and the ingested form would quote on-disk
        #: paths to files that do not survive the restart. Same keying and lifetime as the
        #: three tables above.
        self.pending_original: dict[int, tuple[str, int]] = {}
        client.on_message = self.receive

    # -- Tier-1 core ---------------------------------------------------

    def reconfigure(self, section: Any) -> None:
        """Adopt a reloaded ``whatsapp`` section's authorization fields.

        Called by the dispatcher's config applier when ``config.json`` changes
        under ``whatsapp``, so a policy, allow-list or group-rule edit from the
        dashboard, the CLI or ``$EDITOR`` takes effect on the next stanza instead
        of the next restart. Each holder is REPLACED wholesale (never mutated) so
        an in-flight decision keeps reading one consistent value: the frozen DM
        allow-list, and the ``GroupGate`` -- rebuilt from the same
        ``_coerce_whatsapp_groups`` pass the loader applies, which is what makes
        an unknown ``mode`` fall back to ``mention`` rather than to a mode that
        speaks unprompted.

        Fails closed on shape: a ``dm_policy`` that is not a string, or an
        allow-list that is not a list, keeps the PREVIOUS value and logs at
        WARNING. An unknown policy STRING is adopted, because ``_dm_policy``
        denies everyone it does not recognize -- refusing to adopt it would leave
        the wider previous policy in force. Policy flips and roster changes are
        SEL-audited; a wa_id is a phone number, so the audit counts, never lists.

        Rebuilding the gate resets its unprompted-reply cooldown clock, which is
        the conservative direction only for the cooldown's purpose (rate limiting
        the agent's own speech), so the gate is rebuilt only when the coerced
        rules actually differ.

        ``whatsapp.db_path`` is not reloadable at all: the device store is opened
        once at connect and holds the account's credential.
        """
        policy = getattr(section, "dm_policy", None)
        if not isinstance(policy, str):
            logger.warning(
                "whatsapp: dm_policy is not a string in the reloaded config; keeping %r",
                self._dm_policy,
            )
        else:
            new_policy = policy.strip().lower()
            if new_policy != self._dm_policy:
                logger.warning("whatsapp: dm_policy reloaded %r -> %r", self._dm_policy, new_policy)
                self._dm_policy = new_policy
                sel().log_api_access(
                    caller="config",
                    operation="whatsapp_transport.reconfigure",
                    outcome="dm_policy_changed",
                    source="whatsapp",
                    resources=f"policy={new_policy}",
                )

        wa_ids = getattr(section, "allowed_wa_ids", None)
        if not isinstance(wa_ids, list):
            logger.warning(
                "whatsapp: allowed_wa_ids is not a list in the reloaded config; keeping the "
                "previous allow-list (%d entry/entries)",
                len(self._allowed),
            )
        else:
            new_allowed = frozenset(
                normalize_jid(wa_id_to_user_jid(w)) for w in wa_ids if str(w).strip()
            )
            if new_allowed != self._allowed:
                added = len(new_allowed - self._allowed)
                removed = len(self._allowed - new_allowed)
                self._allowed = new_allowed
                logger.info("whatsapp: allow-list reloaded (+%d/-%d entry/entries)", added, removed)
                sel().log_api_access(
                    caller="config",
                    operation="whatsapp_transport.reconfigure",
                    outcome="allow_list_changed",
                    source="whatsapp",
                    resources=f"added={added} removed={removed} size={len(new_allowed)}",
                )

        groups = _coerce_whatsapp_groups(getattr(section, "groups", None))
        if groups != self._group_rules:
            self._group_rules = groups
            self.group_gate = GroupGate(groups)
            logger.info("whatsapp: group rules reloaded (%d configured group(s))", len(groups))
            sel().log_api_access(
                caller="config",
                operation="whatsapp_transport.reconfigure",
                outcome="group_rules_changed",
                source="whatsapp",
                resources=f"groups={len(groups)}",
            )

    async def send_message(
        self, conversation_id: str, content: str, thread_id: str | None = None
    ) -> str:
        """Chunked send. Every id is remembered for echo discipline the moment
        its chunk lands, via the ``on_sent`` callback, so an echo arriving during
        the inter-chunk delay cannot race the tracker.

        An identical text sent to the same chat within
        :data:`_DUPLICATE_SEND_WINDOW_S` is suppressed: a retried or re-rendered
        reply must not reach the reader twice.
        """
        jid = normalize_jid(conversation_id)
        key = (jid, content)
        now = self._clock()
        previous = self._recent_sends.get(key)
        if previous is not None and now - previous[0] < _DUPLICATE_SEND_WINDOW_S:
            logger.warning(
                "whatsapp: suppressed duplicate send to %s (identical text %.1fs ago)",
                jid,
                now - previous[0],
            )
            return previous[1]
        last_id = ""
        for message_id in await self._send_tracked(jid, content):
            last_id = message_id
        if last_id:
            # Archive the agent's own reply too. The mirror's inbound tap cannot
            # see what this process sends, and without this row the wake-word
            # history would show the operator's questions and never the answers.
            self._archive_outbound(last_id, jid, body=content, kind="text")
        self._recent_sends[key] = (now, last_id)
        if len(self._recent_sends) > 128:
            cutoff = now - max(_DUPLICATE_SEND_WINDOW_S, 60.0)
            self._recent_sends = {
                k: v for k, v in self._recent_sends.items() if v[0] >= cutoff
            }
        return last_id

    def _archive_outbound(self, message_id: str, jid: str, *, body: str, kind: str) -> None:
        """Record a message this transport sent, keeping both sides of the chat.

        The mirror's inbound tap never sees what this process sends, so a reply
        or an attachment would be missing from the wake-word history without
        this row. History must never fail a send, hence the swallowed error.
        """
        if not message_id:
            return
        try:
            self.mirror.submit(
                message_id=message_id,
                chat=jid,
                sender=jid_user(self._client.me.jid),
                from_me=True,
                ts=int(self._clock()),
                body=body,
                kind=kind,
                is_group=is_group_jid(jid),
            )
        except Exception:  # noqa: BLE001 - history must not fail a send
            logger.debug("whatsapp: could not archive the sent %s", kind, exc_info=True)

    def _remember_sent(self, conversation_jid: str, message_id: str) -> None:
        """Track one outbound id for echo suppression and the reply gate."""
        self.echo.remember(conversation_jid, message_id)
        self.bot_sent.remember(conversation_jid, message_id)

    async def _send_tracked(self, jid: str, content: str) -> list[str]:
        return await self._client.send_text(
            jid, content, on_sent=lambda message_id: self._remember_sent(jid, message_id)
        )

    async def send_image(self, conversation_id: str, data: bytes, caption: str = "") -> str:
        """Upload one image, echo-tracked like every other send this makes."""
        jid = normalize_jid(conversation_id)
        mid = await self._client.send_image_bytes(
            jid, data, caption, on_sent=lambda message_id: self._remember_sent(jid, message_id)
        )
        self._archive_outbound(mid, jid, body=caption, kind="image")
        return mid

    async def send_document(
        self,
        conversation_id: str,
        file: OutboundFile,
        *,
        caption: str = "",
        thread_id: str | None = None,
    ) -> str:
        """Upload one file as a WhatsApp document, echo-tracked.

        The transport-level upload verb, with the same shape as Telegram's and
        Discord's: the caller passes the validated :class:`OutboundFile`, never
        a path to re-open, and ``file_send`` reaches it through
        ``upload_destination.resolve_channel``. ``thread_id`` exists for
        cross-transport parity and is unused because a WhatsApp conversation is
        one flat chat.

        The name that travels is the BASENAME only (the client enforces it):
        the caller's path is a real path on the operator's machine and must not
        reach the recipient.
        """
        jid = normalize_jid(conversation_id)
        name = Path(file.path).name
        mid = await self._client.send_document_bytes(
            jid,
            file.data,
            name,
            mimetype=file.mime,
            caption=caption or file.alt,
            on_sent=lambda message_id: self._remember_sent(jid, message_id),
        )
        note = (caption or file.alt or "").strip()
        self._archive_outbound(
            mid,
            jid,
            body=f"{note} ({name})" if note else name,
            kind="document",
        )
        return mid

    async def react(
        self, conversation_id: str, sender_id: str, message_id: str, emoji: str
    ) -> None:
        """React to a message, echo-tracked.

        A reaction is a MESSAGE on this protocol, so it comes back on the event
        stream with ``from_me`` set like anything else the account sends. Tracking
        its id is what stops the channel answering its own receipt.
        """
        jid = normalize_jid(conversation_id)
        sent_id = await self._client.react(jid, sender_id, message_id, emoji)
        if sent_id:
            self._remember_sent(jid, sent_id)

    async def resolve_conversation(self, user_id: str) -> str:
        return normalize_jid(wa_id_to_user_jid(user_id))

    async def fetch_history(
        self, conversation_id: str, thread_id: str | None = None
    ) -> list[InboundMessage]:
        return []  # sessions persist channel-side; no history replay

    # -- Lifecycle -------------------------------------------------------

    async def connect(self) -> None:
        await self._client.connect()

    async def disconnect(self) -> None:
        await self._client.disconnect()

    # -- Authorization ---------------------------------------------------

    def _may_fetch_media(self, msg: InboundMessage, *, is_group: bool) -> bool:
        """Whether this sender may cause bytes to be downloaded onto the host.

        A DM sender has already passed :meth:`authorize`, so the DM policy is the
        operator's own answer there and this adds nothing. A GROUP member is
        judged by :meth:`_group_sender_admitted` in step 5, and this asks the same
        question again at the one point where bytes land on the host: the group
        gate alone would let anyone in a configured group trigger an authenticated
        whole-blob fetch into the gateway's heap at will, and in ``rules`` mode an
        unaddressed message already returns ``respond=True`` with no cooldown
        bounding it (the cooldown starts once a reply actually delivered, which a
        sentinel-silenced turn never does). Re-asking here keeps the fetch closed
        even if a future caller reaches it without passing step 5.

        Group media therefore requires INDIVIDUAL admission: the linked account,
        or a number the operator listed. It deliberately does NOT consult
        ``dm_policy``, because ``open`` resolves to "anyone with a user id" and
        would hand the capability straight back. Being admitted to the
        conversation is not being admitted to the machine.
        """
        if not is_group:
            return True
        return self._group_sender_admitted(msg)

    def _group_sender_admitted(self, msg: InboundMessage) -> bool:
        """Per-sender admission for GROUP traffic: the linked account, or a
        number listed in ``allowed_wa_ids``. Deny by default.

        The group gate decides whether the agent may speak in the group; this
        decides whether the sender may make it speak. It is the same allowlist
        every other channel applies to group traffic, and it is independent of
        ``dm_policy`` on purpose: ``open`` means "anyone may DM", not "anyone in
        a group I configured may drive my agent". An empty allowlist therefore
        admits nobody but the operator, whatever the DM policy says.
        """
        sender_jid = wa_id_to_user_jid(msg.user_id)
        if self._client.me.matches(sender_jid):
            return True
        return normalize_jid(sender_jid) in self._allowed

    def may_send_to(
        self, conversation_id: str, thread_id: str | None = None, *, principal: str = ""
    ) -> bool:
        """Re-decide a PROACTIVE send under the live ``dm_policy``. Fails closed.

        Answerable because the conversation id IS the peer's normalized JID (see
        :meth:`resolve_conversation`), so a persisted link carries the same principal
        :meth:`authorize` checks. Asks the same question that method asks and answers
        it the same way, including denying an unrecognized policy rather than falling
        through to ``open``.

        ``self`` is the default policy and admits only the linked account, so a
        proactive send under it is authorized exactly when the conversation is that
        account's own thread.
        """
        if not conversation_id:
            return False
        jid = normalize_jid(conversation_id)
        if self._client.me.matches(jid):
            return True
        if self._dm_policy == DM_POLICY_OPEN:
            return True
        if self._dm_policy == DM_POLICY_ALLOWLIST:
            return jid in self._allowed
        # DM_POLICY_SELF reached here means a non-self conversation; DISABLED and any
        # unrecognized value deny outright.
        return False

    def authorize(self, msg: InboundMessage) -> bool:
        """Deny-by-default DM policy. ``self`` (default) admits only the
        linked account's own messages; unknown policy values deny everyone."""
        sender_jid = wa_id_to_user_jid(msg.user_id)
        is_self = self._client.me.matches(sender_jid)

        if self._dm_policy == DM_POLICY_SELF:
            allowed = is_self
        elif self._dm_policy == DM_POLICY_ALLOWLIST:
            allowed = is_self or normalize_jid(sender_jid) in self._allowed
        elif self._dm_policy == DM_POLICY_OPEN:
            allowed = bool(msg.user_id)
        elif self._dm_policy == DM_POLICY_DISABLED:
            allowed = False
        else:
            logger.warning(
                "whatsapp: unknown dm_policy %r denies everyone (fail closed)",
                self._dm_policy,
            )
            allowed = False

        if not allowed:
            sel().log_api_access(
                caller=msg.user_id or "unknown",
                operation="whatsapp_transport.authorize",
                outcome="denied",
                source="whatsapp",
            )
        return allowed

    def is_operator(self, msg: InboundMessage) -> bool:
        """Whether *msg* came from the linked account itself.

        The gate for everything that ACTS AS the agent rather than merely talks
        to it: resolving a tool approval, and steering the session with a
        command. Deliberately NARROWER than :meth:`authorize`, and not derived
        from ``dm_policy``: ``open`` admits a stranger to CHAT, and a configured
        group admits its members, but neither should reset the operator's
        session or authorize a command on their machine. "Who may talk to the
        agent" and "who may act as the agent" must not be the same set.

        The command case is reachable on shipped config, not hypothetical: with
        ``messaging.dm_scope = "unified"`` every direct DM collapses into one
        ``unified:{agent}`` bucket, so under ``allowlist`` or ``open`` a peer's
        ``/new`` would bump the generation on the conversation the OPERATOR is
        using.

        Reads the verdict :meth:`receive` already reached rather than re-deriving
        it. ``receive`` decides operator identity from the full multi-device
        picture -- ``IsFromMe`` plus both ``Sender`` and ``SenderAlt`` against
        the account's phone JID *and* its ``@lid`` alias -- while all that
        survives onto ``InboundMessage`` is a bare user part with its server
        dropped. Re-deriving from that string would answer a narrower question
        than the one already answered, and would answer it wrong for an operator
        addressed by an alias.

        Fails closed on an unknown message: an entry is recorded for the exact
        object ``receive`` dispatched, so anything else -- a replay, a synthetic
        message, a caller reaching past the gauntlet -- is not the operator.
        """
        return self.pending_operator.get(id(msg), False)

    LID_SUFFIX = f"@{LID_SERVER}"

    async def _canonical_sender(self, sender: str) -> str:
        """*sender* as a phone JID, resolving an ``@lid`` alias once per sender.

        WhatsApp multi-device addresses a sender either by their phone number
        or by their Linked Identity, and the two user parts are UNRELATED
        strings. Everything downstream that compares a sender against operator
        CONFIG -- the DM allowlist, and the audit trail that has to name the
        same human twice -- is written in phone numbers, so the alias is folded
        here, at the edge, rather than at each comparison. Doing it per
        comparison is how one call site gets the resolution and the next does
        not.

        A failed lookup returns *sender* unchanged, so the allowlist still
        fails CLOSED on an unresolvable alias.
        """
        if not sender.endswith(self.LID_SUFFIX):
            return sender
        cached = self._alias.get(sender)
        if cached is not None:
            self._alias.move_to_end(sender)
            return cached or sender
        phone = normalize_jid(await self._client.phone_for_lid(sender))
        self._alias[sender] = phone
        while len(self._alias) > _ALIAS_CACHE_MAX:
            self._alias.popitem(last=False)
        return phone or sender

    # -- Inbound adapter ---------------------------------------------------

    async def receive(self, raw_envelope: Any) -> None:
        """Normalize one neonize MessageEv through the gauntlet (module doc)."""
        info = getattr(raw_envelope, "Info", None)
        source = getattr(info, "MessageSource", None) if info is not None else None
        if info is None or source is None:
            return

        # Unwrap first: an ephemeral, view-once or edited message carries the
        # real one inside, and every read below (text, media, and the mention
        # context) is blind to it otherwise.
        inner = unwrap_message(getattr(raw_envelope, "Message", None))
        desc = describe(inner)
        text = desc.caption
        skipped_note = unsupported_note(desc)
        if not text.strip() and not desc.has_media and not skipped_note:
            return  # a system event with nothing a turn could act on

        chat = normalize_jid(jid_to_str(getattr(source, "Chat", None)))
        sender = normalize_jid(jid_to_str(getattr(source, "Sender", None)))
        sender_alt = normalize_jid(jid_to_str(getattr(source, "SenderAlt", None)))
        message_id = str(getattr(info, "ID", "") or "")
        from_me = bool(getattr(source, "IsFromMe", False))
        is_group = bool(getattr(source, "IsGroup", False)) or is_group_jid(chat)
        if not chat or not message_id:
            return

        # 2. Reconnect-replay flood gate.
        stamp = float(getattr(info, "Timestamp", 0) or 0)
        connected_at = self._client.connected_at
        if connected_at is not None and stamp and stamp < connected_at - _REPLAY_GRACE_S:
            logger.debug("whatsapp: dropping replayed history message %s", message_id)
            return

        # 2b. Archive tap: everything that reached this device, before any
        #     admission decision. Skips nothing but replays above.
        #     raw_envelope enables the media worker to download voice/images.
        self.mirror.submit(
            message_id=message_id,
            chat=chat,
            sender=jid_user(sender),
            from_me=from_me,
            ts=int(stamp),
            body=text,
            kind=getattr(desc, "kind", ""),
            is_group=is_group,
            push_name=str(getattr(info, "PushName", "") or ""),
            sender_name=str(getattr(info, "PushName", "") or ""),
            raw_envelope=raw_envelope,
        )

        # 3. Polls are archived by the collector but never dispatched. A poll
        #    is not a request to the agent; answering its arrival caused the
        #    "polls are not supported" turns. Read it later from wa.db when the
        #    operator explicitly asks.
        if desc.kind == KIND_POLL:
            logger.info("whatsapp: poll %s in %s archived and skipped", message_id, chat)
            return

        # 3. Echo gate.
        if from_me and self.echo.is_own_echo(chat, message_id):
            return

        # 3b. The operator talking to somebody else. A ``from_me`` message in a
        #     direct chat that is not the self-chat is the operator texting a
        #     contact, not addressing the agent: answering it would put the agent
        #     into their private conversation and reply in that contact's chat.
        #     Groups are exempt: a configured group is where the operator does
        #     address the agent, gated by mention or rules.
        is_group_early = bool(getattr(source, "IsGroup", False)) or is_group_jid(chat)

        # Musone wake: the operator's OWN message beginning with the wake word
        # addresses the agent even in a contact DM or in a group that is not in
        # `whatsapp.groups`. Only the operator (from_me) can trigger this.
        # Voice notes arrive with `text == ""`, so their trigger is decided by
        # the archive worker once the transcript exists (`_on_voice_wake`).
        musone_trigger = from_me and musone_wake(text)

        # A reply/quote carries its target only in contextInfo.stanzaID. Fetch
        # that exact archived row into the prompt: otherwise "trascrivi" sees a
        # recent chat full of voice notes and can transcribe the wrong one.
        cited_id = ""
        extended = getattr(inner, "extendedTextMessage", None)
        ctx = getattr(extended, "contextInfo", None) if extended is not None else None
        if ctx is not None:
            cited_id = str(getattr(ctx, "stanzaID", "") or "").strip()
        quoted = ""
        if cited_id:
            quoted = await asyncio.to_thread(
                quoted_message_context, chat, cited_id
            )

        # A wake-word turn in a contact chat or an unconfigured group has none of
        # that chat's recent messages in its session: the transport drops them.
        # Load the archive's recent history so the agent answers with the
        # conversation in view instead of only the wake word. The typed text is
        # kept separately for the durable spool; the model gets the block.
        parts: list[str] = []
        if quoted:
            parts.append(quoted)
        if musone_trigger:
            context = await asyncio.to_thread(recent_chat_context, chat, message_id)
            if context:
                parts.append(context)
            parts.append(f"[Messaggio attuale]\n{text}")
        else:
            parts.append(text)
        model_text = "\n\n".join(part for part in parts if part)

        if from_me and not is_group_early and not self._client.me.matches(chat):
            if musone_trigger:
                logger.info("whatsapp: musone trigger in contact chat %s", chat)
                # Allow through as a command to the agent
            else:
                logger.debug("whatsapp: own outgoing message to %s is not a command", chat)
                return

        # 4. Group gate (before authorize: an unconfigured group must not
        #    even produce an audit row per message).
        verdict: GroupVerdict | None = None
        # ``from_me`` means the account sent it, which includes the operator
        # texting an ordinary contact from their phone. That is NOT a command: the
        # agent would answer their private conversation and reply into the
        # contact's chat. Operator authority in a DIRECT chat therefore requires
        # the self-chat, which is the command surface; in a configured group the
        # operator addresses the agent normally, gated by mention or rules.
        self_chat = self._client.me.matches(chat)
        # Musone trigger makes the operator an operator even in contact chats
        sender_is_operator = (from_me or self._client.me.matches(sender, sender_alt)) and (
            is_group or self_chat or musone_trigger
        )
        # Musone trigger counts as "addressed" to the agent
        # The operator's ordinary message in a group is NOT a summons: a group
        # is a room full of people, and "I wrote something" is not "agent,
        # answer". Only the wake word, an @mention of the linked account, a
        # quote of its message, or an explicit slash command count.
        is_addressed = (
            self._is_addressed(inner, chat)
            or musone_trigger
            or (sender_is_operator and bool(parse_command(text)))
        )

        if is_group:
            if musone_trigger:
                # An explicit summons by the operator outranks the group roster:
                # a group absent from `whatsapp.groups` (or one configured as
                # `off`) still answers it. The per-sender admission below is
                # unchanged, so only the operator can wake the agent this way.
                verdict = GroupVerdict(respond=True, may_steer=True)
            else:
                verdict = self.group_gate.evaluate(
                    chat,
                    sender_is_operator=sender_is_operator,
                    addressed=is_addressed,
                )
                if not verdict.respond:
                    logger.debug("whatsapp: group %s drop (%s)", chat, verdict.reason)
                    return

        # user_id: the operator's own commands attribute to the operator
        # (self-chat + fromMe in any chat); group members keep their number.
        # A peer addressed by their ``@lid`` alias is folded to the phone JID
        # first, because the DM allowlist is written in phone numbers and an
        # unresolved alias reads as "this person is not allowed".
        if from_me:
            attributed = self._client.me.jid
        else:
            attributed = await self._canonical_sender(sender)
        user_id = jid_user(attributed)
        # Musone trigger counts as a mention/addressed
        is_mention = bool(verdict and not verdict.unprompted and is_group) or musone_trigger
        msg = InboundMessage(
            channel_type="whatsapp",
            user_id=user_id,
            conversation_id=chat,
            text=model_text,
            is_mention=is_mention,
            from_me=from_me,
        )
        logger.warning("whatsapp: dispatching message id=%s from_me=%s musone=%s chat=%s", message_id, from_me, musone_trigger, chat)

        # 5. Authorize. A DM is judged by dm_policy. A group message is judged
        #    by the per-sender allowlist (operator, or a listed number): the
        #    group gate above only says the agent MAY speak here, it does not say
        #    this PERSON may make it speak. Without this step every member of a
        #    configured group who @-mentions, quotes or (in rules mode) merely
        #    posts would drive a turn on the operator's host. Denied senders are
        #    dropped silently with a SEL row, like every other channel.
        if is_group:
            if not self._group_sender_admitted(msg):
                sel().log_api_access(
                    caller=msg.user_id or "unknown",
                    operation="whatsapp_transport.authorize_group",
                    outcome="denied",
                    source="whatsapp",
                )
                logger.debug("whatsapp: group %s drop (sender not allowlisted)", chat)
                return
            if verdict is not None and not verdict.may_steer:
                if parse_command(text):
                    return  # commands from non-operators die silently
        elif not self.authorize(msg):
            return

        # The GOVERNANCE gate, and it has to be here rather than only in the
        # dispatcher: the fetch below is an authenticated download onto the
        # operator's host, and the dispatcher's own check runs after this method
        # has already performed it. A `channels` deny added while the transport is
        # live must stop the download, not merely the reply.
        #
        # `has_media` comes from the ENVELOPE, which is why the cancellation
        # exemption cannot be fooled by a caption: a policy-denied media message
        # captioned `/stop` is still media, so it stays gated and nothing is
        # fetched. Reading `msg.attachments` here instead would exempt it, because
        # nothing has been ingested yet at this point in the method.
        if not await inbound_permitted("whatsapp", text=text, has_attachments=desc.has_media):
            return

        # Media is fetched only now, AFTER the group gate, authorize and the
        # governance gate. Doing it earlier would let any stranger who can message
        # the number trigger an authenticated download on the operator's host,
        # which is a remote-triggered fetch with no authorization behind it.
        temp_paths: list[str] = []
        # The user's own text and media count, before either is rewritten below.
        self.pending_original[id(msg)] = (text, 1 if desc.has_media else 0)
        if desc.has_media and not self._may_fetch_media(msg, is_group=is_group):
            # Refusing is said out loud: silence reads as the agent ignoring a
            # photo the sender believes it received.
            skipped_note = NOTE_MEDIA_NOT_ADMITTED
        elif desc.has_media:
            result = await ingest_media(self._client, inner, desc, message_id)
            temp_paths = list(result.temp_paths)
            msg.text = append_attachment_context(msg.text, result)
            # Populated because downstream readers gate on it (the shared
            # cancellation exemption among them), and a field that is always empty
            # is a guarantee that silently does not hold.
            msg.attachments = list(temp_paths)
        elif skipped_note:
            # Seen and skipped, said out loud: silence reads as the agent
            # ignoring a photo the user believes it received.
            msg.text = f"{msg.text}\n\n{skipped_note}".strip() if msg.text else skipped_note

        if verdict is not None:
            self.pending_verdicts[id(msg)] = verdict
        self.pending_operator[id(msg)] = sender_is_operator
        self.pending_message_id[id(msg)] = message_id
        try:
            # Received from a person: its start is FOREGROUND (kiro_crew.start_priority).
            msg.person_origin = True
            await self._dispatch(msg)
        finally:
            self.pending_verdicts.pop(id(msg), None)
            self.pending_operator.pop(id(msg), None)
            self.pending_message_id.pop(id(msg), None)
            self.pending_original.pop(id(msg), None)
            # The shared ingest hands path ownership to the caller. Unlinked in a
            # worker, last, so a cancel arriving during the await cannot skip the
            # side-table pops above: TMPDIR is not guaranteed to be local disk.
            if temp_paths:
                try:
                    await asyncio.to_thread(cleanup, temp_paths)
                except RuntimeError:
                    # The loop refused the work (executor already shut down), so no
                    # worker owns it; deleting inline beats leaving decrypted media.
                    cleanup(temp_paths)

    def _is_addressed(self, message: Any, chat: str) -> bool:
        """Mentioned (@-tag of the linked account) or replying to the agent's
        own message.

        The operator's own message is deliberately NOT addressed merely because
        it is theirs: in a group the operator talks to the room like everyone
        else, and the agent answers only when called (`musone`, an @mention, or
        a quote of something it sent). A reply to the operator's OWN message is
        not a summons either, even though both addresses are the same linked
        account: only message ids recorded on an agent send count. The caller
        treats an explicit slash command as a summons; the self-chat never
        reaches the group gate.

        Takes the UNWRAPPED message, not the envelope: ``contextInfo`` carries the
        mention list, and it is invisible when the message arrived inside an
        ephemeral or view-once carrier. Reading the envelope made an @-mention in
        a disappearing group message read as "not addressed", so the agent stayed
        silent exactly when it was called on.
        """
        extended = getattr(message, "extendedTextMessage", None) if message else None
        ctx = getattr(extended, "contextInfo", None) if extended is not None else None
        if ctx is None:
            return False
        me = self._client.me
        for mentioned in getattr(ctx, "mentionedJID", []) or []:
            if me.matches(str(mentioned)):
                return True
        stanza_id = str(getattr(ctx, "stanzaID", "") or "")
        if stanza_id and chat and (
            self.echo.is_own_echo(chat, stanza_id)
            or self.bot_sent.contains(chat, stanza_id)
        ):
            return True  # quoted message is one the AGENT sent, not the operator
        return False

    async def _on_voice_wake(self, chat_id: str, transcript: str, message_id: str) -> None:
        """Dispatch the operator's own voice note when its transcript wakes the agent.

        A voice note carries no text at stanza time, so `receive` cannot see a
        spoken "musone" and the note is dropped in a contact DM or an
        unconfigured group before the transcript exists. The archive worker
        transcribes it seconds later and calls back here with the text.

        Only the linked account's own notes qualify (the worker checks
        `from_me`), and the self-chat is excluded: there the ordinary inbound
        path has already dispatched the note as media, and dispatching again
        would answer every self-chat voice twice.
        """
        if not musone_wake(transcript):
            return
        if self._client.me.matches(chat_id):
            return
        logger.info("whatsapp: musone wake from voice note in %s", chat_id)
        context = await asyncio.to_thread(recent_chat_context, chat_id, message_id)
        model_text = f"{context}\n\n[Messaggio attuale]\n{transcript}" if context else transcript
        msg = InboundMessage(
            channel_type="whatsapp",
            user_id=jid_user(self._client.me.jid),
            conversation_id=chat_id,
            text=model_text,
            is_mention=True,
            from_me=True,
        )
        msg.person_origin = True
        self.pending_message_id[id(msg)] = message_id
        self.pending_operator[id(msg)] = True
        self.pending_original[id(msg)] = (transcript, 0)
        if is_group_jid(chat_id):
            self.pending_verdicts[id(msg)] = GroupVerdict(respond=True, may_steer=True)
        try:
            await self._dispatch(msg)
        finally:
            self.pending_verdicts.pop(id(msg), None)
            self.pending_operator.pop(id(msg), None)
            self.pending_message_id.pop(id(msg), None)
            self.pending_original.pop(id(msg), None)

    # -- Configured outbound targets ----------------------------------------

    def configured_targets(self) -> list[ConfiguredChannelTarget]:
        targets: list[ConfiguredChannelTarget] = []
        available = self._client.is_connected
        reason = "" if available else "WhatsApp is not connected (pair from Settings)"
        me = self._client.me.wa_id
        if me:
            targets.append(
                ConfiguredChannelTarget(f"user:{me}", "WhatsApp · yourself", available, reason)
            )
        for jid in sorted(self._allowed):
            wa_id = jid_user(jid)
            targets.append(
                ConfiguredChannelTarget(
                    f"user:{wa_id}", f"WhatsApp DM · {wa_id}", available, reason
                )
            )
        for rule in self._group_rules:
            jid = normalize_jid(rule["jid"])
            label = rule.get("name") or jid
            targets.append(
                ConfiguredChannelTarget(
                    f"group:{jid}", f"WhatsApp group · {label}", available, reason
                )
            )
        return targets

    def _configured_groups(self) -> frozenset[str]:
        """Group JIDs the operator listed in ``whatsapp.groups``.

        A group is an explicit, reviewable allowlist entry like ``allowed_wa_ids``
        is for DMs; a JID that is not in this set is never a proactive target.
        """
        return frozenset(
            normalize_jid(rule["jid"])
            for rule in self._group_rules
            if str(rule.get("jid", "")).strip()
        )

    def _proactive_targets(self) -> frozenset[str]:
        """Conversation JIDs a CONFIGURED proactive send may address: the linked
        account, the DM allowlist, and the configured groups.

        Deliberately the same sources :meth:`configured_targets` lists from, so
        what the dashboard OFFERS and what it will ACCEPT cannot drift apart.
        """
        jids = set(self._allowed) | set(self._configured_groups())
        me = self._client.me.wa_id
        if me:
            jids.add(normalize_jid(wa_id_to_user_jid(me)))
        return frozenset(jids)

    async def resolve_configured_target(self, target_id: str) -> tuple[str, str | None] | None:
        raw = (target_id or "").strip()
        kind, sep, value = raw.partition(":")
        if kind == "group" and sep:
            jid = normalize_jid(value.strip())
            if not is_group_jid(jid) or jid not in self._configured_groups():
                return None
            return jid, None
        # A bare group JID is what the model has when it reads the chat, so accept
        # it beside the advertised ``group:<jid>``. Membership is still the
        # configured groups: this widens the spelling, not the allowlist.
        bare = normalize_jid(raw)
        if is_group_jid(bare):
            if bare not in self._configured_groups():
                return None
            return bare, None
        if kind != "user" or not sep or not value.strip():
            return None
        conversation = await self.resolve_conversation(value.strip())
        # Deny-by-default on membership, as every sibling transport does. This
        # resolver is the ONLY allowlist check on the dashboard mirror-link path,
        # which round-trips a chosen id back through it: resolving an id the list
        # never offered would let a proactive send open a conversation with an
        # arbitrary phone number, and `dm_policy` never sees that path.
        if conversation not in self._proactive_targets():
            return None
        return conversation, None
