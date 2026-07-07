export class BrowserApp {
  constructor(body, meta) {
    this.body = body;
    this.meta = meta;
    body.innerHTML = `
      <div class="menubar">
        <span>File</span><span>Edit</span><span>View</span><span>Favorites</span><span>Help</span>
      </div>
      <div class="urlbar">
        <div class="nav-btn" title="back">&laquo;</div>
        <div class="nav-btn" title="forward">&raquo;</div>
        <div class="nav-btn" title="reload">&circlearrowright;</div>
        <span class="lbl">Address</span>
        <div class="urlbox"></div>
      </div>
      <div class="viewport">
        <div class="placeholder">Browser is idle — ask the agent to navigate somewhere.</div>
        <div class="error" style="display:none;"></div>
        <img alt="page" style="display:none;" />
        <div class="loading-bar" style="display:none;"></div>
      </div>
      <div class="statusbar"><span class="st-text">Done</span><span class="st-sec">Internet</span></div>
    `;
    this.url = body.querySelector(".urlbox");
    this.viewport = body.querySelector(".viewport");
    this.img = body.querySelector("img");
    this.placeholder = body.querySelector(".placeholder");
    this.error = body.querySelector(".error");
    this.loading = body.querySelector(".loading-bar");
    this.status = body.querySelector(".st-text");
    this.focused = false;
  }

  setFocusTarget(target) {
    this.focused = target === "url";
    this.url.classList.toggle("focused", this.focused);
  }

  render(state) {
    const showDraft = (state.url_draft !== undefined) && this.focused;
    this.url.textContent = showDraft ? state.url_draft : (state.url || "");
    if (state.frame) {
      this.img.src = "data:image/jpeg;base64," + state.frame;
      this.img.style.display = "block";
      this.placeholder.style.display = "none";
      this.error.style.display = "none";
      this.status.textContent = "Done";
    } else if (state.error) {
      this.img.style.display = "none";
      this.placeholder.style.display = "none";
      this.error.style.display = "flex";
      this.error.textContent = "Не удалось загрузить страницу: " + String(state.error).slice(0, 200);
      this.status.textContent = "Error";
    } else {
      this.img.style.display = "none";
      this.placeholder.style.display = "flex";
      this.error.style.display = "none";
      this.status.textContent = "Ready";
    }
    this.loading.style.display = state.loading ? "block" : "none";
    if (state.loading) this.status.textContent = "Loading…";
  }
}
