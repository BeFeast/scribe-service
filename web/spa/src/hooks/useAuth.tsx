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
	openSignIn: () => void;
	signOut: () => Promise<void>;
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
	trustedNetwork: boolean;
	signIn: () => void;
	signOut: () => Promise<void>;
	protectedFetch: (
		input: RequestInfo | URL,
		init?: RequestInit,
	) => Promise<Response>;
};

const AuthContext = React.createContext<AuthContextValue | null>(null);

function loadClerk(config: AuthConfig): Promise<void> {
	if (window.Clerk !== undefined) {
		return window.Clerk.load();
	}

	return new Promise((resolve, reject) => {
		const script = document.createElement("script");
		script.async = true;
		script.crossOrigin = "anonymous";
		script.src =
			"https://cdn.jsdelivr.net/npm/@clerk/clerk-js@latest/dist/clerk.browser.js";
		script.dataset.clerkPublishableKey = config.clerk_publishable_key;
		if (config.clerk_frontend_api) {
			script.dataset.clerkFrontendApi = config.clerk_frontend_api;
		}
		script.addEventListener("load", () => {
			window.Clerk?.load().then(resolve).catch(reject);
		});
		script.addEventListener("error", () =>
			reject(new Error("Clerk browser runtime failed to load")),
		);
		document.head.append(script);
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
		setSignedIn(window.Clerk?.session !== null);
	}, []);

	React.useEffect(() => {
		const abort = new AbortController();

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
		return () => abort.abort();
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
			const token = signedIn ? await window.Clerk?.session?.getToken() : null;
			return fetch(input, token ? mergeAuthHeader(init, token) : init);
		},
		[signedIn],
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
