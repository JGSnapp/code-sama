"""FastAPI entry point. Mounts /web as static, exposes /chat and /ws."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .agents import Coordinator
from .browser_engine import BrowserEngine
from .loader import AppRegistry, Harness, Runtime, VisionResolver
from .linux_broker import LinuxBroker
from .models import ModelRouter
from .sandbox import Sandbox
from .state import OSController
from .tool_registry import ToolRegistry


log = logging.getLogger("code-sama-os")

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "code-sama-os.log"


def _configure_logging() -> None:
    """Console + rotating-ish file logger that everyone in the project shares."""
    LOG_DIR.mkdir(exist_ok=True)
    root = logging.getLogger()
    # Avoid re-adding handlers across uvicorn --reload bounces.
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)
    root.setLevel(logging.INFO)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


_configure_logging()


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv(ROOT / ".env")
    controller = OSController()
    browser = BrowserEngine(
        width=int(os.environ.get("BROWSER_WIDTH", "900")),
        height=int(os.environ.get("BROWSER_HEIGHT", "560")),
        headless=os.environ.get("BROWSER_HEADLESS", "true").lower() not in ("0", "false", "no"),
    )
    await browser.start()
    if not browser.available:
        log.warning("Playwright is not available — browser app will show a placeholder.")

    # ─── Model router (providers x roles) ──────────────────────────────
    router = ModelRouter(ROOT / "models.yml")
    log.info("ModelRouter config: %s", router.summary())

    # ─── App loader subsystem ─────────────────────────────────────────
    # The loader turns any Linux program into a set of agent tools. It
    # owns three pieces: the registry (manifest catalogue on disk), the
    # runtime (executes actions visibly), and the harness (generates new
    # manifests via the LLM, CLI-Anything-style).
    broker = LinuxBroker(controller=controller)
    if not broker.available:
        log.warning("Linux broker unavailable — loader apps will no-op. "
                    "Run inside the Docker image for full functionality.")
    registry = AppRegistry()
    registry.scan()

    # Vision resolver adds a smart fallback when AT-SPI can't find the
    # element — only fires if a ``vision`` role is configured.
    vision = VisionResolver(router, frame_source=_BrokerFrameSource(broker))
    runtime = Runtime(controller, broker, resolver=vision)
    harness = Harness(registry, router=router)

    sandbox = Sandbox()

    # Coordinator is created first so we can use its narration callback
    # as the loader's progress sink; tools then get the wired-up loader.
    coord = Coordinator(controller, tools=[], router=router)
    runtime.set_progress_callback(coord.on_progress)
    loader_bundle = {
        "registry": registry,
        "runtime": runtime,
        "harness": harness,
        "on_progress": coord.on_progress,
    }

    # ─── Tool registry: built-ins + one tool per app action ──────────
    # ToolRegistry rebuilds whenever apps are installed/uninstalled so
    # the agent always sees the current surface.
    tool_reg = ToolRegistry(
        controller=controller,
        browser=browser,
        sandbox=sandbox,
        registry=registry,
        runtime=runtime,
        loader_bundle=loader_bundle,
    )
    tool_reg.rebuild()
    coord.worker.tools = tool_reg.tools() + coord.worker._extra_tools(coord.bus, controller)

    # Rebuild tools automatically when apps are installed/uninstalled.
    # We wrap the registry's install/uninstall so any caller (HTTP,
    # agent tool, future CLI) triggers the refresh.
    _patch_registry_for_autorebuild(registry, tool_reg, coord)

    await coord.start()

    app.state.controller = controller
    app.state.browser = browser
    app.state.coord = coord
    app.state.broker = broker
    app.state.registry = registry
    app.state.runtime = runtime
    app.state.harness = harness
    app.state.router = router
    app.state.tool_registry = tool_reg

    try:
        yield
    finally:
        await coord.stop()
        await runtime.close_all()
        await broker.shutdown()
        await browser.stop()


class _BrokerFrameSource:
    """Frame source for the vision resolver — grabs a base64 JPEG of a
    given X display from the running broker."""

    def __init__(self, broker: LinuxBroker) -> None:
        self.broker = broker

    async def grab(self, display: int | None) -> str | None:
        # Pick any open window on the requested display, fall back to
        # the most recent one. The broker's frame loop already updates
        # last_frame_b64, so this is essentially free.
        for win in self.broker.windows.values():
            if display is None or win.display == display:
                if win.last_frame_b64:
                    return win.last_frame_b64
        return None


def _patch_registry_for_autorebuild(
    registry: AppRegistry, tool_reg: ToolRegistry, coord: Coordinator
) -> None:
    """Wrap install/uninstall so the agent's tool list auto-refreshes.

    After every successful manifest write or removal, we rebuild the
    tool registry and swap the worker's tool list in place. The next
    worker graph invocation picks up the new tools automatically.
    """
    orig_install = registry.install
    orig_uninstall = registry.uninstall

    async def install_wrapped(*args, **kwargs):
        result = await orig_install(*args, **kwargs)
        _apply_rebuild(tool_reg, coord)
        return result

    async def uninstall_wrapped(*args, **kwargs):
        result = await orig_uninstall(*args, **kwargs)
        _apply_rebuild(tool_reg, coord)
        return result

    registry.install = install_wrapped  # type: ignore[assignment]
    registry.uninstall = uninstall_wrapped  # type: ignore[assignment]


def _apply_rebuild(tool_reg: ToolRegistry, coord: Coordinator) -> None:
    try:
        tool_reg.rebuild()
        coord.worker.tools = tool_reg.tools() + coord.worker._extra_tools(coord.bus, coord.controller)
        # Drop cached worker graphs so the new tool list takes effect.
        coord.worker._graphs.clear()
        log.info("tool registry rebuilt after app change; worker tools=%d", len(coord.worker.tools))
    except Exception:
        log.exception("tool registry rebuild failed")


app = FastAPI(lifespan=lifespan, title="code-sama-os")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/config")
async def config() -> JSONResponse:
    return JSONResponse(
        {
            "aikeyaUrl": os.environ.get("AIKEYA_URL", ""),
            "model": os.environ.get("MODEL", ""),
            "avatarVrmUrl": os.environ.get(
                "AVATAR_VRM_URL",
                "https://cdn.jsdelivr.net/gh/vrm-c/vrm-specification@master/samples/Seed-san/vrm/Seed-san.vrm",
            ),
            "ttsLang": os.environ.get("AVATAR_TTS_LANG", "ru-RU"),
            "ttsRate": float(os.environ.get("AVATAR_TTS_RATE", "1.0")),
            "ttsPitch": float(os.environ.get("AVATAR_TTS_PITCH", "1.05")),
        }
    )


@app.post("/chat")
async def chat(payload: dict) -> JSONResponse:
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
    controller: OSController = app.state.controller
    coord: Coordinator = app.state.coord
    log.info("USER chat: %s", text)
    await controller.push_chat("user", text)
    await coord.on_user_chat(text)
    return JSONResponse({"ok": True})


@app.get("/logs")
async def logs(n: int = 200) -> JSONResponse:
    """Return the last `n` lines of the project log file."""
    if not LOG_FILE.exists():
        return JSONResponse({"lines": []})
    n = max(1, min(int(n), 5000))
    with LOG_FILE.open("r", encoding="utf-8", errors="replace") as f:
        tail = f.readlines()[-n:]
    return JSONResponse({"file": str(LOG_FILE), "lines": tail})


# ─── App Loader HTTP API ─────────────────────────────────────────────────
# These mirror the agent tools — handy for driving loader apps from the
# Win95 frontend's local click-passthrough or from external scripts.

@app.get("/loader/list")
async def loader_list() -> JSONResponse:
    registry: AppRegistry = app.state.registry
    return JSONResponse({"apps": registry.summary()})


@app.get("/loader/apps/{app}")
async def loader_describe(app: str) -> JSONResponse:
    registry: AppRegistry = app.state.registry
    data = registry.describe(app)
    if not data:
        return JSONResponse({"ok": False, "error": "not_installed"}, status_code=404)
    return JSONResponse({"ok": True, "app": data})


@app.post("/loader/install")
async def loader_install(payload: dict) -> JSONResponse:
    description = (payload.get("description") or "").strip()
    if not description:
        return JSONResponse({"ok": False, "error": "empty_description"}, status_code=400)
    app_slug = payload.get("app")
    harness: Harness = app.state.harness
    result = await harness.install(description, app_slug=app_slug)
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@app.post("/loader/apps/{app}/open")
async def loader_open(app: str, payload: dict | None = None) -> JSONResponse:
    registry: AppRegistry = app.state.registry
    runtime: Runtime = app.state.runtime
    manifest = registry.get(app)
    if not manifest:
        return JSONResponse({"ok": False, "error": "not_installed"}, status_code=404)
    payload = payload or {}
    result = await runtime.open_app(
        manifest,
        width=payload.get("width"),
        height=payload.get("height"),
    )
    return JSONResponse(result)


@app.post("/loader/apps/{app}/actions/{action}")
async def loader_run_action(app: str, action: str, payload: dict | None = None) -> JSONResponse:
    runtime: Runtime = app.state.runtime
    args = (payload or {}).get("args") or {}
    result = await runtime.run_action(app, action, args)
    status = 200 if result.get("ok") else 400
    return JSONResponse(result, status_code=status)


@app.delete("/loader/apps/{app}")
async def loader_uninstall(app: str) -> JSONResponse:
    registry: AppRegistry = app.state.registry
    runtime: Runtime = app.state.runtime
    await runtime.close_app(app)
    removed = await registry.uninstall(app)
    return JSONResponse({"ok": removed, "app": app})


@app.get("/loader/status")
async def loader_status() -> JSONResponse:
    broker: LinuxBroker = app.state.broker
    runtime: Runtime = app.state.runtime
    return JSONResponse({
        "linux_available": broker.available,
        "capture_backend": broker.capture_backend,
        "shared_display": broker.shared_display,
        "open_apps": runtime.open_apps(),
        "installed": len(app.state.registry.list()),
    })


@app.get("/models")
async def models_summary() -> JSONResponse:
    """Show the role → provider → model mapping. Keys are masked."""
    router: ModelRouter = app.state.router
    summary = router.summary()
    # Don't leak keys — mask them in the API response.
    return JSONResponse(summary)


@app.post("/models/refresh")
async def models_refresh() -> JSONResponse:
    """Reload ``models.yml`` and drop cached LLM clients. Use after
    editing the config without restarting the server."""
    router: ModelRouter = app.state.router
    router.refresh()
    return JSONResponse({"ok": True, "config": router.summary()})


@app.get("/tools")
async def tools_list() -> JSONResponse:
    """Return the agent's current tool surface (names + descriptions).

    Includes one tool per action of every installed app, so a client
    can render a catalogue of what code-sama can currently do.
    """
    tool_reg: ToolRegistry = app.state.tool_registry
    return JSONResponse({
        "tools": [
            {"name": t.name, "description": (t.description or "")[:400]}
            for t in tool_reg.tools()
        ],
        "apps": tool_reg.summary(),
    })


@app.websocket("/ws")
async def ws(ws: WebSocket) -> None:
    await ws.accept()
    controller: OSController = ws.app.state.controller
    q = controller.subscribe()
    try:
        # Initial snapshot.
        await ws.send_text(json.dumps(controller.snapshot()))
        while True:
            event = await q.get()
            await ws.send_text(json.dumps(event))
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws error")
    finally:
        controller.unsubscribe(q)


# Static files for the OS shell and apps.
app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")
app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")
app.mount("/css", StaticFiles(directory=str(WEB_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(WEB_DIR / "js")), name="js")
