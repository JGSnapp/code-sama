import type { SceneEventBus } from "../core/eventBus.js";

export class AudioSubsystem {
  private readonly cueMap = new Map<string, string>([
    ["window-switch", "ui/window-switch.wav"],
    ["task-complete", "ui/task-complete.wav"],
    ["error", "ui/error.wav"],
  ]);

  constructor(private readonly bus: SceneEventBus) {}

  playSfx(cue: string): void {
    const asset = this.cueMap.get(cue) ?? "ui/default.wav";
    void this.bus.emit({ type: "audio.sfx.requested", cue: asset });
  }
}
