"""ModelRouter — one place that knows how to build an LLM client for a
given *role*, picking the right provider + model.

Why a router instead of just env vars?
---------------------------------------
code-sama-os has many distinct LLM jobs:

  • ``streamer``   — the chatty front-of-house (fast, warm model)
  • ``worker``     — the hands (strong coding model, expensive)
  • ``narrator``   — live commentary (small, cheap, fast)
  • ``harness``    — manifest generator (coding-capable)
  • ``vision``     — screenshot-grounded element resolver
  • ``planner``    — reserved for a future high-level planner

Each wants a different price/latency/intelligence trade-off, and you may
want to mix providers (OpenAI for vision, a local Ollama for narration,
OpenRouter for the worker). Hard-coding ``MODEL`` + ``PROXY_*`` env vars
collapses all of that into one knob.

The router reads ``models.yml`` (next to ``.env``) describing named
providers and a role→model mapping. If no file exists, it falls back to
the legacy ``PROXY_API_KEY`` / ``MODEL`` / ``STREAMER_MODEL`` /
``WORKER_MODEL`` env vars, so existing deployments keep working.

Example ``models.yml``::

    providers:
      openrouter:
        base_url: https://api.proxyapi.ru/openrouter/v1
        api_key: ${PROXY_API_KEY}        # interpolated from env
      openai:
        base_url: https://api.openai.com/v1
        api_key: ${OPENAI_API_KEY}
      ollama:
        base_url: http://localhost:11434/v1
        api_key: ollama                  # placeholder

    roles:
      streamer:  { provider: openrouter, model: qwen/qwen3-235b-a22b-2507, temperature: 0.55 }
      worker:    { provider: openrouter, model: anthropic/claude-sonnet-4.5, temperature: 0.2 }
      narrator:  { provider: ollama,     model: qwen2.5:7b, temperature: 0.6, max_tokens: 80 }
      harness:   { provider: openrouter, model: qwen/qwen3-235b-a22b-2507, temperature: 0.2 }
      vision:    { provider: openai,     model: gpt-4o, temperature: 0.1 }

The router caches :class:`ChatOpenAI` instances per (provider, model) so
repeated calls for the same role are free.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("code-sama-os.models")


# Fixed set of named roles the rest of the codebase asks for.
ROLES = (
    "streamer", "worker", "narrator", "harness",
    "vision", "planner", "embeddings",
)


@dataclass
class Provider:
    name: str
    base_url: str
    api_key: str
    auth_mode: str = ""
    refresh_token: str = ""


@dataclass
class RoleConfig:
    role: str
    provider: str
    model: str
    temperature: float = 0.4
    max_tokens: int | None = None


class ModelRouter:
    """Resolve a role to a configured :class:`ChatOpenAI` instance."""

    def __init__(self, config_path: str | Path | None = None) -> None:
        self.config_path = Path(config_path) if config_path else _default_config_path()
        self._providers: dict[str, Provider] = {}
        self._roles: dict[str, RoleConfig] = {}
        self._cache: dict[tuple[str, str], Any] = {}
        self._loaded = False

    # ─── loading ──────────────────────────────────────────────────────
    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        if self.config_path.exists():
            try:
                self._load_from_file()
                log.info(
                    "ModelRouter: loaded %d provider(s), %d role(s) from %s",
                    len(self._providers), len(self._roles), self.config_path,
                )
                return
            except Exception:
                log.exception("ModelRouter: failed to load %s — using env fallback", self.config_path)
        self._load_from_env()

    def _load_from_file(self) -> None:
        raw = self.config_path.read_text(encoding="utf-8")
        try:
            import yaml  # type: ignore
            data = yaml.safe_load(raw) or {}
        except ImportError:
            # Fallback: maybe it's JSON despite the .yml name.
            import json
            data = json.loads(raw)
        for name, spec in (data.get("providers") or {}).items():
            spec = spec or {}
            self._providers[name] = Provider(
                name=name,
                base_url=_interpolate(str(spec.get("base_url") or "")),
                api_key=_interpolate(str(spec.get("api_key") or "")),
            )
        for role, spec in (data.get("roles") or {}).items():
            spec = spec or {}
            provider = str(spec.get("provider") or "")
            model = str(spec.get("model") or "")
            if not provider or not model:
                log.warning("ModelRouter: role %s missing provider/model — skipping", role)
                continue
            self._roles[role] = RoleConfig(
                role=role,
                provider=provider,
                model=model,
                temperature=float(spec.get("temperature", 0.4)),
                max_tokens=spec.get("max_tokens"),
            )

    def _load_from_env(self) -> None:
        """Legacy fallback: a single proxy provider + per-role model env vars.

        Kept so existing ``.env`` files keep working without a
        ``models.yml``. The mapping mirrors the old direct env reads in
        ``agents.py`` / ``harness.py``.
        """
        api_key = os.environ.get("PROXY_API_KEY", "")
        base_url = os.environ.get(
            "PROXY_BASE_URL", "https://api.proxyapi.ru/openrouter/v1"
        )
        self._providers["default"] = Provider("default", base_url, api_key)
        default_model = os.environ.get("MODEL", "qwen/qwen3-235b-a22b-2507")
        role_models = {
            "streamer": os.environ.get("STREAMER_MODEL"),
            "worker": os.environ.get("WORKER_MODEL"),
            "harness": os.environ.get("HARNESS_MODEL"),
            "vision": os.environ.get("VISION_MODEL"),
            "narrator": os.environ.get("NARRATOR_MODEL"),
        }
        for role in ROLES:
            model = role_models.get(role) or default_model
            temp = {"streamer": 0.55, "narrator": 0.6, "harness": 0.2,
                    "vision": 0.1, "planner": 0.3}.get(role, 0.4)
            max_tokens = 80 if role == "narrator" else None
            self._roles[role] = RoleConfig(
                role=role, provider="default", model=model,
                temperature=temp, max_tokens=max_tokens,
            )

    # ─── introspection ───────────────────────────────────────────────
    def get_role(self, role: str) -> RoleConfig:
        self._ensure_loaded()
        # Fall back to the worker config for unknown roles — safer than
        # crashing. Callers that care should check `has_role`.
        return self._roles.get(role) or self._roles.get("worker") or RoleConfig(
            role=role, provider="default",
            model=os.environ.get("MODEL", "qwen/qwen3-235b-a22b-2507"),
        )

    def has_role(self, role: str) -> bool:
        self._ensure_loaded()
        return role in self._roles

    def get_provider(self, name: str) -> Provider | None:
        self._ensure_loaded()
        return self._providers.get(name)

    def summary(self) -> dict[str, Any]:
        self._ensure_loaded()
        return {
            "providers": list(self._providers.keys()),
            "roles": {
                r: {"provider": rc.provider, "model": rc.model,
                    "temperature": rc.temperature,
                    "max_tokens": rc.max_tokens}
                for r, rc in self._roles.items()
            },
        }

    # ─── construction ────────────────────────────────────────────────
    def llm(self, role: str) -> Any:
        """Return a (cached) ChatOpenAI configured for ``role``."""
        self._ensure_loaded()
        rc = self.get_role(role)
        provider = self._providers.get(rc.provider)
        if provider is None:
            log.warning("ModelRouter: provider %r missing — falling back to 'default'", rc.provider)
            provider = self._providers.get("default") or Provider("default", "", "")
        key = (rc.provider, rc.model)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        from . import chatgpt_subscription as cg

        if cg.is_chatgpt_base(provider.base_url) or provider.auth_mode == "chatgpt":
            from .chatgpt_llm import ChatGPTSubscriptionChat

            settings = getattr(self, "_settings", None)

            def _token() -> str:
                if settings is not None:
                    return cg.ensure_fresh_access(settings.get, settings.update, provider.name)
                if provider.api_key:
                    return provider.api_key
                raise cg.ChatGPTReauthRequired("ChatGPT Subscription is not connected.")

            client = ChatGPTSubscriptionChat(
                token_fn=_token,
                model=rc.model,
                temperature=rc.temperature,
                provider_name=provider.name,
            )
            self._cache[key] = client
            return client

        from langchain_openai import ChatOpenAI
        kwargs: dict[str, Any] = {
            "model": rc.model,
            "api_key": provider.api_key or "missing",
            "base_url": provider.base_url,
            "temperature": rc.temperature,
        }
        if rc.max_tokens is not None:
            kwargs["max_tokens"] = rc.max_tokens
        try:
            client = ChatOpenAI(**kwargs)
        except Exception as exc:
            log.warning("ModelRouter: ChatOpenAI(%s) failed: %s", rc.model, exc)
            raise
        self._cache[key] = client
        return client

    def refresh(self) -> None:
        """Drop cached clients. Call after editing ``models.yml`` / settings."""
        self._cache.clear()
        self._loaded = False

    def apply_settings_llm(self, models_dict: dict[str, Any] | None) -> bool:
        """Hot-load providers/roles from a settings-derived dict.

        Returns True if the router was updated. Pass None to fall back to
        models.yml / env on the next ``llm()`` call.
        """
        if not models_dict:
            return False
        providers_in = models_dict.get("providers") or {}
        roles_in = models_dict.get("roles") or {}
        if not providers_in or not roles_in:
            return False
        providers: dict[str, Provider] = {}
        for name, spec in providers_in.items():
            spec = spec or {}
            providers[name] = Provider(
                name=name,
                base_url=str(spec.get("base_url") or ""),
                api_key=str(spec.get("api_key") or ""),
                auth_mode=str(spec.get("auth_mode") or ""),
                refresh_token=str(spec.get("refresh_token") or ""),
            )
        roles: dict[str, RoleConfig] = {}
        for role, spec in roles_in.items():
            spec = spec or {}
            provider = str(spec.get("provider") or "")
            model = str(spec.get("model") or "")
            if not provider or not model or provider not in providers:
                continue
            roles[role] = RoleConfig(
                role=role,
                provider=provider,
                model=model,
                temperature=float(spec.get("temperature", 0.4)),
                max_tokens=spec.get("max_tokens"),
            )
        if not roles:
            return False
        self._providers = providers
        self._roles = roles
        self._cache.clear()
        self._loaded = True
        log.info(
            "ModelRouter: applied settings — %d provider(s), %d role(s)",
            len(providers), len(roles),
        )
        return True


# ─── helpers ─────────────────────────────────────────────────────────────

def _default_config_path() -> Path:
    """Look for models.yml next to the project root (where .env lives)."""
    here = Path(__file__).resolve().parent.parent  # .../code-sama-os
    return here / "models.yml"


_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _interpolate(value: str) -> str:
    """Expand ``${VAR}`` references from the environment."""

    def _sub(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))

    return _VAR_RE.sub(_sub, value)


# A process-wide singleton. ``main.py`` creates it once and shares it
# with everything that needs an LLM. Tests can construct their own.
_default_router: ModelRouter | None = None


def get_default_router() -> ModelRouter:
    global _default_router
    if _default_router is None:
        _default_router = ModelRouter()
    return _default_router


def set_default_router(router: ModelRouter) -> None:
    global _default_router
    _default_router = router
