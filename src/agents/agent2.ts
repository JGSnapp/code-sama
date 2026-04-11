import type { Agent2 } from "./contracts.js";
import type { Episode } from "../core/events.js";

export class WorkerAgent implements Agent2 {
  async planEpisode(goal: string): Promise<Episode> {
    return {
      id: `ep-${Date.now()}`,
      goal,
      expectedResult: "Goal delivered with verifiable checks",
      stopConditions: ["fatal_error", "manual_interrupt"],
      safePoints: ["step-update-task", "step-report"],
      viewerPlan: ["Show task board", "Implement code in VS-nya", "Run checks"],
      steps: [
        { id: "step-update-task", type: "update_task", payload: { taskId: "main", status: "in_progress" }, safePoint: true },
        { id: "step-open-editor", type: "open_window", payload: { windowId: "vs-nya" } },
        { id: "step-edit", type: "edit_file", payload: { file: "src/index.ts", snippet: "// implementation" } },
        { id: "step-report", type: "report", payload: { note: "Core implementation done" }, safePoint: true },
      ],
    };
  }
}
