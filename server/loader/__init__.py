"""code-sama-os App Loader — turn any Linux GUI/CLI program into a set of
agent-controllable tools that *still look like a human driving them*.

The loader is the in-OS analog of CLI-Anything: instead of producing a
REPL, it produces a **manifest** — a YAML/JSON description of:

  • which command launches the program,
  • how to launch it (headless? which DISPLAY? install deps first?),
  • a dictionary of **elements** the agent may want to target, each with
    an accessibility-aware locator (AT-SPI role/name, or a pixel hint),
  • a dictionary of **actions** the agent can call by name. Each action
    is a small choreography of ``hover``/``click``/``type``/``wait`` steps
    that the runtime plays back on the OS canvas so the viewer watches
    code-sama's cursor travel to menus, click them, and type — exactly
    like she already does for the built-in apps.

The manifest format is intentionally tiny and declarative. The 7-phase
``Harness`` (see ``harness.py``) generates one of these from a program
description using the LLM, then the ``Runtime`` executes actions from it
at request time.
"""

from .manifest import (
    ActionStep,
    AppManifest,
    ElementLocator,
    load_manifest,
)
from .registry import AppRegistry
from .runtime import Runtime
from .harness import Harness
from .vision_resolver import VisionResolver

__all__ = [
    "ActionStep",
    "AppManifest",
    "ElementLocator",
    "Harness",
    "AppRegistry",
    "Runtime",
    "VisionResolver",
    "load_manifest",
]
