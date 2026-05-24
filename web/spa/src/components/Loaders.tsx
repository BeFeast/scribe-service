import React from "react";

// Loaders: full-screen AuthGate + inline / block / skeleton building blocks.
//
// The gate mounts above the shell while bootstrap runs, while we're waiting
// for sign-in, or while initial data is in flight. Phase wording is mapped
// to what's actually happening in `useAuth`, so a curious user looking at
// the status line learns the order of operations.

export type AuthGatePhase =
	| "config"
	| "clerk"
	| "signin"
	| "workspace"
	| "error"
	| "ready";

const TITLE_BY_PHASE: Record<Exclude<AuthGatePhase, "ready">, string> = {
	config: "Connecting to Scribe",
	clerk: "Authenticating",
	signin: "Sign in to Scribe",
	workspace: "Loading your workspace",
	error: "Couldn't reach Scribe",
};

const STATUS_BY_PHASE: Partial<Record<AuthGatePhase, string>> = {
	config: "Checking access…",
	clerk: "Loading Clerk…",
	workspace: "Fetching transcripts, queue, and metrics…",
};

const STEP_ORDER: AuthGatePhase[] = ["config", "clerk", "signin", "workspace"];
const STEP_LABEL: Record<AuthGatePhase, string> = {
	config: "config",
	clerk: "auth",
	signin: "session",
	workspace: "workspace",
	error: "error",
	ready: "ready",
};

type AuthGateProps = {
	phase: AuthGatePhase;
	error?: string | null;
	onSignIn?: () => void;
	onRetry?: () => void;
	onContinueOffline?: () => void;
};

export function AuthGate({
	phase,
	error,
	onSignIn,
	onRetry,
	onContinueOffline,
}: AuthGateProps) {
	if (phase === "ready") {
		return null;
	}

	const currentIndex = STEP_ORDER.indexOf(phase);
	const stepState = (idx: number): "done" | "active" | "pending" => {
		if (currentIndex === -1) return "pending";
		if (idx < currentIndex) return "done";
		if (idx === currentIndex) return "active";
		return "pending";
	};

	return (
		<output className="auth-gate" aria-live="polite">
			<div className="auth-gate-card">
				<div className="auth-gate-mark">
					<svg
						viewBox="0 0 24 24"
						width="28"
						height="28"
						fill="none"
						role="img"
						aria-label="Scribe"
						stroke="currentColor"
						strokeWidth="1.8"
						strokeLinecap="round"
					>
						<line x1="4" y1="9" x2="4" y2="15" />
						<line x1="7.5" y1="6" x2="7.5" y2="18" />
						<line x1="11" y1="8.5" x2="11" y2="15.5" />
						<line x1="14" y1="8" x2="20" y2="8" />
						<line x1="14" y1="12" x2="20" y2="12" />
						<line x1="14" y1="16" x2="18" y2="16" />
					</svg>
				</div>

				<h1 className="auth-gate-title">{TITLE_BY_PHASE[phase]}</h1>

				{phase === "signin" ? (
					<>
						<p className="auth-gate-help">
							Scribe is invite-only. Sign in with the email your operator added
							to the allowlist.
						</p>
						<div className="auth-gate-signin-actions">
							<button type="button" className="btn primary" onClick={onSignIn}>
								Sign in with Clerk
							</button>
							<a href="/feed.xml" className="btn ghost">
								Read-only RSS
							</a>
						</div>
					</>
				) : phase === "error" ? (
					<>
						<p className="auth-gate-help auth-gate-help-error">
							{error ??
								"Couldn't load auth config. The service may be restarting."}
						</p>
						<div className="auth-gate-signin-actions">
							<button type="button" className="btn primary" onClick={onRetry}>
								Retry
							</button>
							<button
								type="button"
								className="btn ghost"
								onClick={onContinueOffline}
							>
								Continue offline
							</button>
						</div>
					</>
				) : (
					<>
						<div className="auth-gate-progress" aria-hidden="true" />
						<p className="auth-gate-status">
							<span className="spinner" aria-hidden="true" />
							<span>{STATUS_BY_PHASE[phase] ?? "Working…"}</span>
						</p>
						<div className="auth-gate-steps" aria-hidden="true">
							{STEP_ORDER.map((step, idx) => (
								<span key={step} className={`auth-gate-step ${stepState(idx)}`}>
									<span className="step-dot" />
									<span>{STEP_LABEL[step]}</span>
								</span>
							))}
						</div>
					</>
				)}
			</div>

			<div className="auth-gate-footer">
				<span>scribe</span>
				<span className="sep">·</span>
				<span>
					{phase === "signin" || phase === "error"
						? "no data sent until you sign in"
						: "establishing secure session…"}
				</span>
			</div>
		</output>
	);
}

// Component loaders

type LoaderProps = { label?: string; sub?: string };

export function Loader({ label, sub }: LoaderProps) {
	return (
		<output className="loader-block" aria-live="polite">
			<span className="spinner" aria-hidden="true" />
			<span>{label ?? "Loading…"}</span>
			{sub !== undefined ? <span className="loader-sub">{sub}</span> : null}
		</output>
	);
}

export function LoaderInline({ label }: { label?: string }) {
	return (
		<output className="loader" aria-live="polite">
			<span className="spinner" aria-hidden="true" />
			<span>{label ?? "Loading…"}</span>
		</output>
	);
}

type SkeletonKind = "rows" | "transcript";

export function Skeleton({
	kind = "rows",
	rows = 6,
}: {
	kind?: SkeletonKind;
	rows?: number;
}) {
	if (kind === "rows") {
		return (
			<div aria-hidden="true">
				{Array.from({ length: rows }, (_, idx) => `skeleton-row-${idx}`).map(
					(rowId) => (
						<div className="skel-row" key={rowId}>
							<span className="skel skel-line s" />
							<span className="skel skel-line l" />
							<span className="skel skel-line m" />
							<span className="skel skel-line s" />
							<span className="skel skel-line s" />
						</div>
					),
				)}
			</div>
		);
	}
	return (
		<div aria-hidden="true">
			<span className="skel skel-title" />
			<span className="skel skel-line l" />
			<span className="skel skel-line l" />
			<span className="skel skel-line m" />
			<span className="skel skel-line s" style={{ marginTop: 24 }} />
			<span className="skel skel-line l" />
			<span className="skel skel-line l" />
			<span className="skel skel-line l" />
			<span className="skel skel-line m" />
		</div>
	);
}
