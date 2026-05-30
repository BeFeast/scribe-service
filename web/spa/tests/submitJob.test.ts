// Tests for the shared submitJob(auth, url) helper extracted in
// Wave 2f / Issue #281. The helper is the single submit path for the
// desktop command palette AND the mobile CaptureSheet. The contract under
// test is the real network call shape — POST /jobs with body
// `{url, source: "manual"}` via auth.protectedFetch — plus the success and
// error response handling.

import { describe, expect, test } from "bun:test";

import { submitJob } from "../src/design-app/api-jobs.js";

type FetchCall = {
	input: RequestInfo | URL;
	init: RequestInit | undefined;
};

function buildAuth(response: Response): { auth: { protectedFetch: typeof fetch }; calls: FetchCall[] } {
	const calls: FetchCall[] = [];
	const protectedFetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
		calls.push({ input, init });
		return response;
	}) as typeof fetch;
	return { auth: { protectedFetch }, calls };
}

function jsonResponse(body: unknown, init?: ResponseInit): Response {
	return new Response(JSON.stringify(body), {
		status: init?.status ?? 200,
		headers: { "Content-Type": "application/json" },
		...init,
	});
}

describe("submitJob", () => {
	test("posts to /jobs with {url, source: 'manual'} via auth.protectedFetch", async () => {
		const { auth, calls } = buildAuth(
			jsonResponse({ job_id: 42, video_id: "jNQXAC9IVRw", status: "queued" }),
		);
		const body = await submitJob(auth, "https://youtu.be/jNQXAC9IVRw");
		expect(calls).toHaveLength(1);
		expect(calls[0].input).toBe("/jobs");
		expect(calls[0].init?.method).toBe("POST");
		const headers = calls[0].init?.headers as Record<string, string>;
		expect(headers["Content-Type"]).toBe("application/json");
		const sent = JSON.parse(String(calls[0].init?.body));
		expect(sent).toEqual({
			url: "https://youtu.be/jNQXAC9IVRw",
			source: "manual",
		});
		expect(body).toEqual({
			job_id: 42,
			video_id: "jNQXAC9IVRw",
			status: "queued",
		});
	});

	test("rejects invalid URLs before any network call", async () => {
		const { auth, calls } = buildAuth(jsonResponse({}, { status: 500 }));
		await expect(submitJob(auth, "not a url")).rejects.toMatchObject({
			code: "invalid_youtube_url",
		});
		expect(calls).toHaveLength(0);
	});

	test("throws submit_failed on non-2xx with HTTP status in the message", async () => {
		const { auth } = buildAuth(jsonResponse({ detail: "rate limited" }, { status: 429 }));
		await expect(
			submitJob(auth, "https://youtu.be/jNQXAC9IVRw"),
		).rejects.toMatchObject({
			code: "submit_failed",
			status: 429,
			message: "HTTP 429",
		});
	});

	test("throws submit_failed when body is not a JobView shape", async () => {
		const { auth } = buildAuth(jsonResponse({ ok: true }));
		await expect(
			submitJob(auth, "https://youtu.be/jNQXAC9IVRw"),
		).rejects.toMatchObject({
			code: "submit_failed",
		});
	});

	test("preserves the user-typed URL string (no normalization)", async () => {
		const { auth, calls } = buildAuth(
			jsonResponse({ job_id: 1, video_id: "jNQXAC9IVRw", status: "queued" }),
		);
		const typed = "https://www.youtube.com/watch?v=jNQXAC9IVRw&t=42s";
		await submitJob(auth, typed);
		const sent = JSON.parse(String(calls[0].init?.body));
		expect(sent.url).toBe(typed);
		expect(sent.source).toBe("manual");
	});
});
