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
  variant: "paper",
  theme: "light",
  density: "cozy",
  libraryLayout: "feed",
};

type TweakAction =
  | { type: "variant"; value: ScribeVariant }
  | { type: "theme"; value: ScribeTheme }
  | { type: "density"; value: ScribeDensity }
  | { type: "libraryLayout"; value: LibraryLayout }
  | { type: "replace"; value: Tweaks };

const variants = new Set<ScribeVariant>(["paper", "terminal", "console"]);
const themes = new Set<ScribeTheme>(["light", "dark"]);
const densities = new Set<ScribeDensity>(["compact", "cozy", "comfy"]);
const layouts = new Set<LibraryLayout>(["feed", "table", "cards"]);

function readStoredTweaks(): Tweaks {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw === null) {
      return DEFAULT_TWEAKS;
    }
    const parsed = JSON.parse(raw) as Partial<Tweaks>;
    return {
      variant: parsed.variant !== undefined && variants.has(parsed.variant) ? parsed.variant : DEFAULT_TWEAKS.variant,
      theme: parsed.theme !== undefined && themes.has(parsed.theme) ? parsed.theme : DEFAULT_TWEAKS.theme,
      density:
        parsed.density !== undefined && densities.has(parsed.density) ? parsed.density : DEFAULT_TWEAKS.density,
      libraryLayout:
        parsed.libraryLayout !== undefined && layouts.has(parsed.libraryLayout)
          ? parsed.libraryLayout
          : DEFAULT_TWEAKS.libraryLayout,
    };
  } catch {
    return DEFAULT_TWEAKS;
  }
}

function reducer(state: Tweaks, action: TweakAction): Tweaks {
  switch (action.type) {
    case "variant":
      return { ...state, variant: action.value };
    case "theme":
      return { ...state, theme: action.value };
    case "density":
      return { ...state, density: action.value };
    case "libraryLayout":
      return { ...state, libraryLayout: action.value };
    case "replace":
      return action.value;
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
  const [tweaks, dispatch] = React.useReducer(reducer, DEFAULT_TWEAKS, readStoredTweaks);
  const setVariant = React.useCallback((value: ScribeVariant) => dispatch({ type: "variant", value }), []);
  const setTheme = React.useCallback((value: ScribeTheme) => dispatch({ type: "theme", value }), []);
  const setDensity = React.useCallback((value: ScribeDensity) => dispatch({ type: "density", value }), []);
  const setLibraryLayout = React.useCallback(
    (value: LibraryLayout) => dispatch({ type: "libraryLayout", value }),
    [],
  );
  const replaceTweaks = React.useCallback((value: Tweaks) => dispatch({ type: "replace", value }), []);

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
    setVariant,
    setTheme,
    setDensity,
    setLibraryLayout,
    replaceTweaks,
  };
}
