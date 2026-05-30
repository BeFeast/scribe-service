import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

// Structural guard for the mobile Settings + Access port (Issue #280).
//
// The full React tree needs jsdom to render, which is heavier than the rest of
// this test suite. Instead, we read the ported component file as text and
// assert on the verbatim CSS recipes / handler hooks the issue's acceptance
// criteria pin down. This keeps the test fast and catches obvious
// regressions (e.g. someone replacing .glist with .settings-list, dropping
// the setTweak wiring, or sneaking window.alert into the file).

const FILE = resolve(
	import.meta.dir,
	"../src/design-app/mobile/MobileSettingsPage.jsx",
);
const SOURCE = readFileSync(FILE, "utf-8");

const ICONS_FILE = resolve(
	import.meta.dir,
	"../src/design-app/mobile/mobileSettingsIcons.jsx",
);
const ICONS_SOURCE = readFileSync(ICONS_FILE, "utf-8");

describe("MobileSettingsPage source recipes", () => {
	test("uses the literal grouped-inset list recipe classes", () => {
		for (const recipe of [
			'className="glist"',
			'className="grow grow-btn"',
			'className="grow"',
			'className="g-ic"',
			'className="g-label"',
			'className="g-val"',
			'className="me-card"',
			'className="me-name"',
			'className="me-mail"',
			'"urow me" : "urow"',
			'className="u-av"',
			'className="u-main"',
			'className="u-name"',
			'className="u-mail"',
			'className="sec-label"',
		]) {
			expect(SOURCE.includes(recipe)).toBe(true);
		}
	});

	test("renders the .role pill with both admin and user variants", () => {
		expect(SOURCE.includes('`role ${role}`')).toBe(true);
		expect(SOURCE.includes('"admin"')).toBe(true);
		expect(SOURCE.includes('"user"')).toBe(true);
	});

	test("wires Appearance / variant / density / library layout to setTweak", () => {
		for (const wire of [
			'setTweak("theme"',
			'setTweak("variant", "field")',
			'setTweak("variant", "paper")',
			'setTweak("variant", "terminal")',
			'setTweak("variant", "console")',
			'setTweak("density", "compact")',
			'setTweak("density", "cozy")',
			'setTweak("density", "comfy")',
			'setTweak("libraryLayout", "feed")',
			'setTweak("libraryLayout", "table")',
			'setTweak("libraryLayout", "cards")',
		]) {
			expect(SOURCE.includes(wire)).toBe(true);
		}
	});

	test("opens the Access sub-view via local state and offers a Settings back affordance", () => {
		expect(SOURCE.includes("setShowAccess(true)")).toBe(true);
		expect(SOURCE.includes("setShowAccess(false)")).toBe(true);
		expect(SOURCE.includes('aria-label="Back to Settings"')).toBe(true);
	});

	test("never reaches for browser-native dialogs", () => {
		expect(/\bwindow\.(alert|confirm|prompt)\s*\(/.test(SOURCE)).toBe(false);
		expect(/\b(alert|confirm|prompt)\s*\(/.test(SOURCE)).toBe(false);
	});

	test("does not import the tweaks panel", () => {
		expect(SOURCE.includes("tweaks-panel")).toBe(false);
	});
});

describe("mobileSettingsIcons verbatim SVG geometry", () => {
	test("matches the iOS prototype `sym{}` table path data", () => {
		for (const path of [
			'd="M16 11.5A6.5 6.5 0 0 1 8.5 4 6.5 6.5 0 1 0 16 11.5Z"',
			'd="M4 6V4.5h12V6M10 4.5V16M7.5 16h5"',
			'd="M3 10h2M7 6v8M11 3.5v13M15 7v6M17.5 9.5v1"',
			'd="M3 8h14"',
			'd="M10 2.5l6 2.5v5c0 4-3 6.5-6 7.5-3-1-6-3.5-6-7.5V5z"',
			'd="M10 9v4.5M10 6.4v.1"',
			'd="M4.5 8v8h11V8M8 11h4"',
			'd="M6.5 3.5L12 9l-5.5 5.5"',
		]) {
			expect(ICONS_SOURCE.includes(path)).toBe(true);
		}
	});
});
