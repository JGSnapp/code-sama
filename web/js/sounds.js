// Tiny ambient sound effects via Web Audio. The user gesture (chat submit)
// is what unlocks the AudioContext on modern browsers.

export class Sounds {
  constructor() {
    this.ctx = null;
    this._lastKey = 0;
    this._lastClick = 0;
    this.muted = false;
    // Initialise on first user gesture.
    const unlock = () => {
      if (this.ctx) return;
      try {
        this.ctx = new (window.AudioContext || window.webkitAudioContext)();
      } catch (e) { /* no audio */ }
      document.removeEventListener("pointerdown", unlock);
      document.removeEventListener("keydown", unlock);
    };
    document.addEventListener("pointerdown", unlock);
    document.addEventListener("keydown", unlock);
  }

  _tone(freq, dur = 0.06, type = "square", gain = 0.05) {
    if (this.muted || !this.ctx) return;
    const t = this.ctx.currentTime;
    const osc = this.ctx.createOscillator();
    const g = this.ctx.createGain();
    osc.type = type;
    osc.frequency.setValueAtTime(freq, t);
    g.gain.setValueAtTime(0, t);
    g.gain.linearRampToValueAtTime(gain, t + 0.005);
    g.gain.exponentialRampToValueAtTime(0.0001, t + dur);
    osc.connect(g).connect(this.ctx.destination);
    osc.start(t);
    osc.stop(t + dur + 0.02);
  }

  click() {
    const now = performance.now();
    if (now - this._lastClick < 60) return;
    this._lastClick = now;
    this._tone(900, 0.03, "square", 0.025);
  }
  key() {
    const now = performance.now();
    if (now - this._lastKey < 25) return;
    this._lastKey = now;
    this._tone(2200 + Math.random() * 600, 0.018, "triangle", 0.013);
  }
  openWindow() { this._tone(440, 0.05, "sine", 0.04); setTimeout(() => this._tone(660, 0.06, "sine", 0.04), 50); }
  close() { this._tone(330, 0.04, "sine", 0.03); }
  chime() { this._tone(800, 0.07, "sine", 0.03); setTimeout(() => this._tone(1000, 0.07, "sine", 0.03), 70); }
}
