"""Harness — generate an :class:`AppManifest` from a program description.

This is code-sama-os's analog of CLI-Anything's generator. Like the
upstream project, it is *not* a code-generation engine: it's a tight
prompt that walks a coding-capable LLM through a fixed 7-phase pipeline
and expects a JSON manifest at the end.

The 7 phases
------------
1. **Analyze**    — what is this program, what's its CLI/package name,
                    what are the 5–10 most useful things an agent would
                    do with it?
2. **Elements**   — name the widgets / menus / fields the agent will
                    target, with their AT-SPI role+name where possible.
3. **Actions**    — decompose each "useful thing" into a choreography
                    of hover/click/type/key/wait steps that *look* like
                    a human driving the GUI.
4. **Launch**     — exact shell command + window size + any apt deps.
5. **Validate**   — self-check the JSON against the manifest schema.
6. **Narration**  — write one short Russian narration hint per action
                    (feeds idea #3 — the live commentary loop).
7. **Publish**    — emit final JSON, hand to the registry.

Why a class and not a function?
-------------------------------
We want to be able to call ``install`` from a worker tool, from an HTTP
endpoint, and from a CLI helper later. The class holds the LLM config
and the registry handle, and exposes a single coroutine ``install``.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

from .manifest import AppManifest
from .registry import AppRegistry


log = logging.getLogger("code-sama-os.loader.harness")


HARNESS_PROMPT = """\
Ты — генератор манифестов для code-sama App Loader. Твоя задача —
превратить описание Linux-программы в **один JSON-манифест**, который
потом исполнит runtime loader'а. Не пиши никакого кода — только финальный
JSON.

Контекст
--------
code-sama — виртуальная девушка-программист в ретро-ОС. Зритель видит её
курсор и клавиатуру: любое действие обязано **выглядеть** как ручное
управление GUI (курсор едет к меню, кликает, потом посимвольно печатает).
Манифест — это декларативное описание того, КАК это разыграть.

Манифест-схема (строго)
-----------------------
{{
  "app": "slug_lowercase",            // идентификатор, напр. "firefox"
  "title": "Человеческое название",   // для заголовка окна
  "icon": "browser|editor|paint|music|tracker|linux",
  "description": "1 предложение что это и зачем",
  "version": "1.0",
  "package": {{
    "apt": "имя_пакета_или_null",     // что ставить через apt-get install
    "command": "команда_запуска",      // бинарарь или шелл-команда
    "setup": ["опционально доп. шаги"]  // напр. ["apt-get install -y dbus-x11"]
  }},
  "launch": {{
    "cmd": "полная команда запуска с аргументами",
    "width": 900,
    "height": 560
  }},
  "elements": {{                       // словарь имя -> локатор
    "url_bar":  {{ "role": "ENTRY", "name": "Search with DuckDuckGo or enter address" }},
    "back_btn": {{ "role": "PUSH_BUTTON", "name": "Back" }},
    "canvas":   {{ "x_pct": 0.5, "y_pct": 0.5 }}
  }},
  "actions": {{                        // словарь имя -> хореография
    "navigate": {{
      "params": {{ "url": "str" }},
      "narration": "Иду в адресную строку, ввожу {url}…",
      "steps": [
        {{ "click": "url_bar", "dwell": 200 }},
        {{ "type": "{url}" }},
        {{ "key": "Enter" }}
      ]
    }},
    "go_back": {{
      "narration": "Жму «Назад»…",
      "steps": [ {{ "click": "back_btn" }} ]
    }}
  }}
}}

Правила для элементов
---------------------
- ``role`` + ``name`` — предпочтительный способ. Это accessibility-имя
  виджета (GTK/Qt/Electron его экспозят). Если сомневаешься — лучше
  угадай ближе к реальному имени.
- ``x_pct`` / ``y_pct`` — fallback для канвасов/видео/игр, где нет
  a11y-дерева. 0..1 относительно тела окна.
- Не плодите элементы — только те, что реально нужны для действий.

Правила для действий
---------------------
- Имена действий — snake_case глаголы: ``open_file``, ``apply_filter``,
  ``go_back``, ``new_tab``.
- ``params`` описывает аргументы; в ``steps`` используй ``{имя}`` для
  подстановки.
- Каждый шаг — один из: ``hover``, ``click``, ``type``, ``key``,
  ``wait`` (ms), ``wait_for`` (element), ``assert`` (element),
  ``run`` (shell snippet).
- ``dwell`` — пауза в мс перед шагом (для естественности).
- ``narration`` — **одна** короткая фраза по-русски, от первого лица,
  что code-sama сейчас делает. Параметры подставляются как {url}.
- Помни: зритель видит каждое движение, поэтому шаги должны быть
  «человеческими» — открыть меню, дождаться, кликнуть пункт, заполнить
  поле, нажать OK.

7-фазный процесс (делай это в уме, наружу — только финальный JSON)
------------------------------------------------------------------
1. ANALYZE  — что за программа, пакет, 5–10 ключевых операций.
2. ELEMENTS — какие виджеты нужны для этих операций, их role+name.
3. ACTIONS  — разложи операции в хореографии шагов.
4. LAUNCH   — точная команда запуска + размер окна + зависимости.
5. VALIDATE — проверь, что JSON валиден и согласован (все element'ы
              в шагах присутствуют в ``elements``).
6. NARRATE  — одна фраза на каждое действие, по-русски.
7. PUBLISH  — выдай ТОЛЬКО JSON-блок в одном ```json фенсинге.

ВАЖНО: финальный ответ — ровно один JSON в фенсинге ```json, без
дополнительного текста, без пояснений, без markdown вокруг.
"""


class Harness:
    """Generates manifests via the configured LLM."""

    def __init__(self, registry: AppRegistry, router: Any | None = None) -> None:
        self.registry = registry
        self.router = router
        self._role = "harness"

    def _make_llm(self) -> Any:
        if self.router is not None:
            return self.router.llm(self._role)
        # Legacy fallback when no router is wired in (e.g. standalone
        # script use). Mirrors the old env-var behaviour.
        from langchain_openai import ChatOpenAI

        model = (
            os.environ.get("HARNESS_MODEL")
            or os.environ.get("WORKER_MODEL")
            or os.environ.get("MODEL", "qwen/qwen3-235b-a22b-2507")
        )
        return ChatOpenAI(
            model=model,
            api_key=os.environ.get("PROXY_API_KEY", ""),
            base_url=os.environ.get(
                "PROXY_BASE_URL", "https://api.proxyapi.ru/openrouter/v1"
            ),
            temperature=0.2,
        )

    async def install(
        self,
        description: str,
        *,
        app_slug: str | None = None,
        overwrite: bool = True,
        on_phase: Any | None = None,
    ) -> dict[str, Any]:
        """Generate a manifest for ``description`` and persist it.

        ``on_phase`` is an optional awaitable callback receiving
        ``{phase, label}`` so the caller (e.g. an agent narration) can
        narrate progress through the 7 phases.
        """
        phases = [
            ("analyze", "разбираюсь, что за программа"),
            ("elements", "выписываю элементы интерфейса"),
            ("actions", "раскладываю действия в хореографию"),
            ("launch", "подбираю команду запуска"),
            ("validate", "сверяю JSON со схемой"),
            ("narrate", "пишу реплики для комментария"),
            ("publish", "сохраняю манифест"),
        ]
        for label, human in phases:
            log.info("harness phase: %s", label)
            if on_phase:
                try:
                    await on_phase({"phase": label, "label": human})
                except Exception:
                    log.exception("on_phase callback failed")

        llm = self._make_llm()
        user_msg = (
            f"Опиши манифест для следующей программы:\n\n"
            f"{description.strip()}\n\n"
            + (f"slug приложения: {app_slug}\n" if app_slug else "")
            + "Выдай финальный JSON по схеме выше."
        )
        from langchain_core.messages import HumanMessage, SystemMessage

        try:
            result = await llm.ainvoke([
                SystemMessage(content=HARNESS_PROMPT),
                HumanMessage(content=user_msg),
            ])
        except Exception as exc:
            log.exception("harness LLM call failed")
            return {"ok": False, "error": f"llm_failed: {exc}"}

        text = _extract_content(result)
        if not text:
            return {"ok": False, "error": "empty_llm_response"}
        data = _extract_json(text)
        if data is None:
            return {"ok": False, "error": "no_json_in_response",
                    "raw": text[:1000]}
        # Tag + light validation.
        if app_slug and not data.get("app"):
            data["app"] = app_slug
        if not data.get("app"):
            return {"ok": False, "error": "missing_app_field", "raw": text[:500]}
        data["source"] = "generated"
        # Make sure every referenced element exists; auto-add centre hints
        # for dangling references so the runtime can still limp along.
        elements = data.setdefault("elements", {})
        for a in (data.get("actions") or {}).values():
            for step in (a.get("steps") or []):
                for v in step.values():
                    name = v if isinstance(v, str) else (
                        v.get("element") if isinstance(v, dict) else None
                    )
                    if isinstance(name, str) and name and name not in elements \
                            and name not in {"Enter", "Return", "Escape", "Tab"} \
                            and not name.isdigit():
                        elements[name] = {"x_pct": 0.5, "y_pct": 0.5}
        try:
            manifest = AppManifest.from_dict(data)
        except Exception as exc:
            return {"ok": False, "error": f"manifest_invalid: {exc}"}
        try:
            path = await self.registry.install(manifest, overwrite=overwrite)
        except Exception as exc:
            return {"ok": False, "error": f"install_failed: {exc}"}
        return {"ok": True, "app": manifest.app, "path": str(path),
                "manifest": manifest.to_dict()}


def _extract_content(msg: Any) -> str:
    content = getattr(msg, "content", "") or ""
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, dict) and "text" in c:
                parts.append(c["text"])
            elif isinstance(c, str):
                parts.append(c)
        return "".join(parts)
    return str(content)


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first {...} or ```json``` block from ``text``."""
    if not text:
        return None
    # Fenced block first.
    if "```" in text:
        for block in text.split("```"):
            block = block.strip()
            if block.startswith("json"):
                block = block[4:].strip()
            if block.startswith("{") and block.rstrip().endswith("}"):
                try:
                    return json.loads(block)
                except Exception:
                    continue
    # Bare JSON.
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None
    return None
