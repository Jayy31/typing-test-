/** shadcn-style primitives (copied-component pattern: Tailwind + cva + cn), server-safe. */
import Link from "next/link";
import type { ReactNode } from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/cn";

export function Card({ title, action, children, className }: { title?: ReactNode; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-xl border border-line bg-surface p-4", className)}>
      {(title || action) && (
        <div className="mb-3 flex items-center justify-between gap-2">
          {title && <h2 className="text-sm font-semibold text-ink">{title}</h2>}
          {action}
        </div>
      )}
      {children}
    </section>
  );
}

const badge = cva("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap", {
  variants: {
    tone: {
      neutral: "bg-grid text-ink-2",
      good: "bg-good-wash text-good-ink",
      warn: "bg-warn-wash text-ink",
      bad: "bg-bad-wash text-bad",
      info: "bg-grid text-s1",
    },
  },
  defaultVariants: { tone: "neutral" },
});

export function Badge({ tone, children, className }: VariantProps<typeof badge> & { children: ReactNode; className?: string }) {
  return <span className={cn(badge({ tone }), className)}>{children}</span>;
}

export function Direction({ value }: { value: string }) {
  const up = value === "bullish";
  return (
    <Badge tone={up ? "good" : "bad"}>
      <span aria-hidden>{up ? "▲" : "▼"}</span>
      {up ? "Bullish" : "Bearish"}
    </Badge>
  );
}

export function Stat({ label, value, sub, tone }: { label: string; value: ReactNode; sub?: ReactNode; tone?: "good" | "bad" }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-3">
      <div className="text-xs text-muted">{label}</div>
      <div className={cn("mt-1 text-xl font-semibold", tone === "good" && "text-good-ink", tone === "bad" && "text-bad")}>{value}</div>
      {sub && <div className="mt-0.5 text-xs text-ink-2">{sub}</div>}
    </div>
  );
}

export function Row({ href, children }: { href?: string; children: ReactNode }) {
  const cls = "flex items-center justify-between gap-3 border-t border-line py-2.5 first:border-t-0";
  return href ? (
    <Link href={href} className={cn(cls, "-mx-2 rounded-lg px-2 hover:bg-grid/60")}>
      {children}
    </Link>
  ) : (
    <div className={cls}>{children}</div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="py-2 text-sm text-muted">{children}</p>;
}

export function KV({ items }: { items: [string, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
      {items.map(([k, v]) => (
        <div key={k} className="min-w-0">
          <dt className="text-xs text-muted">{k}</dt>
          <dd className="tnum break-words text-ink">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Table({ head, rows, className }: { head: string[]; rows: ReactNode[][]; className?: string }) {
  return (
    <div className={cn("-mx-4 overflow-x-auto px-4", className)}>
      <table className="tnum w-full min-w-max text-sm">
        <thead>
          <tr className="text-left text-xs text-muted">
            {head.map((h) => (
              <th key={h} className="py-1.5 pr-4 font-medium">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-line">
              {r.map((c, j) => (
                <td key={j} className="py-2 pr-4 align-top">
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function PageTitle({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="mb-4">
      <h1 className="text-xl font-semibold">{title}</h1>
      {sub && <p className="mt-0.5 text-sm text-ink-2">{sub}</p>}
      {children}
    </div>
  );
}
