// Window manager. Pure presentation — every change comes from server events.

import { BrowserApp } from "/js/apps/browser.js";
import { EditorApp } from "/js/apps/editor.js";
import { PaintApp } from "/js/apps/paint.js";
import { MusicApp } from "/js/apps/music.js";
import { TrackerApp } from "/js/apps/tracker.js";
import { ComputerApp } from "/js/apps/computer.js";
import { WebGameApp } from "/js/apps/webgame.js";
import { LinuxApp } from "/js/apps/linux.js";

const APPS = {
  browser: BrowserApp,
  editor: EditorApp,
  paint: PaintApp,
  music: MusicApp,
  tracker: TrackerApp,
  computer: ComputerApp,
  webgame: WebGameApp,
  linux: LinuxApp,
};

const TITLE_ICONS = {
  browser: "ico-browser",
  editor: "ico-editor",
  paint: "ico-paint",
  music: "ico-music",
  tracker: "ico-tracker",
  computer: "ico-mycomputer",
  webgame: "ico-game",
  linux: "ico-linux",
};

const DESK_ICON_CLASS = {
  mycomputer: "ico-mycomputer",
  recycle: "ico-recycle",
  browser: "ico-browser",
  editor: "ico-editor",
  paint: "ico-paint",
  music: "ico-music",
  tracker: "ico-tracker",
  game: "ico-game",
  computer: "ico-mycomputer",
  linux: "ico-linux",
};

export class Desktop {
  constructor(windowsLayer, taskbarItems, deskIconsEl) {
    this.layer = windowsLayer;
    this.taskbar = taskbarItems;
    this.deskIconsEl = deskIconsEl;
    this.windows = new Map();
    this._selectedIconId = null;
  }

  renderAll(state) {
    this.layer.innerHTML = "";
    this.taskbar.innerHTML = "";
    this.windows.clear();
    for (const w of state.windows) this.openWindow(w, state);
    this.renderDesktopIcons(state.desktopIcons || []);
  }

  renderDesktopIcons(icons) {
    if (!this.deskIconsEl) return;
    this.deskIconsEl.innerHTML = "";
    for (const ico of icons) {
      const el = document.createElement("div");
      el.className = "desk-icon" + (this._selectedIconId === ico.id ? " selected" : "");
      el.dataset.id = ico.id || "";
      const glyph = document.createElement("div");
      const cls = DESK_ICON_CLASS[ico.icon] || DESK_ICON_CLASS[ico.app] || "ico-browser";
      glyph.className = "ico " + cls;
      const span = document.createElement("span");
      span.textContent = ico.label || ico.id || "Icon";
      el.appendChild(glyph);
      el.appendChild(span);
      this.deskIconsEl.appendChild(el);
    }
  }

  openWindow(meta, state) {
    if (this.windows.has(meta.id)) {
      this.windows.get(meta.id).meta = meta;
      return;
    }
    const Cls = APPS[meta.app];
    if (!Cls) return;
    const root = document.createElement("div");
    root.className = `window app-${meta.app} ${state.focusedId === meta.id ? "active" : "inactive"}`;
    root.style.left = meta.x + "px";
    root.style.top = meta.y + "px";
    root.style.width = meta.w + "px";
    root.style.height = meta.h + "px";
    root.style.zIndex = String(meta.z + 100);
    root.dataset.id = meta.id;
    const iconCls = TITLE_ICONS[meta.app] || "";
    root.innerHTML = `
      <div class="titlebar">
        <div class="ttl-ico ${iconCls}"></div>
        <div class="ttl"></div>
        <div class="btn" title="min">_</div>
        <div class="btn" title="max">▢</div>
        <div class="btn" title="close">✕</div>
      </div>
      <div class="body"></div>
    `;
    root.querySelector(".ttl").textContent = meta.title;
    this.layer.appendChild(root);
    const body = root.querySelector(".body");
    const inst = new Cls(body, meta);
    inst.render(meta.appState || {}, state);
    this.windows.set(meta.id, { meta, root, instance: inst });
    this._renderTaskbar(state);
    this._updateFocusStyles(state);
  }

  closeWindow(id) {
    const w = this.windows.get(id);
    if (!w) return;
    w.root.style.transition = "transform 200ms ease, opacity 200ms ease";
    w.root.style.transform = "scale(0.55)";
    w.root.style.opacity = "0";
    setTimeout(() => w.root.remove(), 200);
    this.windows.delete(id);
    this._renderTaskbar({ windows: [...this.windows.values()].map((x) => x.meta), focusedId: null });
  }

  moveWindow(id, x, y) {
    const w = this.windows.get(id);
    if (!w) return;
    w.meta.x = x; w.meta.y = y;
    w.root.style.left = x + "px";
    w.root.style.top = y + "px";
  }

  resizeWindow(id, ww, hh) {
    const w = this.windows.get(id);
    if (!w) return;
    w.meta.w = ww; w.meta.h = hh;
    w.root.style.width = ww + "px";
    w.root.style.height = hh + "px";
    if (w.instance.resize) w.instance.resize();
  }

  restoreWindow(id) {
    const w = this.windows.get(id);
    if (!w) return;
    w.root.classList.remove("minimized");
  }

  minimizeWindow(id) {
    const w = this.windows.get(id);
    if (!w) return;
    w.root.classList.add("minimized");
  }

  maximizeWindow(id, maximized) {
    const w = this.windows.get(id);
    if (!w) return;
    if (maximized) {
      w._prev = { x: w.meta.x, y: w.meta.y, w: w.meta.w, h: w.meta.h };
      w.root.style.left = "0px"; w.root.style.top = "0px";
      w.root.style.width = "1024px"; w.root.style.height = (720 - 34) + "px";
    } else if (w._prev) {
      const p = w._prev;
      w.root.style.left = p.x + "px"; w.root.style.top = p.y + "px";
      w.root.style.width = p.w + "px"; w.root.style.height = p.h + "px";
    }
    if (w.instance.resize) w.instance.resize();
  }

  focusWindow(id, target, z, state) {
    const w = this.windows.get(id);
    if (w) {
      w.root.classList.remove("minimized");
      if (z) w.root.style.zIndex = String(z + 100);
    }
    if (state) {
      state.focusedId = id;
      state.focusedTarget = target;
    }
    this._updateFocusStyles({ windows: [...this.windows.values()].map((x) => x.meta), focusedId: id, focusedTarget: target });
    this._renderTaskbar({ windows: [...this.windows.values()].map((x) => x.meta), focusedId: id });
  }

  patchAppState(id, partial, state) {
    const w = this.windows.get(id);
    if (!w) return;
    w.meta.appState = { ...(w.meta.appState || {}), ...partial };
    w.instance.render(w.meta.appState, state);
  }

  _updateFocusStyles(state) {
    for (const [id, w] of this.windows) {
      const active = id === state.focusedId;
      w.root.classList.toggle("active", active);
      w.root.classList.toggle("inactive", !active);
      if (w.instance.setFocusTarget) w.instance.setFocusTarget(active ? state.focusedTarget : null);
    }
  }

  _renderTaskbar(state) {
    this.taskbar.innerHTML = "";
    for (const w of [...this.windows.values()].map((x) => x.meta)) {
      const it = document.createElement("div");
      it.className = "tb-item" + (state.focusedId === w.id ? " active" : "");
      const ico = document.createElement("div");
      ico.className = "tbi-ico " + (TITLE_ICONS[w.app] || "");
      const lbl = document.createElement("span");
      lbl.textContent = w.title;
      it.appendChild(ico); it.appendChild(lbl);
      this.taskbar.appendChild(it);
    }
  }
}
