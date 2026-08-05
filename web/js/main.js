// code-sama-os: client bootstrap
import { Cursor } from "/js/cursor.js?v=chrome8";
import { Desktop } from "/js/desktop.js?v=chrome8";
import { Chat } from "/js/chat.js?v=chrome8";
import { Avatar } from "/js/avatar.js?v=chrome8";
import { Sounds } from "/js/sounds.js?v=chrome8";
import { SettingsPanel } from "/js/settings.js?v=chrome8";
import { ActionLogPanel } from "/js/actionlog.js?v=chrome8";
import { InfoWikiPanel } from "/js/wiki.js?v=chrome8";

const state = {
  windows: [],
  focusedId: null,
  focusedTarget: null,
  chat: [],
  agentStatus: "idle",
  desktopIcons: [],
};

const els = {
  os: document.getElementById("os"),
  windowsLayer: document.getElementById("windows"),
  cursor: document.getElementById("cursor"),
  clickFx: document.getElementById("click-fx"),
  taskbarItems: document.getElementById("taskbar-items"),
  clock: document.getElementById("clock"),
  statusDot: document.getElementById("status-dot"),
  statusText: document.getElementById("status-text"),
  lastActionText: document.getElementById("last-action-text"),
  start: document.getElementById("start"),
  startMenu: document.getElementById("start-menu"),
  boot: document.getElementById("boot"),
  deskIcons: document.getElementById("desk-icons"),
};

const cursor = new Cursor(els.cursor);
const desktop = new Desktop(els.windowsLayer, els.taskbarItems, els.deskIcons);
const chat = new Chat();
const avatar = new Avatar();
const sounds = new Sounds();
const settings = new SettingsPanel({ avatar });
const actionLog = new ActionLogPanel();
const wiki = new InfoWikiPanel();

function setAgentStatus(status) {
  state.agentStatus = status;
  els.statusDot.className = "";
  if (status === "thinking" || status === "acting") els.statusDot.classList.add(status);
  els.statusText.textContent = status;
  avatar.setMood(status);
}

function setLastAction(action) {
  if (!els.lastActionText || !action) return;
  const who = action.agent ? `${action.agent}: ` : "";
  const text = action.summary || action.name || action.kind || "—";
  els.lastActionText.textContent = (who + text).slice(0, 120);
  els.lastActionText.title = text;
}

function tickClock() {
  const d = new Date();
  const hh = String(d.getHours()).padStart(2, "0");
  const mm = String(d.getMinutes()).padStart(2, "0");
  els.clock.textContent = `${hh}:${mm}`;
}
tickClock(); setInterval(tickClock, 15000);

function flashClick(x, y) {
  const r = document.createElement("div");
  r.className = "ripple";
  r.style.left = x + "px";
  r.style.top = y + "px";
  els.clickFx.appendChild(r);
  setTimeout(() => r.remove(), 520);
  sounds.click();
  // If click landed on Start button → toggle start menu
  const startRect = startBoundsInOsSpace();
  if (startRect && x >= startRect.x && x <= startRect.x + startRect.w && y >= startRect.y && y <= startRect.y + startRect.h) {
    toggleStartMenu();
  } else {
    closeStartMenu();
  }
}

function startBoundsInOsSpace() {
  // Start button is at the bottom-left of the taskbar; the OS canvas is 1024x720.
  return { x: 4, y: 720 - 32, w: 70, h: 28 };
}

function toggleStartMenu() {
  const open = els.startMenu.classList.toggle("open");
  els.start.classList.toggle("open", open);
}
function closeStartMenu() {
  els.startMenu.classList.remove("open");
  els.start.classList.remove("open");
}

function applyEvent(ev) {
  switch (ev.type) {
    case "snapshot":
      state.windows = ev.windows || [];
      state.focusedId = ev.focusedId;
      state.focusedTarget = ev.focusedTarget;
      state.chat = ev.chat || [];
      state.agentStatus = ev.agentStatus || "idle";
      state.desktopIcons = ev.desktopIcons || [];
      desktop.renderAll(state);
      chat.renderAll(state.chat);
      setAgentStatus(state.agentStatus);
      actionLog.seed(ev.actionLog || []);
      if (ev.lastAction) setLastAction(ev.lastAction);
      if (ev.cursor) cursor.jumpTo(ev.cursor.x, ev.cursor.y);
      bootDone();
      break;
    case "cursorLegs":
      cursor.runLegs(ev.legs);
      break;
    case "cursorJump":
      cursor.jumpTo(ev.x, ev.y);
      break;
    case "click":
      flashClick(ev.x, ev.y);
      break;
    case "keypress":
      avatar.pulse();
      sounds.key();
      break;
    case "key":
      avatar.pulse();
      sounds.key();
      break;
    case "windowOpen":
      state.windows.push(ev.window);
      desktop.openWindow(ev.window, state);
      sounds.openWindow();
      break;
    case "windowClose":
      state.windows = state.windows.filter((w) => w.id !== ev.id);
      if (state.focusedId === ev.id) state.focusedId = null;
      desktop.closeWindow(ev.id);
      sounds.close();
      break;
    case "windowMove":
      desktop.moveWindow(ev.id, ev.x, ev.y);
      break;
    case "windowResize":
      desktop.resizeWindow(ev.id, ev.w, ev.h);
      break;
    case "windowMinimize":
      desktop.minimizeWindow(ev.id);
      break;
    case "windowRestore":
      desktop.restoreWindow(ev.id);
      break;
    case "windowMaximize":
      desktop.maximizeWindow(ev.id, ev.maximized);
      break;
    case "focus":
      state.focusedId = ev.id;
      state.focusedTarget = ev.target;
      desktop.focusWindow(ev.id, ev.target, ev.z, state);
      break;
    case "appState":
      desktop.patchAppState(ev.id, ev.state, state);
      break;
    case "desktopIcons":
      state.desktopIcons = ev.icons || [];
      desktop.renderDesktopIcons(state.desktopIcons);
      break;
    case "chat": {
      const m = ev.message;
      // Skip echo of a message we already painted optimistically.
      if (m?.role === "user" && state.chat.length) {
        const last = state.chat[state.chat.length - 1];
        if (last.local && last.role === "user" && last.content === m.content) {
          last.local = false;
          last.ts = m.ts || last.ts;
          break;
        }
      }
      state.chat.push(m);
      chat.append(m);
      sounds.chime();
      if (m?.role === "assistant" && m.content) {
        avatar.speak(m.content);
      }
      break;
    }
    case "agentStatus":
      setAgentStatus(ev.status);
      break;
    case "actionLog":
      actionLog.push(ev.entry);
      break;
    case "lastAction":
      setLastAction(ev.action);
      break;
    case "mood":
      avatar.setExpression(ev.mood);
      break;
    case "screenshotRequest":
      handleScreenshotRequest(ev);
      break;
  }
}

let activeWs = null;

async function handleScreenshotRequest(ev) {
  const id = ev.id;
  const target = ev.target || "os";
  let imageB64 = "";
  try {
    if (target === "webgame") {
      const w = [...desktop.windows.values()].find((x) => x.meta?.app === "webgame");
      if (w?.instance?.captureFrame) {
        imageB64 = await w.instance.captureFrame();
      }
    }
    if (!imageB64 && typeof window.html2canvas === "function") {
      const node = els.os || document.getElementById("os");
      const canvas = await window.html2canvas(node, {
        backgroundColor: null,
        scale: 0.55,
        logging: false,
        useCORS: true,
      });
      imageB64 = (canvas.toDataURL("image/jpeg", 0.72).split(",")[1]) || "";
    }
  } catch (err) {
    console.warn("screenshot failed", err);
  }
  if (activeWs && activeWs.readyState === 1) {
    activeWs.send(JSON.stringify({ type: "screenshotResult", id, imageB64 }));
  }
}

function bootDone() {
  if (els.boot && !els.boot.classList.contains("gone")) {
    setTimeout(() => els.boot.classList.add("gone"), 900);
    setTimeout(() => els.boot.remove(), 2000);
  }
}

// ─── WebSocket ─────────────────────────────────────────
function connect() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  activeWs = ws;
  ws.onmessage = (m) => {
    try { applyEvent(JSON.parse(m.data)); } catch (e) { console.error(e); }
  };
  ws.onclose = () => {
    if (activeWs === ws) activeWs = null;
    setAgentStatus("offline");
    setTimeout(connect, 1500);
  };
}
connect();

// ─── Avatar + Settings ─────────────────────────────────
const settingsReady = fetch("/config").then((r) => r.json()).then(async (cfg) => {
  avatar.configure(cfg);
  await settings.init();
});

async function openSettings() {
  try {
    await settingsReady;
    await settings.show();
  } catch (err) {
    console.error("[settings] open failed", err);
    // Last resort: still try to open with whatever state we have.
    await settings.show();
  }
}

document.getElementById("btn-settings-chrome")?.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  openSettings();
});
document.getElementById("btn-actionlog-chrome")?.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  actionLog.show();
});
document.getElementById("btn-wiki-chrome")?.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  wiki.show("agents");
});
document.getElementById("btn-settings")?.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  openSettings();
});

// Start menu → Settings (agent-driven click path)
document.querySelectorAll("#start-menu .sm-item").forEach((item) => {
  if ((item.textContent || "").includes("Settings")) {
    item.style.cursor = "pointer";
    item.addEventListener("click", () => {
      closeStartMenu();
      openSettings();
    });
  }
});

// Hide native pointer only over the OS canvas, not the page chrome.
els.os.addEventListener("mouseenter", () => {
  els.os.style.cursor = "none";
});
els.os.addEventListener("mouseleave", () => {
  els.os.style.cursor = "";
});
document.documentElement.style.cursor = "auto";

// ─── Chat input ────────────────────────────────────────
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  // Paint immediately — otherwise a dead WS looks like "message vanished".
  const localMsg = { role: "user", content: text, ts: Date.now() / 1000, local: true };
  state.chat.push(localMsg);
  chat.append(localMsg);
  try {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 25000);
    const res = await fetch("/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
      signal: ctrl.signal,
    });
    clearTimeout(timer);
    if (!res.ok) {
      const body = await res.text().catch(() => "");
      throw new Error(`${res.status} ${body || res.statusText}`);
    }
  } catch (err) {
    console.error(err);
    const tip = /abort|timeout/i.test(String(err))
      ? "сервер не отвечает — открой http://127.0.0.1:8765 и обнови страницу"
      : String(err.message || err);
    chat.append({ role: "assistant", content: `(не отправилось: ${tip})` });
    input.value = text;
  }
});

// Boot screen safety net: if we never receive a snapshot in 5s, still fade.
setTimeout(bootDone, 5000);
