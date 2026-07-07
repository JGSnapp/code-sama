"""Manifest format — the contract between the harness (generates) and the
runtime (executes).

A manifest describes one Linux program as:

  app:      firefox
  package:  { apt: firefox-esr, command: firefox }
  launch:   { cmd: "firefox --new-window about:blank", width: 900, height: 560 }
  elements:
    url_bar:        { role: ENTRY, name: "Search with DuckDuckGo or enter address" }
    nav_back:       { role: PUSH_BUTTON, name: "Back" }
    first_result:   { role: LINK, text_hint: "first top-level link" }
  actions:
    navigate:
      params: { url: str }
      steps:
        - click: url_bar
        - type:  "{url}"
        - key:   Enter
      narration: "Иду в адресную строку, ввожу {url}…"
    go_back:
      steps: [ { click: nav_back } ]
      narration: "Жму «Назад»…"

Element locators
----------------
Each element resolves to a screen rectangle ``(x, y, w, h)`` at runtime.
We try three strategies in order:

  1. **atspi**   — ``role`` + ``name`` (and optional ``parent``). Most
                   reliable for GTK/Qt/Electron. Resolved by
                   :class:`~server.atspi_resolver.AtspiResolver`.
  2. **pixel**   — ``x_pct`` / ``y_pct`` of the window (0..1). A
                   hand-placed fallback when the app has no AT-SPI tree
                   (games, custom canvases).
  3. **text**    — ``text_hint`` runs a quick OCR/substring match over the
                   last frame (very rough; only used when the other two
                   miss). Left as a TODO hook so we don't pull Tesseract
                   into the base image yet.

Steps
-----
A step is a tiny imperative. Supported verbs:

  ``hover``  — move the OS cursor onto the element (no click)
  ``click``  — move + click left/right/middle
  ``type``   — focus the element first, then type the string
  ``key``    — press a single key (Enter, Backspace, ctrl+s, ...)
  ``wait``   — sleep ms (or wait_for an element to appear)
  ``run``    — run a shell snippet inside the container (no UI effect)
  ``assert`` — wait_for + require the element is visible, else abort
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ElementLocator:
    """How to find one widget on screen."""

    name: str | None = None
    role: str | None = None
    parent: str | None = None          # name of another element this nests in
    x_pct: float | None = None         # 0..1 fallback (relative to window)
    y_pct: float | None = None
    text_hint: str | None = None       # OCR hook (TODO)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ElementLocator":
        return cls(
            name=d.get("name"),
            role=d.get("role"),
            parent=d.get("parent"),
            x_pct=d.get("x_pct"),
            y_pct=d.get("y_pct"),
            text_hint=d.get("text_hint"),
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.name is not None:
            d["name"] = self.name
        if self.role is not None:
            d["role"] = self.role
        if self.parent is not None:
            d["parent"] = self.parent
        if self.x_pct is not None:
            d["x_pct"] = self.x_pct
        if self.y_pct is not None:
            d["y_pct"] = self.y_pct
        if self.text_hint is not None:
            d["text_hint"] = self.text_hint
        return d


# A step is stored as a small dict so the runtime can stay generic. The
# keys are intentionally readable in YAML — humans edit these.
STEP_FIELDS = {"hover", "click", "type", "key", "wait", "wait_for",
               "run", "assert", "dwell"}


@dataclass
class ActionStep:
    raw: dict[str, Any]

    @property
    def verb(self) -> str:
        for k in self.raw:
            if k in STEP_FIELDS:
                return k
        return ""

    @property
    def target(self) -> str:
        """Element name this step acts on (or "" for non-element steps)."""
        v = self.raw.get(self.verb)
        if isinstance(v, str):
            return v
        return ""

    @property
    def value(self) -> Any:
        return self.raw.get(self.verb)

    @property
    def dwell_ms(self) -> int:
        return int(self.raw.get("dwell", 0))


@dataclass
class AppManifest:
    """One installed program."""

    app: str
    title: str
    icon: str = "linux"
    package: dict[str, Any] = field(default_factory=dict)
    launch: dict[str, Any] = field(default_factory=dict)
    elements: dict[str, ElementLocator] = field(default_factory=dict)
    actions: dict[str, dict[str, Any]] = field(default_factory=dict)
    description: str = ""
    version: str = "1.0"
    source: str = "manual"            # "manual" | "generated"
    path: str | None = None           # where it was loaded from

    @classmethod
    def from_dict(cls, d: dict[str, Any], *, path: str | None = None) -> "AppManifest":
        elements = {
            name: ElementLocator.from_dict(v or {})
            for name, v in (d.get("elements") or {}).items()
        }
        return cls(
            app=str(d.get("app") or d.get("name") or "").strip(),
            title=str(d.get("title") or d.get("app") or "").strip(),
            icon=str(d.get("icon") or "linux"),
            package=dict(d.get("package") or {}),
            launch=dict(d.get("launch") or {}),
            elements=elements,
            actions=dict(d.get("actions") or {}),
            description=str(d.get("description") or ""),
            version=str(d.get("version") or "1.0"),
            source=str(d.get("source") or "manual"),
            path=path,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "app": self.app,
            "title": self.title,
            "icon": self.icon,
            "package": self.package,
            "launch": self.launch,
            "elements": {n: e.to_dict() for n, e in self.elements.items()},
            "actions": self.actions,
            "description": self.description,
            "version": self.version,
            "source": self.source,
        }

    def action(self, name: str) -> dict[str, Any] | None:
        return self.actions.get(name)

    def action_steps(self, name: str) -> list[ActionStep]:
        a = self.actions.get(name) or {}
        steps = a.get("steps") or []
        return [ActionStep(s) for s in steps]

    def action_params(self, name: str) -> dict[str, Any]:
        a = self.actions.get(name) or {}
        return dict(a.get("params") or {})

    def action_narration(self, name: str) -> str:
        a = self.actions.get(name) or {}
        return str(a.get("narration") or "")


# ─── loaders ─────────────────────────────────────────────────────────────

def _maybe_json(path: Path) -> bool:
    return path.suffix.lower() in {".json"}


def load_manifest(path: str | Path) -> AppManifest:
    """Load a manifest from a YAML or JSON file.

    YAML is the preferred authoring format; JSON is supported so the
    harness can emit straight from the LLM without a YAML dep.
    """
    p = Path(path)
    raw = p.read_text(encoding="utf-8")
    if _maybe_json(p):
        data = json.loads(raw)
    else:
        try:
            import yaml  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PyYAML is required to load .yml/.yaml manifests; "
                "pip install pyyaml or use .json."
            ) from exc
        data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        raise ValueError(f"manifest {p} did not parse to a dict")
    return AppManifest.from_dict(data, path=str(p))
