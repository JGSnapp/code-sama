"""ToolRegistry — builds the agent's tool list dynamically.

The static ``make_tools()`` function in ``tools.py`` is still the source
of truth for the built-in apps (browser, editor, paint, music, tracker,
computer) and the low-level primitives (move_mouse, click, type_text,
...). This registry wraps it and *adds* one first-class tool per action
of every installed loader-app manifest.

Why expose app actions as individual tools?
-------------------------------------------
The previous design gave the agent two generic tools — ``app_open`` and
``app_action(app, action, args)`` — and expected it to call
``app_describe`` to learn an app's verbs. That works, but it buries the
available capabilities: the agent has to *ask* before it can act, and
the LLM has to remember a two-step calling convention.

By materialising each action as its own tool — ``firefox_navigate(url)``,
``gimp_apply_gaussian_blur(radius)``, ... — the agent's tool-calling
loop sees the full surface up front (same way it sees ``open_app`` or
``paint_stroke``). New apps installed at runtime appear as new tools on
the next rebuild.

Auto-open
---------
Per-app tools are smart: if the app isn't running yet, the tool opens
it first (animated, visible) and *then* runs the action. This is the
"skills auto-connect" behaviour: the agent doesn't need a separate
``app_open`` call — invoking ``firefox_navigate`` boots Firefox if it's
not already up. The action's narration still plays through the
narration loop, so code-sama comments on both the launch and the click.

Schema
------
Tool argument schemas are derived from each action's ``params`` dict.
``params: {url: "str"}`` becomes a single required ``url`` string
argument. Optional params default to empty strings; the runtime's
``{placeholder}`` substitution handles missing values gracefully.

MCP / external tools
--------------------
External tool providers (MCP servers, custom plugins) can be registered
via :meth:`add_external_source`. Each source is a callable returning a
list of StructuredTools; the registry merges them on every rebuild.
This is the extension point for "connect MCP when the model needs it"
— a thin MCP adapter would translate ``tools/call`` into StructuredTools
and register itself here.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from langchain_core.tools import StructuredTool

from .loader.manifest import AppManifest
from .loader.registry import AppRegistry
from .loader.runtime import Runtime
from .state import OSController
from .tools import make_tools


log = logging.getLogger("code-sama-os.tool_registry")


# Type of an external tool source: () -> list[StructuredTool]
ExternalSource = Callable[[], list[StructuredTool]]


class ToolRegistry:
    """Owns the agent's tool surface. Rebuilds when apps change."""

    def __init__(
        self,
        controller: OSController,
        browser: Any,
        sandbox: Any,
        registry: AppRegistry,
        runtime: Runtime,
        loader_bundle: dict[str, Any] | None = None,
    ) -> None:
        self.controller = controller
        self.browser = browser
        self.sandbox = sandbox
        self.registry = registry
        self.runtime = runtime
        self.loader_bundle = loader_bundle or {}
        self._external_sources: list[ExternalSource] = []
        self._tools: list[StructuredTool] = []
        self._by_name: dict[str, StructuredTool] = {}

    # ─── external providers (MCP etc.) ────────────────────────────────
    def add_external_source(self, source: ExternalSource) -> None:
        """Register a callable that returns extra tools on each rebuild.

        Used by MCP adapters or any plugin that wants to add tools to
        the agent's surface. The source is called fresh on every
        :meth:`rebuild`, so it can return different tools over time
        (e.g. an MCP server that exposes new methods after init).
        """
        self._external_sources.append(source)

    def remove_external_source(self, source: ExternalSource) -> None:
        try:
            self._external_sources.remove(source)
        except ValueError:
            pass

    # ─── build ────────────────────────────────────────────────────────
    def rebuild(self) -> list[StructuredTool]:
        """Recompute the tool list. Call after install/uninstall."""
        tools: list[StructuredTool] = list(
            make_tools(
                self.controller,
                self.browser,
                self.sandbox,
                loader=self.loader_bundle or None,
            )
        )
        # Add one tool per action of every installed app.
        for manifest in self.registry.list():
            for action_name in sorted(manifest.actions.keys()):
                tool = _build_app_action_tool(manifest, action_name, self.runtime)
                tools.append(tool)
        # Add external sources (MCP etc.).
        for source in self._external_sources:
            try:
                extra = source() or []
                tools.extend(extra)
            except Exception:
                log.exception("external tool source failed: %s", source)
        self._tools = tools
        self._by_name = {t.name: t for t in tools}
        log.info(
            "ToolRegistry rebuilt: %d tools (%d app-actions, %d external)",
            len(tools),
            sum(len(m.actions) for m in self.registry.list()),
            len(self._external_sources),
        )
        return tools

    # ─── access ───────────────────────────────────────────────────────
    def tools(self) -> list[StructuredTool]:
        if not self._tools:
            self.rebuild()
        return list(self._tools)

    def get(self, name: str) -> StructuredTool | None:
        if not self._by_name:
            self.rebuild()
        return self._by_name.get(name)

    def names(self) -> list[str]:
        if not self._by_name:
            self.rebuild()
        return sorted(self._by_name.keys())

    def summary(self) -> list[dict[str, Any]]:
        """Human-readable catalogue: built-ins + per-app verbs."""
        out: list[dict[str, Any]] = []
        for m in self.registry.list():
            out.append({
                "app": m.app,
                "title": m.title,
                "tools": sorted(m.actions.keys()),
                "source": m.source,
            })
        return out


# ─── per-app tool factory ────────────────────────────────────────────────


def _build_app_action_tool(
    manifest: AppManifest,
    action_name: str,
    runtime: Runtime,
) -> StructuredTool:
    """Create one StructuredTool for ``(manifest.app, action_name)``.

    The tool's args schema is derived from ``action.params``. Calling it
    auto-opens the app if needed, then runs the action's choreography.
    """
    app = manifest.app
    tool_name = f"{app}_{action_name}"
    params = manifest.action_params(action_name)
    description = _action_description(manifest, action_name)

    # We build a closure with a stable signature. StructuredTool's
    # from_function introspects the arg list, so we generate a function
    # whose parameters match the manifest's declared params. We hand-
    # build the signature object (no exec) so empty param lists work.
    arg_names = list(params.keys())

    async def invoke(**kwargs: Any) -> Any:
        bound: dict[str, Any] = {}
        for n in arg_names:
            bound[n] = kwargs.get(n, "")
        return await runtime.run_action_autoopen(app, action_name, bound)

    # Attach a synthetic signature so LangChain derives the tool schema
    # from the declared params (rather than the generic **kwargs).
    import inspect

    sig_params = [inspect.Parameter(n, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                  for n in arg_names]
    invoke.__signature__ = inspect.Signature(parameters=sig_params)  # type: ignore
    invoke.__name__ = tool_name
    invoke.__doc__ = description

    return StructuredTool.from_function(coroutine=invoke, name=tool_name,
                                        description=description)


def _action_description(manifest: AppManifest, action_name: str) -> str:
    """Compose a helpful tool description from the manifest."""
    narration = manifest.action_narration(action_name)
    params = manifest.action_params(action_name)
    parts = [f"{manifest.title} — {action_name.replace('_', ' ')}."]
    if narration:
        parts.append(f"Что делает: {narration}")
    if params:
        pl = ", ".join(f"{k}" for k in params)
        parts.append(f"Параметры: {pl}.")
    parts.append(
        "Если приложение ещё не открыто — оно автоматически запустится "
        "в окне ОС (видимо, с курсором и печатью), потом выполнится действие."
    )
    return " ".join(parts)


def _snake(name: str) -> str:
    import re
    s = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
    return s or "x"
