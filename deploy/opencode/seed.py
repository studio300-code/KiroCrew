#!/usr/bin/env python3
"""First-run configuration for the KiroCrew + OpenCode appliance.

The image is clean: every personal value arrives from the compose environment.
This script runs before the gateway starts and writes:

  ~/.kiro/crew/config.json          — OpenCode backend, Nemotron model, channels
  ~/.config/opencode/opencode.jsonc — OpenCode's own default model
  ~/.local/share/opencode/auth.json — OpenRouter credential for OpenCode
  ~/.kiro/crew/.env                 — the gateway's credential file

It is idempotent. On a fresh volume it writes the full config; on an existing
volume it merges only the stack-managed keys (model, effort, roles, sandbox,
channel switches), preserving groups, skills, memory and every other setting.
Set KIROCREW_PRESERVE_AGENT_MODEL=1 to keep an operator's model choice.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

DEFAULT_MODEL = "openrouter/nvidia/nemotron-3-ultra-550b-a55b:free"
DEFAULT_FALLBACK = DEFAULT_MODEL
DEFAULT_EFFORT = "high"


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "")
    if not raw:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _ints(name: str) -> list[int]:
    out: list[int] = []
    for part in os.environ.get(name, "").replace(",", " ").split():
        try:
            value = int(part)
        except ValueError:
            continue
        if value > 0:
            out.append(value)
    return out


def _digits(name: str) -> list[str]:
    out: list[str] = []
    for part in os.environ.get(name, "").replace(",", " ").split():
        value = part.strip().lstrip("+")
        if value.isdigit():
            out.append(value)
    return out


def _read_json(path: pathlib.Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: pathlib.Path, data: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def _merge(base: dict, overlay: dict) -> None:
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge(base[key], value)
        else:
            base[key] = value


def _upsert_env(path: pathlib.Path, key: str, value: str) -> None:
    """Replace one KEY= line, preserving every other line and comment."""
    if "\n" in key or "\n" in value:
        raise ValueError("environment values must be single-line")
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if not line.startswith(f"{key}=")]
    lines.append(f"{key}={value}")
    tmp = path.with_name(path.name + f".tmp.{os.getpid()}")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _sandbox_posture() -> tuple[bool, bool]:
    """Return (probe_ok, allow_unsandboxed) for the first-run config."""
    try:
        from kiro_crew.sandbox import detect_backend

        backend = detect_backend()
    except Exception as exc:  # noqa: BLE001 - a broken probe must fail closed
        print(f"[seed] sandbox probe failed: {exc}", file=sys.stderr)
        backend = "none"
    probe_ok = backend != "none"
    allow = _bool("KIROCREW_ALLOW_UNSANDBOXED", False)
    print(f"[seed] sandbox backend={backend!r} allow_unsandboxed={allow}")
    return probe_ok, allow


def _seed_kirocrew() -> pathlib.Path:
    from kiro_crew.config.paths import ensure_data_home

    home = pathlib.Path(ensure_data_home())
    config_path = home / "config.json"
    first_run = not config_path.is_file()

    model = os.environ.get("KIROCREW_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    effort = os.environ.get("KIROCREW_REASONING_EFFORT", DEFAULT_EFFORT).strip() or DEFAULT_EFFORT
    fallback = os.environ.get("KIROCREW_FALLBACK_MODEL", DEFAULT_FALLBACK).strip() or DEFAULT_FALLBACK
    probe_ok, allow_unsandboxed = _sandbox_posture()

    agent: dict = {
        "acp_backend": "opencode",
        "member_acp_backend": "opencode",
        "model": model,
        "reasoning_effort": effort,
        "role_models": {"background": model, "subagent": model},
        "role_efforts": {"background": effort, "subagent": effort},
        "fallback_model": fallback,
        "approval_mode": os.environ.get("KIROCREW_APPROVAL_MODE", "auto"),
        "dangerously_skip_permissions": _bool("KIROCREW_YOLO", False),
        "sandbox": "auto",
        "sandbox_allow_unsandboxed_exec": (not probe_ok and allow_unsandboxed),
        "chat_turn_timeout_secs": 86400,
    }
    if not probe_ok and not allow_unsandboxed:
        # Leave the key out rather than writing false so the startup log names
        # the two ways to enable execution.
        agent.pop("sandbox_allow_unsandboxed_exec", None)

    overlay: dict = {
        "agent": agent,
        "knowledge": {"extraction_effort": effort},
        "auto_update": False,
        "telemetry": {"beacon_enabled": False},
        "stt": {
            "provider": os.environ.get("KIROCREW_STT_PROVIDER", "local"),
            "model": os.environ.get("KIROCREW_STT_MODEL", "small"),
            "language_code": os.environ.get("KIROCREW_STT_LANGUAGE", "it"),
            "polish": False,
        },
    }

    telegram_token = bool(os.environ.get("TELEGRAM_BOT_TOKEN", "").strip())
    telegram_ids = _ints("TELEGRAM_ALLOWED_USER_IDS")
    tg_raw = os.environ.get("KIROCREW_ENABLE_TELEGRAM")
    tg_forced_on = tg_raw is not None and tg_raw.strip().lower() not in {"", "0", "false", "no", "off"}
    tg_forced_off = tg_raw is not None and not tg_forced_on
    wa_raw = os.environ.get("KIROCREW_ENABLE_WHATSAPP")
    wa_enabled = True if wa_raw is None else wa_raw.strip().lower() not in {"0", "false", "no", "off"}
    wa_ids = _digits("WHATSAPP_ALLOWED_WA_IDS")

    if first_run:
        if telegram_token or telegram_ids or tg_forced_on:
            overlay["telegram"] = {"enabled": telegram_token or bool(telegram_ids) or tg_forced_on}
            if telegram_ids:
                overlay["telegram"]["allowed_user_ids"] = telegram_ids
        if wa_enabled:
            overlay["whatsapp"] = {"enabled": True, "dm_policy": "self"}
            if wa_ids:
                overlay["whatsapp"]["allowed_wa_ids"] = wa_ids
        skip_onboarding = _bool("KIROCREW_SKIP_ONBOARDING", True)
        overlay["dashboard"] = {
            "onboarded": skip_onboarding,
            "import_onboarded": skip_onboarding,
            "privacy_acked": skip_onboarding,
            "verbosity": "default",
        }
        public_url = os.environ.get("KIROCREW_PUBLIC_URL", "").strip()
        if public_url:
            overlay["dashboard"]["url"] = public_url
        data = overlay
        print(f"[seed] first run: writing {config_path}")
    else:
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            # Never replace an operator's config with defaults because a hand
            # edit made it temporarily unparseable.
            print(f"[seed] refusing to rewrite unparseable {config_path}: {exc}", file=sys.stderr)
            return home
        if not isinstance(data, dict):
            print(f"[seed] refusing to rewrite non-object {config_path}", file=sys.stderr)
            return home
        if os.environ.get("KIROCREW_PRESERVE_AGENT_MODEL", "").strip() in {"1", "true", "yes"}:
            overlay.get("agent", {}).pop("model", None)
            overlay.get("agent", {}).pop("fallback_model", None)
            overlay.get("agent", {}).pop("role_models", None)
            overlay.get("agent", {}).pop("reasoning_effort", None)
            overlay.get("agent", {}).pop("role_efforts", None)
        # Never re-enable a channel the operator disabled in the dashboard. An
        # explicit 0 in compose still turns it off; an explicit 1 leaves the
        # stored choice alone.
        if wa_raw is not None and not wa_enabled:
            overlay["whatsapp"] = {"enabled": False}
        if telegram_ids:
            overlay.setdefault("telegram", {})["allowed_user_ids"] = telegram_ids
        if tg_forced_on:
            overlay.setdefault("telegram", {})["enabled"] = True
        elif tg_forced_off:
            overlay.setdefault("telegram", {})["enabled"] = False
        public_url = os.environ.get("KIROCREW_PUBLIC_URL", "").strip()
        if public_url:
            overlay.setdefault("dashboard", {})["url"] = public_url
        _merge(data, overlay)
        print(f"[seed] existing config: merged stack keys into {config_path}")

    _write_json(config_path, data)
    return home


def _seed_opencode(home: pathlib.Path) -> None:
    model = os.environ.get("KIROCREW_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
    effort = os.environ.get("KIROCREW_REASONING_EFFORT", DEFAULT_EFFORT).strip() or DEFAULT_EFFORT
    model_ref = f"{model}#{effort}"

    config_home = pathlib.Path(os.environ.get("XDG_CONFIG_HOME", pathlib.Path.home() / ".config"))
    config_path = config_home / "opencode" / "opencode.jsonc"
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except ValueError as exc:
            print(f"[seed] leaving unparseable OpenCode config untouched: {exc}", file=sys.stderr)
            data = None
        if data is not None and not isinstance(data, dict):
            data = None
    else:
        data = {}
    if data is not None:
        data.setdefault("$schema", "https://opencode.ai/config.json")
        data["model"] = model_ref
        data.setdefault("share", "manual")
        data.setdefault(
            "permissions",
            [{"action": "*", "resource": "*", "effect": "allow"}],
        )
        _write_json(config_path, data)
        print(f"[seed] OpenCode config: {config_path} model={model_ref}")

    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if not key:
        return
    data_home = pathlib.Path(os.environ.get("XDG_DATA_HOME", pathlib.Path.home() / ".local" / "share"))
    auth_path = data_home / "opencode" / "auth.json"
    auth = _read_json(auth_path)
    auth["openrouter"] = {"type": "api", "key": key}
    _write_json(auth_path, auth)
    _upsert_env(home / ".env", "OPENROUTER_API_KEY", key)
    print("[seed] OpenCode auth: openrouter key stored")


def main() -> int:
    home = _seed_kirocrew()
    _seed_opencode(home)
    print(f"[seed] data home: {home}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
