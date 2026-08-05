// In-OS HTML5 game — keyboard + continuous live play inside the iframe
// (no LLM round-trip per key — arcade games must feel real-time).

export class WebGameApp {
  constructor(body, meta) {
    this.body = body;
    this.meta = meta;
    body.innerHTML = `
      <div class="webgame-wrap">
        <iframe class="webgame-frame" sandbox="allow-scripts allow-same-origin"
          title="webgame" tabindex="0"></iframe>
        <div class="webgame-empty">Ждёт HTML-игру… tool: webgame_build</div>
      </div>
    `;
    this.frame = body.querySelector(".webgame-frame");
    this.empty = body.querySelector(".webgame-empty");
    this._html = "";
    this._seq = 0;
    this._holdSeq = 0;
    this._held = null;
    this._liveSeq = 0;
    this._liveTimer = null;
    this._liveUntil = 0;
  }

  setFocusTarget(_target) {
    try { this.frame.focus(); } catch (_) { /* ignore */ }
  }

  render(state) {
    const html = state.html || "";
    const title = state.title || "WebGame";
    if (html !== this._html) {
      this._html = html;
      this._stopLive();
      this._held = null;
      if (html.trim()) {
        // Inject continuous-play bridge so agent bots & hold-keys work.
        const bridge = `<script>(${this._bridgeSource()})()</script>`;
        const injected = html.includes("</body>")
          ? html.replace(/<\/body>/i, bridge + "</body>")
          : html + bridge;
        this.frame.srcdoc = injected;
        this.frame.style.display = "block";
        this.empty.style.display = "none";
      } else {
        this.frame.removeAttribute("srcdoc");
        this.frame.style.display = "none";
        this.empty.style.display = "flex";
      }
    }
    if (state.title) {
      const bar = this.body.closest(".window")?.querySelector(".ttl");
      if (bar) bar.textContent = title;
    }
    // One-shot taps (Space, etc.)
    if (state.keySeq && state.keySeq !== this._seq) {
      this._seq = state.keySeq;
      this._tapKey(state.keyName || "");
    }
    // Held direction — keydown without keyup (arcade style).
    if (state.holdSeq && state.holdSeq !== this._holdSeq) {
      this._holdSeq = state.holdSeq;
      this._holdKey(state.holdKey || "");
    }
    // Continuous local autopilot (no server key spam).
    if (state.livePlay && state.livePlay.seq && state.livePlay.seq !== this._liveSeq) {
      this._liveSeq = state.livePlay.seq;
      this._startLive(state.livePlay);
    }
    if (state.livePlay === null && this._liveTimer) {
      this._stopLive();
    }
  }

  _bridgeSource() {
    // Runs inside the game iframe. Exposes __CSAMA__ for hold/bot/capture.
    return function () {
      const dirs = ["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"];
      let held = null;
      function fire(type, key) {
        const keyName = key === "Space" ? " " : key;
        const opts = {
          key: keyName,
          code: keyName.length === 1 ? "Key" + keyName.toUpperCase() : keyName,
          keyCode: keyName === " " ? 32 : 0,
          which: keyName === " " ? 32 : 0,
          bubbles: true,
          cancelable: true,
        };
        const t = document.activeElement && document.activeElement !== document.body
          ? document.activeElement
          : (document.body || window);
        t.dispatchEvent(new KeyboardEvent(type, opts));
        window.dispatchEvent(new KeyboardEvent(type, opts));
      }
      function hold(key) {
        if (!key) {
          if (held) { fire("keyup", held); held = null; }
          return;
        }
        if (held === key) return;
        if (held) fire("keyup", held);
        held = key;
        fire("keydown", key);
      }
      function tap(key) {
        fire("keydown", key);
        setTimeout(() => fire("keyup", key), 40);
      }
      let botId = null;
      function startBot(style, ms) {
        stopBot();
        let cur = "ArrowLeft";
        botId = setInterval(() => {
          // Prefer game-provided hook if the agent coded one.
          if (typeof window.__CSAMA_TICK__ === "function") {
            try { window.__CSAMA_TICK__(ms / 1000); } catch (e) { /* ignore */ }
            return;
          }
          if (style === "pacman" || style === "arcade") {
            if (Math.random() < 0.22) cur = dirs[(dirs.indexOf(cur) + 1 + (Math.random() < 0.5 ? 2 : 0)) % 4];
            else if (Math.random() < 0.18) cur = dirs[Math.floor(Math.random() * 4)];
            hold(cur);
            if (Math.random() < 0.04) tap("Space");
          } else {
            if (Math.random() < 0.3) cur = dirs[Math.floor(Math.random() * 4)];
            hold(cur);
          }
        }, ms || 120);
      }
      function stopBot() {
        if (botId) { clearInterval(botId); botId = null; }
        hold("");
      }
      function capture() {
        const c = document.querySelector("canvas");
        if (c && c.toDataURL) {
          try { return c.toDataURL("image/jpeg", 0.7).split(",")[1] || ""; } catch (e) { return ""; }
        }
        return "";
      }
      window.__CSAMA__ = { hold, tap, startBot, stopBot, capture };
      window.addEventListener("message", (ev) => {
        const d = ev.data || {};
        if (d.type === "csama-hold") hold(d.key || "");
        if (d.type === "csama-tap") tap(d.key || "");
        if (d.type === "csama-live-start") startBot(d.style || "arcade", d.intervalMs || 120);
        if (d.type === "csama-live-stop") stopBot();
        if (d.type === "csama-capture") {
          const b64 = capture();
          parent.postMessage({ type: "csama-capture-result", id: d.id, imageB64: b64 }, "*");
        }
      });
    }.toString();
  }

  _post(msg) {
    try {
      this.frame.contentWindow?.postMessage(msg, "*");
    } catch (_) { /* ignore */ }
  }

  _holdKey(key) {
    this._held = key || null;
    this._post({ type: "csama-hold", key: key || "" });
  }

  _tapKey(key) {
    if (!key) return;
    this._post({ type: "csama-tap", key });
  }

  _startLive(cfg) {
    this._stopLive();
    const seconds = Number(cfg.seconds || 10);
    const style = cfg.style || "arcade";
    const intervalMs = Math.max(60, Number(cfg.intervalMs || 120));
    this._liveUntil = Date.now() + seconds * 1000;
    this._post({ type: "csama-live-start", style, intervalMs });
    this._liveTimer = setInterval(() => {
      if (Date.now() >= this._liveUntil) this._stopLive();
    }, 400);
  }

  _stopLive() {
    if (this._liveTimer) {
      clearInterval(this._liveTimer);
      this._liveTimer = null;
    }
    this._post({ type: "csama-live-stop" });
  }

  /** Ask iframe for canvas JPEG; falls back to empty. */
  captureFrame() {
    return new Promise((resolve) => {
      const id = "cap_" + Math.random().toString(36).slice(2, 9);
      const onMsg = (ev) => {
        const d = ev.data || {};
        if (d.type === "csama-capture-result" && d.id === id) {
          window.removeEventListener("message", onMsg);
          resolve(d.imageB64 || "");
        }
      };
      window.addEventListener("message", onMsg);
      this._post({ type: "csama-capture", id });
      setTimeout(() => {
        window.removeEventListener("message", onMsg);
        resolve("");
      }, 800);
    });
  }
}
