import { Badge, Card, PageTitle, Table } from "@/components/ui";
import { getDb } from "@/lib/data";
import { when } from "@/lib/format";
import { config } from "@/lib/queries";
import { SWITCHES } from "@/lib/switches";
import { toggleSwitch } from "./actions";

export const dynamic = "force-dynamic";

const HIDDEN = new Set(["source_health", "unverified_alerts"]);

export default async function Settings() {
  const db = await getDb();
  const [cfg, audit, [latest], email] = await Promise.all([
    config(db),
    db.rows<{ id: number; ts: string; key: string; old_value: unknown; new_value: unknown; changed_by: string | null }>("config_audit", { order: ["ts", "desc"], limit: 50 }),
    db.rows<{ engine_version: string | null }>("signals", { order: ["created_at", "desc"], limit: 1 }),
    db.userEmail(),
  ]);
  const value = (k: string, d: boolean) => (cfg[k] === undefined ? d : Boolean(cfg[k]));
  const other = Object.entries(cfg).filter(([k]) => !HIDDEN.has(k) && !SWITCHES.some((s) => s.key === k) && !k.includes("seen"));
  return (
    <>
      <PageTitle title="Settings" sub="Owner switches only. Thresholds, weights and rules stay in the worker's YAML config." />
      <Card title="Owner switches" action={<span className="text-xs text-muted">{email}</span>}>
        {SWITCHES.map((s) => {
          const on = value(s.key, s.default);
          return (
            <form key={s.key} action={toggleSwitch} className="flex items-center justify-between gap-3 border-t border-line py-2.5 first:border-t-0">
              <input type="hidden" name="key" value={s.key} />
              <input type="hidden" name="value" value={String(!on)} />
              <span className="min-w-0">
                <span className="block text-sm font-medium">{s.label}</span>
                <span className="text-xs text-ink-2">{s.help} · <code>{s.key}</code></span>
              </span>
              <button type="submit" role="switch" aria-checked={on} aria-label={s.label} data-key={s.key}
                className={`relative h-7 w-12 shrink-0 rounded-full transition-colors ${on ? "bg-s1" : "bg-grid"}`}>
                <span className={`absolute top-1 h-5 w-5 rounded-full bg-white shadow transition-all ${on ? "left-6" : "left-1"}`} />
              </button>
            </form>
          );
        })}
      </Card>
      <Card title="Change log" className="mt-3">
        <Table head={["When", "Switch", "Change", "By"]} rows={audit.map((a) => [
          when(a.ts), <code key="k" className="text-xs">{a.key}</code>, `${a.old_value === null ? "unset" : String(a.old_value)} → ${String(a.new_value)}`,
          a.changed_by?.includes("@") ? a.changed_by : <Badge key="w">worker / Telegram</Badge>,
        ])} />
      </Card>
      <Card title="Read-only" className="mt-3">
        <Table head={["Key", "Value"]} rows={[
          ["engine version", latest?.engine_version ?? "n/a"],
          ...other.map(([k, v]) => [k, <code key={k} className="text-xs">{JSON.stringify(v).slice(0, 80)}</code>]),
        ]} />
      </Card>
    </>
  );
}
