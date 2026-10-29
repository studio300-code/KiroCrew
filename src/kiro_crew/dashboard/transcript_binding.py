"""Per-session state shared by every live slot on one transcript.

Two live slots are ALIASES when they resolve to one ``slot_history_key``. They then
also share one session (``session_key_for`` returns the shared ``linked_session_key``),
so one kiro-cli process and one working directory. A field that describes that session
cannot be held once per slot: a blank alias's save would write its own empty copy over
the peer's value on the shared transcript.

Such fields live in a :class:`TranscriptBinding`, and aliases hold the SAME binding
object. ``_ChatSlot.project`` reads and writes it through a property, so every
``slot.project = x`` already writes the shared cell and every ``slot.project is token``
rollback check reads it. A rollback therefore skips a peer's newer write (the identity
differs) and restores the whole group otherwise, with no per-peer rollback contract.

:func:`bind_transcript` is the one place a slot's binding is chosen. Call it after
anything that can change the slot's history key.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from kiro_crew.dashboard.state import _ChatSlot


class TranscriptBinding:
    """The cells a slot shares with every live alias on its transcript."""

    __slots__ = ("project",)

    def __init__(self, project: str = "") -> None:
        self.project = project


def bind_transcript(state: Any, slot: _ChatSlot) -> None:
    """Point *slot* at the binding its transcript's live aliases hold.

    * Another live slot on the same history key: adopt that slot's binding. The live
      peer wins, because it holds the value the shared session runs with.
    * No live peer, but the slot's binding is still held by a slot on another key (it
      just left an alias group): give it a private copy. It keeps its value and stops
      affecting the group it left.
    * Otherwise keep the binding.

    Membership is derived from ``state._slots`` on every call rather than kept in a
    registry, so a closed slot can never leave a stale group behind.
    """
    from kiro_crew.dashboard.chat_utils_slots import slot_history_key

    key = slot_history_key(slot)
    # Only real slots carry a binding; a slot-shaped double in ``_slots`` has none.
    live = [
        other
        for other in list(state._slots.values())
        if other is not slot and isinstance(getattr(other, "_binding", None), TranscriptBinding)
    ]
    for other in live:
        if slot_history_key(other) == key:
            slot._binding = other._binding
            # A reset armed for the shared session (a project change still waiting
            # on teardown) belongs to the joiner too: it adopts the new directory,
            # so without the flag its turns would reuse the process in the old one.
            pending = getattr(other, "_pending_reset_history_key", None)
            if pending and not slot._pending_reset_history_key:
                from kiro_crew.dashboard.chat_utils import effective_session_key

                if pending == effective_session_key(slot):
                    slot._pending_reset_history_key = pending
            return
    if any(other._binding is slot._binding for other in live):
        slot._binding = TranscriptBinding(project=slot._binding.project)


def _peers(state: Any, slot: Any) -> list[Any]:
    """Every other live slot that holds *slot*'s binding."""
    binding = getattr(slot, "_binding", None)
    slots = getattr(state, "_slots", None)
    if binding is None or not isinstance(slots, dict):
        return []
    return [
        other
        for other in list(slots.values())
        if other is not slot and getattr(other, "_binding", None) is binding
    ]


def binding_is_shared(state: Any, slot: Any) -> bool:
    """Whether a live alias already holds *slot*'s binding.

    A restore or a seed from disk must not write into a shared binding: the live
    peer holds the value the shared session runs with, which may be newer than disk.
    """
    return bool(_peers(state, slot))


def arm_project_reset(state: Any, slot: Any, key: str) -> None:
    """Arm the deferred session reset on *slot* and on every live alias.

    A project change on one alias is the group's change, and the aliases run one
    session. A peer whose own flag stayed clear would reuse the process in the old
    directory while its turn describes the new one. The reset is spent only on a real
    teardown, so a second consume after the first one costs at most a cold start.
    """
    slot._pending_reset_history_key = key
    for other in _peers(state, slot):
        other._pending_reset_history_key = key
