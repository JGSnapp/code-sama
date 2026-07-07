export class TrackerApp {
  constructor(body, meta) {
    this.body = body;
    body.innerHTML = `
      <div class="row">
        <div class="input-box"></div>
        <div class="add">Add</div>
      </div>
      <ul></ul>
    `;
    this.input = body.querySelector(".input-box");
    this.list = body.querySelector("ul");
    this.focused = false;
  }
  setFocusTarget(target) {
    this.focused = target === "tracker_input";
    this.input.classList.toggle("focused", this.focused);
  }
  render(state) {
    this.input.textContent = state.draft || "";
    this.list.innerHTML = "";
    (state.tasks || []).forEach((t, i) => {
      const li = document.createElement("li");
      if (t.done) li.classList.add("done");
      const ch = document.createElement("span"); ch.className = "check";
      const tx = document.createElement("span"); tx.className = "text"; tx.textContent = `${i+1}. ${t.text}`;
      li.appendChild(ch); li.appendChild(tx);
      this.list.appendChild(li);
    });
  }
}
