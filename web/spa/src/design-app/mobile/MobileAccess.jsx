// Mobile Access sub-view — Wave 2e / Issue #280.
//
// Literal port of viewAccess() (`Scribe iOS.html` ~lines 1196-1218) wired
// to the real `runtime.users` adapter (no mock data). Invitation flow
// surfaces an inline notice instead of a prototype toast — Clerk owns the
// real invite path in this deployment.
//
// Source mapping (Scribe iOS.html → this file):
//   ~1196 viewAccess()      → <MobileAccess />
//   ~1198 .sec-label header → header line with member count + Clerk count
//   ~1199 .glist of .urow   → <ul> of .urow rows (one per user)
//   ~1209 .role pill        → role pill (.role.admin / .role.user)
//   ~1213 Invite member btn → onInvite() → inline notice (Clerk-managed)
//
// No new top-level route is added — this is rendered by <MobileSettings />
// when its local sub-view state is "access".
// The API access rows are production-only wiring for #453: trusted-LAN users
// can optionally establish a Clerk session and mint the existing user token.

import React from "react";
import { useAuth } from "../../hooks/useAuth";
import { IconCopy, IconExternal, IconPlus, IconRefresh } from "../icons.jsx";
import {
	OPTIONAL_CLERK_UNAVAILABLE,
	canGenerateExtensionToken,
	mintExtensionToken,
	shouldOfferTrustedClerkSignIn,
} from "../settings.jsx";

function initials(name) {
	if (!name) return "?";
	const trimmed = String(name).trim();
	return trimmed ? trimmed[0].toUpperCase() : "?";
}

export function MobileAccess({ users = [], notice = null, onBack, onInvite }) {
	const auth = useAuth();
	const total = users.length;
	const clerkCount = users.filter((u) => u.source === "clerk").length;
	const [tokenState, setTokenState] = React.useState({
		pending: false,
		error: null,
		token: null,
		copied: false,
	});
	const summary =
		total > 0
			? `${total} member${total === 1 ? "" : "s"} · ${clerkCount} via Clerk`
			: "No members loaded";

	async function generateToken() {
		if (!canGenerateExtensionToken(auth, tokenState.pending)) return;
		setTokenState({ pending: true, error: null, token: null, copied: false });
		try {
			const body = await mintExtensionToken(auth);
			setTokenState({
				pending: false,
				error: null,
				token: body?.token ?? "",
				copied: false,
			});
		} catch (error) {
			setTokenState({
				pending: false,
				error:
					error instanceof Error ? error.message : "Token generation failed",
				token: null,
				copied: false,
			});
		}
	}

	async function copyToken() {
		if (!tokenState.token) return;
		try {
			await navigator.clipboard.writeText(tokenState.token);
			setTokenState((state) => ({ ...state, copied: true, error: null }));
		} catch (error) {
			setTokenState((state) => ({
				...state,
				copied: false,
				error: error instanceof Error ? error.message : "Token copy failed",
			}));
		}
	}

	return (
		<>
			<div className="ms-subhead">
				<button
					type="button"
					className="ms-back"
					onClick={onBack}
					aria-label="Back to settings"
				>
					<svg
						aria-hidden="true"
						focusable="false"
						width="13"
						height="20"
						viewBox="0 0 13 20"
						fill="none"
						stroke="currentColor"
						strokeWidth="2.5"
						strokeLinecap="round"
						strokeLinejoin="round"
					>
						<path d="M11 1.5L2 10l9 8.5" />
					</svg>
					<span>Settings</span>
				</button>
				<h2 className="ms-subtitle">Access</h2>
				<span className="ms-back-spacer" />
			</div>
			<div className="sec-label">{summary}</div>
			<div className="glist">
				{users.length === 0 ? (
					<div className="urow urow-empty">
						<div className="u-main">
							<div className="u-name">No members yet</div>
							<div className="u-mail">
								Members appear here once they sign in via Clerk.
							</div>
						</div>
					</div>
				) : (
					users.map((user) => (
						<div
							key={user.id ?? user.email}
							className={user.is_me ? "urow me" : "urow"}
						>
							<span className="u-av">{initials(user.name)}</span>
							<div className="u-main">
								<div className="u-name">
									{user.name}
									{user.is_me ? <span className="u-you"> · you</span> : null}
								</div>
								<div className="u-mail">{user.email}</div>
							</div>
							<span
								className={`role ${user.role === "admin" ? "admin" : "user"}`}
							>
								{user.role}
							</span>
						</div>
					))
				)}
			</div>
			<div className="sec-label">API access</div>
			<div className="glist">
				<div className="urow">
					<div className="u-main">
						<div className="u-name">Chrome extension token</div>
						<div className="u-mail">
							{auth.signedIn
								? "Generate a user-scoped token. It is shown once."
								: "A Clerk session is required to mint a portable credential."}
						</div>
					</div>
					{shouldOfferTrustedClerkSignIn(auth) ? (
						<button
							type="button"
							className="btn primary"
							disabled={auth.authRedirectInFlight}
							onClick={() => void auth.signIn()}
						>
							<IconExternal size={13} /> Sign in with Clerk
						</button>
					) : null}
				</div>
				<div className="urow">
					<div className="u-main">
						<div className="u-name">
							{tokenState.token ?? "Token is shown once after generation"}
						</div>
						<div className="u-mail">Current access: {auth.accessStatus}</div>
					</div>
					<button
						type="button"
						className="btn primary"
						disabled={!canGenerateExtensionToken(auth, tokenState.pending)}
						onClick={() => void generateToken()}
					>
						<IconRefresh size={13} />
						{tokenState.pending ? "Generating…" : "Generate token"}
					</button>
					<button
						type="button"
						className="btn"
						disabled={!tokenState.token}
						onClick={() => void copyToken()}
					>
						<IconCopy size={13} /> {tokenState.copied ? "Copied" : "Copy"}
					</button>
				</div>
				{auth.authBlockedMessage ? (
					<div className="ms-inline-error" role="alert">
						{OPTIONAL_CLERK_UNAVAILABLE}
					</div>
				) : null}
				{tokenState.error ? (
					<div className="ms-inline-error" role="alert">
						{tokenState.error}
					</div>
				) : null}
			</div>
			<div className="ms-bigbtn-wrap">
				<button type="button" className="ms-bigbtn" onClick={onInvite}>
					<IconPlus size={16} />
					<span>Invite member</span>
				</button>
				{notice ? (
					<div className="ms-inline-notice" aria-live="polite">
						{notice}
					</div>
				) : null}
			</div>
		</>
	);
}
