// "My Computer" / file explorer — purely cosmetic, but lived-in.
export class ComputerApp {
  constructor(body, meta) {
    this.body = body;
    body.innerHTML = `
      <div class="menubar">
        <span>File</span><span>Edit</span><span>View</span><span>Help</span>
      </div>
      <div class="addr">
        <span class="lbl">Address</span>
        <div class="path">C:\\</div>
      </div>
      <div class="files"></div>
      <div class="statusbar"><span class="sb-count">0 object(s)</span><span>My Computer</span></div>
    `;
    this.pathEl = body.querySelector(".path");
    this.files = body.querySelector(".files");
    this.count = body.querySelector(".sb-count");
  }
  setFocusTarget(_) {}
  render(state) {
    const path = state.path || "C:\\";
    this.pathEl.textContent = path;
    const items = state.entries || [];
    this.files.innerHTML = "";
    for (const it of items) {
      const d = document.createElement("div");
      d.className = "f-item";
      const ico = document.createElement("div");
      ico.className = "f-ico " + (it.kind === "folder" ? "folder" : "doc");
      const name = document.createElement("div");
      name.className = "f-name";
      name.textContent = it.name;
      d.appendChild(ico); d.appendChild(name);
      this.files.appendChild(d);
    }
    this.count.textContent = `${items.length} object(s)`;
  }
}
