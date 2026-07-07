"""Runtime — executes a manifest action as a *visible* choreography.

The runtime is the heart of the loader. Given an open app (a Linux window
owned by :class:`server.linux_broker.LinuxBroker`) and an action name, it:

  1. Resolves every element the action references to a screen rect
     (via :class:`AtspiResolver`, with pixel fallback).
  2. Walks the action's ``steps`` list, animating each one through the
     shared :class:`OSController` — so the OS cursor *travels* to the
     target, the click ripple plays, and the keystrokes get typed with
     the same human rhythm the built-in apps already use.
  3. Simultaneously forwards the real input into the X display via
     ``LinuxBroker`` so the underlying program actually responds.
  4. Emits ``progress`` events onto an optional callback so the
     narration loop (idea 3) can comment on what's happening in real
     time.

The visual performance and the real input are two views of the *same*
coordinates. We never inject a click behind the viewer's back: every
``click`` step animates the cursor to the resolved centre and only then
fires ``xdotool`` on the same pixel. That is the discipline that lets us
keep code-sama's "I'm doing it myself" illusion while staying robust
against UI drift.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..state import OSController, Window
from .atspi_resolver import AtspiResolver, Rect
from .manifest import ActionStep, AppManifest, ElementLocator


log = logging.getLogger("code-sama-os.loader.runtime")


# A progress callback gets a small dict {phase, element, narration?, pct?}
# for each step. The Coordinator wires this to the narration bus.
ProgressCb = Callable[[dict[str, Any]], Awaitable[None]]


@dataclass
class _OpenApp:
    """An app currently running and bound to an OS window."""

    manifest: AppManifest
    window_id: str        # OS window id (the Win95 window)
    linux_id: str | None  # broker id (None for OS-native apps)
    display: int | None   # X display number
    os_window: Window
    birth_ts: float = field(default_factory=lambda: __import__("time").time())


class Runtime:
    """Owns the set of running loader-apps and plays their actions."""

    def __init__(
        self,
        controller: OSController,
        broker: Any,
        resolver: AtspiResolver | None = None,
    ) -> None:
        self.controller = controller
        self.broker = broker
        self.resolver = resolver or AtspiResolver()
        # app name -> open instance. Only one window per app for now
        # (matches the OS's built-in app model).
        self._open: dict[str, _OpenApp] = {}
        # Optional progress callback wired up by main.py — per-app tools
        # (which don't get a per-call progress kwarg) use this so their
        # steps still feed the narration loop.
        self._progress_cb: ProgressCb | None = None

    def set_progress_callback(self, cb: ProgressCb | None) -> None:
        """Wire a narration-loop callback. Called by Coordinator setup."""
        self._progress_cb = cb

    # ─── introspection ────────────────────────────────────────────────
    def is_open(self, app: str) -> bool:
        return app in self._open

    def open_apps(self) -> list[str]:
        return list(self._open.keys())

    def get_open(self, app: str) -> _OpenApp | None:
        return self._open.get(app)

    # ─── lifecycle ────────────────────────────────────────────────────
    async def open_app(
        self,
        manifest: AppManifest,
        *,
        width: int | None = None,
        height: int | None = None,
        progress: ProgressCb | None = None,
    ) -> dict[str, Any]:
        """Launch the program and bind it to a Win95 window.

        Returns the new OS window id. Idempotent: re-opening an already
        open app just focuses it.
        """
        if manifest.app in self._open:
            await self.controller.bring_to_front(self._open[manifest.app].window_id)
            return {"ok": True, "windowId": self._open[manifest.app].window_id,
                    "already_open": True}

        launch = manifest.launch or {}
        w = int(width or launch.get("width") or 800)
        h = int(height or launch.get("height") or 540)

        # 1) Open the Win95 window first so the viewer sees it appear.
        #    We register it as a synthetic app so the frontend's LinuxApp
        #    renderer picks it up.
        os_window_id = await self.controller.open_app(
            "linux", width=w, height=h
        )
        # Patch the title so the title bar shows the real program name.
        await self.controller.patch_app_state(
            os_window_id,
            {
                "title": manifest.title or manifest.app,
                "app": manifest.app,
                "icon": manifest.icon,
                "display": None,
                "width": w,
                "height": h,
                "frame": None,
                "status": "launching",
            },
        )
        os_window = self.controller.windows[os_window_id]
        if progress:
            await progress({"phase": "launch", "app": manifest.app,
                            "narration": f"Запускаю {manifest.title or manifest.app}…"})

        # 2) Hand the actual program to the broker. If the broker is
        #    unavailable (e.g. running on Windows host without Xvfb),
        #    we still keep the OS window open as a "preview" surface —
        #    useful for development.
        linux_id: str | None = None
        display: int | None = None
        broker_err: str | None = None
        cmd = launch.get("cmd") or _default_cmd(manifest)
        if self.broker and getattr(self.broker, "available", False):
            try:
                res = await self.broker.launch(
                    cmd, title=manifest.title or manifest.app,
                    width=w, height=h,
                )
                if res.get("ok"):
                    linux_id = res.get("windowId")
                    display = res.get("display")
                else:
                    broker_err = res.get("error") or "broker_failed"
            except Exception as exc:  # pragma: no cover
                broker_err = f"{type(exc).__name__}: {exc}"
                log.exception("broker launch failed for %s", manifest.app)
        else:
            broker_err = "linux_unavailable"

        await self.controller.patch_app_state(
            os_window_id,
            {"display": display, "status": "ready" if linux_id else broker_err,
             "error": broker_err if not linux_id else None},
        )

        self._open[manifest.app] = _OpenApp(
            manifest=manifest,
            window_id=os_window_id,
            linux_id=linux_id,
            display=display,
            os_window=os_window,
        )
        return {"ok": True, "windowId": os_window_id, "linuxId": linux_id,
                "display": display, "broker_error": broker_err}

    async def close_app(self, app: str) -> dict[str, Any]:
        inst = self._open.pop(app, None)
        if not inst:
            return {"ok": False, "error": "not_open"}
        if inst.linux_id and self.broker:
            try:
                await self.broker.close(inst.linux_id)
            except Exception:
                log.exception("broker close failed for %s", app)
        await self.controller.close_window(inst.window_id)
        return {"ok": True}

    async def close_all(self) -> None:
        for app in list(self._open.keys()):
            await self.close_app(app)

    # ─── action execution ─────────────────────────────────────────────
    async def run_action(
        self,
        app: str,
        action: str,
        args: dict[str, Any] | None = None,
        *,
        progress: ProgressCb | None = None,
    ) -> dict[str, Any]:
        """Run an action against an already-open app. Returns not_open
        error if the app isn't running — see :meth:`run_action_autoopen`
        for the auto-launching variant used by per-app tools."""
        inst = self._open.get(app)
        if inst is None:
            return {"ok": False, "error": "not_open",
                    "detail": f"{app} is not running; call app_open first"}
        manifest = inst.manifest
        if action not in manifest.actions:
            return {"ok": False, "error": "unknown_action",
                    "detail": f"{action} not in {manifest.app}; available: "
                              f"{sorted(manifest.actions.keys())}"}
        args = args or {}
        # Interpolate {placeholders} in step values + narration.
        steps = manifest.action_steps(action)
        rendered = [_render_step(s, args) for s in steps]
        narration_tpl = manifest.action_narration(action)
        narration = _safe_format(narration_tpl, args) if narration_tpl else ""
        if progress and narration:
            await progress({"phase": "action_start", "app": app, "action": action,
                            "narration": narration})

        await self.controller.bring_to_front(inst.window_id)
        await self.controller.set_agent_status("acting")

        results: list[dict[str, Any]] = []
        for idx, step in enumerate(rendered):
            try:
                r = await self._exec_step(inst, step, progress=progress)
                r["step"] = idx
                r["verb"] = step.verb
                results.append(r)
                if not r.get("ok", True):
                    break
            except Exception as exc:  # pragma: no cover
                log.exception("step failed: %s", step.raw)
                results.append({"ok": False, "error": f"{type(exc).__name__}: {exc}",
                                "step": idx, "verb": step.verb})
                break
        await self.controller.set_agent_status("idle")
        ok = all(r.get("ok", True) for r in results)
        if progress:
            await progress({"phase": "action_end", "app": app, "action": action, "ok": ok})
        return {"ok": ok, "app": app, "action": action, "results": results,
                "narration": narration}

    # ─── step primitives ──────────────────────────────────────────────
    async def run_action_autoopen(
        self,
        app: str,
        action: str,
        args: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Auto-opening variant used by per-app tools.

        If ``app`` is already running, this is identical to
        :meth:`run_action`. If not, the manifest is looked up, the app
        is launched (visibly, with the cursor moving over its new
        window), and *then* the action runs. The narration loop sees
        both the launch and the action's steps.

        This is what makes generated tools "auto-connect skills": the
        agent calls ``firefox_navigate(url)`` and Firefox boots if it
        isn't already up — no separate ``app_open`` step required.
        """
        if app not in self._open:
            # We need the manifest to open; look it up from whoever owns
            # the registry. The runtime itself is deliberately decoupled
            # from the registry, so callers that want auto-open pass the
            # manifest in. Per-app tools (built by ToolRegistry) hold a
            # reference to the manifest directly — they use the
            # ``_manifest`` kwarg below.
            manifest = args.pop("_manifest", None) if args else None
            if manifest is None:
                return {"ok": False, "error": "not_open",
                        "detail": f"{app} isn't running and no manifest was "
                                  f"provided for auto-open."}
            open_result = await self.open_app(manifest)
            if not open_result.get("ok"):
                return open_result
        # Plumb progress via the controller-bound callback if one is set.
        progress = self._progress_cb
        return await self.run_action(app, action, args, progress=progress)
    async def _exec_step(
        self,
        inst: _OpenApp,
        step: ActionStep,
        *,
        progress: ProgressCb | None,
    ) -> dict[str, Any]:
        verb = step.verb
        if verb == "hover":
            return await self._do_hover(inst, step, progress)
        if verb == "click":
            return await self._do_click(inst, step, progress)
        if verb == "type":
            return await self._do_type(inst, step, progress)
        if verb == "key":
            return await self._do_key(inst, step, progress)
        if verb == "wait":
            return await self._do_wait(step)
        if verb == "wait_for":
            return await self._do_wait_for(inst, step)
        if verb == "run":
            return await self._do_run(inst, step)
        if verb == "assert":
            return await self._do_assert(inst, step)
        log.warning("unknown step verb %r in %r", verb, step.raw)
        return {"ok": False, "error": f"unknown_verb:{verb}"}

    async def _do_hover(self, inst: _OpenApp, step: ActionStep, progress: ProgressCb | None) -> dict[str, Any]:
        target = step.target
        rect = await self._target_rect(inst, target)
        if rect is None:
            return {"ok": False, "error": f"unresolved:{target}"}
        await self._animate_to(inst, rect, progress=progress, target_name=target)
        if step.dwell_ms:
            await asyncio.sleep(step.dwell_ms / 1000.0)
        return {"ok": True, "element": target, "rect": rect.to_dict()}

    async def _do_click(self, inst: _OpenApp, step: ActionStep, progress: ProgressCb | None) -> dict[str, Any]:
        # ``click`` may be either ``click: element_name`` or
        # ``click: { element: ..., button: ... }``.
        target, button = _parse_click_value(step.value)
        rect = await self._target_rect(inst, target)
        if rect is None:
            return {"ok": False, "error": f"unresolved:{target}"}
        await self._animate_to(inst, rect, progress=progress, target_name=target)
        cx, cy = rect.center()
        btn_num = {"left": 1, "middle": 2, "right": 3}.get(button, 1)
        await self.controller.click(cx, cy, button=button)
        if inst.linux_id and self.broker:
            try:
                await self.broker.click(inst.linux_id, cx, cy, button=btn_num)
            except Exception:
                log.exception("broker click failed")
        if progress:
            await progress({"phase": "click", "app": inst.manifest.app,
                            "element": target,
                            "narration": f"Кликаю «{target}»…"})
        return {"ok": True, "element": target, "button": button, "rect": rect.to_dict()}

    async def _do_type(self, inst: _OpenApp, step: ActionStep, progress: ProgressCb | None) -> dict[str, Any]:
        text = step.value if isinstance(step.value, str) else ""
        # Focus the previously hovered/clicked element by clicking it once.
        # If ``type`` carries its own ``target``, resolve & click it first.
        target = None
        if isinstance(step.raw, dict):
            tv = step.raw.get("target")
            if isinstance(tv, str):
                target = tv
        if target:
            rect = await self._target_rect(inst, target)
            if rect:
                await self._animate_to(inst, rect, progress=progress, target_name=target)
                await self.controller.click(rect.center()[0], rect.center()[1])
                if inst.linux_id and self.broker:
                    try:
                        cx, cy = rect.center()
                        await self.broker.click(inst.linux_id, cx, cy, button=1)
                    except Exception:
                        log.exception("broker focus-click failed")
        await self.controller.type_text(text)
        if inst.linux_id and self.broker:
            try:
                await self.broker.type_text(inst.linux_id, text)
            except Exception:
                log.exception("broker type failed")
        if progress:
            preview = text if len(text) <= 40 else text[:37] + "…"
            await progress({"phase": "type", "app": inst.manifest.app,
                            "narration": f"Печатаю: {preview}"})
        return {"ok": True, "len": len(text)}

    async def _do_key(self, inst: _OpenApp, step: ActionStep, progress: ProgressCb | None) -> dict[str, Any]:
        key = step.value if isinstance(step.value, str) else ""
        await self.controller.press_key(_os_key(key))
        if inst.linux_id and self.broker:
            try:
                await self.broker.key(inst.linux_id, _xdotool_key(key))
            except Exception:
                log.exception("broker key failed")
        if progress:
            await progress({"phase": "key", "app": inst.manifest.app,
                            "narration": f"Жму {key.replace('ctrl+', 'Ctrl+')}…"})
        return {"ok": True, "key": key}

    async def _do_wait(self, step: ActionStep) -> dict[str, Any]:
        ms = int(step.value) if isinstance(step.value, (int, float)) else 250
        await asyncio.sleep(max(0, min(4000, ms)) / 1000.0)
        return {"ok": True, "ms": ms}

    async def _do_wait_for(self, inst: _OpenApp, step: ActionStep) -> dict[str, Any]:
        target = step.target or (step.value if isinstance(step.value, str) else "")
        deadline = _loop_deadline(step.raw.get("timeout_ms", 2500))
        while True:
            rect = await self._target_rect(inst, target)
            if rect is not None:
                return {"ok": True, "element": target}
            if _loop_expired(deadline):
                return {"ok": False, "error": f"timeout:{target}"}
            await asyncio.sleep(0.2)

    async def _do_run(self, inst: _OpenApp, step: ActionStep) -> dict[str, Any]:
        snippet = step.value if isinstance(step.value, str) else ""
        if not self.broker or not getattr(self.broker, "available", False):
            return {"ok": False, "error": "linux_unavailable"}
        try:
            r = await self.broker.run_in_workspace(snippet)
        except Exception as exc:
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return r if isinstance(r, dict) else {"ok": True, "raw": r}

    async def _do_assert(self, inst: _OpenApp, step: ActionStep) -> dict[str, Any]:
        target = step.target or (step.value if isinstance(step.value, str) else "")
        rect = await self._target_rect(inst, target)
        if rect is None:
            return {"ok": False, "error": f"assert_failed:{target}"}
        return {"ok": True, "element": target, "rect": rect.to_dict()}

    # ─── helpers ──────────────────────────────────────────────────────
    async def _target_rect(self, inst: _OpenApp, name: str) -> Rect | None:
        if not name:
            return None
        loc = inst.manifest.elements.get(name)
        if loc is None:
            # An unknown element name is a soft failure — fall back to
            # the window centre so a partially-authored manifest still
            # produces *some* visible motion rather than stalling.
            log.warning("element %r not in manifest %s — using window centre",
                        name, inst.manifest.app)
            w = inst.os_window
            return Rect(w.x + 30, w.y + 30, max(1, w.w - 60), max(1, w.h - 60))
        win_rect = _os_window_to_rect(inst.os_window)
        rect = await self.resolver.resolve(loc, display=inst.display, window_rect=win_rect)
        if rect is not None:
            return rect
        # Final fallback: window centre + tiny jitter.
        cx = win_rect.x + win_rect.w // 2 + random.randint(-8, 8)
        cy = win_rect.y + win_rect.h // 2 + random.randint(-8, 8)
        return Rect(cx - 12, cy - 12, 24, 24)

    async def _animate_to(
        self,
        inst: _OpenApp,
        rect: Rect,
        *,
        progress: ProgressCb | None,
        target_name: str,
    ) -> None:
        cx, cy = rect.center()
        # Tiny human jitter inside the bbox — never exactly the centre.
        jx = random.randint(-max(1, rect.w // 6), max(1, rect.w // 6))
        jy = random.randint(-max(1, rect.h // 6), max(1, rect.h // 6))
        await self.controller.move_cursor(cx + jx, cy + jy)


# ─── helpers ─────────────────────────────────────────────────────────────


def _os_window_to_rect(w: Window) -> Rect:
    # Body rect — skip the ~28px title bar so pixel hints land inside
    # the app surface, not on the window chrome.
    return Rect(w.x + 6, w.y + 30, max(1, w.w - 12), max(1, w.h - 38))


def _default_cmd(manifest: AppManifest) -> str:
    pkg = manifest.package or {}
    cmd = pkg.get("command") or manifest.app
    return str(cmd)


def _render_step(step: ActionStep, args: dict[str, Any]) -> ActionStep:
    """Return a copy of ``step`` with ``{placeholders}`` filled in."""
    raw = dict(step.raw)
    for k, v in list(raw.items()):
        if isinstance(v, str):
            raw[k] = _safe_format(v, args)
        elif isinstance(v, dict):
            raw[k] = {kk: (_safe_format(vv, args) if isinstance(vv, str) else vv)
                      for kk, vv in v.items()}
    return ActionStep(raw)


def _safe_format(text: str, args: dict[str, Any]) -> str:
    try:
        return text.format(**{k: ("" if v is None else v) for k, v in args.items()})
    except (KeyError, IndexError, ValueError):
        return text


def _parse_click_value(value: Any) -> tuple[str, str]:
    if isinstance(value, str):
        return value, "left"
    if isinstance(value, dict):
        return str(value.get("element") or ""), str(value.get("button") or "left")
    return "", "left"


def _os_key(key: str) -> str:
    """Map manifest key names → OSController key names."""
    k = (key or "").strip()
    mapping = {
        "ctrl+s": "ctrl+s", "ctrl+c": "ctrl+c", "ctrl+v": "ctrl+v",
        "ctrl+a": "ctrl+a", "ctrl+x": "ctrl+x",
    }
    return mapping.get(k.lower(), k.capitalize() if len(k) == 1 else k)


def _xdotool_key(key: str) -> str:
    """Map manifest key names → xdotool key names."""
    k = (key or "").strip().lower()
    return {
        "enter": "Return", "return": "Return",
        "esc": "Escape", "escape": "Escape",
        "backspace": "BackSpace", "tab": "Tab",
        "space": "space", "delete": "Delete",
        "ctrl+s": "ctrl+s", "ctrl+c": "ctrl+c", "ctrl+v": "ctrl+v",
        "ctrl+a": "ctrl+a", "ctrl+x": "ctrl+x",
        "ctrl+t": "ctrl+t", "ctrl+w": "ctrl+w",
    }.get(k, key)


def _loop_deadline(timeout_ms: int) -> float:
    import time as _t
    return _t.monotonic() + max(0.1, min(10.0, int(timeout_ms) / 1000.0))


def _loop_expired(deadline: float) -> bool:
    import time as _t
    return _t.monotonic() >= deadline
