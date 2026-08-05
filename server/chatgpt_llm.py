"""LangChain chat model backed by ChatGPT Subscription Codex ``/responses``.

Always streams (Codex requires ``stream: true``). Supports function tools via
the Responses API so LangGraph ``bind_tools`` actually works for Streamer/Worker.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any, Callable, Iterator, Optional

import httpx
from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr

from . import chatgpt_subscription as cg

log = logging.getLogger("code-sama-os.chatgpt_llm")


def _msg_role_content(msg: BaseMessage) -> tuple[str, str]:
    if isinstance(msg, SystemMessage):
        role = "system"
    elif isinstance(msg, AIMessage):
        role = "assistant"
    elif isinstance(msg, ToolMessage):
        role = "tool"
    else:
        role = "user"
    content = msg.content
    if isinstance(content, list):
        text = "\n".join(
            str(p.get("text") or p.get("content") or "") if isinstance(p, dict) else str(p)
            for p in content
        )
    else:
        text = "" if content is None else str(content)
    return role, text


def _proxy() -> str | None:
    return (
        os.environ.get("CHATGPT_HTTP_PROXY")
        or os.environ.get("HTTPS_PROXY")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("ALL_PROXY")
        or None
    )


def _stream_headers(access_token: str | None) -> dict[str, str]:
    headers = cg.chatgpt_headers(access_token)
    headers["Accept"] = "text/event-stream"
    return headers


def _tool_to_responses(tool: Any) -> dict[str, Any]:
    name = getattr(tool, "name", None) or "tool"
    desc = getattr(tool, "description", None) or ""
    schema: dict[str, Any] = {"type": "object", "properties": {}}
    args_schema = getattr(tool, "args_schema", None)
    if args_schema is not None:
        try:
            if hasattr(args_schema, "model_json_schema"):
                schema = args_schema.model_json_schema()
            elif hasattr(args_schema, "schema"):
                schema = args_schema.schema()
        except Exception:
            log.exception("tool schema failed for %s", name)
    # Strip JSON-schema noise Codex may reject.
    schema = {k: v for k, v in schema.items() if k in ("type", "properties", "required", "additionalProperties")}
    if "type" not in schema:
        schema["type"] = "object"
    return {
        "type": "function",
        "name": name,
        "description": desc,
        "parameters": schema,
    }


def _tool_call_name(tc: Any) -> str:
    """Best-effort name from a LangChain tool_call dict/object."""
    if tc is None:
        return ""
    if isinstance(tc, dict):
        name = tc.get("name") or ""
        if not name:
            fn = tc.get("function")
            if isinstance(fn, dict):
                name = fn.get("name") or ""
        return str(name or "").strip()
    name = getattr(tc, "name", None) or ""
    if not name:
        fn = getattr(tc, "function", None)
        if isinstance(fn, dict):
            name = fn.get("name") or ""
        elif fn is not None:
            name = getattr(fn, "name", None) or ""
    return str(name or "").strip()


def _tool_call_id(tc: Any) -> str:
    if isinstance(tc, dict):
        cid = tc.get("id") or tc.get("call_id") or ""
    else:
        cid = getattr(tc, "id", None) or getattr(tc, "call_id", None) or ""
    return str(cid or "").strip() or f"call_{uuid.uuid4().hex[:8]}"


def _tool_call_args(tc: Any) -> dict[str, Any]:
    if isinstance(tc, dict):
        args = tc.get("args")
        if args is None:
            fn = tc.get("function")
            raw = fn.get("arguments") if isinstance(fn, dict) else None
            if isinstance(raw, str):
                try:
                    args = json.loads(raw or "{}")
                except json.JSONDecodeError:
                    args = {"_raw": raw}
            else:
                args = raw
    else:
        args = getattr(tc, "args", None)
    if not isinstance(args, dict):
        return {}
    return args


def _build_input_with_tools(messages: list[BaseMessage]) -> list[dict]:
    """Map LangChain messages to Responses ``input`` items (incl. tool results)."""
    items: list[dict] = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            continue
        if isinstance(msg, ToolMessage):
            call_id = str(getattr(msg, "tool_call_id", None) or "").strip() or f"call_{uuid.uuid4().hex[:8]}"
            out: dict[str, Any] = {
                "type": "function_call_output",
                "call_id": call_id,
                "output": str(msg.content or ""),
            }
            # Some Codex builds validate ``name`` on outputs; only send if non-empty.
            tname = str(getattr(msg, "name", None) or "").strip()
            if tname:
                out["name"] = tname
            items.append(out)
            continue
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            # Prior assistant turn that requested tools — re-emit as function_call items.
            for tc in msg.tool_calls or []:
                name = _tool_call_name(tc)
                if not name:
                    log.warning("skipping tool_call with empty name in history: %r", tc)
                    continue
                items.append({
                    "type": "function_call",
                    "call_id": _tool_call_id(tc),
                    "name": name,
                    "arguments": json.dumps(_tool_call_args(tc), ensure_ascii=False),
                })
            text = msg.content
            if isinstance(text, str) and text.strip():
                items.append({
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                })
            continue
        role, text = _msg_role_content(msg)
        if role == "tool":
            continue
        input_type = "output_text" if role == "assistant" else "input_text"
        items.append({"role": role, "content": [{"type": input_type, "text": text}]})
    return items


def _consume_sse(response: httpx.Response) -> tuple[str, list[dict[str, Any]]]:
    """Return ``(text, tool_calls)`` from a Codex Responses SSE body.

    Important: argument deltas key off ``item_id`` (``fc_…``), while the
    callable id used for ``function_call_output`` is ``call_id`` (``call_…``).
    We index both so arguments are not dropped as ``{}``.
    """
    chunks: list[str] = []
    # Primary store keyed by item id (fc_…) when available, else call_id.
    tool_calls: dict[str, dict[str, Any]] = {}
    # Alias map: call_id / item_id / output_index → primary key
    aliases: dict[str, str] = {}
    event_name = ""
    error_text = ""

    def _resolve_key(data: dict[str, Any], item: dict[str, Any] | None = None) -> str:
        item = item or {}
        candidates = [
            str(data.get("item_id") or "").strip(),
            str(item.get("id") or "").strip(),
            str(data.get("call_id") or "").strip(),
            str(item.get("call_id") or "").strip(),
        ]
        for c in candidates:
            if c and c in aliases:
                return aliases[c]
            if c and c in tool_calls:
                return c
        # Fallback: output_index
        if "output_index" in data:
            idx_key = f"idx:{data['output_index']}"
            if idx_key in aliases:
                return aliases[idx_key]
        for c in candidates:
            if c:
                return c
        if "output_index" in data:
            return f"idx:{data['output_index']}"
        return f"call_{uuid.uuid4().hex[:8]}"

    def _ensure_entry(
        *,
        primary: str,
        call_id: str = "",
        item_id: str = "",
        name: str = "",
        arguments: str = "",
        output_index: Any = None,
    ) -> dict[str, Any]:
        key = aliases.get(primary, primary)
        if key not in tool_calls and primary in tool_calls:
            key = primary
        if key not in tool_calls:
            tool_calls[key] = {
                "id": call_id or primary,
                "item_id": item_id or (primary if primary.startswith("fc_") else ""),
                "name": name,
                "args": {},
                "_arguments": arguments or "",
            }
        entry = tool_calls[key]
        if call_id:
            entry["id"] = call_id
            aliases[call_id] = key
        if item_id:
            entry["item_id"] = item_id
            aliases[item_id] = key
        if name:
            entry["name"] = name
        if arguments and not entry.get("_arguments"):
            entry["_arguments"] = arguments
        if output_index is not None:
            aliases[f"idx:{output_index}"] = key
        aliases[primary] = key
        aliases[key] = key
        return entry

    for line in response.iter_lines():
        if not line:
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if not line.startswith("data:"):
            continue
        raw = line[5:].strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        evt = data.get("type") or event_name

        if evt == "response.output_text.delta":
            delta = data.get("delta") or ""
            if delta:
                chunks.append(str(delta))
        elif evt == "response.output_text.done":
            text = data.get("text")
            if isinstance(text, str) and text and not chunks:
                chunks.append(text)
        elif evt in ("response.output_item.added", "response.output_item.done"):
            item = data.get("item") or {}
            if not isinstance(item, dict) or item.get("type") != "function_call":
                continue
            item_id = str(item.get("id") or "").strip()
            call_id = str(item.get("call_id") or "").strip()
            primary = item_id or call_id or _resolve_key(data, item)
            entry = _ensure_entry(
                primary=primary,
                call_id=call_id or primary,
                item_id=item_id,
                name=str(item.get("name") or "").strip(),
                arguments=str(item.get("arguments") or ""),
                output_index=data.get("output_index"),
            )
            # output_item.done often has the full arguments string.
            if item.get("arguments"):
                entry["_arguments"] = str(item.get("arguments") or "")
            if item.get("name"):
                entry["name"] = str(item["name"]).strip()
        elif evt == "response.function_call_arguments.delta":
            key = _resolve_key(data)
            entry = _ensure_entry(
                primary=key,
                call_id=str(data.get("call_id") or "").strip(),
                item_id=str(data.get("item_id") or "").strip(),
                output_index=data.get("output_index"),
            )
            entry["_arguments"] = (entry.get("_arguments") or "") + str(data.get("delta") or "")
        elif evt == "response.function_call_arguments.done":
            key = _resolve_key(data)
            entry = _ensure_entry(
                primary=key,
                call_id=str(data.get("call_id") or "").strip(),
                item_id=str(data.get("item_id") or "").strip(),
                name=str(data.get("name") or "").strip(),
                output_index=data.get("output_index"),
            )
            if data.get("arguments") is not None:
                entry["_arguments"] = data.get("arguments") or ""
            if data.get("name"):
                entry["name"] = str(data["name"]).strip()
        elif evt == "response.completed":
            if not chunks:
                resp = data.get("response") if isinstance(data.get("response"), dict) else data
                nested = _extract_output_text(resp) if isinstance(resp, dict) else ""
                if nested:
                    chunks.append(nested)
            resp = data.get("response") if isinstance(data.get("response"), dict) else {}
            for item in (resp.get("output") or []):
                if not isinstance(item, dict) or item.get("type") != "function_call":
                    continue
                item_id = str(item.get("id") or "").strip()
                call_id = str(item.get("call_id") or "").strip()
                primary = item_id or call_id or f"call_{uuid.uuid4().hex[:8]}"
                entry = _ensure_entry(
                    primary=primary,
                    call_id=call_id or primary,
                    item_id=item_id,
                    name=str(item.get("name") or "").strip(),
                )
                if item.get("arguments"):
                    entry["_arguments"] = str(item.get("arguments") or "")
            break
        elif evt in ("response.failed", "error"):
            err = data.get("error") or (data.get("response") or {}).get("error") or {}
            error_text = err.get("message") if isinstance(err, dict) else str(err or evt)
            break

    if error_text:
        raise cg.ChatGPTError(f"ChatGPT Subscription stream failed: {error_text}")

    # Deduplicate aliases pointing at the same entry.
    seen: set[int] = set()
    parsed: list[dict[str, Any]] = []
    for tc in tool_calls.values():
        oid = id(tc)
        if oid in seen:
            continue
        seen.add(oid)
        name = str(tc.get("name") or "").strip()
        if not name:
            log.warning("dropping nameless function_call from SSE: %r", tc)
            continue
        raw_args = tc.get("_arguments", "") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
        except json.JSONDecodeError:
            args = {"_raw": raw_args}
        if not isinstance(args, dict):
            args = {"value": args}
        log.info(
            "SSE tool_call name=%s args_keys=%s args_len=%s",
            name,
            list(args.keys()),
            len(raw_args) if isinstance(raw_args, str) else "?",
        )
        parsed.append({
            "id": tc.get("id") or tc.get("item_id") or f"call_{uuid.uuid4().hex[:8]}",
            "name": name,
            "args": args,
            "type": "tool_call",
        })
    return "".join(chunks).strip(), parsed


def _extract_output_text(payload: dict[str, Any]) -> str:
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"]
    chunks: list[str] = []
    for item in payload.get("output") or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("content") or []:
            if not isinstance(part, dict):
                continue
            if part.get("type") in ("output_text", "text") and part.get("text"):
                chunks.append(str(part["text"]))
    return "".join(chunks).strip()


class ChatGPTSubscriptionChat(BaseChatModel):
    """Codex Responses client — streams text + optional function tool calls."""

    model_name: str = Field(default="gpt-5.1")
    temperature: float = 0.4
    provider_name: str = "chatgpt"
    timeout: float = 180.0
    _token_fn: Callable[[], str] = PrivateAttr()
    _tools: list[Any] = PrivateAttr(default_factory=list)

    def __init__(
        self,
        *,
        token_fn: Callable[[], str],
        model: str,
        temperature: float = 0.4,
        tools: list[Any] | None = None,
        **kwargs: Any,
    ):
        super().__init__(model_name=model, temperature=temperature, **kwargs)
        self._token_fn = token_fn
        self._tools = list(tools or [])

    @property
    def _llm_type(self) -> str:
        return "chatgpt-subscription"

    def bind_tools(self, tools: Any, **kwargs: Any) -> "ChatGPTSubscriptionChat":
        del kwargs
        return ChatGPTSubscriptionChat(
            token_fn=self._token_fn,
            model=self.model_name,
            temperature=self.temperature,
            tools=list(tools or []),
            provider_name=self.provider_name,
            timeout=self.timeout,
        )

    def _build_payload(self, messages: list[BaseMessage]) -> dict[str, Any]:
        converted = [_msg_role_content(m) for m in messages]
        system_parts = [c for r, c in converted if r == "system" and c.strip()]
        payload: dict[str, Any] = {
            "model": self.model_name,
            "instructions": "\n\n".join(system_parts) if system_parts else "You are a helpful AI assistant.",
            "input": _build_input_with_tools(messages),
            "stream": True,
            "store": False,
        }
        if self._tools:
            payload["tools"] = [_tool_to_responses(t) for t in self._tools]
            payload["tool_choice"] = "auto"
        model_l = (self.model_name or "").lower()
        if not model_l.startswith("gpt-5") and "o1" not in model_l and "o3" not in model_l:
            payload["temperature"] = self.temperature
        return payload

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> ChatResult:
        no_tools_retry = bool(kwargs.pop("_no_tools_retry", False))
        del stop, run_manager
        payload = self._build_payload(messages)
        token = self._token_fn()
        url = cg.DEFAULT_BASE_URL.rstrip("/") + "/responses"
        timeout = httpx.Timeout(self.timeout, connect=30.0)
        with httpx.Client(timeout=timeout, proxy=_proxy()) as client:
            with client.stream(
                "POST",
                url,
                json=payload,
                headers=_stream_headers(token),
            ) as resp:
                if resp.status_code in (401, 403):
                    body = resp.read().decode("utf-8", errors="replace")[:300]
                    raise cg.ChatGPTReauthRequired(
                        f"ChatGPT Subscription rejected credentials (HTTP {resp.status_code}). "
                        f"Reconnect. {body}"
                    )
                if resp.status_code >= 400:
                    body = resp.read().decode("utf-8", errors="replace")[:400]
                    # If tools are rejected, retry once without tools so chat still works.
                    if self._tools and not no_tools_retry:
                        log.warning("Codex rejected tools — retrying without tools: %s", body[:200])
                        unbound = ChatGPTSubscriptionChat(
                            token_fn=self._token_fn,
                            model=self.model_name,
                            temperature=self.temperature,
                            tools=[],
                            provider_name=self.provider_name,
                            timeout=self.timeout,
                        )
                        return unbound._generate(messages, _no_tools_retry=True)
                    raise cg.ChatGPTError(f"ChatGPT Subscription HTTP {resp.status_code}: {body}")
                text, tool_calls = _consume_sse(resp)
        msg = AIMessage(content=text or "", tool_calls=tool_calls or [])
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def _stream(
        self,
        messages: list[BaseMessage],
        stop: Optional[list[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGeneration]:
        result = self._generate(messages, stop=stop, run_manager=run_manager, **kwargs)
        yield result.generations[0]
