export class EditorApp {
  constructor(body, meta) {
    this.body = body;
    body.innerHTML = `
      <div class="menubar">
        <span>File</span><span>Edit</span><span>Search</span><span>View</span><span>Run</span><span>Help</span>
      </div>
      <div class="editor-main">
        <div class="file-tree">
          <div class="ft-head">Explorer</div>
          <div class="ft-body"></div>
        </div>
        <div class="editor-right">
          <div class="tabs"></div>
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
        </div>
      </div>
    `;
    this.tabsEl = body.querySelector(".tabs");
    this.treeBody = body.querySelector(".ft-body");
    this.sbLang = body.querySelector(".sb-lang");
    this.sbPos = body.querySelector(".sb-pos");
    this.terminal = body.querySelector(".terminal");
    this.termBody = body.querySelector(".term-body");
    const host = body.querySelector(".cm");
    this.cm = window.CodeMirror(host, {
      value: "",
      mode: "python",
      lineNumbers: true,
      readOnly: true,
      theme: "default",
      indentUnit: 4,
      cursorBlinkRate: 530,
    });
    setTimeout(() => this.cm.refresh(), 30);
    this._last = "";
    this._termLastLen = 0;
    this._lastTree = "";
    this._lastTabs = "";
  }

  setFocusTarget(_) {
    try { this.cm.focus(); } catch (_) { /* ignore */ }
  }
  resize() { this.cm.refresh(); }

  _indexToPos(content, index) {
    const safe = Math.max(0, Math.min(index ?? content.length, content.length));
    let line = 0;
    let col = 0;
    for (let i = 0; i < safe; i++) {
      if (content[i] === "\n") { line++; col = 0; }
      else col++;
    }
    return { line, ch: col };
  }

  _renderTree(nodes, depth = 0) {
    const frag = document.createDocumentFragment();
    for (const n of nodes || []) {
      const row = document.createElement("div");
      row.className = "ft-item ft-" + (n.kind === "dir" ? "dir" : "file");
      row.style.paddingLeft = (6 + depth * 10) + "px";
      row.textContent = (n.kind === "dir" ? "📁 " : "📄 ") + n.name;
      row.title = n.path || "";
      frag.appendChild(row);
      if (n.kind === "dir" && n.children?.length) {
        frag.appendChild(this._renderTree(n.children, depth + 1));
      }
    }
    return frag;
  }

  render(state) {
    const path = state.path || "";
    const filename = state.filename || (path.split("/").pop()) || "untitled";
    const lang = state.language || "python";
    const mode = ({
      python: "python", js: "javascript", javascript: "javascript",
      html: "htmlmixed", css: "css", json: "application/json", plain: "null",
    })[lang] || "null";
    if (this.cm.getOption("mode") !== mode) this.cm.setOption("mode", mode);
    this.sbLang.textContent = lang.charAt(0).toUpperCase() + lang.slice(1);

    // File tree
    const treeKey = JSON.stringify(state.tree || []);
    if (treeKey !== this._lastTree) {
      this.treeBody.innerHTML = "";
      const root = document.createElement("div");
      root.className = "ft-root";
      root.textContent = state.project || "projects";
      this.treeBody.appendChild(root);
      this.treeBody.appendChild(this._renderTree(state.tree || []));
      this._lastTree = treeKey;
    }

    // Tabs
    const openFiles = state.open_files || (path ? [path] : []);
    const tabsKey = openFiles.join("|") + "::" + path;
    if (tabsKey !== this._lastTabs) {
      this.tabsEl.innerHTML = "";
      for (const f of openFiles) {
        const tab = document.createElement("div");
        tab.className = "tab" + (f === path ? " active" : "");
        tab.innerHTML = `<span class="dot"></span><span class="fname"></span>`;
        tab.querySelector(".fname").textContent = (f.split("/").pop()) || f;
        this.tabsEl.appendChild(tab);
      }
      if (!openFiles.length) {
        const tab = document.createElement("div");
        tab.className = "tab active";
        tab.innerHTML = `<span class="dot"></span><span class="fname"></span>`;
        tab.querySelector(".fname").textContent = filename;
        this.tabsEl.appendChild(tab);
      }
      this._lastTabs = tabsKey;
    }

    const content = state.content || "";
    const caret = typeof state.caret === "number" ? state.caret : content.length;
    if (content.startsWith(this._last) && content.length >= this._last.length && this._last !== "") {
      const added = content.slice(this._last.length);
      if (added) {
        const end = this.cm.posFromIndex(this._last.length);
        this.cm.replaceRange(added, end);
      }
      this._last = content;
    } else if (content !== this._last) {
      this.cm.setValue(content);
      this._last = content;
    }
    const pos = this._indexToPos(content, caret);
    this.cm.setCursor(pos);
    this.cm.scrollIntoView(pos, 80);
    this.sbPos.textContent = `Ln ${pos.line + 1}, Col ${pos.ch + 1}`;

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
