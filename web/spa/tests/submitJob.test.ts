import { describe, expect, test } from "bun:test";

import { submitJob } from "../src/design-app/api-jobs.js";

type FetchCall = { url: string; init: RequestInit };

function makeAuth(response: Response) {
	const calls: FetchCall[] = [];
	const auth = {
		protectedFetch: async (url: string, init: RequestInit) => {
			calls.push({ url, init });
			return response;
		},
	};
	return { auth, calls };
}

function jobViewResponse() {
	return new Response(
		JSON.stringify({ job_id: 7, video_id: "dQw4w9WgXcQ", status: "queued" }),
		{ status: 201, headers: { "content-type": "application/json" } },
	);
}

function sentBody(calls: FetchCall[]) {
	return JSON.parse(calls[0].init.body as string);
}

describe("submitJob (#296 per-job toggles)", () => {
	test("legacy submitJob(auth, url) stays byte-identical on the wire", async () => {
		const { auth, calls } = makeAuth(jobViewResponse());

		const result = await submitJob(auth, "https://youtu.be/dQw4w9WgXcQ");

		expect(calls[0].url).toBe("/jobs");
		expect(calls[0].init.method).toBe("POST");
		expect(sentBody(calls)).toEqual({
			url: "https://youtu.be/dQw4w9WgXcQ",
			source: "manual",
		});
		expect(result).toEqual({
			job_id: 7,
			video_id: "dQw4w9WgXcQ",
			status: "queued",
		});
	});

	test("forwards summarize/notify and trims the custom prompt", async () => {
		const { auth, calls } = makeAuth(jobViewResponse());

		await submitJob(auth, "https://youtu.be/dQw4w9WgXcQ", {
			summarize: false,
			notify: false,
			summaryPrompt: "  Just the gist.  ",
		});

		expect(sentBody(calls)).toEqual({
			url: "https://youtu.be/dQw4w9WgXcQ",
			source: "manual",
			summarize: false,
			notify: false,
			summary_prompt: "Just the gist.",
		});
	});

	test("omits summary_prompt when blank but keeps the boolean toggles", async () => {
		const { auth, calls } = makeAuth(jobViewResponse());

		await submitJob(auth, "https://youtu.be/dQw4w9WgXcQ", {
			summarize: true,
			notify: true,
			summaryPrompt: "   ",
		});

		expect(sentBody(calls)).toEqual({
			url: "https://youtu.be/dQw4w9WgXcQ",
			source: "manual",
			summarize: true,
			notify: true,
		});
	});

	test("throws on a non-2xx response", async () => {
		const { auth } = makeAuth(new Response(null, { status: 500 }));

		await expect(
			submitJob(auth, "https://youtu.be/dQw4w9WgXcQ"),
		).rejects.toThrow("HTTP 500");
	});
});
