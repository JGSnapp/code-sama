import { CodeSamaRuntime } from "./runtime/orchestrator.js";

export async function bootDemoRuntime(): Promise<CodeSamaRuntime> {
  const runtime = new CodeSamaRuntime();
  await runtime.runGoal("Implement architecture-aligned MVP");
  return runtime;
}
