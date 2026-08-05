"""ChatGPT Subscription (Codex) OAuth + Responses API helpers.

Ported from Odysseus. Uses OpenAI account device authorization, stores
refresh tokens in settings.json, and resolves a fresh bearer at request time.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
import time
import uuid
from typing import Any

import httpx

log = logging.getLogger("code-sama-os.chatgpt")

DEFAULT_BASE_URL = "https://chatgpt.com/backend-api/codex"
PROVIDER_ID = "chatgpt-subscription"
OAUTH_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OAUTH_TOKEN_URL = "https://auth.openai.com/oauth/token"
OAUTH_ISSUER = "https://auth.openai.com"
OAUTH_REDIRECT_URI = f"{OAUTH_ISSUER}/deviceauth/callback"
ACCESS_TOKEN_REFRESH_SKEW = 120

_pending: dict[str, dict[str, Any]] = {}
_pending_lock = threading.Lock()
_refresh_lock = threading.Lock()


class ChatGPTError(RuntimeError):
    pass


class ChatGPTReauthRequired(ChatGPTError):
    pass


def _http_client(timeout: float = 15.0) -> httpx.Client:
    """httpx client; optional proxy via CHATGPT_HTTP_PROXY / HTTPS_PROXY."""
    proxy = (
        os.environ.get("CHATGPT_HTTP_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("ALL_PROXY")
        or ""
    ).strip() or None
    kwargs: dict[str, Any] = {"timeout": timeout}
    if proxy:
        kwargs["proxy"] = proxy
    return httpx.Client(**kwargs)


def is_chatgpt_base(url: str) -> bool:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url or "")
        host = (parsed.hostname or "").lower().rstrip(".")
        path = (parsed.path or "").rstrip("/")
    except Exception:
        return False
    return host == "chatgpt.com" and (
        path == "/backend-api/codex" or path.startswith("/backend-api/codex/")
    )


def chatgpt_headers(access_token: str | None) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/codex",
        "User-Agent": "code-sama-os ChatGPT Subscription",
    }
    if access_token:
        headers["Authorization"] = f"Bearer {access_token}"
    return headers


def _raise_for_oauth(response: httpx.Response, action: str) -> None:
    if response.status_code < 400:
        return
    message = f"ChatGPT Subscription {action} failed with HTTP {response.status_code}."
    code = ""
    try:
        payload = response.json()
        err = payload.get("error") if isinstance(payload, dict) else None
        if isinstance(err, dict):
            code = str(err.get("code") or err.get("type") or "").strip()
            msg = err.get("message")
            if msg:
                message = f"ChatGPT Subscription {action} failed: {msg}"
        elif isinstance(err, str):
            code = err.strip()
            desc = payload.get("error_description") or payload.get("message")
            if desc:
                message = f"ChatGPT Subscription {action} failed: {desc}"
    except Exception:
        pass
    if response.status_code in (401, 403) or code in {
        "invalid_grant", "invalid_token", "invalid_request", "refresh_token_reused",
    }:
        low = message.lower()
        if "not supported" in low or "region" in low or "territory" in low or "country" in low:
            raise ChatGPTError(
                message + " Попробуй VPN или CHATGPT_HTTP_PROXY в .env."
            )
        raise ChatGPTReauthRequired(message)
    raise ChatGPTError(message)


def _json_or_error(response: httpx.Response, action: str) -> dict[str, Any]:
    _raise_for_oauth(response, action)
    data = response.json()
    if not isinstance(data, dict):
        raise ChatGPTError(f"ChatGPT Subscription {action} returned unexpected body.")
    return data


def request_device_code(timeout: float = 15.0) -> dict[str, Any]:
    with _http_client(timeout) as client:
        response = client.post(
            f"{OAUTH_ISSUER}/api/accounts/deviceauth/usercode",
            json={"client_id": OAUTH_CLIENT_ID},
            headers={"Content-Type": "application/json"},
        )
    data = _json_or_error(response, "device-code request")
    if not data.get("device_auth_id") or not data.get("user_code"):
        raise ChatGPTError("ChatGPT device-code response was incomplete.")
    data.setdefault("verification_uri", f"{OAUTH_ISSUER}/codex/device")
    data.setdefault("interval", 5)
    data.setdefault("expires_in", 900)
    return data


def poll_device_auth(device_auth_id: str, user_code: str, timeout: float = 15.0) -> dict[str, Any]:
    with _http_client(timeout) as client:
        response = client.post(
            f"{OAUTH_ISSUER}/api/accounts/deviceauth/token",
            json={"device_auth_id": device_auth_id, "user_code": user_code},
            headers={"Content-Type": "application/json"},
        )
    if response.status_code in (403, 404):
        # Pending auth often returns 403 until the user confirms.
        try:
            payload = response.json()
            err = (payload.get("error") if isinstance(payload, dict) else None) or {}
            msg = ""
            if isinstance(err, dict):
                msg = str(err.get("message") or "")
            elif isinstance(err, str):
                msg = err
            low = msg.lower()
            if "not supported" in low or "region" in low or "territory" in low:
                raise ChatGPTError(msg + " Попробуй VPN или CHATGPT_HTTP_PROXY в .env.")
        except ChatGPTError:
            raise
        except Exception:
            pass
        return {"status": "pending", "error": "authorization_pending"}
    return _json_or_error(response, "device-code poll")


def exchange_authorization_code(authorization_code: str, code_verifier: str, timeout: float = 15.0) -> dict[str, Any]:
    with _http_client(timeout) as client:
        response = client.post(
            OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "code": authorization_code,
                "redirect_uri": OAUTH_REDIRECT_URI,
                "client_id": OAUTH_CLIENT_ID,
                "code_verifier": code_verifier,
            },
        )
    data = _json_or_error(response, "token exchange")
    if not data.get("access_token"):
        raise ChatGPTReauthRequired("Token exchange did not return an access token.")
    return data


def refresh_oauth_tokens(refresh_token: str, timeout: float = 20.0) -> dict[str, Any]:
    if not refresh_token:
        raise ChatGPTReauthRequired("Missing refresh token. Reconnect ChatGPT Subscription.")
    with _http_client(timeout) as client:
        response = client.post(
            OAUTH_TOKEN_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": OAUTH_CLIENT_ID,
            },
        )
    data = _json_or_error(response, "token refresh")
    if not data.get("access_token"):
        raise ChatGPTReauthRequired("Token refresh did not return an access token.")
    return data


def _decode_jwt_payload(token: str) -> dict[str, Any]:
    parts = (token or "").split(".")
    if len(parts) < 2:
        raise ValueError("not a JWT")
    segment = parts[1] + "=" * (-len(parts[1]) % 4)
    raw = base64.urlsafe_b64decode(segment.encode("ascii"))
    payload = json.loads(raw.decode("utf-8"))
    return payload if isinstance(payload, dict) else {}


def access_token_is_expiring(access_token: str, skew_seconds: int = ACCESS_TOKEN_REFRESH_SKEW) -> bool:
    try:
        exp = int(_decode_jwt_payload(access_token).get("exp") or 0)
    except Exception:
        return True
    return exp <= int(time.time()) + int(skew_seconds)


def fetch_available_models(access_token: str, timeout: float = 10.0) -> list[str]:
    if not access_token:
        return []
    try:
        with _http_client(timeout) as client:
            response = client.get(
                f"{DEFAULT_BASE_URL}/models?client_version=1.0.0",
                headers=chatgpt_headers(access_token),
            )
        if response.status_code != 200:
            return []
        data = response.json()
    except Exception:
        return []
    entries = data.get("models", []) if isinstance(data, dict) else []
    sortable: list[tuple[int, str]] = []
    for item in entries:
        if not isinstance(item, dict):
            continue
        slug = item.get("slug")
        if not isinstance(slug, str) or not slug.strip():
            continue
        visibility = item.get("visibility", "")
        if isinstance(visibility, str) and visibility.strip().lower() in {"hide", "hidden"}:
            continue
        priority = item.get("priority")
        rank = int(priority) if isinstance(priority, (int, float)) else 10_000
        sortable.append((rank, slug.strip()))
    sortable.sort(key=lambda item: (item[0], item[1]))
    ordered: list[str] = []
    seen: set[str] = set()
    for _, slug in sortable:
        if slug not in seen:
            ordered.append(slug)
            seen.add(slug)
    return ordered


def build_responses_input(messages: list[dict]) -> list[dict]:
    input_items: list[dict] = []
    for msg in messages or []:
        role = msg.get("role") or "user"
        if role == "tool":
            role = "user"
        content = msg.get("content")
        if isinstance(content, list):
            text = "\n".join(
                str(part.get("text") or part.get("content") or "")
                for part in content if isinstance(part, dict)
            )
        else:
            text = "" if content is None else str(content)
        input_type = "output_text" if role == "assistant" else "input_text"
        input_items.append({"role": role, "content": [{"type": input_type, "text": text}]})
    return input_items


def start_device_flow() -> dict[str, Any]:
    data = request_device_code()
    flow_id = uuid.uuid4().hex[:12]
    with _pending_lock:
        _pending[flow_id] = {
            "device_auth_id": data["device_auth_id"],
            "user_code": data["user_code"],
            "created": time.time(),
            "expires_in": int(data.get("expires_in") or 900),
        }
    return {
        "flow_id": flow_id,
        "user_code": data["user_code"],
        "verification_uri": data.get("verification_uri") or f"{OAUTH_ISSUER}/codex/device",
        "interval": int(data.get("interval") or 5),
        "expires_in": int(data.get("expires_in") or 900),
    }


def poll_device_flow(flow_id: str) -> dict[str, Any]:
    with _pending_lock:
        pending = _pending.get(flow_id)
    if not pending:
        return {"status": "error", "error": "unknown or expired flow_id"}
    if time.time() - pending["created"] > pending["expires_in"]:
        with _pending_lock:
            _pending.pop(flow_id, None)
        return {"status": "error", "error": "device flow expired"}

    data = poll_device_auth(pending["device_auth_id"], pending["user_code"])
    if data.get("status") == "pending" or data.get("error") == "authorization_pending":
        return {"status": "pending"}

    authorization_code = data.get("authorization_code")
    code_verifier = data.get("code_verifier")
    if not authorization_code or not code_verifier:
        return {"status": "pending"}

    tokens = exchange_authorization_code(authorization_code, code_verifier)
    access = tokens.get("access_token") or ""
    refresh = tokens.get("refresh_token") or ""
    models = fetch_available_models(access)
    with _pending_lock:
        _pending.pop(flow_id, None)
    return {
        "status": "authorized",
        "provider": {
            "name": "chatgpt",
            "base_url": DEFAULT_BASE_URL,
            "preset": PROVIDER_ID,
            "auth_mode": "chatgpt",
            "api_key": access,  # bearer used as runtime key
            "access_token": access,
            "refresh_token": refresh,
            "has_key": True,
            "models": models,
        },
        "models": models,
    }


def ensure_fresh_access(settings_get, settings_update, provider_name: str = "chatgpt") -> str:
    """Return a usable access token, refreshing via settings store if needed."""
    data = settings_get()
    llm = data.get("llm") or {}
    providers = llm.get("providers") or {}
    spec = providers.get(provider_name) or {}
    access = (spec.get("access_token") or spec.get("api_key") or "").strip()
    refresh = (spec.get("refresh_token") or "").strip()
    if not access and not refresh:
        raise ChatGPTReauthRequired("ChatGPT Subscription is not connected.")
    if access and not access_token_is_expiring(access):
        return access
    with _refresh_lock:
        data = settings_get()
        llm = data.get("llm") or {}
        providers = llm.get("providers") or {}
        spec = dict(providers.get(provider_name) or {})
        access = (spec.get("access_token") or spec.get("api_key") or "").strip()
        refresh = (spec.get("refresh_token") or "").strip()
        if access and not access_token_is_expiring(access):
            return access
        refreshed = refresh_oauth_tokens(refresh)
        new_access = refreshed["access_token"]
        patch = {
            "llm": {
                "providers": {
                    provider_name: {
                        **spec,
                        "access_token": new_access,
                        "api_key": new_access,
                        "refresh_token": refreshed.get("refresh_token") or refresh,
                        "base_url": spec.get("base_url") or DEFAULT_BASE_URL,
                        "auth_mode": "chatgpt",
                        "has_key": True,
                    }
                }
            }
        }
        settings_update(patch)
        return new_access
