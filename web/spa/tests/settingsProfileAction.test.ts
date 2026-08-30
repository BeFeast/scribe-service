import { describe, expect, test } from "bun:test";

import {
	CLERK_PROFILE_UNAVAILABLE,
	canGenerateExtensionToken,
	canRenderAccessGroup,
	clerkProfileAction,
	mintExtensionToken,
	shouldOfferTrustedClerkSignIn,
} from "../src/design-app/settings.jsx";

function setWindowClerk(clerk: Record<string, unknown>) {
	Object.defineProperty(globalThis, "window", {
		configurable: true,
		value: { Clerk: clerk },
		writable: true,
	});
}

function clearWindow() {
	Reflect.deleteProperty(globalThis, "window");
}

describe("clerkProfileAction", () => {
	test("prefers Account Portal redirects over Clerk UI modal methods", async () => {
		const calls: string[] = [];
		setWindowClerk({
			openUserProfile: () => calls.push("openUserProfile"),
			openProfile: () => calls.push("openProfile"),
			redirectToUserProfile: () => calls.push("redirectToUserProfile"),
		});

		try {
			await clerkProfileAction()?.();
		} finally {
			clearWindow();
		}

		expect(calls).toEqual(["redirectToUserProfile"]);
	});

	test("falls back to modal methods and hides raw Clerk UI component errors", async () => {
		const calls: string[] = [];
		setWindowClerk({
			openUserProfile: () => {
				calls.push("openUserProfile");
				throw new Error("Clerk was not loaded with Ui components");
			},
			openProfile: () => {
				calls.push("openProfile");
			},
		});

		try {
			await clerkProfileAction()?.();
		} finally {
			clearWindow();
		}

		expect(calls).toEqual(["openUserProfile", "openProfile"]);
	});

	test("reports stable product copy when every Clerk profile path fails", async () => {
		setWindowClerk({
			redirectToUserProfile: () => {
				throw new Error("portal unavailable");
			},
			openUserProfile: () => {
				throw new Error("Clerk was not loaded with Ui components");
			},
		});

		try {
			await expect(clerkProfileAction()?.()).rejects.toThrow(
				CLERK_PROFILE_UNAVAILABLE,
			);
		} finally {
			clearWindow();
		}
	});
});

describe("canRenderAccessGroup", () => {
	test("only allows the /api/auth/me admin role to mount Access management", () => {
		expect(canRenderAccessGroup({ role: "admin" })).toBe(true);
		expect(canRenderAccessGroup({ role: "user", canWrite: true })).toBe(false);
		expect(canRenderAccessGroup({ role: "user", users: [{ role: "admin" }] })).toBe(false);
		expect(canRenderAccessGroup(null)).toBe(false);
	});
});

describe("trusted-LAN Clerk token access", () => {
	test("offers optional Clerk sign-in only to a signed-out trusted LAN", () => {
		expect(
			shouldOfferTrustedClerkSignIn({
				trustedNetwork: true,
				clerkConfigured: true,
				signedIn: false,
			}),
		).toBe(true);
		expect(
			shouldOfferTrustedClerkSignIn({
				trustedNetwork: false,
				clerkConfigured: true,
				signedIn: false,
			}),
		).toBe(false);
		expect(
			shouldOfferTrustedClerkSignIn({
				trustedNetwork: true,
				clerkConfigured: true,
				signedIn: true,
			}),
		).toBe(false);
	});

	test("enables generation after a Clerk session and posts to the existing endpoint", async () => {
		const calls: Array<{ url: string; init: RequestInit }> = [];
		const auth = {
			signedIn: true,
			maybeAutoSignIn: () => false,
			protectedFetch: async (url: string, init: RequestInit) => {
				calls.push({ url, init });
				return new Response(JSON.stringify({ token: "stx_once" }), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			},
		};

		expect(canGenerateExtensionToken(auth)).toBe(true);
		expect(canGenerateExtensionToken(auth, true)).toBe(false);
		await expect(mintExtensionToken(auth)).resolves.toEqual({
			token: "stx_once",
		});
		expect(calls).toHaveLength(1);
		expect(calls[0]?.url).toBe("/api/auth/extension-token");
		expect(calls[0]?.init.method).toBe("POST");
		expect(JSON.parse(String(calls[0]?.init.body))).toEqual({
			label: "Settings access token",
		});
	});

	test("does not call the token endpoint without a Clerk session", async () => {
		const auth = {
			signedIn: false,
			protectedFetch: () => {
				throw new Error("endpoint must not be called");
			},
		};

		expect(canGenerateExtensionToken(auth)).toBe(false);
		await expect(mintExtensionToken(auth)).rejects.toThrow(
			"Sign in with Clerk",
		);
	});
});
