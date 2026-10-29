"""Muse Code, launched one process per session through the ``muse-acp`` adapter.

Muse has no ACP mode of its own; it serves the Muse Session Protocol (MSP) over
``muse serve``. ``muse-acp`` is a self-contained adapter binary that speaks ACP on
one side and MSP on the other, so it fills the same place in this tree that
``opencode acp`` and ``goose acp`` fill for their hosts: the binary comes off the
shared self-served ladder and there is no agent to resolve, no npm entry script and
no Node floor.

What stays this harness's own is the launch. Muse's asking posture is a session
config option (``approval_mode``) that the adapter advertises from the host, and
the shared ``SESSION_CONFIG`` routing applies ``promptUnmatched`` and reads it back
before the first prompt. Nothing is seeded into a file or an environment variable,
so this adapter has no read-back child of its own.
"""

from __future__ import annotations

from kiro_crew.acp import launch as launch_mod
from kiro_crew.acp.harness.base import ProcessAdapter, SpawnContext, SpawnPlan
from kiro_crew.agent_sdk.backends import ACP_BACKEND_MUSE

__all__ = ["MuseLaunch"]


class MuseLaunch(ProcessAdapter):
    """Muse Code behind ``muse-acp``: a self-served adapter with session routing."""

    backend = ACP_BACKEND_MUSE

    async def resolve_spawn(self, ctx: SpawnContext) -> SpawnPlan:
        """Resolve the adapter, warm the session's MCP array, then mask."""
        session = ctx.session
        assert session is not None, "muse is launched for one session"
        _muse_acp, argv, spawn_label, stderr_label = await launch_mod.resolve_self_served_launch(
            self.backend
        )
        # Translate the agent spec into this session's MCP array here, for the
        # reason the claude, opencode and goose adapters do it in their own
        # launches: the translation reads disk, and doing it at the shared
        # session/new call site would put an executor hop and a new failure mode
        # on EVERY backend's construction path (harness-parity H13).
        await session._prepare_session_mcp()
        # SESSION_CONFIG is an enforced routing, so the OS credential mask is the
        # compensating control for the passive reads ACP v1 cannot make Muse ask
        # about. This harness re-exposes no file from inside the mask, so no
        # expose list is resolved for it.
        hidden = await launch_mod._run_preflight_bounded(
            launch_mod._sandbox_preflight, self.backend, ctx.sandbox_mode
        )
        return SpawnPlan(
            argv=argv,
            spawn_label=spawn_label,
            stderr_label=stderr_label,
            extra_hidden_dirs=hidden,
        )

    def apply_spawn_env(self, env: dict[str, str], *, spawned_binary: str | None = None) -> None:
        """Nothing: Muse's approval posture travels as a session config option."""
