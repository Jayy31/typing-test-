"use server";
import { revalidatePath } from "next/cache";
import { getDb } from "@/lib/data";

/** Records the owner's decision only. The worker opens a GitHub PR for accepted suggestions and
 * merges one per month at month start (without GITHUB_TOKEN_CONFIG_PR: record-only). */
export async function decide(form: FormData): Promise<void> {
  const id = Number(form.get("id"));
  const status = form.get("status");
  if (!Number.isInteger(id) || (status !== "accepted" && status !== "rejected")) throw new Error("bad decision");
  const db = await getDb();
  await db.decideSuggestion(id, status);
  revalidatePath("/suggestions");
}
