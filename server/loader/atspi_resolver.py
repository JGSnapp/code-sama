"""Resolve an :class:`ElementLocator` to a concrete pixel rectangle on the
virtual display, so the runtime knows where to send the OS cursor.

Resolution order (first hit wins):

  1. **AT-SPI** (when ``role``/``name`` is set and the app exposes one)
     ``pyatspi`` walks the accessibility tree of the running application
     and returns ``(x, y, w, h)`` in display coordinates. This is what
     makes generated manifests robust: the same manifest works whether
     the user resized the window, changed the theme, or switched locale.

  2. **pixel hint** (when ``x_pct``/``y_pct`` is set)
     A fixed location *relative to the window body* — ``0.5, 0.5`` is
     dead-centre. We multiply by the live window size and offset by the
     window position. Good for canvases / GL surfaces that have no a11y
     tree at all.

  3. **none** — caller gets ``None`` and decides whether to fail or to
     fall back to the window centre.

Why a separate module?
----------------------
The resolver is the *only* component that knows about AT-SPI. Everything
else in the loader stays backend-agnostic, so we can later swap in an
OCR resolver (``text_hint``) or a vision-model resolver without touching
the runtime.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from dataclasses import dataclass
from typing import Any

from .manifest import ElementLocator


log = logging.getLogger("code-sama-os.loader.atspi")


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


class AtspiResolver:
    """Find elements on a virtual X display.

    Two execution modes:

    * **in-process** — import :mod:`pyatspi` directly. Only works when
      the broker and the GUI apps share the same X server *and* the
      Python process can import the gir bindings. This is the fast path
      on the Docker image (we install ``python3-atspi`` + ``gir1.2-atspi-2.0``).
    * **cli** — shell out to ``atspi-inspect`` / a small helper script.
      Used when the main process can't import pyatspi (e.g. running on
      Windows against a remote Xvfb via SSH). The CLI helper is a 30-line
      python script executed with ``DISPLAY`` set.
    """

    def __init__(self) -> None:
        self._pyatspi: Any | None = None
        self._probe_done = False
        self._available = False
        self._mode: str = "off"          # "inproc" | "cli" | "off"

    # ─── availability ───────────────────────────────────────────────────
    def _probe(self) -> None:
        if self._probe_done:
            return
        self._probe_done = True
        if os.environ.get("LOADER_DISABLE_ATSPI", "").lower() in ("1", "true", "yes"):
            self._mode = "off"
            return
        try:
            import pyatspi  # type: ignore  # noqa: F401
            self._pyatspi = pyatspi
            self._mode = "inproc"
            self._available = True
            log.info("AT-SPI resolver: in-process pyatspi available")
            return
        except Exception:
            pass
        # Fall back to a CLI helper if present on PATH.
        if shutil.which("atspi-inspect") or shutil.which("python3"):
            self._mode = "cli"
            self._available = True
            log.info("AT-SPI resolver: using CLI helper mode")
        else:
            self._mode = "off"
            log.info("AT-SPI resolver: unavailable (pyatspi not importable, no python3 on PATH)")

    @property
    def available(self) -> bool:
        self._probe()
        return self._available

    @property
    def mode(self) -> str:
        self._probe()
        return self._mode

    # ─── public API ────────────────────────────────────────────────────
    async def resolve(
        self,
        loc: ElementLocator,
        *,
        display: int | None,
        window_rect: Rect | None,
        timeout: float = 2.5,
    ) -> Rect | None:
        """Return the element's rect, or ``None`` if not found.

        ``window_rect`` is the on-canvas rect of the host OS window (so
        pixel-hint fallbacks can be expressed relative to it). It is in
        the *OS-canvas* coordinate space, not the X display's.
        """
        self._probe()
        # 1) AT-SPI lookup
        if loc.role or loc.name:
            r = await self._resolve_atspi(loc, display=display, timeout=timeout)
            if r is not None:
                return r
        # 2) pixel hint relative to window
        if window_rect is not None and (loc.x_pct is not None or loc.y_pct is not None):
            xp = loc.x_pct if loc.x_pct is not None else 0.5
            yp = loc.y_pct if loc.y_pct is not None else 0.5
            cx = window_rect.x + int(xp * window_rect.w)
            cy = window_rect.y + int(yp * window_rect.h)
            # Treat as a small 30x30 target around the point.
            return Rect(cx - 15, cy - 15, 30, 30)
        return None

    # ─── internals ─────────────────────────────────────────────────────
    async def _resolve_atspi(
        self,
        loc: ElementLocator,
        *,
        display: int | None,
        timeout: float,
    ) -> Rect | None:
        if self._mode == "off":
            return None
        try:
            if self._mode == "inproc":
                return await self._resolve_inproc(loc, timeout=timeout)
            return await self._resolve_cli(loc, display=display, timeout=timeout)
        except Exception:
            log.exception("AT-SPI resolve failed for %r", loc)
            return None

    async def _resolve_inproc(self, loc: ElementLocator, *, timeout: float) -> Rect | None:
        """Walk the AT-SPI tree of the default desktop."""

        def _work() -> Rect | None:
            assert self._pyatspi is not None
            desktop = self._pyatspi.Registry.getDesktop(0)
            for i in range(desktop.childCount):
                app = desktop.getChildAt(i)
                rect = _walk(app, loc, depth=0, max_depth=8)
                if rect:
                    return rect
            return None

        return await asyncio.wait_for(asyncio.to_thread(_work), timeout=timeout)

    async def _resolve_cli(
        self,
        loc: ElementLocator,
        *,
        display: int | None,
        timeout: float,
    ) -> Rect | None:
        """Shell out to a tiny python3 helper that prints ``x y w h``."""
        snippet = (
            "import pyatspi,sys\n"
            "ROLE=pyatspi.role.getByName("
            "    (loc_role and ('ROLE_'+loc_role.upper())) or 'ROLE_INVALID')\n"
            "def walk(o,d=0):\n"
            "    if d>8: return None\n"
            "    try:\n"
            "        r=o.queryExtents()\n"
            "    except Exception:\n"
            "        r=None\n"
            "    try:\n"
            "        n=(o.name or '')\n"
            "        role=o.getRoleName()\n"
            "    except Exception:\n"
            "        n=''; role=''\n"
            "    if r and (not loc_role or role==loc_role):\n"
            "        if not loc_name or loc_name.lower() in (n or '').lower():\n"
            "            return r\n"
            "    try:\n"
            "        iface=o.querySelection() if False else None\n"
            "    except Exception:\n"
            "        pass\n"
            "    try:\n"
            "        cnt=o.childCount\n"
            "    except Exception:\n"
            "        cnt=0\n"
            "    for i in range(cnt):\n"
            "        try:\n"
            "            ch=o.getChildAt(i)\n"
            "        except Exception:\n"
            "            ch=None\n"
            "        if ch:\n"
            "            res=walk(ch,d+1)\n"
            "            if res: return res\n"
            "    return None\n"
            "loc_role=sys.argv[1] if len(sys.argv)>1 else ''\n"
            "loc_name=sys.argv[2] if len(sys.argv)>2 else ''\n"
            "res=walk(pyatspi.Registry.getDesktop(0))\n"
            "if res:\n"
            "    print(res.x,res.y,res.width,res.height)\n"
            "else:\n"
            "    sys.exit(2)\n"
        )
        env = os.environ.copy()
        if display is not None:
            env["DISPLAY"] = f":{display}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "python3", "-c", snippet,
                loc.role or "", loc.name or "",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                env=env,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except (FileNotFoundError, asyncio.TimeoutError):
            return None
        if proc.returncode != 0:
            return None
        parts = (out or b"").decode(errors="replace").split()
        if len(parts) != 4:
            return None
        try:
            x, y, w, h = (int(float(p)) for p in parts)
        except ValueError:
            return None
        return Rect(x, y, w, h)


def _walk(node: Any, loc: ElementLocator, *, depth: int, max_depth: int) -> Rect | None:
    """Depth-first search for the first node matching ``loc``.

    Lives at module scope so the in-process worker thread can call it
    without capturing ``self``.
    """
    if depth > max_depth:
        return None
    try:
        extents = node.queryExtents()
    except Exception:
        extents = None
    try:
        name = node.name or ""
    except Exception:
        name = ""
    try:
        role = node.getRoleName()
    except Exception:
        role = ""
    if extents is not None and (not loc.role or role == loc.role):
        if not loc.name or loc.name.lower() in (name or "").lower():
            # extents is a tuple-like (x, y, w, h) or an Atspi.Rect with attrs
            try:
                x = int(extents.x)
                y = int(extents.y)
                w = int(extents.width)
                h = int(extents.height)
                return Rect(x, y, w, h)
            except AttributeError:
                try:
                    x, y, w, h = (int(v) for v in extents)
                    return Rect(x, y, w, h)
                except Exception:
                    pass
    try:
        count = node.childCount
    except Exception:
        count = 0
    for i in range(count):
        try:
            child = node.getChildAt(i)
        except Exception:
            child = None
        if child is None:
            continue
        found = _walk(child, loc, depth=depth + 1, max_depth=max_depth)
        if found is not None:
            return found
    return None
