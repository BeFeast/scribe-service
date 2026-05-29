import { describe, expect, test } from "bun:test";

import { routeLabel, routeToHref } from "../src/hooks/useRoute";

describe("useRoute history page", () => {
	test("history route renders into the #/history hash", () => {
		expect(routeToHref({ page: "history", params: {} })).toBe("#/history");
	});

	test("history route exposes a History label for the nav", () => {
		expect(routeLabel({ page: "history", params: {} })).toBe("History");
	});
});
