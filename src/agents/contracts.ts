import type { Episode, SceneSnapshot } from "../core/events.js";

export interface Agent1Command {
  type: "set_goal" | "interrupt" | "switch_mode" | "speak";
  payload: Record<string, unknown>;
}

export interface Agent2Report {
  nowDoing: string;
  currentFile?: string;
  stepCompleted?: string;
  error?: string;
  needsNarration: boolean;
  safeToInterrupt: boolean;
}

export interface Agent1 {
  onSnapshot(snapshot: SceneSnapshot): Promise<void>;
  onAgent2Report(report: Agent2Report): Promise<Agent1Command[]>;
}

export interface Agent2 {
  planEpisode(goal: string): Promise<Episode>;
}
