import type { Emotion, SceneEvent } from "../core/events.js";
import type { SceneEventBus } from "../core/eventBus.js";

export interface AvatarAdapter {
  loadModel(modelUri: string): Promise<void>;
  setEmotion(emotion: Emotion): Promise<void>;
  playAnimation(name: string): Promise<void>;
  lipSyncStart(text: string): Promise<void>;
  lipSyncStop(): Promise<void>;
}

export class VrmAvatarAdapter implements AvatarAdapter {
  async loadModel(_modelUri: string): Promise<void> {}
  async setEmotion(_emotion: Emotion): Promise<void> {}
  async playAnimation(_name: string): Promise<void> {}
  async lipSyncStart(_text: string): Promise<void> {}
  async lipSyncStop(): Promise<void> {}
}

export class AvatarController {
  private adapter: AvatarAdapter;

  constructor(private readonly bus: SceneEventBus, adapter: AvatarAdapter = new VrmAvatarAdapter()) {
    this.adapter = adapter;
  }

  async speak(text: string, emotion: Emotion = "focused"): Promise<void> {
    await this.bus.emit({ type: "avatar.emotion.changed", emotion });
    await this.adapter.setEmotion(emotion);
    await this.bus.emit({ type: "avatar.speak.requested", text });
    await this.bus.emit({ type: "avatar.speak.started", text });
    await this.adapter.lipSyncStart(text);
    await this.adapter.playAnimation("talking");
    await this.adapter.lipSyncStop();
    await this.bus.emit({ type: "avatar.speak.finished", text });
  }

  async reactToError(error: string): Promise<void> {
    await this.speak(`Упс, зафиксировала ошибку: ${error}.`, "frustrated");
  }

  async applyEvent(event: Extract<SceneEvent, { type: "avatar.emotion.changed" }>): Promise<void> {
    await this.adapter.setEmotion(event.emotion);
  }
}
