export class Chat {
  constructor() {
    this.log = document.getElementById("chat-log");
  }
  renderAll(messages) {
    this.log.innerHTML = "";
    for (const m of messages) this.append(m);
  }
  append(msg) {
    const d = document.createElement("div");
    d.className = "msg " + (msg.role || "assistant");
    d.textContent = msg.content;
    this.log.appendChild(d);
    this.log.scrollTop = this.log.scrollHeight;
  }
}
