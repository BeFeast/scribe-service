import { CMDK_OPEN_EVENT } from "../constants";
import { useAuth } from "../hooks/useAuth";
import type { Tweaks } from "../hooks/useTweaks";
import { IconMoon, IconRSS, IconSearch, IconSun } from "./ShellIcons";

type TopBarProps = {
	tweaks: Tweaks;
	replaceTweaks: (value: Tweaks) => void;
};

function publishCmdkOpen(): void {
	document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT));
}

export function TopBar({ tweaks, replaceTweaks }: TopBarProps) {
	const auth = useAuth();
	const isDark = tweaks.theme === "dark";
	const toggleTheme = () =>
		replaceTweaks({ ...tweaks, theme: isDark ? "light" : "dark" });

	return (
		<header className="topbar">
			<a className="brand" href="#/library" aria-label="Scribe library">
				<span className="brand-mark" aria-hidden="true">
					<svg
						viewBox="0 0 24 24"
						width="14"
						height="14"
						fill="none"
						stroke="currentColor"
						strokeWidth="2"
						strokeLinecap="round"
						aria-hidden="true"
						focusable="false"
					>
						<line x1="4" y1="9" x2="4" y2="15" />
						<line x1="7.5" y1="6" x2="7.5" y2="18" />
						<line x1="11" y1="8.5" x2="11" y2="15.5" />
						<line x1="14" y1="8" x2="20" y2="8" />
						<line x1="14" y1="12" x2="20" y2="12" />
						<line x1="14" y1="16" x2="18" y2="16" />
					</svg>
				</span>
				<span>scribe</span>
			</a>
			<div className="grow" />
			<button
				type="button"
				className="cmdk"
				onClick={publishCmdkOpen}
				aria-label="Open command palette"
			>
				<IconSearch size={14} />
				<span>Paste URL or search transcripts...</span>
				<span className="kbd">⌘K</span>
			</button>
			<button
				type="button"
				className="iconbtn"
				title="Toggle theme"
				aria-label="Toggle theme"
				onClick={toggleTheme}
			>
				{isDark ? <IconSun size={16} /> : <IconMoon size={16} />}
			</button>
			<a
				className="iconbtn"
				href="/feed.xml"
				title="RSS feed"
				aria-label="RSS feed"
			>
				<IconRSS size={16} />
			</a>
			<div className="access-row topbar-access">
				<span className="row-label">{auth.accessStatus}</span>
				<div className="access-facts">
					{!auth.canWrite && auth.clerkConfigured && !auth.signedIn ? (
						<>
							<button
								type="button"
								className="btn"
								onClick={() => void auth.signUp()}
								disabled={!auth.clerkReady || auth.authRedirectInFlight}
								title={
									auth.authBlockedMessage ??
									(auth.clerkReady
										? "Create account with Clerk"
										: "Sign in loading")
								}
							>
								Sign up
							</button>
							<button
								type="button"
								className="btn ghost"
								onClick={() => void auth.signIn()}
								disabled={!auth.clerkReady || auth.authRedirectInFlight}
								title={
									auth.authBlockedMessage ??
									(auth.clerkReady ? "Sign in with Clerk" : "Sign in loading")
								}
							>
								Sign in
							</button>
						</>
					) : null}
					{auth.signedIn ? (
						<button type="button" className="btn ghost" onClick={auth.signOut}>
							Sign out
						</button>
					) : null}
				</div>
			</div>
		</header>
	);
}
