"use client";

import { useEffect, useState } from "react";

type Choice = "system" | "light" | "dark";
const KEY = "theme";
const NEXT: Record<Choice, Choice> = {
  system: "light",
  light: "dark",
  dark: "system",
};
const LABEL: Record<Choice, string> = {
  system: "Auto",
  light: "Light",
  dark: "Dark",
};

/** Runs before first paint (inline in <head>) so a saved dark choice never flashes light. */
export const themeScript = `try{var t=localStorage.getItem("${KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

function apply(c: Choice) {
  const el = document.documentElement;
  if (c === "system") delete el.dataset.theme;
  else el.dataset.theme = c;
  try {
    if (c === "system") localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, c);
  } catch {
    /* private mode: the choice lasts for this page only */
  }
}

export function ThemeToggle() {
  const [choice, setChoice] = useState<Choice>("system");
  useEffect(() => {
    const t = document.documentElement.dataset.theme;
    // eslint-disable-next-line react-hooks/set-state-in-effect -- read the pre-paint choice once
    if (t === "light" || t === "dark") setChoice(t);
  }, []);
  return (
    <button
      type="button"
      data-testid="theme-toggle"
      aria-label={`Theme: ${LABEL[choice]}. Change theme`}
      onClick={() => {
        const n = NEXT[choice];
        apply(n);
        setChoice(n);
      }}
      className="rounded-full border border-line px-2 py-0.5 text-ink-2 hover:text-ink"
    >
      {choice === "dark" ? "☾" : choice === "light" ? "☀" : "◐"} {LABEL[choice]}
    </button>
  );
}
