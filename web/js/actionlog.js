// Action journal — tools + agent messages from the websocket stream.

export class ActionLogPanel {
  constructor() {
    this.backdrop = document.getElementById("actionlog-backdrop");
    this.root = document.getElementById("actionlog-window");
    this.list = document.getElementById("actionlog-list");
    this.entries = [];
    this._max = 200;
    this._bind();
  }

  _bind() {
    this.backdrop?.addEventListener("click", (e) => {
      if (e.target === this.backdrop) this.hide();
    });
    this.root?.querySelector("#actionlog-close")?.addEventListener("click", () => this.hide());
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.root?.classList.contains("open")) this.hide();
    });
  }

  show() {
    this.backdrop?.classList.add("open");
    this.root?.classList.add("open");
    this.backdrop?.setAttribute("aria-hidden", "false");
    this._render();
    this.list?.scrollTo?.(0, this.list.scrollHeight);
  }

  hide() {
    this.backdrop?.classList.remove("open");
    this.root?.classList.remove("open");
    this.backdrop?.setAttribute("aria-hidden", "true");
  }

  seed(entries) {
    this.entries = Array.isArray(entries) ? entries.slice(-this._max) : [];
    if (this.root?.classList.contains("open")) this._render();
  }

  push(entry) {
    if (!entry) return;
    this.entries.push(entry);
    if (this.entries.length > this._max) this.entries = this.entries.slice(-this._max);
    if (this.root?.classList.contains("open")) {
      this._appendRow(entry);
      this.list?.scrollTo?.(0, this.list.scrollHeight);
    }
  }

  _render() {
    if (!this.list) return;
    this.list.innerHTML = "";
    if (!this.entries.length) {
      this.list.innerHTML = `<div class="alog-empty">Пока тихо — как только Streamer/Worker что-то сделают, здесь появятся tool-вызовы и реплики.</div>`;
      return;
    }
    for (const e of this.entries) this._appendRow(e);
  }

  _appendRow(e) {
    if (!this.list) return;
    if (this.list.querySelector(".alog-empty")) this.list.innerHTML = "";
    const row = document.createElement("div");
    row.className = `alog-row alog-${e.kind || "misc"}`;
    const t = new Date((e.ts || 0) * 1000);
    const hh = String(t.getHours()).padStart(2, "0");
    const mm = String(t.getMinutes()).padStart(2, "0");
    const ss = String(t.getSeconds()).padStart(2, "0");
    const agent = e.agent || "—";
    const name = e.name || e.kind || "";
    const summary = e.summary || "";
    row.innerHTML = `
      <span class="alog-time">${hh}:${mm}:${ss}</span>
      <span class="alog-agent">${escapeHtml(agent)}</span>
      <span class="alog-name">${escapeHtml(name)}</span>
      <span class="alog-sum">${escapeHtml(summary)}</span>
    `;
    this.list.appendChild(row);
  }
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
