export class EditorApp {
  constructor(body, meta) {
    this.body = body;
    body.innerHTML = `
      <div class="menubar">
        <span>File</span><span>Edit</span><span>Search</span><span>View</span><span>Run</span><span>Help</span>
      </div>
      <div class="tabs"><div class="tab"><span class="dot" style="display:inline-block;width:6px;height:6px;border-radius:50%;background:#e33;"></span><span class="fname">untitled.py</span></div></div>
      <div class="split">
        <div class="cm"></div>
        <div class="terminal" style="display:none;">
          <div class="term-head">
            <span class="term-title">Terminal — sandbox</span>
            <span class="term-tag">▶</span>
          </div>
          <div class="term-body"></div>
        </div>
      </div>
      <div class="statusbar"><span class="sb-lang">Python</span><span class="sb-pos">Ln 1, Col 1</span><span class="sb-enc">UTF-8</span></div>
    `;
    this.tab = body.querySelector(".tab .fname");
    this.sbLang = body.querySelector(".sb-lang");
    this.sbPos = body.querySelector(".sb-pos");
    this.terminal = body.querySelector(".terminal");
    this.termBody = body.querySelector(".term-body");
    const host = body.querySelector(".cm");
    this.cm = window.CodeMirror(host, {
      value: "",
      mode: "python",
      lineNumbers: true,
      readOnly: "nocursor",
      theme: "default",
      indentUnit: 4,
    });
    setTimeout(() => this.cm.refresh(), 30);
    this._last = "";
    this._termLastLen = 0;
  }

  setFocusTarget(_) {}
  resize() { this.cm.refresh(); }

  render(state) {
    if (state.filename) this.tab.textContent = state.filename;
    const lang = state.language || "python";
    const mode = ({
      python: "python", js: "javascript", javascript: "javascript",
      html: "htmlmixed", css: "css", json: "application/json", plain: "null",
    })[lang] || "null";
    if (this.cm.getOption("mode") !== mode) this.cm.setOption("mode", mode);
    this.sbLang.textContent = lang.charAt(0).toUpperCase() + lang.slice(1);
    const content = state.content || "";
    if (content !== this._last) {
      this.cm.setValue(content);
      const lines = this.cm.lineCount();
      const lastLine = this.cm.getLine(lines - 1) || "";
      this.cm.setCursor(lines - 1, lastLine.length);
      this.cm.scrollIntoView({ line: lines - 1, ch: lastLine.length }, 60);
      this.sbPos.textContent = `Ln ${lines}, Col ${lastLine.length + 1}`;
      this._last = content;
    }
    // Terminal panel
    const open = !!state.terminal_open;
    this.terminal.style.display = open ? "flex" : "none";
    if (open) {
      const lines = state.terminal_lines || [];
      if (lines.length !== this._termLastLen) {
        this.termBody.innerHTML = "";
        for (const l of lines) {
          const d = document.createElement("div");
          d.className = "term-line term-" + (l.kind || "out");
          d.textContent = l.text;
          this.termBody.appendChild(d);
        }
        this.termBody.scrollTop = this.termBody.scrollHeight;
        this._termLastLen = lines.length;
      }
    } else {
      this._termLastLen = 0;
    }
    this.cm.refresh();
  }
}
