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
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from .cursor import plan_path, typing_delays, typing_delays_code


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
        "path": "projects/scratch/untitled.py",
        "content": "",
        "caret": 0,
        "language": "python",
        "terminal_open": False,
        "terminal_lines": [],  # list of {kind: "cmd"|"out"|"err"|"info", text: str}
        "project": "projects/scratch",
        "open_files": ["projects/scratch/untitled.py"],
        "tree": [],  # [{name, path, kind: "file"|"dir"}]
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
        "path": "projects",
        "entries": [],
    },
    "webgame": {
        "title": "WebGame",
        "html": "",
        "keySeq": 0,
        "keyName": "",
        "keyType": "keydown",
        "holdSeq": 0,
        "holdKey": "",
        "livePlay": None,
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
    "webgame": "WebGame",
    "linux": "Linux App",
}

# Built-in desktop icons (left column). Dynamic shortcuts append after these.
BUILTIN_DESKTOP_ICONS: list[dict[str, Any]] = [
    {"id": "mycomputer", "label": "My Computer", "icon": "mycomputer", "app": "computer"},
    {"id": "recycle", "label": "Recycle Bin", "icon": "recycle", "app": None},
    {"id": "internet", "label": "Internet", "icon": "browser", "app": "browser"},
    {"id": "notepad", "label": "Notepad++", "icon": "editor", "app": "editor"},
    {"id": "paint", "label": "Paint", "icon": "paint", "app": "paint"},
    {"id": "music", "label": "Media Player", "icon": "music", "app": "music"},
    {"id": "tracker", "label": "Task Tracker", "icon": "tracker", "app": "tracker"},
]

# Icon grid geometry (must match .desk-icons CSS).
DESK_ICON_ORIGIN_X = 8
DESK_ICON_ORIGIN_Y = 8
DESK_ICON_W = 76
DESK_ICON_ROW = 92


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
        self.action_log: list[dict[str, Any]] = []
        self.last_action: dict[str, Any] | None = None
        self.desktop_icons: list[dict[str, Any]] = [
            dict(ico) for ico in BUILTIN_DESKTOP_ICONS
        ]
        self.vfs: dict[str, str] = {"projects/": "", "projects/scratch/": "", "projects/scratch/untitled.py": "# scratch\n", "Documents/": ""}
        self._type_follow_counter = 0
        self._webgame_autopilot: asyncio.Task | None = None
        self._shot_waiters: dict[str, asyncio.Future] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._max_action_log = 200
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
            "actionLog": self.action_log[-80:],
            "lastAction": self.last_action,
            "desktopIcons": list(self.desktop_icons),
        }

    # ─── Chat ─────────────────────────────────────────────────────────────
    async def push_chat(self, role: str, content: str) -> None:
        msg = {"role": role, "content": content, "ts": time.time()}
        self.chat_history.append(msg)
        await self._emit({"type": "chat", "message": msg})
        # Mirror chat into the action journal so the log shows dialogue too.
        await self.push_action(
            kind="chat",
            agent="streamer" if role == "assistant" else "user",
            summary=(content or "")[:160],
            detail={"role": role, "content": content},
        )

    async def push_action(
        self,
        *,
        kind: str,
        agent: str = "",
        name: str = "",
        summary: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        """Append an entry to the live action journal (tools / chats / status)."""
        entry = {
            "id": uuid.uuid4().hex[:10],
            "ts": time.time(),
            "kind": kind,
            "agent": agent,
            "name": name,
            "summary": summary,
            "detail": detail or {},
        }
        self.action_log.append(entry)
        if len(self.action_log) > self._max_action_log:
            self.action_log = self.action_log[-self._max_action_log:]
        if kind in ("tool", "worker", "status", "dispatch"):
            self.last_action = entry
            await self._emit({"type": "lastAction", "action": entry})
        await self._emit({"type": "actionLog", "entry": entry})

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
    async def type_text(self, text: str, *, pace: str = "normal") -> None:
        if not text:
            return
        if pace == "code":
            await self._type_text_code(text)
            return
        delays = typing_delays(text)
        for ch, dly in zip(text, delays):
            await asyncio.sleep(dly / 1000.0)
            await self._apply_char(ch)
            await self._emit({"type": "keypress", "char": ch})
            await self._maybe_follow_editor_caret()

    async def _type_text_code(self, text: str) -> None:
        """Readable typing for source — slower than before, caret stays in view."""
        # Per-char for moderate dumps; small chunks for long ones.
        if len(text) <= 900:
            delays = typing_delays_code(text)
            # Scale code delays up ~2.5x so viewers can follow.
            for ch, dly in zip(text, delays):
                await asyncio.sleep(max(0.018, dly * 2.4 / 1000.0))
                await self._apply_char(ch)
                await self._emit({"type": "keypress", "char": ch})
                await self._maybe_follow_editor_caret()
            return
        chunk = 5 if len(text) > 2500 else 3
        i = 0
        while i < len(text):
            piece = text[i : i + chunk]
            i += chunk
            await asyncio.sleep(random.uniform(0.045, 0.085))
            await self._apply_string(piece)
            await self._emit({"type": "keypress", "char": piece[-1]})
            await self._maybe_follow_editor_caret()

    async def jump_cursor(self, x: float, y: float) -> None:
        """Teleport cursor without path animation (for caret-follow)."""
        x = max(2, min(self.canvas_w - 2, float(x)))
        y = max(2, min(self.canvas_h - 2, float(y)))
        self.cursor = [x, y]
        await self._emit({"type": "cursorJump", "x": x, "y": y})

    async def _maybe_follow_editor_caret(self) -> None:
        """Keep the OS mouse inside the editor near the typing line."""
        self._type_follow_counter += 1
        if self._type_follow_counter % 40 != 0:
            return
        win = self.windows.get(self.focused_id or "")
        if not win or win.app != "editor":
            return
        x = win.x + int(win.w * 0.55) + random.randint(-18, 18)
        y = win.y + int(win.h * 0.62) + random.randint(-10, 10)
        x = max(win.x + 90, min(win.x + win.w - 24, x))
        y = max(win.y + 70, min(win.y + win.h - 50, y))
        await self.jump_cursor(x, y)

    async def press_key(self, key: str, *, pause: float = 0.08) -> None:
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
        # Forward keys into the in-OS HTML5 game iframe.
        # Directions are HELD (arcade style); Space/Enter are taps.
        if self.focused_id:
            win = self.windows.get(self.focused_id)
            if win and win.app == "webgame":
                hold_keys = {
                    "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown",
                    "Left", "Right", "Up", "Down", "a", "d", "w", "s",
                    "A", "D", "W", "S",
                }
                if key in hold_keys or key in {"", "Release", "None"}:
                    seq = int(win.app_state.get("holdSeq") or 0) + 1
                    await self.patch_app_state(win.id, {
                        "holdSeq": seq,
                        "holdKey": "" if key in {"", "Release", "None"} else key,
                    })
                else:
                    seq = int(win.app_state.get("keySeq") or 0) + 1
                    await self.patch_app_state(win.id, {
                        "keySeq": seq,
                        "keyName": key,
                        "keyType": "keydown",
                    })
        if pause > 0:
            await asyncio.sleep(pause)

    async def _apply_char(self, ch: str) -> None:
        await self._apply_string(ch)

    async def _apply_string(self, s: str) -> None:
        win = self.windows.get(self.focused_id or "")
        if not win or not s:
            return
        target = self.focused_target
        if win.app == "editor" and target == "editor":
            win.app_state["content"] = (win.app_state.get("content", "") or "") + s
            win.app_state["caret"] = len(win.app_state["content"])
            path = win.app_state.get("path") or win.app_state.get("filename")
            if path:
                self.vfs_write_silent(str(path), win.app_state["content"])
            await self._emit({"type": "appState", "id": win.id, "state": win.app_state})
        elif win.app == "browser" and target == "url":
            win.app_state["url_draft"] = (win.app_state.get("url_draft", "") or "") + s
            await self._emit({"type": "appState", "id": win.id, "state": win.app_state})
        elif win.app == "tracker" and target == "tracker_input":
            win.app_state["draft"] = (win.app_state.get("draft", "") or "") + s
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
            "editor": 700,
            "paint": 560,
            "music": 360,
            "tracker": 360,
            "computer": 540,
            "webgame": 1000,
            "linux": 800,
        }[app]
        h = height or {
            "browser": 500,
            "editor": 520,
            "paint": 420,
            "music": 260,
            "tracker": 360,
            "computer": 360,
            "webgame": 640,
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
            "webgame": "webgame",
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
                "webgame": "webgame",
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

    async def ensure_maximized(self, window_id: str) -> None:
        w = self.windows.get(window_id)
        if not w:
            return
        if w.minimized:
            w.minimized = False
            await self._emit({"type": "windowRestore", "id": window_id})
        if not w.maximized:
            w.maximized = True
            await self._emit({"type": "windowMaximize", "id": window_id, "maximized": True})

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

    # ─── Desktop icons ────────────────────────────────────────────────────
    def desk_icon_coords(self, index: int) -> tuple[int, int]:
        """Center of the icon at the given 0-based column-row index."""
        cx = DESK_ICON_ORIGIN_X + DESK_ICON_W // 2
        cy = DESK_ICON_ORIGIN_Y + 22 + index * DESK_ICON_ROW
        return cx, cy

    def find_desktop_icon(self, id_or_label: str) -> tuple[int, dict[str, Any]] | None:
        needle = (id_or_label or "").strip().lower()
        if not needle:
            return None
        for i, ico in enumerate(self.desktop_icons):
            if str(ico.get("id", "")).lower() == needle:
                return i, ico
            if str(ico.get("label", "")).lower() == needle:
                return i, ico
        return None

    async def _emit_desktop_icons(self) -> None:
        await self._emit({"type": "desktopIcons", "icons": list(self.desktop_icons)})

    async def desktop_icon_add(
        self,
        label: str,
        *,
        app: str | None = "webgame",
        icon: str = "game",
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        label = (label or "Shortcut").strip()[:40] or "Shortcut"
        existing = self.find_desktop_icon(label)
        if existing:
            idx, ico = existing
            ico = {
                **ico,
                "app": app,
                "icon": icon or ico.get("icon") or "game",
                "payload": payload or ico.get("payload") or {},
            }
            self.desktop_icons[idx] = ico
            await self._emit_desktop_icons()
            return {"ok": True, "id": ico["id"], "index": idx, "replaced": True}
        ico_id = "ico_" + uuid.uuid4().hex[:8]
        entry = {
            "id": ico_id,
            "label": label,
            "icon": icon or "game",
            "app": app,
            "payload": payload or {},
        }
        self.desktop_icons.append(entry)
        await self._emit_desktop_icons()
        return {"ok": True, "id": ico_id, "index": len(self.desktop_icons) - 1, "replaced": False}

    async def desktop_icon_click(self, id_or_label: str, *, double: bool = True) -> dict[str, Any]:
        found = self.find_desktop_icon(id_or_label)
        if not found:
            return {"ok": False, "error": "icon_not_found"}
        idx, ico = found
        x, y = self.desk_icon_coords(idx)
        await self.move_cursor(x, y)
        await self.click(x, y)
        if double:
            await asyncio.sleep(0.12)
            await self.click(x, y)
        return {"ok": True, "id": ico["id"], "index": idx, "icon": ico}

    async def stop_webgame_autopilot(self) -> None:
        t = self._webgame_autopilot
        self._webgame_autopilot = None
        if t and not t.done():
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        win = self.find_window_by_app("webgame")
        if win:
            seq = int(win.app_state.get("holdSeq") or 0) + 1
            await self.patch_app_state(win.id, {
                "holdSeq": seq,
                "holdKey": "",
                "livePlay": None,
            })

    async def request_screenshot(self, *, target: str = "os", timeout: float = 6.0) -> str | None:
        """Ask connected browsers for a JPEG of the OS canvas or webgame."""
        if not self._subscribers:
            return None
        rid = uuid.uuid4().hex[:10]
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._shot_waiters[rid] = fut
        await self._emit({"type": "screenshotRequest", "id": rid, "target": target})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._shot_waiters.pop(rid, None)

    def fulfill_screenshot(self, request_id: str, image_b64: str | None) -> None:
        fut = self._shot_waiters.get(request_id or "")
        if fut and not fut.done():
            fut.set_result(image_b64 or "")

    # ─── Virtual filesystem ───────────────────────────────────────────────
    @staticmethod
    def vfs_norm(path: str) -> str:
        p = (path or "").replace("\\", "/").strip()
        while "//" in p:
            p = p.replace("//", "/")
        return p.strip("/")

    def vfs_write_silent(self, path: str, content: str) -> str:
        n = self.vfs_norm(path)
        if not n or n.endswith("/"):
            raise ValueError("invalid file path")
        parts = n.split("/")
        for i in range(1, len(parts)):
            d = "/".join(parts[:i]) + "/"
            self.vfs.setdefault(d, "")
        self.vfs[n] = content if content is not None else ""
        return n

    def vfs_mkdir(self, path: str) -> str:
        n = self.vfs_norm(path)
        if not n:
            return ""
        parts = n.strip("/").split("/")
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i]) + "/"
            self.vfs.setdefault(d, "")
        return "/".join(parts)

    def vfs_read(self, path: str) -> str | None:
        n = self.vfs_norm(path)
        if n in self.vfs and not n.endswith("/"):
            return self.vfs[n]
        return None

    def vfs_list(self, path: str = "") -> list[dict]:
        n = self.vfs_norm(path)
        prefix = (n + "/") if n else ""
        names: dict[str, str] = {}
        for k in self.vfs:
            if prefix and not k.startswith(prefix):
                continue
            rest = k[len(prefix):] if prefix else k
            if not rest:
                continue
            name = rest.split("/")[0].rstrip("/")
            kind = "dir" if ("/" in rest.rstrip("/") or k.endswith("/")) else "file"
            if "/" in rest:
                kind = "dir"
            if name not in names or kind == "dir":
                names[name] = kind
        out = []
        for name in sorted(names, key=lambda s: (names[s] != "dir", s.lower())):
            child = f"{prefix}{name}" if prefix else name
            out.append({
                "name": name,
                "path": child.rstrip("/"),
                "kind": "folder" if names[name] == "dir" else "doc",
            })
        return out

    def vfs_tree(self, path: str = "projects", depth: int = 3) -> list[dict]:
        def walk(base: str, d: int) -> list[dict]:
            if d < 0:
                return []
            nodes = []
            for ent in self.vfs_list(base):
                node = {
                    "name": ent["name"],
                    "path": ent["path"],
                    "kind": "dir" if ent["kind"] == "folder" else "file",
                }
                if node["kind"] == "dir":
                    node["children"] = walk(ent["path"], d - 1)
                nodes.append(node)
            return nodes
        return walk(self.vfs_norm(path), depth)

    def editor_tree_for(self, project: str) -> list[dict]:
        return self.vfs_tree(project or "projects", depth=4)

    async def sync_computer_to_vfs(self, path: str | None = None) -> None:
        win = self.find_window_by_app("computer")
        if not win:
            return
        cur = path if path is not None else (win.app_state.get("path") or "projects")
        entries = self.vfs_list(cur)
        await self.patch_app_state(
            win.id, {"path": self.vfs_norm(cur) or "projects", "entries": entries}
        )

    async def sync_editor_tree(self) -> None:
        win = self.find_window_by_app("editor")
        if not win:
            return
        project = win.app_state.get("project") or "projects"
        tree = self.editor_tree_for(str(project))
        open_files = list(win.app_state.get("open_files") or [])
        await self.patch_app_state(win.id, {"tree": tree, "open_files": open_files})
