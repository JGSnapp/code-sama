"""FastAPI entry point. Mounts /web as static, exposes /chat and /ws."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import re
import secrets
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .agents import Coordinator
from .browser_engine import BrowserEngine
from .loader import AppRegistry, Harness, Runtime, VisionResolver
from .linux_broker import LinuxBroker
from .models import ModelRouter
from .providers import PROVIDER_PRESETS
from .sandbox import Sandbox
from .settings_store import SettingsStore
from .state import OSController
from .tool_registry import ToolRegistry
from .tts_omnivoice import get_tts_engine
from .vroid_adapter import get_vroid_adapter
from . import wiki as wiki_mod


log = logging.getLogger("code-sama-os")

ROOT = pathlib.Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
UPLOAD_DIR = ROOT / "uploads"
LOG_DIR = ROOT / "logs"
LOG_FILE = LOG_DIR / "code-sama-os.log"

_UPLOAD_KINDS = {
    "vrm": {
        "subdir": "vrm",
        "exts": {".vrm"},
        "max_mb": 80,
    },
    "avatar_bg": {
        "subdir": "avatar_bg",
        "exts": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
        "max_mb": 15,
    },
    "desktop_bg": {
        "subdir": "desktop_bg",
        "exts": {".png", ".jpg", ".jpeg", ".webp", ".gif"},
        "max_mb": 15,
    },
    "voice": {
        "subdir": "voice",
        "exts": {".wav", ".mp3", ".ogg", ".m4a", ".webm"},
        "max_mb": 20,
    },
}


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
    settings = SettingsStore(ROOT / "settings.json")
    router = ModelRouter(ROOT / "models.yml")
    router._settings = settings
    # Settings LLM overrides models.yml when the user configured providers
    # in the Settings tab.
    applied = router.apply_settings_llm(settings.to_models_yml_dict())
    if not applied:
        log.info("ModelRouter config: %s", router.summary())
    else:
        log.info("ModelRouter from settings.json: %s", router.summary())

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
    app.state.settings = settings
    app.state.tts = get_tts_engine()
    app.state.vroid = get_vroid_adapter()
    # Prefer local Code-sama.vrm if settings still point at the stock Seed-san.
    try:
        _ensure_code_sama_vrm(app.state.vroid, settings)
    except Exception:
        log.exception("VRoid bootstrap failed")
    # Optional warm-load so the first chat utterance isn't a long stall.
    if os.environ.get("OMNIVOICE_PRELOAD", "").lower() in ("1", "true", "yes"):
        try:
            app.state.tts.load()
        except Exception:
            log.exception("OmniVoice preload failed")

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


def _ensure_code_sama_vrm(vroid, settings: SettingsStore) -> None:
    """Install Desktop/local Code-sama.vrm and point settings at it when needed."""
    local = UPLOAD_DIR / "vrm" / "code-sama.vrm"
    if not local.is_file():
        result = vroid.install_from_desktop("Code-sama.vrm")
        if not result.get("ok"):
            # Also try characters/ if a previous copy exists.
            cand = ROOT / "characters" / "code-sama.vrm"
            if cand.is_file():
                result = vroid.install(cand, name="code-sama.vrm")
        if not result.get("ok"):
            log.info("No Code-sama.vrm to bootstrap yet: %s", result.get("error"))
            return
        local = pathlib.Path(result["path"])
    url = "/uploads/vrm/code-sama.vrm"
    current = ((settings.get().get("avatar") or {}).get("vrmUrl") or "").strip()
    stock = "Seed-san.vrm" in current or not current
    if stock or current != url:
        settings.update({"avatar": {"vrmUrl": url}})
        log.info("Avatar VRM set to %s", url)


app = FastAPI(lifespan=lifespan, title="code-sama-os")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    return HTMLResponse((WEB_DIR / "index.html").read_text(encoding="utf-8"))


@app.get("/config")
async def config() -> JSONResponse:
    """Bootstrap payload for the web UI (avatar + merged settings)."""
    settings: SettingsStore = app.state.settings
    data = settings.public_view()
    cam = data.get("camera") or {}
    avatar = data.get("avatar") or {}
    voice = data.get("voice") or {}
    return JSONResponse(
        {
            "aikeyaUrl": os.environ.get("AIKEYA_URL", ""),
            "model": os.environ.get("MODEL", ""),
            "avatarVrmUrl": avatar.get("vrmUrl")
            or os.environ.get(
                "AVATAR_VRM_URL",
                "https://cdn.jsdelivr.net/gh/vrm-c/vrm-specification@master/samples/Seed-san/vrm/Seed-san.vrm",
            ),
            "ttsLang": voice.get("lang") or os.environ.get("AVATAR_TTS_LANG", "ru-RU"),
            "ttsRate": float(voice.get("rate") or os.environ.get("AVATAR_TTS_RATE", "1.0")),
            "ttsPitch": float(voice.get("pitch") or os.environ.get("AVATAR_TTS_PITCH", "1.05")),
            "ttsVoiceName": voice.get("voiceName") or "",
            "ttsSampleUrl": voice.get("sampleUrl") or "",
            "ttsRefText": voice.get("refText") or "",
            "ttsInstruct": voice.get("instruct") or "",
            "omnivoice": app.state.tts.status() if hasattr(app.state, "tts") else {},
            "vroid": app.state.vroid.status() if hasattr(app.state, "vroid") else {},
            "cameraYOffset": float(cam.get("yOffset", 0)),
            "cameraDistance": float(cam.get("distance", 1.45)),
            "cameraFov": float(cam.get("fov", 24)),
            "avatarBackground": avatar.get("background") or "default",
            "desktopWallpaper": (data.get("desktop") or {}).get("wallpaper") or "default",
            "settings": data,
            "providerPresets": PROVIDER_PRESETS,
            "models": app.state.router.summary(),
        }
    )


@app.get("/settings")
async def settings_get() -> JSONResponse:
    settings: SettingsStore = app.state.settings
    return JSONResponse({
        "settings": settings.public_view(),
        "providerPresets": PROVIDER_PRESETS,
        "models": app.state.router.summary(),
    })


@app.put("/settings")
async def settings_put(payload: dict) -> JSONResponse:
    """Persist Settings-tab changes and hot-reload the model router."""
    settings: SettingsStore = app.state.settings
    patch = payload.get("settings") if isinstance(payload.get("settings"), dict) else payload
    if not isinstance(patch, dict):
        return JSONResponse({"ok": False, "error": "expected object"}, status_code=400)
    data = settings.update(patch)
    router: ModelRouter = app.state.router
    models_dict = settings.to_models_yml_dict()
    if models_dict:
        router.apply_settings_llm(models_dict)
    else:
        router.refresh()
    return JSONResponse({
        "ok": True,
        "settings": settings.public_view(),
        "models": router.summary(),
    })


@app.post("/settings/providers/chatgpt/device/start")
async def chatgpt_device_start() -> JSONResponse:
    from . import chatgpt_subscription as cg
    try:
        data = cg.start_device_flow()
        return JSONResponse({"ok": True, **data})
    except Exception as exc:
        log.exception("ChatGPT device start failed")
        # 200 so the UI can read the geo/proxy hint without treating it as a dead route.
        return JSONResponse({"ok": False, "error": str(exc)})


@app.post("/settings/providers/chatgpt/device/poll")
async def chatgpt_device_poll(payload: dict) -> JSONResponse:
    from . import chatgpt_subscription as cg
    flow_id = (payload.get("flow_id") or "").strip()
    if not flow_id:
        return JSONResponse({"ok": False, "error": "flow_id required"}, status_code=400)
    try:
        result = cg.poll_device_flow(flow_id)
    except Exception as exc:
        log.exception("ChatGPT device poll failed")
        return JSONResponse({"ok": False, "status": "error", "error": str(exc)}, status_code=502)

    if result.get("status") != "authorized":
        return JSONResponse({"ok": True, **result})

    settings: SettingsStore = app.state.settings
    provider = result["provider"]
    name = provider["name"]
    patch = {
        "llm": {
            "providers": {
                name: {
                    "base_url": provider["base_url"],
                    "api_key": provider["access_token"],
                    "access_token": provider["access_token"],
                    "refresh_token": provider["refresh_token"],
                    "preset": "chatgpt-subscription",
                    "auth_mode": "chatgpt",
                    "has_key": True,
                }
            },
            "activeProvider": name,
        }
    }
    models = result.get("models") or []
    if models and not (settings.get().get("llm") or {}).get("activeModel"):
        patch["llm"]["activeModel"] = models[0]
    data = settings.update(patch)
    router: ModelRouter = app.state.router
    router._settings = settings
    models_dict = settings.to_models_yml_dict()
    if models_dict:
        router.apply_settings_llm(models_dict)
    return JSONResponse({
        "ok": True,
        "status": "authorized",
        "models": models,
        "settings": settings.public_view(),
        "provider": name,
    })


@app.get("/settings/providers/presets")
async def settings_provider_presets() -> JSONResponse:
    return JSONResponse({"presets": PROVIDER_PRESETS})


@app.post("/settings/providers/test")
async def settings_provider_test(payload: dict) -> JSONResponse:
    """Probe an OpenAI-compatible ``/models`` endpoint with the given key."""
    import httpx

    settings: SettingsStore = app.state.settings
    base_url = (payload.get("base_url") or "").rstrip("/")
    api_key = payload.get("api_key") or ""
    name = payload.get("name") or ""
    if _is_redacted_key(api_key) and name:
        existing = (settings.get().get("llm") or {}).get("providers") or {}
        api_key = (existing.get(name) or {}).get("api_key") or ""
    if not base_url or base_url in ("copilot",):
        return JSONResponse({"ok": False, "error": "unsupported or empty base_url"}, status_code=400)
    if "chatgpt.com/backend-api/codex" in base_url or base_url == "chatgpt-subscription":
        from . import chatgpt_subscription as cg
        token = api_key
        if _is_redacted_key(api_key) and name:
            existing = (settings.get().get("llm") or {}).get("providers") or {}
            token = (existing.get(name) or {}).get("access_token") or (existing.get(name) or {}).get("api_key") or ""
        if not token:
            return JSONResponse({"ok": False, "error": "not connected — use Подключить"}, status_code=200)
        models = cg.fetch_available_models(token)
        return JSONResponse({
            "ok": True,
            "url": cg.DEFAULT_BASE_URL + "/models",
            "models": models,
            "count": len(models),
        })
    url = base_url + ("/models" if base_url.endswith("/v1") or "/v1" in base_url or base_url.endswith("/openai") else "/v1/models")
    # Gemini-style already ends with /openai — append /models
    if base_url.endswith("/openai"):
        url = base_url + "/models"
    elif not base_url.endswith("/models"):
        if base_url.rstrip("/").endswith("/v1") or "/v1/" in base_url or base_url.endswith("/api"):
            url = base_url.rstrip("/") + "/models"
        else:
            url = base_url.rstrip("/") + "/v1/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get(url, headers=headers)
        if r.status_code >= 400:
            return JSONResponse({
                "ok": False,
                "status": r.status_code,
                "error": (r.text or "")[:300],
                "url": url,
            })
        body = r.json()
        models = []
        if isinstance(body, dict):
            data = body.get("data") or body.get("models") or []
            if isinstance(data, list):
                for m in data[:80]:
                    if isinstance(m, dict) and m.get("id"):
                        models.append(m["id"])
                    elif isinstance(m, str):
                        models.append(m)
        return JSONResponse({"ok": True, "url": url, "models": models, "count": len(models)})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc), "url": url}, status_code=200)


def _is_redacted_key(key: Any) -> bool:
    if not isinstance(key, str) or not key:
        return False
    return "…" in key or "•" in key


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

    async def sender() -> None:
        # Initial snapshot.
        await ws.send_text(json.dumps(controller.snapshot(), ensure_ascii=False, default=str))
        while True:
            event = await q.get()
            try:
                payload = json.dumps(event, ensure_ascii=False, default=str)
            except Exception:
                log.exception("ws serialize failed for %s", event.get("type"))
                continue
            await ws.send_text(payload)

    async def receiver() -> None:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if not isinstance(msg, dict):
                continue
            if msg.get("type") == "screenshotResult":
                controller.fulfill_screenshot(
                    str(msg.get("id") or ""),
                    msg.get("imageB64") or "",
                )

    try:
        await asyncio.gather(sender(), receiver())
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws error")
    finally:
        controller.unsubscribe(q)


@app.post("/settings/upload")
async def settings_upload(
    kind: str = Form(...),
    file: UploadFile = File(...),
) -> JSONResponse:
    """Accept a local file for VRM / backgrounds / voice sample."""
    spec = _UPLOAD_KINDS.get(kind)
    if not spec:
        return JSONResponse(
            {"ok": False, "error": f"unknown kind: {kind}"},
            status_code=400,
        )
    raw_name = file.filename or "upload.bin"
    ext = pathlib.Path(raw_name).suffix.lower()
    if ext not in spec["exts"]:
        return JSONResponse(
            {
                "ok": False,
                "error": f"bad extension {ext}; allowed: {', '.join(sorted(spec['exts']))}",
            },
            status_code=400,
        )
    data = await file.read()
    max_bytes = int(spec["max_mb"]) * 1024 * 1024
    if len(data) > max_bytes:
        return JSONResponse(
            {"ok": False, "error": f"file too large (max {spec['max_mb']} MB)"},
            status_code=400,
        )
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", pathlib.Path(raw_name).stem)[:48] or "file"
    out_dir = UPLOAD_DIR / spec["subdir"]
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = f"{safe_stem}-{secrets.token_hex(4)}{ext}"
    out_path = out_dir / out_name
    out_path.write_bytes(data)

    url = f"/uploads/{spec['subdir']}/{out_name}"
    settings: SettingsStore = app.state.settings
    patch: dict[str, Any] = {}
    if kind == "vrm":
        patch = {"avatar": {"vrmUrl": url}}
    elif kind == "avatar_bg":
        patch = {"avatar": {"background": url}}
    elif kind == "desktop_bg":
        patch = {"desktop": {"wallpaper": url}}
    elif kind == "voice":
        patch = {"voice": {"sampleUrl": url}}
    if patch:
        settings.update(patch)

    return JSONResponse({
        "ok": True,
        "kind": kind,
        "url": url,
        "name": raw_name,
        "size": len(data),
        "settings": settings.public_view(),
    })


@app.get("/wiki")
async def wiki_index() -> JSONResponse:
    return JSONResponse({"pages": wiki_mod.list_pages()})


@app.get("/wiki/{page_id}")
async def wiki_page(page_id: str) -> JSONResponse:
    page = wiki_mod.read_page(page_id)
    if not page:
        return JSONResponse({"ok": False, "error": "not found"}, status_code=404)
    return JSONResponse({"ok": True, "page": page})


@app.get("/vroid/status")
async def vroid_status() -> JSONResponse:
    return JSONResponse(get_vroid_adapter().status())


@app.post("/vroid/launch")
async def vroid_launch() -> JSONResponse:
    return JSONResponse(get_vroid_adapter().launch())


@app.post("/vroid/install")
async def vroid_install(payload: dict | None = None) -> JSONResponse:
    """Install a .vrm from an absolute path, Desktop, or the watch folder."""
    vroid = get_vroid_adapter()
    payload = payload or {}
    source = (payload.get("path") or payload.get("source") or "").strip()
    name = (payload.get("name") or "code-sama.vrm").strip()
    if payload.get("fromDesktop"):
        result = vroid.install_from_desktop(payload.get("prefer") or "Code-sama.vrm")
    elif source:
        result = vroid.install(source, name=name)
    else:
        result = vroid.install_from_desktop()
    if not result.get("ok"):
        return JSONResponse(result, status_code=400)
    # Hot-swap settings so the avatar reloads.
    settings: SettingsStore = app.state.settings
    settings.update({"avatar": {"vrmUrl": result["url"]}})
    result["settings"] = settings.public_view()
    return JSONResponse(result)


@app.post("/vroid/watch/start")
async def vroid_watch_start() -> JSONResponse:
    vroid = get_vroid_adapter()
    settings: SettingsStore = app.state.settings

    def _on_install(result: dict) -> None:
        settings.update({"avatar": {"vrmUrl": result["url"]}})

    return JSONResponse(vroid.start_watch(on_install=_on_install))


@app.post("/vroid/watch/stop")
async def vroid_watch_stop() -> JSONResponse:
    return JSONResponse(get_vroid_adapter().stop_watch())


@app.get("/tts/status")
async def tts_status() -> JSONResponse:
    engine = get_tts_engine()
    return JSONResponse(engine.status())


@app.post("/tts")
async def tts_speak(payload: dict) -> Response:
    """Synthesize speech with OmniVoice (clone from uploaded sample when set)."""
    text = (payload.get("text") or "").strip()
    if not text:
        return JSONResponse({"ok": False, "error": "empty"}, status_code=400)

    settings: SettingsStore = app.state.settings
    voice = (settings.get().get("voice") or {})
    sample_url = (payload.get("sampleUrl") or voice.get("sampleUrl") or "").strip()
    ref_text = (payload.get("refText") or voice.get("refText") or None) or None
    instruct = (payload.get("instruct") or voice.get("instruct") or None) or None
    language = payload.get("language") or voice.get("lang") or "ru-RU"
    speed = float(payload.get("speed") or voice.get("rate") or 1.0)

    ref_path = _resolve_upload_path(sample_url) if sample_url else None
    engine = get_tts_engine()
    try:
        # Run blocking model inference off the event loop.
        wav_bytes, sr = await asyncio.to_thread(
            engine.synthesize,
            text,
            ref_audio=ref_path,
            ref_text=ref_text,
            language=language,
            instruct=instruct,
            speed=speed,
        )
    except Exception as exc:
        log.exception("TTS failed")
        return JSONResponse(
            {"ok": False, "error": str(exc), "status": engine.status()},
            status_code=503,
        )
    return Response(
        content=wav_bytes,
        media_type="audio/wav",
        headers={
            "X-Sample-Rate": str(sr),
            "X-OmniVoice": "1" if ref_path else "0",
            "Cache-Control": "no-store",
        },
    )


def _resolve_upload_path(url_or_path: str) -> pathlib.Path | None:
    """Map ``/uploads/...`` URLs (or absolute paths) to a local file."""
    raw = (url_or_path or "").strip()
    if not raw:
        return None
    if raw.startswith("/uploads/"):
        rel = raw[len("/uploads/"):]
        path = UPLOAD_DIR / rel
    elif raw.startswith("uploads/"):
        path = UPLOAD_DIR / raw[len("uploads/"):]
    else:
        path = pathlib.Path(raw)
    try:
        path = path.resolve()
        # Stay inside uploads/ unless it's an absolute path the admin set.
        if UPLOAD_DIR.resolve() in path.parents or path.parent == UPLOAD_DIR.resolve():
            return path if path.is_file() else None
        if path.is_file() and path.is_absolute():
            return path
    except Exception:
        return None
    return None


# Static files for the OS shell and apps.
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")
app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")
app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/css", StaticFiles(directory=str(WEB_DIR / "css")), name="css")
app.mount("/js", StaticFiles(directory=str(WEB_DIR / "js")), name="js")
