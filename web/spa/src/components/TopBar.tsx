import type { ScribeTheme } from "../hooks/useTweaks";
import { CMDK_OPEN_EVENT } from "../constants";

type TopBarProps = {
  theme: ScribeTheme;
  onThemeChange: (theme: ScribeTheme) => void;
};

function publishCmdkOpen(): void {
  document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT));
}

export function TopBar({ theme, onThemeChange }: TopBarProps) {
  const nextTheme: ScribeTheme = theme === "light" ? "dark" : "light";

  return (
    <header className="topbar">
      <a className="brand" href="#/library" aria-label="Scribe library">
        <span className="brand-mark" aria-hidden="true">
          S
        </span>
        <span className="brand-copy">
          <strong>Scribe</strong>
          <span>video notes</span>
        </span>
      </a>
      <button type="button" className="cmdk-button" onClick={publishCmdkOpen} aria-label="Open command palette">
        <span>Search or jump</span>
        <kbd>⌘K</kbd>
      </button>
      <nav className="topbar-actions" aria-label="Global">
        <button
          type="button"
          className="icon-button"
          onClick={() => onThemeChange(nextTheme)}
          aria-label={`Switch to ${nextTheme} theme`}
          aria-pressed={theme === "dark"}
        >
          {theme === "light" ? "☾" : "☀"}
        </button>
        <a className="icon-button" href="/feed.xml" aria-label="RSS feed">
          RSS
        </a>
      </nav>
    </header>
  );
}
