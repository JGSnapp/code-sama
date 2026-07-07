"""Minimal LangGraph LLM example for SW-catalog.

Run:
    python api_example.py

Install dependencies if needed:
    python -m pip install langgraph langchain-openai

Before running against the real API, replace PROXY_API_KEY with a real key.
The endpoint/model below match the ProxyAPI OpenRouter setup used by this
project's env.example.
"""

from __future__ import annotations

import json
from typing import Any, Literal

try:
    from langchain_core.messages import HumanMessage, SystemMessage
    from langchain_core.tools import StructuredTool
    from langchain_openai import ChatOpenAI
    from langgraph.graph import END, START, MessagesState, StateGraph
    from langgraph.prebuilt import ToolNode
except ImportError as exc:
    LANGGRAPH_IMPORT_ERROR: ImportError | None = exc
else:
    LANGGRAPH_IMPORT_ERROR = None


# Hardcoded demo settings. Replace the key with a real ProxyAPI key.
PROXY_API_KEY = "sk-lirm4GErQzCo6eOVH2oR4XGfaiQq7vrD"
PROXY_BASE_URL = "https://api.proxyapi.ru/openrouter/v1"
MODEL = "qwen/qwen3-235b-a22b-2507"
FALLBACK_MODEL = "openai/gpt-oss-120b"


def search_supplier_web(query: str, docs: int = 3) -> dict[str, Any]:
    """Stub tool: imitate web search results for supplier discovery."""

    return {
        "ok": True,
        "query": query,
        "docs": docs,
        "results": [
            {
                "title": "Demo Textile Supplier",
                "url": "https://example.com/suppliers/textile",
                "snippet": "Demo supplier for cotton, viscose, wool, and sewing accessories.",
            }
        ],
    }


def read_supplier_page(url: str) -> dict[str, Any]:
    """Stub tool: imitate page reader output."""

    return {
        "ok": True,
        "url": url,
        "text": (
            "Demo Textile Supplier offers wool fabric from 790 RUB per meter, "
            "lead time 7-10 days, minimum order 100 meters."
        ),
    }


def main() -> None:
    if PROXY_API_KEY == "PASTE_PROXY_API_KEY_HERE":
        print("Set PROXY_API_KEY in api_example.py before running a real LLM call.")
        return

    if LANGGRAPH_IMPORT_ERROR is not None:
        print("Install dependencies first: python -m pip install langgraph langchain-openai")
        return

    tools = [
        StructuredTool.from_function(
            func=search_supplier_web,
            name="search_supplier_web",
            description="Search supplier pages. This is a local stub tool.",
        ),
        StructuredTool.from_function(
            func=read_supplier_page,
            name="read_supplier_page",
            description="Read a supplier page. This is a local stub tool.",
        ),
    ]

    def build_graph(model_name: str):
        llm = ChatOpenAI(
            model=model_name,
            api_key=PROXY_API_KEY,
            base_url=PROXY_BASE_URL,
            temperature=0,
        ).bind_tools(tools)

        def call_llm(state: MessagesState) -> dict[str, Any]:
            return {"messages": [llm.invoke(state["messages"])]}

        def route_tools(state: MessagesState) -> Literal["tools", "__end__"]:
            last_message = state["messages"][-1]
            if getattr(last_message, "tool_calls", None):
                return "tools"
            return END

        graph = StateGraph(MessagesState)
        graph.add_node("agent", call_llm)
        graph.add_node("tools", ToolNode(tools))
        graph.add_edge(START, "agent")
        graph.add_conditional_edges("agent", route_tools)
        graph.add_edge("tools", "agent")
        return graph.compile()

    messages = [
        SystemMessage(
            content=(
                "Ты агент закупок SW-catalog. Используй доступные tools, "
                "если нужно найти или прочитать поставщика. Отвечай кратко на русском."
            )
        ),
        HumanMessage(
            content=(
                "Найди демо-поставщика шерстяной ткани и верни только JSON с полями "
                "name, price_text, lead_time, url."
            )
        ),
    ]

    for model_name in (MODEL, FALLBACK_MODEL):
        try:
            print(f"Using model: {model_name}")
            result = build_graph(model_name).invoke({"messages": messages})
            answer = result["messages"][-1].content
            if isinstance(answer, list):
                answer = json.dumps(answer, ensure_ascii=False)
            print(answer)
            return
        except Exception as exc:
            print(f"Model failed: {model_name}: {type(exc).__name__}: {exc}")

    raise RuntimeError("All configured models failed.")


if __name__ == "__main__":
    main()
