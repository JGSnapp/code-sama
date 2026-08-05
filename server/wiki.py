"""Lightweight wiki: serve repo markdown + agent diagram for the Info panel."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

WIKI_PAGES: list[dict[str, str]] = [
    {"id": "readme", "title": "Обзор", "file": "README.md"},
    {"id": "architecture", "title": "Архитектура", "file": "Code-sama-architecture.md"},
    {"id": "agents", "title": "Агенты", "file": "docs/agents-diagram.md"},
    {"id": "vision", "title": "Видение", "file": "old_vision.md"},
    {"id": "harness", "title": "Harness", "file": "HARNESS.md"},
    {"id": "vroid", "title": "VRoid / VRM", "file": "characters/README.md"},
]


def list_pages() -> list[dict[str, str]]:
    pages = []
    for p in WIKI_PAGES:
        path = ROOT / p["file"]
        pages.append({
            "id": p["id"],
            "title": p["title"],
            "file": p["file"],
            "exists": path.is_file(),
        })
    return pages


def read_page(page_id: str) -> dict[str, str] | None:
    for p in WIKI_PAGES:
        if p["id"] != page_id:
            continue
        path = ROOT / p["file"]
        if not path.is_file():
            return {"id": p["id"], "title": p["title"], "markdown": f"_Файл не найден: `{p['file']}`_"}
        text = path.read_text(encoding="utf-8")
        # Cap huge architecture docs for the browser.
        if len(text) > 120_000:
            text = text[:120_000] + "\n\n… _(обрезано)_"
        return {"id": p["id"], "title": p["title"], "markdown": text, "file": p["file"]}
    return None
