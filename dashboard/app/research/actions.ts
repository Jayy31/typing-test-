"use server";
import { revalidatePath } from "next/cache";
import { getDb } from "@/lib/data";

/** Owner deletes a research-agent lesson (soft delete; logged to the change log). */
export async function deleteLesson(form: FormData): Promise<void> {
  const id = Number(form.get("id"));
  if (!Number.isInteger(id)) throw new Error("bad lesson id");
  const db = await getDb();
  await db.deleteLesson(id);
  revalidatePath("/research");
}
