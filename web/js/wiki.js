// Info wiki — markdown pages + agent diagram tabs.

export class InfoWikiPanel {
  constructor() {
    this.backdrop = document.getElementById("wiki-backdrop");
    this.root = document.getElementById("wiki-window");
    this.tabs = document.getElementById("wiki-tabs");
    this.body = document.getElementById("wiki-body");
    this.pages = [];
    this.pageId = "agents";
    this._bind();
  }

  _bind() {
    this.backdrop?.addEventListener("click", (e) => {
      if (e.target === this.backdrop) this.hide();
    });
    this.root?.querySelector("#wiki-close")?.addEventListener("click", () => this.hide());
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape" && this.root?.classList.contains("open")) this.hide();
    });
  }

  async show(pageId) {
    this.backdrop?.classList.add("open");
    this.root?.classList.add("open");
    this.backdrop?.setAttribute("aria-hidden", "false");
    if (!this.pages.length) {
      try {
        const res = await fetch("/wiki");
        const body = await res.json();
        this.pages = body.pages || [];
      } catch (err) {
        this.pages = [];
        if (this.body) this.body.textContent = String(err);
      }
    }
    if (pageId) this.pageId = pageId;
    else if (!this.pages.find((p) => p.id === this.pageId)) {
      this.pageId = this.pages[0]?.id || "readme";
    }
    this._renderTabs();
    await this._loadPage(this.pageId);
  }

  hide() {
    this.backdrop?.classList.remove("open");
    this.root?.classList.remove("open");
    this.backdrop?.setAttribute("aria-hidden", "true");
  }

  _renderTabs() {
    if (!this.tabs) return;
    this.tabs.innerHTML = this.pages.map((p) =>
      `<button type="button" class="wiki-tab ${p.id === this.pageId ? "active" : ""}" data-page="${p.id}">${escapeHtml(p.title)}</button>`
    ).join("");
    this.tabs.onclick = (e) => {
      const btn = e.target.closest("[data-page]");
      if (!btn) return;
      this.pageId = btn.dataset.page;
      this._renderTabs();
      this._loadPage(this.pageId);
    };
  }

  async _loadPage(id) {
    if (!this.body) return;
    this.body.innerHTML = `<p class="wiki-loading">Загрузка…</p>`;
    try {
      const res = await fetch(`/wiki/${encodeURIComponent(id)}`);
      const body = await res.json();
      if (!body.ok) {
        this.body.textContent = body.error || "not found";
        return;
      }
      const md = body.page.markdown || "";
      this.body.innerHTML = renderMarkdown(md);
      // Extra visual for agents tab
      if (id === "agents") {
        const fig = document.createElement("div");
        fig.className = "wiki-diagram";
        fig.innerHTML = AGENTS_SVG;
        this.body.prepend(fig);
      }
    } catch (err) {
      this.body.textContent = String(err);
    }
  }
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/** Minimal markdown → HTML (headings, lists, code, bold, links, hr). */
function renderMarkdown(src) {
  const lines = String(src || "").replace(/\r\n/g, "\n").split("\n");
  const out = [];
  let inCode = false;
  let codeBuf = [];
  let inList = false;

  const flushList = () => {
    if (inList) { out.push("</ul>"); inList = false; }
  };

  for (const raw of lines) {
    if (raw.startsWith("```")) {
      if (inCode) {
        out.push(`<pre class="wiki-code"><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
        codeBuf = [];
        inCode = false;
      } else {
        flushList();
        inCode = true;
      }
      continue;
    }
    if (inCode) { codeBuf.push(raw); continue; }

    if (/^\s*[-*]\s+/.test(raw)) {
      if (!inList) { out.push("<ul>"); inList = true; }
      out.push(`<li>${inline(raw.replace(/^\s*[-*]\s+/, ""))}</li>`);
      continue;
    }
    flushList();

    if (/^###\s+/.test(raw)) { out.push(`<h3>${inline(raw.slice(4))}</h3>`); continue; }
    if (/^##\s+/.test(raw)) { out.push(`<h2>${inline(raw.slice(3))}</h2>`); continue; }
    if (/^#\s+/.test(raw)) { out.push(`<h1>${inline(raw.slice(2))}</h1>`); continue; }
    if (/^---+$/.test(raw.trim())) { out.push("<hr/>"); continue; }
    if (!raw.trim()) { out.push(""); continue; }
    // tables — crude
    if (raw.includes("|")) {
      out.push(`<pre class="wiki-table">${escapeHtml(raw)}</pre>`);
      continue;
    }
    out.push(`<p>${inline(raw)}</p>`);
  }
  flushList();
  if (inCode) out.push(`<pre class="wiki-code"><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
  return out.join("\n");
}

function inline(s) {
  let t = escapeHtml(s);
  t = t.replace(/`([^`]+)`/g, "<code>$1</code>");
  t = t.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  t = t.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  return t;
}

const AGENTS_SVG = `
<svg viewBox="0 0 640 280" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="Схема агентов">
  <rect width="640" height="280" fill="#120e22" rx="8"/>
  <defs>
    <marker id="arr" markerWidth="8" markerHeight="8" refX="6" refY="3" orient="auto">
      <path d="M0,0 L6,3 L0,6 Z" fill="#9a92d8"/>
    </marker>
  </defs>
  <rect x="30" y="110" width="100" height="50" rx="8" fill="#2a2450" stroke="#7a72b8"/>
  <text x="80" y="140" text-anchor="middle" fill="#f0ecff" font-size="13" font-family="Tahoma">User</text>

  <rect x="180" y="40" width="130" height="60" rx="8" fill="#3a2860" stroke="#c89cff"/>
  <text x="245" y="65" text-anchor="middle" fill="#fff" font-size="13" font-family="Tahoma">Streamer</text>
  <text x="245" y="84" text-anchor="middle" fill="#c9c0ff" font-size="11" font-family="Tahoma">голос / чат</text>

  <rect x="180" y="180" width="130" height="60" rx="8" fill="#1e3a4a" stroke="#6ec8e0"/>
  <text x="245" y="205" text-anchor="middle" fill="#fff" font-size="13" font-family="Tahoma">Worker</text>
  <text x="245" y="224" text-anchor="middle" fill="#b8e8f5" font-size="11" font-family="Tahoma">мышь / apps</text>

  <rect x="380" y="100" width="100" height="60" rx="8" fill="#2a3048" stroke="#8890b0"/>
  <text x="430" y="135" text-anchor="middle" fill="#f0ecff" font-size="13" font-family="Tahoma">EventBus</text>

  <rect x="520" y="100" width="100" height="60" rx="8" fill="#0a5a5a" stroke="#40c0c0"/>
  <text x="570" y="135" text-anchor="middle" fill="#e8ffff" font-size="12" font-family="Tahoma">Win95 OS</text>

  <line x1="130" y1="135" x2="180" y2="70" stroke="#9a92d8" stroke-width="1.5" marker-end="url(#arr)"/>
  <line x1="310" y1="70" x2="380" y2="120" stroke="#9a92d8" stroke-width="1.5" marker-end="url(#arr)"/>
  <line x1="380" y1="145" x2="310" y2="200" stroke="#6ec8e0" stroke-width="1.5" marker-end="url(#arr)"/>
  <line x1="310" y1="210" x2="380" y2="145" stroke="#6ec8e0" stroke-width="1.5" marker-end="url(#arr)" stroke-dasharray="4 3"/>
  <line x1="480" y1="130" x2="520" y2="130" stroke="#40c0c0" stroke-width="1.5" marker-end="url(#arr)"/>
  <text x="245" y="155" text-anchor="middle" fill="#8a84aa" font-size="10" font-family="Tahoma">start_coding / done</text>
</svg>`;
