# code-sama App Loader — HARNESS

This file is the *human-readable* companion to
`server/loader/harness.py`. It documents the 7-phase pipeline that turns
an arbitrary Linux program into a set of agent-controllable tools, and
serves as the spec the LLM follows when generating a manifest.

Unlike CLI-Anything (which produces a CLI REPL), our harness produces a
**manifest** — a declarative description of *visible* actions. Every
action animates the OS cursor and types with human rhythm, so the viewer
watches code-sama drive the real GUI. That's the whole point: the agent
is not allowed to "secretly" click anything — every interaction must be
visible on the Win95 canvas.

## The 7 phases

### 1. ANALYZE
What is this program? What package provides it (`apt`)? What are the
5–10 most useful operations an agent would do with it? (e.g. for
Firefox: navigate, search, go back, new tab, close tab.)

### 2. ELEMENTS
Name every widget / menu / field the agent will target. Prefer
AT-SPI `role` + `name` — these survive resize, theme, locale changes.
Fall back to `x_pct`/`y_pct` only for canvases / GL surfaces with no
a11y tree.

### 3. ACTIONS
Decompose each operation into a choreography of `hover`/`click`/`type`/
`key`/`wait`/`wait_for`/`assert`/`run` steps. Steps must feel like a
human: hover the menu, wait for it to open, click the item, fill the
field, click OK. Never collapse to a single magic step.

### 4. LAUNCH
Exact shell command + window size + any `apt` deps. The broker
spawns `cmd` on its own Xvfb display. If the program needs extra setup
(DBus, fonts, configs), list them in `package.setup` — they run once
in the workspace via `run_in_workspace`.

### 5. VALIDATE
Walk the JSON: every element name referenced in a step must exist in
`elements`. Every `params` field used in a step must be declared in
`params`. Auto-add centre-pixel fallbacks for dangling element
references so a partial manifest still runs.

### 6. NARRATE
Write **one** short Russian line per action (feeds the live narration
loop). First person, no emojis. Parameter substitution via `{name}`.

### 7. PUBLISH
Emit one JSON block. The harness parses it, wraps in an
`AppManifest`, persists to `apps/` (or `apps/generated/`), and the app
becomes available via `app_open` / `app_action` immediately.

## Manifest schema

```jsonc
{
  "app": "slug",                       // unique identifier
  "title": "Human Name",               // window title
  "icon": "browser|editor|paint|music|tracker|linux",
  "description": "1 sentence",
  "version": "1.0",
  "package": {
    "apt": "pkg-name",                 // apt-get install target
    "command": "binary",               // what to exec
    "setup": ["optional shell steps"]  // run once before first launch
  },
  "launch": {
    "cmd": "full command with args",
    "width": 900,
    "height": 560
  },
  "elements": {
    "name": { "role": "ENTRY", "name": "accessible name" },
    "pixel_one": { "x_pct": 0.5, "y_pct": 0.5 }
  },
  "actions": {
    "verb_name": {
      "params": { "arg": "str" },
      "narration": "Иду ... {arg}...",
      "steps": [
        { "click": "element_name", "dwell": 200 },
        { "type": "{arg}" },
        { "key": "Enter" },
        { "wait": 800 }
      ]
    }
  }
}
```

## Step reference

| verb       | value                                   | effect                                            |
|------------|-----------------------------------------|---------------------------------------------------|
| `hover`    | element name                            | move cursor onto element, no click                |
| `click`    | element name, OR `{element, button}`    | move + click (left/right/middle) on element       |
| `type`     | string (with `{params}`)                | focus last target, type with human rhythm         |
| `key`      | key name (`Enter`, `ctrl+s`, ...)       | press one key                                     |
| `wait`     | milliseconds (int)                      | sleep                                             |
| `wait_for` | element name                            | poll AT-SPI until element appears (≤2.5s)         |
| `assert`   | element name                            | wait_for + fail the action if not found           |
| `run`      | shell snippet                           | run inside workspace (no UI effect)               |
| `dwell`    | ms (optional sibling on any step)       | pause before this step for natural pacing         |

## Locator strategies (tried in order)

1. **AT-SPI** — `role` + `name` (+ optional `parent`). Resolved by
   `server/loader/atspi_resolver.py` via in-process `pyatspi` or a
   `python3` CLI helper on the X display. Most reliable.
2. **pixel hint** — `x_pct` / `y_pct` of the window body. Used for
   canvases without a11y (GIMP canvas, videos, games).
3. **window centre** — final fallback when neither match. Ensures a
   partial manifest still produces visible motion.

## Generating a new app

```python
from server.loader import Harness, AppRegistry

registry = AppRegistry()
registry.scan()
harness = Harness(registry)

await harness.install(
    "Audacity audio editor — record from mic, apply noise reduction, export MP3",
    app_slug="audacity",
)
```

Or from inside the OS, the agent simply calls the `app_install` tool
with a plain-language description — the harness does the rest.
