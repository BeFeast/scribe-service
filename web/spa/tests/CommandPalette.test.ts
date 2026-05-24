import { describe, expect, test } from "bun:test";

import {
	isCommandPaletteShortcut,
	isJobView,
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
