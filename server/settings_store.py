"""Persistent UI + LLM settings (settings.json next to .env).

Visual prefs (camera, wallpaper, voice, VRM) and LLM provider credentials
live here so the Settings tab can read/write them without editing .env by
hand. API keys are stored in plaintext on disk (same trust model as .env);
the HTTP API redacts them when returning the document to the browser.
"""

from __future__ import annotations

import copy
import json
import logging
import threading
from pathlib import Path
from typing import Any

from .providers import DEFAULT_VRM, default_settings

log = logging.getLogger("code-sama-os.settings")

_lock = threading.RLock()


class SettingsStore:
    def __init__(self, path: Path | None = None) -> None:
        root = Path(__file__).resolve().parent.parent
        self.path = path or (root / "settings.json")
        self._data: dict[str, Any] = default_settings()
        self.load()

    def load(self) -> dict[str, Any]:
        with _lock:
            if self.path.exists():
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                    self._data = _deep_merge(default_settings(), raw if isinstance(raw, dict) else {})
                except Exception:
                    log.exception("Failed to load %s — using defaults", self.path)
                    self._data = default_settings()
            else:
                self._data = default_settings()
            return copy.deepcopy(self._data)

    def save(self) -> None:
        with _lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            tmp.replace(self.path)

    def get(self) -> dict[str, Any]:
        with _lock:
            return copy.deepcopy(self._data)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Deep-merge ``patch`` into the stored document and persist."""
        with _lock:
            # Preserve existing API keys when the client sends a redacted stub.
            patch = copy.deepcopy(patch)
            llm = patch.get("llm")
            if isinstance(llm, dict):
                providers = llm.get("providers")
                if isinstance(providers, dict):
                    existing = self._data.get("llm", {}).get("providers", {})
                    for name, spec in providers.items():
                        if not isinstance(spec, dict):
                            continue
                        existing_spec = existing.get(name) or {}
                        key = spec.get("api_key")
                        if _is_redacted(key) and name in existing:
                            spec["api_key"] = existing_spec.get("api_key", "")
                        for tok in ("access_token", "refresh_token"):
                            val = spec.get(tok)
                            if _is_redacted(val) and existing_spec.get(tok):
                                spec[tok] = existing_spec.get(tok, "")
                            elif val is None and existing_spec.get(tok):
                                # Don't wipe tokens on partial patches.
                                spec[tok] = existing_spec[tok]
            self._data = _deep_merge(self._data, patch)
            self.save()
            return copy.deepcopy(self._data)

    def public_view(self) -> dict[str, Any]:
        """Copy suitable for GET /settings (API keys redacted)."""
        data = self.get()
        llm = data.get("llm") or {}
        providers = llm.get("providers") or {}
        for name, spec in list(providers.items()):
            if not isinstance(spec, dict):
                continue
            key = spec.get("api_key") or ""
            spec["api_key"] = _redact(key)
            spec["has_key"] = bool(key) and not _is_redacted(key)
            if spec.get("access_token"):
                spec["access_token"] = _redact(spec["access_token"])
            if spec.get("refresh_token"):
                spec["refresh_token"] = "••••••••"
                spec["has_key"] = True
            if (spec.get("auth_mode") or "") == "chatgpt" or "chatgpt.com/backend-api/codex" in (spec.get("base_url") or ""):
                spec["auth_mode"] = "chatgpt"
                spec["connected"] = bool(key) or bool(spec.get("has_key"))
        return data

    def to_models_yml_dict(self) -> dict[str, Any] | None:
        """Build a models.yml-compatible dict from llm settings, or None if empty."""
        llm = self.get().get("llm") or {}
        providers_in = llm.get("providers") or {}
        roles_in = llm.get("roles") or {}
        providers: dict[str, Any] = {}
        for name, spec in providers_in.items():
            if not isinstance(spec, dict):
                continue
            base = (spec.get("base_url") or "").strip()
            auth_mode = (spec.get("auth_mode") or "").strip()
            # Sentinel until device-flow completes — skip. After connect,
            # base_url is https://chatgpt.com/backend-api/codex.
            if not base or base in ("copilot",):
                continue
            if base == "chatgpt-subscription" and auth_mode != "chatgpt":
                continue
            if base == "chatgpt-subscription":
                base = "https://chatgpt.com/backend-api/codex"
            api_key = spec.get("access_token") or spec.get("api_key") or ""
            providers[name] = {
                "base_url": base,
                "api_key": api_key,
                "auth_mode": auth_mode or ("chatgpt" if "chatgpt.com/backend-api/codex" in base else ""),
                "refresh_token": spec.get("refresh_token") or "",
                "provider_name": name,
            }
        if not providers:
            return None
        # Prefer an explicit active provider for roles that have no provider set.
        active_p = (llm.get("activeProvider") or "").strip()
        active_m = (llm.get("activeModel") or "").strip()
        if active_p and active_p not in providers:
            active_p = next(iter(providers), "")
        roles: dict[str, Any] = {}
        for role, spec in roles_in.items():
            if not isinstance(spec, dict):
                continue
            p = (spec.get("provider") or active_p or "").strip()
            m = (spec.get("model") or active_m or "").strip()
            if not p or not m or p not in providers:
                continue
            entry: dict[str, Any] = {
                "provider": p,
                "model": m,
                "temperature": float(spec.get("temperature", 0.4)),
            }
            if spec.get("max_tokens") is not None:
                entry["max_tokens"] = spec["max_tokens"]
            roles[role] = entry
        if not roles and active_p and active_m and active_p in providers:
            # Seed all roles from the active route so the OS works after one click.
            for role, defaults in (default_settings()["llm"]["roles"]).items():
                roles[role] = {
                    "provider": active_p,
                    "model": active_m,
                    "temperature": defaults.get("temperature", 0.4),
                    **({"max_tokens": defaults["max_tokens"]} if "max_tokens" in defaults else {}),
                }
        if not roles:
            return None
        return {"providers": providers, "roles": roles}


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _redact(key: str) -> str:
    key = key or ""
    if not key:
        return ""
    if len(key) <= 8:
        return "••••••••"
    return f"{key[:3]}…{key[-4:]}"


def _is_redacted(key: Any) -> bool:
    if not isinstance(key, str) or not key:
        return False
    return "…" in key or "•" in key


# Hint for type checkers / imports
_ = DEFAULT_VRM
