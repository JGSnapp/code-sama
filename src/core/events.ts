export type WindowId =
  | "scene"
  | "vs-nya"
  | "ji-nya"
  | "nya-paint"
  | "browser"
  | "github"
  | "chat"
  | "fortune-wheel";

export type Emotion = "neutral" | "happy" | "focused" | "frustrated" | "tired" | "surprised";

export type AgentId = "agent-1" | "agent-2";

export type EpisodeStepType =
  | "open_window"
  | "edit_file"
  | "run_command"
  | "update_task"
  | "browse"
  | "draw_diagram"
  | "report";

export interface EpisodeStep {
  id: string;
  type: EpisodeStepType;
  payload: Record<string, unknown>;
  safePoint?: boolean;
}

export interface Episode {
  id: string;
  goal: string;
  steps: EpisodeStep[];
  expectedResult: string;
  stopConditions: string[];
  safePoints: string[];
  viewerPlan: string[];
}

export type SceneEvent =
  | { type: "window.opened"; windowId: WindowId }
  | { type: "window.focused"; windowId: WindowId }
  | { type: "window.layout.changed"; mode: "work" | "chat" }
  | { type: "avatar.emotion.changed"; emotion: Emotion }
  | { type: "avatar.speak.requested"; text: string }
  | { type: "avatar.speak.started"; text: string }
  | { type: "avatar.speak.finished"; text: string }
  | { type: "chat.received"; id: string; message: string; priority: "normal" | "high" }
  | { type: "task.updated"; taskId: string; status: "todo" | "in_progress" | "done" }
  | { type: "episode.approved"; episode: Episode }
  | { type: "episode.progress"; episodeId: string; stepId: string; note: string }
  | { type: "episode.error"; episodeId: string; error: string; safeToInterrupt: boolean }
  | { type: "episode.completed"; episodeId: string }
  | { type: "tool.called"; tool: string; args: Record<string, unknown> }
  | { type: "audio.sfx.requested"; cue: string }
  | { type: "state.snapshot.requested"; by: AgentId };

export interface SceneSnapshot {
  activeWindow: WindowId | null;
  openWindows: WindowId[];
  currentFile: string | null;
  cursorLine: number | null;
  operation: string | null;
  taskStatus: "idle" | "in_progress" | "blocked" | "done";
  speechStatus: "idle" | "playing";
  unreadChatCount: number;
  emotion: Emotion;
}
