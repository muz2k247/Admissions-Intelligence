import { useEffect, useState } from "react";

const STORAGE_KEY = "admissions-intelligence-theme";

function getStoredTheme() {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : null;
  } catch {
    return null; // localStorage unavailable (private browsing, disabled storage)
  }
}

function getSystemPreference() {
  try {
    return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  } catch {
    return "light";
  }
}

/* Explicit light/dark override on top of the OS-driven default in
 * tokens.css. null means "follow system" — the pre-interaction default
 * only. cycleTheme() is a true light<->dark toggle: once a user has
 * clicked, null is never reachable again by clicking (it previously was,
 * as a third cycle state, which made dark->light take two clicks). */
export default function useTheme() {
  const [theme, setTheme] = useState(getStoredTheme);

  useEffect(() => {
    const root = document.documentElement;
    if (theme) {
      root.setAttribute("data-theme", theme);
    } else {
      root.removeAttribute("data-theme");
    }
    try {
      if (theme) {
        localStorage.setItem(STORAGE_KEY, theme);
      } else {
        localStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      /* ignore storage failures — theme still applies for this session */
    }
  }, [theme]);

  function cycleTheme() {
    setTheme((current) => {
      const effective = current ?? getSystemPreference();
      return effective === "dark" ? "light" : "dark";
    });
  }

  return { theme, cycleTheme };
}
