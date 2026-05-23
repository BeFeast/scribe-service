import { describe, expect, test } from "bun:test";

import {
	clerkRedirectOptions,
	parseFreshRedirectIntent,
} from "../src/hooks/useAuth";

describe("Clerk redirect auth helpers", () => {
	test("keeps sign-in and sign-up cross-flow redirects inside the app", () => {
		const redirectUrl = "https://scribe.example.test/library?view=feed";

		expect(clerkRedirectOptions("sign-in", redirectUrl)).toEqual({
			redirectUrl,
			signInForceRedirectUrl: redirectUrl,
			signInFallbackRedirectUrl: redirectUrl,
			signUpForceRedirectUrl: redirectUrl,
			signUpFallbackRedirectUrl: redirectUrl,
		});
		expect(clerkRedirectOptions("sign-up", redirectUrl)).toEqual({
			redirectUrl,
			signInForceRedirectUrl: redirectUrl,
			signInFallbackRedirectUrl: redirectUrl,
			signUpForceRedirectUrl: redirectUrl,
			signUpFallbackRedirectUrl: redirectUrl,
		});
	});

	test("rejects stale redirect intents so canceled auth can be retried", () => {
		const now = 200_000;

		expect(
			parseFreshRedirectIntent(
				JSON.stringify({ startedAt: now - 89_999 }),
				now,
			),
		).toEqual({ startedAt: now - 89_999 });
		expect(
			parseFreshRedirectIntent(
				JSON.stringify({ startedAt: now - 90_001 }),
				now,
			),
		).toBeNull();
		expect(parseFreshRedirectIntent(null, now)).toBeNull();
		expect(parseFreshRedirectIntent("not-json", now)).toBeNull();
	});
});
