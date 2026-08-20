// Maps the live design-system tokens (set per [data-variant][data-theme] on
// <html>) onto Clerk's appearance API, so the in-app sign-in/sign-up dialog
// renders in whatever variant/theme is active (#439). Read at open time —
// the dialog is transient, so a later theme switch simply applies to the
// next open.

export type TokenReader = (name: string) => string;

type ClerkAppearance = {
	variables: Record<string, string>;
	elements: Record<string, Record<string, string>>;
};

// Clerk appearance variable -> design token, using the post-2025 variable
// names (@clerk/ui v1 ignores the deprecated colorText-era aliases). Missing
// tokens are omitted so Clerk's own defaults apply instead of empty strings
// breaking the theme.
const VARIABLE_TOKENS: ReadonlyArray<[string, string]> = [
	["colorPrimary", "--accent"],
	["colorPrimaryForeground", "--accent-fg"],
	["colorBackground", "--bg-card"],
	["colorForeground", "--fg"],
	["colorMutedForeground", "--fg-soft"],
	["colorMuted", "--bg-soft"],
	["colorInput", "--bg"],
	["colorInputForeground", "--fg"],
	["colorNeutral", "--fg"],
	["colorBorder", "--border"],
	["colorRing", "--accent"],
	["colorDanger", "--err"],
	["colorSuccess", "--ok"],
	["colorWarning", "--warn"],
	["borderRadius", "--radius"],
	["fontFamily", "--font-sans"],
	["fontFamilyButtons", "--font-sans"],
	["fontSize", "--fs-base"],
];

export function clerkAppearanceFromTokens(read: TokenReader): ClerkAppearance {
	const variables: Record<string, string> = {};
	for (const [variable, token] of VARIABLE_TOKENS) {
		const value = read(token).trim();
		if (value) {
			variables[variable] = value;
		}
	}

	const elements: Record<string, Record<string, string>> = {};
	const border = read("--border").trim();
	const shadow = read("--shadow").trim();
	const card: Record<string, string> = {};
	if (border) {
		card.border = `1px solid ${border}`;
	}
	if (shadow) {
		card.boxShadow = shadow;
	}
	if (Object.keys(card).length > 0) {
		elements.cardBox = card;
	}
	const display = read("--font-display").trim();
	if (display) {
		elements.headerTitle = { fontFamily: display };
	}

	return { variables, elements };
}

export function currentClerkAppearance(): ClerkAppearance {
	const styles = window.getComputedStyle(document.documentElement);
	return clerkAppearanceFromTokens((name) => styles.getPropertyValue(name));
}
