export class FileWorker {
  async applyPatch(file: string, patch: string): Promise<{ file: string; bytes: number }> {
    return { file, bytes: patch.length };
  }
}

export class BrowserWorker {
  async fetchStructured(url: string): Promise<{ title: string; summary: string }> {
    return { title: `Loaded ${url}`, summary: "Structured content extracted" };
  }
}

export class GitWorker {
  async status(): Promise<string> {
    return "clean";
  }
}

export class TtsWorker {
  async speak(text: string): Promise<{ durationMs: number }> {
    return { durationMs: text.length * 35 };
  }
}
