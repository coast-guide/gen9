"use client";

import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";

const OPTIONS = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];

const noop = () => () => {};

/** Native radios drawn as segments, as Search's modes are: one Tab stop, and the arrow keys move
 *  and choose (buttons with role="radio" were each a Tab stop and ignored arrows, manual-e2e.md,
 *  P3-D1). */
export function Appearance() {
  const { theme, setTheme } = useTheme();
  // The stored theme is only known in the browser: render neutral until hydrated
  const hydrated = useSyncExternalStore(noop, () => true, () => false);
  return (
    <fieldset className="flex flex-wrap gap-1 rounded-[1.375rem] bg-muted p-1">
      <legend className="sr-only">Theme</legend>
      {OPTIONS.map((option) => (
        <label key={option.value} className="relative flex-1">
          <input
            type="radio"
            name="theme"
            value={option.value}
            checked={hydrated && (theme ?? "system") === option.value}
            onChange={() => setTheme(option.value)}
            className="peer sr-only"
          />
          <span
            className={
              "flex min-h-9 cursor-pointer items-center justify-center rounded-full px-4 text-sm font-medium text-muted-foreground transition-colors pointer-coarse:min-h-10 " +
              "hover:text-foreground peer-checked:bg-card peer-checked:text-foreground peer-checked:shadow-sm " +
              "peer-focus-visible:ring-2 peer-focus-visible:ring-ring"
            }
          >
            {option.label}
          </span>
        </label>
      ))}
    </fieldset>
  );
}
