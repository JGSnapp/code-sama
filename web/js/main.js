// code-sama-os: client bootstrap
import { Cursor } from "/js/cursor.js";
import { Desktop } from "/js/desktop.js";
import { Chat } from "/js/chat.js";
import { Avatar } from "/js/avatar.js";
import { Sounds } from "/js/sounds.js";

const state = {
  windows: [],
  focusedId: null,
  focusedTarget: null,
  chat: [],
  agentStatus: "idle",
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
  start: document.getElementById("start"),
  startMenu: document.getElementById("start-menu"),
  boot: document.getElementById("boot"),
};

const cursor = new Cursor(els.cursor);
const desktop = new Desktop(els.windowsLayer, els.taskbarItems);
const chat = new Chat();
const avatar = new Avatar();
const sounds = new Sounds();

function setAgentStatus(status) {
  state.agentStatus = status;
  els.statusDot.className = "";
  if (status === "thinking" || status === "acting") els.statusDot.classList.add(status);
  els.statusText.textContent = status;
  avatar.setMood(status);
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
      desktop.renderAll(state);
      chat.renderAll(state.chat);
      setAgentStatus(state.agentStatus);
      if (ev.cursor) cursor.jumpTo(ev.cursor.x, ev.cursor.y);
      bootDone();
      break;
    case "cursorLegs":
      cursor.runLegs(ev.legs);
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
    case "chat":
      state.chat.push(ev.message);
      chat.append(ev.message);
      sounds.chime();
      if (ev.message.role === "assistant" && ev.message.content) {
        avatar.speak(ev.message.content);
      }
      break;
    case "agentStatus":
      setAgentStatus(ev.status);
      break;
    case "mood":
      avatar.setExpression(ev.mood);
      break;
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
  ws.onmessage = (m) => {
    try { applyEvent(JSON.parse(m.data)); } catch (e) { console.error(e); }
  };
  ws.onclose = () => {
    setAgentStatus("offline");
    setTimeout(connect, 1500);
  };
}
connect();

// ─── Avatar config ─────────────────────────────────────
fetch("/config").then((r) => r.json()).then((cfg) => {
  avatar.configure(cfg);
});

// ─── Chat input ────────────────────────────────────────
const form = document.getElementById("chat-form");
const input = document.getElementById("chat-input");
form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  try {
    await fetch("/chat", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ text }),
    });
  } catch (err) { console.error(err); }
});

// Hide native pointer on the OS area.
els.os.parentElement.addEventListener("mouseenter", () => {
  els.os.parentElement.style.cursor = "none";
});

// Boot screen safety net: if we never receive a snapshot in 5s, still fade.
setTimeout(bootDone, 5000);
