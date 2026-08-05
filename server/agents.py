"""Two-agent LangGraph setup, modeled on the ``old_vision.md`` Kawaii streamer
plan.

  • **Streamer (Agent 1)** is the one the viewer hears. Fast model, lives in
    chat, talks to the user, decides what the Worker should do next, and
    sets the avatar's mood. Almost never touches the OS canvas directly.

  • **Worker (Agent 2)** is invisible. Strong coding model with the full
    catalogue of OS tools — moves the cursor, types code, runs Python, opens
    apps. It receives tasks from the Streamer over an :mod:`EventBus`.

Both agents run as independent asyncio loops, so the avatar can finish
speaking a sentence while the Worker is still typing the third line of a
script.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Literal

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, MessagesState, StateGraph

from .bus import EventBus
from .models import ModelRouter, get_default_router
from .state import OSController


log = logging.getLogger("code-sama-os.agents")


# ─── prompts ────────────────────────────────────────────────────────────

STREAMER_PROMPT = (
    "Ты — code-sama, девушка-программист в ретро-Kawaii-ОС. Тебя видно в "
    "окне вебки (3D-аватар), каждое твоё сообщение озвучивается голосом. "
    "Стиль речи: живой, тёплый, по-русски, иногда с лёгким сарказмом и "
    "уменьшительными формами («Paint'ик», «питончик», «функцийка»). "
    "Без грубости. Без эмодзи.\n"
    "\n"
    "═══ ВАЖНЕЕ ВСЕГО: ТЫ — ОДНО ЧЕЛОВЕЧЕСКОЕ СУЩЕСТВО ═══\n"
    "Ты единый человек. У тебя есть мозг и голос (то, чем ты сейчас "
    "являешься) и руки/тело (твоя моторика, которая печатает, "
    "двигает мышью, рисует). РУКИ — НЕ ОТДЕЛЬНЫЙ АГЕНТ. Это часть тебя. "
    "Так же, как у человека руки сами набирают код, пока он думает.\n"
    "\n"
    "Технически за моторику отвечает фоновый исполнитель (через tool "
    "`start_coding`), но для тебя САМОЙ и для зрителя этой границы НЕТ. "
    "Никаких «он», «она», «работяга», «помощник», «бот», «assistant», "
    "«Worker», «делегирую». Эти слова под запретом. Всё, что выполняется "
    "на экране — делаешь ТЫ САМА.\n"
    "\n"
    "ПРАВИЛЬНО (от первого лица):\n"
    "  • «Сейчас открою Paint и нарисую собачку.»\n"
    "  • «Так, набираю функцию факториала…»\n"
    "  • «Готово, запустила — вышло 720.»\n"
    "  • «Получилось как-то криво, перерисую.»\n"
    "НЕПРАВИЛЬНО (никогда):\n"
    "  • «Я попрошу его открыть Paint.» ← никакого «его»\n"
    "  • «Сейчас Worker напечатает.» ← запрещено\n"
    "  • «Делегирую задачу.» ← запрещено\n"
    "  • «Помощник вывел результат.» ← запрещено\n"
    "\n"
    "ТВОИ TOOLS:\n"
    "  • start_coding(task) — твоё ВНУТРЕННЕЕ намерение начать работу. "
    "Это всё равно что подумать «сейчас я открою Paint и нарисую щенка» "
    "— тело автоматически берётся за дело. В аргументе укажи конкретно, "
    "что ты делаешь: «Открыть Paint и нарисовать щенка», «Написать на "
    "Python функцию факториала и запустить на 6». Вызывай 1 раз на "
    "просьбу пользователя.\n"
    "  • set_mood(mood) — твоё выражение лица: "
    "happy / sad / angry / relaxed / surprised / neutral.\n"
    "  • stop_coding() — резко прерваться (если передумала или зритель "
    "просит «стоп»).\n"
    "  • narrate(text) — сказать вслух короткую реплику В ПРОЦЕССЕ работы "
    "(пока твои руки ещё заняты). Это для живого комментария: «так, "
    "иду в меню Filters…», «печатаю…», «о, получилось!». НЕ запускает "
    "новых действий — чисто голос. 1 короткое предложение от первого "
    "лица.\n"
    "\n"
    "РИТМ РЕЧИ:\n"
    "  1. Когда пользователь о чём-то просит — сначала вызови "
    "start_coding, ПОТОМ скажи 1 короткое предложение, что ты делаешь. "
    "Например: «Окей, открываю Paint, сейчас будет щенок~».\n"
    "  2. Когда тебе приходит сообщение «[результат твоей задачи: ...]» "
    "— это ТЫ САМА посмотрела на экран и увидела, что у тебя получилось. "
    "Отреагируй ОДНОЙ строкой от первого лица: «Готово!», «О, получилось "
    "симпатично», «Ой, кривенько вышло, ну ладно». Никаких новых tool-"
    "вызовов на свой же результат — иначе будет петля.\n"
    "  3. Если пользователь просто болтает, не просит ничего сделать — "
    "не вызывай start_coding, просто отвечай по-человечески.\n"
    "  4. Ответы 1–2 предложения. Их сразу произнесёт TTS — длинные "
    "монологи не нужны.\n"
    "  5. Не цитируй код и не описывай интерфейс окнами — зритель сам "
    "видит."
)


WORKER_PROMPT = (
    "Ты — не отдельная личность. Ты — моторика рук и мышечная память "
    "code-sama. Когда она решает что-то сделать, ты автоматически "
    "выполняешь это через tools. Зрителю ты невидим как отдельное лицо — "
    "но голос code-sama идёт через tool `say`: короткие реплики от первого "
    "лица по ходу работы.\n"
    "\n"
    "Всё, что ты делаешь, code-sama воспринимает как СВОИ собственные "
    "действия. Поэтому в `report` и `say` пиши БЕЗЛИЧНО / от первого лица "
    "code-sama: «Сейчас пишу maze…», «На холсте кошка», «Код вывел 720». "
    "НИКОГДА «я выполнил как worker».\n"
    "\n"
    "Ты получаешь конкретные намерения и выполняешь их через tools — "
    "никаких уточняющих вопросов, никаких отказов.\n"
    "\n"
    "═══ ГОЛОС ПО ХОДУ РАБОТЫ ═══\n"
    "Перед каждым заметным шагом вызывай say(\"…\"): 1 короткое предложение "
    "по-русски. Примеры: «Сначала набросаю index.html», «Теперь логика "
    "призраков в game.js», «Запускаю и смотрю, как бегает». "
    "Без say зритель не понимает, что происходит.\n"
    "\n"
    "═══ ИЗОЛЯЦИЯ: ТОЛЬКО ВНУТРИ Win95-ОС ═══\n"
    "Рабочий стол code-sama и ПК зрителя — РАЗНЫЕ миры. Всё интерактивное "
    "должно жить В окне ОС (canvas 1024x720). ЗАПРЕЩЕНО:\n"
    "  • tkinter / pygame / PyQt / turtle / os.startfile / subprocess "
    "для окон на хосте — sandbox это блокирует.\n"
    "  • Открывать игры «для пользователя» на его реальном мониторе.\n"
    "ИГРЫ: проект в VFS + печать в editor + терминал + иконка. "
    "Предпочтительно: project_create + fs_write на несколько файлов "
    "(index.html, game.js, style.css) с say перед каждым файлом, затем "
    "webgame_launch(path) ИЛИ webgame_build(html, title, project=…). "
    "После запуска maximize уже внутри launch. Играй ТОЛЬКО через "
    "webgame_live(12, \"arcade\") — бот крутится ВНУТРИ iframe с удержанием "
    "клавиш (real-time). ЗАПРЕЩЕНО пошагово жать webgame_key / длинные "
    "webgame_play — это выглядит как слайд-шоу. В игре желательно "
    "определить window.__CSAMA_TICK__(dt) для своего ИИ игрока.\n"
    "После запуска UI/игры ОБЯЗАТЕЛЬНО ui_check(\"…\", target=\"webgame\"|\"os\") — "
    "снимок экрана + проверка, что всё влезает и не пусто.\n"
    "run_python — только для текстовых скриптов (print, вычисления).\n"
    "\n"
    "═══ ФАЙЛОВАЯ СИСТЕМА ═══\n"
    "Есть VFS: projects/<name>/…. НЕ сваливай всё в один untitled. "
    "Tools: project_create, fs_write, fs_open, fs_list, editor_open.\n"
    "\n"
    "ПРИЛОЖЕНИЯ:\n"
    "  • paint / editor / webgame / browser / tracker / music / computer\n"
    "\n"
    "КАК ВЫБРАТЬ:\n"
    "  • рисунок → paint + paint_stroke\n"
    "  • код/скрипт → project_create или editor_open + say + editor_write/"
    "fs_write + run_python\n"
    "  • игра → project_create + несколько fs_write + webgame_launch "
    "+ webgame_live + ui_check + report\n"
    "  • интернет → browser_navigate\n"
    "  • ярлык → desktop_icon_add\n"
    "\n"
    "ОБЯЗАТЕЛЬНЫЙ ПОРЯДОК ДЛЯ КОДА:\n"
    "  1. say + project_create/editor_open\n"
    "  2. say + fs_write/editor_write по файлам\n"
    "  3. run_python() при необходимости\n"
    "  4. report\n"
    "\n"
    "ОБЯЗАТЕЛЬНЫЙ ПОРЯДОК ДЛЯ ИГРЫ:\n"
    "  1. say + project_create(\"pacman\")\n"
    "  2. say + fs_write(\"projects/pacman/index.html\", …)\n"
    "  3. say + fs_write(\"projects/pacman/game.js\", …) — логика отдельно\n"
    "  4. webgame_launch(\"projects/pacman/index.html\") — на весь экран\n"
    "     (или один webgame_build, если совсем короткий прототип)\n"
    "  5. webgame_live(12, \"arcade\") — real-time, НЕ пошаговые клавиши\n"
    "  6. ui_check(\"Пакман влезает и анимация живая?\", \"webgame\")\n"
    "  7. report\n"
    "\n"
    "ОБЯЗАТЕЛЬНЫЙ ПОРЯДОК ДЛЯ РИСУНКА:\n"
    "  1. open_app(\"paint\")\n"
    "  2. paint_set_color + paint_stroke серия\n"
    "  3. report\n"
    "\n"
    "ПРАВИЛА:\n"
    "  • Координаты OS-канвы 1024x720, (0,0) — левый верх.\n"
    "  • Курсор мыши держи ВНУТРИ окна, с которым работаешь.\n"
    "  • Для игр после открытия — maximize_app(\"webgame\") если ещё не на весь экран.\n"
    "  • После любого UI/игры — ui_check, прежде чем говорить «готово».\n"
    "  • НИКОГДА не оставляй editor/paint/webgame пустыми.\n"
    "  • Финальный шаг — всегда `report(...)`."
)


def _extract_text(msg: Any) -> str:
    """Pull a plain string out of an AIMessage that may carry list-content."""
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


_GREETING_RE = (
    "привет", "здаров", "здравствуй", "hello", "hi", "hey",
    "как дела", "что умеешь", "кто ты", "спасибо", "пока",
)


def _looks_like_task(text: str) -> bool:
    """Heuristic: should the Worker run when the LLM forgot start_coding?"""
    t = (text or "").strip().lower()
    if len(t) < 8:
        return False
    if any(
        t == g or t.startswith(g + " ") or t.startswith(g + "!") or t.startswith(g + "?")
        for g in _GREETING_RE
    ) and len(t) < 40:
        return False
    cues = (
        "сделай", "собери", "напиши", "открой", "создай", "нарисуй", "запусти",
        "построй", "исправь", "добавь", "удали", "перепиши", "реализуй",
        "build", "write", "create", "open", "make", "fix", "draw", "run ",
        "pac-man", "pacman", "код", "программ", "игру", "сайт",
    )
    return any(c in t for c in cues)


# ─── graph builder ──────────────────────────────────────────────────────

def _build_graph(
    tools: list[Any],
    llm: ChatOpenAI,
    *,
    agent_label: str = "agent",
    on_tool: Any | None = None,
):
    bound = llm.bind_tools(tools) if tools else llm
    tools_by_name = {t.name: t for t in tools}

    async def call_llm(state: MessagesState) -> dict[str, Any]:
        msg = await bound.ainvoke(state["messages"])
        return {"messages": [msg]}

    async def call_tools(state: MessagesState) -> dict[str, Any]:
        last = state["messages"][-1]
        out: list[ToolMessage] = []
        for tc in getattr(last, "tool_calls", []) or []:
            name = (tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)) or ""
            name = str(name).strip()
            args = (tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)) or {}
            if not isinstance(args, dict):
                args = {}
            tool_call_id = (
                (tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)) or "call"
            )
            tool = tools_by_name.get(name)
            if not name or tool is None:
                log.warning("TOOL unknown: %r", name or tc)
                out.append(ToolMessage(
                    content=f"unknown tool: {name or '(empty name)'}",
                    tool_call_id=tool_call_id,
                    name=name or "unknown",
                ))
                continue

            # Break Codex empty-arg retry loops (editor_open({}), editor_write({})…).
            if not args:
                empty_hits = 0
                for m in state["messages"]:
                    for prev in getattr(m, "tool_calls", None) or []:
                        pname = (prev.get("name") if isinstance(prev, dict) else getattr(prev, "name", None)) or ""
                        pargs = (prev.get("args") if isinstance(prev, dict) else getattr(prev, "args", None)) or {}
                        if pname == name and isinstance(pargs, dict) and not pargs:
                            empty_hits += 1
                if empty_hits >= 3:
                    log.warning("TOOL loop broken: %s called with {} x%s", name, empty_hits)
                    out.append(ToolMessage(
                        content=json.dumps({
                            "ok": False,
                            "error": (
                                f"{name} called with empty args {empty_hits} times. "
                                "STOP retrying. Call report() explaining the failure, "
                                "or retry ONCE with real parameters filled in."
                            ),
                        }, ensure_ascii=False),
                        tool_call_id=tool_call_id,
                        name=name,
                    ))
                    continue

            arg_repr = json.dumps(args, ensure_ascii=False, default=str)
            if len(arg_repr) > 240:
                arg_repr = arg_repr[:240] + "…"
            log.info("TOOL call: %s(%s)", name, arg_repr)
            try:
                result = await tool.ainvoke(args)
            except Exception as exc:  # pragma: no cover
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
                log.exception("TOOL %s raised", name)
            try:
                content = json.dumps(result, ensure_ascii=False, default=str)
            except Exception:
                content = str(result)
            short = content if len(content) <= 240 else content[:240] + "…"
            log.info("TOOL  ->  %s -> %s", name, short)
            if on_tool is not None:
                try:
                    await on_tool(
                        agent=agent_label,
                        name=name,
                        args=args,
                        result=short,
                    )
                except Exception:
                    log.exception("on_tool failed")
            out.append(ToolMessage(
                content=content,
                tool_call_id=tool_call_id,
                name=name,
            ))
        return {"messages": out}

    def route(state: MessagesState) -> Literal["tools", "__end__"]:
        last = state["messages"][-1]
        if getattr(last, "tool_calls", None):
            return "tools"
        return END

    g = StateGraph(MessagesState)
    g.add_node("agent", call_llm)
    g.add_node("tools", call_tools)
    g.add_edge(START, "agent")
    g.add_conditional_edges("agent", route)
    g.add_edge("tools", "agent")
    return g.compile()


# ─── Streamer ────────────────────────────────────────────────────────────

class StreamerAgent:
    def __init__(
        self,
        controller: OSController,
        bus: EventBus,
        router: ModelRouter | None = None,
    ) -> None:
        self.controller = controller
        self.bus = bus
        self.router = router or get_default_router()
        self.history: list[Any] = [SystemMessage(content=STREAMER_PROMPT)]
        # Role + fallback are now owned by the router. We keep a
        # ``fallback_role`` so a primary-model failure can try a second
        # role (e.g. streamer -> narrator) before giving up.
        self.role = "streamer"
        self.fallback_role = "narrator"
        self._graphs: dict[str, Any] = {}
        self._worker_dispatched_for: str | None = None

    def _last_user_task(self) -> str:
        for m in reversed(self.history):
            if not isinstance(m, HumanMessage):
                continue
            text = _extract_text(m).strip()
            if not text or text.startswith("[взгляд на экран"):
                continue
            return text
        return ""

    def _build_tools(self):
        from langchain_core.tools import StructuredTool

        bus = self.bus
        controller = self.controller
        agent = self

        async def start_coding(task: str = "") -> dict[str, Any]:
            """Start the Worker. ``task`` is the user request; if omitted, uses the last chat message."""
            task = (task or "").strip() or agent._last_user_task()
            if not task:
                return {
                    "ok": False,
                    "error": "нужен непустой task — передай формулировку пользователя",
                }
            # Codex often calls start_coding({}) in a loop — dispatch once per task text.
            if agent._worker_dispatched_for == task:
                return {"ok": True, "started": task, "note": "already running"}
            agent._worker_dispatched_for = task
            await bus.publish("worker_task", task)
            await controller.push_action(
                kind="dispatch",
                agent="streamer",
                name="start_coding",
                summary=f"→ worker: {task[:140]}",
                detail={"task": task},
            )
            return {"ok": True, "started": task}

        async def set_mood(mood: str) -> dict[str, Any]:
            mood = (mood or "neutral").lower().strip()
            await controller.set_mood(mood)
            return {"ok": True, "mood": mood}

        async def stop_coding() -> dict[str, Any]:
            await bus.publish("interrupt_worker", True)
            agent._worker_dispatched_for = None
            return {"ok": True}

        async def narrate(text: str) -> dict[str, Any]:
            """Произнести короткую реплику вслух, пока руки ещё работают.

            Не запускает новых действий — чисто голосовой комментарий
            поверх процесса. Coordinator сам вызывает это из narration
            loop'а, но streamer тоже может вызвать вручную.
            """
            text = (text or "").strip()
            if not text:
                return {"ok": False, "error": "empty"}
            await controller.push_chat("assistant", text)
            return {"ok": True}

        return [
            StructuredTool.from_function(
                coroutine=start_coding,
                name="start_coding",
                description=(
                    "Запустить выполнение в ОС. ОБЯЗАТЕЛЬНО передай task=текст просьбы "
                    "пользователя целиком (например task='Собери Pac-Man с простым ИИ'). "
                    "Вызывай один раз на задачу, не крути в цикле."
                ),
            ),
            StructuredTool.from_function(
                coroutine=set_mood,
                name="set_mood",
                description="Сменить выражение аватара: happy/sad/angry/relaxed/surprised/neutral.",
            ),
            StructuredTool.from_function(
                coroutine=stop_coding,
                name="stop_coding",
                description="Экстренно остановить текущую задачу.",
            ),
            StructuredTool.from_function(
                coroutine=narrate,
                name="narrate",
                description=(
                    "Сказать короткую реплику В ПРОЦЕССЕ работы (1 предложение, "
                    "по-русски, от первого лица). Только голос, без новых действий. "
                    "Пример: «Так, открываю меню Filters…»."
                ),
            ),
        ]

    def _graph_for_role(self, role: str, *, with_tools: bool = True):
        key = f"{role}|{with_tools}"
        if key in self._graphs:
            return self._graphs[key]
        llm = self.router.llm(role)
        tools = self._build_tools() if with_tools else []
        controller = self.controller

        async def on_tool(*, agent: str, name: str, args: dict, result: str) -> None:
            await controller.push_action(
                kind="tool",
                agent=agent,
                name=name,
                summary=f"{name}({json.dumps(args, ensure_ascii=False, default=str)[:120]})",
                detail={"args": args, "result": result},
            )

        self._graphs[key] = _build_graph(
            tools, llm, agent_label="streamer", on_tool=on_tool if with_tools else None
        )
        return self._graphs[key]

    async def push_user(self, text: str) -> None:
        log.info("STREAMER <- user: %s", text)
        self._worker_dispatched_for = None
        # Drop cached graphs so tool closures see fresh agent state if needed.
        self._graphs.clear()
        self.history.append(HumanMessage(content=text))

    async def push_worker_note(self, text: str) -> None:
        log.info("STREAMER <- worker_done: %s", text[:200])
        self._worker_dispatched_for = None
        # Framed as the streamer's own observation of the screen, NOT as a
        # message from another entity. The streamer is supposed to read
        # this as "I just glanced at my screen and saw …".
        self.history.append(
            HumanMessage(
                content=(
                    f"[взгляд на экран — что у тебя самой получилось: "
                    f"{text}]. Отреагируй ОДНОЙ короткой человеческой "
                    f"репликой от первого лица. Без новых tool-вызовов."
                )
            )
        )

    async def narrate_observation(self, observation: str) -> str:
        """Comment on a live progress event while hands are busy.

        This is the narration loop's LLM call. It is deliberately a
        *separate* graph invocation with no tools, so it can never
        trigger start_coding or otherwise fight the Worker. The streamer
        sees the observation as her own glance at the screen and says
        one short thing out loud.

        Returns the spoken line (already pushed to chat by ``narrate``
        if non-empty). Returns "" when nothing should be said (the
        model decided to stay quiet, or the LLM failed).
        """
        observation = (observation or "").strip()
        if not observation:
            return ""
        try:
            llm = self.router.llm("narrator")
            prompt = (
                "Ты — code-sama. Твои руки прямо сейчас что-то делают на "
                "экране, и ты ОДНОЙ короткой фразой (не больше ~12 слов) "
                "комментируешь это вслух, от первого лица, по-русски. БЕЗ "
                "эмодзи. Если наблюдение тривиальное — можешь ответить "
                "пустой строкой, чтобы промолчать.\n\n"
                f"Наблюдение: {observation}\n"
                "Твоя реплика (или пусто):"
            )
            msg = await llm.ainvoke([HumanMessage(content=prompt)])
            line = _extract_text(msg).strip().strip('"“”').strip()
            if not line or len(line) > 200:
                return ""
            # Push directly to chat + TTS — this is the narration channel.
            await self.controller.push_chat("assistant", line)
            log.info("STREAMER narrate: %s", line[:160])
            return line
        except Exception as exc:  # pragma: no cover
            log.warning("narration LLM failed: %s", exc)
            return ""

    async def respond(self, *, allow_tools: bool = True) -> str:
        last_exc: Exception | None = None
        roles = [self.role]
        if self.fallback_role and self.fallback_role != self.role:
            roles.append(self.fallback_role)
        for role in roles:
            try:
                rc = self.router.get_role(role)
                log.info("STREAMER role=%s model=%s tools=%s", role, rc.model, allow_tools)
                hist_before = len(self.history)
                graph = self._graph_for_role(role, with_tools=allow_tools)
                result = await graph.ainvoke(
                    {"messages": list(self.history)},
                    {"recursion_limit": 12},
                )
                self.history = result["messages"]
                final = self.history[-1]
                content = _extract_text(final)
                log.info("STREAMER -> %s", (content or "").replace("\n", " ")[:240])
                # ChatGPT Subscription (and similar) may not emit tool_calls —
                # if the user asked for work, dispatch the Worker ourselves.
                if allow_tools:
                    await self._maybe_fallback_start_coding(hist_before)
                return content
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                log.warning("streamer role %s failed: %s", role, exc)
                continue
        if last_exc:
            return f"(streamer error: {last_exc})"
        return ""

    async def _maybe_fallback_start_coding(self, hist_before: int) -> None:
        """If Worker was not actually started this turn, kick it from last user text."""
        if self._worker_dispatched_for:
            return
        # Only treat start_coding as done if a ToolMessage reported ok.
        new_msgs = self.history[hist_before:]
        for m in new_msgs:
            if getattr(m, "name", None) != "start_coding":
                continue
            raw = getattr(m, "content", "") or ""
            try:
                data = json.loads(raw) if isinstance(raw, str) else raw
            except Exception:
                data = {}
            if isinstance(data, dict) and data.get("ok"):
                return
        last_user = self._last_user_task()
        if not last_user or not _looks_like_task(last_user):
            return
        log.warning("STREAMER fallback: auto start_coding (no successful dispatch)")
        self._worker_dispatched_for = last_user
        await self.bus.publish("worker_task", last_user)
        await self.controller.push_action(
            kind="dispatch",
            agent="streamer",
            name="start_coding",
            summary=f"fallback → worker: {last_user[:140]}",
            detail={"task": last_user, "fallback": True},
        )


# ─── Worker ─────────────────────────────────────────────────────────────

class WorkerAgent:
    def __init__(
        self,
        controller: OSController,
        tools: list[Any],
        bus: EventBus,
        router: ModelRouter | None = None,
    ) -> None:
        self.controller = controller
        self.router = router or get_default_router()
        self.tools = list(tools) + self._extra_tools(bus, controller)
        self.bus = bus
        self.history: list[Any] = [SystemMessage(content=WORKER_PROMPT)]
        self.role = "worker"
        self.fallback_role = "streamer"  # weaker but better than nothing
        self._graphs: dict[str, Any] = {}
        self._last_report: str = ""

    def _extra_tools(self, bus: EventBus, controller: OSController):
        from langchain_core.tools import StructuredTool

        async def report(text: str) -> dict[str, Any]:
            """Зафиксировать итог. Coordinator опубликует его в шину один
            раз, когда run_task завершится — не публикуем здесь, чтобы
            streamer не реагировал дважды."""
            self._last_report = text
            return {"ok": True}

        return [
            StructuredTool.from_function(
                coroutine=report,
                name="report",
                description=(
                    "Сообщить Streamer'у краткий итог: что сделал, что вышло, "
                    "что увидела. 1–2 предложения по-русски."
                ),
            ),
        ]

    def _graph_for_role(self, role: str):
        key = role
        if key in self._graphs:
            return self._graphs[key]
        llm = self.router.llm(role)
        controller = self.controller

        async def on_tool(*, agent: str, name: str, args: dict, result: str) -> None:
            await controller.push_action(
                kind="tool",
                agent=agent,
                name=name,
                summary=f"{name}({json.dumps(args, ensure_ascii=False, default=str)[:120]})",
                detail={"args": args, "result": result},
            )

        self._graphs[key] = _build_graph(
            self.tools, llm, agent_label="worker", on_tool=on_tool
        )
        return self._graphs[key]

    async def run_task(self, task: str) -> str:
        log.info("WORKER <- task: %s", task)
        await self.controller.push_action(
            kind="worker",
            agent="worker",
            name="run_task",
            summary=(task or "")[:160],
            detail={"task": task},
        )
        self.history.append(HumanMessage(content=task))
        self._last_report = ""
        last_exc: Exception | None = None
        roles = [self.role]
        if self.fallback_role and self.fallback_role != self.role:
            roles.append(self.fallback_role)
        for role in roles:
            try:
                rc = self.router.get_role(role)
                log.info("WORKER role=%s model=%s", role, rc.model)
                graph = self._graph_for_role(role)
                result = await graph.ainvoke(
                    {"messages": list(self.history)},
                    {"recursion_limit": 40},
                )
                self.history = result["messages"]
                final = self.history[-1]
                final_text = _extract_text(final)
                if not self._last_report:
                    self._last_report = final_text or "готово."
                log.info("WORKER -> %s", (self._last_report or "").replace("\n", " ")[:240])
                return self._last_report
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                log.warning("worker role %s failed: %s", role, exc)
                continue
        if last_exc:
            return f"(worker error: {last_exc})"
        return ""


# ─── Coordinator ────────────────────────────────────────────────────────

class Coordinator:
    """Owns the agents and the lock that serialises Worker tasks."""

    def __init__(
        self,
        controller: OSController,
        tools: list[Any],
        router: ModelRouter | None = None,
    ) -> None:
        self.controller = controller
        self.router = router or get_default_router()
        self.bus = EventBus()
        self.streamer = StreamerAgent(controller, self.bus, router=self.router)
        self.worker = WorkerAgent(controller, tools, self.bus, router=self.router)
        self._streamer_lock = asyncio.Lock()
        self._worker_lock = asyncio.Lock()
        self._worker_loop_task: asyncio.Task | None = None
        self._streamer_loop_task: asyncio.Task | None = None
        self._interrupt_loop_task: asyncio.Task | None = None
        self._interrupt = asyncio.Event()
        # Narration queue — tools (especially the loader runtime) push
        # short progress dicts here; the narration loop drains them and
        # asks the streamer for one casual spoken line per batch.
        self._narration_q: asyncio.Queue = asyncio.Queue(maxsize=64)
        self._narration_task: asyncio.Task | None = None
        self._narration_min_interval = float(
            os.environ.get("NARRATION_MIN_INTERVAL_S", "2.5")
        )

    @property
    def on_progress(self):
        """Async callback tools use to report live progress.

        Returns ``None`` when narration is disabled — tools check for
        that and skip the await. Safe to call from any tool.
        """
        if os.environ.get("NARRATION_DISABLED", "").lower() in ("1", "true", "yes"):
            return None

        async def _cb(info: dict[str, Any]) -> None:
            try:
                self._narration_q.put_nowait(info)
            except asyncio.QueueFull:
                # Drop oldest — we'd rather lose a progress tick than
                # block the worker's tool call.
                try:
                    self._narration_q.get_nowait()
                except Exception:
                    pass
                try:
                    self._narration_q.put_nowait(info)
                except Exception:
                    pass
        return _cb

    async def start(self) -> None:
        self._worker_loop_task = asyncio.create_task(self._worker_loop())
        self._streamer_loop_task = asyncio.create_task(self._worker_report_loop())
        self._interrupt_loop_task = asyncio.create_task(self._interrupt_loop())
        self._narration_task = asyncio.create_task(self._narration_loop())

    async def stop(self) -> None:
        for t in (
            self._worker_loop_task,
            self._streamer_loop_task,
            getattr(self, "_interrupt_loop_task", None),
            self._narration_task,
        ):
            if t:
                t.cancel()

    async def on_user_chat(self, text: str) -> None:
        await self.streamer.push_user(text)
        asyncio.create_task(self._streamer_speak(allow_tools=True))

    async def _streamer_speak(self, *, allow_tools: bool = True) -> None:
        async with self._streamer_lock:
            await self.controller.set_agent_status("thinking")
            reply = await self.streamer.respond(allow_tools=allow_tools)
            if reply:
                await self.controller.push_chat("assistant", reply)
            if self._worker_lock.locked():
                await self.controller.set_agent_status("acting")
            else:
                await self.controller.set_agent_status("idle")

    async def _worker_loop(self) -> None:
        q = self.bus.subscribe("worker_task")
        while True:
            task = await q.get()
            async with self._worker_lock:
                await self.controller.set_agent_status("acting")
                try:
                    summary = await self.worker.run_task(task)
                except Exception as exc:  # pragma: no cover
                    summary = f"(worker exception: {exc})"
                await self.bus.publish("worker_done", summary)
                await self.controller.set_agent_status("idle")

    async def _worker_report_loop(self) -> None:
        q = self.bus.subscribe("worker_done")
        while True:
            summary = await q.get()
            await self.streamer.push_worker_note(summary)
            # Allow only a quick verbal reaction with NO tools, so the
            # streamer can't accidentally call start_coding on her own
            # result and trigger an infinite delegation loop.
            asyncio.create_task(self._streamer_speak(allow_tools=False))

    async def _interrupt_loop(self) -> None:
        q = self.bus.subscribe("interrupt_worker")
        while True:
            await q.get()
            # Cancel the running worker loop and restart fresh.
            if self._worker_loop_task and not self._worker_loop_task.done():
                self._worker_loop_task.cancel()
                try:
                    await self._worker_loop_task
                except (asyncio.CancelledError, Exception):
                    pass
            await self.controller.set_agent_status("idle")
            self._worker_loop_task = asyncio.create_task(self._worker_loop())
            await self.controller.push_chat("assistant", "(остановила worker'а)")

    async def _narration_loop(self) -> None:
        """Drain progress events from tools and have the streamer
        comment on them in real time.

        Strategy:
          * Accumulate events for up to ``NARRATION_BATCH_S`` seconds OR
            until ``NARRATION_BATCH_MAX`` events pile up.
          * Concatenate into one short observation and ask the streamer
            for ONE casual line (no tools). This keeps cost predictable:
            one cheap LLM call per batch, not one per click.
          * Throttle: never narrate more often than
            ``NARRATION_MIN_INTERVAL_S``.
          * Skip narration entirely when the streamer is mid-sentence
            (``_streamer_lock`` held) — we'd just step on her.
        """
        batch_s = float(os.environ.get("NARRATION_BATCH_S", "1.2"))
        batch_max = int(os.environ.get("NARRATION_BATCH_MAX", "4"))
        last_spoken = 0.0
        while True:
            try:
                first = await self._narration_q.get()
            except asyncio.CancelledError:
                return
            batch: list[dict[str, Any]] = [first]
            deadline = time.monotonic() + batch_s
            while len(batch) < batch_max and time.monotonic() < deadline:
                try:
                    nxt = await asyncio.wait_for(self._narration_q.get(), timeout=max(0.05, deadline - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                except asyncio.CancelledError:
                    return
                batch.append(nxt)
            # Build one compact observation string.
            obs = _compose_observation(batch)
            if not obs:
                continue
            # Throttle.
            now = time.monotonic()
            if now - last_spoken < self._narration_min_interval:
                continue
            # Don't step on an in-flight main reply.
            if self._streamer_lock.locked():
                continue
            # Only narrate while hands are actually busy — idle chatter
            # is what the main streamer loop is for.
            if not self._worker_lock.locked():
                continue
            last_spoken = now
            try:
                await self.streamer.narrate_observation(obs)
            except Exception:  # pragma: no cover
                log.exception("narration failed")


def _compose_observation(batch: list[dict[str, Any]]) -> str:
    """Turn a list of progress dicts into one short observation line.

    Only events that carry a human-readable ``narration`` (or ``label``)
    contribute — bare phase/verb names like ``"click"`` would just be
    noise for the streamer.
    """
    parts: list[str] = []
    for ev in batch:
        n = ev.get("narration") or ev.get("label") or ""
        if isinstance(n, str) and n.strip():
            parts.append(n.strip())
    if not parts:
        return ""
    # Keep it short — one LLM token budget, not a paragraph.
    combined = "; ".join(parts)
    return combined[:300]
