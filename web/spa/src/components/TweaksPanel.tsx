import type React from "react";

import type { Route } from "../hooks/useRoute";
import type {
	LibraryLayout,
	ScribeDensity,
	ScribeTheme,
	ScribeVariant,
	Tweaks,
} from "../hooks/useTweaks";

type TweaksPanelProps = {
	tweaks: Tweaks;
	setVariant: (value: ScribeVariant) => void;
	setTheme: (value: ScribeTheme) => void;
	setDensity: (value: ScribeDensity) => void;
	setLibraryLayout: (value: LibraryLayout) => void;
	navigate: (route: Route) => void;
};

const variantOptions: ScribeVariant[] = ["paper", "terminal", "console"];
const themeOptions: ScribeTheme[] = ["light", "dark"];
const densityOptions: ScribeDensity[] = ["compact", "cozy", "comfy"];
const layoutOptions: LibraryLayout[] = ["feed", "table", "cards"];

export function TweaksPanel({
	tweaks,
	setVariant,
	setTheme,
	setDensity,
	setLibraryLayout,
	navigate,
}: TweaksPanelProps) {
	return (
		<aside className="tweaks-panel" aria-label="Tweaks">
			<div className="tweaks-head">
				<strong>Tweaks</strong>
				<span>runtime</span>
			</div>
			<TweakRow label="Variant">
				{variantOptions.map((value) => (
					<button
						type="button"
						key={value}
						className={tweaks.variant === value ? "seg active" : "seg"}
						onClick={() => setVariant(value)}
					>
						{value}
					</button>
				))}
			</TweakRow>
			<TweakRow label="Theme">
				{themeOptions.map((value) => (
					<button
						type="button"
						key={value}
						className={tweaks.theme === value ? "seg active" : "seg"}
						onClick={() => setTheme(value)}
					>
						{value}
					</button>
				))}
			</TweakRow>
			<TweakRow label="Density">
				{densityOptions.map((value) => (
					<button
						type="button"
						key={value}
						className={tweaks.density === value ? "seg active" : "seg"}
						onClick={() => setDensity(value)}
					>
						{value}
					</button>
				))}
			</TweakRow>
			<TweakRow label="Library">
				{layoutOptions.map((value) => (
					<button
						type="button"
						key={value}
						className={tweaks.libraryLayout === value ? "seg active" : "seg"}
						onClick={() => setLibraryLayout(value)}
					>
						{value}
					</button>
				))}
			</TweakRow>
			<div className="jump-row">
				<button
					type="button"
					className="jump-button"
					onClick={() => navigate({ page: "library", params: {} })}
				>
					Library
				</button>
				<button
					type="button"
					className="jump-button"
					onClick={() => navigate({ page: "queue", params: {} })}
				>
					Queue
				</button>
				<button
					type="button"
					className="jump-button"
					onClick={() => navigate({ page: "ops", params: {} })}
				>
					Ops
				</button>
			</div>
		</aside>
	);
}

function TweakRow({
	label,
	children,
}: { label: string; children: React.ReactNode }) {
	return (
		<div className="tweak-row">
			<span>{label}</span>
			<div className="seg-group">{children}</div>
		</div>
	);
}
