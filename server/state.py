"""Authoritative OS state + the controller every tool calls into.

The frontend is a thin renderer. It connects to `/ws`, receives a `snapshot`,
then patches its local copy from a stream of typed events. Tool calls run on
the asyncio loop and *actually sleep* during animations — so a 600ms cursor
hop blocks the agent for 600ms, which is what makes the playback feel real.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from .cursor import plan_path, typing_delays


CANVAS_W = 1024
CANVAS_H = 720
TASKBAR_H = 32

APP_DEFAULTS: dict[str, dict[str, Any]] = {
    "browser": {
        "url": "https://example.com",
        "title": "Browser",
        "frame": None,
        "loading": False,
    },
    "editor": {
        "filename": "untitled.py",
        "content": "",
        "caret": 0,
        "language": "python",
        "terminal_open": False,
        "terminal_lines": [],  # list of {kind: "cmd"|"out"|"err"|"info", text: str}
    },
    "paint": {
        "strokes": [],  # list of {color, size, points: [[x,y]...]}
        "color": "#222222",
        "size": 4,
    },
    "music": {
        "playlist": [
            {"title": "Lo-fi Beat", "src": "/assets/music/lofi.mp3"},
            {"title": "Synthwave", "src": "/assets/music/synth.mp3"},
        ],
        "index": 0,
        "playing": False,
    },
    "tracker": {
        "tasks": [],  # list of {id, text, done}
    },
    "computer": {
        "path": "C:\\",
        "entries": [
            {"name": "Program Files", "kind": "folder"},
            {"name": "Windows", "kind": "folder"},
            {"name": "Users", "kind": "folder"},
            {"name": "Documents", "kind": "folder"},
            {"name": "autoexec.bat", "kind": "doc"},
            {"name": "config.sys", "kind": "doc"},
            {"name": "readme.txt", "kind": "doc"},
        ],
    },
    # Loader apps (any Linux program installed via the harness) register
    # as synthetic "linux" windows. The app_state is patched by the
    # loader runtime as the program launches & streams frames.
    "linux": {
        "title": "Linux App",
        "app": "",
        "icon": "linux",
        "display": None,
        "width": 800,
        "height": 540,
        "frame": None,
        "status": "idle",
        "error": None,
    },
}

WINDOW_TITLES = {
    "browser": "Internet",
    "editor": "Notepad++",
    "paint": "Paint",
    "music": "Media Player",
    "tracker": "Task Tracker",
    "computer": "My Computer",
    "linux": "Linux App",
}


@dataclass
class Window:
    id: str
    app: str
    title: str
    x: int
    y: int
    w: int
    h: int
    z: int = 1
    minimized: bool = False
    maximized: bool = False
    app_state: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "app": self.app,
            "title": self.title,
            "x": self.x,
            "y": self.y,
            "w": self.w,
            "h": self.h,
            "z": self.z,
            "minimized": self.minimized,
            "maximized": self.maximized,
            "appState": self.app_state,
        }


class OSController:
    def __init__(self) -> None:
        self.canvas_w = CANVAS_W
        self.canvas_h = CANVAS_H
        self.cursor = [CANVAS_W // 2, CANVAS_H // 2]
        self.windows: dict[str, Window] = {}
        self.window_order: list[str] = []  # back -> front
        self.focused_id: str | None = None
        self.focused_target: str | None = None  # e.g. "url", "editor", "search"
        self.next_z = 1
        self.chat_history: list[dict[str, str]] = []
        self.agent_status = "idle"
        self.mood = "neutral"
        self._subscribers: set[asyncio.Queue] = set()
        self._lock = asyncio.Lock()

    # ─── Subscriptions ────────────────────────────────────────────────────
    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2048)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    async def _emit(self, event: dict[str, Any]) -> None:
        dead: list[asyncio.Queue] = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.discard(q)

    def snapshot(self) -> dict[str, Any]:
        return {
            "type": "snapshot",
            "canvas": {"w": self.canvas_w, "h": self.canvas_h, "taskbar": TASKBAR_H},
            "cursor": {"x": self.cursor[0], "y": self.cursor[1]},
            "windows": [self.windows[i].to_dict() for i in self.window_order],
            "focusedId": self.focused_id,
            "focusedTarget": self.focused_target,
            "chat": self.chat_history,
            "agentStatus": self.agent_status,
            "mood": self.mood,
        }

    # ─── Chat ─────────────────────────────────────────────────────────────
    async def push_chat(self, role: str, content: str) -> None:
        msg = {"role": role, "content": content, "ts": time.time()}
        self.chat_history.append(msg)
        await self._emit({"type": "chat", "message": msg})

    async def set_agent_status(self, status: str) -> None:
        self.agent_status = status
        await self._emit({"type": "agentStatus", "status": status})

    async def set_mood(self, mood: str) -> None:
        self.mood = mood
        await self._emit({"type": "mood", "mood": mood})

    # ─── Cursor ───────────────────────────────────────────────────────────
    async def move_cursor(self, x: float, y: float, *, speed: float = 1200.0) -> None:
        x = max(2, min(self.canvas_w - 2, float(x)))
        y = max(2, min(self.canvas_h - 2, float(y)))
        legs = plan_path((self.cursor[0], self.cursor[1]), (x, y), speed_px_per_s=speed)
        if not legs:
            return
        payload_legs = [
            {
                "from": [round(l.from_x, 2), round(l.from_y, 2)],
                "to": [round(l.to_x, 2), round(l.to_y, 2)],
                "c1": [round(l.ctrl1_x, 2), round(l.ctrl1_y, 2)],
                "c2": [round(l.ctrl2_x, 2), round(l.ctrl2_y, 2)],
                "duration": l.duration_ms,
            }
            for l in legs
        ]
        await self._emit({"type": "cursorLegs", "legs": payload_legs})
        total = sum(l.duration_ms for l in legs) / 1000.0
        await asyncio.sleep(total)
        self.cursor = [x, y]

    async def click(self, x: float | None = None, y: float | None = None, *, button: str = "left") -> None:
        if x is not None and y is not None:
            await self.move_cursor(x, y)
        await self._emit({"type": "click", "x": self.cursor[0], "y": self.cursor[1], "button": button})
        await asyncio.sleep(0.12)
        # If a window is under the cursor, focus it.
        win = self._window_at(self.cursor[0], self.cursor[1])
        if win:
            await self.focus_window(win.id)

    # ─── Typing ───────────────────────────────────────────────────────────
    async def type_text(self, text: str) -> None:
        if not text:
            return
        delays = typing_delays(text)
        for ch, dly in zip(text, delays):
            await asyncio.sleep(dly / 1000.0)
            await self._apply_char(ch)
            await self._emit({"type": "keypress", "char": ch})

    async def press_key(self, key: str) -> None:
        await self._emit({"type": "key", "key": key})
        if self.focused_id and self.focused_target == "editor":
            win = self.windows.get(self.focused_id)
            if win and win.app == "editor":
                if key == "Enter":
                    win.app_state["content"] = (win.app_state.get("content", "") or "") + "\n"
                    win.app_state["caret"] = len(win.app_state["content"])
                    await self._emit({"type": "appState", "id": win.id, "state": win.app_state})
                elif key == "Backspace":
                    c = win.app_state.get("content", "") or ""
                    win.app_state["content"] = c[:-1]
                    win.app_state["caret"] = len(win.app_state["content"])
                    await self._emit({"type": "appState", "id": win.id, "state": win.app_state})
        await asyncio.sleep(0.08)

    async def _apply_char(self, ch: str) -> None:
        win = self.windows.get(self.focused_id or "")
        if not win:
            return
        target = self.focused_target
        if win.app == "editor" and target == "editor":
            win.app_state["content"] = (win.app_state.get("content", "") or "") + ch
            win.app_state["caret"] = len(win.app_state["content"])
            await self._emit({"type": "appState", "id": win.id, "state": win.app_state})
        elif win.app == "browser" and target == "url":
            win.app_state["url_draft"] = (win.app_state.get("url_draft", "") or "") + ch
            await self._emit({"type": "appState", "id": win.id, "state": win.app_state})
        elif win.app == "tracker" and target == "tracker_input":
            win.app_state["draft"] = (win.app_state.get("draft", "") or "") + ch
            await self._emit({"type": "appState", "id": win.id, "state": win.app_state})

    # ─── Windows ──────────────────────────────────────────────────────────
    def _window_at(self, x: float, y: float) -> Window | None:
        for wid in reversed(self.window_order):
            w = self.windows[wid]
            if w.minimized:
                continue
            if w.x <= x <= w.x + w.w and w.y <= y <= w.y + w.h:
                return w
        return None

    def _next_slot(self, w: int, h: int) -> tuple[int, int]:
        # Cascade so each new window is offset from existing top-left corners
        # by ~50px — never spawn exactly on top of another window.
        used = {(self.windows[i].x, self.windows[i].y) for i in self.window_order}
        base_x = 50
        base_y = 40
        for step in range(0, 12):
            x = base_x + step * 60
            y = base_y + step * 40
            if (x, y) not in used:
                break
        else:
            x = base_x + (len(self.windows) * 40) % 300
            y = base_y + (len(self.windows) * 30) % 200
        x = max(8, min(self.canvas_w - w - 8, x))
        y = max(8, min(self.canvas_h - TASKBAR_H - h - 8, y))
        return x, y

    async def open_app(self, app: str, *, width: int | None = None, height: int | None = None) -> str:
        if app not in APP_DEFAULTS:
            raise ValueError(f"Unknown app: {app}")
        # If already open and not closed, just focus it.
        for wid in self.window_order:
            w = self.windows[wid]
            if w.app == app:
                if w.minimized:
                    w.minimized = False
                await self.focus_window(wid)
                return wid
        w = width or {
            "browser": 720,
            "editor": 620,
            "paint": 560,
            "music": 360,
            "tracker": 360,
            "computer": 540,
            "linux": 800,
        }[app]
        h = height or {
            "browser": 500,
            "editor": 420,
            "paint": 420,
            "music": 260,
            "tracker": 360,
            "computer": 360,
            "linux": 540,
        }[app]
        wx, wy = self._next_slot(w, h)
        wid = uuid.uuid4().hex[:8]
        self.next_z += 1
        win = Window(
            id=wid,
            app=app,
            title=WINDOW_TITLES.get(app, app.title()),
            x=wx,
            y=wy,
            w=w,
            h=h,
            z=self.next_z,
            app_state=dict(APP_DEFAULTS[app]),
        )
        self.windows[wid] = win
        self.window_order.append(wid)
        self.focused_id = wid
        # Set a default focus target per app.
        self.focused_target = {
            "browser": "url",
            "editor": "editor",
            "tracker": "tracker_input",
            "paint": "canvas",
            "music": None,
            "computer": "path",
            "linux": "canvas",
        }.get(app)
        await self._emit({"type": "windowOpen", "window": win.to_dict()})
        await self._emit({"type": "focus", "id": wid, "target": self.focused_target})
        return wid

    async def close_window(self, window_id: str) -> None:
        if window_id not in self.windows:
            return
        self.window_order = [w for w in self.window_order if w != window_id]
        self.windows.pop(window_id, None)
        if self.focused_id == window_id:
            self.focused_id = self.window_order[-1] if self.window_order else None
            self.focused_target = None
        await self._emit({"type": "windowClose", "id": window_id})
        if self.focused_id:
            await self._emit({"type": "focus", "id": self.focused_id, "target": self.focused_target})

    async def focus_window(self, window_id: str, target: str | None = None, *, animate: bool = False) -> None:
        if window_id not in self.windows:
            return
        w = self.windows[window_id]
        if animate and not w.minimized:
            # Move cursor to the title bar and emit a click so the user sees
            # "clicked to bring window forward" before z-index actually pops.
            tx = w.x + max(40, min(w.w - 40, w.w // 2))
            ty = w.y + 12
            await self.move_cursor(tx, ty)
            await self._emit({"type": "click", "x": tx, "y": ty, "button": "left"})
            await asyncio.sleep(0.12)
        if w.minimized:
            w.minimized = False
            await self._emit({"type": "windowRestore", "id": window_id})
        self.window_order = [i for i in self.window_order if i != window_id] + [window_id]
        self.next_z += 1
        w.z = self.next_z
        self.focused_id = window_id
        if target is None:
            target = {
                "browser": "url",
                "editor": "editor",
                "tracker": "tracker_input",
                "paint": "canvas",
                "music": None,
                "computer": "path",
                "linux": "canvas",
            }.get(w.app)
        self.focused_target = target
        await self._emit({"type": "focus", "id": window_id, "target": target, "z": self.next_z})

    async def bring_to_front(self, window_id: str, target: str | None = None) -> None:
        """Animated focus — used by high-level tools before they interact.

        If the window is already on top and visible, the cursor just glides to
        its title bar without an extra click ripple, so we keep the motion
        natural without an unnecessary extra event.
        """
        if window_id not in self.windows:
            return
        is_top = bool(self.window_order) and self.window_order[-1] == window_id
        await self.focus_window(window_id, target=target, animate=not is_top)

    async def move_window(self, window_id: str, x: int, y: int) -> None:
        w = self.windows.get(window_id)
        if not w:
            return
        w.x = max(0, min(self.canvas_w - 40, int(x)))
        w.y = max(0, min(self.canvas_h - TASKBAR_H - 40, int(y)))
        await self._emit({"type": "windowMove", "id": window_id, "x": w.x, "y": w.y})

    async def resize_window(self, window_id: str, w: int, h: int) -> None:
        win = self.windows.get(window_id)
        if not win:
            return
        win.w = max(220, min(self.canvas_w - 16, int(w)))
        win.h = max(140, min(self.canvas_h - TASKBAR_H - 16, int(h)))
        await self._emit({"type": "windowResize", "id": window_id, "w": win.w, "h": win.h})

    async def minimize_window(self, window_id: str) -> None:
        w = self.windows.get(window_id)
        if not w:
            return
        w.minimized = True
        await self._emit({"type": "windowMinimize", "id": window_id})

    async def maximize_window(self, window_id: str) -> None:
        w = self.windows.get(window_id)
        if not w:
            return
        w.maximized = not w.maximized
        await self._emit({"type": "windowMaximize", "id": window_id, "maximized": w.maximized})

    # ─── App state mutations ─────────────────────────────────────────────
    async def patch_app_state(self, window_id: str, patch: dict[str, Any]) -> None:
        w = self.windows.get(window_id)
        if not w:
            return
        w.app_state.update(patch)
        await self._emit({"type": "appState", "id": window_id, "state": w.app_state})

    def find_window_by_app(self, app: str) -> Window | None:
        for wid in reversed(self.window_order):
            w = self.windows[wid]
            if w.app == app:
                return w
        return None
