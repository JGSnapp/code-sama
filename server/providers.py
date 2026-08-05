"""LLM provider presets — mirrored from Odysseus Settings → Services.

Device-flow providers (GitHub Copilot, ChatGPT Subscription) need OAuth and
are listed but marked ``auth: device`` so the UI can show them as unavailable
until that flow is wired. Everything else is OpenAI-compatible (or Anthropic
Messages API via a compatible proxy URL).
"""

from __future__ import annotations

from typing import Any


# id → preset. ``base_url`` empty means custom / user-typed.
PROVIDER_PRESETS: list[dict[str, Any]] = [
    {
        "id": "custom",
        "name": "Custom URL",
        "base_url": "",
        "logo": "custom",
        "auth": "api_key",
    },
    {
        "id": "proxyapi",
        "name": "ProxyAPI (OpenRouter)",
        "base_url": "https://api.proxyapi.ru/openrouter/v1",
        "logo": "openrouter",
        "auth": "api_key",
    },
    {
        "id": "anthropic",
        "name": "Anthropic",
        "base_url": "https://api.anthropic.com",
        "logo": "anthropic",
        "auth": "api_key",
        "note": "Use an OpenAI-compatible proxy URL if your stack talks /chat/completions only.",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "logo": "deepseek",
        "auth": "api_key",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "logo": "openai",
        "auth": "api_key",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "logo": "openrouter",
        "auth": "api_key",
    },
    {
        "id": "ollama-cloud",
        "name": "Ollama Cloud",
        "base_url": "https://ollama.com/api",
        "logo": "ollama",
        "auth": "api_key",
    },
    {
        "id": "ollama-local",
        "name": "Ollama (local)",
        "base_url": "http://localhost:11434/v1",
        "logo": "ollama",
        "auth": "none",
        "default_api_key": "ollama",
    },
    {
        "id": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "logo": "groq",
        "auth": "api_key",
    },
    {
        "id": "mistral",
        "name": "Mistral",
        "base_url": "https://api.mistral.ai/v1",
        "logo": "mistral",
        "auth": "api_key",
    },
    {
        "id": "together",
        "name": "Together AI",
        "base_url": "https://api.together.xyz/v1",
        "logo": "together",
        "auth": "api_key",
    },
    {
        "id": "fireworks",
        "name": "Fireworks AI",
        "base_url": "https://api.fireworks.ai/inference/v1",
        "logo": "fireworks",
        "auth": "api_key",
    },
    {
        "id": "google",
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "logo": "gemini",
        "auth": "api_key",
    },
    {
        "id": "xai",
        "name": "xAI Grok",
        "base_url": "https://api.x.ai/v1",
        "logo": "grok",
        "auth": "api_key",
    },
    {
        "id": "zai",
        "name": "Z.AI (Zhipu)",
        "base_url": "https://api.z.ai/api/paas/v4",
        "logo": "zhipu",
        "auth": "api_key",
    },
    {
        "id": "zai-coding",
        "name": "Z.AI Coding Plan",
        "base_url": "https://api.z.ai/api/coding/paas/v4",
        "logo": "zhipu",
        "auth": "api_key",
    },
    {
        "id": "opencode-zen",
        "name": "OpenCode Zen",
        "base_url": "https://opencode.ai/zen/v1",
        "logo": "opencode",
        "auth": "api_key",
    },
    {
        "id": "opencode-go",
        "name": "OpenCode Go",
        "base_url": "https://opencode.ai/zen/go/v1",
        "logo": "opencode",
        "auth": "api_key",
    },
    {
        "id": "nvidia",
        "name": "NVIDIA",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "logo": "nvidia",
        "auth": "api_key",
    },
    {
        "id": "copilot",
        "name": "GitHub Copilot",
        "base_url": "copilot",
        "logo": "github",
        "auth": "device",
        "note": "Device-flow auth not wired in code-sama-os yet.",
    },
    {
        "id": "chatgpt-subscription",
        "name": "ChatGPT Subscription",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "logo": "openai",
        "auth": "device",
        "note": "OAuth device flow — Plus/Pro/Team через Codex.",
    },
]


DEFAULT_VRM = "/uploads/vrm/code-sama.vrm"


def default_settings() -> dict[str, Any]:
    """Fresh settings document used when settings.json is missing."""
    return {
        "camera": {
            "yOffset": 0.0,
            "distance": 1.45,
            "fov": 24,
        },
        "avatar": {
            "vrmUrl": DEFAULT_VRM,
            "background": "default",
        },
        "desktop": {
            "wallpaper": "default",
        },
        "voice": {
            "lang": "ru-RU",
            "rate": 1.0,
            "pitch": 1.05,
            "voiceName": "",
            "sampleUrl": "",
            "refText": "",
            "instruct": "female, young adult, russian accent, moderate pitch",
        },
        "llm": {
            "providers": {},
            "roles": {
                "streamer": {"provider": "", "model": "", "temperature": 0.55},
                "worker": {"provider": "", "model": "", "temperature": 0.2},
                "narrator": {"provider": "", "model": "", "temperature": 0.6, "max_tokens": 80},
                "harness": {"provider": "", "model": "", "temperature": 0.2},
                "vision": {"provider": "", "model": "", "temperature": 0.1},
                "planner": {"provider": "", "model": "", "temperature": 0.3},
            },
            "activeProvider": "",
            "activeModel": "",
        },
    }


def preset_by_id(preset_id: str) -> dict[str, Any] | None:
    for p in PROVIDER_PRESETS:
        if p["id"] == preset_id:
            return p
    return None
