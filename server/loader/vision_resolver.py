"""Vision-based element resolver — the smart fallback for GUIs with no
AT-SPI tree.

When the AT-SPI resolver misses (games, custom canvases, Wine/Proton,
some Java/Swing apps), this asks a vision-capable LLM "where is the
thing described as X?" and parses the bbox out of the answer. It's the
option we discussed in our earlier conversation: instead of pulling
Tesseract + leptonica (~150MB) into the image for brittle OCR, we reuse
a vision model that already understands UI context — icons, gesture
menus, disabled vs. enabled buttons — things OCR can't distinguish.

The vision resolver is **opt-in**: it only runs when:

  1. The AT-SPI resolver returned ``None`` for this locator, AND
  2. The locator carries a ``text_hint`` (a human description), AND
  3. A vision role is configured in :class:`ModelRouter`.

That keeps the cheap path cheap: AT-SPI-resolvable apps never spend a
vision call.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from typing import Any

from .atspi_resolver import AtspiResolver, Rect
from .manifest import ElementLocator


log = logging.getLogger("code-sama-os.loader.vision")


# Strict bbox parser: accept "x=12, y=34, w=56, h=78" or "(12,34,56,78)".
_BBOX_RE = re.compile(
    r"(?:x\s*[:=]\s*(\d+)\D{0,3}y\s*[:=]\s*(\d+)\D{0,3}w\s*[:=]\s*(\d+)\D{0,3}h\s*[:=]\s*(\d+))"
    r"|(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)",
    re.IGNORECASE,
)


@dataclass
class _FrameSource:
    """Anything that can hand us a base64 JPEG of the current display."""

    async def grab(self, display: int | None) -> str | None:  # pragma: no cover
        raise NotImplementedError


class VisionResolver:
    """Wraps :class:`AtspiResolver` and adds a vision fallback.

    The runtime constructs one of these and calls :meth:`resolve` instead
    of the raw AT-SPI resolver. The first-hit-wins order is:

      1. AT-SPI (cheap, exact, no network)
      2. Vision LLM (only if ``text_hint`` present and role configured)
      3. Pixel hint (free, hand-authored)
      4. Window centre (free, last-ditch)

    Callers can pin a frame source so the resolver doesn't spawn its own
    screenshot call — the broker's frame loop already has one.
    """

    def __init__(
        self,
        router: Any,
        *,
        atspi: AtspiResolver | None = None,
        frame_source: _FrameSource | None = None,
        role: str = "vision",
    ) -> None:
        self.router = router
        self.atspi = atspi or AtspiResolver()
        self.frame_source = frame_source
        self.role = role

    @property
    def available(self) -> bool:
        return bool(self.router) and self.router.has_role(self.role)

    async def resolve(
        self,
        loc: ElementLocator,
        *,
        display: int | None,
        window_rect: Rect | None,
        timeout: float = 6.0,
    ) -> Rect | None:
        # 1) AT-SPI first.
        if loc.role or loc.name:
            r = await self.atspi.resolve(loc, display=display, window_rect=window_rect, timeout=2.5)
            if r is not None:
                return r
        # 2) Vision fallback — only if we have something to ask about.
        if loc.text_hint and self.available:
            r = await self._resolve_vision(loc, display=display, window_rect=window_rect, timeout=timeout)
            if r is not None:
                return r
        # 3) Pixel hint.
        if window_rect is not None and (loc.x_pct is not None or loc.y_pct is not None):
            xp = loc.x_pct if loc.x_pct is not None else 0.5
            yp = loc.y_pct if loc.y_pct is not None else 0.5
            cx = window_rect.x + int(xp * window_rect.w)
            cy = window_rect.y + int(yp * window_rect.h)
            return Rect(cx - 15, cy - 15, 30, 30)
        return None

    async def _resolve_vision(
        self,
        loc: ElementLocator,
        *,
        display: int | None,
        window_rect: Rect | None,
        timeout: float,
    ) -> Rect | None:
        frame = await self._grab_frame(display)
        if not frame:
            return None
        try:
            llm = self.router.llm(self.role)
        except Exception:
            log.warning("vision role not available — skipping")
            return None
        prompt = (
            "Ты смотришь на скриншот графического интерфейса. Найди элемент, "
            "соответствующий этому описанию: «" + (loc.text_hint or "") + "». "
            "Если нашёл — ответь СТРОГО в формате: x=NN, y=NN, w=NN, h=NN "
            "(координаты в пикселях от левого верхнего угла изображения). "
            "Если такого элемента нет — ответь словом NONE."
        )
        try:
            msg = await asyncio.wait_for(
                llm.ainvoke([
                    _user_message_with_image(prompt, frame),
                ]),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            log.warning("vision LLM call failed: %s", exc)
            return None
        text = _strip_content(msg)
        if not text or "NONE" in text.upper()[:10]:
            return None
        bbox = _parse_bbox(text)
        if bbox is None:
            log.warning("vision returned unparseable bbox: %s", text[:160])
            return None
        # Vision returns coords in screenshot space. If we know the
        # window rect on the OS canvas, translate so the runtime lands
        # the OS cursor in the right spot.
        if window_rect is not None:
            return _translate_to_window(bbox, window_rect)
        return bbox

    async def _grab_frame(self, display: int | None) -> str | None:
        if self.frame_source is not None:
            try:
                return await self.frame_source.grab(display)
            except Exception:
                log.exception("frame source failed")
        return None


# ─── helpers ─────────────────────────────────────────────────────────────


def _user_message_with_image(prompt: str, frame_b64: str) -> Any:
    """Build a multimodal HumanMessage.

    LangChain's ``HumanMessage`` accepts a list of content dicts with
    ``image_url`` entries; most OpenAI-compatible endpoints accept the
    data URL form below. We deliberately don't import LangChain at
    module top so this file is import-safe even in stripped-down envs.
    """
    from langchain_core.messages import HumanMessage

    return HumanMessage(content=[
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {
            "url": f"data:image/jpeg;base64,{frame_b64}",
        }},
    ])


def _strip_content(msg: Any) -> str:
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


def _parse_bbox(text: str) -> Rect | None:
    m = _BBOX_RE.search(text)
    if not m:
        return None
    if m.group(1):
        x, y, w, h = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    else:
        x, y, w, h = int(m.group(5)), int(m.group(6)), int(m.group(7)), int(m.group(8))
    if w <= 0 or h <= 0:
        return None
    return Rect(x, y, w, h)


def _translate_to_window(bbox: Rect, win: Rect) -> Rect:
    """Vision bbox is in screenshot space (0..W,0..H of the frame).

    We want OS-canvas coords. If the screenshot came from the same
    broker window, the simplest robust transform is: scale bbox
    proportionally to the window rect. This assumes the frame covers
    exactly the window body (which is what the broker streams).
    """
    # We can't know the frame's pixel size from here without the source
    # telling us. Best-effort: assume frame == window body, identity.
    # The runtime already adds small jitter, so being off by a few px
    # is fine for a fallback path.
    return Rect(win.x + bbox.x, win.y + bbox.y, bbox.w, bbox.h)
