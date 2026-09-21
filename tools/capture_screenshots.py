"""Drive the running OS through a few representative tasks and capture
screenshots for the README.

Usage (server must already be listening):

    .venv\\Scripts\\python.exe tools\\capture_screenshots.py
    .venv\\Scripts\\python.exe tools\\capture_screenshots.py --port 8765

Each step sends a chat message, waits for the agent to go back to
``idle``, then screenshots either the whole UI or a single element.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

OUT = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


def post_chat(port: int, text: str) -> None:
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/chat",
        data=json.dumps({"text": text}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()


async def agent_status(port: int) -> str:
    """Read agentStatus from a fresh WebSocket snapshot."""
    import websockets

    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws", open_timeout=8) as ws:
            data = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
            return data.get("agentStatus", "?")
    except Exception:
        return "?"


async def wait_idle(port: int, *, timeout: float = 240.0, settle: float = 4.0) -> None:
    """Wait until the agent has been idle continuously for `settle` seconds."""
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    idle_since: float | None = None
    while loop.time() < deadline:
        st = await agent_status(port)
        now = loop.time()
        if st == "idle":
            if idle_since is None:
                idle_since = now
            elif now - idle_since >= settle:
                return
        else:
            idle_since = None
        await asyncio.sleep(2)
    print("  ! timed out waiting for idle")


async def shot(page, name: str, selector: str | None = None) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"{name}.png"
    if selector:
        await page.locator(selector).screenshot(path=str(path), timeout=20000)
    else:
        await page.screenshot(path=str(path), timeout=20000)
    kb = path.stat().st_size // 1024
    print(f"  -> {path.name} ({kb} KB)")


async def main(port: int) -> None:
    from playwright.async_api import async_playwright

    steps = [
        (
            "editor",
            "Напиши на Python функцию, которая считает числа Фибоначчи, "
            "и запусти её для первых 10 чисел.",
        ),
        (
            "paint",
            "Открой Paint и нарисуй там домик с крышей, дверью и солнцем.",
        ),
        (
            "browser",
            "Открой браузер и зайди на example.com",
        ),
    ]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        ctx = await browser.new_context(
            viewport={"width": 1600, "height": 950}, device_scale_factor=2
        )
        page = await ctx.new_page()
        await page.goto(f"http://127.0.0.1:{port}/", wait_until="networkidle", timeout=40000)
        print("page loaded; waiting for boot + VRM…")
        await page.wait_for_timeout(13000)

        # Clean desktop + avatar before anything is open.
        print("[0] baseline")
        await shot(page, "desktop", "#os")
        await shot(page, "avatar", "#cam-frame")

        for name, prompt in steps:
            print(f"[{name}] {prompt[:60]}…")
            post_chat(port, prompt)
            await wait_idle(port)
            await page.wait_for_timeout(2500)
            await shot(page, name, "#os")

        # Everything open at once -> hero shot of the full UI.
        print("[hero] full UI")
        await page.wait_for_timeout(1500)
        await shot(page, "hero")

        await browser.close()
    print("done")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    asyncio.run(main(args.port))
