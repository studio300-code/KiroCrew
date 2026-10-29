"""A creator's approval-mode change reaches the sessions it already created.

``session_create`` copies the creator's posture at birth. The repro: a person
dispatches workers from a conductor, then turns the conductor to Trust, and every
worker must stop asking. These pin the follow-on rules: trusted -> trusted, normal -> normal, a lower creator never
raises a worker, a worker whose own mode a person set keeps it, a raise needs a
gateway-authored creation edge, and an app or cron caller can only lower.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from chat_test_helpers import _make_state
from session_control_helpers import (  # noqa: F401 -- the autouse fixtures register by import
    _agent_resolves,
    _enabled_fixture,
    _fresh_create_budget,
    _fresh_stop_windows,
    _key,
    _slot,
)

from kiro_crew.dashboard import session_control as sc
from kiro_crew.dashboard.approval_inheritance import plan_descendant_postures
from kiro_crew.dashboard.chat_handlers import api_chat_mode


@web.middleware
async def _owner(request: web.Request, handler):
    request["app"] = ""
    request["user"] = "local-app"
    return await handler(request)


def _create(state, caller):
    created = asyncio.run(sc.create_session(state, caller_session_key=_key(caller)))
    child = state.get_slot(created["target"])
    assert child is not None
    return child


def _set_mode(state, slot_key: str, mode: str) -> None:
    async def go() -> None:
        app = web.Application(middlewares=[_owner])
        app["state"] = state
        app.router.add_post("/api/chat/mode", api_chat_mode)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/mode", json={"mode": mode, "slot": slot_key})
            assert resp.status == 200, await resp.text()

    asyncio.run(go())


@pytest.fixture
def state(tmp_path):
    st = _make_state(tmp_path)
    st.broadcast_ws = MagicMock()
    st.push_slots_update = MagicMock()
    st.owner_id = ""
    return st


def test_repro_conductor_trusted_after_dispatch_trusts_its_workers(state):
    """The user report: trust set on the conductor after dispatch never reached a worker."""
    conductor = _slot(state, "chat-root")
    worker = _create(state, conductor)
    grandchild = _create(state, worker)
    assert worker._trust is False

    _set_mode(state, conductor.key, "trust")

    assert worker._trust is True
    assert grandchild._trust is True
    assert worker._trust_card_scoped is False


def test_trusted_creator_to_normal_returns_workers_to_asking(state):
    conductor = _slot(state, "chat-root")
    conductor._trust = True
    worker = _create(state, conductor)
    assert worker._trust is True  # born trusted, as before

    _set_mode(state, conductor.key, "normal")

    assert worker._trust is False and worker._trust_reads is False


def test_default_creator_leaves_workers_on_default(state):
    conductor = _slot(state, "chat-root")
    worker = _create(state, conductor)

    _set_mode(state, conductor.key, "normal")

    assert worker._trust is False and worker._trust_reads is False


def test_lower_creator_never_raises_a_worker(state):
    """Reads-only on the conductor gives a worker reads-only, and lowers a trusted one."""
    conductor = _slot(state, "chat-root")
    worker = _create(state, conductor)

    _set_mode(state, conductor.key, "trust_reads")
    assert worker._trust is False and worker._trust_reads is True

    _set_mode(state, conductor.key, "trust")
    _set_mode(state, conductor.key, "trust_reads")
    assert worker._trust is False and worker._trust_reads is True


def test_card_scoped_creator_hands_down_nothing(state):
    """A "Trust this crew member" card on the creator covers its own tools only."""
    conductor = _slot(state, "chat-root")
    worker = _create(state, conductor)
    conductor._trust = True
    conductor._trust_card_scoped = True

    assert plan_descendant_postures(state._slots.values(), conductor, may_raise=True) == []
    assert worker._trust is False


def test_a_workers_own_mode_is_an_explicit_override(state):
    """A person who set a worker's mode keeps it; that worker's children follow it."""
    conductor = _slot(state, "chat-root")
    worker = _create(state, conductor)
    grandchild = _create(state, worker)

    _set_mode(state, worker.key, "trust")
    _set_mode(state, conductor.key, "normal")
    assert worker._trust is True
    assert grandchild._trust is True

    _set_mode(state, worker.key, "normal")
    _set_mode(state, conductor.key, "trust")
    assert worker._trust is False
    assert grandchild._trust is False


def test_a_walk_from_above_never_re_hands_a_grant_set_below(state):
    """A trust an app set on a worker (which could not reach that worker's children)
    is not handed to them later by a person's change on the conductor."""
    conductor = _slot(state, "chat-root")
    worker = _create(state, conductor)
    grandchild = _create(state, worker)
    worker._trust = True  # as an app's slot-scoped trust leaves it
    worker._posture_inherited = False

    _set_mode(state, conductor.key, "normal")

    assert worker._trust is True
    assert grandchild._trust is False


def test_raise_needs_a_gateway_authored_creation_edge(state):
    """A ``created_by`` read back from an editable file is no way to collect trust."""
    conductor = _slot(state, "chat-root")
    claimer = _slot(state, "chat-claimer")
    claimer._created_by = conductor.key  # as a restored metadata line would set it
    claimer._posture_inherited = True
    assert claimer._lineage_minted is False

    _set_mode(state, conductor.key, "trust")
    assert claimer._trust is False

    # Lowering may follow any recorded edge: asking more is the safe direction.
    claimer._trust = True
    _set_mode(state, conductor.key, "normal")
    assert claimer._trust is False


def test_a_caller_that_may_not_raise_only_lowers(state):
    conductor = _slot(state, "chat-root")
    conductor._trust = True
    worker = _create(state, conductor)
    other = _create(state, _slot(state, "chat-other"))

    assert plan_descendant_postures(state._slots.values(), conductor, may_raise=False) == []
    conductor._trust = False
    plan = plan_descendant_postures(state._slots.values(), conductor, may_raise=False)
    assert [(c.slot, c.posture) for c in plan] == [(worker, "normal")]
    assert other not in [c.slot for c in plan]


def test_a_crewmate_profile_worker_is_left_to_its_profile(state):
    from kiro_crew.mate_worker import MATE_WORKER_AGENT

    conductor = _slot(state, "chat-root")
    worker = _create(state, conductor)
    worker.agent = MATE_WORKER_AGENT

    _set_mode(state, conductor.key, "trust")
    assert worker._trust is False


def test_trusting_the_creator_answers_its_workers_pending_prompts(state):
    conductor = _slot(state, "chat-root")
    worker = _create(state, conductor)

    async def go() -> str:
        fut = asyncio.get_running_loop().create_future()
        row = worker.append("permission", "Shell", '{"request_id": "r"}')
        worker.register_approval("r", fut, row)
        app = web.Application(middlewares=[_owner])
        app["state"] = state
        app.router.add_post("/api/chat/mode", api_chat_mode)
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/api/chat/mode", json={"mode": "trust", "slot": "chat-root"})
            assert resp.status == 200, await resp.text()
        return fut.result()

    assert asyncio.run(go()) == "approved"


def test_a_restored_worker_follows_its_conductor_on_a_recorded_edge(state, monkeypatch):
    """After a restart no mode a person set survives, so a restored worker follows
    its creator again; the raise rides on the crew log's creation record."""
    from types import SimpleNamespace

    from kiro_crew.crew_log import session_tree_projection
    from kiro_crew.dashboard.slot_persistence.metadata_codec import _read_created_by

    conductor = _slot(state, "chat-root")
    restored = _slot(state, "chat-restored")
    _read_created_by(SimpleNamespace(meta={"created_by": conductor.key}, slot=restored))
    assert restored._created_by == conductor.key and restored._posture_inherited is True
    assert restored._lineage_minted is False

    _set_mode(state, conductor.key, "trust")
    assert restored._trust is False  # no creation record: no raise

    monkeypatch.setattr(
        session_tree_projection,
        "creator_of",
        lambda key: conductor.key if key == restored.key else "",
    )
    _set_mode(state, conductor.key, "trust")
    assert restored._trust is True
