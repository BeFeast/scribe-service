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

type AccessStatus =
	| "Trusted network"
	| "Signed in"
	| "Read-only"
	| "Sign in required"
	| "Unauthorized";

type AuthContextValue = {
	accessStatus: AccessStatus;
	signedIn: boolean;
	canWrite: boolean;
	clerkReady: boolean;
	clerkConfigured: boolean;
	authBlockedMessage: string | null;
	authRedirectInFlight: boolean;
	authRequired: boolean;
	trustedNetwork: boolean;
	signIn: () => Promise<void>;
	signUp: () => Promise<void>;
	signOut: () => Promise<void>;
	retryAuth: () => void;
	maybeAutoSignIn: () => boolean;
	protectedFetch: (
		input: RequestInfo | URL,
		init?: RequestInit,
	) => Promise<Response>;
};

const AUTO_SIGN_IN_KEY = "scribe.signInAttempted";
const AUTH_REDIRECT_INTENT_KEY = "scribe.auth.redirect-intent";
const AUTH_REDIRECT_INTENT_TTL_MS = 90_000;
const REDIRECT_NAVIGATION_GRACE_MS = 1_200;

const AuthContext = React.createContext<AuthContextValue | null>(null);

type ClerkRedirectOptions = {
	redirectUrl: string;
	signInForceRedirectUrl?: string;
	signInFallbackRedirectUrl?: string;
	signUpForceRedirectUrl?: string;
	signUpFallbackRedirectUrl?: string;
};

export function clerkRedirectOptions(
	mode: "sign-in" | "sign-up",
	redirectUrl: string,
): ClerkRedirectOptions {
	return mode === "sign-in"
		? {
				redirectUrl,
				signInForceRedirectUrl: redirectUrl,
				signInFallbackRedirectUrl: redirectUrl,
				signUpForceRedirectUrl: redirectUrl,
				signUpFallbackRedirectUrl: redirectUrl,
			}
		: {
				redirectUrl,
				signInForceRedirectUrl: redirectUrl,
				signInFallbackRedirectUrl: redirectUrl,
				signUpForceRedirectUrl: redirectUrl,
				signUpFallbackRedirectUrl: redirectUrl,
			};
}

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

export function parseFreshRedirectIntent(
	raw: string | null,
	now: number,
): { startedAt: number } | null {
	try {
		if (raw === null) {
			return null;
		}
		const parsed = JSON.parse(raw) as { startedAt?: number };
		if (
			typeof parsed.startedAt === "number" &&
			now - parsed.startedAt <= AUTH_REDIRECT_INTENT_TTL_MS
		) {
			return { startedAt: parsed.startedAt };
		}
		return null;
	} catch {
		return null;
	}
}

function readRedirectIntent(): { startedAt: number } | null {
	try {
		const raw = window.sessionStorage.getItem(AUTH_REDIRECT_INTENT_KEY);
		const intent = parseFreshRedirectIntent(raw, Date.now());
		if (raw !== null && intent === null) {
			window.sessionStorage.removeItem(AUTH_REDIRECT_INTENT_KEY);
		}
		return intent;
	} catch {
		return null;
	}
}

function hasFreshRedirectIntent(): boolean {
	return readRedirectIntent() !== null;
}

function redirectIntentExpiresInMs(): number | null {
	const intent = readRedirectIntent();
	if (intent === null) {
		return null;
	}
	return Math.max(
		0,
		AUTH_REDIRECT_INTENT_TTL_MS - (Date.now() - intent.startedAt),
	);
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

export function shouldRequireSignIn({
	trustedNetwork,
	signedIn,
	clerkConfigured,
	authConfigLoaded = true,
}: {
	trustedNetwork: boolean;
	signedIn: boolean;
	clerkConfigured: boolean;
	authConfigLoaded?: boolean;
}): boolean {
	return !trustedNetwork && !signedIn && (!authConfigLoaded || clerkConfigured);
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
	const [config, setConfig] = React.useState<AuthConfig | null>(null);
	const [clerkReady, setClerkReady] = React.useState(false);
	const [signedIn, setSignedIn] = React.useState(false);
	const [unauthorized, setUnauthorized] = React.useState(false);
	const [authRequired, setAuthRequired] = React.useState(false);
	const [authBlocked, setAuthBlocked] = React.useState<string | null>(null);
	const [authReloadKey, setAuthReloadKey] = React.useState(0);
	const [authRedirectInFlight, setAuthRedirectInFlight] = React.useState(() =>
		hasFreshRedirectIntent(),
	);
	const navigationStartedRef = React.useRef(false);
	const syncSignedIn = React.useCallback(() => {
		setSignedIn(Boolean(window.Clerk?.session));
		setUnauthorized(false);
		if (window.Clerk?.session) {
			setAuthRequired(false);
		}
	}, []);

	React.useEffect(() => {
		const markNavigationStarted = () => {
			navigationStartedRef.current = true;
		};
		const markHiddenNavigation = () => {
			if (document.hidden) {
				markNavigationStarted();
			}
		};
		window.addEventListener("beforeunload", markNavigationStarted);
		window.addEventListener("pagehide", markNavigationStarted);
		document.addEventListener("visibilitychange", markHiddenNavigation);
		return () => {
			window.removeEventListener("beforeunload", markNavigationStarted);
			window.removeEventListener("pagehide", markNavigationStarted);
			document.removeEventListener("visibilitychange", markHiddenNavigation);
		};
	}, []);

	React.useEffect(() => {
		if (!authRedirectInFlight || signedIn) {
			return;
		}
		const expiresInMs = redirectIntentExpiresInMs();
		if (expiresInMs === null) {
			setAuthRedirectInFlight(false);
			return;
		}
		const timeout = window.setTimeout(() => {
			if (!window.Clerk?.session) {
				clearRedirectIntent();
				setAuthRedirectInFlight(false);
			}
		}, expiresInMs);
		return () => {
			window.clearTimeout(timeout);
		};
	}, [authRedirectInFlight, signedIn]);

	React.useEffect(() => {
		void authReloadKey;
		const abort = new AbortController();
		let unsubscribe: (() => void) | undefined;

		async function loadAuth() {
			setAuthBlocked(null);
			const response = await fetch("/api/auth/config", {
				signal: abort.signal,
			});
			if (!response.ok) {
				throw new Error("auth config unavailable");
			}
			const body = (await response.json()) as AuthConfig;
			setConfig(body);
			if (body.trusted_network || !body.clerk_publishable_key) {
				setAuthRequired(false);
				return;
			}
			await loadClerk(body);
			setAuthBlocked(null);
			setClerkReady(true);
			syncSignedIn();
			clearRedirectIntent();
			setAuthRedirectInFlight(false);
			unsubscribe = window.Clerk?.addListener?.(({ session }) => {
				setSignedIn(Boolean(session));
				if (session) {
					setUnauthorized(false);
					setAuthRequired(false);
					clearRedirectIntent();
					setAuthRedirectInFlight(false);
				}
			});
		}

		loadAuth().catch((error) => {
			if (!abort.signal.aborted) {
				console.warn(error);
				setAuthBlocked(authBlockedMessage(error));
				clearRedirectIntent();
				setAuthRedirectInFlight(false);
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
	}, [syncSignedIn, authReloadKey]);

	const trustedNetwork = config?.trusted_network === true;
	const clerkConfigured = Boolean(config?.clerk_publishable_key);
	const canWrite = trustedNetwork || (signedIn && !unauthorized);
	const accessStatus: AccessStatus = trustedNetwork
		? "Trusted network"
		: unauthorized
			? "Unauthorized"
			: signedIn
				? "Signed in"
				: clerkConfigured
					? "Sign in required"
					: "Read-only";

	const startRedirect = React.useCallback(
		async (mode: "sign-in" | "sign-up") => {
			if (!window.Clerk) {
				setAuthRequired(true);
				return;
			}
			if (authRedirectInFlight) {
				return;
			}
			navigationStartedRef.current = false;
			setAuthBlocked(null);
			setAuthRedirectInFlight(true);
			writeRedirectIntent(mode);
			const redirectUrl = currentAppUrl();
			try {
				if (mode === "sign-in") {
					await window.Clerk.redirectToSignIn(
						clerkRedirectOptions(mode, redirectUrl),
					);
				} else {
					await window.Clerk.redirectToSignUp(
						clerkRedirectOptions(mode, redirectUrl),
					);
				}
				await new Promise((resolve) =>
					window.setTimeout(resolve, REDIRECT_NAVIGATION_GRACE_MS),
				);
				if (navigationStartedRef.current || document.hidden) {
					return;
				}
				setAuthBlocked(
					"The browser did not leave for Clerk sign-in. Check extension settings, then retry.",
				);
				clearRedirectIntent();
				setAuthRedirectInFlight(false);
			} catch (error) {
				if (navigationStartedRef.current || document.hidden) {
					return;
				}
				setAuthBlocked(authBlockedMessage(error));
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
		setUnauthorized(false);
		setAuthRequired(false);
	}, []);

	const maybeAutoSignIn = React.useCallback((): boolean => {
		if (
			!shouldRequireSignIn({
				trustedNetwork,
				signedIn,
				clerkConfigured,
				authConfigLoaded: config !== null,
			})
		) {
			return false;
		}
		setAuthRequired(true);
		if (config === null) {
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
	}, [trustedNetwork, signedIn, clerkConfigured, config]);

	const retryAuth = React.useCallback(() => {
		setAuthBlocked(null);
		setAuthReloadKey((key) => key + 1);
	}, []);

	const protectedFetch = React.useCallback(
		async (input: RequestInfo | URL, init: RequestInit = {}) => {
			const token = await window.Clerk?.session?.getToken();
			const response = await fetch(
				input,
				token ? mergeAuthHeader(init, token) : init,
			);
			if (response.status === 403) {
				setUnauthorized(true);
			} else if (response.ok) {
				setUnauthorized(false);
				if (window.Clerk?.session || trustedNetwork) {
					setAuthRequired(false);
				}
			}
			return response;
		},
		[trustedNetwork],
	);

	const value = React.useMemo(
		() => ({
			accessStatus,
			signedIn,
			canWrite,
			clerkReady,
			clerkConfigured,
			authBlockedMessage: authBlocked,
			authRedirectInFlight,
			authRequired,
			trustedNetwork,
			signIn,
			signUp,
			signOut,
			retryAuth,
			maybeAutoSignIn,
			protectedFetch,
		}),
		[
			accessStatus,
			signedIn,
			canWrite,
			clerkReady,
			clerkConfigured,
			authBlocked,
			authRedirectInFlight,
			authRequired,
			trustedNetwork,
			signIn,
			signUp,
			signOut,
			retryAuth,
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
