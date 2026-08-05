"""Agent tools — every action is async, animates on the OS canvas, and
returns a small JSON-ish dict so the LangGraph can reason about results.

Tools fall into two groups:
  • low-level — `move_mouse`, `click`, `type_text`, `press_key`
  • high-level — `open_app`, `browser_navigate`, `editor_write`,
    `paint_stroke`, `task_add`, `music_play`, `move_window`, ...

The high-level tools internally call low-level ones so the user sees the
cursor travel to the URL bar, the click ripple, then the URL typed out
character by character.
"""

from __future__ import annotations

import asyncio
import random
import re
import uuid
from typing import Any

from langchain_core.tools import StructuredTool

from .browser_engine import BrowserEngine
from .sandbox import Sandbox
from .state import OSController


# ─── geometry helpers ────────────────────────────────────────────────────

def _url_bar_coords(win) -> tuple[int, int]:
    return win.x + 110, win.y + 50


def _browser_viewport_rect(win) -> tuple[int, int, int, int]:
    return win.x + 8, win.y + 70, win.w - 16, win.h - 78


def _editor_area_coords(win) -> tuple[int, int]:
    return win.x + win.w // 2, win.y + win.h // 2


def _tracker_input_coords(win) -> tuple[int, int]:
    return win.x + win.w // 2, win.y + 50


def _tracker_add_button_coords(win) -> tuple[int, int]:
    return win.x + win.w - 40, win.y + 50


def _paint_canvas_rect(win) -> tuple[int, int, int, int]:
    return win.x + 8, win.y + 40, win.w - 16, win.h - 48


def _music_play_btn(win) -> tuple[int, int]:
    return win.x + 40, win.y + win.h - 40


def _music_next_btn(win) -> tuple[int, int]:
    return win.x + 100, win.y + win.h - 40


def make_tools(
    controller: OSController,
    browser: BrowserEngine,
    sandbox: Sandbox | None = None,
    *,
    loader: Any | None = None,
) -> list[StructuredTool]:
    sandbox = sandbox or Sandbox()
    # Loader is optional — the tools below no-op gracefully when it's
    # absent so the rest of the OS still works on a Windows dev box.
    loader = loader  # noqa: PLW0127 — alias for clarity in closures
    _progress_cb = (loader or {}).get("on_progress") if loader else None

    async def _note(text: str) -> None:
        """Feed the streamer narration loop."""
        if not text or not _progress_cb:
            return
        try:
            await _progress_cb({"narration": text, "label": text})
        except Exception:
            pass

    def _lang_from_path(path: str) -> str:
        p = (path or "").lower()
        if p.endswith((".html", ".htm")):
            return "html"
        if p.endswith(".css"):
            return "css"
        if p.endswith((".js", ".mjs")):
            return "javascript"
        if p.endswith(".json"):
            return "json"
        if p.endswith(".py"):
            return "python"
        return "plain"

    async def say(text: str) -> dict[str, Any]:
        """Speak a short first-person line NOW (while coding). Viewers hear progress."""
        text = (text or "").strip()
        if not text:
            return {"ok": False, "error": "empty"}
        text = text[:220]
        await controller.push_chat("assistant", text)
        await _note(text)
        return {"ok": True}

    async def move_mouse(x: float, y: float) -> dict[str, Any]:
        """Move the virtual mouse to absolute OS-canvas coordinates."""
        await controller.move_cursor(x, y)
        return {"ok": True, "cursor": controller.cursor}

    async def click(x: float | None = None, y: float | None = None, button: str = "left") -> dict[str, Any]:
        """Click at (x, y) or at the current cursor position."""
        await controller.click(x, y, button=button)
        return {"ok": True}

    async def type_text(text: str) -> dict[str, Any]:
        """Type a string into whatever the agent currently has focused.

        Honours human typing rhythm and produces per-keystroke events on the OS.
        Use it for URL bars, editor content, task names, etc.
        """
        await controller.type_text(text)
        return {"ok": True, "len": len(text)}

    async def press_key(key: str) -> dict[str, Any]:
        """Press a single non-character key. Examples: "Enter", "Backspace"."""
        await controller.press_key(key)
        return {"ok": True}

    # ─── window management ────────────────────────────────────────────
    async def open_app(app: str) -> dict[str, Any]:
        """Open one of the OS apps and bring it to focus.

        Allowed: "browser", "editor", "paint", "music", "tracker", "computer", "webgame".
        """
        app = app.lower().strip()
        if app not in {"browser", "editor", "paint", "music", "tracker", "computer", "webgame"}:
            return {"ok": False, "error": f"unknown app: {app}"}
        # Move cursor over to the matching desktop icon and double-click feel.
        icon_index = {
            "computer": 0, "browser": 2, "editor": 3, "paint": 4,
            "music": 5, "tracker": 6, "webgame": 3,
        }.get(app, 0)
        icon_x = 46
        icon_y = 28 + icon_index * 86
        await controller.move_cursor(icon_x, icon_y)
        await controller.click()
        await asyncio.sleep(0.18)
        wid = await controller.open_app(app)
        if app == "browser":
            await _refresh_browser_frame(wid)
        if app == "computer":
            await controller.sync_computer_to_vfs("projects")
        if app == "editor":
            await controller.sync_editor_tree()
        return {"ok": True, "app": app, "windowId": wid}

    async def close_app(app: str) -> dict[str, Any]:
        """Close the focused window of the given app (if open)."""
        win = controller.find_window_by_app(app)
        if not win:
            return {"ok": False, "error": "not_open"}
        # Click the X button location.
        await controller.click(win.x + win.w - 14, win.y + 14)
        await controller.close_window(win.id)
        return {"ok": True}

    async def focus_app(app: str) -> dict[str, Any]:
        """Bring an app's window to the front (use this before working with
        a covered window — never type into something visually under another
        window)."""
        win = controller.find_window_by_app(app)
        if not win:
            return {"ok": False, "error": "not_open"}
        await controller.bring_to_front(win.id)
        return {"ok": True, "windowId": win.id}

    async def move_window(app: str, x: int, y: int) -> dict[str, Any]:
        """Drag a window to a new top-left position on the OS canvas."""
        win = controller.find_window_by_app(app)
        if not win:
            return {"ok": False, "error": "not_open"}
        # Animate: move to title bar, click+hold-drag is faked as a single move event.
        await controller.move_cursor(win.x + 80, win.y + 14)
        await controller.move_window(win.id, x, y)
        # Slide cursor along.
        await controller.move_cursor(x + 80, y + 14)
        return {"ok": True, "x": x, "y": y}

    async def minimize_app(app: str) -> dict[str, Any]:
        """Minimize an app to the taskbar."""
        win = controller.find_window_by_app(app)
        if not win:
            return {"ok": False, "error": "not_open"}
        await controller.click(win.x + win.w - 46, win.y + 14)
        await controller.minimize_window(win.id)
        return {"ok": True}

    async def maximize_app(app: str) -> dict[str, Any]:
        """Maximize an app to fill the OS desktop (use for games)."""
        win = controller.find_window_by_app(app)
        if not win:
            return {"ok": False, "error": "not_open"}
        await controller.bring_to_front(win.id)
        await controller.ensure_maximized(win.id)
        return {"ok": True, "windowId": win.id}

    # ─── browser ──────────────────────────────────────────────────────
    async def _refresh_browser_frame(window_id: str, *, error: str | None = None) -> None:
        win = controller.windows.get(window_id)
        if not win:
            return
        b64 = await browser.screenshot_b64() if error is None else None
        await controller.patch_app_state(
            window_id,
            {
                "frame": b64,
                "loading": False,
                "url": await browser.current_url(),
                "error": error,
            },
        )

    async def browser_navigate(url: str) -> dict[str, Any]:
        """Navigate the in-OS browser. Accepts a URL, a bare hostname, or a
        plain natural-language query (auto-routed through DuckDuckGo).
        Opens the Browser app if it isn't already.
        """
        win = controller.find_window_by_app("browser")
        if not win:
            wid = await controller.open_app("browser")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id, target="url")
        bx, by = _url_bar_coords(win)
        await controller.click(bx, by)
        await controller.patch_app_state(win.id, {"url_draft": "", "error": None})
        await controller.type_text(url)
        await controller.press_key("Enter")
        await controller.patch_app_state(win.id, {"loading": True, "url": url, "error": None})
        result = await browser.navigate(url)
        if result.get("ok"):
            await _refresh_browser_frame(win.id)
        else:
            await _refresh_browser_frame(win.id, error=result.get("error", "navigation failed"))
        return result

    async def browser_read() -> dict[str, Any]:
        """Read visible text from the current browser page (truncated)."""
        text = await browser.get_text()
        return {"ok": True, "text": text, "url": await browser.current_url()}

    async def browser_back() -> dict[str, Any]:
        """Browser back navigation."""
        win = controller.find_window_by_app("browser")
        if win:
            await controller.click(win.x + 26, win.y + 50)
        await browser.back()
        if win:
            await _refresh_browser_frame(win.id)
        return {"ok": True}

    # ─── editor ───────────────────────────────────────────────────────
    async def editor_open(filename: str = "untitled.py") -> dict[str, Any]:
        """Open the code editor on a file path (creates empty file if needed)."""
        path = filename
        if "/" not in path.replace("\\", "/"):
            path = f"projects/scratch/{filename}"
        path = controller.vfs_norm(path)
        win = controller.find_window_by_app("editor")
        if not win:
            wid = await controller.open_app("editor")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id, target="editor")
        existing = controller.vfs_read(path)
        if existing is None:
            controller.vfs_write_silent(path, "")
            existing = ""
        project = "/".join(path.split("/")[:2]) if path.startswith("projects/") else "projects"
        open_files = list(win.app_state.get("open_files") or [])
        if path not in open_files:
            open_files.append(path)
        await controller.patch_app_state(win.id, {
            "filename": path.split("/")[-1],
            "path": path,
            "project": project,
            "content": existing,
            "caret": len(existing),
            "language": _lang_from_path(path),
            "open_files": open_files,
            "tree": controller.editor_tree_for(project),
        })
        return {"ok": True, "windowId": win.id, "path": path}

    async def editor_write(text: str, append: bool = True) -> dict[str, Any]:
        """Type text into the active editor file (set append=false to overwrite)."""
        win = controller.find_window_by_app("editor")
        if not win:
            wid = await controller.open_app("editor")
            win = controller.windows[wid]
        path = win.app_state.get("path") or win.app_state.get("filename") or "projects/scratch/untitled.py"
        await _note(f"пишу {path.split('/')[-1]}" + (f" (~{len(text)} симв.)" if len(text) > 80 else ""))
        ex, ey = _editor_area_coords(win)
        await controller.bring_to_front(win.id, target="editor")
        # Keep click inside the code pane (right of file tree).
        await controller.click(win.x + int(win.w * 0.55), win.y + int(win.h * 0.45))
        if not append:
            await controller.patch_app_state(win.id, {"content": "", "caret": 0})
            controller.vfs_write_silent(str(path), "")
        lang = (win.app_state.get("language") or _lang_from_path(str(path))).lower()
        pace = "code" if lang in ("html", "css", "js", "javascript", "json") or len(text) > 200 else "normal"
        await controller.type_text(text, pace=pace)
        return {"ok": True, "len": len(text), "pace": pace, "path": path}

    async def project_create(name: str) -> dict[str, Any]:
        """Create projects/<name>/ and open it in the editor sidebar."""
        name = (name or "app").strip().replace(" ", "-")
        name = "".join(ch for ch in name if ch.isalnum() or ch in "-_")[:40] or "app"
        project = controller.vfs_mkdir(f"projects/{name}")
        win = controller.find_window_by_app("editor")
        if not win:
            wid = await controller.open_app("editor")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id, target="editor")
        await controller.patch_app_state(win.id, {
            "project": project,
            "tree": controller.editor_tree_for(project),
            "open_files": [],
            "filename": "",
            "path": "",
            "content": "",
            "caret": 0,
        })
        await controller.sync_computer_to_vfs("projects")
        await _note(f"создаю проект {name}")
        return {"ok": True, "project": project}

    async def fs_write(path: str, content: str, append: bool = False) -> dict[str, Any]:
        """Write a file under the VFS with visible typing in the editor."""
        path = controller.vfs_norm(path)
        if not path or path.endswith("/"):
            return {"ok": False, "error": "bad path"}
        if not path.startswith("projects/") and not path.startswith("Documents/"):
            path = f"projects/{path}"
        await editor_open(path)
        return await editor_write(content, append=append)

    async def fs_open(path: str) -> dict[str, Any]:
        """Open an existing VFS file in the editor."""
        return await editor_open(path)

    async def fs_list(path: str = "projects") -> dict[str, Any]:
        """List files/folders in the virtual filesystem."""
        entries = controller.vfs_list(path)
        await controller.sync_computer_to_vfs(path)
        return {"ok": True, "path": controller.vfs_norm(path), "entries": entries}

    async def editor_set_language(language: str) -> dict[str, Any]:
        """Change syntax highlighting language: python, js, html, css, json, plain."""
        win = controller.find_window_by_app("editor")
        if not win:
            return {"ok": False, "error": "not_open"}
        await controller.patch_app_state(win.id, {"language": language})
        return {"ok": True, "language": language}

    # ─── code execution ──────────────────────────────────────────────
    async def _terminal_open(win) -> None:
        await controller.patch_app_state(win.id, {"terminal_open": True})

    async def _terminal_log(win, kind: str, text: str) -> None:
        if not text:
            return
        lines = list(win.app_state.get("terminal_lines", []))
        for raw in text.splitlines() or [""]:
            lines.append({"kind": kind, "text": raw})
        lines = lines[-400:]
        win.app_state["terminal_lines"] = lines
        await controller.patch_app_state(win.id, {"terminal_lines": lines})

    async def run_python(code: str | None = None, *, timeout: float = 8.0) -> dict[str, Any]:
        """Execute Python code in the sandbox and stream output to the in-OS terminal.

        If ``code`` is omitted, the current contents of the editor are run.
        Output appears in the terminal panel under the editor — both for the
        viewer and for follow-up tool calls (``stdout``/``stderr`` are also
        returned to the agent).
        """
        win = controller.find_window_by_app("editor")
        if not win:
            wid = await controller.open_app("editor")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id, target="editor")
        await _terminal_open(win)
        body = code if code is not None else (win.app_state.get("content") or "")
        if not body.strip():
            await _terminal_log(win, "info", "Editor is empty — nothing to run.")
            return {"ok": False, "error": "empty"}
        filename = win.app_state.get("filename") or "main.py"
        await _terminal_log(win, "cmd", f"$ python {filename}")
        result = await sandbox.run_python(body, filename=filename, timeout=timeout)
        if result.stdout:
            await _terminal_log(win, "out", result.stdout.rstrip("\n"))
        if result.stderr:
            await _terminal_log(win, "err", result.stderr.rstrip("\n"))
        tag = "ok" if result.ok else ("timeout" if result.timed_out else f"exit {result.exit_code}")
        await _terminal_log(win, "info", f"[{tag}] {result.elapsed:.2f}s")
        return {
            "ok": result.ok,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "elapsed": round(result.elapsed, 3),
            "timed_out": result.timed_out,
        }

    async def run_node(code: str | None = None, *, timeout: float = 8.0) -> dict[str, Any]:
        """Same as run_python but for Node.js. Requires `node` on PATH."""
        win = controller.find_window_by_app("editor")
        if not win:
            wid = await controller.open_app("editor")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id, target="editor")
        await _terminal_open(win)
        body = code if code is not None else (win.app_state.get("content") or "")
        if not body.strip():
            await _terminal_log(win, "info", "Editor is empty — nothing to run.")
            return {"ok": False, "error": "empty"}
        filename = win.app_state.get("filename") or "main.js"
        await _terminal_log(win, "cmd", f"$ node {filename}")
        result = await sandbox.run_node(body, filename=filename, timeout=timeout)
        if result.stdout:
            await _terminal_log(win, "out", result.stdout.rstrip("\n"))
        if result.stderr:
            await _terminal_log(win, "err", result.stderr.rstrip("\n"))
        tag = "ok" if result.ok else ("timeout" if result.timed_out else f"exit {result.exit_code}")
        await _terminal_log(win, "info", f"[{tag}] {result.elapsed:.2f}s")
        return {
            "ok": result.ok,
            "exit_code": result.exit_code,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "elapsed": round(result.elapsed, 3),
        }

    async def terminal_print(text: str, kind: str = "info") -> dict[str, Any]:
        """Append a line to the editor terminal. kind: info/out/err/cmd."""
        win = controller.find_window_by_app("editor")
        if not win:
            return {"ok": False, "error": "not_open"}
        await _terminal_open(win)
        await _terminal_log(win, kind, str(text))
        return {"ok": True}

    async def terminal_clear() -> dict[str, Any]:
        """Wipe all lines from the editor terminal."""
        win = controller.find_window_by_app("editor")
        if not win:
            return {"ok": False, "error": "not_open"}
        win.app_state["terminal_lines"] = []
        await controller.patch_app_state(win.id, {"terminal_lines": []})
        return {"ok": True}

    # ─── paint ────────────────────────────────────────────────────────
    async def paint_open() -> dict[str, Any]:
        """Open the Paint app (brings it to front if already open)."""
        win = controller.find_window_by_app("paint")
        if not win:
            wid = await controller.open_app("paint")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id, target="canvas")
        return {"ok": True, "windowId": win.id}

    async def paint_set_color(color: str) -> dict[str, Any]:
        """Set the paint brush color (hex like #ff0066)."""
        win = controller.find_window_by_app("paint")
        if not win:
            wid = await controller.open_app("paint")
            win = controller.windows[wid]
        await controller.patch_app_state(win.id, {"color": color})
        return {"ok": True}

    async def paint_stroke(points: list[list[float]], color: str = "#222222", size: int = 4) -> dict[str, Any]:
        """Draw a stroke through a list of (x, y) points (canvas-local, 0..1).

        Coordinates are 0..1 inside the paint canvas — (0,0) top-left, (1,1)
        bottom-right. The cursor animates along the path while the line is
        drawn.
        """
        win = controller.find_window_by_app("paint")
        if not win:
            wid = await controller.open_app("paint")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id, target="canvas")
        cx, cy, cw, ch = _paint_canvas_rect(win)
        absolute: list[list[float]] = []
        for p in points:
            if not isinstance(p, (list, tuple)) or len(p) < 2:
                continue
            ax = cx + max(0.0, min(1.0, float(p[0]))) * cw
            ay = cy + max(0.0, min(1.0, float(p[1]))) * ch
            absolute.append([ax, ay])
        if not absolute:
            return {"ok": False, "error": "no_points"}
        # Animate cursor through the stroke.
        await controller.move_cursor(absolute[0][0], absolute[0][1])
        await controller.click()
        for ax, ay in absolute[1:]:
            await controller.move_cursor(ax, ay, speed=1800)
        # Persist the stroke in the app state.
        strokes = list(win.app_state.get("strokes", []))
        strokes.append({"color": color, "size": size, "points": absolute})
        await controller.patch_app_state(win.id, {"strokes": strokes, "color": color, "size": size})
        return {"ok": True, "points": len(absolute)}

    async def paint_clear() -> dict[str, Any]:
        """Erase everything on the paint canvas."""
        win = controller.find_window_by_app("paint")
        if not win:
            return {"ok": False, "error": "not_open"}
        await controller.patch_app_state(win.id, {"strokes": []})
        return {"ok": True}

    # ─── in-OS HTML5 games (never host tkinter) ───────────────────────
    def _wrap_html_doc(html: str) -> str:
        html = (html or "").strip()
        if not html:
            return ""
        if "<html" not in html.lower():
            html = (
                "<!doctype html><html><head><meta charset=utf-8>"
                "<style>html,body{margin:0;background:#000;overflow:hidden}"
                "canvas{display:block;margin:0 auto}</style></head><body>"
                f"{html}</body></html>"
            )
        return html

    async def _launch_webgame_window(html: str, title: str) -> dict[str, Any]:
        html = _wrap_html_doc(html)
        if not html:
            return {"ok": False, "error": "empty html"}
        await controller.stop_webgame_autopilot()
        win = controller.find_window_by_app("webgame")
        if not win:
            wid = await controller.open_app("webgame")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id, target="webgame")
        win.title = (title or "WebGame")[:80]
        await controller.patch_app_state(win.id, {
            "html": html,
            "title": win.title,
            "keySeq": 0,
            "keyName": "",
        })
        await controller.ensure_maximized(win.id)
        return {"ok": True, "windowId": win.id, "title": win.title}

    def _inline_project_assets(html: str, base_dir: str) -> str:
        """Inline relative <script src> / <link href> from VFS into the HTML."""

        def repl_script(m):
            src = m.group(1)
            if src.startswith(("http:", "https:", "//", "data:")):
                return m.group(0)
            path = controller.vfs_norm(f"{base_dir}/{src}")
            body = controller.vfs_read(path)
            if body is None:
                return m.group(0)
            return f"<script>\n{body}\n</script>"

        def repl_link(m):
            href = m.group(1)
            if href.startswith(("http:", "https:", "//", "data:")):
                return m.group(0)
            path = controller.vfs_norm(f"{base_dir}/{href}")
            body = controller.vfs_read(path)
            if body is None:
                return m.group(0)
            return f"<style>\n{body}\n</style>"

        html = re.sub(
            r'<script[^>]+src=["\']([^"\']+)["\'][^>]*>\s*</script>',
            repl_script,
            html,
            flags=re.I,
        )
        html = re.sub(
            r'<link[^>]+href=["\']([^"\']+\.css)["\'][^>]*/?>',
            repl_link,
            html,
            flags=re.I,
        )
        return html

    async def webgame_open(html: str, title: str = "WebGame") -> dict[str, Any]:
        """DEPRECATED silent open — prefer webgame_build so viewers see typing.

        Still opens the game inside the Win95 OS (never host tkinter).
        """
        return await _launch_webgame_window(html, title)

    async def webgame_build(
        html: str,
        title: str = "WebGame",
        filename: str = "index.html",
        icon_label: str = "",
        project: str = "",
    ) -> dict[str, Any]:
        """Visible pipeline: project → editor type → terminal → desktop icon → maximized launch."""
        html = _wrap_html_doc(html)
        if not html:
            return {"ok": False, "error": "empty html"}
        title = (title or "WebGame")[:80]
        filename = (filename or "index.html").strip() or "index.html"
        if not filename.lower().endswith((".html", ".htm")):
            filename = filename + ".html"
        label = (icon_label or title or "Game").strip()[:40]
        slug = (project or label or "game").strip().replace(" ", "-")
        slug = "".join(ch for ch in slug if ch.isalnum() or ch in "-_")[:32] or "game"
        proj = controller.vfs_mkdir(f"projects/{slug}")
        path = f"{proj}/{filename}"

        await say(f"Ок, делаю проект {slug} — сейчас пишу {filename}")

        win = controller.find_window_by_app("editor")
        if not win:
            wid = await controller.open_app("editor")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id, target="editor")
        await controller.ensure_maximized(win.id)
        await controller.patch_app_state(win.id, {
            "filename": filename,
            "path": path,
            "project": proj,
            "content": "",
            "caret": 0,
            "language": "html",
            "terminal_open": False,
            "terminal_lines": [],
            "open_files": [path],
            "tree": controller.editor_tree_for(proj),
        })
        await controller.click(win.x + int(win.w * 0.55), win.y + int(win.h * 0.4))
        await controller.type_text(html, pace="code")
        controller.vfs_write_silent(path, html)
        await controller.sync_editor_tree()

        await _terminal_open(win)
        await asyncio.sleep(0.15)
        cmd = f"webgame open {path}"
        tx, ty = win.x + 120, win.y + win.h - 36
        await controller.move_cursor(tx, ty)
        await controller.click(tx, ty)
        shown = ""
        for ch in cmd:
            shown += ch
            await asyncio.sleep(0.028)
            await controller._emit({"type": "keypress", "char": ch})
            lines = list(win.app_state.get("terminal_lines", []))
            if lines and lines[-1].get("kind") == "cmd" and str(lines[-1].get("text", "")).startswith("$ "):
                lines[-1] = {"kind": "cmd", "text": f"$ {shown}"}
            else:
                lines.append({"kind": "cmd", "text": f"$ {shown}"})
            win.app_state["terminal_lines"] = lines[-400:]
            await controller.patch_app_state(win.id, {"terminal_lines": win.app_state["terminal_lines"]})
        await asyncio.sleep(0.2)
        await _terminal_log(win, "info", f"Launching {title}...")
        await asyncio.sleep(0.25)

        icon_res = await controller.desktop_icon_add(
            label,
            app="webgame",
            icon="game",
            payload={"html": html, "title": title, "filename": path, "project": proj},
        )

        try:
            await controller.minimize_window(win.id)
        except Exception:
            pass
        await asyncio.sleep(0.2)
        await controller.desktop_icon_click(label, double=True)
        launched = await _launch_webgame_window(html, title)
        await say(f"Готово — {title} на весь экран, сейчас поиграю")
        # Real-time local bot (not step-by-step WS keys).
        await webgame_live(seconds=10.0, style="arcade", interval_ms=110)
        return {
            "ok": True,
            "title": title,
            "filename": path,
            "project": proj,
            "icon": icon_res,
            "windowId": launched.get("windowId"),
            "hint": "call ui_check(target='webgame') to verify look",
        }

    async def webgame_launch(path: str, title: str = "") -> dict[str, Any]:
        """Launch a project HTML from VFS (inlines linked css/js), maximized."""
        path = controller.vfs_norm(path)
        html = controller.vfs_read(path)
        if html is None:
            return {"ok": False, "error": f"missing {path}"}
        base = "/".join(path.split("/")[:-1])
        html = _inline_project_assets(html, base)
        label = title or (path.split("/")[-2] if "/" in path else "Game")
        return await _launch_webgame_window(html, label)

    async def webgame_key(key: str) -> dict[str, Any]:
        """Send one key to the in-OS HTML5 game (ArrowLeft/Right/Up/Down/Space…)."""
        win = controller.find_window_by_app("webgame")
        if not win:
            return {"ok": False, "error": "webgame not open — call webgame_build first"}
        await controller.bring_to_front(win.id, target="webgame")
        await controller.press_key(key, pause=0.05)
        return {"ok": True, "key": key}

    async def webgame_play(keys: list[str], interval_ms: int = 90) -> dict[str, Any]:
        """Burst-play: press many keys in one tool call (games-speed, no LLM lag).

        Pass 8–40 keys like [\"ArrowLeft\",\"ArrowUp\",\"ArrowRight\",…].
        interval_ms is delay between presses (default 90). Prefer this over
        repeated webgame_key — one LLM round-trip cannot keep up with arcade games.
        """
        win = controller.find_window_by_app("webgame")
        if not win:
            return {"ok": False, "error": "webgame not open — call webgame_build first"}
        if not keys:
            return {"ok": False, "error": "empty keys"}
        keys = [str(k) for k in keys][:80]
        interval_ms = max(30, min(400, int(interval_ms or 90)))
        await controller.bring_to_front(win.id, target="webgame")
        pause = interval_ms / 1000.0
        for key in keys:
            await controller.press_key(key, pause=pause)
        return {"ok": True, "pressed": len(keys), "interval_ms": interval_ms}

    async def webgame_live(
        seconds: float = 12.0,
        style: str = "arcade",
        interval_ms: int = 120,
    ) -> dict[str, Any]:
        """Real-time play INSIDE the game iframe (no LLM / WS spam per frame).

        Local bot HOLDS arrow keys at arcade pace. If the game defines
        window.__CSAMA_TICK__(dt), that hook is used. Prefer over webgame_play.
        Call webgame_live(0) to stop.
        """
        seconds = float(seconds or 0)
        win = controller.find_window_by_app("webgame")
        if not win:
            return {"ok": False, "error": "webgame not open"}
        await controller.stop_webgame_autopilot()
        if seconds <= 0:
            seq = int(win.app_state.get("holdSeq") or 0) + 1
            await controller.patch_app_state(win.id, {
                "livePlay": None,
                "holdSeq": seq,
                "holdKey": "",
            })
            return {"ok": True, "stopped": True}
        await controller.bring_to_front(win.id, target="webgame")
        live_seq = 1
        prev = win.app_state.get("livePlay")
        if isinstance(prev, dict):
            live_seq = int(prev.get("seq") or 0) + 1
        await controller.patch_app_state(win.id, {
            "livePlay": {
                "seq": live_seq,
                "seconds": seconds,
                "style": style or "arcade",
                "intervalMs": max(60, int(interval_ms or 120)),
            },
        })
        return {"ok": True, "seconds": seconds, "style": style, "mode": "iframe-local"}

    async def webgame_autopilot(seconds: float = 8.0, interval_ms: int = 100) -> dict[str, Any]:
        """Alias for webgame_live — real-time iframe bot (not step-by-step WS keys)."""
        return await webgame_live(seconds=seconds, style="arcade", interval_ms=interval_ms)

    async def ui_check(question: str = "Does the UI look correct?", target: str = "os") -> dict[str, Any]:
        """Screenshot the OS or webgame canvas and ask vision if it looks OK.

        Call after opening/testing UI apps or games. target: os | webgame.
        """
        question = (question or "Does the UI look correct?").strip()[:400]
        target = (target or "os").lower().strip()
        if target not in ("os", "webgame"):
            target = "os"
        b64 = await controller.request_screenshot(target=target, timeout=7.0)
        if not b64:
            return {"ok": False, "error": "no_screenshot", "hint": "frontend did not return a frame"}
        try:
            from langchain_core.messages import HumanMessage
            from .models import get_default_router
            router = get_default_router()
            if not router.has_role("vision"):
                return {
                    "ok": True,
                    "vision": False,
                    "note": "screenshot captured but vision role not configured",
                    "bytes": len(b64),
                    "question": question,
                }
            llm = router.llm("vision")
            prompt = (
                "You are checking a Win95-style OS screenshot for an agent. "
                "Answer in Russian, 1-3 short sentences: what you see, and whether "
                "the UI/game looks OK (layout fits, not blank, not clipped badly). "
                f"Question: {question}"
            )
            msg = await llm.ainvoke([HumanMessage(content=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ])])
            content = getattr(msg, "content", "") or ""
            if isinstance(content, list):
                content = "".join(
                    (c.get("text") if isinstance(c, dict) else str(c)) for c in content
                )
            answer = str(content).strip()[:600]
            await _note(f"посмотрела экран: {answer[:120]}")
            return {"ok": True, "vision": True, "answer": answer, "target": target}
        except Exception as exc:
            return {"ok": False, "error": f"vision_failed: {exc}", "bytes": len(b64)}

    async def desktop_icon_add(
        label: str,
        app: str = "webgame",
        icon: str = "game",
        title: str = "",
        filename: str = "",
    ) -> dict[str, Any]:
        """Add (or update) a desktop shortcut. For games, pass app=\"webgame\".

        If the editor currently holds HTML, that source is bound to the icon
        so a later desktop_icon_open launches the game.
        """
        payload: dict[str, Any] = {}
        if app == "webgame":
            ed = controller.find_window_by_app("editor")
            html = ""
            if ed:
                html = ed.app_state.get("content") or ""
            if not html and filename:
                html = controller.vfs.get(filename, "")
            if html:
                payload = {
                    "html": _wrap_html_doc(html),
                    "title": title or label,
                    "filename": filename or (ed.app_state.get("filename") if ed else ""),
                }
        return await controller.desktop_icon_add(
            label, app=app, icon=icon or "game", payload=payload or None
        )

    async def desktop_icon_open(label: str) -> dict[str, Any]:
        """Animate cursor to a desktop icon and open it (double-click)."""
        clicked = await controller.desktop_icon_click(label, double=True)
        if not clicked.get("ok"):
            return clicked
        ico = clicked["icon"]
        app = ico.get("app")
        payload = ico.get("payload") or {}
        if app == "webgame" and payload.get("html"):
            launched = await _launch_webgame_window(
                payload["html"], payload.get("title") or ico.get("label") or "WebGame"
            )
            return {**clicked, **launched}
        if app and app in ("browser", "editor", "paint", "music", "tracker", "computer"):
            wid = await controller.open_app(app)
            return {**clicked, "windowId": wid}
        return clicked

    # ─── music ────────────────────────────────────────────────────────
    async def music_play() -> dict[str, Any]:
        """Start playback in the music player."""
        win = controller.find_window_by_app("music")
        if not win:
            wid = await controller.open_app("music")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id)
        await controller.click(*_music_play_btn(win))
        await controller.patch_app_state(win.id, {"playing": True})
        return {"ok": True}

    async def music_pause() -> dict[str, Any]:
        """Pause the music player."""
        win = controller.find_window_by_app("music")
        if not win:
            return {"ok": False, "error": "not_open"}
        await controller.click(*_music_play_btn(win))
        await controller.patch_app_state(win.id, {"playing": False})
        return {"ok": True}

    async def music_next() -> dict[str, Any]:
        """Skip to the next track."""
        win = controller.find_window_by_app("music")
        if not win:
            wid = await controller.open_app("music")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id)
        await controller.click(*_music_next_btn(win))
        playlist = win.app_state.get("playlist", [])
        idx = (win.app_state.get("index", 0) + 1) % max(1, len(playlist))
        await controller.patch_app_state(win.id, {"index": idx, "playing": True})
        return {"ok": True, "index": idx}

    # ─── tracker ──────────────────────────────────────────────────────
    async def task_add(text: str) -> dict[str, Any]:
        """Append a task to the task tracker."""
        win = controller.find_window_by_app("tracker")
        if not win:
            wid = await controller.open_app("tracker")
            win = controller.windows[wid]
        await controller.bring_to_front(win.id, target="tracker_input")
        ix, iy = _tracker_input_coords(win)
        await controller.click(ix, iy)
        await controller.patch_app_state(win.id, {"draft": ""})
        await controller.type_text(text)
        bx, by = _tracker_add_button_coords(win)
        await controller.click(bx, by)
        tasks = list(win.app_state.get("tasks", []))
        tasks.append({"id": uuid.uuid4().hex[:6], "text": text, "done": False})
        await controller.patch_app_state(win.id, {"tasks": tasks, "draft": ""})
        return {"ok": True, "count": len(tasks)}

    async def task_done(index: int) -> dict[str, Any]:
        """Mark a task at the given 0-based index as done."""
        win = controller.find_window_by_app("tracker")
        if not win:
            return {"ok": False, "error": "not_open"}
        tasks = list(win.app_state.get("tasks", []))
        if not (0 <= index < len(tasks)):
            return {"ok": False, "error": "index_out_of_range"}
        # Animate clicking the checkbox at that row.
        row_y = win.y + 100 + index * 26
        await controller.click(win.x + 24, row_y)
        tasks[index] = {**tasks[index], "done": True}
        await controller.patch_app_state(win.id, {"tasks": tasks})
        return {"ok": True}

    async def task_list() -> dict[str, Any]:
        """Read the current task list."""
        win = controller.find_window_by_app("tracker")
        if not win:
            return {"ok": True, "tasks": []}
        return {"ok": True, "tasks": list(win.app_state.get("tasks", []))}

    # ─── misc ─────────────────────────────────────────────────────────
    async def think(seconds: float = 0.6) -> dict[str, Any]:
        """Pause briefly — useful for natural pacing between actions."""
        await asyncio.sleep(max(0.05, min(3.0, float(seconds))))
        return {"ok": True}

    # ─── app loader (any Linux program) ───────────────────────────────
    async def app_list() -> dict[str, Any]:
        """List installed loader-apps (programs turned into tool sets).

        Each entry has ``app`` (slug), ``title``, ``description``,
        ``actions`` (the verbs the agent may call via app_action), and
        ``installed`` (whether the manifest is on disk).
        """
        if loader is None:
            return {"ok": True, "apps": [], "note": "loader disabled"}
        return {"ok": True, "apps": loader["registry"].summary()}

    async def app_describe(app: str) -> dict[str, Any]:
        """Describe one app: full manifest including its actions and
        elements. Call this before app_action to learn the available
        verbs and the parameters they expect.
        """
        if loader is None:
            return {"ok": False, "error": "loader disabled"}
        m = loader["registry"].get(app)
        if not m:
            return {"ok": False, "error": "not_installed", "app": app,
                    "hint": "Call app_install first."}
        return {"ok": True, "app": m.to_dict()}

    async def app_install(description: str, app: str | None = None) -> dict[str, Any]:
        """Install a brand-new Linux program as a set of tools.

        You describe the program in plain words ("Firefox browser",
        "GIMP image editor", "Audacity audio recorder"); the harness
        generates a manifest via the LLM and registers it. After this
        call, app_open / app_action work for the new program. Idempotent
        — re-installing overwrites the manifest.
        """
        if loader is None:
            return {"ok": False, "error": "loader disabled"}
        result = await loader["harness"].install(description, app_slug=app)
        return result

    async def app_open(app: str, width: int | None = None, height: int | None = None) -> dict[str, Any]:
        """Launch an installed loader-app and bind it to a Win95 window.

        The agent's cursor will end up over the new window. Subsequent
        app_action calls drive the program through visible clicks &
        typing — the viewer sees code-sama operate the real GUI.
        """
        if loader is None:
            return {"ok": False, "error": "loader disabled"}
        m = loader["registry"].get(app)
        if not m:
            return {"ok": False, "error": "not_installed", "app": app,
                    "hint": "Call app_install first."}
        # Optional narrate callback so the streamer can comment on the launch.
        async def _p(info: dict[str, Any]) -> None:
            try:
                await loader["on_progress"](info)
            except Exception:
                pass
        return await loader["runtime"].open_app(m, width=width, height=height, progress=_p)

    async def app_action(app: str, action: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        """Run one named action of an open loader-app.

        Each app exposes its own verbs (e.g. firefox has ``navigate``,
        ``go_back``, ``new_tab``; gimp has ``open_file``, ``apply_filter``).
        Learn them via app_describe. ``args`` is a dict of the action's
        parameters, e.g. {"url": "https://example.com"}.

        Every action animates the cursor and typing on the OS canvas —
        the viewer watches the choreography, never a silent command.
        """
        if loader is None:
            return {"ok": False, "error": "loader disabled"}
        async def _p(info: dict[str, Any]) -> None:
            try:
                await loader["on_progress"](info)
            except Exception:
                pass
        return await loader["runtime"].run_action(app, action, args, progress=_p)

    async def app_close(app: str) -> dict[str, Any]:
        """Close an open loader-app's window and terminate the program."""
        if loader is None:
            return {"ok": False, "error": "loader disabled"}
        return await loader["runtime"].close_app(app)

    def _wrap(fn, name: str, desc: str) -> StructuredTool:
        return StructuredTool.from_function(coroutine=fn, name=name, description=desc)

    return [
        _wrap(move_mouse, "move_mouse", "Move the cursor to OS-canvas (x, y). Canvas is 1024x720."),
        _wrap(click, "click", "Click at (x, y) or at the cursor's current spot. button=\"left\"|\"right\"."),
        _wrap(type_text, "type_text", "Type a string into the focused field with human rhythm."),
        _wrap(press_key, "press_key", "Press a single key like Enter or Backspace."),
        _wrap(open_app, "open_app", "Open one of: browser, editor, paint, music, tracker, computer, webgame."),
        _wrap(close_app, "close_app", "Close the given app's window."),
        _wrap(focus_app, "focus_app", "Bring the given app to the front."),
        _wrap(move_window, "move_window", "Drag the given app's window to (x, y)."),
        _wrap(minimize_app, "minimize_app", "Minimize the given app to the taskbar."),
        _wrap(maximize_app, "maximize_app", "Maximize an app to fill the OS screen (games, editor)."),
        _wrap(say, "say", "Short first-person spoken line WHILE coding (e.g. «сейчас пишу логику призраков»)."),
        _wrap(browser_navigate, "browser_navigate", "Open and navigate the in-OS browser to a URL."),
        _wrap(browser_read, "browser_read", "Read visible text from the current browser page."),
        _wrap(browser_back, "browser_back", "Go back one page in the browser."),
        _wrap(editor_open, "editor_open", "Open a file path in the editor (creates if missing)."),
        _wrap(editor_write, "editor_write", "Type into the active editor file. append=false to replace."),
        _wrap(editor_set_language, "editor_set_language", "Change editor language (python/js/html/css/json/plain)."),
        _wrap(project_create, "project_create", "Create projects/<name>/ and focus it in the editor tree."),
        _wrap(fs_write, "fs_write", "Write a VFS file with visible typing (path like projects/pacman/game.js)."),
        _wrap(fs_open, "fs_open", "Open a VFS file in the editor."),
        _wrap(fs_list, "fs_list", "List a VFS directory (default projects)."),
        _wrap(run_python, "run_python", "Run Python code in a sandbox and stream stdout/stderr to the in-OS terminal. Omit code to run whatever is currently in the editor."),
        _wrap(run_node, "run_node", "Run JavaScript code with Node.js in a sandbox."),
        _wrap(terminal_print, "terminal_print", "Print a line into the in-OS terminal (kind: info/out/err/cmd)."),
        _wrap(terminal_clear, "terminal_clear", "Clear the in-OS terminal."),
        _wrap(paint_open, "paint_open", "Open Paint."),
        _wrap(paint_set_color, "paint_set_color", "Set brush color (#rrggbb)."),
        _wrap(paint_stroke, "paint_stroke", "Draw a stroke. points is a list of [x,y] in 0..1 canvas-local coords."),
        _wrap(paint_clear, "paint_clear", "Wipe the Paint canvas."),
        _wrap(webgame_open, "webgame_open", "Silent open of an HTML5 game (prefer webgame_build)."),
        _wrap(webgame_build, "webgame_build", "VISIBLE game pipeline: project + type HTML + terminal + icon + maximize. Prefer multi-file via project_create+fs_write+webgame_launch."),
        _wrap(webgame_launch, "webgame_launch", "Launch projects/.../index.html from VFS (inlines css/js), maximized."),
        _wrap(webgame_key, "webgame_key", "Press ONE key / hold a direction in webgame. Prefer webgame_live."),
        _wrap(webgame_play, "webgame_play", "Burst direction changes. Prefer webgame_live for real-time arcade."),
        _wrap(webgame_live, "webgame_live", "REAL-TIME play inside iframe (held keys / __CSAMA_TICK__). Use this for games."),
        _wrap(webgame_autopilot, "webgame_autopilot", "Alias of webgame_live."),
        _wrap(ui_check, "ui_check", "Screenshot OS/webgame and ask vision if UI looks OK. Call after testing UI."),
        _wrap(desktop_icon_add, "desktop_icon_add", "Add a desktop shortcut (label, app, icon)."),
        _wrap(desktop_icon_open, "desktop_icon_open", "Double-click a desktop icon by label (animates cursor)."),
        _wrap(music_play, "music_play", "Play music."),
        _wrap(music_pause, "music_pause", "Pause music."),
        _wrap(music_next, "music_next", "Skip to next track."),
        _wrap(task_add, "task_add", "Add a task to the Task Tracker."),
        _wrap(task_done, "task_done", "Mark task by 0-based index as done."),
        _wrap(task_list, "task_list", "List all tasks in the tracker."),
        _wrap(think, "think", "Pause for some seconds without doing anything visible."),
        _wrap(app_list, "app_list", "List installed loader-apps (any Linux program turned into tool sets)."),
        _wrap(app_describe, "app_describe", "Show full manifest of a loader-app: elements, actions, params. Call before app_action."),
        _wrap(app_install, "app_install", "Install a NEW Linux program as a tool set. Pass a plain-language description; the harness generates a manifest. Idempotent."),
        _wrap(app_open, "app_open", "Launch an installed loader-app and bind it to a window. Cursor ends up over the new window."),
        _wrap(app_action, "app_action", "Run one named action of an open loader-app (e.g. navigate, apply_filter). Animates cursor + typing visibly."),
        _wrap(app_close, "app_close", "Close an open loader-app and terminate the program."),
    ]
