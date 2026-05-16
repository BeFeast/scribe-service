import React from "react";

export type ScribeVariant = "paper" | "terminal" | "console";
export type ScribeTheme = "light" | "dark";
export type ScribeDensity = "compact" | "cozy" | "comfy";
export type LibraryLayout = "feed" | "table" | "cards";

export type Tweaks = {
	variant: ScribeVariant;
	theme: ScribeTheme;
	density: ScribeDensity;
	libraryLayout: LibraryLayout;
};

const STORAGE_KEY = "scribe.tweaks";

export const DEFAULT_TWEAKS: Tweaks = {
	variant: "terminal",
	theme: "light",
	density: "cozy",
	libraryLayout: "feed",
};

type TweakAction =
	| { type: "theme"; value: ScribeTheme }
	| { type: "replace"; value: { theme: ScribeTheme } };

const themes = new Set<ScribeTheme>(["light", "dark"]);

function readStoredTweaks(): Tweaks {
	try {
		const raw = localStorage.getItem(STORAGE_KEY);
		if (raw === null) {
			return DEFAULT_TWEAKS;
		}
		const parsed = JSON.parse(raw) as Partial<Tweaks>;
		return {
			variant: DEFAULT_TWEAKS.variant,
			theme:
				parsed.theme !== undefined && themes.has(parsed.theme)
					? parsed.theme
					: DEFAULT_TWEAKS.theme,
			density: DEFAULT_TWEAKS.density,
			libraryLayout: DEFAULT_TWEAKS.libraryLayout,
		};
	} catch {
		return DEFAULT_TWEAKS;
	}
}

function reducer(state: Tweaks, action: TweakAction): Tweaks {
	switch (action.type) {
		case "theme":
			return { ...state, theme: action.value };
		case "replace":
			return {
				...DEFAULT_TWEAKS,
				theme: action.value.theme,
			};
	}
}

function applyTweaks(tweaks: Tweaks): void {
	const { dataset } = document.documentElement;
	dataset.variant = tweaks.variant;
	dataset.theme = tweaks.theme;
	dataset.density = tweaks.density;
	dataset.libraryLayout = tweaks.libraryLayout;
}

export function useTweaks() {
	const [tweaks, dispatch] = React.useReducer(
		reducer,
		DEFAULT_TWEAKS,
		readStoredTweaks,
	);
	const setTheme = React.useCallback(
		(value: ScribeTheme) => dispatch({ type: "theme", value }),
		[],
	);
	const replaceTweaks = React.useCallback(
		(value: { theme: ScribeTheme }) => dispatch({ type: "replace", value }),
		[],
	);

	React.useEffect(() => {
		applyTweaks(tweaks);
		try {
			localStorage.setItem(STORAGE_KEY, JSON.stringify(tweaks));
		} catch {
			// Persistence is optional; the visible runtime tweaks still apply.
		}
	}, [tweaks]);

	return {
		tweaks,
		setTheme,
		replaceTweaks,
	};
}
