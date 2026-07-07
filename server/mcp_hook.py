"""Model Context Protocol (MCP) integration hook.

This is an **extension point**, not a full MCP client. It documents how
to plug external MCP servers (or any other tool source) into code-sama's
agent surface via :class:`server.tool_registry.ToolRegistry`.

The pattern
-----------
The :class:`ToolRegistry` accepts *external sources* — callables that
return a list of :class:`StructuredTool`. Each rebuild calls them fresh,
so a source can return different tools over time (e.g. after an MCP
server finishes initializing).

To add MCP tools::

    from server.tool_registry import ToolRegistry
    from server.mcp_hook import build_mcp_source

    tool_reg.add_external_source(build_mcp_source(
        servers=[
            {"name": "filesystem", "command": "npx",
             "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]},
        ],
    ))

Each entry in ``servers`` maps to an MCP server spec. ``build_mcp_source``
returns a callable; when the registry rebuilds, the callable lazily
connects to each server, lists its ``tools/list``, and wraps each as a
StructuredTool that forwards ``tools/call``.

Why a hook, not a hard dependency?
---------------------------------
MCP requires ``mcp`` (the Python SDK) + Node.js for many official
servers. Bundling those into the base image would bloat it for users
who never touch MCP. Keeping this as an opt-in adapter means:

  • The base image stays small.
  • Users who want MCP ``pip install mcp`` and wire it up.
  • Everything else (loader apps, narration, model routing) works
    unchanged.

When ``mcp`` isn't importable, :func:`build_mcp_source` returns a
no-op source so the registry never crashes.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

log = logging.getLogger("code-sama-os.mcp")


# Type alias matching ToolRegistry's ExternalSource.
ExternalSource = Callable[[], list[Any]]


def build_mcp_source(servers: list[dict[str, Any]]) -> ExternalSource:
    """Build an external tool source backed by MCP servers.

    ``servers`` is a list of MCP server specs::

        {"name": "fs", "command": "npx",
         "args": ["-y", "@modelcontextprotocol/server-filesystem", "/workspace"]}

    The returned callable, when invoked by the ToolRegistry, will:

      1. Connect to each server (lazily, in a background task that
         survives across registry rebuilds).
      2. Cache the list of tools the server exposes.
      3. Return a :class:`StructuredTool` per exposed MCP tool, whose
         ``invoke`` forwards to ``tools/call``.

    If the ``mcp`` package isn't installed, the source returns ``[]``.
    """
    state = _McpState(servers)

    def source() -> list[Any]:
        return state.snapshot_tools()

    # Kick off the connection loop in the background. We try to grab the
    # running loop; if none (e.g. called from sync context at import
    # time), the first rebuild will trigger it.
    state.ensure_connect()
    return source


class _McpState:
    """Holds MCP connections + a flat list of wrapped tools."""

    def __init__(self, servers: list[dict[str, Any]]) -> None:
        self.servers = servers
        self._tools: list[Any] = []
        self._connect_task: asyncio.Task | None = None
        self._connected = False
        self._tried = False

    def ensure_connect(self) -> None:
        if self._tried:
            return
        self._tried = True
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return
        if loop.is_running():
            self._connect_task = loop.create_task(self._connect_all())

    async def _connect_all(self) -> None:
        try:
            from mcp import ClientSession, StdioServerParameters  # type: ignore
            from mcp.client.stdio import stdio_client  # type: ignore
        except ImportError:
            log.info("MCP: 'mcp' package not installed — MCP source disabled")
            return
        for spec in self.servers:
            try:
                await self._connect_one(spec)
            except Exception:
                log.exception("MCP: failed to connect %s", spec.get("name"))
        self._connected = True

    async def _connect_one(self, spec: dict[str, Any]) -> None:
        """Connect to one MCP server and wrap its tools.

        This is intentionally minimal — a real adapter would maintain a
        long-lived session, handle reconnects, and respect the server's
        tool schemas. The skeleton here is enough to demonstrate the
        integration boundary; fleshing it out is a follow-up task.
        """
        try:
            from mcp import ClientSession, StdioServerParameters  # type: ignore
            from mcp.client.stdio import stdio_client  # type: ignore
        except ImportError:
            return
        name = spec.get("name") or "mcp"
        params = StdioServerParameters(
            command=spec["command"],
            args=list(spec.get("args") or []),
            env=spec.get("env"),
        )
        # NOTE: a stdio_client context must stay open for the session's
        # lifetime. We hold it open via the task; on shutdown the task
        # is cancelled and the context closes.
        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_resp = await session.list_tools()
                    for t in getattr(tools_resp, "tools", []) or []:
                        wrapped = _wrap_mcp_tool(name, t, session)
                        if wrapped is not None:
                            self._tools.append(wrapped)
        except Exception:
            log.exception("MCP: session failed for %s", name)

    def snapshot_tools(self) -> list[Any]:
        return list(self._tools)


def _wrap_mcp_tool(server_name: str, tool_meta: Any, session: Any) -> Any:
    """Wrap one MCP tool as a StructuredTool.

    Returns ``None`` if the MCP package isn't available — we never want
    a hard dependency at import time.
    """
    try:
        from langchain_core.tools import StructuredTool
    except ImportError:
        return None
    tool_name = getattr(tool_meta, "name", None) or "mcp_tool"
    description = getattr(tool_meta, "description", "") or f"MCP tool {tool_name}"
    input_schema = getattr(tool_meta, "inputSchema", {}) or {}
    properties = (input_schema.get("properties") or {}) if isinstance(input_schema, dict) else {}
    arg_names = list(properties.keys())

    import inspect

    async def invoke(**kwargs: Any) -> Any:
        try:
            result = await session.call_tool(tool_name, kwargs)
            return getattr(result, "content", result)
        except Exception as exc:
            log.exception("MCP call failed: %s/%s", server_name, tool_name)
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    sig_params = [inspect.Parameter(n, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                  for n in arg_names]
    invoke.__signature__ = inspect.Signature(parameters=sig_params)  # type: ignore
    full_name = f"mcp_{server_name}_{tool_name}"
    invoke.__name__ = full_name
    invoke.__doc__ = description
    return StructuredTool.from_function(coroutine=invoke, name=full_name,
                                        description=description)
