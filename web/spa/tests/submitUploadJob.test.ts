import { describe, expect, test } from "bun:test";

import { submitUploadJob } from "../src/design-app/api-jobs.js";

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
		JSON.stringify({ job_id: 9, video_id: "upload:abcd1234abcd1234", status: "queued" }),
		{ status: 201, headers: { "content-type": "application/json" } },
	);
}

function makeFile() {
	return new File([new Uint8Array([1, 2, 3, 4])], "clip.mp4", { type: "video/mp4" });
}

describe("submitUploadJob (#408 upload ingest)", () => {
	test("POSTs multipart to /jobs/upload with the file and default source", async () => {
		const { auth, calls } = makeAuth(jobViewResponse());

		const result = await submitUploadJob(auth, makeFile());

		expect(calls[0].url).toBe("/jobs/upload");
		expect(calls[0].init.method).toBe("POST");
		// No forced Content-Type — the browser sets the multipart boundary.
		expect(calls[0].init.headers).toBeUndefined();
		const form = calls[0].init.body as FormData;
		expect(form).toBeInstanceOf(FormData);
		expect((form.get("file") as File).name).toBe("clip.mp4");
		expect(form.get("source")).toBe("upload");
		expect(result).toEqual({
			job_id: 9,
			video_id: "upload:abcd1234abcd1234",
			status: "queued",
		});
	});

	test("forwards summarize/notify and trims the custom prompt", async () => {
		const { auth, calls } = makeAuth(jobViewResponse());

		await submitUploadJob(auth, makeFile(), {
			summarize: false,
			notify: false,
			summaryPrompt: "  Just the gist.  ",
			source: "capture",
		});

		const form = calls[0].init.body as FormData;
		expect(form.get("summarize")).toBe("false");
		expect(form.get("notify")).toBe("false");
		expect(form.get("summary_prompt")).toBe("Just the gist.");
		expect(form.get("source")).toBe("capture");
	});

	test("omits summary_prompt when blank", async () => {
		const { auth, calls } = makeAuth(jobViewResponse());

		await submitUploadJob(auth, makeFile(), { summaryPrompt: "   " });

		const form = calls[0].init.body as FormData;
		expect(form.get("summary_prompt")).toBeNull();
	});

	test("throws on a non-2xx response", async () => {
		const { auth } = makeAuth(new Response(null, { status: 503 }));

		await expect(submitUploadJob(auth, makeFile())).rejects.toThrow("HTTP 503");
	});

	test("surfaces the 413 size-cap detail from the server", async () => {
		const { auth } = makeAuth(
			new Response(
				JSON.stringify({ detail: "upload exceeds 2048 MiB cap" }),
				{ status: 413, headers: { "content-type": "application/json" } },
			),
		);

		await expect(submitUploadJob(auth, makeFile())).rejects.toThrow(
			"HTTP 413: upload exceeds 2048 MiB cap",
		);
	});

	test("surfaces the 422 invalid-media detail from the server", async () => {
		const { auth } = makeAuth(
			new Response(
				JSON.stringify({ detail: "invalid media file: not decodable" }),
				{ status: 422, headers: { "content-type": "application/json" } },
			),
		);

		await expect(submitUploadJob(auth, makeFile())).rejects.toThrow(
			"HTTP 422: invalid media file: not decodable",
		);
	});
});
