import type { SceneEventBus } from "../core/eventBus.js";

export class VsNyaWindow {
  constructor(private readonly bus: SceneEventBus) {}

  async animateTyping(file: string, line: number, snippet: string): Promise<void> {
    await this.bus.emit({ type: "window.focused", windowId: "vs-nya" });
    await this.bus.emit({ type: "episode.progress", episodeId: "runtime", stepId: "typing", note: `Typing in ${file}:${line}` });
    await this.bus.emit({ type: "tool.called", tool: "editor.animateTyping", args: { file, line, snippet } });
  }
}

export class JiNyaWindow {
  constructor(private readonly bus: SceneEventBus) {}

  async updateTask(taskId: string, status: "todo" | "in_progress" | "done"): Promise<void> {
    await this.bus.emit({ type: "window.focused", windowId: "ji-nya" });
    await this.bus.emit({ type: "task.updated", taskId, status });
  }
}

export class NyaPaintWindow {
  constructor(private readonly bus: SceneEventBus) {}

  async drawDiagram(title: string, nodes: string[]): Promise<void> {
    await this.bus.emit({ type: "window.focused", windowId: "nya-paint" });
    await this.bus.emit({ type: "tool.called", tool: "paint.drawDiagram", args: { title, nodes } });
  }
}

export class BrowserWindow {
  constructor(private readonly bus: SceneEventBus) {}

  async openAndRead(url: string, query: string): Promise<void> {
    await this.bus.emit({ type: "window.focused", windowId: "browser" });
    await this.bus.emit({ type: "tool.called", tool: "playwright.browse", args: { url, query } });
  }
}
