"use server";

import { revalidatePath } from "next/cache";

import { AgentError, agentJson, type TaskSchedule } from "@/lib/agent";
import { requireSession } from "@/lib/auth/session";

export type TaskResult = { ok: true; message: string } | { ok: false; message: string };

export type TaskInput = {
  name: string;
  prompt: string;
  schedule: TaskSchedule;
  time_zone: string;
  permission_mode: "ask" | "auto";
  /** Empty removes it */
  rubric: string;
  max_iterations: number;
};

async function call(path: string, init: RequestInit, message: string): Promise<TaskResult> {
  const session = await requireSession("/scheduled");
  try {
    await agentJson<unknown>(session, path, init);
  } catch (error) {
    return { ok: false, message: error instanceof AgentError ? error.message : "Something went wrong. Try again." };
  }
  revalidatePath("/scheduled");
  return { ok: true, message };
}

const json = (method: string, body: unknown): RequestInit => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export async function addTask(input: TaskInput) {
  return call("/v1/tasks", json("POST", input), "Scheduled.");
}

export async function changeTask(id: string, input: TaskInput) {
  return call(`/v1/tasks/${encodeURIComponent(id)}`, json("PATCH", input), "Saved.");
}

export async function runTask(id: string) {
  return call(`/v1/tasks/${encodeURIComponent(id)}/run`, { method: "POST" }, "Running it now, in a new chat.");
}

export async function pauseTask(id: string, paused: boolean) {
  return call(`/v1/tasks/${encodeURIComponent(id)}/${paused ? "pause" : "resume"}`, { method: "POST" }, paused ? "Paused." : "Resumed.");
}

export async function deleteTask(id: string) {
  return call(`/v1/tasks/${encodeURIComponent(id)}`, { method: "DELETE" }, "Deleted. Its chats stay.");
}

export type TriggerResult = { ok: true; url: string; token: string } | { ok: false; message: string };

/** Makes the task's API trigger: its token is returned this once (a new one revokes the old). */
export async function makeTrigger(id: string): Promise<TriggerResult> {
  const session = await requireSession("/scheduled");
  try {
    const made = await agentJson<{ url: string; token: string }>(session, `/v1/tasks/${encodeURIComponent(id)}/trigger`, { method: "POST" });
    revalidatePath("/scheduled");
    return { ok: true, ...made };
  } catch (error) {
    return { ok: false, message: error instanceof AgentError ? error.message : "Something went wrong. Try again." };
  }
}

export async function revokeTrigger(id: string) {
  return call(`/v1/tasks/${encodeURIComponent(id)}/trigger`, { method: "DELETE" }, "Revoked: the token no longer fires it.");
}
