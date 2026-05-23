import React from "react";

type AuthConfig = {
	clerk_publishable_key: string;
	clerk_frontend_api: string;
	trusted_network: boolean;
};

type ClerkSession = {
	getToken: () => Promise<string | null>;
};

type ClerkRuntime = {
	load: () => Promise<void>;
	redirectToSignIn: (options?: ClerkRedirectOptions) => Promise<unknown>;
	redirectToSignUp: (options?: ClerkRedirectOptions) => Promise<unknown>;
	signOut: () => Promise<void>;
	addListener?: (
		listener: (resources: { session: ClerkSession | null }) => void,
	) => () => void;
	session: ClerkSession | null;
};

declare global {
	interface Window {
		Clerk?: ClerkRuntime;
	}
}

type AccessStatus = "Trusted network" | "Signed in" | "Read-only";

type AuthContextValue = {
	accessStatus: AccessStatus;
	canWrite: boolean;
	clerkReady: boolean;
	clerkConfigured: boolean;
	authBlockedMessage: string | null;
	authRedirectInFlight: boolean;
	trustedNetwork: boolean;
	signIn: () => Promise<void>;
	signUp: () => Promise<void>;
	signOut: () => Promise<void>;
	maybeAutoSignIn: () => boolean;
	protectedFetch: (
		input: RequestInfo | URL,
		init?: RequestInit,
	) => Promise<Response>;
};

const AUTO_SIGN_IN_KEY = "scribe.signInAttempted";
const AUTH_REDIRECT_INTENT_KEY = "scribe.auth.redirect-intent";
const AUTH_REDIRECT_INTENT_TTL_MS = 90_000;

const AuthContext = React.createContext<AuthContextValue | null>(null);

type ClerkRedirectOptions = {
	redirectUrl: string;
	signInForceRedirectUrl?: string;
	signInFallbackRedirectUrl?: string;
	signUpForceRedirectUrl?: string;
	signUpFallbackRedirectUrl?: string;
};

function clerkFrontendHost(config: AuthConfig): string {
	if (config.clerk_frontend_api.trim()) {
		const endpoint = config.clerk_frontend_api.includes("://")
			? config.clerk_frontend_api
			: `https://${config.clerk_frontend_api}`;
		return new URL(endpoint).host;
	}

	const encodedHost = config.clerk_publishable_key.split("_")[2];
	if (!encodedHost) {
		throw new Error("Clerk publishable key is malformed");
	}
	return window.atob(encodedHost).replace(/\$$/, "");
}

function appendScript(
	src: string,
	configure?: (script: HTMLScriptElement) => void,
): Promise<void> {
	const existing = document.querySelector(`script[src="${src}"]`);
	if (existing) {
		return Promise.resolve();
	}
	return new Promise((resolve, reject) => {
		const script = document.createElement("script");
		script.async = true;
		script.crossOrigin = "anonymous";
		script.src = src;
		configure?.(script);
		script.addEventListener("load", () => resolve());
		script.addEventListener("error", () => reject(new Error(`${src} failed`)));
		document.head.append(script);
	});
}

async function loadClerk(config: AuthConfig): Promise<void> {
	const host = clerkFrontendHost(config);
	if (window.Clerk === undefined) {
		await appendScript(
			`https://${host}/npm/@clerk/clerk-js@6/dist/clerk.browser.js`,
			(script) => {
				script.dataset.clerkPublishableKey = config.clerk_publishable_key;
			},
		);
	}
	if (window.Clerk === undefined) {
		throw new Error("Clerk browser runtime failed to load");
	}
	await window.Clerk.load();
}

function mergeAuthHeader(init: RequestInit, token: string): RequestInit {
	const headers = new Headers(init.headers);
	headers.set("Authorization", `Bearer ${token}`);
	return { ...init, headers };
}

function currentAppUrl(): string {
	return `${window.location.origin}${window.location.pathname}${window.location.search}${window.location.hash}`;
}

function writeRedirectIntent(mode: "sign-in" | "sign-up"): void {
	try {
		window.sessionStorage.setItem(
			AUTH_REDIRECT_INTENT_KEY,
			JSON.stringify({ mode, startedAt: Date.now() }),
		);
	} catch {
		// Storage may be blocked; the explicit user click still starts redirect.
	}
}

function hasFreshRedirectIntent(): boolean {
	try {
		const raw = window.sessionStorage.getItem(AUTH_REDIRECT_INTENT_KEY);
		if (raw === null) {
			return false;
		}
		const parsed = JSON.parse(raw) as { startedAt?: number };
		if (
			typeof parsed.startedAt === "number" &&
			Date.now() - parsed.startedAt <= AUTH_REDIRECT_INTENT_TTL_MS
		) {
			return true;
		}
		window.sessionStorage.removeItem(AUTH_REDIRECT_INTENT_KEY);
		return false;
	} catch {
		return false;
	}
}

function clearRedirectIntent(): void {
	try {
		window.sessionStorage.removeItem(AUTH_REDIRECT_INTENT_KEY);
	} catch {
		// Ignore cleanup failures.
	}
}

function authBlockedMessage(error: unknown): string {
	const detail = error instanceof Error ? error.message : "unknown Clerk error";
	return `Authentication resources were blocked or failed to load. Disable the blocker for this site, then retry. Detail: ${detail}`;
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
	const [config, setConfig] = React.useState<AuthConfig | null>(null);
	const [clerkReady, setClerkReady] = React.useState(false);
	const [signedIn, setSignedIn] = React.useState(false);
	const [authBlocked, setAuthBlocked] = React.useState<string | null>(null);
	const [authRedirectInFlight, setAuthRedirectInFlight] = React.useState(() =>
		hasFreshRedirectIntent(),
	);
	const syncSignedIn = React.useCallback(() => {
		setSignedIn(Boolean(window.Clerk?.session));
	}, []);

	React.useEffect(() => {
		const abort = new AbortController();
		let unsubscribe: (() => void) | undefined;

		async function loadAuth() {
			const response = await fetch("/api/auth/config", {
				signal: abort.signal,
			});
			if (!response.ok) {
				throw new Error("auth config unavailable");
			}
			const body = (await response.json()) as AuthConfig;
			setConfig(body);
			if (body.trusted_network || !body.clerk_publishable_key) {
				return;
			}
			await loadClerk(body);
			setAuthBlocked(null);
			setClerkReady(true);
			syncSignedIn();
			if (window.Clerk?.session) {
				clearRedirectIntent();
				setAuthRedirectInFlight(false);
			}
			unsubscribe = window.Clerk?.addListener?.(({ session }) => {
				setSignedIn(Boolean(session));
				if (session) {
					clearRedirectIntent();
					setAuthRedirectInFlight(false);
				}
			});
		}

		loadAuth().catch((error) => {
			if (!abort.signal.aborted) {
				console.warn(error);
				setAuthBlocked(authBlockedMessage(error));
				setConfig(
					(current) =>
						current ?? {
							clerk_publishable_key: "",
							clerk_frontend_api: "",
							trusted_network: false,
						},
				);
			}
		});
		return () => {
			abort.abort();
			unsubscribe?.();
		};
	}, [syncSignedIn]);

	const trustedNetwork = config?.trusted_network === true;
	const clerkConfigured = Boolean(config?.clerk_publishable_key);
	const canWrite = trustedNetwork || signedIn;
	const accessStatus: AccessStatus = trustedNetwork
		? "Trusted network"
		: signedIn
			? "Signed in"
			: "Read-only";

	const startRedirect = React.useCallback(
		async (mode: "sign-in" | "sign-up") => {
			if (!window.Clerk || authRedirectInFlight) {
				return;
			}
			setAuthBlocked(null);
			setAuthRedirectInFlight(true);
			writeRedirectIntent(mode);
			const redirectUrl = currentAppUrl();
			try {
				if (mode === "sign-in") {
					await window.Clerk.redirectToSignIn({
						redirectUrl,
						signInForceRedirectUrl: redirectUrl,
						signInFallbackRedirectUrl: redirectUrl,
						signUpFallbackRedirectUrl: redirectUrl,
					});
				} else {
					await window.Clerk.redirectToSignUp({
						redirectUrl,
						signUpForceRedirectUrl: redirectUrl,
						signUpFallbackRedirectUrl: redirectUrl,
						signInFallbackRedirectUrl: redirectUrl,
					});
				}
				setAuthBlocked(
					"The browser did not leave for Clerk sign-in. Check extension settings, then retry.",
				);
			} catch (error) {
				setAuthBlocked(authBlockedMessage(error));
			} finally {
				clearRedirectIntent();
				setAuthRedirectInFlight(false);
			}
		},
		[authRedirectInFlight],
	);

	const signIn = React.useCallback(
		() => startRedirect("sign-in"),
		[startRedirect],
	);
	const signUp = React.useCallback(
		() => startRedirect("sign-up"),
		[startRedirect],
	);

	const signOut = React.useCallback(async () => {
		await window.Clerk?.signOut();
		setSignedIn(false);
	}, []);

	// Record one auth miss per browser session, but do not reopen Clerk
	// automatically. The visible CTA keeps cancelled/blocked flows stable.
	const maybeAutoSignIn = React.useCallback((): boolean => {
		if (trustedNetwork || signedIn || !clerkConfigured || !clerkReady) {
			return false;
		}
		try {
			if (window.sessionStorage.getItem(AUTO_SIGN_IN_KEY) === "1") {
				return false;
			}
			window.sessionStorage.setItem(AUTO_SIGN_IN_KEY, "1");
		} catch {
			return false;
		}
		return true;
	}, [trustedNetwork, signedIn, clerkConfigured, clerkReady]);

	const protectedFetch = React.useCallback(
		async (input: RequestInfo | URL, init: RequestInit = {}) => {
			const token = await window.Clerk?.session?.getToken();
			return fetch(input, token ? mergeAuthHeader(init, token) : init);
		},
		[],
	);

	const value = React.useMemo(
		() => ({
			accessStatus,
			canWrite,
			clerkReady,
			clerkConfigured,
			authBlockedMessage: authBlocked,
			authRedirectInFlight,
			trustedNetwork,
			signIn,
			signUp,
			signOut,
			maybeAutoSignIn,
			protectedFetch,
		}),
		[
			accessStatus,
			canWrite,
			clerkReady,
			clerkConfigured,
			authBlocked,
			authRedirectInFlight,
			trustedNetwork,
			signIn,
			signUp,
			signOut,
			maybeAutoSignIn,
			protectedFetch,
		],
	);

	return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
	const value = React.useContext(AuthContext);
	if (value === null) {
		throw new Error("useAuth must be used inside AuthProvider");
	}
	return value;
}
