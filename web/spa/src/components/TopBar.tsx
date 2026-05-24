import { CMDK_OPEN_EVENT } from "../constants";
import { useAuth } from "../hooks/useAuth";
import type { Route } from "../hooks/useRoute";
import { routeLabel } from "../hooks/useRoute";
import { IconRSS, IconSearch } from "./ShellIcons";

type TopBarProps = {
	route: Route;
};

function publishCmdkOpen(): void {
	document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT));
}

export function TopBar({ route }: TopBarProps) {
	const auth = useAuth();
	const screenLabel = routeLabel(route);

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
				aria-label={`Open command palette from ${screenLabel}`}
			>
				<IconSearch size={14} />
				<span>{screenLabel} / Paste URL or search transcripts...</span>
				<span className="kbd">⌘K</span>
			</button>
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
			<a
				className="iconbtn"
				href="/feed.xml"
				title="RSS feed"
				aria-label="RSS feed"
			>
				<IconRSS size={16} />
			</a>
		</header>
	);
}
