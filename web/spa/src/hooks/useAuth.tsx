import React from "react";

type AuthConfig = {
	clerk_publishable_key: string;
	clerk_frontend_api: string;
	trusted_network: boolean;
};

type ClerkSession = {
	getToken: () => Promise<string | null>;
};

type ClerkLoadOptions = {
	ui?: {
		ClerkUI?: unknown;
	};
};

type ClerkRuntime = {
	load: (options?: ClerkLoadOptions) => Promise<void>;
	openSignIn: () => void;
	signOut: () => Promise<void>;
	addListener?: (
		listener: (resources: { session: ClerkSession | null }) => void,
	) => () => void;
	session: ClerkSession | null;
};

declare global {
	interface Window {
		Clerk?: ClerkRuntime;
		__internal_ClerkUICtor?: unknown;
	}
}

type AccessStatus = "Trusted network" | "Signed in" | "Read-only";

type AuthContextValue = {
	accessStatus: AccessStatus;
	canWrite: boolean;
	clerkReady: boolean;
	clerkConfigured: boolean;
	trustedNetwork: boolean;
	signIn: () => void;
	signOut: () => Promise<void>;
	protectedFetch: (
		input: RequestInfo | URL,
		init?: RequestInit,
	) => Promise<Response>;
};

const AuthContext = React.createContext<AuthContextValue | null>(null);

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
	await appendScript(`https://${host}/npm/@clerk/ui@1/dist/ui.browser.js`);
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
	await window.Clerk.load({
		ui: { ClerkUI: window.__internal_ClerkUICtor },
	});
}

function mergeAuthHeader(init: RequestInit, token: string): RequestInit {
	const headers = new Headers(init.headers);
	headers.set("Authorization", `Bearer ${token}`);
	return { ...init, headers };
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
	const [config, setConfig] = React.useState<AuthConfig | null>(null);
	const [clerkReady, setClerkReady] = React.useState(false);
	const [signedIn, setSignedIn] = React.useState(false);
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
			setClerkReady(true);
			syncSignedIn();
			unsubscribe = window.Clerk?.addListener?.(({ session }) => {
				setSignedIn(Boolean(session));
			});
		}

		loadAuth().catch((error) => {
			if (!abort.signal.aborted) {
				console.warn(error);
				setConfig({
					clerk_publishable_key: "",
					clerk_frontend_api: "",
					trusted_network: false,
				});
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

	const signIn = React.useCallback(() => {
		window.Clerk?.openSignIn();
		for (const delayMs of [500, 1500, 3000, 6000]) {
			window.setTimeout(syncSignedIn, delayMs);
		}
	}, [syncSignedIn]);

	const signOut = React.useCallback(async () => {
		await window.Clerk?.signOut();
		setSignedIn(false);
	}, []);

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
			trustedNetwork,
			signIn,
			signOut,
			protectedFetch,
		}),
		[
			accessStatus,
			canWrite,
			clerkReady,
			clerkConfigured,
			trustedNetwork,
			signIn,
			signOut,
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
