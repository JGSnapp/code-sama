"""Per-app Xvfb broker — the ChromeOS-style compositor for code-sama-os.

For every Linux GUI program code-sama wants to run, we:

  1. Pick a free X display number (``:42`` and up).
  2. Spawn ``Xvfb :N -screen 0 WxHx24`` so that program has a virtual monitor.
  3. Launch the program with ``DISPLAY=:N`` set.
  4. Periodically grab ``import -display :N -window root png:-`` and ship
     the (jpeg-compressed) bytes over a per-window WebSocket.
  5. Forward mouse / keyboard events from the WS back into the X server
     via ``xdotool --display :N``.

Inside the Win95 UI each Linux app becomes a regular window — title bar,
minimise / close, drop-shadow — and its body is a ``<canvas>`` painted
with the incoming frames. From the agent's point of view the whole thing
is exposed as four uniform tools (``linux_launch`` / ``linux_click`` /
``linux_type`` / ``linux_key``).

If the system isn't actually Linux (e.g. the dev box is plain Windows
without ``Xvfb`` on PATH) the broker just refuses to launch and reports
``"linux_unavailable"`` — the rest of the OS keeps working.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import shutil
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


log = logging.getLogger("code-sama-os.linux_broker")


@dataclass
class LinuxWindow:
    id: str
    display: int          # X display number (e.g. 42 → :42)
    cmd: str              # original command line
    title: str
    width: int
    height: int
    xvfb_proc: subprocess.Popen | None = None
    app_proc: subprocess.Popen | None = None
    started_at: float = field(default_factory=time.time)
    last_frame_b64: str | None = None
    last_frame_ts: float = 0.0


class LinuxBroker:
    """Coordinates Xvfb instances + GUI streaming + input injection."""

    def __init__(self, controller=None) -> None:
        self.controller = controller
        self.windows: dict[str, LinuxWindow] = {}
        self._frame_tasks: dict[str, asyncio.Task] = {}
        self._next_display = 42
        self._lock = asyncio.Lock()
        self.fps = float(os.environ.get("LINUX_STREAM_FPS", "8"))
        self.quality = int(os.environ.get("LINUX_STREAM_QUALITY", "55"))
        # Frame-capture backend: "ffmpeg" (one long-lived process piping
        # JPEGs — fast, the default on Linux) or "import" (ImageMagick,
        # forks per frame — the original fallback). Auto-downgrades.
        self.capture_backend = os.environ.get("LINUX_CAPTURE", "ffmpeg").lower()
        if self.capture_backend == "ffmpeg" and not shutil.which("ffmpeg"):
            self.capture_backend = "import"
        # Optional shared-display mode: one Xvfb + a tiny WM (openbox)
        # shared across every launched app, so they can talk to each
        # other (clipboard, DnD) and the agent sees a real desktop.
        self.shared_display: int | None = None
        self._shared_xvfb: subprocess.Popen | None = None
        self._shared_wm: subprocess.Popen | None = None
        self.workspace_dir = os.environ.get("WORKSPACE_DIR", "/workspace")

    # ─── capabilities probe ───────────────────────────────────────────
    @property
    def available(self) -> bool:
        # ``import`` (ImageMagick) is only required for the legacy
        # capture backend; ffmpeg replaces it on the Docker image.
        caps = [shutil.which("Xvfb"), shutil.which("xdotool")]
        if self.capture_backend == "import":
            caps.append(shutil.which("import"))
        else:
            caps.append(shutil.which("ffmpeg"))
        return all(caps)

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "windows": len(self.windows),
            "fps": self.fps,
            "quality": self.quality,
        }

    # ─── lifecycle ────────────────────────────────────────────────────
    async def launch(
        self,
        cmd: str,
        title: str | None = None,
        *,
        width: int = 800,
        height: int = 540,
        shared: bool = False,
    ) -> dict[str, Any]:
        if not self.available:
            return {"ok": False, "error": "linux_unavailable", "detail": "Xvfb/xdotool not on PATH (run inside the Docker image)."}
        cmd = (cmd or "").strip()
        if not cmd:
            return {"ok": False, "error": "empty_cmd"}
        # Shared-display mode: every shared app lands on the same X
        # server. Per-app mode (the original): one Xvfb per window.
        if shared:
            display = await self.ensure_shared_display()
            win_w = max(width, 1400)
            win_h = max(height, 900)
        else:
            async with self._lock:
                display = self._next_display
                self._next_display += 1
            win_w, win_h = width, height

        win_id = uuid.uuid4().hex[:8]
        xvfb: subprocess.Popen | None = None
        if not shared:
            try:
                xvfb = subprocess.Popen(
                    [
                        "Xvfb", f":{display}",
                        "-screen", "0", f"{win_w}x{win_h}x24",
                        "-nolisten", "tcp",
                        "-ac",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except Exception as exc:  # pragma: no cover
                log.exception("Xvfb spawn failed")
                return {"ok": False, "error": "xvfb_spawn_failed", "detail": str(exc)}

            # Give Xvfb a beat to come up before launching the app.
            await asyncio.sleep(0.35)

        env = os.environ.copy()
        env["DISPLAY"] = f":{display}"
        # A friendly $HOME inside /workspace so apps store dotfiles there.
        env["HOME"] = os.environ.get("WORKSPACE_DIR", "/workspace")
        try:
            app = subprocess.Popen(
                ["sh", "-c", cmd],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except Exception as exc:  # pragma: no cover
            log.exception("Linux app spawn failed")
            if xvfb:
                xvfb.terminate()
            return {"ok": False, "error": "app_spawn_failed", "detail": str(exc)}

        # Give the app a moment to map its first window.
        await asyncio.sleep(0.6)
        win = LinuxWindow(
            id=win_id,
            display=display,
            cmd=cmd,
            title=title or cmd.split()[0],
            width=win_w,
            height=win_h,
            xvfb_proc=xvfb,        # None in shared mode (owned by broker)
            app_proc=app,
        )
        self.windows[win_id] = win
        self._frame_tasks[win_id] = asyncio.create_task(self._frame_loop(win))
        log.info("LINUX launch id=%s display=:%s shared=%s cmd=%r", win_id, display, shared, cmd)
        return {"ok": True, "windowId": win_id, "display": display, "title": win.title, "width": win_w, "height": win_h}

    async def close(self, window_id: str) -> dict[str, Any]:
        win = self.windows.pop(window_id, None)
        if not win:
            return {"ok": False, "error": "not_found"}
        task = self._frame_tasks.pop(window_id, None)
        if task:
            task.cancel()
        # Kill the app process always. Only kill the Xvfb if this window
        # owned its own (per-app mode); in shared mode the Xvfb is owned
        # by the broker and outlives individual windows.
        for proc in (win.app_proc, win.xvfb_proc):
            if not proc:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, AttributeError, OSError):
                try:
                    proc.terminate()
                except Exception:
                    pass
        log.info("LINUX close id=%s display=:%s", window_id, win.display)
        return {"ok": True}

    async def shutdown(self) -> None:
        for wid in list(self.windows.keys()):
            await self.close(wid)
        # Tear down the shared display if we spun one up.
        for proc in (self._shared_wm, self._shared_xvfb):
            if not proc:
                continue
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, AttributeError, OSError):
                try:
                    proc.terminate()
                except Exception:
                    pass
        self._shared_wm = None
        self._shared_xvfb = None
        self.shared_display = None

    # ─── shared-display mode ──────────────────────────────────────────
    async def ensure_shared_display(self, *, width: int = 1400, height: int = 900) -> int:
        """Spin up one shared Xvfb + openbox WM and reuse it for every
        subsequent launch that asks for ``shared=True``.

        Returns the display number. Useful when the agent wants several
        Linux apps visible at once (e.g. browser + editor passing content
        via clipboard). Falls back to per-app mode if openbox isn't
        installed — the caller doesn't need to care.
        """
        if self.shared_display is not None:
            return self.shared_display
        async with self._lock:
            if self.shared_display is not None:
                return self.shared_display
            display = self._next_display
            self._next_display += 1
            xvfb = subprocess.Popen(
                ["Xvfb", f":{display}", "-screen", "0",
                 f"{width}x{height}x24", "-nolisten", "tcp", "-ac"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            await asyncio.sleep(0.4)
            wm = None
            if shutil.which("openbox"):
                env = os.environ.copy()
                env["DISPLAY"] = f":{display}"
                wm = subprocess.Popen(
                    ["openbox"], env=env,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                await asyncio.sleep(0.3)
            self._shared_xvfb = xvfb
            self._shared_wm = wm
            self.shared_display = display
            log.info("LINUX shared display :%s (wm=%s)", display,
                     "openbox" if wm else "none")
            return display

    # ─── workspace shell (for loader ``run`` steps) ───────────────────
    async def run_in_workspace(self, snippet: str, *, timeout: float = 8.0) -> dict[str, Any]:
        """Run a short shell snippet inside the persistent workspace dir.

        Used by loader manifests' ``run`` steps (e.g. to ``mkdir``,
        ``curl`` a file, or set up configs before launching the app).
        Not a security boundary — same trust level as the existing
        :class:`Sandbox`.
        """
        snippet = (snippet or "").strip()
        if not snippet:
            return {"ok": False, "error": "empty"}
        try:
            proc = await asyncio.create_subprocess_exec(
                "sh", "-c", snippet,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.workspace_dir,
                start_new_session=True,
            )
        except FileNotFoundError as exc:
            return {"ok": False, "error": f"no_shell: {exc}"}
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, AttributeError, OSError):
                try:
                    proc.kill()
                except Exception:
                    pass
            return {"ok": False, "error": "timeout", "timed_out": True}
        out = (stdout or b"").decode("utf-8", errors="replace").strip()
        err = (stderr or b"").decode("utf-8", errors="replace").strip()
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode if proc.returncode is not None else -1,
            "stdout": out[-2000:],
            "stderr": err[-2000:],
        }

    # ─── input injection ──────────────────────────────────────────────
    async def click(self, window_id: str, x: int, y: int, button: int = 1) -> dict[str, Any]:
        win = self.windows.get(window_id)
        if not win:
            return {"ok": False, "error": "not_found"}
        await self._xdo(win, "mousemove", "--sync", str(int(x)), str(int(y)))
        await self._xdo(win, "click", str(button))
        return {"ok": True}

    async def type_text(self, window_id: str, text: str) -> dict[str, Any]:
        win = self.windows.get(window_id)
        if not win:
            return {"ok": False, "error": "not_found"}
        await self._xdo(win, "type", "--delay", "35", "--", text)
        return {"ok": True}

    async def key(self, window_id: str, key: str) -> dict[str, Any]:
        win = self.windows.get(window_id)
        if not win:
            return {"ok": False, "error": "not_found"}
        await self._xdo(win, "key", key)
        return {"ok": True}

    async def screenshot(self, window_id: str) -> dict[str, Any]:
        win = self.windows.get(window_id)
        if not win:
            return {"ok": False, "error": "not_found"}
        b64 = await self._grab_b64(win)
        return {"ok": True, "imageB64": b64} if b64 else {"ok": False, "error": "no_frame"}

    # ─── frame streaming ──────────────────────────────────────────────
    async def _frame_loop(self, win: LinuxWindow) -> None:
        period = 1.0 / max(1.0, self.fps)
        try:
            while True:
                start = time.monotonic()
                b64 = await self._grab_b64(win)
                if b64 and b64 != win.last_frame_b64:
                    win.last_frame_b64 = b64
                    win.last_frame_ts = time.time()
                    if self.controller:
                        await self.controller.patch_app_state(
                            win.id,
                            {"frame": b64, "width": win.width, "height": win.height},
                        )
                elapsed = time.monotonic() - start
                await asyncio.sleep(max(0.0, period - elapsed))
        except asyncio.CancelledError:
            return
        except Exception:  # pragma: no cover
            log.exception("frame loop crashed for %s", win.id)

    async def _grab_b64(self, win: LinuxWindow) -> str | None:
        """Capture one JPEG frame of the window's X display.

        Two backends:

        * ``ffmpeg`` — preferred. We fork a one-shot ``ffmpeg`` per
          frame, but it's dramatically cheaper than ImageMagick's
          ``import`` because ffmpeg's x11grab demuxer warms up faster
          and we cap it with ``-frames:v 1``.
        * ``import`` — the original fallback. ImageMagick, available
          basically everywhere.
        """
        if self.capture_backend == "ffmpeg":
            cmd = [
                "ffmpeg", "-y", "-loglevel", "error",
                "-f", "x11grab", "-draw_mouse", "1",
                "-video_size", f"{win.width}x{win.height}",
                "-i", f":{win.display}",
                "-frames:v", "1",
                "-f", "image2", "-c:v", "mjpeg",
                "-q:v", str(max(2, min(31, (100 - self.quality) // 3))),
                "pipe:1",
            ]
        else:
            cmd = ["import", "-display", f":{win.display}", "-window", "root",
                   "-quality", str(self.quality), "jpeg:-"]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            data, _ = await asyncio.wait_for(proc.communicate(), timeout=2.5)
            if not data:
                return None
            return base64.b64encode(data).decode("ascii")
        except Exception:
            return None

    async def _xdo(self, win: LinuxWindow, *args: str) -> None:
        cmd = ["xdotool", "--display", f":{win.display}", *args]
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.wait(), timeout=2.5)
        except Exception:
            log.exception("xdotool failed: %s", cmd)
