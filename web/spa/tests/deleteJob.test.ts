import { describe, expect, test } from "bun:test";

import { HttpError, deleteJob } from "../src/design-app/api.jsx";

type FetchCall = { url: string; init: RequestInit };

function makeAuth(response: Response) {
	const calls: FetchCall[] = [];
	let autoSignInCalls = 0;
	const auth = {
		protectedFetch: async (url: string, init: RequestInit) => {
			calls.push({ url, init });
			return response;
		},
		maybeAutoSignIn: () => {
			autoSignInCalls += 1;
		},
	};
	return { auth, calls, getAutoSignInCalls: () => autoSignInCalls };
}

describe("deleteJob (admin dismiss for failed jobs)", () => {
	test("issues DELETE /admin/jobs/{id} and resolves on 204", async () => {
		const { auth, calls } = makeAuth(new Response(null, { status: 204 }));

		await expect(deleteJob(auth, 42)).resolves.toBeUndefined();

		expect(calls).toHaveLength(1);
		expect(calls[0].url).toBe("/admin/jobs/42");
		expect(calls[0].init.method).toBe("DELETE");
		expect(calls[0].init.cache).toBe("no-store");
	});

	test("throws HttpError with status 409 when the job is not failed", async () => {
		const { auth } = makeAuth(
			new Response(JSON.stringify({ detail: "job 7 is queued; only failed jobs can be dismissed." }), {
				status: 409,
				headers: { "content-type": "application/json" },
			}),
		);

		const error = await deleteJob(auth, 7).then(
			() => null,
			(reason) => reason,
		);

		expect(error).toBeInstanceOf(HttpError);
		expect((error as HttpError).status).toBe(409);
		expect((error as HttpError).message).toContain("only failed jobs can be dismissed");
	});

	test("triggers maybeAutoSignIn and throws HttpError on 403 (non-admin)", async () => {
		const { auth, getAutoSignInCalls } = makeAuth(
			new Response(JSON.stringify({ detail: "admin role required" }), {
				status: 403,
				headers: { "content-type": "application/json" },
			}),
		);

		const error = await deleteJob(auth, 9).then(
			() => null,
			(reason) => reason,
		);

		expect(error).toBeInstanceOf(HttpError);
		expect((error as HttpError).status).toBe(403);
		expect(getAutoSignInCalls()).toBe(1);
	});

	test("removes the failure from the local list after a successful DELETE + refresh", async () => {
		const initialFailures = [
			{ id: 11, title: "alpha" },
			{ id: 22, title: "beta" },
			{ id: 33, title: "gamma" },
		];
		let failures = [...initialFailures];

		const { auth, calls } = makeAuth(new Response(null, { status: 204 }));
		const refreshCore = async () => {
			failures = failures.filter((f) => f.id !== 22);
		};

		const dismissJob = async (id: number) => {
			await deleteJob(auth, id);
			await refreshCore();
		};

		await dismissJob(22);

		expect(calls[0].url).toBe("/admin/jobs/22");
		expect(calls[0].init.method).toBe("DELETE");
		expect(failures.map((f) => f.id)).toEqual([11, 33]);
	});
});
