import { CMDK_OPEN_EVENT } from "../constants";
import { useAuth } from "../hooks/useAuth";
import type { ScribeTheme } from "../hooks/useTweaks";

type TopBarProps = {
	theme: ScribeTheme;
	onThemeChange: (theme: ScribeTheme) => void;
};

function publishCmdkOpen(): void {
	document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT));
}

export function TopBar({ theme, onThemeChange }: TopBarProps) {
	const auth = useAuth();
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
			<button
				type="button"
				className="cmdk-button"
				onClick={publishCmdkOpen}
				aria-label="Open command palette"
			>
				<span>Search or jump</span>
				<kbd>⌘K</kbd>
			</button>
			<nav className="topbar-actions" aria-label="Global">
				<span className="topbar-access-status">{auth.accessStatus}</span>
				{!auth.canWrite && auth.clerkConfigured ? (
					<button
						type="button"
						className="auth-button"
						onClick={auth.signIn}
						disabled={!auth.clerkReady}
						title={auth.clerkReady ? "Sign in with Google" : "Sign in loading"}
					>
						Sign in
					</button>
				) : null}
				{auth.accessStatus === "Signed in" ? (
					<button
						type="button"
						className="auth-button ghost"
						onClick={auth.signOut}
					>
						Sign out
					</button>
				) : null}
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
