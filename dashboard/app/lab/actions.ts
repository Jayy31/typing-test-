"use server";
import { revalidatePath } from "next/cache";
import { getDb } from "@/lib/data";

/** Telegram alerts for a validated lab book. The database allows only this column on lab_books
 * (owner email, RLS) and logs the change to config_audit as lab.alerts.<book>. */
export async function toggleLabAlerts(form: FormData): Promise<void> {
  const id = Number(form.get("id"));
  if (!Number.isInteger(id)) throw new Error("bad book id");
  const db = await getDb();
  await db.setLabAlerts(id, form.get("value") === "true");
  revalidatePath("/lab");
}
