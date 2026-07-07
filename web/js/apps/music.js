export class MusicApp {
  constructor(body, meta) {
    this.body = body;
    body.innerHTML = `
      <div class="now">--:-- ▶ No track</div>
      <div class="progress"><i></i></div>
      <ul></ul>
      <div class="controls">
        <div class="btn-m" title="prev">⏮</div>
        <div class="btn-m" title="play">▶</div>
        <div class="btn-m" title="pause">❚❚</div>
        <div class="btn-m" title="next">⏭</div>
      </div>
    `;
    this.now = body.querySelector(".now");
    this.list = body.querySelector("ul");
    this.bar = body.querySelector(".progress");
    this.audio = new Audio();
    this.audio.preload = "none";
    this._last = { index: -1, playing: false };
    this._t = 0;
    setInterval(() => this._tickBar(), 350);
  }
  setFocusTarget(_) {}
  _tickBar() {
    if (!this.audio.duration) return;
    const pct = Math.min(100, (this.audio.currentTime / this.audio.duration) * 100);
    this.bar.style.setProperty("--pct", pct + "%");
  }
  render(state) {
    const playlist = state.playlist || [];
    this.list.innerHTML = "";
    playlist.forEach((t, i) => {
      const li = document.createElement("li");
      li.textContent = `${String(i+1).padStart(2,"0")}. ${t.title}`;
      if (i === state.index) li.classList.add("active");
      this.list.appendChild(li);
    });
    const cur = playlist[state.index || 0];
    this.now.textContent = (state.playing ? "▶ NOW PLAYING — " : "❚❚ STOPPED — ") + (cur ? cur.title : "No track");
    if (cur && (state.index !== this._last.index || state.playing !== this._last.playing)) {
      try {
        if (state.index !== this._last.index) this.audio.src = cur.src;
        if (state.playing) this.audio.play().catch(() => {});
        else this.audio.pause();
      } catch (e) { /* ignore */ }
      this._last = { index: state.index, playing: state.playing };
    }
  }
}
