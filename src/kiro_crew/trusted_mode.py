"""Single-operator trust switch.

This install has exactly one human operator, who already holds the OS account
the gateway runs as. Trusted mode is therefore the DEFAULT: the dashboard policy
floors are bypassed — the tool gate approves every call, the deny gate allows
every command, and any authenticated dashboard request is treated as a local one
(so remote settings writes are not read-only). Authentication itself is NOT
bypassed: the dashboard still requires a token/cookie.

Set ``KIROCREW_TRUSTED_SINGLE_USER=0`` (false/no/off) to restore the stock
security floors for a process without editing code.
"""

from __future__ import annotations

import os

_OFF = frozenset({"0", "false", "no", "off"})


def enabled() -> bool:
    """Whether this process runs in trusted single-user mode (default: yes)."""
    raw = os.environ.get("KIROCREW_TRUSTED_SINGLE_USER")
    if raw is None:
        return True
    return raw.strip().lower() not in _OFF
