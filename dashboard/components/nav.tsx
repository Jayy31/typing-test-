"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";

const TABS = [
  { href: "/", label: "Today", icon: "◉" },
  { href: "/signals", label: "Signals", icon: "↗" },
  { href: "/trades", label: "Trades", icon: "⇄" },
  { href: "/more", label: "More", icon: "☰" },
];

export function BottomNav() {
  const path = usePathname();
  const active = (href: string) => (href === "/" ? path === "/" : path.startsWith(href));
  const moreActive = !TABS.slice(0, 3).some((t) => active(t.href));
  return (
    <nav aria-label="Main" className="fixed inset-x-0 bottom-0 z-20 border-t border-line bg-surface/95 backdrop-blur pb-[env(safe-area-inset-bottom)] md:static md:border-0 md:bg-transparent">
      <ul className="mx-auto flex max-w-3xl md:gap-2">
        {TABS.map((t) => {
          const on = t.href === "/more" ? moreActive : active(t.href);
          return (
            <li key={t.href} className="flex-1 md:flex-none">
              <Link
                href={t.href}
                aria-current={on ? "page" : undefined}
                className={cn(
                  "flex flex-col items-center gap-0.5 py-2 text-xs md:flex-row md:gap-1.5 md:rounded-lg md:px-3 md:text-sm",
                  on ? "font-semibold text-s1" : "text-ink-2",
                )}
              >
                <span aria-hidden className="text-base md:text-sm">{t.icon}</span>
                {t.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
