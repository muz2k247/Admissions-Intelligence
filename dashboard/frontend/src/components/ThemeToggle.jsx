import useTheme from "../hooks/useTheme";
import { MoonIcon, SunIcon } from "./Icons";

const LABELS = {
  null: "System theme",
  light: "Light theme",
  dark: "Dark theme",
};

export default function ThemeToggle() {
  const { theme, cycleTheme } = useTheme();
  const label = LABELS[theme];

  return (
    <button
      type="button"
      className="button button--icon"
      onClick={cycleTheme}
      aria-label={`${label}. Click to switch theme.`}
      title={label}
    >
      {theme === "dark" ? <MoonIcon /> : theme === "light" ? <SunIcon /> : <SunIcon style={{ opacity: 0.5 }} />}
    </button>
  );
}
