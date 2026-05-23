import { CMDK_OPEN_EVENT } from "../constants";
import { useAuth } from "../hooks/useAuth";

function publishCmdkOpen(): void {
	document.dispatchEvent(new CustomEvent(CMDK_OPEN_EVENT));
}

export function TopBar() {
	const auth = useAuth();

	return (
		<header className="topbar">
			<a className="brand" href="#/library" aria-label="Scribe library">
				<span className="brand-mark" aria-hidden="true">
					<svg viewBox="0 0 32 32" focusable="false" aria-hidden="true">
						<path d="M7 6.5h18v3H10v5h12v3H10v5h15v3H7z" />
						<path d="M13 12.5h12v3H13z" />
					</svg>
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
					<>
						<button
							type="button"
							className="auth-button"
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
							className="auth-button ghost"
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
				{auth.accessStatus === "Signed in" ? (
					<button
						type="button"
						className="auth-button ghost"
						onClick={auth.signOut}
					>
						Sign out
					</button>
				) : null}
				<a className="icon-button" href="/feed.xml" aria-label="RSS feed">
					RSS
				</a>
			</nav>
		</header>
	);
}
