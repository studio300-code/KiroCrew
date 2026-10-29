"""Projects the agent spec onto ``muse-acp``.

Muse reaches ACP through the community ``muse-acp`` adapter, which forwards the
session's ``mcpServers`` array to ``muse serve`` over its ``sessionMcp`` grant
(Muse 1.3.0 and newer). The mechanics are the ones the single-binary mirrors
already own: the shared translation, the ``tools`` allowlist, the registry filter,
the control-plane re-derivation and the one-owner rule for pooled broker stubs. So
this module delegates to :func:`kiro_crew.providers.mirrors.opencode.place_single_binary_array`
rather than restating them, and carries what is Muse's own: the rulings prose.

A server whose spec narrows it per tool is withheld WHOLE. The ACP element has no
per-tool disable slot, and Muse's approval flow is a host-side policy Crew can pin
to "ask" but cannot use to identify a switched-off tool at permission time.
"""

from __future__ import annotations

from typing import Any, Collection, Mapping

from kiro_crew.acp.session_mcp import session_mcp_projection
from kiro_crew.agent_sdk.backends import ACP_BACKEND_MUSE
from kiro_crew.providers.mirrors.base import AgentConfigMirror, Concern
from kiro_crew.providers.mirrors.base import Disposition as _D
from kiro_crew.providers.mirrors.base import Ruling, SessionProjection
from kiro_crew.providers.mirrors.opencode import place_single_binary_array

__all__ = ["MuseMirror", "muse_projection"]


def muse_projection(
    agent: str | None,
    *,
    stub_server_names: Collection[str] = (),
    stub_elements: Collection[Mapping[str, Any]] = (),
    work_dir: object = None,
    session_key: str = "",
    channel_id: str = "",
    session_token: str = "",
) -> SessionProjection:
    """The whole Muse array -- spec translation AND pooled stubs.

    Placed by the single-binary helper, for the same reason goose uses it: one
    owner for both halves means a stub appended after the projection withheld a
    name cannot re-add that server unrestricted. ``denied_tools`` stays empty
    because the restriction is honoured by withholding the whole server, not by a
    per-call refusal.

    Blocking (parses the agent spec once), so callers run it off the event loop.
    """
    projection = session_mcp_projection(
        agent,
        stub_server_names=stub_server_names,
        work_dir=work_dir,  # type: ignore[arg-type]
    )
    narrowed = frozenset(server for server, _tool in projection.disabled_tools)
    out = place_single_binary_array(
        projection,
        label="muse",
        unhonoured=narrowed,
        stub_elements=stub_elements,
        session_key=session_key,
        channel_id=channel_id,
        session_token=session_token,
    )
    return SessionProjection(
        params={"mcpServers": out},
        disabled_servers=projection.disabled_servers,
        restricted_servers=narrowed,
        unhonoured_servers=narrowed,
        zero_tools=projection.zero_tools,
        derived_spec_snapshot=projection.derived_spec_snapshot,
    )


class MuseMirror(AgentConfigMirror):
    """Projects the agent spec onto ``muse-acp``."""

    backend = ACP_BACKEND_MUSE

    def rulings(self) -> Mapping[Concern, Ruling]:
        return {
            Concern.MCP_SERVERS: Ruling(
                _D.DELIVERED,
                "the session/new + session/load mcpServers array, translated by "
                "acp.session_mcp.session_mcp_servers with no new translator. "
                "muse-acp accepts the array and forwards it to muse serve through "
                "the harness's sessionMcp grant (Muse 1.3.0+); the adapter drops an "
                "entry it cannot forward rather than failing the session. Crew's own "
                "control plane travels here because Muse reads no agent file of "
                "Crew's.",
            ),
            Concern.TOOL_ALLOWLIST: Ruling(
                _D.TRANSLATED,
                "into the allowlist that decides which servers enter the array -- the "
                "shared translation's own rule, not a second one here",
            ),
            Concern.DENIED_TOOLS: Ruling(
                _D.TRANSLATED,
                "by withholding the server it narrows, Crew's own control plane "
                "included, and declared as per_tool_deny=whole-server on the "
                "projection record. The ACP element has no per-tool disable slot, and "
                "Muse's permission frames carry no stable server/tool identity Crew "
                "can match at approval time, so a narrowed server mounted here would "
                "leave the switched-off tool callable",
            ),
            Concern.MODEL: Ruling(
                _D.DELIVERED,
                "as a session config option: muse-acp exposes the host's model "
                "catalog as the ``model`` select on session/new, and Crew captures "
                "the advertised ids and switches through session/set_config_option",
            ),
            Concern.MODEL_ALLOWLIST: Ruling(
                _D.WITHHELD,
                "the harness advertises its own Muse catalog on session/new, and those "
                "ids are the only ones its model select accepts; a Crew-side list "
                "would offer ids the session cannot serve",
            ),
            Concern.AUTO_APPROVE: Ruling(
                _D.WITHHELD,
                "Muse's approval granularity is a session MODE, and the only mode Crew "
                "puts on the wire is ``promptUnmatched`` -- the one that sends every "
                "unmatched tool call to session/request_permission. Projecting an "
                "auto-approve value here would suppress the frames the host gate is "
                "reached through",
            ),
            Concern.PERMISSION_MODE: Ruling(
                _D.WITHHELD,
                "not projected as spec data. Crew applies the required approval mode "
                "through session/set_config_option and reads it back before the first "
                "prompt; a second copy here would give one guarantee two owners",
            ),
            Concern.PROMPT: Ruling(
                _D.WITHHELD,
                "no definition is projected in any shape; the session's instructions "
                "arrive as the turn's own text",
            ),
            Concern.RESOURCES: Ruling(
                _D.WITHHELD,
                "the array carries MCP servers rather than resources and the ACP "
                "session/new element set has no resources slot",
            ),
            Concern.HOOKS: Ruling(
                _D.NO_CHANNEL,
                "the spec's hooks block reaches kiro-cli and no other backend. "
                "muse-acp can read the HOST's installed hook rows through its "
                "optional _session/hooks extension, but the ACP session/new element "
                "set has no field that carries a per-agent hooks definition, so the "
                "spec block is dropped here",
                channel="a hooks field on the ACP session/new element set, or a "
                "Muse-side config projection Crew could write",
            ),
        }

    def session_params(
        self,
        agent: str | None,
        *,
        stub_server_names: Collection[str] = (),
        work_dir: object = None,
        session_key: str = "",
        channel_id: str = "",
        session_token: str = "",
        **kwargs: object,
    ) -> dict[str, object]:
        """The wire face: the ``mcpServers`` array for this Muse session."""
        stubs = kwargs.get("stub_elements") or ()
        return self.session_projection(
            agent,
            stub_server_names=stub_server_names,
            stub_elements=stubs if isinstance(stubs, (list, tuple)) else (),
            work_dir=work_dir,
            session_key=session_key,
            channel_id=channel_id,
            session_token=session_token,
        ).params

    def session_projection(
        self,
        agent: str | None,
        *,
        stub_server_names: Collection[str] = (),
        stub_elements: Collection[Mapping[str, Any]] = (),
        work_dir: object = None,
        session_key: str = "",
        channel_id: str = "",
        session_token: str = "",
        **kwargs: object,
    ) -> SessionProjection:
        """The structured face: :func:`muse_projection`, with ``kwargs`` ignored."""
        del kwargs
        return muse_projection(
            agent,
            stub_server_names=stub_server_names,
            stub_elements=stub_elements,
            work_dir=work_dir,
            session_key=session_key,
            channel_id=channel_id,
            session_token=session_token,
        )
