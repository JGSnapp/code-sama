export class PaintApp {
  constructor(body, meta) {
    this.body = body;
    this.meta = meta;
    body.innerHTML = `
      <div class="toolbar">
        ${["#000000","#ffffff","#ff3a3a","#ffb43a","#fff14a","#3aff5b","#3aaaff","#a25aff"]
          .map((c) => `<div class="swatch" data-c="${c}" style="background:${c}"></div>`).join("")}
      </div>
      <div class="canvas-wrap"><canvas></canvas></div>
    `;
    this.canvas = body.querySelector("canvas");
    this.ctx = this.canvas.getContext("2d");
    this._sized = false;
    this._lastStrokes = 0;
  }

  setFocusTarget(_) {}
  resize() { this._sized = false; this._redraw(this._lastState || {}); }

  render(state) {
    this._lastState = state;
    this._redraw(state);
  }

  _redraw(state) {
    const rect = this.canvas.parentElement.getBoundingClientRect();
    if (!this._sized || this.canvas.width !== Math.floor(rect.width) || this.canvas.height !== Math.floor(rect.height)) {
      this.canvas.width = Math.max(1, Math.floor(rect.width));
      this.canvas.height = Math.max(1, Math.floor(rect.height));
      this._sized = true;
    }
    const c = this.ctx;
    c.fillStyle = "#fff";
    c.fillRect(0, 0, this.canvas.width, this.canvas.height);
    // Convert OS-canvas absolute points to local canvas pixels.
    // We need the paint canvas absolute rect on OS canvas. We approximate by
    // mapping using the canvas-wrap's bounding rect translated against the OS
    // canvas position. Since both are scaled together, we compute via local
    // canvas dimensions.
    const localRect = this.canvas.parentElement.getBoundingClientRect();
    const osRect = document.getElementById("os").getBoundingClientRect();
    const scale = osRect.width / 1024;
    const offX = (localRect.left - osRect.left) / scale;
    const offY = (localRect.top - osRect.top) / scale;
    const w = localRect.width / scale;
    const h = localRect.height / scale;
    for (const s of state.strokes || []) {
      c.strokeStyle = s.color || "#222";
      c.lineWidth = (s.size || 4);
      c.lineCap = "round";
      c.lineJoin = "round";
      c.beginPath();
      const pts = s.points || [];
      for (let i = 0; i < pts.length; i++) {
        const lx = ((pts[i][0] - offX) / w) * this.canvas.width;
        const ly = ((pts[i][1] - offY) / h) * this.canvas.height;
        if (i === 0) c.moveTo(lx, ly); else c.lineTo(lx, ly);
      }
      c.stroke();
    }
    this._lastStrokes = (state.strokes || []).length;
  }
}
