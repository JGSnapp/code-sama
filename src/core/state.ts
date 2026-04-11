import type { Emotion, SceneSnapshot, WindowId } from "./events.js";

export interface WindowState {
  id: WindowId;
  open: boolean;
}

export interface SceneState {
  windows: Map<WindowId, WindowState>;
  activeWindow: WindowId | null;
  mode: "work" | "chat";
  currentFile: string | null;
  cursorLine: number | null;
  operation: string | null;
  taskStatus: "idle" | "in_progress" | "blocked" | "done";
  speechStatus: "idle" | "playing";
  unreadChatCount: number;
  emotion: Emotion;
}

export class SceneStateStore {
  private readonly state: SceneState = {
    windows: new Map(),
    activeWindow: null,
    mode: "work",
    currentFile: null,
    cursorLine: null,
    operation: null,
    taskStatus: "idle",
    speechStatus: "idle",
    unreadChatCount: 0,
    emotion: "neutral",
  };

  openWindow(id: WindowId): void {
    this.state.windows.set(id, { id, open: true });
  }

  focusWindow(id: WindowId): void {
    this.openWindow(id);
    this.state.activeWindow = id;
  }

  setMode(mode: "work" | "chat"): void {
    this.state.mode = mode;
  }

  setEditorContext(file: string, line: number, operation: string): void {
    this.state.currentFile = file;
    this.state.cursorLine = line;
    this.state.operation = operation;
  }

  setTaskStatus(status: SceneState["taskStatus"]): void {
    this.state.taskStatus = status;
  }

  setSpeechStatus(status: SceneState["speechStatus"]): void {
    this.state.speechStatus = status;
  }

  incrementUnreadChat(): void {
    this.state.unreadChatCount += 1;
  }

  clearUnreadChat(): void {
    this.state.unreadChatCount = 0;
  }

  setEmotion(emotion: Emotion): void {
    this.state.emotion = emotion;
  }

  snapshot(): SceneSnapshot {
    return {
      activeWindow: this.state.activeWindow,
      openWindows: [...this.state.windows.values()].filter((x) => x.open).map((x) => x.id),
      currentFile: this.state.currentFile,
      cursorLine: this.state.cursorLine,
      operation: this.state.operation,
      taskStatus: this.state.taskStatus,
      speechStatus: this.state.speechStatus,
      unreadChatCount: this.state.unreadChatCount,
      emotion: this.state.emotion,
    };
  }
}
