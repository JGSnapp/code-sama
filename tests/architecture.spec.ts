import { describe, expect, it } from "vitest";
import { bootDemoRuntime } from "../src/index.js";
import { bootstrapSchemaSql } from "../src/storage/sqliteRepository.js";

describe("Code-sama architecture MVP", () => {
  it("maintains a single structured scene state instead of screenshots", async () => {
    const runtime = await bootDemoRuntime();
    const snapshot = runtime.scene.getSnapshot();

    expect(snapshot.openWindows.length).toBeGreaterThan(0);
    expect(snapshot.activeWindow).toBeTruthy();
    expect(typeof snapshot.unreadChatCount).toBe("number");
  });

  it("orchestrates Agent1 + Agent2 with episode execution", async () => {
    const runtime = await bootDemoRuntime();
    const events = runtime.db.listEvents().map((x) => x.type);

    expect(events).toContain("episode.approved");
    expect(events).toContain("episode.completed");
    expect(events).toContain("avatar.speak.started");
    expect(events).toContain("task.updated");
  });

  it("contains SQLite schema for required persistence entities", () => {
    const requiredTables = [
      "sessions",
      "episodes",
      "tasks",
      "events",
      "chat_messages",
      "utterances",
      "project_states",
      "scene_settings",
      "voice_sound_settings",
    ];

    for (const table of requiredTables) {
      expect(bootstrapSchemaSql).toContain(`CREATE TABLE IF NOT EXISTS ${table}`);
    }
  });
});
