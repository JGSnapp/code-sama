"""Headless Chromium for the in-OS Browser window.

We don't ship the live browser to the client — we render the page in a
Playwright instance on the server and push compressed PNG screenshots over
the WebSocket whenever something changes. That way the agent can use a real
browser (with JS, redirects, real network) while the OS canvas stays passive.
"""

from __future__ import annotations

import asyncio
import base64
import logging
from typing import Any

log = logging.getLogger(__name__)


class BrowserEngine:
    def __init__(self, width: int = 900, height: int = 560, headless: bool = True) -> None:
        self.width = width
        self.height = height
        self.headless = headless
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._lock = asyncio.Lock()
        self._available = False

    async def start(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:  # pragma: no cover
            log.warning("Playwright not installed: %s", exc)
            return
        try:
            self._pw = await async_playwright().start()
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-features=IsolateOrigins,site-per-process",
            ]
            self._browser = await self._pw.chromium.launch(
                headless=self.headless, args=launch_args
            )
            self._context = await self._browser.new_context(
                viewport={"width": self.width, "height": self.height},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                locale="ru-RU",
                timezone_id="Europe/Moscow",
                extra_http_headers={
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                },
            )
            # Hide the navigator.webdriver flag that headless Chromium sets.
            await self._context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            self._page = await self._context.new_page()
            await self._page.goto("about:blank")
            self._available = True
        except Exception as exc:  # pragma: no cover
            log.warning("Failed to start Playwright: %s", exc)
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    async def stop(self) -> None:
        try:
            if self._context:
                await self._context.close()
            if self._browser:
                await self._browser.close()
            if self._pw:
                await self._pw.stop()
        except Exception:  # pragma: no cover
            pass

    async def navigate(self, url: str, *, timeout_ms: int = 20000) -> dict[str, Any]:
        if not self._available:
            return {"ok": False, "error": "browser_unavailable"}
        async with self._lock:
            target = self._resolve_url(url)
            try:
                resp = await self._page.goto(target, timeout=timeout_ms, wait_until="domcontentloaded")
                try:
                    await self._page.wait_for_load_state("networkidle", timeout=4000)
                except Exception:  # pragma: no cover
                    pass
                title = await self._page.title()
                return {
                    "ok": True,
                    "url": self._page.url,
                    "title": title,
                    "status": resp.status if resp else None,
                    "search": target != url and target.startswith("https://duckduckgo.com/"),
                }
            except Exception as exc:
                return {"ok": False, "error": str(exc), "url": target}

    @staticmethod
    def _resolve_url(raw: str) -> str:
        """Convert user input into a real URL.

        Accepts plain URLs, bare hostnames (example.com), or natural-language
        queries — the last group is routed through DuckDuckGo's lite endpoint
        so the page renders fast and reliably even in headless Chromium.
        """
        import re
        from urllib.parse import quote_plus

        s = (raw or "").strip()
        if not s:
            return "about:blank"
        low = s.lower()
        if low.startswith(("http://", "https://", "about:", "data:", "file:")):
            return s
        # bare hostname like example.com or sub.example.co.uk
        if " " not in s and re.search(r"^[\w.-]+\.[a-zA-Z]{2,}([/?#].*)?$", s):
            return "https://" + s
        # Bing is the most reliable search engine for headless Chromium —
        # DuckDuckGo (both the JS and HTML endpoints) 403s the Playwright
        # signature; Google requires a long consent dance and CAPTCHA. Bing
        # just serves results.
        return "https://www.bing.com/search?q=" + quote_plus(s)

    async def back(self) -> None:
        if not self._available:
            return
        async with self._lock:
            try:
                await self._page.go_back(wait_until="domcontentloaded")
            except Exception:  # pragma: no cover
                pass

    async def reload(self) -> None:
        if not self._available:
            return
        async with self._lock:
            try:
                await self._page.reload(wait_until="domcontentloaded")
            except Exception:  # pragma: no cover
                pass

    async def screenshot_b64(self, *, quality: int = 60) -> str | None:
        if not self._available:
            return None
        async with self._lock:
            try:
                png = await self._page.screenshot(type="jpeg", quality=quality, full_page=False)
                return base64.b64encode(png).decode("ascii")
            except Exception as exc:  # pragma: no cover
                log.debug("screenshot failed: %s", exc)
                return None

    async def get_text(self, max_chars: int = 4000) -> str:
        if not self._available:
            return ""
        async with self._lock:
            try:
                text = await self._page.evaluate("() => document.body ? document.body.innerText : ''")
                return (text or "")[:max_chars]
            except Exception:  # pragma: no cover
                return ""

    async def current_url(self) -> str:
        if not self._available or not self._page:
            return "about:blank"
        try:
            return self._page.url
        except Exception:
            return "about:blank"
