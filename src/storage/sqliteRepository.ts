export const bootstrapSchemaSql = `
CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL,
  ended_at TEXT
);
CREATE TABLE IF NOT EXISTS episodes (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL,
  goal TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  priority INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_messages (
  id TEXT PRIMARY KEY,
  role TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS utterances (
  id TEXT PRIMARY KEY,
  text TEXT NOT NULL,
  emotion TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS project_states (
  id TEXT PRIMARY KEY,
  path TEXT NOT NULL,
  state_json TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scene_settings (
  id TEXT PRIMARY KEY,
  settings_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voice_sound_settings (
  id TEXT PRIMARY KEY,
  voice_json TEXT NOT NULL,
  soundpack_json TEXT NOT NULL
);
`;

export interface EventLogRecord {
  type: string;
  payload: Record<string, unknown>;
  createdAt: string;
}

export class SqliteRepository {
  private events: EventLogRecord[] = [];

  schemaSql(): string {
    return bootstrapSchemaSql;
  }

  appendEvent(record: EventLogRecord): void {
    this.events.push(record);
  }

  listEvents(): EventLogRecord[] {
    return [...this.events];
  }
}
