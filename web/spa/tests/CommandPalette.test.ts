import { describe, expect, test } from "bun:test";

import {
	isCommandPaletteShortcut,
	isJobView,
	isSubmitInFlight,
	parseVideoUrl,
} from "../src/design-app/command-utils.js";

describe("parseVideoUrl", () => {
	test("accepts YouTube URLs with reordered watch query params", () => {
		expect(
			parseVideoUrl(
				"https://www.youtube.com/watch?t=42s&feature=share&v=jNQXAC9IVRw",
			),
		).toEqual({
			url: "https://www.youtube.com/watch?t=42s&feature=share&v=jNQXAC9IVRw",
			videoId: "jNQXAC9IVRw",
		});
	});

	test("accepts supported YouTube hosts and path forms", () => {
		expect(parseVideoUrl("m.youtube.com/watch?v=jNQXAC9IVRw")?.videoId).toBe(
			"jNQXAC9IVRw",
		);
		expect(parseVideoUrl("https://youtu.be/_-AbCdEf123")?.videoId).toBe(
			"_-AbCdEf123",
		);
		expect(
			parseVideoUrl("https://www.youtube.com/shorts/abcDEF12345")?.videoId,
		).toBe("abcDEF12345");
		expect(
			parseVideoUrl("https://www.youtube.com/embed/jNQXAC9IVRw")?.videoId,
		).toBe("jNQXAC9IVRw");
	});

	test("rejects redirect URLs where YouTube appears outside the host", () => {
		expect(
			parseVideoUrl(
				"http://other.com/redirect?url=youtube.com/watch?v=abc1234567X",
			),
		).toBeNull();
	});
});

describe("isJobView", () => {
	test("requires the success response fields rendered by the palette", () => {
		expect(
			isJobView({ job_id: 42, video_id: "jNQXAC9IVRw", status: "queued" }),
		).toBe(true);
		expect(isJobView(null)).toBe(false);
		expect(isJobView({ job_id: 42, video_id: "jNQXAC9IVRw" })).toBe(false);
		expect(
			isJobView({ job_id: "42", video_id: "jNQXAC9IVRw", status: "queued" }),
		).toBe(false);
	});
});

describe("isSubmitInFlight", () => {
	test("blocks re-picking only while a submit is pending", () => {
		// In flight — a new pick must be ignored to avoid a double submit.
		expect(isSubmitInFlight({ state: "submitting", video_id: "clip.mp4" })).toBe(
			true,
		);
	});

	test("allows retry after a terminal upload error (413/422)", () => {
		// Error is terminal: re-picking a replacement file must go through.
		expect(
			isSubmitInFlight({
				state: "error",
				video_id: "clip.mp4",
				message: "HTTP 413: upload exceeds 2048 MiB cap",
			}),
		).toBe(false);
	});

	test("allows another upload after a queued success and when idle", () => {
		expect(
			isSubmitInFlight({ id: 9, video_id: "upload:abcd", status: "queued" }),
		).toBe(false);
		expect(isSubmitInFlight(null)).toBe(false);
	});
});

describe("isCommandPaletteShortcut", () => {
	const keyEvent = (
		value: Pick<KeyboardEvent, "code" | "ctrlKey" | "key" | "metaKey">,
	): KeyboardEvent => value as KeyboardEvent;

	test("accepts Ctrl+K and Cmd+K, including code fallback", () => {
		expect(
			isCommandPaletteShortcut(
				keyEvent({ key: "k", code: "", ctrlKey: true, metaKey: false }),
			),
		).toBe(true);
		expect(
			isCommandPaletteShortcut(
				keyEvent({ key: "K", code: "", ctrlKey: false, metaKey: true }),
			),
		).toBe(true);
		expect(
			isCommandPaletteShortcut(
				keyEvent({
					key: "Unidentified",
					code: "KeyK",
					ctrlKey: true,
					metaKey: false,
				}),
			),
		).toBe(true);
		expect(
			isCommandPaletteShortcut(
				keyEvent({ key: "k", code: "", ctrlKey: false, metaKey: false }),
			),
		).toBe(false);
	});
});
