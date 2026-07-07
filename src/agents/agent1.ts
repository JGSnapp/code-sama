import type { Agent1, Agent1Command, Agent2Report } from "./contracts.js";
import type { SceneSnapshot } from "../core/events.js";

export class DirectorAgent implements Agent1 {
  async onSnapshot(snapshot: SceneSnapshot): Promise<void> {
    if (snapshot.unreadChatCount > 0 && snapshot.activeWindow !== "chat") {
      // would prioritize chat in real runtime
    }
  }

  async onAgent2Report(report: Agent2Report): Promise<Agent1Command[]> {
    const commands: Agent1Command[] = [];
    if (report.error) {
      commands.push({ type: "speak", payload: { text: `Есть ошибка: ${report.error}` } });
      commands.push({ type: "interrupt", payload: { safe: report.safeToInterrupt } });
    } else if (report.needsNarration) {
      commands.push({ type: "speak", payload: { text: `Сейчас делаю: ${report.nowDoing}` } });
    }
    return commands;
  }
}
