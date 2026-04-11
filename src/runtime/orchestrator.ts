import { DirectorAgent } from "../agents/agent1.js";
import { WorkerAgent } from "../agents/agent2.js";
import type { Episode } from "../core/events.js";
import { SceneHost } from "../core/sceneHost.js";
import { SqliteRepository } from "../storage/sqliteRepository.js";
import { BrowserWindow, JiNyaWindow, NyaPaintWindow, VsNyaWindow } from "../windows/windows.js";

export class CodeSamaRuntime {
  readonly scene = new SceneHost();
  readonly agent1 = new DirectorAgent();
  readonly agent2 = new WorkerAgent();
  readonly db = new SqliteRepository();
  readonly windows = {
    vs: new VsNyaWindow(this.scene.bus),
    ji: new JiNyaWindow(this.scene.bus),
    paint: new NyaPaintWindow(this.scene.bus),
    browser: new BrowserWindow(this.scene.bus),
  };

  constructor() {
    this.scene.bus.onAny(async (event) => {
      this.db.appendEvent({
        type: event.type,
        payload: event as unknown as Record<string, unknown>,
        createdAt: new Date().toISOString(),
      });
    });
  }

  async runGoal(goal: string): Promise<Episode> {
    const episode = await this.agent2.planEpisode(goal);
    await this.scene.bus.emit({ type: "episode.approved", episode });
    await this.executeEpisode(episode);
    return episode;
  }

  private async executeEpisode(episode: Episode): Promise<void> {
    for (const step of episode.steps) {
      switch (step.type) {
        case "update_task":
          await this.windows.ji.updateTask(String(step.payload.taskId), step.payload.status as "todo" | "in_progress" | "done");
          break;
        case "open_window":
          await this.scene.openWindow(step.payload.windowId as never);
          break;
        case "edit_file":
          await this.windows.vs.animateTyping(String(step.payload.file), 1, String(step.payload.snippet));
          break;
        case "browse":
          await this.windows.browser.openAndRead(String(step.payload.url), String(step.payload.query));
          break;
        case "draw_diagram":
          await this.windows.paint.drawDiagram(String(step.payload.title), (step.payload.nodes as string[]) ?? []);
          break;
        case "report": {
          await this.scene.bus.emit({
            type: "episode.progress",
            episodeId: episode.id,
            stepId: step.id,
            note: String(step.payload.note),
          });
          const commands = await this.agent1.onAgent2Report({
            nowDoing: String(step.payload.note),
            needsNarration: true,
            safeToInterrupt: true,
          });
          for (const command of commands) {
            if (command.type === "speak") {
              await this.scene.avatar.speak(String(command.payload.text));
            }
          }
          break;
        }
        case "run_command":
          await this.scene.bus.emit({ type: "tool.called", tool: "terminal.run", args: step.payload });
          break;
      }

      if (step.safePoint) {
        await this.scene.bus.emit({
          type: "episode.progress",
          episodeId: episode.id,
          stepId: step.id,
          note: "Safe point reached",
        });
      }
    }

    await this.scene.bus.emit({ type: "episode.completed", episodeId: episode.id });
    this.scene.audio.playSfx("task-complete");
  }
}
