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

/* Explicit light/dark override on top of the OS-driven default in
 * tokens.css. null means "follow system" — the third state in the toggle
 * cycle, not just a two-way switch. */
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
    setTheme((current) => (current === null ? "light" : current === "light" ? "dark" : null));
  }

  return { theme, cycleTheme };
}
