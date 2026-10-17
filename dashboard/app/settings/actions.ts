"use server";
import { revalidatePath } from "next/cache";
import { getDb } from "@/lib/data";
import { SWITCHES } from "@/lib/switches";

/** Owner switch change. The database enforces the same rules (RLS: owner email, switch keys,
 * boolean values) and logs who/when/old -> new in config_audit. */
export async function toggleSwitch(form: FormData): Promise<void> {
  const key = String(form.get("key"));
  const value = form.get("value") === "true";
  if (!SWITCHES.some((s) => s.key === key)) throw new Error("not an owner switch");
  const db = await getDb();
  await db.setSwitch(key, value);
  revalidatePath("/settings");
}
