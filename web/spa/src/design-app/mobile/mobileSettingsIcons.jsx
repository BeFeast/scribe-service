// Mobile-Settings icon glyphs — verbatim port of the `sym = {...}` table and
// `I.chevR(w)` helper from `Scribe iOS.html` (mobile design source, SHA-256
// 421c930d9f2d5c1549dc632760f796992630a27eaa6fa38f28ff25584bc3ebb9).
//
// Source mapping (Scribe iOS.html → this file):
//   ~753 const I.chevR     → MobileChevR
//   ~765 const sym = {...} → MobileGlyph{Moon,Type,Wave,Source,Cpu,Coin,
//                                         Shield,Info,Archive}
//
// Geometry, viewBox, stroke widths, and path data are byte-for-byte from the
// design source — only the wrapping syntax is converted from JS template
// strings to JSX so React can render them inline. `width`/`height` accept a
// `size` prop with the source default of 17 so the grouped-list `.g-ic`
// recipe sees the exact pixel weight the iOS prototype rendered with.

import React from "react";

export const MobileGlyphMoon = ({ size = 17 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 20 20"
		fill="currentColor"
		aria-hidden="true"
		focusable="false"
	>
		<path d="M16 11.5A6.5 6.5 0 0 1 8.5 4 6.5 6.5 0 1 0 16 11.5Z" />
	</svg>
);

export const MobileGlyphType = ({ size = 17 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 20 20"
		fill="none"
		stroke="currentColor"
		strokeWidth="1.8"
		strokeLinecap="round"
		aria-hidden="true"
		focusable="false"
	>
		<path d="M4 6V4.5h12V6M10 4.5V16M7.5 16h5" />
	</svg>
);

export const MobileGlyphWave = ({ size = 17 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 20 20"
		fill="none"
		stroke="currentColor"
		strokeWidth="1.8"
		strokeLinecap="round"
		aria-hidden="true"
		focusable="false"
	>
		<path d="M3 10h2M7 6v8M11 3.5v13M15 7v6M17.5 9.5v1" />
	</svg>
);

export const MobileGlyphSource = ({ size = 17 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 20 20"
		fill="none"
		stroke="currentColor"
		strokeWidth="1.7"
		strokeLinejoin="round"
		aria-hidden="true"
		focusable="false"
	>
		<rect x="3" y="4" width="14" height="12" rx="2.5" />
		<path d="M3 8h14" />
	</svg>
);

export const MobileGlyphCpu = ({ size = 17 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 20 20"
		fill="none"
		stroke="currentColor"
		strokeWidth="1.7"
		aria-hidden="true"
		focusable="false"
	>
		<rect x="6" y="6" width="8" height="8" rx="1.5" />
		<path
			d="M8 3v3M12 3v3M8 14v3M12 14v3M3 8h3M3 12h3M14 8h3M14 12h3"
			strokeLinecap="round"
		/>
	</svg>
);

export const MobileGlyphCoin = ({ size = 17 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 20 20"
		fill="none"
		stroke="currentColor"
		strokeWidth="1.7"
		aria-hidden="true"
		focusable="false"
	>
		<circle cx="10" cy="10" r="7" />
		<path
			d="M10 6.5v7M8 8.2c0-1 1-1.5 2-1.5s2 .5 2 1.4-1 1.3-2 1.4-2 .5-2 1.4 1 1.5 2 1.5 2-.6 2-1.5"
			strokeLinecap="round"
		/>
	</svg>
);

export const MobileGlyphShield = ({ size = 17 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 20 20"
		fill="none"
		stroke="currentColor"
		strokeWidth="1.7"
		strokeLinejoin="round"
		aria-hidden="true"
		focusable="false"
	>
		<path d="M10 2.5l6 2.5v5c0 4-3 6.5-6 7.5-3-1-6-3.5-6-7.5V5z" />
	</svg>
);

export const MobileGlyphInfo = ({ size = 17 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 20 20"
		fill="none"
		stroke="currentColor"
		strokeWidth="1.7"
		strokeLinecap="round"
		aria-hidden="true"
		focusable="false"
	>
		<circle cx="10" cy="10" r="7.5" />
		<path d="M10 9v4.5M10 6.4v.1" />
	</svg>
);

export const MobileGlyphArchive = ({ size = 17 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 20 20"
		fill="none"
		stroke="currentColor"
		strokeWidth="1.7"
		strokeLinejoin="round"
		aria-hidden="true"
		focusable="false"
	>
		<rect x="3" y="4" width="14" height="4" rx="1" />
		<path d="M4.5 8v8h11V8M8 11h4" />
	</svg>
);

export const MobileGlyphPlus = ({ size = 18 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 18 18"
		fill="none"
		stroke="currentColor"
		strokeWidth="2"
		strokeLinecap="round"
		strokeLinejoin="round"
		aria-hidden="true"
		focusable="false"
	>
		<path d="M9 3v12M3 9h12" />
	</svg>
);

export const MobileChevR = ({ size = 18 }) => (
	<svg
		width={size}
		height={size}
		viewBox="0 0 18 18"
		fill="none"
		stroke="currentColor"
		strokeWidth="2"
		strokeLinecap="round"
		strokeLinejoin="round"
		aria-hidden="true"
		focusable="false"
	>
		<path d="M6.5 3.5L12 9l-5.5 5.5" />
	</svg>
);
