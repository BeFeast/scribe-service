import { describe, expect, test } from "bun:test";

import { clerkAppearanceFromTokens } from "../src/hooks/clerkAppearance";

const FIELD_LIGHT_TOKENS: Record<string, string> = {
	"--accent": "#b15233",
	"--accent-fg": "#fff",
	"--bg": "#f6f3ec",
	"--bg-card": "#fbf9f3",
	"--fg": "#1c1a16",
	"--fg-soft": "#44403a",
	"--err": "#b04035",
	"--ok": "#4e7e3e",
	"--warn": "#b8853a",
	"--radius": "6px",
	"--font-sans": '"Inter", ui-sans-serif, system-ui, sans-serif',
	"--font-display": '"Newsreader", Georgia, serif',
	"--fs-base": "14px",
	"--border": "#d8d1c1",
	"--shadow": "0 1px 2px rgba(28, 26, 22, 0.05)",
};

function reader(tokens: Record<string, string>) {
	return (name: string) => tokens[name] ?? "";
}

describe("clerkAppearanceFromTokens", () => {
	test("maps the active design tokens onto Clerk appearance variables", () => {
		const appearance = clerkAppearanceFromTokens(reader(FIELD_LIGHT_TOKENS));
		expect(appearance.variables.colorPrimary).toBe("#b15233");
		expect(appearance.variables.colorPrimaryForeground).toBe("#fff");
		expect(appearance.variables.colorBackground).toBe("#fbf9f3");
		expect(appearance.variables.colorInput).toBe("#f6f3ec");
		expect(appearance.variables.colorForeground).toBe("#1c1a16");
		expect(appearance.variables.colorMutedForeground).toBe("#44403a");
		expect(appearance.variables.colorBorder).toBe("#d8d1c1");
		expect(appearance.variables.colorRing).toBe("#b15233");
		expect(appearance.variables.colorDanger).toBe("#b04035");
		expect(appearance.variables.borderRadius).toBe("6px");
		expect(appearance.variables.fontFamily).toContain("Inter");
		expect(appearance.variables.fontSize).toBe("14px");
	});

	test("styles the card and header from border/shadow/display tokens", () => {
		const appearance = clerkAppearanceFromTokens(reader(FIELD_LIGHT_TOKENS));
		expect(appearance.elements.cardBox.border).toBe("1px solid #d8d1c1");
		expect(appearance.elements.cardBox.boxShadow).toBe(
			"0 1px 2px rgba(28, 26, 22, 0.05)",
		);
		expect(appearance.elements.headerTitle.fontFamily).toContain("Newsreader");
	});

	test("omits missing tokens so Clerk defaults apply", () => {
		const appearance = clerkAppearanceFromTokens(reader({ "--accent": "#0af" }));
		expect(appearance.variables).toEqual({
			colorPrimary: "#0af",
			colorRing: "#0af",
		});
		expect(appearance.elements.cardBox).toBeUndefined();
		expect(appearance.elements.headerTitle).toBeUndefined();
	});

	test("trims computed-style whitespace", () => {
		const appearance = clerkAppearanceFromTokens(
			reader({ "--accent": "  #b15233  " }),
		);
		expect(appearance.variables.colorPrimary).toBe("#b15233");
	});
});
