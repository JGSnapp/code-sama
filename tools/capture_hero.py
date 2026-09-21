"""Capture just the hero shot: one clean task, full-UI screenshot.

Run after a fresh server start so the chat history is empty.
"""

from __future__ import annotations

import asyncio
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from capture_screenshots import post_chat, shot, wait_idle  # noqa: E402

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PROMPT = (
    "Напиши на Python функцию is_prime и проверь ею числа от 1 до 20, "
    "потом открой Paint и нарисуй улыбающееся солнце."
)


async def main(port: int = 8765) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1600, "height": 950}, device_scale_factor=2
        )
        page = await ctx.new_page()
        await page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=40000)
        await page.wait_for_timeout(13000)

        post_chat(port, PROMPT)
        await wait_idle(port)
        await page.wait_for_timeout(2500)
        await shot(page, "hero")
        await shot(page, "chat", "#side")
        await browser.close()
    print("done")


if __name__ == "__main__":
    asyncio.run(main())
