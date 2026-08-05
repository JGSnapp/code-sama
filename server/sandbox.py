"""Tiny code-execution sandbox.

Headless stdout/stderr only. Host GUI libraries and process spawning are
blocked so the agent cannot pop tkinter/pygame windows on the user's real
desktop — everything interactive must live inside the Win95 OS canvas.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# Injected at the top of every Python job.
_PYTHON_GUARD = r'''# --- code-sama sandbox guard (do not remove) ---
import builtins as _b, sys as _sys
_BLOCK = frozenset({
    "tkinter", "_tkinter", "Tkinter", "turtle", "pygame", "pyglet",
    "arcade", "wx", "PyQt5", "PyQt6", "PySide2", "PySide6", "gi",
    "gtk", "PySimpleGUI", "dearpygui",
})
_orig_import = _b.__import__
def _guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = (name or "").split(".")[0]
    if root in _BLOCK or name in _BLOCK:
        raise ImportError(
            f"{name!r} is blocked in the code-sama sandbox. "
            "GUI/games must run INSIDE the Win95 OS (tool webgame_build with "
            "HTML5/canvas), never as a host-desktop window. "
            "Do not use tkinter/pygame/subprocess to escape."
        )
    return _orig_import(name, globals, locals, fromlist, level)
_b.__import__ = _guarded_import

import subprocess as _sp, os as _os
def _blocked(*_a, **_k):
    raise RuntimeError(
        "subprocess/os process spawning is blocked in the sandbox — "
        "no host windows. Use webgame_build(html) for interactive games."
    )
_sp.Popen = _blocked
_sp.call = _blocked
_sp.run = _blocked
_sp.check_call = _blocked
_sp.check_output = _blocked
if hasattr(_os, "startfile"):
    _os.startfile = _blocked
try:
    import webbrowser as _wb
    _wb.open = _blocked
    _wb.open_new = _blocked
    _wb.open_new_tab = _blocked
except Exception:
    pass
# --- end guard ---
'''

_BLOCKED_PATTERNS = (
    re.compile(r"\bimport\s+tkinter\b", re.I),
    re.compile(r"\bfrom\s+tkinter\b", re.I),
    re.compile(r"\bimport\s+pygame\b", re.I),
    re.compile(r"\bfrom\s+pygame\b", re.I),
    re.compile(r"\bimport\s+turtle\b", re.I),
    re.compile(r"\bPyQt[56]\b"),
    re.compile(r"\bPySide[26]\b"),
    re.compile(r"\bsubprocess\.(Popen|run|call|check_)\w*\s*\("),
    re.compile(r"\bos\.startfile\s*\("),
    re.compile(r"\bwebbrowser\.open"),
)


@dataclass
class RunResult:
    ok: bool
    exit_code: int
    stdout: str
    stderr: str
    elapsed: float
    timed_out: bool = False


class Sandbox:
    """Run short scripts in a scratch directory under the OS temp dir."""

    def __init__(self) -> None:
        self.root = Path(tempfile.gettempdir()) / "code-sama-sandbox"
        self.root.mkdir(parents=True, exist_ok=True)

    def _reject_host_gui(self, code: str) -> str | None:
        for pat in _BLOCKED_PATTERNS:
            if pat.search(code or ""):
                return (
                    "Blocked: this script tries to open a host GUI or spawn a "
                    "process on the real PC. Interactive apps/games must use "
                    "webgame_build(html) inside code-sama OS, not tkinter/pygame."
                )
        return None

    async def run_python(
        self,
        code: str,
        *,
        filename: str = "main.py",
        timeout: float = 8.0,
    ) -> RunResult:
        reason = self._reject_host_gui(code)
        if reason:
            return RunResult(False, -1, "", reason, 0.0)
        wrapped = _PYTHON_GUARD + "\n" + (code or "")
        return await self._run_file(wrapped, filename, [sys.executable, "-X", "utf8"], timeout)

    async def run_node(
        self,
        code: str,
        *,
        filename: str = "main.js",
        timeout: float = 8.0,
    ) -> RunResult:
        node = shutil.which("node")
        if not node:
            return RunResult(False, -1, "", "node interpreter not found on PATH", 0.0)
        return await self._run_file(code, filename, [node], timeout)

    async def _run_file(
        self,
        code: str,
        filename: str,
        argv_prefix: list[str],
        timeout: float,
    ) -> RunResult:
        script_dir = self.root / f"job-{int(time.time() * 1000)}"
        script_dir.mkdir(parents=True, exist_ok=True)
        script_path = script_dir / filename
        script_path.write_text(code, encoding="utf-8")
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "TEMP": os.environ.get("TEMP", str(self.root)),
            "TMP": os.environ.get("TMP", str(self.root)),
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            # Headless: no display for accidental GUI libs that slip through.
            "DISPLAY": "",
            "SDL_VIDEODRIVER": "dummy",
            "MPLBACKEND": "Agg",
        }
        argv = [*argv_prefix, str(script_path)]
        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(script_dir),
                env=env,
            )
        except FileNotFoundError as exc:
            return RunResult(False, -1, "", f"failed to launch: {exc}", 0.0)

        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            timed_out = True
            try:
                proc.kill()
            except ProcessLookupError:  # pragma: no cover
                pass
            stdout, stderr = await proc.communicate()
        elapsed = time.monotonic() - start
        out = (stdout or b"").decode("utf-8", errors="replace")
        err = (stderr or b"").decode("utf-8", errors="replace")
        if timed_out:
            err = (err + "\n" if err else "") + f"[sandbox killed after {timeout:.1f}s]"
        return RunResult(
            ok=(proc.returncode == 0 and not timed_out),
            exit_code=proc.returncode if proc.returncode is not None else -1,
            stdout=out,
            stderr=err,
            elapsed=elapsed,
            timed_out=timed_out,
        )
