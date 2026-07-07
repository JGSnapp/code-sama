// Smooth cursor animator. The server sends Bezier legs; we ride them.

export class Cursor {
  constructor(el) {
    this.el = el;
    this.x = 512;
    this.y = 360;
    this.queue = [];
    this.running = false;
    this._raf = null;
    this.apply();
  }

  apply() {
    // SVG hotspot is at viewBox (2,2); center cursor on (x, y).
    this.el.style.transform = `translate(${this.x - 2}px, ${this.y - 2}px)`;
  }

  jumpTo(x, y) {
    this.x = x;
    this.y = y;
    this.apply();
  }

  runLegs(legs) {
    for (const l of legs) this.queue.push(l);
    this._pump();
  }

  _pump() {
    if (this.running) return;
    const next = this.queue.shift();
    if (!next) return;
    this.running = true;
    const start = performance.now();
    const { from, c1, c2, to, duration } = next;
    const dur = Math.max(40, duration);
    const tick = (now) => {
      const t = Math.min(1, (now - start) / dur);
      // cubic bezier
      const u = 1 - t;
      const x = u * u * u * from[0]
              + 3 * u * u * t * c1[0]
              + 3 * u * t * t * c2[0]
              + t * t * t * to[0];
      const y = u * u * u * from[1]
              + 3 * u * u * t * c1[1]
              + 3 * u * t * t * c2[1]
              + t * t * t * to[1];
      this.x = x;
      this.y = y;
      this.apply();
      if (t < 1) {
        this._raf = requestAnimationFrame(tick);
      } else {
        this.x = to[0];
        this.y = to[1];
        this.apply();
        this.running = false;
        this._pump();
      }
    };
    this._raf = requestAnimationFrame(tick);
  }
}
