// Live Linux GUI app — the body is just a <canvas> painted with JPEGs
// streamed from the broker. Local clicks/keys forward to the agent via
// HTTP so the user can drive a real Linux app from inside the Win95 UI.

export class LinuxApp {
  constructor(body, meta) {
    this.body = body;
    this.meta = meta;
    body.innerHTML = `
      <div class="lx-toolbar">
        <span class="lx-display">DISPLAY :--</span>
        <span class="lx-status">connecting…</span>
      </div>
      <div class="lx-canvas-wrap">
        <canvas class="lx-canvas" width="800" height="540"></canvas>
        <div class="lx-overlay">streaming Linux app…</div>
      </div>
    `;
    this.canvas = body.querySelector(".lx-canvas");
    this.ctx = this.canvas.getContext("2d");
    this.overlay = body.querySelector(".lx-overlay");
    this.displayLabel = body.querySelector(".lx-display");
    this.statusLabel = body.querySelector(".lx-status");
    this._lastFrameLen = 0;
    this._pendingImage = null;
    this._bindInput();
  }

  setFocusTarget(_) {}
  resize() {}

  render(state) {
    if (state.display !== undefined) this.displayLabel.textContent = `DISPLAY :${state.display}`;
    if (state.width && state.width !== this.canvas.width)  this.canvas.width  = state.width;
    if (state.height && state.height !== this.canvas.height) this.canvas.height = state.height;
    if (state.frame && state.frame !== this._lastFrame) {
      this._lastFrame = state.frame;
      this.statusLabel.textContent = "live";
      this.overlay.style.display = "none";
      this._paint(state.frame);
    }
    if (state.error) {
      this.statusLabel.textContent = `error: ${state.error}`;
      this.overlay.style.display = "flex";
      this.overlay.textContent = `Linux error: ${state.error}`;
    }
  }

  _paint(b64) {
    const img = new Image();
    img.onload = () => {
      try { this.ctx.drawImage(img, 0, 0, this.canvas.width, this.canvas.height); } catch (e) {}
    };
    img.src = "data:image/jpeg;base64," + b64;
  }

  _bindInput() {
    // Local clicks on the canvas drive xdotool on the matching X display.
    // We deliberately don't take pointer-events on the rest of the OS —
    // the agent's cursor remains the read-only entertainer; only canvas
    // clicks here pass through, so the user can poke at the Linux app
    // even when the agent isn't doing anything.
    this.canvas.style.pointerEvents = "auto";
    const rect = () => this.canvas.getBoundingClientRect();
    this.canvas.addEventListener("click", (e) => {
      const r = rect();
      const x = Math.round((e.clientX - r.left) * this.canvas.width  / r.width);
      const y = Math.round((e.clientY - r.top)  * this.canvas.height / r.height);
      fetch(`/linux/${this.meta.id}/click`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ x, y, button: 1 }),
      }).catch(() => {});
    });
    this.canvas.addEventListener("keydown", (e) => {
      e.preventDefault();
      const key = e.key.length === 1 ? null : e.key;
      if (key) {
        fetch(`/linux/${this.meta.id}/key`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ key }),
        }).catch(() => {});
      } else {
        fetch(`/linux/${this.meta.id}/type`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ text: e.key }),
        }).catch(() => {});
      }
    });
    this.canvas.tabIndex = 0;
  }
}
