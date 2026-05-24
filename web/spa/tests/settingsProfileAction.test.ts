import { describe, expect, test } from "bun:test";

import {
	CLERK_PROFILE_UNAVAILABLE,
	canRenderAccessGroup,
	clerkProfileAction,
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
