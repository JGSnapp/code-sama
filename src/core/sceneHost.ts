import { TypedEventBus } from "./eventBus.js";
import type { SceneEvent, SceneSnapshot, WindowId } from "./events.js";
import { SceneStateStore } from "./state.js";
import { AvatarController } from "../avatar/avatarController.js";
import { AudioSubsystem } from "../audio/audioSubsystem.js";

export class SceneHost {
  readonly bus = new TypedEventBus<SceneEvent>();
  readonly state = new SceneStateStore();
  readonly avatar = new AvatarController(this.bus);
  readonly audio = new AudioSubsystem(this.bus);

  constructor() {
    this.bus.on("window.opened", (e) => this.state.openWindow(e.windowId));
    this.bus.on("window.focused", (e) => {
      this.state.focusWindow(e.windowId);
      this.audio.playSfx("window-switch");
    });
    this.bus.on("window.layout.changed", (e) => this.state.setMode(e.mode));
    this.bus.on("avatar.emotion.changed", (e) => this.state.setEmotion(e.emotion));
    this.bus.on("avatar.speak.started", () => this.state.setSpeechStatus("playing"));
    this.bus.on("avatar.speak.finished", () => this.state.setSpeechStatus("idle"));
    this.bus.on("chat.received", () => this.state.incrementUnreadChat());
  }

  async openWindow(windowId: WindowId): Promise<void> {
    await this.bus.emit({ type: "window.opened", windowId });
    await this.bus.emit({ type: "window.focused", windowId });
  }

  getSnapshot(): SceneSnapshot {
    return this.state.snapshot();
  }
}
